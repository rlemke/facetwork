package fw.agent

import com.mongodb.{ConnectionString, MongoClientSettings}
import org.mongodb.scala.{MongoClient, MongoDatabase, ObservableFuture, documentToUntypedDocument}
import org.slf4j.LoggerFactory

import java.util.concurrent.atomic.AtomicReference
import scala.jdk.CollectionConverters.*

/** RegistryRunner wraps an AgentPoller and restricts polling to only those
  * handler names that also appear in MongoDB's `handler_registrations`
  * collection. This provides DB-driven topic filtering — handlers are
  * registered at compile time via `register()`, but only those whose facet
  * names appear in both the local registry AND the DB are polled.
  *
  * @param poller          The underlying AgentPoller to wrap
  * @param refreshIntervalMs  How often to refresh topics from DB (default 30s)
  */
class RegistryRunner(
    val poller: AgentPoller,
    val refreshIntervalMs: Long = 30000
):

  private val logger = LoggerFactory.getLogger(getClass)
  private val activeTopics = AtomicReference(Set.empty[String])
  private val handlerMetadata = AtomicReference(Map.empty[String, Map[String, Any]])
  private var refreshThread: Thread = _
  private var refreshClient: MongoClient = _

  // Wire metadata provider into the underlying poller
  poller.metadataProvider = facetName => handlerMetadata.get().get(facetName)

  /** Register a handler (delegates to the underlying poller). */
  def register(facetName: String)(
      handler: Map[String, Any] => Map[String, Any]
  ): Unit =
    poller.register(facetName)(handler)

  /** Returns the effective handlers: intersection of registered and active topics. */
  def effectiveHandlers: Seq[String] =
    val registered = poller.registeredNames.toSet
    val active = activeTopics.get()
    registered.intersect(active).toSeq

  /** Refresh active topics from the handler_registrations collection. */
  def refreshTopics(db: MongoDatabase): Unit =
    try
      import scala.concurrent.Await
      import scala.concurrent.duration.*
      val coll = db.getCollection(Protocol.Collections.HandlerRegistrations)
      val docs = Await.result(
        coll.find().toFuture(),
        10.seconds
      )
      val topics = docs.flatMap { doc =>
        Option(doc.getString("facet_name"))
      }.toSet
      activeTopics.set(topics)
      val metadata = docs.flatMap { doc =>
        val name = Option(doc.getString("facet_name"))
        val meta = doc.get[org.bson.BsonDocument]("metadata").map { bd =>
          import scala.jdk.CollectionConverters.*
          bd.entrySet().asScala.map(e => e.getKey -> (e.getValue.asInstanceOf[Any]: Any)).toMap
        }
        for n <- name; m <- meta yield n -> m
      }.toMap
      handlerMetadata.set(metadata)
      logger.debug(s"Refreshed ${topics.size} active topics from DB")
    catch
      case e: Exception =>
        logger.warn(s"Failed to refresh topics: ${e.getMessage}")

  /** Start the runner. Connects to MongoDB, performs initial refresh, starts
    * periodic refresh thread, and delegates to the poller.
    */
  def start(): Unit =
    // Create own MongoDB connection for refresh loop
    val settings = MongoClientSettings
      .builder()
      .applyConnectionString(ConnectionString(poller.config.mongoUrl))
      .build()
    refreshClient = MongoClient(settings)
    val db = refreshClient.getDatabase(poller.config.database)

    // Initial refresh
    refreshTopics(db)

    // Start periodic refresh thread
    refreshThread = Thread(() => {
      while !Thread.currentThread().isInterrupted do
        try
          Thread.sleep(refreshIntervalMs)
          refreshTopics(db)
        catch case _: InterruptedException =>
          Thread.currentThread().interrupt()
    })
    refreshThread.setDaemon(true)
    refreshThread.setName("afl-registry-refresh")
    refreshThread.start()

    poller.start()

  /** Stop the runner. */
  def stop(): Unit =
    poller.stop()
    if refreshThread != null then refreshThread.interrupt()
    if refreshClient != null then refreshClient.close()
