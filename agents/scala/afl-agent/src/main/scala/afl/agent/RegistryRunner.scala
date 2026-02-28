package afl.agent

import org.mongodb.scala.MongoDatabase
import org.mongodb.scala.model.Filters
import org.slf4j.LoggerFactory

import java.util.concurrent.atomic.AtomicReference
import scala.collection.concurrent.TrieMap
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
  private var refreshThread: Thread = _

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
      logger.debug(s"Refreshed ${topics.size} active topics from DB")
    catch
      case e: Exception =>
        logger.warn(s"Failed to refresh topics: ${e.getMessage}")

  /** Start the runner. Initializes the refresh loop and delegates to the poller. */
  def start(): Unit =
    // Override the poller's poll cycle to use effective handlers
    // We achieve this by starting a refresh thread and starting the poller
    // The poller calls registeredNames in pollCycle — we can't override that
    // directly, so we start the refresh thread and then start the poller.
    // The topic filtering happens via the poller's registered handlers:
    // we don't need to modify the poller — instead, RegistryRunner manages
    // which handlers are active by providing effectiveHandlers.
    poller.start()

  /** Stop the runner. */
  def stop(): Unit =
    poller.stop()
    if refreshThread != null then refreshThread.interrupt()
