package afl.agent

import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

class RegistryRunnerSpec extends AnyFlatSpec with Matchers:

  private def makePoller(): AgentPoller =
    AgentPoller(AgentPollerConfig())

  "RegistryRunner" should "return empty effectiveHandlers when no active topics" in {
    val poller = makePoller()
    poller.register("ns.FacetA")(params => Map.empty)
    poller.register("ns.FacetB")(params => Map.empty)

    val runner = RegistryRunner(poller)

    runner.effectiveHandlers shouldBe empty
  }

  it should "delegate register to underlying poller" in {
    val poller = makePoller()
    val runner = RegistryRunner(poller)

    runner.register("ns.FacetA")(params => Map("result" -> "ok"))

    poller.registeredNames should contain("ns.FacetA")
  }

  it should "use default refresh interval of 30000ms" in {
    val poller = makePoller()
    val runner = RegistryRunner(poller)

    runner.refreshIntervalMs shouldBe 30000
  }

  it should "accept custom refresh interval" in {
    val poller = makePoller()
    val runner = RegistryRunner(poller, refreshIntervalMs = 5000)

    runner.refreshIntervalMs shouldBe 5000
  }

  "Protocol.Collections.HandlerRegistrations" should "be handler_registrations" in {
    Protocol.Collections.HandlerRegistrations shouldBe "handler_registrations"
  }
