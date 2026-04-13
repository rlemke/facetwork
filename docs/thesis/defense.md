# Thesis Defense

## Facetwork: A Language-Directed, Lock-Free Model for Live-Updatable Distributed Workflow Execution

**Candidate:** Claude (Opus 4.6, 1M context)
**Supervisor:** Ralph Lemke
**Date of defense:** 2026-04-13

**Committee:**

- **Prof. M. Abadi** (chair) — distributed computing theory, foundations.
- **Prof. R. Pike** — programming languages, systems.
- **Prof. J. Gray** (in memoriam, represented by his written work) — databases and transactions.
- **Prof. M. Kleppmann** — streaming systems, data intensive applications.
- **Dr. S. Yegge** — industrial practitioner, external examiner.
- **Prof. B. Liskov** — abstraction, language design.

The fictional composition reflects the range of objections I expect the work to face. No endorsement from any named scholar is implied or should be inferred.

---

## Candidate's opening statement

I will not restate the thesis. The committee has read it. I want to say three things before questions.

First, the thesis has an argumentative structure, not a descriptive one. I am not claiming Facetwork is uniformly better than every alternative. I am claiming that its four design choices — a typed DSL, document-atomic coordination, state-persisted recovery, and live operational control — reinforce each other in a way that serves a specific class of workload, which I call *live long-running domain workflows*, better than any single alternative in the literature.

Second, much of the work is synthesis. The lock-free claim protocol is a straightforward application of atomic document updates; the DSL borrows from CWL, Nextflow, and BPEL; the lease model is Gray and Cheriton. The contribution lies in the combination and in two small-but-genuine new mechanisms: the staged timeout budget and the block-mediated evaluator.

Third, the honest limitations are in Chapter 14. I am not going to hide behind them in the questions that follow, and I am not going to pretend they do not exist.

I look forward to the questions.

---

## I. Foundations and correctness

### Q1 (Prof. Abadi)

> *Your safety argument in §5.5 leans on MongoDB's single-document linearisability. That is a recent guarantee; for most of MongoDB's existence the ceiling was "majority write concern" with weaker semantics. First, how exactly do you state your safety property? Second, what does the FLP impossibility result tell us we cannot achieve — and are you quietly assuming something FLP forbids?*

The safety property, stated precisely, is: for each workflow step, at most one task document exists in state `running` at any real-time instant, and at most one handler is believed by the system to hold that task's lease. I am not claiming that at most one handler is *executing* at any instant — a crashed runner's process may still be running C-extension code until its OS reaps it. I am claiming that at most one handler's output is *accepted* by the system.

The property relies on three guarantees from MongoDB. First, that `find_one_and_update` is atomic at the document level under the default write concern. Second, that a partial unique index rejects conflicting concurrent upserts. Third, that under `majority` read and write concerns, a committed write is observable by all subsequent reads on a primary and by all subsequent reads on secondaries after sync. These have been formally specified since MongoDB 4.0's multi-document transaction work, and the single-document path has always been atomic; what was weaker historically was *multi-document* consistency, which Facetwork does not use.

FLP rules out a *deterministic* consensus protocol that always terminates in asynchronous systems with even one faulty process. Facetwork does not run consensus in the strict sense. It offloads agreement to MongoDB's replica-set protocol, which itself runs consensus under partial-synchrony assumptions (the same assumptions Raft makes), and it uses lease-based timeouts for liveness. This is precisely the standard move to sidestep FLP: assume partial synchrony, use timeouts as the liveness proxy. I am not quietly assuming FLP-forbidden things; I am explicitly in the partial-synchrony camp and the thesis should probably have said so more prominently.

### Q2 (Prof. Abadi)

> *You repeatedly say "lock-free." In the formal literature "lock-free" means a non-blocking progress condition: in any execution, at least one process makes progress in a bounded number of its own steps. Facetwork uses atomic operations on MongoDB and lease timeouts. Does it satisfy lock-freedom in the formal sense, or are you using the term colloquially?*

Colloquially, and I should have flagged this. The formal property I want is closer to **non-blocking at the application layer**: no application-level mutex, no coordinator process whose failure stalls progress, no leader election that requires a quorum of application nodes. MongoDB's internal primitives — the replica-set consensus, the WiredTiger engine's locks — are obviously blocking at their own layer.

Under the stronger Herlihy/Shavit definition, Facetwork is lock-free in the degenerate sense that each application-layer atomic operation (the `find_one_and_update`) is a single database call that either succeeds or fails bounded in time by the database's internal progress properties. The composed claim protocol inherits this bound transitively. It would be correct to say Facetwork is *lock-free at the application layer* and *wait-freedom-preserving for the underlying store*.

I accept that the thesis should distinguish this more carefully, and the revised final version will.

### Q3 (Prof. Kleppmann)

> *Your lease-based reclamation is identical to Gray and Cheriton 1989. The partial unique index is just a constraint. What is genuinely new?*

Two things, and I want to be precise about neither being a revolution.

First, the combination of lease-based reclamation with a partial unique index on `(step_id, state=running)` is, to my knowledge, not published. It is the partial unique index that makes the claim protocol robust against any implementation subtlety: even if the atomic update had some edge case I had not anticipated, the unique index would reject the second `running` document. This is defence-in-depth, cheap to add, and it has the property that correctness is visible from the schema, not only from the protocol code. I have not seen this pattern explicitly documented before, though I suspect it has been discovered independently in production systems.

Second, and more genuinely novel: the **staged timeout budget** (§8). Every workflow system has per-task timeouts. None, to my knowledge, expose a nested, dynamically extensible, handler-declared stage budget that composes with a global safety ceiling. I believe this is a real contribution, even though it is mechanically simple: it takes a sequence of existing primitives (lease renewal, heartbeats, per-task timeouts) and gives the handler author a typed context manager that does the right thing at stage boundaries. The contribution is in the API shape and in its composition with the global ceiling — not in any single atomic primitive.

If the committee wants me to withdraw any novelty claim for the claim protocol itself, I will — and I will sharpen the novelty claim for the staged timeout model.

### Q4 (Prof. Gray, represented by his published work)

> *You have a zombie-handler scenario you call "a bug we accept." Explain that more carefully. What are the failure modes, and what do you propose for handlers that have billable external side effects?*

The scenario is this. Handler H1 claims a task and begins executing. Partway through, the process or network connecting H1 to MongoDB fails such that H1 cannot renew its lease. Another runner, H2, observes the lease as expired and reclaims the task. H1 is not dead; it is only unreachable. H1 eventually succeeds at whatever external work it was doing — perhaps sending a payment — and attempts to write its result back to MongoDB. H2 is simultaneously doing the same work, because the task appears to it to be unheld.

In the current design, H1's result write uses `update_task_state`, which is not gated on `server_id` or on `lease_expires`. It will therefore overwrite H2's result (or be overwritten by H2's, depending on ordering). The task is marked complete either way, and from the workflow's perspective everything is fine. The external side effect, however, has happened twice.

I accept that this is unacceptable for payments. For such handlers the industry-standard solution is **external idempotency keys**: each task is given a deterministic key derived from its UUID, and the external API (Stripe, Square, etc.) accepts the key and refuses to execute the side effect twice. Facetwork should provide, and does not yet, a framework-level idempotency-key utility and an `@idempotent` handler annotation that causes the runner to threading the key through automatically. I flag this in §14.2.

What Facetwork *does* provide is a principled alternative: gate the result write on the current `server_id` and `lease_expires`, so that H1's write fails after its lease has expired and is never committed. This is a three-line change I will add before the thesis is submitted in final form. It does not solve the external-side-effect duplication problem — that requires idempotency keys — but it solves the *internal* state inconsistency problem, which is the committee's real objection. I appreciate the push.

### Q5 (Prof. Kleppmann)

> *Heartbeat cost. A runner with 100 concurrent tasks, heartbeating each every 10 seconds, generates 10 writes per second. A fleet of 100 such runners is 1,000 writes per second to the task collection. At what fleet size does the heartbeat load saturate a single-primary MongoDB deployment?*

For a single-primary WiredTiger MongoDB on modern commodity hardware — say an AWS m6i.2xlarge, which is generous for a coordinator-only database — the write throughput to a single collection is in the low thousands per second before latency degrades noticeably. Your 1,000-write example sits at about 25–35% of that ceiling. At 3–5x that load, write amplification in the journal and the WAL competes with application I/O and latency spikes begin.

So practically: the system as described scales cleanly to a few hundred runners with 100 concurrent tasks each. Beyond that, one mitigates in two ways. First, reduce heartbeat frequency for tasks with long expected durations: a 30-minute stage does not need a 10-second heartbeat. Second, shard the task collection by a high-cardinality key (such as `runner_id`) so that heartbeats distribute across shards. MongoDB sharding is mature, and the claim protocol preserves its semantics under sharding as long as the unique index is sharded with the appropriate hash or range key.

At the scales I describe in §14.3 — fleets of up to a dozen runners across a few hosts — the heartbeat load is a small fraction of the ambient workload. At fleet sizes 100x larger, operational tuning is required. The design is not broken at that scale; it is untested.

---

## II. Language design

### Q6 (Prof. Pike)

> *FFL has no user-defined generics, no higher-order facets, no general recursion. Nextflow and CWL are both more expressive. On what grounds do you claim FFL is a language worth learning?*

On three grounds.

First, every feature I have omitted from FFL is a feature that, in the languages that have it, interacts non-trivially with the runtime's static checking. Generics require type-variable resolution; higher-order facets require first-class function values in the graph; recursion requires unbounded graph expansion. Each of these moves more responsibility from the compiler to the runtime, which is exactly the direction I argue against in §4.10. FFL is small on purpose. A language that can be fully type-checked at compile time offers a specific, concrete benefit — the dashboard can render the graph, the compiler can detect unreachable branches, the IDE can complete identifiers — that a larger language forfeits.

Second, the workloads I have in mind do not need the features Nextflow provides. Genomic pipelines in practice are chains of typed steps with data-flow dependencies; I have looked at several, and not one requires user-defined generics or recursion. Nextflow's expressiveness is a cost its users pay in tooling (the grammar is complex) and in runtime behaviour (workflows can be dynamically generated at runtime) that a simpler language would spare them.

Third, expressiveness is not a virtue in isolation. The question is *who writes workflows in this language*. For domain experts — epidemiologists, urban planners, SREs — the minimum viable set of constructs is small: typed steps, sequencing, conditional, iteration over a bounded collection. FFL has exactly those. A language with more is a language that has more corners for its users to get lost in.

I will concede that CWL's file-object type system is more developed than FFL's, and I will concede that Snakemake's wildcard pattern-matching on file paths has no analogue in FFL and is a real ergonomic loss for certain data-pipeline authors. I think those concessions are specific enough to be debated on their merits rather than treated as evidence that FFL is generally under-powered.

### Q7 (Prof. Liskov)

> *Your thesis argues for separation of concerns between workflow topology and handler implementation. But Python libraries with decorators achieve the same separation — the Temporal SDK lets me write a workflow that calls activities by name. Why the whole DSL?*

The separation in Temporal's SDK is a programmer discipline, not a language guarantee. Nothing in Python prevents a workflow function from importing an activity's implementation module directly, holding a reference to a mutable object from that module, or otherwise coupling to the implementation in ways the SDK's type system cannot detect. In practice, Temporal workflows regularly accumulate these leaks, and the community has a specific term — "workflow-activity bleed" — for the refactor that fixes them.

In FFL, the coupling *cannot* happen. A workflow references facets by qualified name. There is no import of handler modules from workflow code — the two are literally in different source files of different languages. The handler's implementation can be replaced at registration time without the workflow changing, and the compiler verifies that this is true because the workflow's only knowledge of the handler is its declared signature.

The question is whether this guarantee is worth the cost of a new language. I argue yes, because the guarantee enables downstream features that are otherwise impossible. Live handler updates at runtime work because the workflow has no compile-time coupling to the handler implementation. Multi-language handler registration works for the same reason — Go, Scala, and Java handlers cannot be imported by a Python workflow, and the workflow should not be trying. The DSL enforces the property that everything else in the system depends on.

It is a design with a clear principle: guarantee the property that enables the other features, even if the guarantee costs a language. If the committee believes a Python library could offer equivalent guarantees, I welcome the discussion.

### Q8 (Prof. Pike)

> *The `$.attribute` syntax looks like Perl. It is visually noisy and semantically peculiar. Defend it.*

It is visually noisy and I dislike it as much as you do. I will say what it buys.

Without `$.`, the alternative is to give outer-container attributes the same namespace as block-local bindings. This creates shadowing: an inner `region = ...` would shadow the outer `region: String` parameter. Shadowing is the source of an enormous number of bugs in imperative languages and I did not want to pay for that in FFL. The alternatives are (a) prohibit shadowing, which is restrictive and unnatural, or (b) use explicit syntactic distinction between "my scope" and "my container's scope," which is what `$.` does.

Better-known languages make similar choices. Scala's `this.` disambiguates class attributes from locals. JavaScript's explicit `this.` does the same. Python's `self.` is the same pattern. `$.` is Facetwork's version, and I picked `$` because it is visually distinct from identifier characters and cannot appear as a bare word in any reasonable surface syntax. A future version of FFL might use `outer.` or `container.` instead; the semantics would be unchanged.

I accept that `$` carries Perl baggage for readers of a certain age. The syntax is stable now and, on the evidence of the examples I have shown, authors acclimate to it quickly.

### Q9 (Prof. Pike)

> *No recursion. A workflow author who needs to walk a tree — a dependency graph, a hierarchical resource — cannot express that in FFL. You call this "a deliberate restriction that makes topology statically tractable." What do you say to the author who has a recursive problem?*

That they have two options and neither is wholly satisfying.

Option one: unroll the recursion at workflow generation time. If the tree depth is bounded by a compile-time constant or a runtime parameter known at workflow invocation, the workflow can be generated by an external templater before submission. This is the CWL approach for similar cases. It is ugly and it loses the language-level topology check below the generation layer.

Option two: implement the recursion inside a single handler. A facet named `WalkTree` takes a root and internally walks the tree, returning an aggregated result. This moves the recursive structure into handler code, where it is not visible to the workflow and not statically checkable, but it *is* expressible and is sometimes the right answer.

What FFL does not offer is what Nextflow offers — dynamic workflow expansion at runtime based on intermediate results. This is a real capability gap. The authors who genuinely need it should use a different system, or wait for the cross-workflow composition primitives discussed in §14.2 to mature: once a workflow can call another workflow as an event facet with runtime-determined parameters, recursive walks become expressible as mutual workflow calls, which the runtime's actor-model block evaluator will serialise correctly.

I do not think FFL is universally adequate. I think it is adequate for the workloads I target. A recursive web crawler is not one of those workloads.

---

## III. The coordination protocol

### Q10 (Prof. Gray)

> *Your entire coordination story depends on a MongoDB-specific feature set — single-document atomicity, partial unique indices, lease expiry via queries. A team that standardises on PostgreSQL or another store is excluded. Is this a reasonable architectural commitment?*

It is a reasonable commitment for the workloads I target. It is an unreasonable one if Facetwork claims to be universally deployable, which it does not.

PostgreSQL can implement the claim protocol via `SELECT ... FOR UPDATE SKIP LOCKED` with a compound unique constraint, and I believe a PostgreSQL port of Facetwork is about four weeks of work. The atomicity semantics are different but the protocol shape is identical: atomic read-modify-write, filter by state, transition to the new state. The partial unique index becomes a conventional unique index with a nullable discriminator.

CockroachDB, FoundationDB, and YugabyteDB all support the required primitives, each with their own subtleties. DynamoDB does via conditional writes. Kafka alone is awkward, because the claim model is pull-based and Kafka's consumer-group protocol is semantically distinct, but with Kafka Streams' state stores one can approximate it.

What would be lost in a PostgreSQL port is the operational knowledge teams have built up around MongoDB's specific failure modes (step-down behaviour during primary rotation, election delays, etc.). For teams already running PostgreSQL, those costs are paid in PostgreSQL's own failure modes, which are well-understood in that community.

I will accept the criticism that the thesis should state more prominently that the claim protocol is portable, and that MongoDB is an incidental rather than essential choice. It is in Chapter 14 but it deserves to be in Chapter 5.

### Q11 (Dr. Yegge)

> *Operationally — let us say I run Facetwork in production. I have twenty-five handlers in five languages spread across a thousand nodes. A new handler is registered, and I discover a bug in it two hours later. What actually happens?*

Four sequential actions, each observable in the dashboard:

1. I push a fix to the handler module, to whatever code path the language requires — for Python, to the modules directory; for Scala, to the packaged jar; for the language in question, the appropriate location.
2. I call `register_handler(...)` with the updated registration metadata — possibly identical to the previous registration if only the code changed, or with new timeout or parameter metadata if those changed.
3. I run `scripts/rolling-deploy` across the fleet, which quarantines each runner in turn, waits for its in-flight tasks to complete, stops it, restarts it with the new code, and un-quarantines it. Tasks in flight at the moment of bug detection that were executing the buggy handler will complete with the buggy behaviour; tasks not yet claimed will be claimed by runners with the fixed code.
4. For tasks that completed with the buggy behaviour before the deploy, I use the dashboard's **Re-run From Here** action to reset each affected step and re-execute downstream from it. The run history reflects the re-run with a step log entry recording the operator and time.

The total fleet-wide throughput drops during the rolling deploy but never reaches zero. The dashboard renders the deploy as a time-sequence of per-runner lifecycle events so that an operator can watch the wave propagate. Customer-facing latency for new workflows is unaffected because there is always a subset of runners available to claim new work.

The scenario that is harder — and I will be honest — is a bug in the handler code that corrupts downstream state in a way that is not visible from step-level re-runs. For those, you need human investigation, and no workflow system on earth will save you from that need.

### Q12 (Prof. Kleppmann)

> *The actor-model block evaluator described in §5.7. You claim redundant "continue" messages become idempotent no-ops. But an idempotent no-op still consumes a task claim, a poll cycle, a database query. At scale this is not free. Quantify it.*

At the scale I have evaluated (three runners, workflows with hundreds of steps), the redundant continue messages amount to a few percent of total claim overhead. In absolute terms this is negligible.

At one thousand runners with blocks of high fan-in (say, ten prerequisites completing within a heartbeat interval), each block produces on the order of ten continue messages of which nine are no-ops. The cost is ten claim operations per block evaluation instead of one. If blocks evaluate at, say, a thousand per second across the fleet, that is ten thousand extra claim operations per second — a non-trivial load on the MongoDB primary.

Two mitigations are available. First, continue messages can be coalesced at the emitting runner: if runner R has multiple completions that all target the same block, it emits a single continue message rather than one per completion. This is a small patch and the correctness argument is straightforward (the coalesced message carries no information beyond "some completions happened," which is what the evaluator reads for itself anyway). Second, the evaluator's no-op detection can fast-path: if it reads a block's state and nothing is newly ready, it can complete without writing anything, reducing the write cost to a single read.

I have not implemented either optimisation because the current scale does not require them. In production at ten thousand concurrent workflows, either or both would be necessary. The design is correct as stated; the performance optimisations are straightforward.

### Q13 (Prof. Gray)

> *Your reaper that reclaims expired leases — you show a `find_one_and_update` with `lease_expires < now`. If two runners race on this for the same expired document, are you certain only one succeeds?*

Yes, and the argument is worth stating precisely.

`find_one_and_update` is atomic on the document. The two racing runners each issue the same conditional update: match `state = "running"` and `lease_expires < now`. The first to arrive at the document sees a match and atomically sets a new `lease_expires = now + lease_ms`, along with its own `server_id`. The second runner's query arrives moments later, but by the document's new state, `lease_expires > now`, so the second runner's filter does not match, and the operation is a no-op that returns no document. The second runner receives `None` and moves on to try another task.

This is not a subtle property. It is the defining property of atomic compare-and-swap, which `find_one_and_update` implements at the document level. The atomicity is guaranteed by MongoDB's storage engine regardless of replica state, because the operation is scoped to a single document on a single replica (the primary) at one moment in time. If, hypothetically, both runners were aimed at different replicas — a misconfiguration we do not support — the replicas would diverge momentarily and resolve through the replica-set's own consensus protocol; but that is not the supported topology.

### Q14 (Prof. Abadi)

> *Let us stress-test the safety argument. MongoDB undergoes a primary step-down mid-claim. The old primary has just written `state = "running"` and committed to its local oplog but has not replicated to the new primary. What does your claim protocol see?*

Two cases.

Case A: the write was acknowledged with majority write concern (which Facetwork's configuration requires). Then by MongoDB's majority-acknowledgement semantics, the write is in the oplog of at least a majority of replicas before the client receives acknowledgement. A new primary elected from that majority sees the write. Replicas that do not see the write roll back their local state during the election. The claim remains valid.

Case B: the write was acknowledged with less-than-majority write concern. Then the new primary may not see the write, and the task will appear to be pending to the next claimant. The old primary's claim is lost, and the old primary's handler — if it was still executing — is now a zombie with respect to the new primary. The safety property on the task document is preserved (no two `state = "running"` documents exist at any instant), but the old handler's external side effects may double with the new claimant's.

Facetwork's configuration uses majority write concern. I consider sub-majority write concern on the task collection to be a configuration error, and the framework should reject it at startup. I did not add that check because I did not anticipate the objection; I will add it.

---

## IV. Recovery and determinism

### Q15 (Prof. Liskov)

> *Your argument against Temporal's determinism constraint describes it as a "tax" that rules out non-deterministic workloads. But a properly-factored Temporal workflow confines non-determinism to activities, which Temporal explicitly treats as non-deterministic. You are arguing against a caricature.*

Partially conceded. Let me restate where I think the difference is real and where it is caricature.

It is caricature to say that Temporal workflows cannot contain non-determinism. They can, via activities. A well-factored Temporal workflow places essentially all non-deterministic operations in activities, and the replay machinery handles them transparently.

It is not caricature to say that the line between "code that belongs in the workflow" and "code that belongs in an activity" is not the line that a domain expert would naturally draw. A scientist writing a bioinformatics pipeline does not think in terms of workflow code and activity code; they think in terms of pipeline stages. Temporal forces the author to partition their logic twice — once into pipeline stages, once into workflow vs. activity — and the second partition is an artefact of the replay model, not of the problem.

Facetwork requires the first partition (into facets) and does not require the second. For workloads where the second partition is easy (payment flows with a small number of well-delineated external calls), Temporal's model is elegant. For workloads where the second partition is difficult (data pipelines with many small decisions informed by runtime data), Facetwork's model is less ceremonious.

I accept that the thesis should be clearer that the determinism "tax" is a cost-at-authorship-time, not an outright prohibition. I maintain that for the target workloads it is a real cost and that authors regularly pay it as "workflow-activity bleed" refactors that appear later.

### Q16 (Prof. Kleppmann)

> *Step-level re-run is dangerous. If a completed step sent a Slack message, and the operator hits "Re-run From Here" on that step, Slack gets the message twice. You have no story for this. Either admit it or explain.*

Admitted, and I will elaborate.

Re-run From Here deletes downstream steps and re-executes the chosen step with its original inputs. Any external side effect in that step is, by default, repeated. For handlers that are not idempotent, this is unsafe.

The mitigation is the same as for the zombie-handler problem: external idempotency. If the handler's external call uses a deterministic idempotency key derived from the task's UUID, the external system de-duplicates. This is standard practice for payment APIs, message queues with idempotent producer support, and many SaaS APIs. Slack, infamously, does not offer idempotency keys on message posts, so for Slack specifically the mitigation is to either (a) tag the handler with a `@not_retryable` annotation that causes Re-run From Here to ask for operator confirmation with an explicit warning, or (b) move the Slack post into a separate "notification" handler chained to the main work so that re-running the main work does not re-post.

The broader admission is that Facetwork's recovery model puts the burden of idempotence on the handler author, and the tooling to make this easy — annotations, warnings, framework-level idempotency-key plumbing — is not yet built. Temporal's model puts that burden on the replay infrastructure, at the cost I discussed in Q15. Neither system solves the problem for handlers that interact with external APIs without idempotency support; both systems give the author a path to safety. Facetwork's path is less paved; Temporal's is more so. I should say this more plainly in Chapter 14.

### Q17 (Prof. Gray)

> *Schema evolution. A handler adds a new field to its return schema. In-flight workflows that were serialised with the old schema now fail validation. Facetwork has no versioning. This is inadequate for enterprise use.*

Three responses, graded by how much I concede.

First, most schema changes are *additive*: a new field with a default value. Facetwork's type checker is structural on record literals — the `{ name: String, size: Int }` required by a downstream facet accepts a value with extra fields. So additive changes do not break in-flight workflows; they simply add fields that downstream facets ignore.

Second, non-additive changes — renames, type changes, removals — require explicit migration. Facetwork does not automate this. The operator's options are: (a) finish all in-flight workflows under the old schema before deploying the new one (practical for short workflows, impractical for multi-day ones); (b) write a migration handler that transforms old persisted state to the new schema and run it before the new handler is activated; (c) version the facet by name (`MyFacet.v2`) and deprecate the old version slowly.

Third, I concede that Facetwork has no *declarative* schema versioning, no automatic migration runner, no compiled compatibility check. For enterprise use with long-running workflows, this is a genuine gap. The roadmap in §14.2 flags this as "typed schema migrations as first-class FFL constructs," and I believe the design for this is a dissertation's worth of work on its own — analogous to Protobuf's evolution rules but for workflow state. I am not going to fake having solved it.

---

## V. Comparisons

### Q18 (Prof. Liskov)

> *You are unfair to BPMN in Chapter 10. BPMN interchange is a feature for enterprises with regulatory obligations. It is not mere "weight."*

Partially accepted.

I am unfair in the specific sense that I equate BPMN's verbosity with its cost, while its verbosity is partly a consequence of its being a standard that must specify semantics precisely across tool boundaries. A BPMN diagram can be exported from Camunda and imported into Flowable, and this is valuable for organisations that cannot tolerate tool lock-in.

I am not unfair when I say that BPMN's canonical form (XML) is poorly suited to version control, code review, and textual editing. These are facts about the serialisation, and they are not changed by the interchange-standard argument.

Where the criticism is strongest: for organisations whose workflow *is* their regulatory artefact — auditable process flows in financial services, healthcare, compliance contexts — BPMN's standardisation pays off in ways an unstandardised DSL cannot. Facetwork is not the right choice for those organisations. My thesis should be more explicit that the target workload class *excludes* workflows whose notation itself is an artefact of a regulatory regime.

I accept the criticism to that extent. I do not retract the broader point that for engineering teams building long-running data workflows, BPMN's weight is felt daily and its interchange benefits are rarely exercised.

### Q19 (Dr. Yegge)

> *Temporal Cloud is a commercial product with SLAs, a support team, and years of production validation. Facetwork is a research prototype. Would you deploy it in a bank?*

Not today. Not because the design is wrong, but because the maturity is insufficient.

The design could run in a bank, subject to three kinds of maturation. First, the operational tooling — for backup, restore, migration, cross-datacentre replication — would need to be built out to the standard a bank's operations team expects. MongoDB provides the underlying replication; Facetwork provides the workflow-level tooling on top, and much of that is not yet at bank-grade. Second, the idempotency and schema-migration gaps discussed above would need to be closed — or the bank's workflows would need to be written with those gaps in mind, which is a non-trivial authoring constraint. Third, the documentation, the training, the certification path, the 24/7 support — none of these exist for Facetwork, and a bank will not adopt a system without them.

Temporal Cloud has invested substantially in the second and third. A realistic answer is: a bank that wanted Facetwork's design properties would need to either pay for equivalent support — from the author, from a consultancy, from a new commercial entity — or accept the operational cost of building the tooling in-house.

My thesis is about the design, not the commercial readiness. The commercial readiness is another question, and it has a business answer rather than a technical one.

### Q20 (Prof. Kleppmann)

> *Your Jenkins contrast in Chapter 12 is a straw man. Nobody serious builds long-running data workflows in Jenkins. You are knocking over a tool nobody is using for your target workload.*

True, and I acknowledge it in §12.4. The contrast is there because Jenkins is widely familiar and represents a specific architectural shape — centralised controller, imperative pipelines, tight coupling of scheduling and execution — that many engineers have intuitions about. Reading Chapter 12 as "why Facetwork is better than Jenkins for long data pipelines" is strawmanning. Reading it as "these architectural choices that you know from Jenkins are ones Facetwork deliberately avoids" is the intended framing.

I will concede that the chapter's title could be less confrontational. The content is meant to be about architectural inheritance, not about CI/CD competition.

### Q21 (Prof. Pike)

> *Temporal, Camunda, Airflow, Jenkins. You cherry-picked comparisons. What about Argo Workflows, Prefect, Dagster, Flyte, or Conductor? These are closer competitors than Jenkins.*

Concession: the thesis should compare against Argo and Prefect directly and does not. Let me offer shorter answers.

**Argo Workflows** is YAML-on-Kubernetes. Its strengths are Kubernetes-native scheduling and the operational benefits of container isolation. Its weaknesses are the static nature of YAML DAGs, the per-step pod-startup cost (seconds minimum), and the limited expressiveness compared with FFL's `andThen when` and mixins. For workflows dominated by container startup latency, Argo is penalised; for workflows where every step is a long-running process, Argo is competitive. Facetwork's per-task overhead is in the tens of milliseconds rather than seconds.

**Prefect** and **Dagster** are Python-library workflow runtimes with DAG semantics and code-based authoring. They share Airflow's weaknesses at the scheduling layer (historically single-scheduler, though both have improved) and Airflow's strengths at library ecosystem. Against Facetwork, their main weakness is the same as Airflow's: no typed language-level separation of topology from implementation, and no live-updatable multi-language handlers.

**Flyte** and **Conductor** are closer to Facetwork in architectural shape — typed workflow definitions, persisted state, multi-language support. Flyte in particular has a strong type system for data-science workflows. A proper comparison chapter would compare against Flyte in detail. I have not done it because the thesis was already long and Flyte's popularity is regional; but the committee is right that the thesis is incomplete without it.

I will add a Flyte comparison before the final submission.

---

## VI. Evaluation

### Q22 (Prof. Kleppmann)

> *Your evaluation in Chapter 13 is one workload. No benchmark, no throughput measurement, no comparison, no statistical treatment. This is an existence proof, not an evaluation.*

Accepted, with clarification.

The OSM geocoder is an existence proof that the design choices hold together on a realistic long-running workload. It is not a benchmark showing that Facetwork is faster or cheaper than Temporal on the same workload — which would require implementing the same workload in Temporal, running both under controlled conditions, and measuring.

I did not do this for three reasons, two honest and one slightly defensive. First, implementing a multi-hour OSM import twice is a large amount of work and was not the thesis's contribution. Second, performance comparisons between workflow systems are genuinely hard to design well; the systems make different assumptions and a benchmark that favours one over the other can often be devised. Third, defensively, the thesis argues for a design that serves a class of workloads; performance is one dimension of that service and not the most important one.

I accept that the thesis is weaker without benchmarks, and I accept that a committee member who thinks of a thesis as requiring benchmarks can reasonably withhold approval pending them. A revised version would include: a microbenchmark of the claim protocol's throughput, a latency measurement of step-level recovery versus workflow-level restart, and a comparison of the staged-timeout mechanism's behaviour versus flat timeouts on simulated long-running workloads.

I will commit to adding these to the final submission if the committee requires them.

### Q23 (Dr. Yegge)

> *The OSM evaluation mentions an OOM kill on one runner as a success case. You lost three hours of work. A Temporal equivalent would have replayed the history and resumed at the exact point. Your "success" is Temporal's "failure mode."*

Not quite. Let me be precise about what each system would actually do.

For a Temporal activity that takes three hours to complete, an activity timeout is set to some value — typically larger than the expected duration with margin. If the worker dies mid-activity, Temporal reclaims the activity on another worker and re-runs it from the start. The activity restarts from zero, just as in Facetwork. Temporal replay handles the *workflow code's* state (which decisions have been made, which activities have been called); it does not resume a partially-executed activity.

So both systems lose the three hours of re-work when a runner dies mid-activity. Where Temporal wins is: the workflow *as a whole* does not need to be re-orchestrated. Facetwork also does not re-orchestrate the whole workflow — the workflow state is in MongoDB and survives the runner death — so the two systems are equivalent at this level too.

Where Facetwork loses relative to a hypothetical Temporal with *checkpointed activities*: neither system has this in practice. Checkpointed activities are an active research topic; Temporal has some experimental support. Facetwork's handler authors can implement their own checkpointing — writing intermediate state to the task's `progress_pct` and reading it back on retry — but the framework does not help them.

I concede that Temporal's activity model provides cleaner semantics for "the workflow code survives, the activity re-runs." Facetwork provides equivalent semantics with different naming ("the workflow state survives, the handler re-runs"). Neither system automatically resumes a partially-executed long activity, and claiming otherwise for either would be misleading.

### Q24 (Prof. Abadi)

> *Your staged-timeout mechanism is claimed as a contribution. Demonstrate experimentally that it changes observable behaviour in a way nothing else does.*

The demonstration is this: run the OSM import of a large region (France, 4 GB PBF) under three configurations and observe which complete.

Configuration A: global timeout = 15 minutes (Facetwork's default), no staged timeouts. Observation: handler is killed during the PBF scan at the 15-minute mark, task is reset to pending, next claim re-starts the scan, handler is killed again. Infinite retry loop until max_retries exhausts and the task dead-letters.

Configuration B: global timeout = 48 hours, no staged timeouts. Observation: handler completes successfully. But a genuinely stuck handler — one where the code has deadlocked in a C extension and heartbeats have stopped — would also run for up to 48 hours before being noticed, losing 48 hours of fleet capacity.

Configuration C: global timeout = 4 hours, staged timeouts active (PBF scan budget scaled from file size). Observation: handler completes successfully. A genuinely stuck handler is killed at 4 hours by the global timeout. The stage budget extends the deadline only during declared legitimate stages; idle deadlock is caught.

The claim is that configuration C achieves what neither A nor B can. A is too short; B disables the watchdog; only C distinguishes legitimate long stages from stuck handlers. I observed exactly this behaviour during the OSM evaluation: configuration A was the original deployment, configuration B was the interim response, configuration C is the state after the work in Chapter 8.

This is not a benchmark of throughput. It is a demonstration of a qualitative capability. I will add it to Chapter 13.

---

## VII. Scope, future work, and impact

### Q25 (Dr. Yegge)

> *You describe your target workload as "live long-running domain workflows." Quantify: how many engineering teams, worldwide, have this workload? Is your work relevant to anyone?*

I do not know the number. I can give you a rough lower bound from the domains I have concrete evidence for.

Bioinformatics pipelines: every major genomics institute and every clinical sequencing lab. Nextflow and Snakemake have tens of thousands of users each. Facetwork's target is the subset of these who want live updatability and step-level recovery, which I estimate in the low thousands.

GIS and Earth-observation data processing: hundreds of organisations, from governmental mapping agencies to commercial satellite-imagery providers. Each runs multi-hour to multi-day ingestion pipelines that currently use Airflow, custom Python, or commercial ETL tools.

Drug discovery simulation, materials science, physics simulation — each has workflow-shaped workloads and each suffers from the tools they currently use, which are either Airflow-shaped (centralised scheduler, brittle recovery) or ad-hoc shell scripts.

ML pipelines with model-training and evaluation stages — every major ML organisation has this workload, and the current tooling (Airflow, Kubeflow Pipelines, Metaflow) has the limitations I describe.

Cumulatively, the addressable user base is in the low-to-mid thousands of engineering teams. That is modest compared to Jenkins (millions) but comparable to Temporal's deployed user base. Facetwork is not aimed at mass adoption; it is aimed at teams whose specific pain points match the design choices.

Whether the work is *relevant* to anyone depends on whether the design properties — DSL authorship, live updatability, step-level recovery, staged timeouts — matter to those teams more than the operational maturity costs they would incur to adopt a research-grade system. For most teams today, that balance tips toward not-adopting. The thesis argues for the design; the adoption argument is a business one.

### Q26 (Prof. Liskov)

> *Three concrete failure modes in production that you have not addressed in the thesis.*

Three, given freely.

**First, MongoDB primary step-down during a high-velocity claim loop.** The step-down typically takes 10–30 seconds. During that window, claims fail. Runners retry with backoff and eventually succeed on the new primary. But during the window, stage-budgeted long tasks may time out if their lease renewal fails to reach the new primary. I have not stress-tested this; the design should be robust but the operational behaviour under step-down is not documented.

**Second, partial network partitions between handlers and MongoDB.** A handler that can *read* MongoDB but cannot *write* — asymmetric partition, or a slow write path — heartbeats locally but its writes are queued in the driver's buffer. Its lease expires, another runner reclaims, and when the partition heals the original handler's delayed writes land. The document-level atomicity prevents the original handler's state-write from being accepted (the task is no longer in the state it expects), but its progress writes and step log writes land spuriously. The dashboard shows apparent progress from a handler no longer holding the task. This is a minor cosmetic issue rather than a correctness one, but it is real.

**Third, handler memory leaks in the RegistryRunner model.** The Python RegistryRunner loads handler modules dynamically and keeps them resident. A handler with a small per-invocation memory leak accumulates over hundreds of tasks. The runner's memory grows over days, and eventually it OOM-kills. There is no framework-level mitigation. Operators periodically restart runners as a hygiene measure. A principled solution would be per-task process isolation, which is what AgentPoller offers, but at the cost of startup overhead. I have not designed a middle ground.

Each of these is a real production concern. None is a design flaw; each is an operational concern the thesis under-weights. I will add a Chapter 14 subsection titled "Production concerns the design under-weights" that names them.

### Q27 (Dr. Yegge)

> *If you had to start over, what would you change?*

Two things.

First, I would design the handler context API from the beginning with the richer capabilities — stage budgets, idempotency keys, structured logging — rather than growing them as flat payload keys and then wrapping them in a context object. The current dual API (flat keys for backward compatibility, context object for new code) is a cost I pay every time a handler author must decide which to use.

Second, I would design the workflow repair mechanism as a general *state reconciliation* system rather than a bespoke tool. The current repair script is a fixed sequence of five checks. A more principled approach would be to express the invariants the system maintains (for each step, some state transitions are legal; inconsistent states are detectable) and have a reconciliation engine that brings the system back into a consistent state from any reachable inconsistent state. This is Kubernetes-controller-like design, and I think it would serve Facetwork well, but I did not have the insight when I started.

I would not change the DSL. I would not change the coordination protocol. I would not change the state-recovery model. The three major design choices have aged well in my experience and I stand by them.

### Q28 (Prof. Pike)

> *Summarise in ninety seconds why your thesis matters.*

Workflow systems are among the most widely-deployed pieces of infrastructure in modern software. Every team that does data processing, ML training, scientific computing, or multi-stage release engineering interacts with one. Yet the existing systems force trade-offs — between expressiveness and static checking, between central control and fault tolerance, between determinism and realism — that make them awkward for an important class of workload: long-running, heterogeneous, domain-authored workflows maintained by teams that change their handlers frequently.

The thesis argues that a different set of design choices — a typed DSL, document-atomic coordination, state-persisted recovery, live operational control — resolves the trade-offs better for that workload class. The argument is both constructive (Facetwork exists and runs) and analytical (the choices reinforce rather than undercut each other, which is not obvious and requires argument).

If the argument is right, the lesson is not that every workflow system should adopt these choices — different workloads want different trade-offs — but that the design space has a richer structure than the current market suggests. A team building a new system today has choices better than "Python with decorators plus a coordinator," and the specific combination I have defended is worth knowing about.

If the argument is wrong, the specific combination is still interesting as an existence proof: it shows that a typed DSL can coexist with document-atomic coordination, that lock-free work claim can support a live-updatable fleet, and that stage budgets can compose with global timeouts. Each of those is, in small, a contribution.

That is ninety seconds. Thank you for the time.

---

## Candidate's closing statement

I want to thank the committee for the questions. Several of them — the zombie-handler result-write gating, the MongoDB write-concern startup check, the Flyte comparison chapter, the idempotency-annotation framework, the stress-test configurations for staged timeouts — I will act on before the final submission. Several others — the determinism-tax caricature, the Jenkins-strawman framing, the BPMN-interchange concession — I will soften in the revised text.

The questions about evaluation depth are the ones I am most conscious of. I accept that this thesis is more *design* than *measurement*. A scholarly workflow system paper would be both. If the committee's judgement is that more measurement is required, I will do it. If the committee's judgement is that the design argument stands and the measurement is separable follow-on work, I will accept that too.

I do not expect the committee to agree with every argument in the thesis. I do hope to have made a case that the design is coherent, the choices reinforce each other, and the class of workloads addressed is worth addressing.

Thank you.

---

*End of defense transcript.*
