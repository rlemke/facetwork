package fw.agent

import com.sun.net.httpserver.HttpServer
import org.bson.{Document => JDoc}
import org.scalatest.BeforeAndAfterEach
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.io.IOException
import java.net.InetSocketAddress
import java.nio.charset.StandardCharsets
import java.util.concurrent.{CountDownLatch, TimeUnit}
import scala.jdk.CollectionConverters.*

/** The wire contract with `facetwork.runtime.claimer` (POST /next, POST /kick). */
class ClaimProxySpec extends AnyFlatSpec with Matchers with BeforeAndAfterEach:

  private var server: HttpServer = _
  @volatile private var lastBody: JDoc = JDoc()
  @volatile private var status = 200
  @volatile private var reply = """{"task": null}"""
  @volatile private var kicked = CountDownLatch(1)

  override def beforeEach(): Unit =
    status = 200
    reply = """{"task": null}"""
    kicked = CountDownLatch(1)
    server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    server.createContext("/next", ex =>
      lastBody = JDoc.parse(String(ex.getRequestBody.readAllBytes(), StandardCharsets.UTF_8))
      val out = reply.getBytes(StandardCharsets.UTF_8)
      ex.sendResponseHeaders(status, out.length)
      ex.getResponseBody.write(out)
      ex.close()
    )
    server.createContext("/kick", ex =>
      kicked.countDown()
      ex.sendResponseHeaders(200, -1)
      ex.close()
    )
    server.start()

  override def afterEach(): Unit = server.stop(0)

  private def proxy = ClaimProxy(s"http://127.0.0.1:${server.getAddress.getPort}/")

  "ClaimProxy" should "send the same spec a direct claim uses" in {
    reply = """{"task": "t-1"}"""
    proxy.next("srv-1", Seq("ns.Facet"), Seq("ns"), Map("memory_gb" -> 6.5), 5.0, kick = true) shouldBe Some("t-1")
    lastBody.getString("server_id") shouldBe "srv-1"
    val claim = lastBody.getList("claims", classOf[java.util.List[?]]).get(0)
    claim.get(0) shouldBe java.util.List.of("ns.Facet")
    claim.get(1) shouldBe java.util.List.of("ns")
    // an agent runs no evaluator: no FFL features, no script environments
    lastBody.getList("known_features", classOf[Object]).asScala shouldBe empty
    lastBody.getList("script_environments", classOf[Object]).asScala shouldBe empty
    lastBody.get("resources", classOf[JDoc]).getDouble("memory_gb") shouldBe 6.5
    lastBody.getDouble("wait_s") shouldBe 5.0
    lastBody.getBoolean("kick") shouldBe true
  }

  it should "return None when there is no task" in {
    proxy.next("s", Seq("a.B"), Seq("a"), Map.empty, 0.0, kick = false) shouldBe None
  }

  it should "throw on an error status so the caller claims directly" in {
    status = 400
    reply = """{"error": "server_id and claims are required"}"""
    an[IOException] should be thrownBy proxy.next("s", Seq("a.B"), Seq("a"), Map.empty, 0.0, kick = false)
  }

  it should "throw when the claimer is unreachable" in {
    // discard: nothing listens
    an[IOException] should be thrownBy
      ClaimProxy("http://127.0.0.1:9").next("s", Seq("a.B"), Seq("a"), Map.empty, 0.0, kick = false)
  }

  it should "reach the claimer with a kick" in {
    proxy.kick()
    kicked.await(5, TimeUnit.SECONDS) shouldBe true
  }

  it should "claim directly when the environment names no claimer" in {
    ClaimProxy.fromEnv(Map.empty) shouldBe None
    ClaimProxy.fromEnv(Map(ClaimProxy.EnvUrl -> " http://c:8790/ ")).map(_.baseUrl) shouldBe Some("http://c:8790")
  }
