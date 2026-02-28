package afl.agent

import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import scala.io.Source
import scala.util.Using

class ProtocolSpec extends AnyFlatSpec with Matchers:

  // Load constants.json relative to the project root
  private val constantsJson: String =
    val paths = Seq(
      "../../protocol/constants.json",     // from agents/scala/afl-agent/
      "agents/protocol/constants.json"     // from repo root
    )
    paths
      .flatMap(p => Using(Source.fromFile(p))(_.mkString).toOption)
      .headOption
      .getOrElse(sys.error("Cannot find agents/protocol/constants.json"))

  private def extractJsonString(json: String, key: String): String =
    val pattern = s""""$key"\\s*:\\s*"([^"]+)"""".r
    pattern.findFirstMatchIn(json).map(_.group(1)).getOrElse("")

  // --- Collection names ---

  "Protocol.Collections" should "match steps collection" in {
    extractJsonString(constantsJson, "steps") shouldBe Protocol.Collections.Steps
  }

  it should "match events collection" in {
    extractJsonString(constantsJson, "events") shouldBe Protocol.Collections.Events
  }

  it should "match tasks collection" in {
    extractJsonString(constantsJson, "tasks") shouldBe Protocol.Collections.Tasks
  }

  it should "match servers collection" in {
    extractJsonString(constantsJson, "servers") shouldBe Protocol.Collections.Servers
  }

  it should "match locks collection" in {
    extractJsonString(constantsJson, "locks") shouldBe Protocol.Collections.Locks
  }

  it should "match logs collection" in {
    extractJsonString(constantsJson, "logs") shouldBe Protocol.Collections.Logs
  }

  it should "match flows collection" in {
    extractJsonString(constantsJson, "flows") shouldBe Protocol.Collections.Flows
  }

  it should "match workflows collection" in {
    extractJsonString(constantsJson, "workflows") shouldBe Protocol.Collections.Workflows
  }

  it should "match runners collection" in {
    extractJsonString(constantsJson, "runners") shouldBe Protocol.Collections.Runners
  }

  it should "match step_logs collection" in {
    extractJsonString(constantsJson, "step_logs") shouldBe Protocol.Collections.StepLogs
  }

  it should "match handler_registrations collection" in {
    extractJsonString(constantsJson, "handler_registrations") shouldBe Protocol.Collections.HandlerRegistrations
  }

  // --- Task states ---

  "Protocol.TaskState" should "match pending" in {
    extractJsonString(constantsJson, "pending") shouldBe Protocol.TaskState.Pending
  }

  it should "match running" in {
    extractJsonString(constantsJson, "running") shouldBe Protocol.TaskState.Running
  }

  it should "match completed" in {
    extractJsonString(constantsJson, "completed") shouldBe Protocol.TaskState.Completed
  }

  it should "match failed" in {
    extractJsonString(constantsJson, "failed") shouldBe Protocol.TaskState.Failed
  }

  it should "match ignored" in {
    extractJsonString(constantsJson, "ignored") shouldBe Protocol.TaskState.Ignored
  }

  it should "match canceled" in {
    extractJsonString(constantsJson, "canceled") shouldBe Protocol.TaskState.Canceled
  }

  // --- Step states ---

  "Protocol.StepState" should "match EVENT_TRANSMIT" in {
    extractJsonString(constantsJson, "EVENT_TRANSMIT") shouldBe Protocol.StepState.EventTransmit
  }

  it should "match CREATED" in {
    extractJsonString(constantsJson, "CREATED") shouldBe Protocol.StepState.Created
  }

  it should "match STATEMENT_ERROR" in {
    extractJsonString(constantsJson, "STATEMENT_ERROR") shouldBe Protocol.StepState.StatementError
  }

  it should "match COMPLETED" in {
    extractJsonString(constantsJson, "COMPLETED") shouldBe Protocol.StepState.Completed
  }

  // --- Server states ---

  "Protocol.ServerState" should "match startup" in {
    extractJsonString(constantsJson, "startup") shouldBe Protocol.ServerState.Startup
  }

  it should "match shutdown" in {
    extractJsonString(constantsJson, "shutdown") shouldBe Protocol.ServerState.Shutdown
  }

  it should "match error" in {
    extractJsonString(constantsJson, "error") shouldBe Protocol.ServerState.Error
  }

  // --- Step log levels ---

  "Protocol.StepLogLevel" should "match info" in {
    extractJsonString(constantsJson, "info") shouldBe Protocol.StepLogLevel.Info
  }

  it should "match warning" in {
    extractJsonString(constantsJson, "warning") shouldBe Protocol.StepLogLevel.Warning
  }

  it should "match error" in {
    extractJsonString(constantsJson, "error") shouldBe Protocol.StepLogLevel.Error
  }

  it should "match success" in {
    extractJsonString(constantsJson, "success") shouldBe Protocol.StepLogLevel.Success
  }

  // --- Step log sources ---

  "Protocol.StepLogSource" should "match framework" in {
    extractJsonString(constantsJson, "framework") shouldBe Protocol.StepLogSource.Framework
  }

  it should "match handler" in {
    extractJsonString(constantsJson, "handler") shouldBe Protocol.StepLogSource.Handler
  }

  // --- Protocol tasks ---

  "Protocol.ResumeTaskName" should "match afl:resume" in {
    constantsJson should include("\"afl:resume\"")
    Protocol.ResumeTaskName shouldBe "afl:resume"
  }

  "Protocol.ExecuteTaskName" should "match afl:execute" in {
    constantsJson should include("\"afl:execute\"")
    Protocol.ExecuteTaskName shouldBe "afl:execute"
  }
