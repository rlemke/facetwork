package fw.agent

import org.bson.{Document => JDoc}

import java.io.IOException
import java.net.URI
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.time.Duration
import scala.jdk.CollectionConverters.*

/** Client for the host's claimer (`facetwork.runtime.claimer`).
  *
  * The claimer polls MongoDB once for every runner on its machine and hands
  * each task over with an ownership-gated write, so a task it returns is
  * already `running` under this agent's server id. The agent sends the same
  * spec its direct claim uses: handler names and task list, the memory free
  * now, and no FFL features or script environments (an agent declines both).
  */
class ClaimProxy(url: String):

  val baseUrl: String = url.stripSuffix("/")

  private val http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build()

  /** Ask for one task, waiting up to `waitS` seconds for one to appear.
    *
    * @param kick work just finished here, so follow-on tasks may be waiting
    * @return the uuid of a task now held by `serverId`, or None
    * @throws IOException when the claimer cannot be reached or answers badly;
    *                     the caller claims directly instead
    */
  def next(
      serverId: String,
      taskNames: Seq[String],
      taskLists: Seq[String],
      resources: Map[String, Double],
      waitS: Double,
      kick: Boolean
  ): Option[String] =
    val body = JDoc("server_id", serverId)
      .append("claims", java.util.List.of(java.util.List.of(taskNames.asJava, taskLists.asJava)))
      .append("known_features", java.util.List.of())
      .append("resources", JDoc(resources.map((k, v) => k -> java.lang.Double.valueOf(v).asInstanceOf[AnyRef]).asJava))
      .append("script_environments", java.util.List.of())
      .append("wait_s", waitS)
      .append("kick", kick)
    val req = HttpRequest.newBuilder(URI.create(s"$baseUrl/next"))
      .timeout(Duration.ofMillis(((waitS + 30) * 1000).toLong))
      .header("Content-Type", "application/json")
      .POST(HttpRequest.BodyPublishers.ofString(body.toJson))
      .build()
    val resp =
      try http.send(req, HttpResponse.BodyHandlers.ofString())
      catch
        case e: InterruptedException =>
          Thread.currentThread().interrupt()
          throw IOException("interrupted waiting for the claimer", e)
    if resp.statusCode() != 200 then
      throw IOException(s"claimer answered HTTP ${resp.statusCode()}: ${resp.body()}")
    val reply =
      try JDoc.parse(resp.body())
      catch case e: Exception => throw IOException(s"claimer reply is not JSON: ${e.getMessage}", e)
    Option(reply.get("task")).collect { case s: String if s.nonEmpty => s }

  /** Tell the claimer work may be waiting. Fire-and-forget: a lost kick costs latency only. */
  def kick(): Unit =
    val req = HttpRequest.newBuilder(URI.create(s"$baseUrl/kick"))
      .timeout(Duration.ofSeconds(2))
      .POST(HttpRequest.BodyPublishers.ofString("{}"))
      .build()
    http.sendAsync(req, HttpResponse.BodyHandlers.discarding()).exceptionally(_ => null)

object ClaimProxy:

  /** Env var naming the claimer, e.g. `http://runner-claimer:8790`. */
  val EnvUrl = "FW_CLAIM_PROXY_URL"

  /** The claimer named by `FW_CLAIM_PROXY_URL`, or None when it is unset. */
  def fromEnv(env: Map[String, String] = sys.env): Option[ClaimProxy] =
    env.get(EnvUrl).map(_.trim).filter(_.nonEmpty).map(ClaimProxy(_))
