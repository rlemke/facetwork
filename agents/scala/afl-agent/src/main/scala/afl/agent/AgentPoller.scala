package afl.agent

import com.mongodb.{ConnectionString, MongoClientSettings}
import org.mongodb.scala.{MongoClient, MongoDatabase}
import org.slf4j.LoggerFactory

import java.util.UUID
import java.util.concurrent.{ExecutorService, Executors, TimeUnit}
import java.util.concurrent.atomic.AtomicBoolean
import scala.collection.concurrent.TrieMap

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

  // MongoDB resources (initialized lazily on start)
  private var client: MongoClient = _
  private var db: MongoDatabase = _
  private var mongoOps: MongoOps = _
  private var serverReg: ServerRegistration = _
  private var executor: ExecutorService = _
  private var heartbeatThread: Thread = _

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
      mongoOps.claimTask(registeredNames, config.taskList) match
        case Some(task) =>
          processEvent(task)
          dispatched += 1
        case None =>
          continue = false
    dispatched

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
        mongoOps.markTaskFailed(task, errorMsg)

      case Some(fn) =>
        try
          // 3. Dispatching handler
          emitStepLog(task.stepId, task.workflowId, handlerName,
            Protocol.StepLogLevel.Info, s"Dispatching handler: $handlerName")
          logger.debug(s"Processing event: $handlerName (step=${task.stepId})")

          val dispatchStart = System.currentTimeMillis()

          // Read step params
          val params = mongoOps.readStepParams(task.stepId)

          // Invoke handler
          val result = fn(params)

          val durationMs = System.currentTimeMillis() - dispatchStart

          // Convert result to returns format (value, typeHint)
          val returns = result.map { case (name, value) =>
            name -> (value -> inferTypeHint(value))
          }

          // Write returns to step
          mongoOps.writeStepReturns(task.stepId, returns)

          // Mark task completed
          mongoOps.markTaskCompleted(task)

          // Insert afl:resume task
          mongoOps.insertResumeTask(task.stepId, task.workflowId, config.taskList)

          // 4. Handler completed
          emitStepLog(task.stepId, task.workflowId, handlerName,
            Protocol.StepLogLevel.Success, s"Handler completed: $handlerName (${durationMs}ms)")
          logger.info(s"Completed event: $handlerName (step=${task.stepId})")
        catch
          case e: Exception =>
            // 5. Handler error
            emitStepLog(task.stepId, task.workflowId, handlerName,
              Protocol.StepLogLevel.Error, s"Handler error: ${e.getMessage}")
            logger.error(
              s"Handler failed for $handlerName (step=${task.stepId}): ${e.getMessage}",
              e
            )
            mongoOps.markTaskFailed(task, e.getMessage)

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

  /** Infer type hint from a Scala value. */
  private def inferTypeHint(value: Any): String = value match
    case _: Boolean    => "Boolean"
    case _: Int        => "Long"
    case _: Long       => "Long"
    case _: Double     => "Double"
    case _: Float      => "Double"
    case _: String     => "String"
    case _: Seq[?]     => "List"
    case _: Map[?, ?]  => "Map"
    case null          => "Any"
    case _             => "Any"

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
          if running.get() then serverReg.heartbeat(serverId_)
        catch case _: InterruptedException => ()
    })
    heartbeatThread.setDaemon(true)
    heartbeatThread.setName(s"afl-heartbeat-${config.serviceName}")
    heartbeatThread.start()

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
