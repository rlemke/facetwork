package fw.agent

/** AFL protocol constants matching agents/protocol/constants.json */
object Protocol:

  object Collections:
    val Steps = "steps"
    val Events = "events"
    val Tasks = "tasks"
    val Servers = "servers"
    val Locks = "locks"
    val Logs = "logs"
    val Flows = "flows"
    val Workflows = "workflows"
    val Runners = "runners"
    val StepLogs = "step_logs"
    val HandlerRegistrations = "handler_registrations"

  object TaskState:
    val Pending = "pending"
    val Running = "running"
    val Completed = "completed"
    val Failed = "failed"
    val Ignored = "ignored"
    val Canceled = "canceled"
    val DeadLetter = "dead_letter"

  object StepState:
    val EventTransmit = "state.EventTransmit"
    val Created = "state.statement.Created"
    val StatementError = "state.statement.Error"
    val Completed = "state.statement.Complete"

  object ServerState:
    val Startup = "startup"
    val Running = "running"
    val Shutdown = "shutdown"
    val Error = "error"
    val Quarantine = "quarantine"

  object StepLogLevel:
    val Info = "info"
    val Warning = "warning"
    val Error = "error"
    val Success = "success"

  object StepLogSource:
    val Framework = "framework"
    val Handler = "handler"

  val ResumeTaskName = "fw:resume"
  val ExecuteTaskName = "fw:execute"

  /** Protocol version this library implements (constants.json "version"). */
  val ProtocolVersion = "1.2"

  /** Task lease: max(5 min, 15 min execution timeout + 1 min). */
  val LeaseMs: Long = 960_000L

  /** How often to look for expired leases to take over. */
  val LeaseReclaimScanMs: Long = 30_000L

  /** max_retries assumed when a task document has none. */
  val DefaultMaxRetries = 5

  /** Delay before a failed task may be claimed again: min(5000 * 2^n, 300000),
    * n = retry count AFTER the failure.
    */
  def retryBackoffMs(retryCount: Int): Long =
    if retryCount >= 6 then 300_000L
    else math.min(5_000L * (1L << math.max(0, retryCount)), 300_000L)
