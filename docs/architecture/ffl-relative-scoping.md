# FFL Relative `$`-Scoping — Design Proposal

**STATUS: DESIGN RATIFIED 2026-07-12 — NOT YET IMPLEMENTED.** The five decisions
in [§5](#5-decisions-ratified-by-the-author-2026-07-12) are settled by the
language author; the semantics are frozen. No code has changed yet. This document
specifies a language-design change to how blocks reference their surroundings in
FFL. The *current* compiler implements a different (flatter) model; every shipping
`.ffl` file depends on the current behavior. Rollout is **flag-gated** (§5.3): do
not "correct" reference docs (`docs/reference/language/grammar.md`, the rule docs)
to this model until the new form is the default and the migration below has run.

Authoritative source: the language author's worked example (see
[§1](#1-the-model-authors-worked-example)). This doc formalizes that example,
maps it against the current compiler (measured, not assumed), and lays out the
grammar / validator / runtime / migration work.

---

## 1. The model (author's worked example)

```ffl
namespace test {
    event facet sf1(input: String) => (output: String, refs: [String])

    facet f1(input: String) => (output: String) andThen {
        s1 = sf1(input = $.input)            // f1 is the CONTAINER of s1; $ = f1
    } andThen {
        // s2 = sf1(input = s1.output)       // ILLEGAL: s1 is in a sibling block
        s3 = sf1(input = $.input) andThen {  // $ = f1 here (s3's arg block)
            s4 = sf1(input = $.input ++ $$.input)
            //   $  = s3  → $.input  = s3's input arg
            //   $$ = f1  → $$.input = f1's (workflow) input
            //   s4 = sf1(input = s3.input ++ f1.input)  // ILLEGAL: name the
            //   container via $, never by its own name
        } andThen foreach r in $.refs {      // $ = s3 → $.refs is s3's RETURN
            r1 = sf1(input = r)              // loop var `r` is bare in the body
        } andThen when {                     // $ = s3
            case $.output == "one" => {       // $.output is s3's RETURN
                w1 = sf1(input = $.output)
            }
            // case s4.output == "one" => …   // ILLEGAL: s4 is in a sibling
            //   clause; the containing step is reached via $, not by name
            case _ => {
                w1 = sf1(input = "default")
            }
        }
    }
}
```

> **Correction (author, 2026-07-12):** the loop var is accessed **`$.r`**, not
> bare `r` — the example above has a typo. See rule 7.

### 1.1 The rules

1. **Every `{ }` block has exactly one *immediate container*** — the step, facet,
   event, or workflow whose body/clause it is.
2. **`$` denotes that immediate container.** `$.attr` reads one of its
   attributes.
3. **A container's attribute surface = all of its attributes, params and returns
   alike — there is no language-level distinction between an input and an
   output.** They are just fields on the container's record. For a step
   `s = F(x = …) => (y)`, both `$.x` and `$.y` resolve inside `s`'s
   body/clauses. **Reading an attribute that has not yet been assigned (e.g. a
   return read before the step `yield`s) is not an error — it evaluates to the
   attribute's declared default, or `NONE`.**
4. **`$$` walks up one container** (the container's container); `$$$` up two;
   `$$$$` up three — arbitrary depth, each extra `$` is one lexical hop.
   Walking up **past the outermost visible container** (the top-level
   facet/workflow) is a **compile error**.
5. **A block may reference only:**
   - (a) `$`, `$$`, … container attributes, and
   - (b) **steps declared in the *same* block**, by name.
   Nothing else. Not a step in any other block, and **not the containing step by
   its own name** — the containing step's name is declared in the *parent* block,
   so it is out of scope here; you reach it via `$`.
6. **`andThen` / `foreach` / `when` clauses chained onto a step** are all
   *co-clauses of that step*: each has `$` = the step, sees the step's attribute
   surface, and its own same-block steps — but **not** each other's steps (they
   are siblings to one another, same as sibling `andThen` blocks). This includes
   **`andThen when`**: it is allowed, but like every block it may reference only
   `$` (the step/container it is attached to) + its own same-block steps — never
   a sibling block's step.
7. **A `foreach r in …` loop var is a fresh binding on the loop body's `$`
   surface**, read as **`$.r`** (same namespace as the container's other
   attributes, matching today's `$.<var>` convention).

### 1.2 Consequences that fall out

- **`andThen when` reads the step's returns via `$`** (`case $.output == …`). It
  is therefore *not* a cross-block reference — it is a gate on the step it is
  attached to. This is the clean form.
- A top-level `when` (chained onto the workflow, not a step) has `$` = the
  workflow and sees only the workflow's attributes + its own block's steps — so
  the current **`andThen { s1 = … } andThen when { case s1.score … }`** idiom
  (where `when` reads a *sibling block's* step) is **illegal** under this model.
- `foreach r in $.refs` iterates the containing step's return collection; the
  loop var is a fresh same-block binding (`r`, used bare).

---

## 2. Current compiler behavior (measured 2026-07-12, `aed3ddf`)

Probes compiled with `./fw ffl compile --primary <file>` (semantic validation on
by default). See `scratchpad/probes/` for the exact files.

| Reference | Author's model | Current compiler | Probe |
|---|---|---|---|
| `$.input` = **step** param | legal | `$.` = enclosing **facet/workflow** inputs only (flat); a step's own param is not on `$` | — |
| `$.output` = **step** return (in `when` case) | legal | **ERROR** `Invalid input reference '$.output': no parameter named 'output'` | `p2` |
| `foreach r in $.refs` = step return | legal | *compiles*, but only because `$.` foreach collections are **un-validated** (the `1c06445` gate skips `is_input` refs to avoid false-positives on pre-script keys) — a false-negative, not real support | `p1` |
| `$$.input` up-level | legal | **PARSE ERROR** `Unexpected character '$'` — `$$` not in grammar | `p3` |
| containing step by name `s3.output` | **illegal** | compiles **clean** — current compiler lets a step body name its containing step | `p4` |
| sibling-block step `s1.output` | illegal | rejected `REF_CROSS_BLOCK_STEP` (except `andThen when`) | — |

**Summary of the delta.** Today: `$.` = the enclosing *workflow/facet's flat
inputs*; a step's params/returns are **not** on `$`; the **containing step is
nameable**; **`$$` does not exist**; and the sibling-block `andThen when` form is
a blessed exception (the "Gap 2" special-case in `validator.py:783`, kept alive
at runtime by the `_StepNotReady` cross-block deferral). The author's model
inverts the first three and removes the fourth.

---

## 3. Implementation sketch

### 3.1 Grammar (`facetwork/grammar` / lark)
- Add the **`$$…` up-level operator**: lex a run of `$` followed by `.ident`
  chains. `$` → hop 0, `$$` → hop 1, `$$$` → hop 2. (Decision: repeated-`$`
  spelling per the example, *not* `$.$.`.)
- `$`-references remain `DOLLAR ("." IDENT)+`; the only lexical change is allowing
  the leading token to be a run of `$`.

### 3.2 Resolver / validator (`facetwork/validator.py`, reference resolution)
- Maintain a **lexical container stack** while walking: pushing a frame for each
  facet/workflow **and each step whose body/clauses we descend into**. Each frame
  carries the container's **attribute surface = params ∪ returns**.
- `$.attr` resolves against `stack[-1]`; `$$.attr` against `stack[-2]`; underflow
  (too many `$`) is a new diagnostic (`REF_DOLLAR_OVERFLOW` or similar).
- **Unify the surface — no input/output split.** A frame exposes *all* the
  container's attributes (params + returns). Resolving `$.attr` succeeds for any
  declared attribute; there is no "return read before assignment" error at
  compile time (it is default/`NONE` at runtime — §3.3). Today only params are on
  `$`; adding returns is what unblocks `$.output`.
- **Loop var on the surface as `$.r`.** A `foreach r in …` injects `r` into the
  loop body's frame; `$.r` resolves, `$$`/outer attributes still resolve past it.
- **`$$…` up-level + overflow diagnostic.** `$` = `stack[-1]`, `$$` = `stack[-2]`,
  … ; a depth that exceeds the number of enclosing containers (walks past the
  top-level facet/workflow) is a new error, e.g. `REF_DOLLAR_OVERFLOW`.
- **Forbid naming the containing step.** A `step.field` reference where `step` is
  the frame directly above must become an error (new rule, e.g.
  `REF_CONTAINING_STEP_BY_NAME`) with the fix "use `$.field`".
- Same-block sibling steps by name stay legal (unchanged).
- **`andThen when` obeys the universal rule (remove the Gap-2 exemption).** The
  construct stays valid; it simply may reference only `$` (the step/container it
  is attached to) + its own same-block steps. A `when` case/body referencing a
  *prior sibling block's* step becomes `REF_CROSS_BLOCK_STEP` like any other
  block. (Legal replacement: attach the `when` to the step and read `$.field` —
  which now works because returns are on `$`.)

### 3.3 Runtime (`facetwork/runtime/`)
- Step-body / `foreach` / `when` clauses already execute **after** their
  containing step completes, so the step's returns are available when `$.<return>`
  is evaluated — no new deferral needed for the clean form.
- **Reading an unassigned attribute → default/`NONE`.** With returns on the `$`
  surface, `$.output` may be read before the step yields (e.g. a `when` that a
  future refactor attaches higher up). Runtime returns the declared default or
  `NONE` rather than raising — no deferral, no error.
- **`_StepNotReady` cross-block deferral becomes dead** once `andThen when` obeys
  the universal rule (it was the last valid FFL that reached it — see
  `docs/reference/rules/REF_CROSS_BLOCK_STEP.md` §"Exception"). Prune it and the
  `get_completed_step_by_name` workflow-wide fallback **only after** the migration
  lands and the test that exercises it (`TestCrossBlockStepRefDeferral`,
  `test_evaluator.py:6278`) is retargeted/removed.

### 3.4 Loop-variable access — DECIDED: `$.r`
The loop var stays on the `$` surface, read as `$.<var>` (today's convention);
the bare-`r` in the §1 example was a typo. No change from current loop-var
resolution — the frame just also carries the container's returns and `$$` chains.

---

## 4. Migration (every shipping `.ffl` depends on current behavior)

This is not a compatibility-preserving change; it is a coordinated rewrite.
Auto-transformable classes (brace-aware transformer, like the `519e5de`
`fix_foreach.py` used for the cross-block foreach migration):

1. **Containing-step-by-name → `$.field`.** Any `step.field` where `step` is the
   directly-enclosing step becomes `$.field`. (This is the big one — e.g. the
   `SubregionChargers` rewrite I shipped uses `resolved.regions` = containing step
   by name; it would become `$.regions`.)
2. **Up-level references → `$$…`.** Where a body currently reaches the
   workflow/outer inputs, rewrite to the correct `$$` depth.
3. **Sibling-block `andThen when` → step-body `when`.** Reattach the `when` to the
   step it gates and rewrite `s1.field` → `$.field`. Affects **14 uses across
   examples/tests + `examples/canonical/05-workflow-when.ffl` + the primary
   `grammar.md` `andThen when` example** — canonical + grammar must be rewritten
   in the same change.
4. **Reference docs** (`grammar.md`, rule docs, `reference_ffl_block_scoping`
   memory) rewritten to the new model — **last**, once behavior matches.

Rollout order: land behind a compiler flag if feasible (parse both, warn on old
form) → migrate all in-repo + `fwh_*` FFL → flip default → remove old path →
prune `_StepNotReady`. A flag-gated dual-parse is strongly preferred over a
flag day given the `fwh_*` repos are separate and baked into the fleet image.

---

## 5. Decisions (ratified by the author 2026-07-12)

1. **Loop-var access — `$.r`.** Keep the `$.<var>` convention; bare `r` in the §1
   example was a typo.
2. **`$` attribute surface — all attributes, no input/output distinction.** Params
   and returns are the same kind of thing (fields on the container's record).
   Reading an unassigned return yields the declared default / `NONE`, not an
   error.
3. **Roll out flag-gated.** Parse both forms behind a compiler flag, warn on the
   old form, migrate all in-repo + `fwh_*` FFL, then flip the default and remove
   the old path. No flag day (the `fwh_*` domains are separate repos baked into
   the fleet image).
4. **`andThen when` stays, but obeys the universal rule** — it may not reference
   anything outside its own block (no sibling-block step reads). This removes the
   Gap-2 exemption and, transitively, makes the `_StepNotReady` deferral dead
   (prunable after migration).
5. **`$$…` is arbitrary depth** (`$`, `$$`, `$$$`, `$$$$`, …). Walking up past the
   outermost visible container is a **compile error** (`REF_DOLLAR_OVERFLOW`). No
   fixed ceiling below that.

---

## 6. What already shipped in this direction (context, not to redo)

- Cross-block **foreach collection** now enforces `REF_CROSS_BLOCK_STEP`
  (`1c06445`); 9 `fwh_osm` workflows rewritten to step-body form (`519e5de`);
  rule doc + 3 tests. This is a *subset* of the model above (forbidding sibling
  refs in foreach headers) and is already live on the fleet (v73).
- The `andThen when` sibling-block exception is currently **documented as
  intended** (`aed3ddf`) — this proposal would *reverse* that. Do not touch the
  rule doc until ratified.

Related: `docs/architecture/server-groups.md`, the
`project-ffl-scoping-redesign-handoff` memory, `REF_CROSS_BLOCK_STEP.md`.
