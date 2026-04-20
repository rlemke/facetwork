# Making LLMs Fluent in FFL

**Status:** design exploration / future TODO.
**Scope:** how to augment a general-purpose LLM (Claude, today) with enough Facetwork Flow Language knowledge to be a productive co-author rather than a well-meaning translator of natural-language intent into broken FFL.
**Not in scope:** immediate implementation. This document describes the phased path, the open questions, and the order in which pieces would be built if/when we decide to do this.

Related design: [`mcp-workflow-surfacing.md`](mcp-workflow-surfacing.md) — how to expose running workflows to LLMs over MCP. That doc assumes the LLM can read and write FFL; this doc is about getting it to that point.

---

## 1. The problem

General-purpose LLMs are trained on huge corpora that include the languages they're "fluent" in: Python, Go, Rust, SQL, bash. FFL is not in that corpus. Empirically, this shows up as:

- **Invented syntax** — the model hallucinates keywords that look plausible but aren't in the grammar (`import`, `def`, `for each ... in ...`).
- **Wrong idioms** — it reaches for `if`/`else` instead of `andThen when { case ... }`, or writes handler-style Python inside an FFL block.
- **Mis-scoped namespaces** — `osm.cache.Africa.Algeria` vs `osm.Africa.cache.Algeria` are indistinguishable to a model that hasn't seen the actual project.
- **Stale references** — the model confidently names facets, schemas, or handlers that don't exist in the current repo.

Every `CLAUDE.md` entry, every hand-curated example, every `/init` session is the user patching around this gap one retrieval at a time. The question is whether there's a more durable way to close it.

---

## 2. Approaches, in roughly increasing cost

### 2.1 In-context learning (today)

A well-maintained `CLAUDE.md`, a few representative `.ffl` files in the conversation, and the grammar in `docs/reference/language/grammar.md` already get surprisingly far. The ceiling is how much relevant material the human (or the tooling) can fit into the context window at the right moment.

This is the baseline. Everything below adds capability on top of it, not instead of it.

### 2.2 Retrieval-Augmented Generation (RAG)

Index the FFL corpus — grammar, reference docs, every `.ffl` file in `handlers/**/ffl/` and `examples/**/ffl/`, representative handler modules — into a vector store. At inference time, retrieve the 5–15 most semantically relevant chunks and inject them into the model's context ahead of the user's question.

- **Cheap.** No training. Works with any model unchanged.
- **Stays fresh.** Re-embed on commit; the LLM's "memory" is whatever's in the repo today.
- **Bounded by retrieval quality.** If the right chunk isn't retrieved, the answer degrades to the in-context baseline.

### 2.3 MCP specialist tools

Extend the existing `facetwork.mcp` server with tools that answer FFL-specific questions the LLM can't answer from memory. The LLM does the reasoning; the tool is authoritative about the DSL.

Examples of what such tools could expose:

- `fw_explain_facet(qualified_name)` — return the facet's signature, namespace, docstring, and every other facet that references it.
- `fw_find_similar_workflows(description)` — return the top-N workflows whose purpose embeddings are closest to the query. A dedicated semantic index over just workflow descriptions.
- `fw_suggest_next_step(partial_ffl)` — given a half-written `andThen` block, suggest legal continuations (facets whose input types match the current bindings, filtered by relevance).
- `fw_type_check(ffl_fragment)` — compile a snippet and return structured errors (file, line, column, reason).
- `fw_list_handlers_for(namespace)` — enumerate which Python handlers are registered for an FFL namespace.
- `fw_schema_shape(schema_name)` — resolve a schema by name and return its fields plus callers that consume it.

`fw_compile`, `fw_validate`, and `fw_execute_workflow` already exist today — the specialist tools are strictly additive.

- **Cheap and targeted.** Each tool is a small piece of Python over existing compiler/runtime APIs.
- **Composable with RAG.** RAG gives the LLM the reference material; MCP lets it ask the compiler/runtime for ground truth.
- **Narrow by design.** A specialist tool only helps if the LLM knows to call it. That's an instruction-tuning / system-prompt concern, not a training concern.

### 2.4 Fine-tuned specialist model

Fine-tune a code-capable open base model (Qwen2.5-Coder, DeepSeek-Coder, CodeLlama) on the FFL corpus + synthetic Q&A pairs, and expose it as one more MCP tool the general LLM delegates to for deeply FFL-specific work (autocompletion, refactoring, idiom suggestions).

- **Actually speaks FFL.** Internalizes patterns — knows that an `event facet` likely gets a handler of a specific shape, that `yield` names the workflow, that `andThen foreach` parallelizes.
- **Expensive.** Compute + operators + ongoing retraining as FFL evolves. Needs a corpus larger than one repo — likely synthetic augmentation (grammar-driven sampling, back-translation from Python handlers, etc.).
- **Worth it only once 2.2 + 2.3 are visibly saturating.** Fine-tuning before that is paying for capability the cheaper tiers haven't run out of.

---

## 3. Recommended phased roadmap

Cheapest first; each phase is useful on its own; later phases stack on top of earlier ones rather than replacing them.

### Phase 1 — RAG over the FFL corpus

**Goal:** the LLM always has the relevant FFL reference material in its context, whether or not the user explicitly paste it in.

What to index:

- `docs/reference/language/grammar.md` (chunked by section)
- `docs/reference/examples.md`, `docs/reference/agent-sdk.md`, `docs/reference/runtime.md`
- Every `**/*.ffl` file, chunked per facet or per namespace
- Handler `*.py` files, chunked per `register_*` function and per event-facet handler
- `osmtypes.ffl` and every `schema` definition, standalone
- Comments on event facets, since those carry intent that isn't in the signature

What to build:

- An embedding job that runs on commit (or on demand) and writes to a local vector store (Chroma, Qdrant, or even SQLite + pgvector).
- A small MCP tool `fw_retrieve(query, k)` that returns the top-k chunks. The LLM calls it as a first step for anything FFL-specific.
- No retraining. No new model.

Effort estimate: days, not weeks.

### Phase 2 — Specialist MCP tools

**Goal:** when the LLM needs authoritative answers ("does this facet exist?", "what does this schema look like?", "will this compile?"), it asks the compiler / runtime, not its memory.

What to add (expanding on §2.3):

1. `fw_explain_facet(name)`
2. `fw_find_similar_workflows(description)` — uses the Phase 1 index.
3. `fw_schema_shape(name)`
4. `fw_type_check(fragment)` — lightweight wrapper over the existing parser/validator.
5. `fw_list_handlers_for(namespace)`
6. `fw_suggest_next_step(partial_ffl)` — initially a simple type-and-name-based suggestor; later could back into Phase 3.

These are all thin shims over existing Python APIs in `facetwork.parser`, `facetwork.runtime`, and the handler registry.

Effort estimate: a week or two per tool, less for the ones that are simple wrappers.

### Phase 3 — Fine-tuned FFL specialist

**Goal:** a model that produces FFL as naturally as Claude produces Python, for the narrow slice of work (bulk refactors, idiom-heavy code, autocomplete) where retrieval isn't enough.

Preconditions (don't start this until all of these hold):

- Phase 1 + Phase 2 are deployed and we can point to specific failures they don't solve.
- There's an actual corpus big enough to fine-tune on — probably requires a synthetic-augmentation pipeline (grammar-driven sampling, paraphrase of existing `.ffl` files into varied namings, back-translation from handler code).
- Someone owns retraining when FFL grammar changes.

What to build:

- LoRA fine-tune on a ~7–14B code-base model.
- Wrap the fine-tuned model as an MCP tool the general LLM delegates to: `fw_specialist_generate(intent, context)`.
- Evaluation harness: a held-out set of FFL tasks we can re-run to detect regressions.

Effort estimate: month+, recurring cost for retraining.

---

## 4. Open questions / tradeoffs to resolve before doing any of this

- **Does RAG alone close the gap?** The honest answer is probably "not 100% but enough to defer Phase 3 indefinitely." We should deploy Phase 1 + 2 and measure before committing to a specialist model.
- **What's the evaluation metric?** "Feels better" isn't good enough for comparing approaches. Candidates: compile-rate of generated FFL, type-check-rate, percentage of generated workflows that actually run end-to-end, human-graded idiom adherence on a held-out test set.
- **Where does the index live?** Per-user local, a repo-committed artifact, a hosted service? Smallest version: a SQLite file in `.facetwork/` that the MCP server reads.
- **What about private / un-open-sourced FFL?** If downstream users write proprietary workflows, they'd want local indexing only — no data leaving the machine. Phase 1 is already compatible with this; Phase 3 would need a "bring your own corpus" path.
- **Model choice for the specialist.** Fine-tuning a base model commits us to an ecosystem. Picking one that's easy to run locally (quantized GGUF, llama.cpp / Ollama) keeps the deployment simple.
- **Interaction with [`mcp-workflow-surfacing.md`](mcp-workflow-surfacing.md).** That doc assumes the LLM can author and reason about FFL. Everything here makes that assumption more likely to hold. The two efforts are complementary: workflow-surfacing is "expose running workflows to LLMs"; this doc is "teach LLMs the language those workflows are written in."

---

## 5. What to do first if we picked this up tomorrow

1. Stand up a local embedding index over `docs/reference/`, every `.ffl`, and every `handlers/**/*.py`. Anything — `sentence-transformers` + Chroma — is fine for a v0.
2. Add `fw_retrieve(query, k)` to `facetwork.mcp`.
3. Teach `CLAUDE.md` (and any MCP-aware system prompt) to call `fw_retrieve` before answering FFL questions.
4. Ship that. Measure compile-rate / type-check-rate of Claude-generated FFL before and after. If the delta is large enough, stop and don't build Phase 2 yet. If it's not, extend to Phase 2 one tool at a time, starting with `fw_explain_facet` and `fw_type_check`.
5. Only consider Phase 3 after Phase 1 + 2 are clearly limited by *model internalization of FFL*, not by *retrieval coverage* or *tool surface*.

The ordering here is deliberate: every step adds a capability that doesn't get thrown away if we stop there.
