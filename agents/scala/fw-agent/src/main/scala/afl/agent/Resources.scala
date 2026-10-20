package fw.agent

import java.lang.management.ManagementFactory
import java.nio.file.{Files, Path}
import scala.util.Try

/** Measured capacity of this host, as the protocol's resource routing needs it.
  *
  * Same rules as the Python runtime (`facetwork/runtime/base_runner.py`) and the
  * Java library: memory capacity is the container's cgroup limit where one is set
  * (what the OOM killer enforces), else the machine total; memory offered when
  * CLAIMING is what is available now, capped by the cgroup's remaining headroom.
  * A dimension that cannot be measured is OMITTED, never guessed: an absent
  * dimension makes this server decline tasks that demand it.
  */
object Resources:

  private val Gib = 1024.0 * 1024.0 * 1024.0
  private val LiveTtlMs = 5_000L

  @volatile private var liveAt = 0L
  @volatile private var liveGb: Option[Double] = None

  /** Static capacity, measured once per process: memory_gb, cpus. */
  lazy val capacity: Map[String, Double] =
    val cpus = Runtime.getRuntime.availableProcessors()
    val mem = cgroupLimit().orElse(physicalTotal())
    Map.empty[String, Double] ++
      (if cpus > 0 then Map("cpus" -> cpus.toDouble) else Map.empty) ++
      mem.filter(_ > 0).map(b => "memory_gb" -> round(b / Gib))

  /** Memory this process could get right now, in GB. */
  def availableMemoryGb(): Option[Double] = synchronized {
    val now = System.currentTimeMillis()
    if now - liveAt < LiveTtlMs then liveGb
    else
      var avail = memAvailable()
      for
        lim <- read("/sys/fs/cgroup/memory.max").filter(_.forall(_.isDigit))
        cur <- read("/sys/fs/cgroup/memory.current").filter(_.forall(_.isDigit))
        l <- Try(lim.toLong).toOption if l < (1L << 62)
        c <- Try(cur.toLong).toOption
      do
        val head = math.max(0L, l - c)
        avail = Some(avail.fold(head)(math.min(_, head)))
      liveGb = avail.map(b => round(b / Gib))
      liveAt = now
      liveGb
  }

  /** What to offer a claim: capacity with memory cut to what is free now. */
  def claimResources(): Map[String, Double] =
    val res = capacity
    if sys.env.get("FW_CLAIM_LIVE_MEMORY").contains("0") || !res.contains("memory_gb") then res
    else
      availableMemoryGb() match
        case Some(live) => res.updated("memory_gb", math.min(res("memory_gb"), live))
        case None       => res

  /** What to publish on the heartbeat as free_resources (without "at"). */
  def liveFree(): Option[Map[String, Double]] =
    availableMemoryGb().map(gb => Map("memory_gb" -> gb))

  private def cgroupLimit(): Option[Long] =
    Seq("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")
      .iterator
      .flatMap(p => read(p).map(p -> _))
      .nextOption()
      .flatMap { case (_, raw) =>
        if raw == "max" then None
        else Try(raw.toLong).toOption.filter(v => v > 0 && v < (1L << 62))
      }

  private def physicalTotal(): Option[Long] =
    ManagementFactory.getOperatingSystemMXBean match
      case os: com.sun.management.OperatingSystemMXBean => Some(os.getTotalMemorySize)
      case _                                            => None

  private def memAvailable(): Option[Long] =
    read("/proc/meminfo") match
      case Some(meminfo) =>
        meminfo.linesIterator
          .find(_.startsWith("MemAvailable"))
          .flatMap(l => Try(l.trim.split("\\s+")(1).toLong * 1024L).toOption)
      case None =>
        // No /proc (macOS dev box): the JVM's view of free physical memory.
        ManagementFactory.getOperatingSystemMXBean match
          case os: com.sun.management.OperatingSystemMXBean => Some(os.getFreeMemorySize)
          case _                                            => None

  private def read(path: String): Option[String] =
    Try(Files.readString(Path.of(path)).trim).toOption

  private def round(gb: Double): Double = math.round(gb * 100.0) / 100.0
