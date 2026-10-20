package fw.agent

import fw.agent.model.{StepAttributes, TaskDocument}
import fw.agent.model.TaskDocument.*
import org.mongodb.scala.*
import org.mongodb.scala.bson.{BsonNull, Document}
import org.mongodb.scala.model.{Filters, FindOneAndUpdateOptions, ReturnDocument, Updates}
import org.slf4j.LoggerFactory

import java.util.UUID
import org.bson.{Document => JDoc}
import scala.concurrent.Await
import scala.concurrent.duration.*
import scala.jdk.CollectionConverters.*

/** MongoDB operations for the AFL agent poller.
  *
  * All operations are synchronous (blocking) to match the polling model.
  */
class MongoOps(db: MongoDatabase, timeout: Duration = 10.seconds):

  private val logger = LoggerFactory.getLogger(getClass)
  private val tasks = db.getCollection(Protocol.Collections.Tasks)
  private val steps = db.getCollection(Protocol.Collections.Steps)

  /** Claim one task (protocol v1.2 `claim_task`, then optionally
    * `reclaim_expired_task`). Records `serverId` and a lease.
    *
    * Besides name and task list the filter skips tasks in their retry cooldown,
    * tasks that need a script environment or FFL feature (an agent runs no
    * evaluator, so it declines both), and tasks whose `requires` floor exceeds
    * `resources`. A dimension the task names that is not in `resources`
    * declines the claim.
    *
    * @param reclaimExpired also take over a running task whose lease expired;
    *                       callers pace this (Protocol.LeaseReclaimScanMs)
    */
  def claimTask(
      taskNames: Seq[String],
      taskLists: Seq[String],
      serverId: String,
      resources: Map[String, Double],
      reclaimExpired: Boolean
  ): Option[TaskDocument] =
    if taskNames.isEmpty then return None
    val now = System.currentTimeMillis()
    val set = JDoc("state", Protocol.TaskState.Running)
      .append("updated", now)
      .append("server_id", serverId)
      .append("lease_expires", now + Protocol.LeaseMs)
    val after = FindOneAndUpdateOptions().returnDocument(ReturnDocument.AFTER)

    val pending = JDoc("state", Protocol.TaskState.Pending)
      .append("task_list_name", taskListFilter(taskLists))
      .append("$and", jl(
        nameFilter(taskNames),
        JDoc("$or", jl(
          JDoc("next_retry_after", JDoc("$exists", false)),
          JDoc("next_retry_after", 0),
          JDoc("next_retry_after", JDoc("$lte", now)))),
        noEnvironment,
        JDoc("$or", jl(
          JDoc("required_features", JDoc("$exists", false)),
          JDoc("required_features", java.util.List.of()))),
        resourceFilter(resources)))
    val claimed = Await.result(
      tasks.findOneAndUpdate(pending, JDoc("$set", set), after).toFutureOption(), timeout)
    if claimed.isDefined || !reclaimExpired then
      return claimed.map(doc => TaskDocument.fromBson(Document(doc.toBsonDocument)))

    // A running task whose holder stopped renewing its lease. It is a RETRY, so
    // retry_count is bumped, and only while budget remains: an exhausted one is
    // left for the runtime's reaper, which dead-letters it.
    val expired = JDoc("state", Protocol.TaskState.Running)
      .append("task_list_name", taskListFilter(taskLists))
      .append("lease_expires", JDoc("$lt", now).append("$gt", 0))
      .append("$and", jl(
        nameFilter(taskNames),
        noEnvironment,
        JDoc("$or", jl(
          JDoc("max_retries", JDoc("$lte", 0)),
          JDoc("$expr", JDoc("$lt", jl(
            JDoc("$ifNull", jl("$retry_count", 0)),
            JDoc("$ifNull", jl("$max_retries", Protocol.DefaultMaxRetries)))))))))
    Await.result(
      tasks.findOneAndUpdate(expired,
        JDoc("$set", set).append("$inc", JDoc("retry_count", 1)), after).toFutureOption(),
      timeout
    ).map(doc => TaskDocument.fromBson(Document(doc.toBsonDocument)))

  private def jl(xs: Any*): java.util.List[Any] = java.util.List.of(xs.map(_.asInstanceOf[AnyRef])*).asInstanceOf[java.util.List[Any]]

  private def taskListFilter(taskLists: Seq[String]): Any =
    if taskLists.size == 1 then taskLists.head else JDoc("$in", taskLists.asJava)

  /** Exact names, or `name:` prefixes (the prefix is a literal, so quoted). */
  private def nameFilter(taskNames: Seq[String]): JDoc =
    val or = JDoc("name", JDoc("$in", taskNames.asJava)) +:
      taskNames.map(n => JDoc("name", JDoc("$regex", "^" + java.util.regex.Pattern.quote(n) + ":")))
    JDoc("$or", or.asJava)

  private def noEnvironment: JDoc =
    JDoc("$or", jl(
      JDoc("environment_hash", JDoc("$exists", false)),
      JDoc("environment_hash", "")))

  private def resourceFilter(resources: Map[String, Double]): JDoc =
    val base = Seq(JDoc("requires", JDoc("$exists", false)), JDoc("requires", JDoc()))
    val perDim =
      if resources.isEmpty then Seq.empty
      else
        val dims = JDoc("requires_dims", JDoc("$not",
          JDoc("$elemMatch", JDoc("$nin", resources.keys.toSeq.asJava))))
        val caps = resources.toSeq.map { case (dim, cap) =>
          JDoc("$or", jl(
            JDoc(s"requires.$dim", JDoc("$exists", false)),
            JDoc(s"requires.$dim", JDoc("$lte", cap))))
        }
        Seq(JDoc("$and", (dims +: caps).asJava))
    JDoc("$or", (base ++ perDim).asJava)

  /** Renew a running task's lease (protocol `renew_task_lease`).
    * Ownership-gated: false when the task is no longer this server's.
    */
  def renewLease(
      taskId: String,
      serverId: String,
      progressPct: Option[Int] = None,
      progressMessage: Option[String] = None
  ): Boolean =
    val now = System.currentTimeMillis()
    val set = JDoc("task_heartbeat", now).append("lease_expires", now + Protocol.LeaseMs)
    progressPct.foreach(p => set.append("progress_pct", math.max(0, math.min(100, p))))
    progressMessage.foreach(m => set.append("progress_message", m))
    Await.result(
      tasks.updateOne(owned(taskId, serverId), JDoc("$set", set)).toFuture(), timeout
    ).getMatchedCount > 0

  /** Why an in-flight execution should stop, or None (protocol
    * `check_cancellation`): the task is gone, canceled, already terminal (e.g.
    * the runtime's timeout watchdog), or reclaimed by another server.
    */
  def cancellationReason(taskId: String, serverId: String): Option[String] =
    val found = Await.result(
      tasks.find(Filters.eq("uuid", taskId))
        .projection(JDoc("state", 1).append("server_id", 1).append("_id", 0))
        .headOption(),
      timeout
    )
    found match
      case None => Some("task record no longer exists")
      case Some(d) =>
        val doc: org.bson.Document = d
        val state = String.valueOf(doc.get("state"))
        val holder = Option(doc.get("server_id")).map(_.toString).getOrElse("")
        if state == Protocol.TaskState.Canceled then Some("task was canceled (terminate-workflow)")
        else if Seq(Protocol.TaskState.Completed, Protocol.TaskState.Failed,
            Protocol.TaskState.DeadLetter, Protocol.TaskState.Ignored).contains(state)
        then Some(s"task already terminal ($state)")
        else if serverId != null && serverId != holder then Some("task was reclaimed by another runner")
        else None

  private def owned(taskId: String, serverId: String) =
    Filters.and(
      Filters.eq("uuid", taskId),
      Filters.eq("state", Protocol.TaskState.Running),
      Filters.eq("server_id", serverId)
    )

  /** Read step params from the steps collection.
    *
    * @param stepId  The step UUID
    * @return        Map of param name to value
    */
  def readStepParams(stepId: String): Map[String, Any] =
    val filter = Filters.eq("uuid", stepId)
    val result =
      Await.result(steps.find(filter).first().toFuture(), timeout)
    Option(result) match
      case Some(doc) =>
        val stepDoc = Document(doc.toBsonDocument)
        StepAttributes
          .extractParams(stepDoc)
          .map((k, v) => k -> v.value)
      case None =>
        logger.warn(s"Step not found: $stepId")
        Map.empty

  /** Fetch a referenced step's full snapshot.  Mirrors Python
    * `HandlerContext.fetch_step`: given a tagged JSON FacetRef
    * (a Map containing at least `step_id`), return a Map containing
    * the upstream step's `step_id`, `workflow_id`, `facet_name`,
    * `params`, and `returns`.  The snapshot is read-only by contract.
    */
  def fetchStep(ref: Map[String, Any]): Map[String, Any] =
    val stepId = ref.get("step_id") match
      case Some(s: String) if s.nonEmpty => s
      case _ =>
        throw new IllegalArgumentException("fetch_step: ref missing 'step_id'")
    val filter = Filters.eq("uuid", stepId)
    val result = Await.result(steps.find(filter).first().toFuture(), timeout)
    Option(result) match
      case Some(doc) =>
        val stepDoc = Document(doc.toBsonDocument)
        val params = StepAttributes.extractParams(stepDoc).map((k, v) => k -> v.value)
        val returns = StepAttributes.extractReturns(stepDoc).map((k, v) => k -> v.value)
        Map(
          "step_id" -> stepDoc.get("uuid").map(_.asString().getValue).getOrElse(""),
          "workflow_id" -> stepDoc.get("workflow_id").map(_.asString().getValue).getOrElse(""),
          "facet_name" -> stepDoc.get("facet_name").map(_.asString().getValue).getOrElse(""),
          "params" -> params,
          "returns" -> returns,
        )
      case None =>
        throw new IllegalArgumentException(s"Step not found: $stepId")

  /** Write return attributes to a step at EVENT_TRANSMIT state.
    *
    * @param stepId   The step UUID
    * @param returns  Map of return name to (value, typeHint)
    */
  def writeStepReturns(
      stepId: String,
      returns: Map[String, (Any, String)]
  ): Unit =
    val filter = Filters.and(
      Filters.eq("uuid", stepId),
      Filters.eq("state", Protocol.StepState.EventTransmit)
    )
    val updates = returns.map { case (name, (value, typeHint)) =>
      Updates.set(
        s"attributes.returns.$name",
        Document(
          "name" -> name,
          "value" -> scalaToMongo(value),
          "type_hint" -> typeHint
        )
      )
    }.toSeq

    if updates.nonEmpty then
      Await.result(
        steps.updateOne(filter, Updates.combine(updates*)).toFuture(),
        timeout
      )

  /** Merge partial return attributes into a step.
    * Unlike writeStepReturns, this does NOT require the step to be in EVENT_TRANSMIT state,
    * allowing handlers to stream partial results during execution.
    *
    * @param stepId   The step UUID
    * @param partial  Map of return name to (value, typeHint)
    */
  def updateStepReturns(
      stepId: String,
      partial: Map[String, (Any, String)]
  ): Unit =
    val filter = Filters.eq("uuid", stepId)
    val updates = partial.map { case (name, (value, typeHint)) =>
      Updates.set(
        s"attributes.returns.$name",
        Document(
          "name" -> name,
          "value" -> scalaToMongo(value),
          "type_hint" -> typeHint
        )
      )
    }.toSeq

    if updates.nonEmpty then
      Await.result(
        steps.updateOne(filter, Updates.combine(updates*)).toFuture(),
        timeout
      )

  /** Mark a task completed (protocol `complete_task`), only while this server
    * still holds it. False when the task was reclaimed or terminated: another
    * execution owns the outcome, so do NOT insert a resume.
    */
  def completeTask(task: TaskDocument, serverId: String): Boolean =
    Await.result(
      tasks.updateOne(owned(task.uuid, serverId),
        JDoc("$set", JDoc("state", Protocol.TaskState.Completed)
          .append("updated", System.currentTimeMillis()))).toFuture(),
      timeout
    ).getMatchedCount > 0

  /** Record a handler failure (protocol `fail_task`), only while this server
    * still holds the task. Returns the transition applied, or None if the task
    * was no longer this server's.
    *
    * Never writes `failed`: the runtime's stuck-step sweep sees no active and no
    * dead-lettered task for the step and creates a fresh one at retry_count 0,
    * so a failure would retry forever. A dead-lettered task makes the sweep fail
    * the step instead.
    */
  def failTask(
      task: TaskDocument,
      serverId: String,
      errorMessage: String,
      permanent: Boolean
  ): Option[MongoOps.FailTransition] =
    val now = System.currentTimeMillis()
    val t = MongoOps.failTransition(task.retryCount, task.maxRetries, permanent, now)
    val error = JDoc("message", Option(errorMessage).getOrElse(""))
    val set = JDoc("state", t.state).append("updated", now).append("retry_count", t.retryCount)
    if t.deadLettered then
      if permanent then error.append("permanent", true)
    else set.append("server_id", "").append("next_retry_after", t.nextRetryAfter)
    set.append("error", error)
    val matched = Await.result(
      tasks.updateOne(owned(task.uuid, serverId), JDoc("$set", set)).toFuture(), timeout
    ).getMatchedCount
    if matched > 0 then Some(t) else None

  /** Insert an fw:resume task so the Python RunnerService resumes the workflow.
    *
    * @param stepId      The step whose returns have been written
    * @param workflowId  The workflow to resume
    * @param taskList    Task list name for routing
    */
  def insertResumeTask(
      stepId: String,
      workflowId: String,
      taskList: String,
      facetName: String = ""
  ): Unit =
    val nowMs = System.currentTimeMillis()
    val resumeName = if facetName.nonEmpty then s"${Protocol.ResumeTaskName}:$facetName" else Protocol.ResumeTaskName
    val resumeTask = TaskDocument(
      uuid = UUID.randomUUID().toString,
      name = resumeName,
      runnerId = "",
      workflowId = workflowId,
      flowId = "",
      stepId = stepId,
      state = Protocol.TaskState.Pending,
      created = nowMs,
      updated = nowMs,
      error = None,
      taskListName = taskList,
      dataType = "resume",
      data = Some(
        Document(
          "step_id" -> stepId,
          "workflow_id" -> workflowId
        )
      )
    )
    Await.result(tasks.insertOne(resumeTask.toBson).toFuture(), timeout)

  /** Insert a step log entry for dashboard observability.
    *
    * Best-effort: errors are caught and logged at debug level.
    */
  def insertStepLog(
      stepId: String,
      workflowId: String,
      runnerId: String,
      facetName: String,
      source: String,
      level: String,
      message: String
  ): Unit =
    try
      val nowMs = System.currentTimeMillis()
      val doc = Document(
        "uuid" -> UUID.randomUUID().toString,
        "step_id" -> stepId,
        "workflow_id" -> workflowId,
        "runner_id" -> runnerId,
        "facet_name" -> facetName,
        "source" -> source,
        "level" -> level,
        "message" -> message,
        "details" -> Document(),
        "time" -> nowMs
      )
      Await.result(
        db.getCollection(Protocol.Collections.StepLogs).insertOne(doc).toFuture(),
        timeout
      )
    catch
      case e: Exception =>
        logger.debug(s"Could not save step log for step $stepId: ${e.getMessage}")

  /** Convert a Scala value to a MongoDB-compatible BsonValue. */
  private def scalaToMongo(value: Any): org.mongodb.scala.bson.BsonValue =
    import org.mongodb.scala.bson.*
    value match
      case null          => BsonNull()
      case s: String     => BsonString(s)
      case i: Int        => BsonInt32(i)
      case l: Long       => BsonInt64(l)
      case d: Double     => BsonDouble(d)
      case b: Boolean    => BsonBoolean(b)
      case seq: Seq[?]   => BsonArray.fromIterable(seq.map(scalaToMongo))
      case m: Map[?, ?] =>
        val bsonDoc = new org.bson.BsonDocument()
        m.foreach { case (k, v) =>
          bsonDoc.put(k.toString, scalaToMongo(v))
        }
        bsonDoc
      case other => BsonString(other.toString)

object MongoOps:

  /** The outcome of [[failTransition]]. */
  case class FailTransition(state: String, retryCount: Int, nextRetryAfter: Long):
    def deadLettered: Boolean = state == Protocol.TaskState.DeadLetter

  /** The protocol's failure transition (conformance/retry_cases.json). A
    * permanent error dead-letters without spending budget; otherwise the count
    * goes up and the task is either dead-lettered (budget spent) or returned to
    * pending after an exponential backoff.
    */
  def failTransition(retryCount: Int, maxRetries: Int, permanent: Boolean, now: Long): FailTransition =
    if permanent then FailTransition(Protocol.TaskState.DeadLetter, retryCount, 0L)
    else
      val next = retryCount + 1
      if maxRetries > 0 && next >= maxRetries then FailTransition(Protocol.TaskState.DeadLetter, next, 0L)
      else FailTransition(Protocol.TaskState.Pending, next, now + Protocol.retryBackoffMs(next))
