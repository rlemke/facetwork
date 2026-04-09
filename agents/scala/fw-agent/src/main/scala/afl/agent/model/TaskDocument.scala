package fw.agent.model

import org.mongodb.scala.bson.{BsonNull, BsonValue, Document}
import org.mongodb.scala.documentToUntypedDocument

import scala.jdk.CollectionConverters.*

/** Represents a task document from the AFL tasks collection. */
case class TaskDocument(
    uuid: String,
    name: String,
    runnerId: String,
    workflowId: String,
    flowId: String,
    stepId: String,
    state: String,
    created: Long,
    updated: Long,
    error: Option[Document],
    taskListName: String,
    dataType: String,
    data: Option[Document]
)

object TaskDocument:
  /** Deserialize a MongoDB document into a TaskDocument. */
  def fromBson(doc: Document): TaskDocument =
    val underlying: org.bson.Document = doc
    TaskDocument(
      uuid = underlying.getString("uuid"),
      name = underlying.getString("name"),
      runnerId = underlying.getString("runner_id"),
      workflowId = underlying.getString("workflow_id"),
      flowId = underlying.getString("flow_id"),
      stepId = underlying.getString("step_id"),
      state = underlying.getString("state"),
      created = underlying.getLong("created"),
      updated = underlying.getLong("updated"),
      error = optionalSubDoc(doc, "error"),
      taskListName = underlying.getString("task_list_name"),
      dataType = underlying.getString("data_type"),
      data = optionalSubDoc(doc, "data")
    )

  private def optionalSubDoc(doc: Document, key: String): Option[Document] =
    doc.get[BsonValue](key) match
      case Some(bv) if bv.isDocument => Some(Document(bv.asDocument()))
      case _                         => None

  extension (task: TaskDocument)
    /** Serialize to a MongoDB document. */
    def toBson: Document =
      Document(
        "uuid" -> task.uuid,
        "name" -> task.name,
        "runner_id" -> task.runnerId,
        "workflow_id" -> task.workflowId,
        "flow_id" -> task.flowId,
        "step_id" -> task.stepId,
        "state" -> task.state,
        "created" -> task.created,
        "updated" -> task.updated,
        "error" -> task.error.map(_.toBsonDocument).getOrElse(BsonNull()),
        "task_list_name" -> task.taskListName,
        "data_type" -> task.dataType,
        "data" -> task.data.map(_.toBsonDocument).getOrElse(BsonNull())
      )
