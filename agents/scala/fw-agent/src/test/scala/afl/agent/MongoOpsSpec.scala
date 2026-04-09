package fw.agent

import fw.agent.model.TaskDocument
import org.mongodb.scala.bson.{BsonNull, Document}
import org.mongodb.scala.documentToUntypedDocument
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

class MongoOpsSpec extends AnyFlatSpec with Matchers:

  // Unit tests for TaskDocument serialization (no MongoDB required)

  "TaskDocument.fromBson" should "deserialize a task document" in {
    val doc = Document(
      "uuid" -> "task-1",
      "name" -> "ns.MyEvent",
      "runner_id" -> "runner-1",
      "workflow_id" -> "wf-1",
      "flow_id" -> "flow-1",
      "step_id" -> "step-1",
      "state" -> "pending",
      "created" -> 1000L,
      "updated" -> 1000L,
      "error" -> BsonNull(),
      "task_list_name" -> "default",
      "data_type" -> "event",
      "data" -> BsonNull()
    )

    val task = TaskDocument.fromBson(doc)
    task.uuid shouldBe "task-1"
    task.name shouldBe "ns.MyEvent"
    task.runnerId shouldBe "runner-1"
    task.workflowId shouldBe "wf-1"
    task.flowId shouldBe "flow-1"
    task.stepId shouldBe "step-1"
    task.state shouldBe "pending"
    task.created shouldBe 1000L
    task.updated shouldBe 1000L
    task.error shouldBe None
    task.taskListName shouldBe "default"
    task.dataType shouldBe "event"
    task.data shouldBe None
  }

  "TaskDocument.toBson" should "serialize a task document with correct field names" in {
    import fw.agent.model.TaskDocument.*
    val task = TaskDocument(
      uuid = "task-2",
      name = "fw:resume",
      runnerId = "",
      workflowId = "wf-2",
      flowId = "",
      stepId = "step-2",
      state = "pending",
      created = 2000L,
      updated = 2000L,
      error = None,
      taskListName = "default",
      dataType = "resume",
      data = Some(Document("step_id" -> "step-2", "workflow_id" -> "wf-2"))
    )

    val doc: org.bson.Document = task.toBson
    doc.getString("uuid") shouldBe "task-2"
    doc.getString("name") shouldBe "fw:resume"
    doc.getString("runner_id") shouldBe ""
    doc.getString("workflow_id") shouldBe "wf-2"
    doc.getString("step_id") shouldBe "step-2"
    doc.getString("state") shouldBe "pending"
    doc.getLong("created") shouldBe 2000L
    doc.getString("task_list_name") shouldBe "default"
    doc.getString("data_type") shouldBe "resume"
  }

  "TaskDocument round-trip" should "preserve all fields" in {
    import fw.agent.model.TaskDocument.*
    val original = TaskDocument(
      uuid = "task-3",
      name = "geo.Download",
      runnerId = "runner-3",
      workflowId = "wf-3",
      flowId = "flow-3",
      stepId = "step-3",
      state = "running",
      created = 3000L,
      updated = 3500L,
      error = Some(Document("message" -> "timeout")),
      taskListName = "special",
      dataType = "event",
      data = Some(Document("url" -> "https://example.com"))
    )

    val roundTripped = TaskDocument.fromBson(original.toBson)
    roundTripped.uuid shouldBe original.uuid
    roundTripped.name shouldBe original.name
    roundTripped.runnerId shouldBe original.runnerId
    roundTripped.workflowId shouldBe original.workflowId
    roundTripped.flowId shouldBe original.flowId
    roundTripped.stepId shouldBe original.stepId
    roundTripped.state shouldBe original.state
    roundTripped.created shouldBe original.created
    roundTripped.updated shouldBe original.updated
    roundTripped.taskListName shouldBe original.taskListName
    roundTripped.dataType shouldBe original.dataType
  }
