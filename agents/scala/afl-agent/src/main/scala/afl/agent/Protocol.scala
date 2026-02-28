package afl.agent

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

  object TaskState:
    val Pending = "pending"
    val Running = "running"
    val Completed = "completed"
    val Failed = "failed"
    val Ignored = "ignored"
    val Canceled = "canceled"

  object StepState:
    val EventTransmit = "state.facet.execution.EventTransmit"
    val Created = "state.facet.initialization.Created"
    val StatementError = "state.facet.execution.StatementError"
    val Completed = "state.facet.completion.Completed"

  object ServerState:
    val Startup = "startup"
    val Running = "running"
    val Shutdown = "shutdown"
    val Error = "error"

  object StepLogLevel:
    val Info = "info"
    val Warning = "warning"
    val Error = "error"
    val Success = "success"

  object StepLogSource:
    val Framework = "framework"
    val Handler = "handler"

  val ResumeTaskName = "afl:resume"
  val ExecuteTaskName = "afl:execute"
