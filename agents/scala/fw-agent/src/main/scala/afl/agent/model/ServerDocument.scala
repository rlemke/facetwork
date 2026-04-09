package fw.agent.model

import org.mongodb.scala.bson.{BsonArray, BsonNull, BsonValue, Document}
import org.mongodb.scala.documentToUntypedDocument

import scala.jdk.CollectionConverters.*

/** Handler statistics for a server. */
case class HandledCount(handler: String, handled: Int, notHandled: Int)

/** Represents a server document from the AFL servers collection. */
case class ServerDocument(
    uuid: String,
    serverGroup: String,
    serviceName: String,
    serverName: String,
    serverIps: Seq[String],
    startTime: Long,
    pingTime: Long,
    topics: Seq[String],
    handlers: Seq[String],
    handled: Seq[HandledCount],
    state: String,
    manager: String,
    error: Option[Document]
)

object ServerDocument:
  /** Deserialize a MongoDB document into a ServerDocument. */
  def fromBson(doc: Document): ServerDocument =
    val underlying: org.bson.Document = doc
    ServerDocument(
      uuid = underlying.getString("uuid"),
      serverGroup = underlying.getString("server_group"),
      serviceName = underlying.getString("service_name"),
      serverName = underlying.getString("server_name"),
      serverIps = bsonArrayToStrings(doc, "server_ips"),
      startTime = underlying.getLong("start_time"),
      pingTime = underlying.getLong("ping_time"),
      topics = bsonArrayToStrings(doc, "topics"),
      handlers = bsonArrayToStrings(doc, "handlers"),
      handled = doc
        .get[BsonArray]("handled")
        .map(
          _.getValues.asScala.map { v =>
            val d = v.asDocument()
            HandledCount(
              handler = d.getString("handler").getValue,
              handled = d.getInt32("handled").getValue,
              notHandled = d.getInt32("not_handled").getValue
            )
          }.toSeq
        )
        .getOrElse(Seq.empty),
      state = underlying.getString("state"),
      manager = Option(underlying.getString("manager")).getOrElse(""),
      error = optionalSubDoc(doc, "error")
    )

  private def bsonArrayToStrings(doc: Document, key: String): Seq[String] =
    doc
      .get[BsonArray](key)
      .map(_.getValues.asScala.map(_.asString().getValue).toSeq)
      .getOrElse(Seq.empty)

  private def optionalSubDoc(doc: Document, key: String): Option[Document] =
    doc.get[BsonValue](key) match
      case Some(bv) if bv.isDocument => Some(Document(bv.asDocument()))
      case _                         => None

  extension (server: ServerDocument)
    /** Serialize to a MongoDB document. */
    def toBson: Document =
      Document(
        "uuid" -> server.uuid,
        "server_group" -> server.serverGroup,
        "service_name" -> server.serviceName,
        "server_name" -> server.serverName,
        "server_ips" -> server.serverIps,
        "start_time" -> server.startTime,
        "ping_time" -> server.pingTime,
        "topics" -> server.topics,
        "handlers" -> server.handlers,
        "handled" -> server.handled.map { h =>
          Document(
            "handler" -> h.handler,
            "handled" -> h.handled,
            "not_handled" -> h.notHandled
          )
        },
        "state" -> server.state,
        "manager" -> server.manager,
        "error" -> server.error.map(_.toBsonDocument).getOrElse(BsonNull())
      )
