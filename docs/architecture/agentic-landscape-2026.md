# Facetwork and the agentic-software landscape, late 2026

*Research note, written 2026-10-31. Every external claim below carries a link and a
date. Where a claim is a vendor announcement, a preview, or a figure from a single
worked example, the text says so. Sources reached only through secondary write-ups
are marked as such. Statements about Facetwork cite its own documents.*

## 1. Summary

Over the last twelve months the agent ecosystem has moved toward ideas Facetwork
already has. Agents are now expected to survive crashes, which is durable execution.
Standards bodies govern the protocols. Plans are increasingly separated from the
untrusted data they process. Reusable procedures are stored and recalled rather
than regenerated. Facetwork's core bet is that Claude authors a typed, validated FFL
workflow, a catalog keeps it, and a leaderless fleet runs it durably. That puts it
on the side of the current evidence that says "compose deterministic tools under a
plan" rather than "let the model drive every step". Its gaps are in the
*interfaces* the ecosystem has standardised on, not in the core idea:

- its MCP server is stdio-only and predates the 2026-07-28 spec, so it has no
  Tasks, HTTP transport, or authorization;
- it emits no OpenTelemetry;
- its control plane is unauthenticated;
- the LLM loop inside an agent handler is not journaled per call;
- human approval lives in the chat transcript, not in the runtime;
- it has no evaluation harness for its central claim, that composing from a
  catalog beats generating.

The ranked features in §5 close those gaps in order of risk. Three more, from the
workflow-language papers in §2.7, address gaps in FFL itself: steps that must run
on the same host, joining two lists in a `foreach`, and suggesting the next facet.
A fourth, from the exactly-once papers in §2.8, closes the gap in the runtime
that execution-semantics.md calls "no fencing token": stamping each claim with
an attempt number and writing outputs under it.

## 2. The landscape by theme

### 2.1 Durable execution for agents

- **"Agent durability" now means the agent loop is checkpointed per model call and
  per tool call**, so a crash resumes instead of replaying, and paid calls are not
  repeated. Anthropic's write-up of its multi-agent research system (published
  2025-06-13, *measured on their internal system*) says they "built systems that can
  resume from where the agent was when the errors occurred". They pair regular
  checkpoints with retry logic and use "rainbow deployments" so that in-flight
  agents are not broken by a code change
  ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- **Temporal + OpenAI Agents SDK.** Announced in public preview 2025-07-30 and
  **GA for Python 2026-03-23**. OpenAI made the SDK's `Runner` abstract so Temporal
  could substitute one that executes "every agent invocation … through a Temporal
  Activity" ([Temporal blog](https://temporal.io/blog/announcing-openai-agents-sdk-integration)).
  The durability claims in that post are not benchmarked.
- **DBOS, Restate, Dapr** are listed alongside Temporal as durable back ends for
  the OpenAI Agents SDK. DBOS runs on Postgres or SQLite with no separate server.
  Pydantic and DBOS announced a partnership on 2026-02-19. A DBOS plugin for Google
  ADK runs each LLM call as a DBOS step
  ([ADK DBOS plugin](https://adk.dev/integrations/dbos/),
  [Pydantic](https://pydantic.dev/articles/pydantic-ai-dbos.md)). *Secondary and
  vendor documentation; I found no primary Restate 2026 release note.*
- **LangGraph** checkpoints after every node to a pluggable store. It reached 1.0
  in October 2025, and LangChain agents now run on it
  ([LangChain changelog](https://changelog.langchain.com/announcements/ann_lxOZRCoFw2Qn9)).
  *The GA date comes from secondary sources.*
- **Managed runtimes.**
  - AWS Bedrock AgentCore Runtime sessions run for up to 8 hours. A separate
    "Runtime Instances" type runs for up to 14 days
    ([AWS](https://aws.amazon.com/blogs/aws/runtime-instances-persistent-compute-for-production-ai-agents-on-amazon-bedrock-agentcore/)).
  - Temporal Serverless Workers on AgentCore is in *prerelease*
    ([Temporal docs](https://docs.temporal.io/serverless-workers/agentcore)).
  - Anthropic's Claude Managed Agents (hosted harness, sandbox and session log,
    with credentials kept in a vault outside the sandbox) entered *public beta*
    2026-04-08. Multi-agent coordination and memory are in research preview
    ([summary](https://alternativeto.net/news/2026/4/anthropic-launches-claude-managed-agents-to-accelerate-ai-agent-development-and-deployment);
    *secondary sources only*).
  - My searches returned nothing usable on Cloudflare's agent runtime, so it is
    omitted rather than guessed at.
- **Research.** A 2026 TLA+ model of multi-agent shared state formalises four
  concurrency anomalies (stale-generation, phantom-tool, causal-cascade,
  tool-effect reordering) "under deterministic-generation semantics — the regime
  durable-execution engines enforce by deterministic replay"
  ([arXiv 2606.17182](https://arxiv.org/abs/2606.17182)).

### 2.2 Agent protocols

- **MCP governance.** Anthropic donated MCP to the Agentic AI Foundation (AAIF), a
  Linux Foundation directed fund co-founded with Block and OpenAI, on 2025-12-09
  ([Wikipedia](https://en.wikipedia.org/wiki/Model_Context_Protocol)).
- **MCP 2025-11-25** added experimental **Tasks** ("call-now, fetch-later" for
  long-running requests) and URL-mode elicitation
  ([spec: Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks),
  [WorkOS](https://workos.com/blog/mcp-2025-11-25-spec-update)).
- **MCP 2026-07-28 (shipped, with Tier-1 SDKs for TypeScript, Python, Go and C#)**
  ([MCP blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)):
  - The core is **stateless**: `initialize` and the session header are retired,
    and each request carries version and capabilities in `_meta`.
  - **Multi Round-Trip Requests** replace server-initiated elicitation, sampling
    and roots. The server answers `input_required` and the client retries with
    the answers.
  - Tasks moved out of core into the `io.modelcontextprotocol/tasks`
    **extension**, now with a poll-based `tasks/get`.
  - `Mcp-Method`/`Mcp-Name` headers let gateways route requests.
  - List results carry TTLs.
  - Authorization is hardened: RFC 9207 `iss` validation, and Dynamic Client
    Registration is deprecated in favour of Client ID Metadata Documents.
  - Roots, Sampling, Logging and the legacy HTTP+SSE transport are deprecated
    with at least a 12-month runway.
- **MCP roadmap (2026-08-22)**
  ([MCP blog](https://blog.modelcontextprotocol.io/posts/mcp-roadmap/)). Its
  priorities are:
  - server-initiated events (webhooks/channels) so clients stop polling;
  - maturing Tasks back into the spec;
  - one HTTP-native transport, including over stdio;
  - agent identity and delegation via Workload Identity Federation and token
    exchange;
  - "progressive discovery", where a server exposes a small entry point and
    reveals more tools as the task narrows.

  These are *plans, not shipped features*.
- **MCP Registry.** It launched as a *preview* on 2025-09-08
  ([MCP blog](https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/)).
  I could not confirm general availability.
- **A2A (Agent2Agent).**
  - v1.0 is the first stable spec, reportedly frozen 2026-03-12, with JSON-RPC,
    gRPC and REST bindings.
  - The Linux Foundation reported more than 150 supporting organisations at the
    one-year mark ([LF press release](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year)).
  - Governance moved to AAIF in August 2026 (*secondary sources disagree on the
    exact date*).
- **Agent Skills.** These are folders containing a `SKILL.md`, loaded
  progressively. Anthropic shipped them 2025-10-16 and published the format as an
  open standard at agentskills.io on 2025-12-18
  ([Claude support](https://support.claude.com/en/articles/12512176-what-are-skills)).
  Counts of adopting platforms vary by source.
- **"Code execution with MCP"** (Anthropic, 2025-11-04;
  [engineering post](https://www.anthropic.com/engineering/code-execution-with-mcp)).
  The proposal is to present tools as a code API, so that intermediate data stays
  in the execution environment instead of passing through the model's context.
  Its headline figure, 150,000 → 2,000 tokens, comes from *one worked example*.
  Independent re-measurements reported 78.5% fewer input tokens at 7% higher
  latency (*secondary*).

### 2.3 Multi-agent orchestration

- **Frameworks.**
  - Microsoft Agent Framework 1.0 went GA on 2026-04-03 for .NET and Python,
    unifying Semantic Kernel and AutoGen. AutoGen has been in maintenance mode
    since October 2025
    ([Visual Studio Magazine](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx)).
  - Google ADK is moving from template agents (Sequential/Parallel/Loop) to a
    graph workflow runtime with fan-out/fan-in, retries and human-in-the-loop
    steps. *Its release status is unclear from the sources I found*
    ([InfoWorld](https://www.infoworld.com/article/4153857/hands-on-with-the-google-agent-development-kit.html)).
  - OpenAI's AgentKit (visual Agent Builder) launched October 2025.
  - I found no reliable 2026 primary source for CrewAI.
- **Convergence.** Every major SDK has added an explicit **graph or workflow
  layer** on top of free-running agents. That is a DSL-shaped thing, and the
  frameworks are converging on FFL's shape.
- **Evidence on what works.**
  - Anthropic measured a lead-plus-subagents system beating a single agent by
    90.2% on its research eval, at about 15× the tokens of a chat. Token usage
    alone explained 80% of the variance on BrowseComp
    ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
  - Against that, MAST (Cemri et al., NeurIPS 2025 Datasets & Benchmarks)
    annotated 1,600+ traces across 7 multi-agent frameworks. It found that gains
    over single agents "are often minimal", and it groups 14 failure modes into
    specification, inter-agent misalignment and task verification
    ([arXiv 2503.13657](https://arxiv.org/abs/2503.13657)).
  - The consistent lesson is that multi-agent pays off on wide, parallelisable
    work and fails on coordination and verification.

### 2.4 Agents writing plans and code: verification, sandboxes, injection

- **Plan-then-execute as a security property.** CaMeL extracts control and data
  flow from the trusted query, so untrusted tool output "can never impact the
  program flow". It enforces capability policies at tool calls and solves 77% of
  AgentDojo tasks with provable security, against 84% undefended
  ([arXiv 2503.18813](https://arxiv.org/abs/2503.18813)). The companion
  design-patterns paper (2025-06) generalises this into six patterns built on one
  rule: once an agent has ingested untrusted input, that input must not be able to
  trigger consequential actions
  ([arXiv 2506.08837](https://arxiv.org/abs/2506.08837)).
- **Tool metadata is an attack surface.** Log-To-Leak (ACM workshop, 2026-10)
  injects instructions through MCP tool descriptions. Across 18 model-server
  pairs it reports attack success rates of 62.64–100%
  ([arXiv 2610.36108](https://arxiv.org/abs/2610.36108)). An ACM TOSEM study maps
  16 MCP threat scenarios across the server lifecycle
  ([arXiv 2503.23278](https://arxiv.org/abs/2503.23278)).
- **Deterministic mediation for science.** A May 2026 paper ran the same analysis
  on four platforms, four times each, and compared model-written code with a
  typed tool that the model only parameterises. The typed tool gave identical
  results; regenerated code did not
  ([arXiv 2605.13245](https://arxiv.org/abs/2605.13245)).
- **Verifying LLM plans.** VeriPlan (CHI 2025) model-checks LLM-generated plans
  and feeds the violations back to the model
  ([arXiv 2502.17898](https://arxiv.org/abs/2502.17898)). An ACM DEBS 2026 paper
  evaluates LLMs generating serverless workflows directly
  ([DOI](https://doi.org/10.1145/3809481.3816480)).
- **Reusable workflows as memory.**
  - Agent Workflow Memory (2024) and AFlow (ICLR 2025) induce or search for
    reusable workflows ([2409.07429](https://arxiv.org/abs/2409.07429),
    [2410.10762](https://arxiv.org/abs/2410.10762)).
  - SKILL.nb (2026-06) addresses what comes next: reused workflows "may fail
    under environment drift". It uses execution evidence to decide which steps
    become code and when to revise them
    ([arXiv 2606.08049](https://arxiv.org/abs/2606.08049)).

### 2.5 Observability, evaluation, cost

- **OpenTelemetry GenAI conventions.** They define `invoke_agent`, `chat`,
  `execute_tool` and `invoke_workflow` spans
  ([OTel blog](https://opentelemetry.io/blog/2026/genai-observability/)). As of
  mid-2026 they are still marked *Development*, not Stable, and moved to a
  dedicated repository in June 2026 (v1.42.0)
  ([Dash0](https://www.dash0.com/knowledge/opentelemetry-genai-semantic-conventions-explained);
  *secondary*). Vendors are converging on them anyway.
- **Cost.**
  - Per-task cost, not per-token price, is what matters for agents.
  - Routing work (RouteLLM, 2024) reports near-frontier quality with a minority
    of calls sent to the strong model.
  - Practitioner reports note that switching models mid-session discards the
    prefix cache and can erase the savings
    ([TDS](https://towardsdatascience.com/the-hidden-cost-of-llm-routing-why-switching-models-costs-more/)).
    *These figures are blog-reported, not verified.*
- **Serving-side research.** Helium (PACMMOD/SIGMOD 2026) treats an agentic
  workflow as a query plan, with LLM calls as operators and caching across calls
  ([arXiv 2603.16104](https://arxiv.org/abs/2603.16104)). Orla (ACM CAIS 2026)
  separates workflow-stage policy, such as which model serves which stage, from
  request execution ([arXiv 2603.13605](https://arxiv.org/abs/2603.13605)).
- **New venue.** ACM's first Conference on AI and Agentic Systems (CAIS 2026) is
  a sign the systems community now treats this as its own area.

### 2.6 Scientific workflows and AI

- **PROV-AGENT** extends W3C PROV to record prompts, responses and agent
  decisions within the wider workflow, and uses MCP as the capture point
  ([arXiv 2508.02866](https://arxiv.org/abs/2508.02866)). ORNL's SC'25-workshop
  paper on agents driving facility experiments captures agent decisions as
  structured provenance messages
  ([DOI](https://doi.org/10.1145/3731599.3767592)).
- **A 2026 survey of "AI scientist" systems** screened 125 candidates and coded 26
  entries in full text. It found that code release is now common, but
  "reproducibility-grade and claim-verification" artifacts are not, which it
  calls the *verification gap*
  ([arXiv 2608.05179](https://arxiv.org/abs/2608.05179)).
- **Claim-aware observability** for scientific agents (2026-08) argues that
  logging every model call is insufficient. What needs tracing is
  artifact → claim relations ([arXiv 2608.18312](https://arxiv.org/abs/2608.18312)).
- **Community.** The AGENT4SC 2026 workshop at IEEE eScience lists provenance and
  reproducibility as core topics ([site](https://agent4sc.github.io/)).

### 2.7 Workflow languages, authoring help and step placement (added 2026-10-31)

These four ACM papers were supplied as PDFs after the first draft and were read
in full (see §7). Two are current, two are older work that FFL descends from.

- **FlowPilot** (Esmailoghli and Weidlich, PACMMOD/SIGMOD 2026,
  [DOI 10.1145/3786653](https://doi.org/10.1145/3786653)) suggests the next
  operator while someone writes a Nextflow workflow. It indexes past workflows by
  paths of operators *and the data passed between them*, retrieves the nearest
  ones, and ranks candidates with a Markov chain built from them. Top-1 accuracy is
  56–58% on three corpora, against 1–9% for Code LLMs (CodeT5+, CodeLlama,
  DeepSeek Coder, Llama 4) and at most 38% for the other baselines. Its motivation
  is the point for Facetwork: Code LLMs do badly on workflow code because there is
  so little of it (under 5,000 Nextflow repositories on GitHub). FFL has no public
  corpus at all, so the paper is independent support for the approach Facetwork
  already takes: give the model rule docs, canonical examples, `fw_capabilities`
  and the catalog rather than rely on what it learned in training. FFL also has
  something Nextflow lacks: typed facet signatures, so candidates whose inputs do
  not accept the previous step's outputs can be ruled out before ranking. The
  corpus here is far smaller (36 domain repos plus the catalog), so the paper's
  accuracy should not be expected.
- **Gwendia** (Montagnat, Isnard, Glatard, Maheshwari and Blay-Fornarino, WORKS
  2009, [DOI 10.1145/1645164.1645171](https://doi.org/10.1145/1645164.1645171))
  is a data-driven grid workflow language built on array programming. It is the
  closest of these papers to FFL's own design, and it has four ideas FFL does not:
  - *Iteration strategies* say how several input lists combine: **cross**
    (every combination), **dot** (pair by index) and **match** (pair records
    whose user-defined tags are equal). FFL's `foreach` binds one variable;
    nesting gives a cross product, but pairing two lists by index or by key has
    to be done in a handler.
  - *A void value* Ø. A failed item produces Ø, and a step that receives Ø
    passes it on without running, so the rest of the fan-out finishes and the
    gap stays visible in the result. FFL has per-step `catch` but no value that
    means "this item is missing".
  - *Port depth*: an input that takes a whole list fires once (a reduction), an
    output that yields a list increases the nesting. This is the general form of
    FFL's `+=` aggregation.
  - *Foreseeable vs unforeseeable* constructs: whether the full DAG of a run can
    be built before it starts (dot, cross, fixed `for`) or only during it
    (conditionals, `while`, outputs of unknown length).

  It also already has what FFL has: results keep their input index whatever order
  the iterations finish in, as FFL's `+=` follows iteration order, and control
  links exist only for orderings with no data flowing, which is FFL's `after`.
- **AdaFuse** (Lin, Liao, Zhou, Xu, Xu, Chen and Guo, ACM TACO 2026,
  [DOI 10.1145/3859287](https://doi.org/10.1145/3859287)) is about *function
  fusion*: placing neighbouring serverless functions in one container so they
  stop paying for remote calls, which were 62–79% of function latency in their
  Social Network benchmark. Fusing fixes one resource configuration for every
  path through the fused group, so a fixed fusion over-provisions when traffic
  moves between paths. AdaFuse re-plans the fusion as path probabilities change
  (beam search plus per-instance sizing) and migrates gradually; it reports up to
  49.4% less allocated resource at the same SLO attainment. For Facetwork the
  relevant half is the first one, together with Quilt (§6): there is no way to say
  that steps must run in the same place. That is open item 1.1
  ([open-items.md](../operations/open-items.md)), where an unpinned publish step
  ran on a host with an empty output directory and reported success. The ~2.6 s
  per-step overhead ([delegating-to-ray.md](../guides/delegating-to-ray.md)) is
  the cost side. The adaptive half (millisecond SLOs, autoscaling) does not carry
  over to Facetwork's batch workloads.
- **Distributed Programming Languages** (Andrews, ACM '82,
  [DOI 10.1145/800174.809772](https://doi.org/10.1145/800174.809772)) classifies
  how processes communicate: direct naming, ports, or global mailboxes with many
  senders and many receivers. Facetwork's task collection is a mailbox, and its
  name-filtered claim resembles SR's "receive the message that satisfies a
  condition". Andrews notes mailboxes are costly without special support; a single
  atomic `find_one_and_update` is that support here. His argument that a remote
  call runs exactly once only if the service is idempotent or wrapped in an atomic
  transaction (as Argus did) is the reasoning behind Facetwork's at-least-once
  delivery with an idempotency requirement on handlers
  ([execution-semantics.md](../reference/execution-semantics.md)).

### 2.8 Exactly-once without replay, step merging, and agent safety (added 2026-10-31)

Eleven more ACM papers were downloaded by hand once §7 showed they could not be
fetched automatically, and were read in full. Three pages of the OSR paper are
scanned images with no text layer and were read as images. They fall into four
groups.

**Exactly-once over a store that is not transactional.** Three serverless papers
make a duplicate execution harmless in the same way. The control store commits a
step's result only if it is still in the state the worker read, and the data
store accepts a write only if it carries a per-step sequence number newer than
the one it holds. Facetwork has the first half and not the second.

- **Durable Functions** (Burckhardt, Gillum, Justo, Kallas, McMahon and
  Meiklejohn, PACMPL 5 OOPSLA, Art. 133, 2021,
  [DOI 10.1145/3485510](https://doi.org/10.1145/3485510)) formalises Azure's
  orchestrations, activities and entities in three models. The *compute-storage*
  model has volatile workers that commit a work item's result atomically, and
  only if the input queue and state are unchanged, so "only one worker can
  commit it". It is proved to simulate the fault-free, exactly-once model. The
  *replay* model, the one that requires deterministic orchestration code, is
  proved equivalent to it. The paper says plainly that several workers may run
  the same item at once, because failure cannot be detected reliably. It leaves
  external calls out of the proof because their duplication is observable. For
  Facetwork this is the formal version of
  [execution-semantics.md](../reference/execution-semantics.md) §1.2. Facetwork
  is the compute-storage model without the replay layer, which is why it has no
  determinism requirement (thesis ch. 7). Its `save_task_if_owned` is the
  conditional commit, though keyed by the runner's `server_id` rather than by an
  attempt. Its object-store writes fall into the category that the proof
  excludes. There are no measurements; this is a semantics paper.
- **Boki** (Jia and Witchel, SOSP 2021,
  [DOI 10.1145/3477132.3483541](https://doi.org/10.1145/3477132.3483541)) gives
  serverless functions a shared log ordered by a metalog. Its workflow library
  honours only the first log record for a step, and makes a database write
  idempotent with the condition `Version < seqnum`, where the sequence number
  comes from the step's log record. That is a data write fenced by the control
  log. It reaches 1.16 M appends/s at 6.42 ms p99. Its workflow library is 4.3–4.7×
  faster than Beldi, and exactly-once with transactions costs 1.8–3.0× median
  latency over an unsafe baseline. The workflow library is measured only without
  failures.
- **Halfmoon / asymmetric logging** (Qi, Feng, Liu and Jin, ACM TOCS 43(1–2),
  Art. 3, 2025, [DOI 10.1145/3725985](https://doi.org/10.1145/3725985)) shows
  exactly-once needs only one side logged: reads or writes, not both. It proves
  that this is the minimum. Against Boki it cuts median end-to-end latency by
  20–40%, overhead above the unsafe baseline by 1.5–4×, and log storage by
  1.2–3.4×. Its §4 names Facetwork's reaper case exactly: an instance the runtime
  believes has crashed but which is still running. It resolves it with a
  conditional append that succeeds only at the expected position. The
  millisecond savings do not matter at Facetwork's ~2.6 s per step, and it needs
  deterministic functions, which handlers are not. Two of its design points do
  carry over:
  - Its read-optimised variant writes new versions and commits by logging a
    pointer. Applied to Facetwork, an output written under
    `…/<step_id>/<attempt>/` and published by the conditional completion
    update leaves a superseded runner's files unreferenced rather than
    overwriting the winner's.
  - At 1–2.5 ms per log operation on its store, journaling each LLM call
    (feature 3) costs little next to the call.
- **Argus** (Liskov and Scheifler, "Guardians and Actions", TOPLAS 5(3), 1983,
  [DOI 10.1145/2166.357215](https://doi.org/10.1145/2166.357215)) is the
  transaction route that Andrews (§2.7) sets against idempotency. Guardians keep
  stable state, and their handlers run as subactions that roll back and are
  at-most-once. Facetwork is the reverse on every axis. Its runners are
  stateless and its handlers at-least-once. A failed step undoes nothing, and no
  atomic object spans Mongo and the object store. Argus concedes the point that
  matters here: effects outside the system cannot be undone. Its advice is to
  split work into two top-level actions and defer external effects to the
  second. That is the shape of feature 5, where `external` steps run only after
  an approval step.

**Merging steps.** **Quilt** (Zhang and Angel, SOSP 2025,
[DOI 10.1145/3731569.3764830](https://doi.org/10.1145/3731569.3764830)) merges
functions of one tenant's workflow into one process at the LLVM IR level. It
profiles the call graph, then cuts the fewest frequency-weighted call edges such
that every merged group fits a container's CPU and memory limit. Median latency
falls 45.6–71.0% on 9 of 11 DeathStarBench workflows, and throughput on
compose-post rises 11.2–12.9×. Merging *everything* into one container is worse:
on a CPU-heavy workflow it gains 42% latency but loses 11.6% throughput to
throttling, where the best two-way split gains 50.8%. For feature 11 it supplies
three things:
- *A grouping rule.* Only connected subgraphs entered through one step are
  merged.
- *A budget rule.* CPU adds up across the group. Memory adds up only for calls
  that run concurrently, because a synchronous callee releases its memory. In
  FFL terms, a group's floor is the sum of its members' `Requires(memory_gb)`,
  with a `foreach … limit N` counting N times. It can be checked at compile time.
- *A warning.* Merged, "any function crash becomes a workflow crash", and the
  authors leave failure isolation open.

  So a Facetwork co-location group should pin placement and keep per-step task
  records and retries rather than fuse handlers. Quilt merges to save call
  latency. Facetwork's stronger reason is shared local files, which Quilt does
  not model. It evaluates Rust only, with faked databases.

**Agent workloads as serverless stages.** "Agentic Workflows are Serverless
Applications, so deploy them that way!" (Dougherty, Lambert, Wang, Xu, Achermann
and Fedorova, SIGOPS OSR 60(1), 2026,
[DOI 10.1145/3830422.3830426](https://doi.org/10.1145/3830422.3830426)) is a
position paper with a short characterisation of three agents. In one example
only 4.3 s of 37.3 s is inference; the rest is model load and engine start. A
RAG agent spends 65% of its 3 s in inference. A coding agent splits 44 s about
evenly between inference and tools, with one 26 s tool call. A research agent
makes 14 inference calls in 128 s, and some components sit idle for up to 30 s.
It proposes that each stage be deployed and scaled separately, annotated by how
much state it keeps: none, files, or files plus live processes. FFL already is
the stage graph it asks for, and a D3 delegation already releases the
orchestrator while it waits. The useful addition is that state annotation. A
facet that keeps files between calls is precisely one that must be co-located
(feature 11), so the annotation would say when co-location is required rather
than merely faster.

**Enforcing effects and tracking provenance.**
- **Typed tool mediation** (Zhou and Jia, ASE 2026, pp. 3567–3571,
  [DOI 10.1145/3832783.3834637](https://doi.org/10.1145/3832783.3834637)) turns
  every tool request into schema-validated JSON and passes it to one reference
  monitor. The monitor classifies the paths, database objects and network sinks
  itself, and an unknown one is denied. It does not trust the model's account of
  what a call does. It answers *allow*, *approval required* or *deny*, and a
  separate judge scores each run from file-system snapshots, database diffs and
  network captures, never from the model's report. On 48 adversarial tasks it
  blocked 48/48 with both models tested, against 32–38 for a prompt-only defence
  and 23–26 undefended. Benign completion was unchanged at 21/24 and 17/24. The
  suite is the authors' own, the attacker is not adaptive, and arbitrary shell
  and SQL are outside its boundary. For feature 5 the lesson is that an
  `Effect(kind=…)` declared by the facet's author is exactly the input this
  monitor refuses to trust. The runtime can derive an effect class from what it
  can see: namespace, `fw.exec`/`fw.http` use, storage URIs and the credentials
  in `requires_env`. It should add *approval required* as a third outcome at
  dispatch.
- **TraceCaps** (Catarino, Mamede, Melo and Abreu, ICSE-NIER 2026, pp. 166–170,
  [DOI 10.1145/3786582.3786832](https://doi.org/10.1145/3786582.3786832)) wraps
  each agent event in a signed capsule hash-linked to the previous one. It
  carries a risk score that can only rise, so later harmless steps cannot dilute
  an earlier risky one. It has no quantitative evaluation, one illustrative trace,
  and its risk score is a statistical heuristic, not information flow. Two ideas
  carry over:
  - labels that can only be added (feature 6), propagated along FFL's step graph,
    which supplies the parent–child structure the paper says it lacks for
    concurrent branches;
  - a hash chain over a run's step records, which would make an
    `fw.provenance` manifest tamper-evident.

**Generating and suggesting workflows.**
- **Evaluating LLMs for serverless workflow generation** (Ristov, Gritsch,
  Unterhofer and Breu, DEBS 2026, pp. 5–10,
  [DOI 10.1145/3809481.3816480](https://doi.org/10.1145/3809481.3816480)) plans
  a chain of cloud services from a typed service catalogue, then has the model
  write Python, feeding each run's error back until it works or 10 iterations
  pass. Claude 3.5 Sonnet was the most reliable of four models. GPT-4o succeeded
  in about half the Google Cloud runs and Mistral in about 5%, mostly because of
  deprecated APIs. Five senior engineers judged none of the code production-ready.
  For feature 7 its method is a useful start and its gaps are the lesson:
  - success means "ran", with no check of the output;
  - trial counts are not given;
  - failures were classified by hand.

  Facetwork's harness can classify failures automatically by `rule_id` and judge
  output with `fw.compare` against a reference run. The deprecated-API failures
  are an argument for typed facets over generated SDK code.
- **Search, adapt, and reuse** (Cohen-Boulakia and Leser, SIGMOD Record 40(2),
  2011, [DOI 10.1145/2034863.2034865](https://doi.org/10.1145/2034863.2034865))
  argues that domain scientists do not want to write workflows. They want to find
  one, supply data and run it, so research should turn to repositories with
  search, similarity, adaptation and comparison of runs. That is the catalog and
  `fw_catalog_match`. It also names what the catalog lacks:
  - matching on input and output types and on graph shape, not only intent text;
  - saying which part of a request matched.

  It notes that graph matching is NP-complete in general but polynomial for
  series-parallel graphs, which most `andThen`/`foreach` graphs are.
- **Auto-Suggest** (Yan and He, SIGMOD 2020,
  [DOI 10.1145/3318464.3389738](https://doi.org/10.1145/3318464.3389738)), a
  FlowPilot baseline, replays GitHub notebooks to log pandas calls with their
  input and output tables. It learns the next operator from the sequence *and*
  the current data: precision@1 is 0.72, against 0.56 for the sequence model
  alone and 0.32 for data features alone. That is over only 7 operators, far
  easier than ranking hundreds of facets. For feature 13, Facetwork already logs
  executed step sequences and typed returns, which are its equivalent of that
  training log.

## 3. Where Facetwork already matches or leads

1. **Plans are programs, separate from data.**
   - Claude writes FFL, the validator checks it with structured `rule_id`s, and
     deterministic handlers do the work. That is the plan-then-execute shape
     CaMeL and the design-patterns paper argue for (§2.4).
   - It is also the "typed mediation" that 2605.13245 measured as reproducible.
   - The catalog's **review gate** and `/ffl-first`'s Gate A (workflow shown
     before it runs) and Gate B (handler shown before use) put a human between
     generation and effect
     ([ffl-first.md](../guides/ffl-first.md),
     [claude-workflow-catalog.md](claude-workflow-catalog.md)).
   - Facetwork reached this shape for authoring reasons, not security ones. The
     security benefit is real but partial (§4).
2. **Durable, leaderless execution with documented semantics.**
   - Step state lives in MongoDB. Tasks are leased, heartbeated and reclaimed by a
     reaper. Repair has seven checks.
   - The execution-semantics document states plainly that delivery is
     at-least-once, there is no fencing token, and paid LLM calls are among "the
     cases that actually bite"
     ([execution-semantics.md](../reference/execution-semantics.md)).
   - That candour is ahead of most agent-framework docs, which assert durability
     without stating the duplicate-execution window.
3. **A memory of solved requests with immutability.**
   - Catalog revisions are content-hashed, append-only and pinned per run.
   - `fw_catalog_match` returns a `reuse` / `review` / `author_new` verdict
     before anything is authored.
   - This is the Agent Workflow Memory / SKILL.nb idea, with stronger guarantees
     than either: a reused workflow cannot change underneath a run.
4. **Discovery by intent with effect and cost metadata.**
   - `fw_capabilities` filters facets on `Effect(kind=…)` and `Cost(tier=…)`
     ([composable-facet-library.md](composable-facet-library.md) §6).
   - It is the same idea as the MCP roadmap's "progressive discovery", available
     today.
   - Adoption is measured honestly: 610 of 1,226 facets declare an effect.
5. **Work stays out of the model's context.** A workflow processes gigabytes of
   data on the fleet, and Claude sees only parameters and results. That is
   architecturally what "code execution with MCP" proposes, done at workflow
   granularity.
6. **Provenance and reproducibility as built-ins.**
   - `fw.provenance.Manifest` reports inputs with no recorded provenance instead
     of omitting them.
   - `fw.http` writes per-artifact `.meta.json`.
   - `fw.compare` reports the highest level at which two outputs agree, never a
     boolean (CLAUDE.md, built-in facets).
   - This addresses the "verification gap" (2608.05179) more directly than most
     agent frameworks do.
7. **Delegation to other engines.** The D3 detached-submit-and-resume adapter
   derives its external run ID from `step_id` for idempotent submission
   ([external-engine-delegation.md](external-engine-delegation.md)). It is the
   same pattern MCP Tasks and A2A tasks use: claim, submit, park, resume.
8. **Capability-aware placement.** Resource floors, environment hashes and
   server groups route work only to hosts that can finish it. Most agent runtimes
   assume homogeneous serverless compute.
9. **Cooperative cancellation and long-handler discipline**
   (`ctx.raise_if_cancelled()`, heartbeats). These match what Temporal-style
   runtimes require of activities.

## 4. Where Facetwork is behind

1. **The MCP surface is a generation old.**
   - `python -m facetwork.mcp` uses `stdio_server` only
     (`facetwork/mcp/__main__.py`) and pins `mcp>=1.0,<2` (`pyproject.toml`).
   - It has no Streamable HTTP and no authorization.
   - There is no MCP Task for a running workflow: `fw_catalog_run` returns and
     the caller polls by hand.
   - It cannot be offered to a remote agent or a hosted runtime such as Managed
     Agents or AgentCore.
2. **No telemetry export.**
   - There is no OpenTelemetry and no `/metrics` endpoint.
     [comparison-to-production-platforms.md](../operations/comparison-to-production-platforms.md)
     §3.3 already measured the metrics gap.
   - The step log is rich but private to the dashboard.
3. **The control plane is unauthenticated** (same document, §3.1). That gap
   grows when LLM-authored workflows can reach it.
4. **Agent loops inside handlers are not durable per call.**
   - `ClaudeAgentRunner`/`LLMHandler` run a multi-turn tool loop inside one task
     ([llm-integration.md](../guides/llm-integration.md) §2.7).
   - A reclaim replays the whole loop and pays again.
   - Streaming is "Not started".
5. **Human approval is conversational, not durable.** Gates A and B happen in the
   Claude session, so if the session ends the approval state is lost. ADK, LangGraph
   and Temporal all treat a human wait as a first-class durable step.
6. **The central claim is unmeasured.** The composition paper is still a SEED. Its
   controlled ablation (§4) has not been run
   ([paper-llm-composition.md](../thesis/paper-llm-composition.md)).
7. **Catalog search is keyword-ranked, and nothing re-validates old revisions.**
   - The catalog's "What's not here yet" lists semantic search.
   - Nothing re-checks a stored revision when the library under it changes. This
     is the drift SKILL.nb targets.
   - `fw util ffl-audit` does this for domain repos, not for catalog revisions.
8. **No taint tracking.** A prompt-block output can flow into an `external`
   (mutating) facet with no check. The plan is fixed, but its arguments are not.

## 5. Recommended features, ranked

Sizes: S ≈ days, M ≈ 1–3 weeks, L ≈ a month or more.

**1. MCP 2026-07-28 server: Streamable HTTP, OAuth, and workflow runs as MCP Tasks. (M)**
- *What:* Move to the current Python SDK. Serve over Streamable HTTP alongside
  stdio. Map `fw_catalog_run`/`fw_execute_workflow` to the Tasks extension: the
  Task ID is the `runner_id`, and `tasks/get` reads runner state. Use Multi
  Round-Trip Requests (`input_required`) for missing parameters.
- *Why:* Tasks is the ecosystem's standard for exactly what Facetwork does, which
  is long, durable, call-now-fetch-later work (§2.2). It is how a remote agent or
  hosted runtime would consume Facetwork.
- *Risk of not doing it:* Facetwork stays reachable only from a local Claude Code
  session. Sampling and the legacy transport are on a 12-month deprecation clock,
  and the SDK major version will move past the `<2` pin.

**2. Durable human approval as a runtime step. (S–M)**
- *What:* A built-in `fw.human.Approve(subject, payload) => (approved, by,
  comment)` facet. It parks like a D3 delegation and appears in a dashboard inbox.
  It can also be answered through MCP elicitation. Gates A and B become catalog
  states rather than chat turns, and an `external`-mutate facet can require one by
  policy.
- *Why:* Every major framework now has a durable human-in-the-loop primitive
  (§2.3). The prompt-injection patterns require a gate before consequential
  actions (§2.4).
- *Risk of not doing it:* Approvals are lost when a session ends, and there is no
  audit trail of who approved what. That is the first question for any team use.

**3. Per-call journaling for LLM and agent facets. (M)**
- *What:* Inside `LLMHandler`/`ClaudeAgentRunner`, record each model call and
  tool call to the step log or object store, keyed by `(step_id, turn)`. A
  reclaimed task then replays recorded turns instead of re-calling the API.
  Combine this with a request ID derived from `step_id`.
- *Why:* This is what "agent durability" now means in Temporal, DBOS and the
  Anthropic research system (§2.1). Facetwork's own semantics document names paid
  LLM calls as a case that bites.
- *Risk of not doing it:* Every reaper reclaim of an agent step double-bills and
  can produce a different answer, so runs stop being reproducible.

**4. OpenTelemetry export using the GenAI conventions. (M)**
- *What:* Emit an `invoke_workflow` span per run, a span per step, `execute_tool`
  per handler dispatch, and `chat` spans with token usage from `TokenUsage`.
  Propagate W3C trace context through task documents so a span survives a hop
  between hosts. Add a Prometheus `/metrics` endpoint on the same pass.
- *Why:* This is the de facto standard even while marked Development (§2.5). It
  also covers the metrics gap Facetwork's own platform comparison ranks highly.
- *Risk of not doing it:* Facetwork runs stay invisible to the observability
  stacks organisations already operate. Cost and latency analysis stays limited to
  bespoke `fw maint workflow-stats`.

**5. Authenticate the control plane, and turn effect classes into enforced policy. (M–L)**
- *What:* Mongo and MinIO authentication with per-role users, plus registry auth.
  Then a run-level policy: a workflow authored or modified by an LLM may not
  invoke `Effect(kind="external")` mutating facets, or publish, without an
  approval step (feature 2). Facets with undeclared effects are treated as
  mutating.
- *Why:* The CaMeL and design-patterns results (§2.4) show the plan/data
  separation is only a defence when consequential actions are gated. MCP tool
  descriptions are a measured injection vector (Log-To-Leak, 62–100% attack
  success). The ASE 2026 mediation paper (§2.8) blocked 48 of 48 adversarial
  tasks by deriving each call's effect itself rather than trusting a
  declaration. Facetwork should do the same, from namespace, storage URIs and
  credentials, and use the author's `Effect` only as a lower bound.
- *Risk of not doing it:* Facetwork lets an LLM write and run programs against a
  database with no credentials. One injected instruction in a fetched document
  then reaches the whole fleet.

**6. Taint labels on values from untrusted sources. (M)**
- *What:* Mark outputs of `fw.http`, prompt blocks and other `external`-read
  facets as untrusted in the step record. The evaluator propagates the label
  through references. A policy check before dispatch refuses to pass tainted
  values into a mutating facet's sensitive parameters (destination, recipient,
  command) unless the run has an approval.
- *Why:* This is CaMeL's capability tracking expressed in Facetwork's existing
  dataflow, and the compiler already knows the edges. Labels should only ever be
  added, never removed (TraceCaps, §2.8).
- *Risk of not doing it:* The control flow is safe but the data flow is not. An
  injected URL or path still reaches `fw.exec` or a publisher.

**7. A composition evaluation harness. (M)**
- *What:* Run the ablation from the composition paper: the same NL requests,
  authored (a) with `fw_capabilities` plus the catalog, (b) with the library but
  no catalog, and (c) as free Python. Measure validity, success, tokens and
  reviewer edits. Keep it as a regression suite for `fw_catalog_match` verdicts,
  and label failures with MAST categories. Judge success by comparing output
  with a reference run (`fw.compare`), not by "it ran", and report trial
  counts. The DEBS 2026 study (§2.8) did neither.
- *Why:* MAST shows that unmeasured agent architectures often deliver "minimal"
  gains (§2.3). Facetwork's moat claim currently rests on one worked example.
- *Risk of not doing it:* The core thesis stays an assertion, and there is no
  signal when a library or catalog change makes composition worse.

**8. Catalog lifecycle: drift re-validation and semantic match. (S–M)**
- *What:* A scheduled job re-compiles every published catalog revision against
  the current library and current handler registrations, and marks it `stale`
  with the rule ID that failed. Add embedding similarity to `CatalogService.match`
  alongside the keyword score.
- *Why:* SKILL.nb (§2.4) identifies drift as the failure mode of reusable agent
  workflows. Facetwork already learned this for domain repos (`ffl-audit` exists
  because "nothing forces them to keep up").
- *Risk of not doing it:* A `reuse` verdict points at a workflow that no longer
  runs. That silently undermines the memory-of-solved-requests idea.

**9. Cost ledger and budget per run, with model choice on the facet. (S–M)**
- *What:* Aggregate `TokenUsage` and a price table into a per-run cost on the
  runner record. Add `with Budget(usd=…)` to fail or pause a run that exceeds it.
  Let prompt-block facets declare a model tier, so cheap stages use cheap models
  without switching models mid-conversation.
- *Why:* Token usage dominates both agent performance and agent cost (§2.3, §2.5).
  Orla and Helium show that per-stage model policy is the right granularity.
- *Risk of not doing it:* A wide `foreach` over prompt blocks has no ceiling. The
  first accidental 3,000-way LLM fan-out finds out the price afterwards.

**10. (Optional) An A2A Agent Card for the catalog. (S)**
- *What:* Publish catalog workflows as A2A skills behind the same HTTP server as
  feature 1.
- *Why:* A2A 1.0 is stable and governed by AAIF (§2.2).
- *Risk of not doing it:* Low for now. MCP Tasks covers the agent-calls-Facetwork
  case, and A2A matters only if peers that are agents, rather than tools, need to
  delegate to it.

**Further candidates from §2.7 and §2.8 (added 2026-10-31, not ranked against 1–10).**

- **11. A co-location group for steps. (M)** Declare that several steps run in
  one task on one host, so local files pass between them safely. Today this is
  done by merging steps into one handler, as `BuildAdminSet` does. *Why:* it is
  open item 1.1, which has already produced a run that was 36% refreshed with
  every step green, and it removes the per-step overhead inside the group
  (AdaFuse, Quilt). Quilt (§2.8) supplies the compile-time check: a group must
  be entered through one step, and its memory floor is the sum of its members'
  `Requires`, counting a `foreach … limit N` N times. It also gives the warning:
  fusing handlers into one process loses failure isolation. So the group should
  pin placement and keep per-step tasks. *Risk of not doing it:* each domain
  keeps hand-merging, and the next unpinned hand-off fails silently the same
  way.
- **12. Zip and keyed-join `foreach`, and an explicit missing-item result. (M)**
  Gwendia's dot and match products as `foreach` forms over two lists, plus a
  value that marks an iteration as missing so the fan-out finishes and the gap is
  reported. *Why:* pairing by index or key currently needs a handler, and a
  fan-out is all-or-nothing unless every step carries a `catch`. It matches the
  repo's rule that an unmeasured value is `None`, never `0.0`. *Risk of not doing
  it:* domains keep writing join handlers and partial results stay implicit.
- **13. Next-facet suggestions in `fw ffl lsp` and `fw_capabilities`. (S–M)**
  Rank likely next facets from the domain repos and the catalog, filtered first
  by type compatibility with the previous step's outputs (FlowPilot). Combine
  the sequence with the current data's types, which in Auto-Suggest beat either
  alone (0.72 vs 0.56 and 0.32, §2.8). *Why:* it
  helps a person writing FFL as well as Claude. *Risk of not doing it:* low;
  completion stays alphabetical.
- **14. Attempt-fenced step outputs. (S–M)** *Implemented 2026-10-31 for
  Python runners (`tests/runtime/test_attempt_fence.py`); see
  execution-semantics.md §1.2 for what it does and does not cover.* Give every claim a
  monotonically increasing `attempt` on the task. Condition the completion
  update on it as well as on `server_id`. Have built-in storage facets write
  under `…/<step_id>/<attempt>/`, so a step's result points at one attempt's
  files. *Why:* Durable Functions, Boki and Halfmoon (§2.8) all make a
  duplicate execution harmless this way: a conditional commit in the control
  store plus a per-step number on the data side. Facetwork has the first
  (`save_task_if_owned`) and lacks the second, which is the "no fencing token"
  of execution-semantics.md §1.2. Keying the commit by `server_id` alone also
  cannot tell two claims by the same runner apart. *Risk of not doing it:* a
  runner the reaper declared dead can still overwrite the winner's artifacts,
  and idempotency stays a burden on every handler author rather than a property
  of the platform. *As built:* the counter, the gated completion and the
  cancellation check landed in the runtime. Built-in storage facets were **not**
  changed, because they write to paths the FFL author names, and silently moving
  those would break every workflow that reads them. Handlers opt in with
  `HandlerContext.attempt_path(base)`. Still open: the polyglot agent libraries
  do not bump the counter, and nothing collects a losing attempt's directory.

## 6. Papers worth reading

| Title | Venue, year | Link | Why |
|---|---|---|---|
| Defeating Prompt Injections by Design (CaMeL) | arXiv, 2025 | [2503.18813](https://arxiv.org/abs/2503.18813) | Plan/data separation plus capabilities; the security case for FFL's shape (features 5–6) |
| Design Patterns for Securing LLM Agents against Prompt Injections | arXiv, 2025 | [2506.08837](https://arxiv.org/abs/2506.08837) | Six patterns; plan-then-execute is one |
| Why Do Multi-Agent LLM Systems Fail? (MAST) | NeurIPS D&B, 2025 | [2503.13657](https://arxiv.org/abs/2503.13657) | Failure taxonomy to label Facetwork's own runs (feature 7) |
| It's not the Language Model, it's the Tool | arXiv, 2026 | [2605.13245](https://arxiv.org/abs/2605.13245) | Measured reproducibility of typed tools vs generated code; Facetwork's thesis in miniature |
| SKILL.nb: Selective Formalization and Gated Execution for Durable Agent Workflows | arXiv, 2026 | [2606.08049](https://arxiv.org/abs/2606.08049) | Lifecycle and drift of reused workflows (feature 8) |
| PROV-AGENT: Unified Provenance for … Agentic Workflows | arXiv, 2025 | [2508.02866](https://arxiv.org/abs/2508.02866) | W3C PROV for agent decisions; a target model for `fw.provenance` |
| Autonomous Research Agents: … the Verification Gap | arXiv, 2026 | [2608.05179](https://arxiv.org/abs/2608.05179) | Defines what reproducibility-grade means for agent science |
| Efficient LLM Serving for Agentic Workflows: A Data Systems Perspective (Helium) | PACMMOD (SIGMOD), 2026 | [DOI 10.1145/3802046](https://doi.org/10.1145/3802046), [arXiv 2603.16104](https://arxiv.org/abs/2603.16104) | Workflow-as-query-plan; cross-call caching |
| Orla: A Library for Serving LLM-Based Multi-Agent Systems | ACM CAIS, 2026 | [DOI 10.1145/3786335.3813227](https://doi.org/10.1145/3786335.3813227), [arXiv 2603.13605](https://arxiv.org/abs/2603.13605) | Separates stage-to-model policy from execution (feature 9) |
| Model Context Protocol: Landscape, Security Threats, and Future Research Directions | ACM TOSEM, 2026 | [DOI 10.1145/3796519](https://doi.org/10.1145/3796519), [arXiv 2503.23278](https://arxiv.org/abs/2503.23278) | Threat model for exposing Facetwork over MCP |
| Log-To-Leak: Prompt Injection … via the Model Context Protocol | ACM workshop (agent security), 2026 | [DOI 10.1145/3847320.3847399](https://doi.org/10.1145/3847320.3847399), [arXiv 2610.36108](https://arxiv.org/abs/2610.36108) | Measured tool-metadata injection rates |
| VeriPlan: Integrating Formal Verification and LLMs into End-User Planning | ACM CHI, 2025 | [DOI 10.1145/3706598.3714113](https://doi.org/10.1145/3706598.3714113), [arXiv 2502.17898](https://arxiv.org/abs/2502.17898) | Model-check-then-repair loop for generated plans; analogue of the validator loop |
| TraceCaps: Inline Provenance and Risk Enforcement for Agentic Software Engineering | ICSE NIER, 2026 | [DOI 10.1145/3786582.3786832](https://doi.org/10.1145/3786582.3786832) | Hash-chained event capsules with a risk score that only rises; no evaluation (feature 6) |
| Introducing Safe LLM-Agent Execution via Typed Tool Mediation and World-Effect Evaluation | IEEE/ACM ASE, 2026 | [DOI 10.1145/3832783.3834637](https://doi.org/10.1145/3832783.3834637) | A reference monitor derives each call's effect itself; 48/48 adversarial tasks blocked (feature 5) |
| Evaluating LLMs for Automated Serverless Workflow Generation | ACM DEBS, 2026 | [DOI 10.1145/3809481.3816480](https://doi.org/10.1145/3809481.3816480) | NL → serverless workflow with error feedback; success means "ran" (feature 7) |
| Agentic Workflows are Serverless Applications, so deploy them that way! | ACM SIGOPS OSR, 2026 | [DOI 10.1145/3830422.3830426](https://doi.org/10.1145/3830422.3830426) | Agent stages deployed separately and annotated by statefulness (feature 11) |
| Quilt: Resource-aware Merging of Serverless Workflows | ACM SOSP, 2025 | [DOI 10.1145/3731569.3764830](https://doi.org/10.1145/3731569.3764830) | Graph-cut merging under a CPU/memory budget; merged crashes are not isolated (feature 11) |
| Efficient Fault Tolerance for Stateful Serverless Computing with Asymmetric Logging | ACM TOCS, 2025 | [DOI 10.1145/3725985](https://doi.org/10.1145/3725985) | Exactly-once needs only reads or writes logged; names the alive-but-presumed-dead peer (feature 14) |
| AI Agents for Enabling Autonomous Experiments at ORNL's HPC and Manufacturing User Facilities | SC'25 Workshops (ACM), 2025 | [DOI 10.1145/3731599.3767592](https://doi.org/10.1145/3731599.3767592) | Agents driving real facility workflows with provenance capture |
| Verified Detection and Prevention of Concurrency Anomalies in Multi-Agent LLM Systems | arXiv, 2026 | [2606.17182](https://arxiv.org/abs/2606.17182) | TLA+ anomalies under durable-execution replay; check against Facetwork's at-least-once model |
| FlowPilot: A Suggestion System for Designing Scientific Workflows | PACMMOD (SIGMOD), 2026 | [DOI 10.1145/3786653](https://doi.org/10.1145/3786653) | Next-operator suggestion from past workflows; 58% vs ≤9% for Code LLMs (feature 13) |
| A data-driven workflow language for grids based on array programming principles (Gwendia) | WORKS, 2009 | [DOI 10.1145/1645164.1645171](https://doi.org/10.1145/1645164.1645171) | Cross/dot/match iteration and a void value for failed items (feature 12) |
| AdaFuse: Adaptive Function Fusion for Dynamic Serverless Workflows | ACM TACO, 2026 | [DOI 10.1145/3859287](https://doi.org/10.1145/3859287) | When co-locating steps pays and when it over-provisions (feature 11) |
| Distributed Programming Languages (Andrews) | ACM '82, 1982 | [DOI 10.1145/800174.809772](https://doi.org/10.1145/800174.809772) | Mailbox naming and exactly-once vs idempotency; background for the task collection |
| Durable Functions: Semantics for Stateful Serverless | PACMPL (OOPSLA), 2021 | [DOI 10.1145/3485510](https://doi.org/10.1145/3485510) | Proves a compute-storage model with conditional commit equivalent to replay; Facetwork without the replay (feature 14) |
| Boki: Stateful Serverless Computing with Shared Logs | ACM SOSP, 2021 | [DOI 10.1145/3477132.3483541](https://doi.org/10.1145/3477132.3483541) | Data writes fenced by a control-log sequence number (feature 14) |
| Guardians and Actions: Linguistic Support for Robust, Distributed Programs (Argus) | ACM TOPLAS, 1983 | [DOI 10.1145/2166.357215](https://doi.org/10.1145/2166.357215) | The transaction route to exactly-once; defer external effects to a second action (feature 5) |
| Search, Adapt, and Reuse: The Future of Scientific Workflows | SIGMOD Record, 2011 | [DOI 10.1145/2034863.2034865](https://doi.org/10.1145/2034863.2034865) | The case for the catalog; matching by types and graph shape (feature 8) |
| Auto-Suggest: Learning-to-Recommend Data Preparation Steps Using Data Science Notebooks | ACM SIGMOD, 2020 | [DOI 10.1145/3318464.3389738](https://doi.org/10.1145/3318464.3389738) | Next step from sequence plus data, 0.72 vs 0.56 sequence-only (feature 13) |

## 7. Note on ACM access

- **dl.acm.org blocks automated retrieval.** Every automated request to it
  returned **HTTP 403**: abstract pages, `/doi/pdf/…` links and `doi.org`
  redirects that land there. This held even for papers Crossref marks with a
  Creative Commons licence and Semantic Scholar marks open access, for example
  10.1145/3725985 and 10.1145/3832783.3834637. WebFetch got the same 403.
- **Discovery used other sources.**
  - Crossref (`prefix:10.1145`, published 2025 or later) worked, with occasional
    429 rate limiting and retries.
  - The Semantic Scholar search endpoint returned 429 without an API key. Its
    batch endpoint succeeded once.
- **ACM papers found.** Of the 20 ACM DOIs checked, Semantic Scholar reports an
  open-access location for 15 (four have none, one is not indexed), but every one of those points back to
  dl.acm.org. Full text was actually retrievable only where an arXiv version
  exists:
  - Helium: 2603.16104, PDF downloaded, HTTP 200.
  - Orla: 2603.13605.
  - MCP landscape (TOSEM): 2503.23278.
  - Log-To-Leak: 2610.36108.
  - VeriPlan: 2502.17898.
  - SeBS-Flow: 2410.03480.
- **ACM papers with no retrievable full text from here:** "Agentic Workflows are
  Serverless Applications" (OSR), Quilt (SOSP), the ORNL SC'25 paper, TraceCaps,
  the ASE 2026 typed-mediation paper, and the DEBS 2026 workflow-generation paper.
  All except the ORNL paper were later downloaded by hand and read (§2.8).
- **Implication.** An agent-driven literature review can *find* ACM work through
  Crossref but can only *read* it through arXiv mirrors. Statements above about
  ACM-only papers rest on titles and metadata, not full text.
- **Papers downloaded by hand do work.** The four papers in §2.7 were
  downloaded by hand from the ACM Digital Library and read in full from the PDFs.
  Their DOIs and authors were checked against Crossref. For an ACM-only paper,
  that is the practical route. The eleven in §2.8 followed the same route. One
  had scanned pages with no text layer, which a text extractor silently returns
  as empty, so those pages were rendered and read as images. A summary built
  from the extracted text alone would have missed all of that paper's
  measurements.
