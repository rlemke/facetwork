package fw.agent

import fw.agent.model.{HandledCount, ServerDocument}
import fw.agent.model.ServerDocument.*
import org.mongodb.scala.*
import org.mongodb.scala.bson.Document
import org.mongodb.scala.model.{Filters, ReplaceOptions}
import org.slf4j.LoggerFactory

import java.net.InetAddress
import scala.concurrent.Await
import scala.concurrent.duration.*
import scala.util.Try

/** Handles server lifecycle registration with MongoDB. */
class ServerRegistration(db: MongoDatabase, timeout: Duration = 10.seconds):

  private val logger = LoggerFactory.getLogger(getClass)
  private val servers = db.getCollection(Protocol.Collections.Servers)

  /** Register (or re-register) this server in the servers collection.
    *
    * Uses upsert so that restarts update the existing document.
    */
  def register(
      serverId: String,
      config: AgentPollerConfig,
      handlers: Seq[String]
  ): Unit =
    val nowMs = System.currentTimeMillis()
    val serverDoc = ServerDocument(
      uuid = serverId,
      serverGroup = config.serverGroup,
      serviceName = config.serviceName,
      serverName = config.serverName,
      serverIps = localIps(),
      startTime = nowMs,
      pingTime = nowMs,
      topics = handlers,
      handlers = handlers,
      handled = Seq.empty,
      state = Protocol.ServerState.Running,
      manager = "",
      error = None
    )
    val filter = Filters.eq("uuid", serverId)
    val options = ReplaceOptions().upsert(true)
    Await.result(
      servers.replaceOne(filter, serverDoc.toBson, options).toFuture(),
      timeout
    )
    logger.info(s"Server registered: $serverId (${config.serviceName})")

  /** Update the heartbeat timestamp. */
  def heartbeat(serverId: String): Unit =
    val filter = Filters.eq("uuid", serverId)
    val update = Document(
      "$set" -> Document("ping_time" -> System.currentTimeMillis())
    )
    Await.result(servers.updateOne(filter, update).toFuture(), timeout)

  /** Mark the server as shut down. */
  def deregister(serverId: String): Unit =
    val filter = Filters.eq("uuid", serverId)
    val update = Document(
      "$set" -> Document(
        "state" -> Protocol.ServerState.Shutdown,
        "ping_time" -> System.currentTimeMillis()
      )
    )
    Await.result(servers.updateOne(filter, update).toFuture(), timeout)
    logger.info(s"Server deregistered: $serverId")

  private def localIps(): Seq[String] =
    Try(Seq(InetAddress.getLocalHost.getHostAddress)).getOrElse(Seq.empty)
