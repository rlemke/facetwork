package fw.agent

import org.bson.{Document => JDoc}
import org.mongodb.scala.*
import org.mongodb.scala.bson.Document
import org.scalatest.BeforeAndAfterAll
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.nio.file.{Files, Path}
import java.util.UUID
import scala.concurrent.Await
import scala.concurrent.duration.*
import scala.jdk.CollectionConverters.*

/** This library against the protocol's shared conformance cases
  * (agents/protocol/conformance). The Python runtime and the Java library run
  * the same files, so a rule exists once.
  *
  * The retry cases are pure. The claim and lifecycle cases need a real MongoDB:
  * set FW_AGENT_TEST_MONGO_URL (e.g. a throwaway `mongo:7` container); they are
  * canceled otherwise. A scratch database is created and dropped.
  */
class ConformanceSpec extends AnyFlatSpec with Matchers with BeforeAndAfterAll:

  private val conf = Seq("../../protocol/conformance", "agents/protocol/conformance")
    .map(Path.of(_)).find(Files.isDirectory(_))
    .getOrElse(sys.error("Cannot find agents/protocol/conformance"))
  private def load(name: String): JDoc = JDoc.parse(Files.readString(conf.resolve(name)))

  private val mongoUrl = sys.env.get("FW_AGENT_TEST_MONGO_URL").filter(_.nonEmpty)
  private var client: MongoClient = _
  private var db: MongoDatabase = _

  override def beforeAll(): Unit =
    mongoUrl.foreach { url =>
      client = MongoClient(url)
      db = client.getDatabase("fw_agent_conformance_" + UUID.randomUUID().toString.take(8))
    }

  override def afterAll(): Unit =
    if db != null then
      Await.result(db.drop().toFuture(), 10.seconds)
      client.close()

  private def requireMongo(): Unit =
    assume(db != null, "FW_AGENT_TEST_MONGO_URL not set")

  private def tasks = db.getCollection[JDoc](Protocol.Collections.Tasks)
  private def await[T](f: scala.concurrent.Future[T]): T = Await.result(f, 10.seconds)
  private def num(d: JDoc, k: String): Long = d.get(k).asInstanceOf[Number].longValue()

  // --- retry transition (pure) ------------------------------------------------

  for c <- load("retry_cases.json").getList("cases", classOf[JDoc]).asScala do
    "failTransition" should s"satisfy retry case ${c.getString("id")}" in {
      val now = 1_000_000L
      val t = MongoOps.failTransition(c.getInteger("retry_count"), c.getInteger("max_retries"),
        c.getBoolean("permanent"), now)
      t.state shouldBe c.getString("state")
      t.retryCount shouldBe c.getInteger("retry_count_after")
      if c.containsKey("backoff_ms") then (t.nextRetryAfter - now) shouldBe num(c, "backoff_ms")
    }

  "Protocol" should "match the spec's version and timing" in {
    val spec = JDoc.parse(Files.readString(conf.getParent.resolve("constants.json")))
    spec.getString("version") shouldBe Protocol.ProtocolVersion
    val timing = spec.get("timing", classOf[JDoc])
    num(timing, "lease_ms") shouldBe Protocol.LeaseMs
    num(timing, "lease_reclaim_scan_ms") shouldBe Protocol.LeaseReclaimScanMs
    timing.getInteger("default_max_retries") shouldBe Protocol.DefaultMaxRetries
  }

  // --- claim (live) -----------------------------------------------------------

  private val claimSpec = load("claim_cases.json")
  private val srv = claimSpec.get("server", classOf[JDoc])
  private val names = srv.getList("task_names", classOf[String]).asScala.toSeq
  private val lists = srv.getList("task_lists", classOf[String]).asScala.toSeq
  private val resources = srv.get("resources", classOf[JDoc]).asScala.toMap
    .map((k, v) => k -> v.asInstanceOf[Number].doubleValue())

  private def resolve(v: Any, now: Long): Any = v match
    case d: JDoc if d.keySet().asScala == Set("rel_ms") => now + num(d, "rel_ms")
    case d: JDoc =>
      val out = JDoc()
      d.asScala.foreach((k, x) => out.append(k, resolve(x, now)))
      out
    case i: Integer => i.longValue()
    case other => other

  for c <- claimSpec.getList("cases", classOf[JDoc]).asScala do
    "claimTask" should s"satisfy claim case ${c.getString("id")}" in {
      requireMongo()
      await(tasks.drop().toFuture())
      val now = System.currentTimeMillis()
      val uuid = "t-" + c.getString("id")
      val doc = JDoc("uuid", uuid).append("runner_id", "r").append("workflow_id", "w")
        .append("flow_id", "f").append("step_id", "s-" + uuid)
        .append("created", now).append("updated", now)
      c.get("task", classOf[JDoc]).asScala.foreach((k, v) => doc.append(k, resolve(v, now)))
      await(tasks.insertOne(doc).toFuture())

      val got = MongoOps(db).claimTask(names, lists, "conformance-server", resources, reclaimExpired = true)
      got.isDefined shouldBe c.getBoolean("claimed")
      if got.isDefined then
        val stored = await(tasks.find(JDoc("uuid", uuid)).head())
        stored.getString("state") shouldBe "running"
        stored.getString("server_id") shouldBe "conformance-server"
        num(stored, "lease_expires") should be > now
        if c.containsKey("retry_count_after") then
          num(stored, "retry_count") shouldBe num(c, "retry_count_after")
    }

  // --- the rest of the lifecycle (live) ----------------------------------------

  private def claimOne(ops: MongoOps, server: String, extra: JDoc = JDoc()) =
    await(tasks.drop().toFuture())
    val now = System.currentTimeMillis()
    val doc = JDoc("uuid", "life").append("name", "ns.Facet").append("task_list_name", "ns")
      .append("state", "pending").append("step_id", "s").append("workflow_id", "w")
      .append("created", now).append("updated", now)
    doc.putAll(extra)
    await(tasks.insertOne(doc).toFuture())
    ops.claimTask(Seq("ns.Facet"), Seq("ns"), server, Map.empty, reclaimExpired = false).get

  private def stored(): JDoc = await(tasks.find(JDoc("uuid", "life")).head())
  private def setField(k: String, v: Any): Unit =
    await(tasks.updateOne(JDoc("uuid", "life"), JDoc("$set", JDoc(k, v))).toFuture())

  "completeTask" should "be ownership-gated" in {
    requireMongo()
    val ops = MongoOps(db)
    val t = claimOne(ops, "me")
    ops.completeTask(t, "someone-else") shouldBe false
    stored().getString("state") shouldBe "running"
    ops.completeTask(t, "me") shouldBe true
    stored().getString("state") shouldBe "completed"
  }

  "failTask" should "retry with backoff, then dead-letter" in {
    requireMongo()
    val ops = MongoOps(db)
    val t = claimOne(ops, "me", JDoc("retry_count", 0).append("max_retries", 2))
    val first = ops.failTask(t, "me", "boom", permanent = false).get
    val d = stored()
    d.getString("state") shouldBe "pending"
    d.getString("server_id") shouldBe ""
    num(d, "retry_count") shouldBe 1
    num(d, "next_retry_after") shouldBe first.nextRetryAfter
    d.get("error", classOf[JDoc]).getString("message") shouldBe "boom"
    ops.claimTask(Seq("ns.Facet"), Seq("ns"), "me", Map.empty, reclaimExpired = false) shouldBe None

    setField("next_retry_after", 0)
    val again = ops.claimTask(Seq("ns.Facet"), Seq("ns"), "me", Map.empty, reclaimExpired = false).get
    ops.failTask(again, "me", "boom", permanent = false).get.deadLettered shouldBe true
    stored().getString("state") shouldBe "dead_letter"
  }

  it should "dead-letter a permanent error at once" in {
    requireMongo()
    val ops = MongoOps(db)
    val t = claimOne(ops, "me")
    ops.failTask(t, "me", "bad input", permanent = true).get.deadLettered shouldBe true
    val d = stored()
    d.getString("state") shouldBe "dead_letter"
    num(d, "retry_count") shouldBe 0
    d.get("error", classOf[JDoc]).getBoolean("permanent") shouldBe true
  }

  it should "write nothing for a task this server no longer holds" in {
    requireMongo()
    val ops = MongoOps(db)
    val t = claimOne(ops, "me")
    ops.failTask(t, "not-me", "boom", permanent = false) shouldBe None
    stored().getString("state") shouldBe "running"
  }

  "renewLease and cancellationReason" should "follow ownership" in {
    requireMongo()
    val ops = MongoOps(db)
    val t = claimOne(ops, "me")
    val before = num(stored(), "lease_expires")
    ops.renewLease(t.uuid, "me", Some(40), Some("halfway")) shouldBe true
    num(stored(), "lease_expires") should be >= before
    num(stored(), "progress_pct") shouldBe 40
    ops.cancellationReason(t.uuid, "me") shouldBe None

    setField("server_id", "other")
    ops.renewLease(t.uuid, "me") shouldBe false
    ops.cancellationReason(t.uuid, "me").get should include("reclaimed")
    setField("state", "canceled")
    ops.cancellationReason(t.uuid, "me").get should include("canceled")
  }

  "ServerRegistration.heartbeat" should "report a pruned record and publish free memory" in {
    requireMongo()
    val servers = db.getCollection[JDoc](Protocol.Collections.Servers)
    await(servers.drop().toFuture())
    val reg = ServerRegistration(db)
    val cfg = AgentPollerConfig(mongoUrl = mongoUrl.get)
    reg.heartbeat("srv") shouldBe false
    reg.register("srv", cfg, Seq("ns.Facet"))
    var s = await(servers.find(JDoc("uuid", "srv")).head())
    s.get("resources", classOf[JDoc]).containsKey("memory_gb") shouldBe true
    s.getString("task_list") shouldBe cfg.taskList
    reg.heartbeat("srv") shouldBe true
    s = await(servers.find(JDoc("uuid", "srv")).head())
    s.get("free_resources", classOf[JDoc]).containsKey("memory_gb") shouldBe true

    await(servers.updateOne(JDoc("uuid", "srv"), JDoc("$set", JDoc("state", "shutdown"))).toFuture())
    reg.heartbeat("srv") shouldBe false
    await(servers.updateOne(JDoc("uuid", "srv"), JDoc("$set", JDoc("state", "quarantine"))).toFuture())
    reg.heartbeat("srv") shouldBe true
    await(servers.find(JDoc("uuid", "srv")).head()).getString("state") shouldBe "quarantine"
  }
