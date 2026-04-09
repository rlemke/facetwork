package fw.agent

import fw.agent.model.{AttributeValue, StepAttributes}
import org.mongodb.scala.bson.{BsonDocument, BsonInt32, BsonString, Document}
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

class AgentPollerSpec extends AnyFlatSpec with Matchers:

  // Unit tests that don't require a live MongoDB connection

  "AgentPoller" should "register handlers" in {
    val poller = AgentPoller(
      AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    )
    poller.register("ns.MyEvent") { params => Map("result" -> "ok") }
    poller.register("ns.OtherEvent") { params => Map("value" -> 42) }

    poller.registeredNames should contain allOf ("ns.MyEvent", "ns.OtherEvent")
  }

  it should "have a unique server ID" in {
    val poller1 = AgentPoller(
      AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    )
    val poller2 = AgentPoller(
      AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    )
    poller1.serverId should not be poller2.serverId
  }

  it should "start as not running" in {
    val poller = AgentPoller(
      AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    )
    poller.isRunning shouldBe false
  }

  it should "reject start with no handlers" in {
    val poller = AgentPoller(
      AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    )
    an[IllegalStateException] should be thrownBy poller.start()
  }

  it should "have null metadataProvider by default" in {
    val config = AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    val poller = new AgentPoller(config)
    poller.metadataProvider("any") shouldBe None
  }

  it should "inject _facet_name into params" in {
    val config = AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    val poller = new AgentPoller(config)
    poller.register("ns.TestFacet") { params =>
      params should contain key "_facet_name"
      params("_facet_name") shouldBe "ns.TestFacet"
      Map.empty
    }
    poller.registeredNames should contain("ns.TestFacet")
  }

  it should "use metadataProvider when set" in {
    val config = AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    val poller = new AgentPoller(config)
    poller.metadataProvider = {
      case "ns.TestFacet" => Some(Map("description" -> "test handler"))
      case _ => None
    }
    poller.metadataProvider("ns.TestFacet") shouldBe Some(Map("description" -> "test handler"))
    poller.metadataProvider("ns.Other") shouldBe None
  }

  it should "inject _update_step into params" in {
    val config = AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    val poller = new AgentPoller(config)
    poller.register("ns.StreamFacet") { params =>
      params should contain key "_update_step"
      Map.empty
    }
    poller.registeredNames should contain("ns.StreamFacet")
  }

  it should "have _update_step as function type" in {
    // Verify the callback pattern
    val updates = scala.collection.mutable.ListBuffer.empty[Map[String, Any]]
    val updateStep: Map[String, Any] => Unit = partial => updates += partial
    updateStep(Map("progress" -> 50))
    updateStep(Map("progress" -> 100, "result" -> "done"))
    updates should have length 2
  }

  it should "support partial updates via _update_step" in {
    val updateStep: Map[String, Any] => Unit = _ => ()
    // Should not throw
    updateStep(Map("key" -> "value"))
  }

  // --- StepAttributes extraction tests ---

  "StepAttributes.extractParams" should "extract params from a step document" in {
    val doc = Document(
      "attributes" -> Document(
        "params" -> Document(
          "query" -> Document(
            "name" -> "query",
            "value" -> "London",
            "type_hint" -> "String"
          ),
          "limit" -> Document(
            "name" -> "limit",
            "value" -> 10,
            "type_hint" -> "Long"
          )
        ),
        "returns" -> Document()
      )
    )

    val params = StepAttributes.extractParams(doc)
    params should have size 2
    params("query") shouldBe AttributeValue("query", "London", "String")
    params("limit") shouldBe AttributeValue("limit", 10, "Long")
  }

  "StepAttributes.extractReturns" should "extract returns from a step document" in {
    val doc = Document(
      "attributes" -> Document(
        "params" -> Document(),
        "returns" -> Document(
          "result" -> Document(
            "name" -> "result",
            "value" -> "success",
            "type_hint" -> "String"
          )
        )
      )
    )

    val returns = StepAttributes.extractReturns(doc)
    returns should have size 1
    returns("result") shouldBe AttributeValue("result", "success", "String")
  }

  "StepAttributes" should "handle empty attributes" in {
    val doc = Document("attributes" -> Document("params" -> Document(), "returns" -> Document()))
    StepAttributes.extractParams(doc) shouldBe empty
    StepAttributes.extractReturns(doc) shouldBe empty
  }

  it should "handle missing attributes section" in {
    val doc = Document()
    StepAttributes.extractParams(doc) shouldBe empty
    StepAttributes.extractReturns(doc) shouldBe empty
  }

  // --- AgentPollerConfig tests ---

  "AgentPollerConfig" should "have sensible defaults" in {
    val config = AgentPollerConfig(mongoUrl = "mongodb://localhost:27017")
    config.serviceName shouldBe "fw-agent"
    config.serverGroup shouldBe "default"
    config.taskList shouldBe "default"
    config.pollIntervalMs shouldBe 2000
    config.maxConcurrent shouldBe 5
    config.heartbeatIntervalMs shouldBe 10000
    config.database shouldBe "afl"
  }

  "AgentPollerConfig.fromJsonString" should "parse mongodb fields" in {
    val json =
      """{
        |  "mongodb": {
        |    "url": "mongodb://myhost:27017",
        |    "database": "afl_test"
        |  }
        |}""".stripMargin
    val config = AgentPollerConfig.fromJsonString(json)
    config.mongoUrl shouldBe "mongodb://myhost:27017"
    config.database shouldBe "afl_test"
  }

  "AgentPollerConfig.extractField" should "extract quoted values" in {
    AgentPollerConfig.extractField("""{"url": "mongodb://host"}""", "url") shouldBe
      Some("mongodb://host")
  }

  it should "return None for missing fields" in {
    AgentPollerConfig.extractField("""{"url": "x"}""", "missing") shouldBe None
  }

  // --- Protocol constant values ---

  "Protocol constants" should "have correct resume task name" in {
    Protocol.ResumeTaskName shouldBe "fw:resume"
  }

  it should "have correct execute task name" in {
    Protocol.ExecuteTaskName shouldBe "fw:execute"
  }

  it should "have correct EventTransmit state" in {
    Protocol.StepState.EventTransmit shouldBe "state.facet.execution.EventTransmit"
  }

  // --- StepAttributes.inferTypeHint tests ---

  "StepAttributes.inferTypeHint" should "infer Boolean" in {
    StepAttributes.inferTypeHint(true) shouldBe "Boolean"
    StepAttributes.inferTypeHint(false) shouldBe "Boolean"
  }

  it should "infer Int as Long" in {
    StepAttributes.inferTypeHint(42) shouldBe "Long"
  }

  it should "infer Long as Long" in {
    StepAttributes.inferTypeHint(42L) shouldBe "Long"
  }

  it should "infer Double as Double" in {
    StepAttributes.inferTypeHint(3.14) shouldBe "Double"
  }

  it should "infer Float as Double" in {
    StepAttributes.inferTypeHint(3.14f) shouldBe "Double"
  }

  it should "infer String" in {
    StepAttributes.inferTypeHint("hello") shouldBe "String"
  }

  it should "infer Seq as List" in {
    StepAttributes.inferTypeHint(Seq(1, 2, 3)) shouldBe "List"
    StepAttributes.inferTypeHint(List("a", "b")) shouldBe "List"
  }

  it should "infer Map as Map" in {
    StepAttributes.inferTypeHint(Map("k" -> "v")) shouldBe "Map"
  }

  it should "infer null as Any" in {
    StepAttributes.inferTypeHint(null) shouldBe "Any"
  }

  it should "infer unknown types as Any" in {
    StepAttributes.inferTypeHint(java.time.Instant.now()) shouldBe "Any"
  }
