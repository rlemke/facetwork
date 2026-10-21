package fw.agent

import com.mongodb.{ConnectionString, MongoClientSettings}
import org.mongodb.scala.{MongoClient, MongoDatabase}
import org.slf4j.LoggerFactory

import java.util.UUID
import java.util.concurrent.{ExecutorService, Executors, TimeUnit}
import java.util.concurrent.atomic.AtomicBoolean
import fw.agent.model.StepAttributes
import scala.collection.concurrent.TrieMap
import scala.util.Try

/** Main polling agent for processing AFL event tasks.
  *
  * Mirrors the Python AgentPoller pattern: register handlers for event facet
  * names, poll MongoDB for tasks, dispatch to handlers, write returns, and
  * insert afl:resume tasks for the Python RunnerService.
  *
  * @param config  Poller configuration
  */
class AgentPoller(val config: AgentPollerConfig):

  private val logger = LoggerFactory.getLogger(getClass)
  private val handlers = TrieMap.empty[String, Map[String, Any] => Map[String, Any]]
  private val running = AtomicBoolean(false)
  private val serverId_ = UUID.randomUUID().toString

  /** Optional metadata provider callback, set by RegistryRunner. */
  var metadataProvider: String => Option[Map[String, Any]] = _ => None

  // MongoDB resources (initialized lazily on start)
  private var client: MongoClient = _
  private var db: MongoDatabase = _
  private var mongoOps: MongoOps = _
  private var serverReg: ServerRegistration = _
  private var executor: ExecutorService = _
  private var heartbeatThread: Thread = _

  // Tasks this server is running, for lease renewal; value = still owned.
  private val activeTasks = TrieMap.empty[String, AtomicBoolean]
  @volatile private var lastReclaimScanMs = 0L

  // The host's claimer (FW_CLAIM_PROXY_URL), or None to claim directly.
  @volatile private var claimProxy: Option[ClaimProxy] = ClaimProxy.fromEnv()
  @volatile private var proxyDownUntilMs = 0L
  // A task finished since the last ask: follow-on work may be waiting.
  @volatile private var workFinished = true

  /** Claim through this claimer instead of polling MongoDB (None claims
    * directly). Defaults to `FW_CLAIM_PROXY_URL`.
    */
  def setClaimProxy(proxy: Option[ClaimProxy]): Unit =
    claimProxy = proxy
    proxyDownUntilMs = 0L

  /** Register a handler for an event facet name.
    *
    * @param facetName  Qualified event facet name (e.g. "ns.CountDocuments")
    * @param handler    Function that receives step params and returns result attributes.
    *                   The returned map values should be (value, typeHint) tuples
    *                   or plain values (type will be inferred).
    */
  def register(facetName: String)(
      handler: Map[String, Any] => Map[String, Any]
  ): Unit =
    handlers.put(facetName, handler)
    logger.debug(s"Registered handler: $facetName")

  /** Get the list of registered handler names. */
  def registeredNames: Seq[String] = handlers.keys.toSeq

  /** Get the server UUID. */
  def serverId: String = serverId_

  /** Check if the poller is currently running. */
  def isRunning: Boolean = running.get()

  /** Start the polling loop (blocking).
    *
    * Initializes MongoDB connection, registers the server, starts the heartbeat
    * thread, and enters the poll loop until stop() is called.
    */
  def start(): Unit =
    if handlers.isEmpty then
      throw new IllegalStateException("No handlers registered")

    initMongo()
    running.set(true)

    serverReg.register(serverId_, config, registeredNames)
    startHeartbeat()
    executor = Executors.newFixedThreadPool(config.maxConcurrent)

    logger.info(
      s"AgentPoller started: ${config.serviceName} " +
        s"(${handlers.size} handlers, polling every ${config.pollIntervalMs}ms)"
    )

    try
      while running.get() do
        try pollCycle()
        catch
          case _: InterruptedException => running.set(false)
          case e: Exception =>
            logger.error(s"Error in poll cycle: ${e.getMessage}", e)
        if running.get() then Thread.sleep(config.pollIntervalMs)
    finally shutdown()

  /** Signal the poller to stop. */
  def stop(): Unit =
    running.set(false)
    logger.info("Stop requested")

  /** Execute a single synchronous poll cycle (for testing).
    *
    * @return  Number of tasks dispatched in this cycle
    */
  def pollOnce(): Int =
    if client == null then initMongo()
    pollCycle()

  /** Execute a single synchronous poll cycle, dispatching up to maxConcurrent tasks. */
  private def pollCycle(): Int =
    var dispatched = 0
    var continue = true
    while continue && dispatched < config.maxConcurrent do
      // Only the first ask of a cycle long-polls; later ones fill free slots.
      claimNext(firstAsk = dispatched == 0) match
        case Some(task) =>
          processEvent(task)
          workFinished = true
          dispatched += 1
        case None =>
          continue = false
    dispatched

  /** One claim (protocol v1.2): records this server and a lease, offers the
    * memory free now, and every Protocol.LeaseReclaimScanMs also looks for a
    * running task whose lease expired.
    */
  private def claimNext(firstAsk: Boolean): Option[model.TaskDocument] =
    val now = System.currentTimeMillis()
    claimProxy.filter(_ => now >= proxyDownUntilMs) match
      case Some(proxy) =>
        try return claimViaProxy(proxy, if firstAsk then Protocol.ClaimProxyWaitS else 0.0)
        catch
          case e: java.io.IOException =>
            proxyDownUntilMs = now + Protocol.ClaimProxyRetryMs
            logger.warn(s"Claimer ${proxy.baseUrl} unreachable (${e.getMessage}); " +
              s"claiming directly for ${Protocol.ClaimProxyRetryMs / 1000}s")
      case None => ()
    val reclaim = now - lastReclaimScanMs >= Protocol.LeaseReclaimScanMs
    val task = mongoOps.claimTask(registeredNames, Seq(config.taskList), serverId_,
      Resources.claimResources(), reclaim)
    if task.isEmpty && reclaim then lastReclaimScanMs = now
    task

  /** Ask the host's claimer. It also does the lease-expiry reclaim scan for its
    * waiters. A task it returns is already held by this server; it is re-read
    * and run only if that is still true (it may have been reaped between
    * hand-over and read).
    */
  private def claimViaProxy(proxy: ClaimProxy, waitS: Double): Option[model.TaskDocument] =
    val kick = workFinished
    workFinished = false
    proxy.next(serverId_, registeredNames, Seq(config.taskList), Resources.claimResources(), waitS, kick)
      .flatMap(mongoOps.getTask)
      .filter(t => t.serverId == serverId_ && t.state == Protocol.TaskState.Running)

  /** Emit a step log entry (best-effort). */
  private def emitStepLog(
      stepId: String,
      workflowId: String,
      facetName: String,
      level: String,
      message: String
  ): Unit =
    try mongoOps.insertStepLog(stepId, workflowId, serverId_, facetName,
      Protocol.StepLogSource.Framework, level, message)
    catch case _: Exception => ()

  /** Process a single event task synchronously. */
  private def processEvent(task: model.TaskDocument): Unit =
    val handlerName = task.name
    val handler = lookupHandler(handlerName)

    // 1. Task claimed
    emitStepLog(task.stepId, task.workflowId, handlerName,
      Protocol.StepLogLevel.Info, s"Task claimed: $handlerName")

    handler match
      case None =>
        // 2. No handler found
        val errorMsg = s"No handler registered for: $handlerName"
        emitStepLog(task.stepId, task.workflowId, handlerName,
          Protocol.StepLogLevel.Error, s"Handler error: $errorMsg")
        logger.warn(s"No handler for task: $handlerName (step=${task.stepId})")
        mongoOps.failTask(task, serverId_, errorMsg, permanent = false)

      case Some(fn) =>
        val owned = AtomicBoolean(true)
        activeTasks.put(task.uuid, owned)
        try
          // 3. Dispatching handler
          emitStepLog(task.stepId, task.workflowId, handlerName,
            Protocol.StepLogLevel.Info, s"Dispatching handler: $handlerName")
          logger.debug(s"Processing event: $handlerName (step=${task.stepId})")

          val dispatchStart = System.currentTimeMillis()

          // Read step params
          val params = mongoOps.readStepParams(task.stepId)

          // Inject handler-level step_log callback
          val stepLogCallback: (String, String) => Unit = (message, level) =>
            try mongoOps.insertStepLog(task.stepId, task.workflowId, serverId_,
              handlerName, Protocol.StepLogSource.Handler, level, message)
            catch case _: Exception => ()
          val paramsWithLog = params + ("_step_log" -> stepLogCallback) + ("_facet_name" -> handlerName)
          val paramsWithMeta = metadataProvider(handlerName) match
            case Some(meta) => paramsWithLog + ("_handler_metadata" -> meta)
            case None => paramsWithLog

          // Inject _update_step callback for streaming partial results
          val updateStepCallback: Map[String, Any] => Unit = (partial) =>
            try
              val returns = partial.map { case (name, value) =>
                name -> (value -> inferTypeHint(value))
              }
              mongoOps.updateStepReturns(task.stepId, returns)
            catch case _: Exception => ()
          // _task_heartbeat(progressPct, message): renews the lease now (the
          // heartbeat thread also renews it every interval).
          val heartbeatCallback: (Option[Int], Option[String]) => Unit = (pct, msg) =>
            try if !mongoOps.renewLease(task.uuid, serverId_, pct, msg) then owned.set(false)
            catch case _: Exception => ()
          // _is_cancelled(): true once the task was canceled, finished by someone
          // else, or reclaimed. A long handler should check it at batch
          // boundaries and return early.
          val cancelledCallback: () => Boolean = () =>
            !owned.get() ||
              Try(mongoOps.cancellationReason(task.uuid, serverId_).isDefined).getOrElse(false)
          val paramsWithUpdate = paramsWithMeta +
            ("_update_step" -> updateStepCallback) +
            ("_task_heartbeat" -> heartbeatCallback) +
            ("_is_cancelled" -> cancelledCallback)

          // Invoke handler
          val result = fn(paramsWithUpdate)

          val durationMs = System.currentTimeMillis() - dispatchStart

          // Convert result to returns format (value, typeHint)
          val returns = result.map { case (name, value) =>
            name -> (value -> inferTypeHint(value))
          }

          // Another execution may own this task now (reclaimed after a lost
          // lease, canceled, or finished by the timeout watchdog): write nothing.
          mongoOps.cancellationReason(task.uuid, serverId_) match
            case Some(reason) =>
              emitStepLog(task.stepId, task.workflowId, handlerName,
                Protocol.StepLogLevel.Warning, s"Handler result discarded: $reason")
              logger.warn(s"Discarding result of $handlerName ${task.uuid}: $reason")
            case None =>
              mongoOps.writeStepReturns(task.stepId, returns)
              // Complete (ownership-gated), then signal the Python RunnerService.
              if !mongoOps.completeTask(task, serverId_) then
                emitStepLog(task.stepId, task.workflowId, handlerName,
                  Protocol.StepLogLevel.Warning,
                  "Handler result discarded: task was reclaimed before completion")
              else
                mongoOps.insertResumeTask(task.stepId, task.workflowId, task.taskListName, task.name)
                // 4. Handler completed
                emitStepLog(task.stepId, task.workflowId, handlerName,
                  Protocol.StepLogLevel.Success, s"Handler completed: $handlerName (${durationMs}ms)")
                logger.info(s"Completed event: $handlerName (step=${task.stepId})")
        catch
          case e: Exception =>
            // 5. Handler error: retry with backoff, or dead-letter (never 'failed').
            val permanent = e.isInstanceOf[PermanentError]
            val what = mongoOps.failTask(task, serverId_, e.getMessage, permanent) match
              case Some(t) if t.deadLettered && permanent => "dead-lettered (permanent)"
              case Some(t) if t.deadLettered => s"dead-lettered after ${t.retryCount} attempts"
              case Some(t) =>
                s"retry ${t.retryCount}/${task.maxRetries} in " +
                  s"${(t.nextRetryAfter - System.currentTimeMillis()) / 1000}s"
              case None => "not recorded: task no longer held by this server"
            emitStepLog(task.stepId, task.workflowId, handlerName,
              Protocol.StepLogLevel.Error, s"Handler error ($what): ${e.getMessage}")
            logger.error(
              s"Handler failed for $handlerName (step=${task.stepId}, $what): ${e.getMessage}",
              e
            )
        finally activeTasks.remove(task.uuid)

  /** Look up handler by exact name, then try short name for qualified names. */
  private def lookupHandler(
      name: String
  ): Option[Map[String, Any] => Map[String, Any]] =
    handlers.get(name).orElse {
      // If name is qualified (ns.Facet), try the short name (Facet)
      val dotIndex = name.lastIndexOf('.')
      if dotIndex >= 0 then handlers.get(name.substring(dotIndex + 1))
      else None
    }

  /** Infer type hint from a Scala value (delegates to StepAttributes). */
  private def inferTypeHint(value: Any): String =
    StepAttributes.inferTypeHint(value)

  private def initMongo(): Unit =
    val settings = MongoClientSettings
      .builder()
      .applyConnectionString(ConnectionString(config.mongoUrl))
      .build()
    client = MongoClient(settings)
    db = client.getDatabase(config.database)
    mongoOps = MongoOps(db)
    serverReg = ServerRegistration(db)

  private def startHeartbeat(): Unit =
    heartbeatThread = Thread(() => {
      while running.get() do
        try
          Thread.sleep(config.heartbeatIntervalMs)
          if running.get() then heartbeatOnce()
        catch
          case _: InterruptedException => ()
          case e: Exception => logger.warn(s"Heartbeat error: ${e.getMessage}")
    })
    heartbeatThread.setDaemon(true)
    heartbeatThread.setName(s"afl-heartbeat-${config.serviceName}")
    heartbeatThread.start()

  /** Ping the server record (re-registering a pruned one) and renew the lease
    * of every task still running here.
    */
  private def heartbeatOnce(): Unit =
    if !serverReg.heartbeat(serverId_) && running.get() then
      logger.warn(s"Server record $serverId_ missing or marked shutdown -- registering again")
      serverReg.register(serverId_, config, registeredNames)
    activeTasks.foreach { case (taskId, owned) =>
      try if !mongoOps.renewLease(taskId, serverId_) then owned.set(false)
      catch case e: Exception => logger.debug(s"Lease renewal failed for $taskId: ${e.getMessage}")
    }

  private def shutdown(): Unit =
    logger.info("Shutting down...")
    try serverReg.deregister(serverId_)
    catch case e: Exception => logger.warn(s"Deregister failed: ${e.getMessage}")

    if executor != null then
      executor.shutdown()
      executor.awaitTermination(5, TimeUnit.SECONDS)

    if heartbeatThread != null then heartbeatThread.interrupt()

    if client != null then client.close()
    logger.info("Shutdown complete")
