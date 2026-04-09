package fw.agent

import fw.agent.model.{StepAttributes, TaskDocument}
import fw.agent.model.TaskDocument.*
import org.mongodb.scala.*
import org.mongodb.scala.bson.{BsonNull, Document}
import org.mongodb.scala.model.{Filters, FindOneAndUpdateOptions, ReturnDocument, Updates}
import org.slf4j.LoggerFactory

import java.util.UUID
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

  /** Atomically claim a pending task (pending -> running).
    *
    * @param taskNames  Event facet names to match
    * @param taskList   Task list name for routing
    * @return           The claimed task, or None if no tasks available
    */
  def claimTask(
      taskNames: Seq[String],
      taskList: String
  ): Option[TaskDocument] =
    val filter = Filters.and(
      Filters.eq("state", Protocol.TaskState.Pending),
      Filters.in("name", taskNames*),
      Filters.eq("task_list_name", taskList)
    )
    val update = Updates.combine(
      Updates.set("state", Protocol.TaskState.Running),
      Updates.set("updated", System.currentTimeMillis())
    )
    val options = FindOneAndUpdateOptions().returnDocument(ReturnDocument.AFTER)

    val result = Await.result(
      tasks.findOneAndUpdate(filter, update, options).toFuture(),
      timeout
    )
    Option(result).map(doc => TaskDocument.fromBson(Document(doc.toBsonDocument)))

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

  /** Mark an event task as completed. */
  def markTaskCompleted(task: TaskDocument): Unit =
    val completed = task.copy(
      state = Protocol.TaskState.Completed,
      updated = System.currentTimeMillis()
    )
    val filter = Filters.eq("uuid", task.uuid)
    Await.result(
      tasks.replaceOne(filter, completed.toBson).toFuture(),
      timeout
    )

  /** Mark an event task as failed with an error message. */
  def markTaskFailed(task: TaskDocument, errorMessage: String): Unit =
    val failed = task.copy(
      state = Protocol.TaskState.Failed,
      updated = System.currentTimeMillis(),
      error = Some(Document("message" -> errorMessage))
    )
    val filter = Filters.eq("uuid", task.uuid)
    Await.result(
      tasks.replaceOne(filter, failed.toBson).toFuture(),
      timeout
    )

  /** Insert an afl:resume task so the Python RunnerService resumes the workflow.
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
