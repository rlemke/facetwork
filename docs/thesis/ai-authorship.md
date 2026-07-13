# AI Authorship of Facetwork Workflows and Handlers

*Companion addendum to the Facetwork thesis (`thesis.md`).*

This document summarises an extended discussion between the thesis supervisor and the candidate on the implications of AI agents — rather than human programmers — becoming the primary authors of Facetwork workflows and handlers. It reframes certain thesis arguments, resists the naive forms of some popular extensions, and sets out a constructive set of design proposals that the final thesis forward-looking section (§15.3) summarises and refers back to.

The discussion developed as a sequence of successive refinements. Each section below captures one step of the argument.

---

## 1. Determinism, reconsidered

A common pitch for workflow systems in the agentic-AI era is that they are *deterministic*, so that non-deterministic AI agents can run them and produce "consistent behaviour and steps." The pitch is popular because it is superficially correct and rhetorically attractive.

The pitch is a **half-truth delivered as a full truth**. There are at least four distinct senses in which a workflow system can be called deterministic:

1. **Deterministic replay** (Temporal, Cadence). The workflow code reproduces the same decisions given the same event history. This is Facetwork's *opposite*: my thesis argues against imposing this constraint.
2. **Deterministic topology**. The workflow graph is fixed at compile time. Given the same inputs, the same sequence of steps runs with the same typed payloads and the same dependency ordering. Facetwork does have this.
3. **Deterministic orchestration**. Step lifecycle, retry semantics, recovery actions, and observability behave predictably across runs. Facetwork has this.
4. **Deterministic end-to-end behaviour**. Same inputs produce the same outputs across runs. Facetwork does *not* have this when handlers are non-deterministic, and LLM-backed handlers are paradigmatically non-deterministic.

Saying "Facetwork is deterministic" without specifying which sense is misleading. Operators who hear the phrase reasonably infer sense (1) or sense (4); neither holds. The argument worth making is stricter and narrower.

## 2. The correct argument: procedural consistency

The honest version of the argument is **procedural consistency across heterogeneous executors**, not determinism.

If ten different AI agents (backed by different LLMs, different versions, different frameworks) are each asked to "transform this OSM data," they decompose the task differently, invoke different tools in different orders, and produce different intermediate states. If instead those ten agents are each asked to execute a specific FFL workflow, they all follow the same DAG: same steps, same typed inputs and outputs between steps, same dependency ordering, same retry semantics, same observability. Only the leaf computations — what each LLM actually produces inside a given handler — differ.

This buys several concrete properties the bare-agent approach lacks:

- **Auditability of method, not just output.** The FFL source *is* the procedure. Regulated domains can audit the procedure even if the leaf outputs vary.
- **Meaningful benchmarking across LLMs.** Comparing two LLMs at a fixed facet holds everything else equal. Comparing them at "do this task however you want" compares incomparable things.
- **Fix-once-apply-everywhere.** Fixing a flaky step fixes it for every consumer of the workflow. In an agent-decomposes-the-task world, each agent has its own "step 5" that is differently flaky.
- **Interchangeable implementations.** A facet's typed contract is stable; the handler behind it can be an LLM today, a deterministic algorithm tomorrow, a different LLM next year. Consumers are unaffected.

A better phrase for what workflow systems offer agentic systems is **"procedural reproducibility with leaf-level variability."** Honest about what the scaffold buys; honest about what it does not.

## 3. The shift in authorship

A natural observation follows: in practice, the thesis's work on Facetwork — workflows and handlers — has been produced largely by an AI agent, with the human supervisor acting as director and reviewer. This suggests a shift in the authorship model the thesis describes.

The thesis argues that FFL separates *domain programmers* (who write workflows) from *service-provider programmers* (who write handlers). The extended argument is that both roles can be filled by AI agents, with humans serving as:

- **Directors** who specify intent in natural language.
- **Reviewers** who approve generated artifacts.
- **Curators** of the library of approved artifacts.

This is higher-leverage than authorship: one reviewer can approve what would have taken ten authors to write. The review is tractable because FFL was designed to be readable by domain experts, which happens also to make it readable by reviewers of AI-generated workflows, and because the FFL compiler catches the boring class of errors automatically.

This shift is no longer hypothetical in the small. A recent development cycle redesigned FFL's scoping model end to end — grammar, validator, runtime, and a scope-aware migration tool (`fw ffl migrate-scoping`) — then migrated the in-repo workflow corpus and one external domain's FFL onto the new model, flipped it to the default, and carried the change through a live rollout to a three-machine runner fleet. The work was AI-authored throughout, with the human supervisor confined to the director and reviewer roles above: specifying the intent, adjudicating the design (see §5.2), and approving the result. The division of labour §3 describes is the one that actually produced a language-level change to Facetwork itself — not merely a workflow written in it.

## 4. Sealed skills

Once generated, a workflow can be **sealed** — frozen into a reusable, inspectable, versioned artifact. The concept parallels Docker images, WASM modules, and signed smart contracts, adapted to workflows.

A sealed skill is, concretely:

- An FFL source file (the workflow definition).
- Pinned handler registrations (specific versions of specific implementations, addressed by content hash).
- A declared input/output contract.
- Provenance metadata: which model generated it, which prompt, which reviewer approved it, which tests passed at seal time.
- A signature from the reviewer or sealer.
- A stable content-addressable identifier.

Sealing makes the artifact immutable; new versions are new artifacts, not mutations of the old. Any Facetwork runtime that can parse FFL and resolve the named handler registrations can execute a sealed skill. The operational model becomes: **AI generates, human reviews, skill seals, fleet executes** — a clean division of labour that falls out of the FFL/handler separation almost for free.

## 5. Two arguments worth resisting

Two plausible-sounding extensions of the AI-authorship premise deserve explicit pushback, because both get the tradeoffs wrong.

### 5.1 "Humans need not read the workflow language"

The strong form of the argument: since AI reads and writes the workflow language, humans only need a human-readable description of what was generated. The language can be optimised for AI — dense, binary, whatever fits the model — with no concern for human readability.

**The flaw.** The machine-readable form is authoritative; the description is an approximation. Natural language cannot distinguish between all pairs of subtly different workflows. When the description diverges from the artifact — and it will — the executed behaviour is determined by the artifact, not by the description.

Concrete risks:

1. **Debugging collapses.** A 3 AM production incident needs someone who can read the artifact. "Ask the AI" is a reasonable first step; "read the code" must remain a fallback.
2. **Audit fails as a matter of policy.** Regulated domains require that a qualified human can read what was executed. No regulator will accept "the AI told us it does X" as evidence.
3. **Ontological lock-in.** If only AIs can read the language, humans are dependent on whichever AI can read it. That AI becomes irreplaceable infrastructure. If it becomes unavailable, expensive, or compromised, the repository of artifacts is uninspectable.
4. **The cost of readability is near zero.** FFL is small, declarative, and domain-readable by design. The argument "optimise for AI" would have force if human-readability imposed a significant tax. It does not.

**The correct line.** *Most* humans *most* of the time do not need to read FFL; a domain expert's day-to-day interaction can be through the description. What must remain true is that *some* humans in *some* roles — compliance reviewers, senior engineers, incident responders, auditors — retain the ability to read the artifact when they need to. The population shrinks; it does not go to zero. Keep both: FFL stays the source of truth and stays human-readable; the description is a generated convenience layer.

### 5.2 "FFL can be more expressive now that AI writes it"

The plausible version of the argument: FFL was kept simple because non-programmers were meant to author it. With AI authoring, that constraint is gone, so the language can be made more expressive.

**The flaw.** This conflates two senses of "simple." FFL's simplicity is not primarily about author ergonomics; it is about what the *compiler, runtime, and tooling* can do with the program. The restrictions — bounded iteration, no user-defined generics, no general recursion, statically resolvable references — exist because they make the compiler able to type-check every reference, the runtime able to analyse topology statically, the dashboard able to render execution graphs, the recovery actions able to operate at step granularity, and the block evaluator able to guarantee progress without locks.

Those properties are independent of *who* authors the language. They are what give the DSL its value over code-as-workflow in the first place. Relaxing the restrictions because the author changed loses those properties and degrades the DSL into something closer to Temporal's workflow-as-code, which the thesis explicitly argues against.

**The counter-intuitive direction.** AI authorship argues for *more* constraint, not less. The research literature on languages designed for program synthesis (Dafny, CVC5's SyGuS, Rosette, Sketch, Lean tactic languages) consistently makes its target languages more restrictive, because synthesisers work better with smaller search spaces and stronger type information. An LLM generating code is, at a high level, a synthesiser with a language-model prior. Giving it a richer language gives it more ways to go wrong and the compiler fewer ways to catch mistakes.

**What is worth adding.** Specific features — sum types, refinement types, structural row polymorphism, better composition primitives — are worth considering on their own merits because they add expressiveness *without* sacrificing static checkability. What is not worth doing is lifting restrictions (recursion, unbounded dynamic graphs, arbitrary control flow) on the grounds that AI can handle the complexity. The compiler cannot.

**A concrete data point.** A subsequent redesign of FFL's scoping model bears the prediction out directly. The model moved from a *flat* scheme — where `$.name` reached the enclosing workflow or facet's inputs, and a step body could name its containing step by name (e.g. `resolved.regions`) — to a *relative* one: `$` denotes the immediate lexical container and reads that container's parameters *and* returns uniformly (no input/output distinction; an unassigned read yields the default or NONE), while `$$`/`$$$` walk up the enclosing containers and overrunning the outermost is a compile error. Tellingly, the change *added* expressive machinery — up-level references and step-body clause chaining (`s = F() andThen {…} andThen foreach … andThen when …`) — while *simultaneously narrowing* what is legal: a block may now reference only its `$…` container attributes and its same-block steps, no longer its containing step by name (that is reached through `$`) nor a sibling block's step, and `andThen when` lost its former cross-block exemption and obeys the same rule. Expressiveness and constraint moved together, exactly as this section argues they should — the additions buy new well-typed programs; the restrictions let the validator reject a larger class of ill-scoped ones before any handler runs.

The same episode shows the language *nudging authors toward explicit structure* rather than cleverness. To gate a step on several prior ones, the idiom that survives is to pass those steps as facet-typed parameters to a small gating facet: every reference then reads `$.param.field`, the gated steps stay parallel, and the facet's own signature *names its dependencies* — reusable, self-documenting, and valid under either scoping model. And one tempting extension was deliberately *resisted*: a deeper reading of `$$` as a *contract* — a facet's `$$.x` reaching whatever *instantiates* it, checked at each use site — was considered and parked, because every motivating case turned out clearer as an explicit parameter. That is this section's thesis in miniature: the feature that would have added a dynamic, use-site-dependent surface lost to the one that keeps each facet's contract local and statically checkable. The lesson was not that AI authorship licensed more cleverness in the language; it was that the sharper compile-time contract, plus a parameter-passing idiom, served the AI author better than a cleverer scope would have.

## 6. Concrete design proposals

With the false paths ruled out, the constructive direction follows. Given that AI agents are the primary authors and humans are reviewers and operators, the following specific changes serve the resulting system best. None relax the static discipline; most strengthen it.

### 6.1 Strengthen the compile-time contract

- **Effect annotations** on every facet: `@pure`, `@idempotent`, `@network`, `@llm`, `@disk-write`, `@external-side-effect`. AI can reason about them; reviewers read them at a glance; the runtime picks retry and sealing policy from them. An `@external-side-effect` handler not also marked `@idempotent` refuses to be the target of `Re-run From Here` without explicit operator confirmation.
- **Refinement types and sum types.** Return fields typed as `Int where x >= 0 and x <= 100` rather than bare `Int`. Sum types (`Result = Ok(T) | Err(E)`) replace loose `error: dict | None` conventions. AI generates these more precisely than humans do, and the compiler catches more.
- **Pre- and post-conditions on facet signatures.** Lightweight contracts: `requires x > 0`, `ensures result.count >= 0`. A small theorem prover or SMT backend at compile time catches whole classes of AI hallucination before execution.

The relative-scoping redesign (§5.2) is a *shipped* instance of this strengthening, applied to references rather than types. Under the relative model the validator enforces that a block names only its `$…` container attributes and its same-block steps: the containing step is not nameable (it is reached through `$`), cross-block references are rejected, and walking past the outermost container is a compile error. The references a well-formed block may make are now fixed by its lexical position, so the compiler resolves — or rejects — every reference structurally, before any handler runs. It is the same discipline the bullets above pursue through effect and refinement types, carried into the scope rules themselves, and it strengthened the compile-time contract without adding any dynamic surface.

### 6.2 Make generation itself a first-class concern

- **Schema inference from examples.** Instead of the AI hallucinating a schema, the author provides two or three concrete example payloads; the compiler infers the most specific schema that accepts them and rejects generated workflows that then violate it. Closes the "guessed at the shape" failure mode.
- **Two-phase generation.** First pass: the AI generates the FFL and a stub handler whose only job is to typecheck and return a valid example output. The compiler verifies the stub. Second pass: the AI generates the real implementation against the verified contract. Two-phase generation turns out to be substantially more reliable than single-phase.
- **Structured docstring annotations.** `@purpose`, `@inputs`, `@outputs`, `@failure-modes`, `@idempotent` — filled in by the AI, cross-checked against the code by the compiler, rendered in the dashboard for reviewers. Not free text. The AI's explanation of its own output becomes part of the checked artifact.

### 6.3 First-class sealed-skill infrastructure

- **Content-addressed skill registry.** A sealed skill is `FFL source + pinned handler registrations + contract + provenance`, hashed into a stable identifier. Immutable. Versioned. Signed by the reviewer.
- **Skill discovery protocol** (MCP-style, extending the existing MCP server). Agents query: "find a sealed skill whose contract is `(PBF) -> GeoJSON`" and receive existing skills before re-generating. Essential for compounding value; without discovery, every team regenerates the same workflows from scratch. *(Realized — see §7.1: `fw_capabilities` at the facet level, `fw_catalog_match` at the workflow level.)*
- **Regeneration as semantic diff, not replacement.** When a skill is regenerated from the same specification, the system presents a semantic diff — "added a `catch` on step 3," "tightened the return type of `Sum` from `Int` to `Int where x > 0`." Reviewers approve at the semantic level rather than the syntactic one.

### 6.4 Review and testing tooling

- **Auto-generated property-based tests.** The AI generates a workflow; the system generates inputs that exercise each branch, runs them in a sandbox, and reports coverage. Sealing requires the suite to pass.
- **Confidence markers on generated output.** The AI annotates its output with per-section confidence; reviewers focus attention on low-confidence sections. Markers are logged with the sealing metadata so that post-hoc analysis can study correlation between AI confidence and actual correctness.
- **Counterfactual replay.** "What would this workflow have produced if step 5 had used a different LLM?" — re-run downstream from step 5 with alternative handlers. Useful for incident triage and for benchmarking LLMs within a fixed procedural scaffold (§2).

### 6.5 What to remove

- **Retire the `script python` escape hatch.** It exists to let humans reach for a general-purpose language when FFL does not cover something. AI agents should instead generate a new typed facet and its handler; if they cannot, that is a signal to improve FFL, not a reason to keep the escape hatch. Removing it closes a common backdoor for un-checkable logic.
- **Retire any syntax that does not statically check.** Every FFL feature should either be checkable by the compiler or flagged as an explicit dynamic surface. The dynamic surface should shrink over time.

### 6.6 What to preserve

- **The grammar stays small.** Restrictions (bounded iteration, no general recursion) stay. The arguments from §5.2 apply doubly in an AI-generation world.
- **The FFL/handler separation stays.** Generation is a two-phase process whose phases have different properties; conflating them sacrifices the static checkability that makes AI generation tractable.
- **Human-readability stays.** For the reasons in §5.1, the artifact remains inspectable. Review is not a matter of trusting AI self-reports.

## 7. Implemented: the workflow catalog

The "sealed skills" of §4 and the content-addressed skill registry + discovery protocol of §6.3 are no longer only proposals. Facetwork now ships a **workflow catalog** (`facetwork/catalog/`, the `claude_workflows` + `claude_workflow_revisions` collections, and the `fw_catalog_*` MCP tools) that implements the core of that infrastructure — and makes the team-collaboration shape "AI generates, human reviews, skill seals, fleet executes" concrete.

A cataloged workflow is authored, stored, and run **without a file**. Each save is an immutable, content-hashed *revision*: identical content de-dupes, any change is a new version, and old versions stay runnable. A revision is a `draft` until a reviewer **publishes** it, and `run` refuses an unpublished revision unless an operator explicitly opts into an attended test. Running pins a revision, so re-running with different parameters re-uses an identical body. The mapping to §4 is almost one-to-one — content-addressable identity, immutability, provenance (author, version, validation status, note, and an authoring *summary*), and the **generate → review/seal → execute** division of labour — with publish playing the role of the reviewer's signature.

In a team environment this is the difference between an LLM that *suggests* automation and one a team can *depend on*:

- **No silent drift.** A workflow others rely on cannot change underneath them. An LLM editing the logic produces a new version; the version a scheduled job or a teammate pinned keeps executing the body it was reviewed against. Reproducibility is structural, not a convention.
- **Review as the gate, not an afterthought.** Draft → published is a governance checkpoint: AI-authored automation does not run unattended until a human approves it. The cost of that control is one explicit, auditable action.
- **Discover before regenerating.** `fw_catalog_search` lets an agent (or a person) find an existing workflow whose description, tags, and parameter contract fit a request, and reuse it — the compounding-value property §6.3 argues is essential. Without it, every teammate's agent regenerates the same workflow slightly differently.
- **Provenance and audit by construction.** Every revision records who authored it, its version, whether it validated, and a content hash. "What ran, who approved it, and what changed since" is answerable from the catalog — what a regulated or simply careful team needs before trusting AI-authored steps.
- **Intent travels with the artifact.** This is the property most specific to *AI* authorship. When a person is given a description and writes a workflow, the reasoning lives in their head and the review conversation; the generated FFL that survives is precise about *what* runs but silent about *why* it exists or *what was asked for*. The catalog closes that gap: each revision carries an authoring **summary** — the request the workflow was generated from and how it addresses it — recorded by the author at save time (the agent writes it as it writes the FFL) and shown beside the body in the UI. A reviewer approving a draft, or a teammate who rediscovers the workflow months later, reads the intent next to the code, so the artifact is *understandable*, not merely auditable. Because the summary is bound to the immutable revision but excluded from its content hash, it is the recorded rationale for *that* sealed body; a regeneration from a different request becomes a new revision with its own. Understandability is the precondition for the human half of "AI generates, human reviews": you cannot meaningfully approve what you cannot understand, and a content hash alone explains nothing.
- **Shared, pinned libraries.** A `library` entry is a reusable base that workflows depend on *by pinned revision*, so improving a base never breaks a dependent — composition without the fragility that makes shared code scary.

The loop is verified end-to-end: an agent authors FFL, stores it as a draft, a reviewer publishes it, and the runner fleet executes the pinned revision against MongoDB, producing the expected outputs — and re-running with new parameters reuses the identical sealed body. What remains from §6 is additive: semantic-diff review of regenerations (§6.3), auto-generated property tests and confidence markers (§6.4), and *refinement* types (§6.1). The catalog is the substrate those refinements attach to.

### 7.1 Implemented: the composable facet library, capability discovery, and effect annotations

The catalog of §7 remembers *workflows*. The complement — and the part that makes a complex request *expressible* in the first place — is a deliberately-designed library of *primitives* the workflows compose from, plus the discovery and selection surfaces that let an AI author pick the right ones. Both are now built (the `fwh_osm` package + `facetwork/capabilities/`; design in `docs/architecture/composable-facet-library.md`), and they realize, at the facet level, the proposals §6.3 and §6.1 raised.

- **An orthogonal primitive library.** The OSM package is now a layered, path-chaining set of single-purpose facets — source/clip/extract/filter/spatial (9 verbs)/transform/geocoding/routing (across five interchangeable engines)/tiles — that compose by passing GeoJSON paths, rather than a pile of facets that grew for human convenience. A complex request *decomposes* onto them (e.g. "food deserts" = extract supermarkets ∪ extract population ∪ beyond-distance ∪ render), each step a reviewed, reusable primitive.
- **Skill discovery by contract — realized (§6.3).** The capability index (`facetwork/capabilities/`, MCP tool `fw_capabilities`) is the facet-level form of §6.3's "find a skill whose contract is `(PBF) -> GeoJSON`." It indexes every facet's purpose (from its doc comment), typed parameters and returns, and namespace directly from the compiled FFL, so an agent asks "what operates on a GeoJSON path?" or "what reverse-geocodes?" and gets precise primitives before regenerating one. `fw_catalog_match` does the same one level up: it matches a natural-language request against the catalog *by intent* (the authoring summary of §7), returning a `reuse` / `review` / `author_new` verdict — "discover before regenerating" made a first-class decision rather than a hope.
- **Effect annotations — a first form (§6.1, and the effect-typing of the companion notes).** Facets carry effect and cost via the existing mixin grammar — `with Effect(kind = "pure"|"external"|"io")`, `with Cost(tier = "free".."expensive")` — with no new syntax; cost is also inferred from an existing `Timeout`. `fw_capabilities` surfaces and filters on them (`effect=`, `max_cost=`), so an author preferring cheap, side-effect-free steps, or avoiding an expensive full-PBF scan, can select on that basis. All 247 osm event facets are annotated. This is not yet the full vision of `future-thoughts-ai-native.md` §3 — the effects are *discovery* metadata, not lifted into the type signature for a cost-aware scheduler to dispatch on — but it is the property that vision's first step needs, and the place a scheduler would later read.
- **A domain vocabulary.** `osm.Vocab.ResolveTag` resolves a natural-language term to the OSM tag it denotes ("pharmacy" → `amenity=pharmacy`), so the NL → spec refinement is deterministic lookup rather than memorised guesswork.

Together these close the composition loop the AI-authorship argument depends on: **match an existing workflow by intent → else discover the primitives (now effect/cost-aware) and the tags to compose from → seal the result in the catalog → reuse next time.** The compounding-value property §6.3 names as essential is no longer carried only by workflow search; it now reaches down to the primitive library the workflows are built from.

## 8. Summary

The shift from human-authored to AI-authored Facetwork is not a departure from the thesis's design position; it is an evolution that *vindicates* that position. The DSL's smallness and type discipline, defended in the thesis as supporting domain-expert authorship, turn out to support AI authorship for the same underlying reasons: the compiler catches boring mistakes, the runtime catches operational mistakes, and the review burden is bounded by the size of the semantic surface.

The false paths — optimising the language for AI at the cost of human inspectability, or relaxing restrictions because "AI can handle complexity" — are tempting and wrong for specific, identifiable reasons.

The correct direction is a set of additive refinements: richer *checkable* types, effect annotations, lightweight contracts, two-phase generation with typed stubs, structured provenance, a content-addressable skill registry, a discovery protocol, semantic-diff review, property-based testing, confidence markers, counterfactual replay. Removal of the `script python` escape hatch and of any un-checkable surface. Preservation of everything that makes the compiler effective.

The operational shape that results is: **AI generates, human reviews, skill seals, fleet executes** — now realized by the workflow catalog (§7), and extended by the composable primitive library, capability discovery, and effect/cost annotations (§7.1) that make the *generate* step a *compose-from-discoverable-primitives* step. The thesis's four design properties — typed DSL, lock-free coordination, state-persisted recovery, live updatability — are unchanged; a fifth property, *AI-author-ready artifact discipline*, is added. The thesis's forward-looking section (§15.3) flags this direction as the fourth natural evolution of the design, and this document is its extended form.

---

*End of addendum.*
