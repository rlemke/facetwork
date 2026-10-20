package fw.agent

/** Thrown by a handler when retrying cannot change the outcome (bad input, a
  * missing artifact that will not appear). The task is dead-lettered at once
  * instead of spending its retry budget, and the step fails so catch blocks run.
  */
class PermanentError(message: String, cause: Throwable = null)
    extends RuntimeException(message, cause)
