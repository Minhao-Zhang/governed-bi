# Module deepening plan

_[English](module-deepening.md) · [简体中文](module-deepening.zh.md)_

Structural refactor plan, opened 2026-07-29. Working doc, not canonical design:
where it disagrees with [Architecture](../architecture.md) or
[Design decisions](../design-decisions.md), those win. Nothing here changes what
the system does. Every item is behaviour-preserving by intent, and the ones that
are not say so.

This is about **where the interfaces sit**. The repo's own vocabulary for that is
already "seam" (D7's RLS seam, the `Embedder` seam, the `Responder` seam), so this
doc keeps it, and adds two terms it needs:

- **Interface**: everything a caller must know to use a module correctly. Not
  just the signature: the invariants, the ordering constraints, which parameters
  are constant for the life of the object and which change per turn.
- **Depth**: how much behaviour a caller gets per unit of interface it has to
  learn. A module is shallow when its interface is nearly as complex as writing
  the thing yourself, which is the test that fails below.

## What was measured

Five packages carry the weight (`src/` is 35,767 lines, `tests/` 32,546):

| Package | Lines | Notes |
|---|---|---|
| `eval` | 14,960 | `run_datalake.py` alone is 5,371 |
| `analyst` | 5,699 | `agent.py` 1,381, `run_log.py` 1,065 |
| `curator` | 4,328 | `pipeline.py` 1,340, `asset_bag.py` 1,197 |
| `gateway` | 1,901 | `guardrails.py` 930 |
| `retrieval` | 1,528 | |

The package import graph is layered and has exactly one cycle (`eval` ↔
`curator`, W6). Fan-in concentrates where it should: `corpus.schemas` 28,
`corpus` 23, `config` 21, `gateway` 16. Fan-out is where the problems are:
`run_datalake` imports 26 internal modules, `run_experiment` 21, `analyst.agent`
20, `curator.pipeline` 17. Those four are also the four largest files, and that
correlation is not incidental: the modules that know about
everything are the ones nobody could put an interface on.

Reach-past-the-interface counts:

| Interface | Evidence of leak |
|---|---|
| `Corpus` | 67 `.assets` references (63 outside the loader) across 22 modules, resolving to 15 distinct queries; ~140 full-list passes per question |
| `build_serve_rails` | 17 keyword parameters, forwarded verbatim by `answer_question_agent`, re-exploded at 5 call sites; 4 of them neither cleanly stack- nor turn-scoped, 2 dead |
| `run_datalake` | 25 underscore-prefixed names imported by 23 test files; 23 `inspect.getsource` assertions repo-wide |
| `Gateway.execute` | accepts `str`; 8 call sites, 2 guardrailed, and the 2 guardrails are not the same gate |
| `Settings` | 33 own fields over 6 concerns, read by 21 modules; 3 clusters nested, 3 not |

## W1: deepen `Corpus`

**Finding.** [`Corpus`](../../src/governed_bi/corpus/loader.py) is a `list[Asset]`
plus `by_id`, `tables()`, `for_analyst()`. Apply the deletion test: replace it with
a bare list and almost nothing is lost, because 22 modules outside the loader
already work that way. The one member that earns its keep is `for_analyst()`,
which enforces the loader contract in one place, and that is the rule callers
re-derive by hand anyway.

The audit grouped all 63 external reach-in sites by the *query* being performed,
and 15 distinct queries cover them. The three biggest: "all `TableAsset`s" (11
sites, and `tables()` already exists, but they just do not use it), "table assets in
schema X" (9 sites, two of them byte-identical comprehensions), and typed
by-asset-type filters (~20 sites of inline `isinstance`).

**The triplicated lookup is not the defect it appears to be, and it hides a worse
one.** `analyst/tools.py:38`, `analyst/middleware.py:118` and the inline copy at
`analyst/agent.py:465` do disagree about exclusion filtering, but that divergence
is **unreachable**: every serve entry hands `build_serve_rails` a `for_analyst()`
view (`api/stack.py:242`, `run_datalake.py:4511`), which deletes excluded assets
outright. Cosmetic triplication, not a defect.

What all three share is the defect:

> **Ambiguous bare physical names resolve to whichever schema loaded first.**
>
> Measured on the real pooled corpus (`BIRD-corpus` at HEAD: 69 schemas, 731 table
> assets, 6,877 columns): **27 bare physical names are ambiguous, covering 67 of
> 731 table assets, or 9.2%.** `pais` appears in 5 schemas, `kunden` in 4,
> `clients` / `produits` / `client` / `usuarios` in 3 each.
>
> The reachable failure needs no adversary. A question routes to `sales`. The agent
> calls `inspect_schema("tbl_sales_kunden")`, the id that
> `search_corpus` prints, and `render_columns` replies `physical: sales.kunden`
> (`tools.py:75`). The agent then calls `sample_rows(table_id="kunden")`, the bare
> name it just read. `middleware.py:125-132` scans in load order and returns
> **`tbl_beer_factory_kunden`**, so the agent is told
> `"tbl_beer_factory_kunden: not licensed this turn — call inspect_schema first"`:
> a table it never named, in a schema outside its routed scope, whose name the
> message leaks. It burns a step against the budget, and if the agent complies,
> `inspect_schema("kunden")` then fails the scope check: a dead loop ending in a
> step-cap refusal that the eval records as an **agent** failure rather than a
> resolver failure.
>
> And the answer is **order-dependent**: it flips with the order of `built` in
> `_load_built_corpus` (`run_datalake.py:850-865`), which is the run-to-run
> nondeterminism that function's own docstring exists to eliminate.
>
> The correct policy is already in the repo, 60 lines from one of the offenders.
> `rvgd.py:530-538` maps a bare name to `None` when more than one table carries it,
> commenting: *"rather than to whichever table happened to be loaded last."* The
> three lookups do the thing that comment forbids.

**A second live gap, not scale-gated.** No lookup anywhere accepts a
schema-qualified name. The seeded context block renders tables as
`### {schema}.{physical_name}` with no asset id at all (`context.py:380-388`), and
`render_columns` prints `physical: {schema}.{physical_name}`. So
`sample_rows("sales.kunden")` misses on both the id and the bare-name paths and
returns `"not available"`, for the exact string the system just showed the model.
Related asymmetry: `middleware.py:163-171` repairs a mis-cased identifier *inside
SQL*, but `sample_rows("KUNDEN")` gets no such tolerance.

**Cost, measured.** Per question on the pooled corpus, before the agent core even
runs: **≈103 full 731-element passes** (≈47 constant, plus 3× the retrieved-table
count and 4× the licensed count). A typical turn adds ~35 more. Call it
**~140 passes, ~100k asset visits per question**, all pure-Python and GIL-bound,
which directly caps what the `workers` knob can buy.

The worst offender is `licensed_physical_names` (`middleware.py:84`), the only
`by_id`-per-id helper on both the once-per-question path (twice, via
`agent.py:196`) and the per-tool-call path (`middleware.py:374`, re-derived on
every `run_query` attempt): **≈40 of the ~140 passes, ~29,000 asset visits per
question through one 8-line function**, recomputing a value that changes only when
`inspect_schema` licenses something. Runners-up worth fixing in the same pass:

- `governance.py:213`: L full scans whose only purpose is a type assertion on ids
  it already holds.
- `tools.py:148` / `:397` / `:420`: `_excluded_identifier_tokens(list(corpus.assets))`
  visits 731 assets **plus all 6,877 columns** = 7,608 elements, uncached, once per
  `render_notes` / `read_notes` / `grep_notes`, for a pure function of the corpus.
- `rvgd.py:597`: calls `corpus.by_id` T times while the local id→asset dict built
  at `rvgd.py:488` is in scope; the comment at `:485-487` explains exactly why
  that dict exists.
- `schema_router.py:78`: `_analyst_tables` runs `for_analyst()`, a pydantic
  `model_copy(deep=True)`, once per pick candidate (3× per question).
  `RetrievalIndexCache.schema_docs` was added to kill this cost on the router path;
  the picker path never got a cache.

**Target interface.** Built once, all pure functions of an immutable asset list.
Absorbed-site counts from the audit:

| Method | Sites absorbed |
|---|---|
| `tables()` (index-backed) | 11 |
| `tables_in(schemas)` | 9 |
| `table_by_id(id)` (typed `by_id`) | 10 |
| `table_by_physical(name, schema=None)`, **ambiguous bare name returns `None`** | 3 |
| `physical_index()` (qualified + bare, ambiguity → `None`) | 4 |
| `joins()` / `metrics()` / `terms()` / `few_shots()` / `notes()` / `negatives()` | ~20 |
| `joins_within(ids)` / `metrics_over(ids)` / `few_shots_in(schemas)` | 13 |
| `column_owner(column_id)` / `columns()` | 4 |
| `counts_by_type()` | 5 |
| `excluded_identifier_tokens()` (cached) | 3 |
| `schemas()` | 2 |

Four queries stay full scans with a caller predicate, and the accessor should
supply only the type filter: negative-example Jaccard matching
(`governance.py:164`), trigger substring-of-question (`triggers.py:43`), arbitrary
regex over note bodies (`tools.py:423`), and the presenter's column reverse-index.

**Risk, now concrete.** `eval/run_datalake.py:864` builds the pooled corpus with
`corpus.assets.extend(...)`, mutating an already-constructed `Corpus`. Any index
built at construction would be stale by the time that corpus is served. So the
deepening must ship with a `Corpus.concat(...)` / `merged_with(assets)`
constructor and that mutation site must move to it. `api/app.py:399` is the second
such site.

**Done when** no module outside `corpus/` scans `.assets` for a query an accessor
serves, the three lookups are one that returns `None` on ambiguity and accepts a
qualified name, `licensed_physical_names` is computed once per licensing change
rather than per attempt, and the two mutation sites go through a constructor.

## W2: `ServeRuntime` / `TurnRequest`

**Finding.** `build_serve_rails` takes **17** keyword-only parameters (not 18, as
this plan first said); `answer_question_agent` takes 18 and forwards all 17. Five
production call sites re-explode them: `api/app.py:508`, `api/graph_app.py:163`,
`eval/arms.py:436`, `eval/oracle.py:342`, `eval/refuse_gate.py:71`.

The implementation behind that interface is deep: roughly 950 lines compiling a
five-node graph, so this is not a shallow module. It is a deep module wearing an
interface about as complex as the thing it hides, and the parameter list mixes two
lifetimes without saying which is which.

**Lifetime audit.** Eleven parameters are cleanly stack-scoped, two cleanly
turn-scoped, and the interesting ones are neither:

| Parameter | Verdict |
|---|---|
| `corpus`, `settings`, `model`, `embedder`, `narrator`, `clarify_checkpointer`, `index_cache` | Stack. Closed over at build; a per-turn value invalidates the derived graph, allowlist and prompt resolution. |
| `on_event`, `clarify_resume` | Turn, hard. `graph_app` gets its writer from `get_stream_writer()` *inside* the node, so a build-time capture would stream turn N into turn 1's writer. |
| `working_memory`, `clarify_thread` | Turn, currently captured at build. Both API paths mint a fresh memory per request, so a stack-scoped graph would serve turn 1's memory forever. |
| **`gateway`** | **Both: the genuine blocker.** Used at build to derive `dialect` (`:386`), closed over per turn by `build_agent_core`. But both API paths open *and close* a connector per request, so the object is turn-scoped there and per-worker in eval. |
| **`identity`** | Stack today, turn-scoped semantically. Constant only because `ServeStack.identity` is one dev identity. Putting it in `ServeRuntime` **bakes single-tenancy into the type**. |
| **`session_id`**, **`n_human`** | Both, inconsistently. The build-time `session_id` becomes `FinalizeCtx.thread_id` and hence the run-log key `f"{thread_id}:{n_human}"`; the per-turn one lands in `base_provenance` and the memory key. Passing a different `session_id` in state changes provenance and memory but **not** the run-log key. `n_human` is converted into a build-time seed (`_turn_n = [n_human - 1]`) and derived thereafter. |
| **`run_id`** | **Dead.** `ingest` overwrites it unconditionally (`:518-525`). No caller-supplied `run_id` ever reaches a logged row through the graph. |
| **`schema_vectors`** | Stack, and **no production caller passes it**. Every graph recomputes `embed_schema_documents` at build. |

The grouping already exists on the wrong side of the seam:
[`ServeStack`](../../src/governed_bi/api/stack.py) holds nearly every stack-scoped
dependency, but it lives in `api/` and `analyst/` correctly refuses to depend on
it, so all five callers unpack it back into keywords and the three eval drivers
hand-roll their own equivalent.

**Target.**

```
ServeRuntime  # corpus, settings, identity*, model, embedder, narrator,
              # clarify_checkpointer, index_cache, dialect
              # + everything now derived at build: default_schema, graph_obj,
              #   allowlist, corpus schemas, resolved prompts, router_chat,
              #   schema_vectors (lazy), routed-corpus memo
TurnRequest   # question, session_id, n_human, gateway*, working_memory,
              # on_event, clarify_thread, clarify_resume
```

Drop `run_id` and `schema_vectors` from the public surface: one is discarded, the
other is passed by nobody. Promote `dialect` to an explicit `ServeRuntime` field so
the turn's gateway no longer has to exist at build time. The serving-schema
reconciliation (`:409-415`) moves to `ServeRuntime.__post_init__`; it is a pure
function of corpus + settings, and `test_governance_ledger.py:113-132` (which pins
that it raises before any model runs) keeps passing.

**Why `gateway` in `TurnRequest` is cheap.** `build_agent_core` (and therefore
`make_tools` and `GovernanceMiddleware`) **already runs per turn**
(`agent.py:965-985`), so it can take the turn's gateway with no rebuild. The only
other users are the three `_finish_unsuccessful` calls, all inside per-turn nodes.

**The single biggest win.** `graph_app.py:156-190` resumes a clarification by
calling `answer_question_agent` again in a `while True` loop: **a full graph
rebuild to change one value** (`clarify_resume`). With the split it becomes a
second `invoke` on the same compiled graph.

**What the split must choose, not dodge.** Making `session_id` / `n_human`
turn-scoped means `FinalizeCtx` is built per turn in `ingest` rather than at
compile time. That is a behaviour change: today `state["session_id"]` cannot
influence the run-log key. It is also what deletes the `_turn_n` counter and
oracle's `f"{session_id}:{n_built}"` workaround.

**A constraint on the shape.** `oracle_solver` legitimately needs a runtime per
narrowed corpus, so `ServeRuntime` must be **cheap to build**. The expensive
derived state (`index_cache`, and ideally a per-document embedding memo) has to be
*injected into* the runtime rather than owned by it, or the split just relocates
the per-corpus rebuild cost from `build_serve_rails` into `ServeRuntime.__init__`.

**Test payoff, measured honestly.** Seven patches target these two builders. Four
(`test_eval_arms_meta.py:36,89`, `test_stage_metrics_seam.py:120`,
`test_prompt_attribution.py:483`, `test_oracle_and_probes.py:352`) become ordinary
injection, **but only if `agent_solver` / `oracle_solver` accept a runtime or a
compiled graph**; decomposing `build_serve_rails` alone does not help them. Two
(`test_prompt_attribution.py:103`, `test_agent_governance_fixes.py:259`) are **not**
fixed by the split: they assert on per-turn prompt *composition* inside
`agent_core_node`, and want a separate extracted `compose_agent_prompt(base,
context_block, now)`. And one is actively **invalidated**:
`test_oracle_and_probes.py:444-490` asserts on *build arguments*: that an
evicted-then-rebuilt graph never reuses a `session_id`, which is exactly what the
split relocates. It guards a real historical collision, so it must be rewritten as
"`turn_id`s are unique across the run", not deleted.

Only **two** `inspect.getsource` assertions target these functions
(`test_retrieval_index_cache.py:327` and `:524`), and both convert if the runtime
*owns* the cache; behavioural equivalents already exist in the same file. The other
twenty source-text assertions belong to W3 and the curator, untouched here.

Also constraining: nine call sites across seven test files use `build_agent_core`
directly as an ordinary behavioural seam. Its signature must keep accepting
`corpus, gateway, identity, model` with `settings` / `dialect` / `default_schema`
explicit, or those churn.

**Done when** the five call sites construct two objects, `graph_app` resumes by
re-invoking rather than rebuilding, and the four convertible patches are injection.

## W3: decompose `eval/run_datalake.py`

**Finding.** 5,371 lines, ~50 module-level functions, one interface:
`run_datalake(**28 keywords)` plus `main(argv)`. Behind it sit staging and
promotion filesystem mechanics, the gold pre-flight, pooled item selection,
manifest resume, arm summarisation (632 lines in one function), ladder deltas, arm
comparison, price verdict, serve-worker factories, and the CLI.

[Open work](../open-work.md) already tracks the size and the 23 `getsource`
assertions. What it does not name is the sharper evidence: **23 test files import
25 distinct underscore-prefixed names from this module**: `_summarise_rows` at 23
import sites, `_compare_arms` at 17, plus `_stage_roots`, `_promote_build`,
`_relocate_sidecars`, `_assert_build_coverage`, `_quarantine_curator_failures`,
`_assert_gold_is_trustworthy`, `_check_resume_manifest`, `ladder_deltas`,
`price_verdict`. A private name imported by 23 files is a public interface with
the wrong label. The `getsource` tests are not a testing-style problem; they are
what is left when the interface you need does not exist.

**The precedent is already in the file.** `run_build_phase` (line 412) was
extracted for exactly this reason, and its docstring makes the argument better
than this plan can: as a closure inside `run_datalake` it "could only be tested by
driving the whole harness — Postgres, gold, serve loop and all. So it never was."
The move is proven. It needs applying four more times.

**The cut is cleaner than expected.** A full census of all 60 module-level
definitions and 13 constants found that **the clusters are already contiguous line
ranges**, with no interleaving anywhere. Seven modules fall out, and the four proposed
here were the right four; three more are available in the same pass.

| Module | Lines | Range | Monkeypatch cost |
|---|---|---|---|
| `eval/summarise.py` | 935 | 1277-1316, 2001-2895 | none |
| `eval/compare.py` | 676 | 1323-1998 | none |
| `eval/build_staging.py` | 544 | 227-770 | 1 site |
| `eval/run_artifacts.py` | 407 | 868-1274 | none |
| `eval/build_corpora.py` | 258 | 2898-3155 | 1 site |
| `eval/preflight.py` | 233 | 793-847, 3158-3335 | 3 sites |
| `eval/serve_plan.py` | 159 | 3338-3496 | 4 sites |

`run_artifacts.py` is the one this plan missed and the strongest of the seven: the
run-directory I/O and resume contract (`_RowSink`, `_read_rows`,
`_stage_event_rows`, `_build_manifest`, `_check_resume_manifest`, `_RESUME_KNOBS`),
one internal edge, zero cross-module dependencies, zero patch cost, and eight test
files that touch it and nothing else in the driver. Start there.

**It is a DAG.** Verified by AST walk over every name load in every function body.
Topological order: `summarise, build_staging, preflight, run_artifacts, serve_plan`
→ `compare, build_corpora` → driver. Nothing in `src/` imports `run_datalake` at
all, so there is no package-level cycle either.

Two edges are worth knowing. `compare → summarise` exists and this plan did not
predict it, but it is a single 10-line function (`_twin_stamps_complete`), it
points the right way, and the docstring at `:1745` ties `_compare_arms`'s stratum
gate to `_summarise_rows`' deliberately. Take the edge. And the one place a cycle
*would* exist (`build_staging ⇄ build_corpora`) **is already broken**, by the
`build_one_db: Callable` parameter on `run_build_phase`. No new injection is needed
anywhere.

**Every ordering invariant survives a pure cut.** Sixteen were catalogued from
comments and docstrings, including the two this plan flagged (gold pre-flight
before the build phase; replicate appended last in `serve_order`) and fourteen
others: leftovers healed before deletion, staging cleared at the start of every
attempt, `Executor.map` consumed inside the `with`, `stage_events.jsonl` cleared
once outside the arm loop, counters counted directly and never by subtraction.
**Not one straddles a module boundary.** They are all either intra-function,
between two functions landing in the same module, or expressed in `run_datalake`'s
own body, which does not move. That is what makes a pure cut safe to attempt.

**Two placement corrections to this plan.**

- `_assert_build_coverage` and `_quarantine_curator_failures` are *gates*, the same
  species as `_assert_gold_is_trustworthy`, not staging mechanics. Put all four in
  one `gates.py`, or leave the two in `build_staging.py`, but do not separate them
  from each other: `test_eval_curator_quarantine.py:26-29` imports their three
  constants in one statement.
- `_routing_escaped`, `_schema_of_assets` and `_fmt_rate` are **not**
  summarisation. None is called by `_summarise_rows`; the first two are per-question
  row annotation called only from `_run_pool_arm`, and 12 of `_fmt_rate`'s 14 uses
  are the driver's stdout block. Moving them to `summarise.py` is harmless but
  mislabels them.

**The migration tactic that makes this cheap.** Keep explicit re-exports in
`run_datalake.py`. All 23 coupled test files then work **unchanged**: plain
imports, `rd.X` attribute access, and `inspect.getsource(rd.X)`, which follows
`__code__.co_filename`. That reduces mandatory test edits to **four monkeypatch
sites**, all of which fail loudly rather than silently, and only two of which are
in the four modules this plan proposed. Migrate the imports afterwards as a
separate behaviour-free commit.

**Six cheap seams, each retiring a `getsource` test.** These are separate from the
module moves and each is 4-35 lines. `build_serve_order(arms, oracles, replicate)` is
the highest value-per-line seam in the file and retires two source assertions at
once. Then `arm_corpus` widened onto `ArmServingPlan`; `collect_pool_curator_errors`;
`LADDER_DELTA_METRICS` as a module constant; `stamp_serve_position`;
`plan_db_builds`. Only the two gold-ordering tests need something invasive: a
`phase_hook` callback on `run_datalake` so a stubbed, model-free run can assert its
own phase sequence.

**`_summarise_rows` decomposes, but as a second commit.** It is 142 lines of
derivation followed by a single 458-line `return {…}` with 87 keys. It splits into
5-6 functions, and the reason it can is that the shared state is *derived, not
accumulated*: every subset is a pure filter of `rows`, with only one self-contained
accumulating loop. The mandatory first step is a `_populations(rows) -> Populations`
NamedTuple naming the eleven shared subsets, because the comments at `:2354` ("Literally
the population above. Recomputing it with its own filter is what let the two drift
apart") and `:2399` are warnings about precisely the mistake a careless split
invites.

Two costs the plan should record before anyone starts. **`summary.json` key order
changes**: the themes are interleaved, not blocked, so a `{**grading, **routing, …}`
merge reorders the committed artifact. Consumers all read by key, so it is probably
safe, but it must be a stated decision, not a side effect. And
**`test_eval_metrics.py:806` goes quietly vacuous**: it regexes `r.get("…")` out of
`getsource(_summarise_rows)` and checks every hit against the declared
`ROW_FIELDS`. A module move preserves it; a decomposition moves the reads out of
that source and the test still passes while checking less. It already has this hole
for the dynamic-key helpers.

Leave `_bucket` alone. It duplicates `_group_by` plus an EX computation, but it
sorts keys as strings, so replacing it changes `by_difficulty`'s key order, a
behaviour change dressed as a cleanup.

**Not the same thing as unifying the two drivers.** Collapsing `run_experiment.py`
into `run_datalake.py` is deferred by decision and stays deferred. Decomposition is
independent and cheaper, and it runs in the useful direction: [open
work](../open-work.md) sequences unification before making the driver drivable, but
extracting these modules first gives the five blocking test files somewhere to
point whenever the collapse happens.

**Recommended order.** `run_artifacts` (warm-up, proves the re-export tactic) →
`summarise` → `compare` (that order, because of the one edge) → `build_staging` (1
patch) → `preflight` (3 patches) → optionally `build_corpora` and `serve_plan` →
then the six seams → then, separately and last, the `_summarise_rows` split.

**Done when** the driver is ~2,100 lines of which `_run_pool_arm`, `run_datalake`
and `main` are 83%: genuinely serve loop, orchestration and CLI, and each
migrated test asserts on behaviour rather than source text.

**Found on the way:** `_FROZEN_GOLD_RE` is defined identically three times
(`run_datalake.py:196`, `analysis.py:50`, `sql_diff.py:195`), and
`leakage.py:87` documents that they must agree. The extraction is a natural moment
to collapse them onto `sql_diff.is_frozen_gold`.

## W4: make the guardrail proof a type

**Finding.** [`Gateway.execute(sql: str, identity)`](../../src/governed_bi/gateway/gateway.py)
accepts any string. Nothing in that interface requires the SQL to have cleared
[`guardrails.check()`](../../src/governed_bi/gateway/guardrails.py), which is a
free function in a sibling module. Eight call sites exist. Two are guardrailed
(`analyst/middleware.py:460`, `analyst/governance.py:720`); six are not
(`curator/deep_agent.py:118`, `curator/sme.py:355`, `eval/ex.py:29`,
`eval/hash_grade.py:351` and `:442`, `eval/run_experiment.py:557`) and are
legitimate: curator probes and gold execution. The interface cannot tell those
apart from a mistake, and a ninth caller gets unvetted execution for free.

The code already reasons about the missing type. `analyst/governance.py:97-114`
argues that a semantic `failed_layer` is "a **proof**, minted by `check()`
itself, that L1/L2/L3 passed". That proof exists in the argument and not in the
program. The docs call this "governance = topology-not-trust", which is honest
about the situation rather than a defence of it.

**What the audit confirmed.** The topology holds where it is claimed to hold.
`Connector.execute` has exactly one caller in the whole repo (`gateway.py:61`), and
the `run_query` / `sample_rows` tool bodies unconditionally raise
(`tools.py:353-365`) if the middleware is not intercepting them. Remove the
governance middleware and the system fails closed rather than opening. Every exit
from `wrap_tool_call` was enumerated and none reaches `execute` without the
`check()` at `middleware.py:376`. No ungoverned tool can execute SQL. A `verdict`
of `cap` or `error` carries no `layer` key, so an attempt that never earned a
verdict cannot reach graded delivery. That is all real, and it is the part of the
design that is working.

**What it also found is in the box below.** The refactor argument stands unchanged;
the finding is separate and larger.

> ### The graded-delivery re-check is not the same gate
>
> `governance.py:696` re-runs `check()` before the second `execute` at `:720`, and
> passes **`allowed_tables=None`**, which skips L4 (term-semantics) entirely.
> `:708` additionally lets an L5 `cost_estimate` re-check *failure* fall through to
> execution. Verified by running `check()` directly on a two-schema pooled
> allowlist:
>
> ```
> licensed this turn = {'sales.orders'}
> "SELECT hr.salaries.base_pay FROM hr.salaries"
>   original check  -> BLOCKED (term_semantics, "table outside the retrieved scope")
>   graded re-check -> PASSED
> "SELECT COUNT(*) FROM pg_catalog.pg_authid"        # not in the corpus at all
>   original check  -> BLOCKED (term_semantics)
>   graded re-check -> PASSED    (no Column nodes, so L3 has nothing to reject)
> ```
>
> The trigger is ordinary rather than crafted: ask something whose answer needs a
> schema this turn was not routed to, and let that be the turn's last `run_query`.
> `extract_final_sql` returns `None`, `agent.py:1142-1149` picks the block entry as
> `last`, `failed_layer == "term_semantics"` makes it deliverable, the re-check
> passes, and `governance.py:737` narrates the **real rows** back behind an
> `(unverified)` prefix.
>
> Scope, stated fairly: `grade_semantic_failures` defaults to `False` and
> [Architecture](../architecture.md) §1 correctly says graded delivery is not the
> serve default. But it is `true` in `governed_bi.local.toml` and on in the eval
> drivers. On a single-schema BIRD corpus the blast radius is small. On the
> 69-schema pooled lake it is a cross-schema read of un-routed, un-licensed data,
> bounded only by the corpus-wide column allowlist, which is the boundary D15 and
> `_in_licensable_scope` exist to enforce.
>
> The comment at `governance.py:677` is honest that L4 is the layer graded delivery
> forgives, so the L4 skip is a designed trade-off. Two things are not covered by
> that design: the surrounding prose reads as if the re-check were equivalent to
> the original, and the L5 fall-through at `:708` forgives a layer the design does
> not claim to forgive. Tracked in [open work](../open-work.md).

**Target interface.**

```
check(...) -> GuardrailVerdict        # unchanged
LicensedSql                           # sql + verdict + THE SCOPE IT WAS MINTED
                                      # UNDER; constructible only by check()
Gateway.execute(licensed: LicensedSql, identity)
Gateway.execute_unchecked(sql, identity, *, exempt_reason: str)
```

The scope on the token is not decoration. If `check()` mints tokens and one of its
two production callers passes `allowed_tables=None`, then a single opaque
`LicensedSql` would make the graded-delivery token and the middleware token
indistinguishable at `Gateway.execute`: the type system would assert equivalent
proof where none exists, and the finding above would get *harder* to see rather
than easier. Carry the `allowed_tables` frozenset, or at minimum an "L4 skipped"
flag, so `execute` can refuse or separately audit a scopeless token.

**The thing to settle first, now answered.** Identifier canonicalisation at
`middleware.py:331` happens *before* the check, and no assignment to `sql`
intervenes between `check()` at `:376` and `execute()` at `:460`. That ordering is
correct. The problem is one level lower: `_force_row_limit`
(`gateway/connectors/base.py:30`) runs *inside* `Connector.execute`, below
`Gateway.execute`, and it is a full sqlglot re-parse and re-serialise, not a string
append:

```
'SELECT "CustomerID" FROM "demo"."customers"'
  -> 'SELECT "CustomerID" FROM "demo"."customers" LIMIT 1001'
```

So a token can only claim "this *tree* passed", never "these *bytes* ran". Worse,
`_force_row_limit` takes a hardcoded dialect (`"sqlite"` / `"postgres"`) while
`check()` receives `gateway.catalog().dialect.value`. On Redshift the checked
grammar is `redshift` and the re-serialising grammar is `postgres`, since
`redshift.py` inherits `PostgresConnector.execute` unchanged. Latent, because
Redshift is untested live, but it is precisely the divergence a token is meant to
make impossible. Fix by hoisting the LIMIT injection above the check, or by having
`check()` return the parsed AST and the connector serialise from that instead of
re-parsing.

**Cost.** Eight production call sites plus the definition. Two get a real token
(`middleware.py:376` and `governance.py:696` are the repo's only production
`check()` callers). Four take `execute_unchecked` with a reason
(`curator_probe`, `sme_probe`, `gold_reexecution`, `harness_smoke`). Two of them,
`eval/hash_grade.py:351` and the prediction half of `eval/ex.py:29`, should
*not* be exempted: threading the token through the solver's return value makes
"the grader only re-runs licensed SQL" structural instead of incidental. It is
incidental today: `extract_final_sql` sources SQL from ledger entries only, so
graded SQL has always passed a check, but nothing enforces that, and
`run_datalake.py:3715` grades against an **unpinned** gateway, so the same string
is re-run under a broader `search_path` than it was checked with. Test-side cost:
19 fake-gateway doubles across 8 files.

**Also worth documenting rather than fixing.** The curator probe path
(`deep_agent.py:118`, `sme.py:355`) hands LLM-authored SQL to `execute` under an
`all_access` identity with no check, defensibly: the curator is the thing that
builds the allowlist, so it has nothing to check against. But the L2 policy
denylist (`pg_read_file`, `query_to_xml`, `dblink`) does not protect it either, and
[Architecture](../architecture.md) §1's "executes only guardrail-passed SQL" carves
out no exception for it. Either run L1/L2 on curator probes (cheap: they need no
allowlist) or state the carve-out.

**Done when** the serve path cannot reach the data plane without a scoped token,
every exempt path names its reason in the audit log, and the checked tree is the
executed tree.

## W5: group `Settings`

**Finding.** [`Settings`](../../src/governed_bi/config.py) has 33 own fields and
nests `ModelConfig` (13), `DataSourceConfig` (7) and `NoteGovernance` (5, a
parameter object rather than a field). The pattern is established, then applied
to three clusters out of six.

A field-by-field usage audit corrected two things this plan first claimed, and
both corrections matter:

- **`serve_config_hash` does not hash the eval worker knobs.** It hashes exactly
  13 things (`provenance.py:84-102`), and the three `eval_*` fields are not among
  them. There is no reflective read anywhere. Changing an eval concurrency knob
  moves no serving digest, and it marks nothing non-comparable: worker counts
  reach the manifest as `serve_workers` / `build_workers`, which sit in
  `MANIFEST_OPERATIONAL` under the heading "Recorded, deliberately NOT gate keys"
  (`eval/metrics.py:181-188`), and `COMPARABILITY_KEYS` derives from
  `MANIFEST_KNOBS` only.
- **`auto_accept_corpus` is the only hashed-but-gating-nothing field, and there
  are no dead fields at all.** Every other hashed field has a live non-recording
  reader. The audit confirms [open work](../open-work.md)'s existing entry and
  adds nothing to it. Worth recording that `auto_accept_corpus` is reachable from
  the *serve* path, not just from eval: `finalize_and_log` calls
  `serve_config_hash` at `analyst/run_log.py:888`, so it cannot simply be
  dropped from the analyst's object without changing a recorded digest.

**What survives, in a sharper form.** `for_env` (`config.py:362-380`) has no
keyword for any of the three `eval_*` fields; only `load_settings` sets them. Both
drivers rebuild `Settings` through `for_env` (`run_datalake.py:4153`,
`run_experiment.py:568`), so during an eval run the `Settings` the serve loop and
middleware actually hold reports `eval_workers=1, eval_serve_workers=None,
eval_build_workers=None` **regardless of the real concurrency**, which arrives
separately as a CLI-resolved function argument (`run_datalake.py:5181-5189`).

That is the same drift `NoteGovernance.from_settings` was invented to fix for the
`[notes]` table (`config.py:176-181`), and it is latent today only because nothing
on the serve path reads the fields. So the misplacement is doubled: wrong object,
and the wrong value on that object during exactly the runs it claims to describe.

**Target.**

| Group | Fields | Droppable from the analyst's object? |
|---|---|---|
| `EvalConcurrency` | `eval_workers`, `eval_serve_workers`, `eval_build_workers` + `serve_worker_count()` / `build_worker_count()` | **Yes**, zero serve-behaviour change: 3 call sites, all at eval CLI entry |
| `RunLogConfig` | the 9 checkpointer / run-log / full-content fields | No: `FinalizeCtx.settings` reaches all nine from the serve path |
| `SchemaRoutingConfig` | `schema_route_top_k`, `schema_route_llm_pick`, `schema_pick_max_columns` | No: all three gate serve routing |

Two constraints the audit surfaced:

- **Nesting `SchemaRoutingConfig` changes every serve digest** unless the hash
  payload keeps the flat key names. `provenance.serve_config_hash` reads those
  three flat today.
- **`RunLogConfig` must be curator-reachable, not serve-only.** `curator/sme.py:507`
  reads `log_full_content` to decide whether an SME answer's verbatim text is
  persisted.

A fourth cluster is available and was not in the original plan: `can_stream`,
`allow_edit`, `serve_api_key_env`, `cors_origins`, `corpus_root` and
`single_all_access_identity` are read **only** inside `api/`, never in `analyst/`,
`retrieval/` or `gateway/`. An `HttpServeConfig` would also be droppable from the
analyst's object, but it is a real refactor of `api/stack.py` rather than a
mechanical move, and `single_all_access_identity` is a security gate worth keeping
visible. Left out of scope for now.

`grade_semantic_failures` belongs to none of the clusters. Leave it flat, or fold
it with `hard_block_suspect_columns` into a future `ServePolicy`. `environment`
cannot be nested: it is the discriminator `for_env` switches on.

**Done when** `EvalConcurrency` is off the analyst's object, `for_env` can express
whatever remains, and the serve path's real `Settings` dependency is readable from
the type.

## W6: `TrainPair`, and the curator's declared vocabulary

**Correction first.** This plan opened by calling `eval` ↔ `curator` the repo's
one package cycle. At runtime it is not a cycle: both curator→eval edges are
`TYPE_CHECKING`-only (`curator/pipeline.py:49-52`, `curator/sme.py:20-22`), and
the eval→curator edge at `eval/harness.py:211` is a function-local import inside
`_sme_fold_signal`. It is a declared-vocabulary cycle, visible to a reader and to
mypy, deferred at import time. Fixing it buys clarity, not correctness. Scope the
work accordingly.

**Finding.** `curator/pipeline.py` and `curator/sme.py` type their training input
as `Sequence[EvalItem]`, a type owned by `eval/dataset.py`. Field-level usage:

| `EvalItem` field | curator | eval |
|---|---|---|
| `question` | Phase A prompt, SME brief | everywhere |
| `sql` | Phase A prompt, `seed_from_train_sql` | grading, leakage checks |
| `evidence` | Phase A prompt **and the SME brief's domain hints** | nothing reads it |
| `question_id` | pair labels in the Phase A prompt (positional fallback) | row keys everywhere |
| `difficulty` | nothing | two stratification reads |
| `answerable_by_template` | nothing | **nothing in `src/`** |

**`answerable_by_template` is dead.** The `TemplateSqlGenerator` it names does not
exist anywhere in `src/`. ADR 0002 records its deletion. `eval/bird_loader.py`
never sets it, so on every real BIRD run it is `False` by default for every item.
Deleting it costs one line in `tests/test_eval.py:148` (inside a
`@requires_live_serve` test), two kwargs in `eval/dataset.py`, and a docstring.

**`TrainPair` needs four fields, not two.** `question`, `sql`, `evidence`,
`question_id`. `evidence` is load-bearing in a way the original plan missed:
`curator/sme.py:252-266` builds the SME brief's domain-hints section from it,
deduped and deliberately **uncapped**, with a comment saying that dropping any
"starves the SME of exactly what it needs to answer". Narrowing `TrainPair` to two
fields would silently gut the `curated_sme` arm.

**One fact worth stating wherever the SME arm's lift is quoted.** BIRD's `evidence`
is human hint text authored *alongside* the gold SQL to make each question
answerable. It reaches the Simulated SME's system brief verbatim and in full
(`curator/sme.py:263-266`) and the curator's Phase A prompt
(`curator/pipeline.py:84-85`). This is train-split only and
`assert_brief_no_leakage` (`curator/sme.py:276-295`) enforces no gold SQL and no
test-question text, so it is **not** test contamination. It is a channel by which
knowledge written to make the gold derivable becomes the SME's domain expertise,
which belongs next to [open work](../open-work.md)'s X7, the item saying the
`curated_sme` delta can never be attributed to the clarification protocol. This
sharpens X7 rather than adding to it: the confounded second mechanism is not only
the description CSVs, it is also the per-question hints.

**Target.** `TrainPair(question, sql, evidence, question_id=None)` owned by the
curator side. Eight conversion sites in the two drivers, three signature changes
(`pipeline.py:837`, `:1066`, `sme.py:74`), two `TYPE_CHECKING` imports deleted, and
roughly 14 test constructions updated.

The other edge is independent and smaller: `_corpora_differ`
(`curator/pipeline.py:761`) is a pure filesystem function: it sha256s
`sorted((root/schema).rglob("*.yaml"))` and compares two digests, touching no
curator state. Move it to a neutral home (`eval/atomic.py` already owns filesystem
primitives) and both callers import from there. Carry the `*.yaml`-only scope
comment with it.

## W7: `Provenance` is a declared interface with one constant writer

**Finding.** `corpus/schemas.py` declares `ProvenanceSource` (`curator` / `gold` /
`human`) and `ProvenanceStatus` (`proposed` / `draft` / `certified`). What the
curator actually writes is one value.

Every non-certified write goes through `AssetBag._audit()` (asset_bag.py:1181) into
`_inference_audit(model=self.model_name)`, which defaults to `source=curator,
status=proposed`. And `AssetBag.from_tables` accepts a `model_name` that **no
production call site passes**: `curator/pipeline.py:234`, `:876` and `:1210` all
omit it, so `Provenance.model` is `None` on every asset of every generated corpus.
Meanwhile `ProvenanceStatus.draft` ("adversary passed it") has **no writer
anywhere in `src/`**; only tests and the hand-authored example corpus under
`corpus/beer_factory/notes/` use it.

So a generated corpus has two provenance states in practice: `curator/proposed`
on everything, and `human/certified` on whatever a Simulated SME answered, a
tier [open work](../open-work.md) already flags as minted by a model. The middle
tier is empty, and the model field that would say who wrote an asset is always
null.

**Four consumers degrade.**

1. `analyst/note_inject.py:33-35` ranks notes by status with `certified=0,
   draft=1, proposed=2`. On a generated corpus the ordering is inert.
2. `retrieval/triggers.py:50` gates PIN on `publication_status == certified`, and
   `pin_require_certified = true` in `governed_bi.toml`. The only producer of a
   certified, triggered note is `AssetBag.record_caveats` (asset_bag.py:1035-1047),
   which folds SME clarification answers. **So the PIN channel is reachable only
   on the `curated_sme` arm.** Run `--pin-triggers` and `baseline` / `seeded` /
   `curated` measure zero PIN events by construction, not by result. That is the
   same shape of failure the `NoteGovernance` docstring (config.py:176-181) was
   written to close, one field further in.
3. `viz/presenter.py` surfaces `provenance_status` on seven view models to the
   audit UI, where a human auditor sees `proposed` on everything and cannot use
   it to triage.
4. The eval ladder cannot query it. `tests/test_curator_seed_joins.py:270` reads
   `inspect.getsource(build_curated_corpus)` to answer "does the mechanical path
   write few-shots?", a question an asset's own provenance ought to answer
   directly. Another `getsource` test that exists because a field carries no
   information.

The naming already knows about the distinction the record loses. `AssetBag` keeps
`propose_join` / `propose_metric` / `propose_term` / `propose_few_shot` as
`*args, **kwargs` forwards to the `upsert_*` methods (asset_bag.py:810-820),
labelled back-compat. But the split is live and meaningful: the deterministic seed
calls `propose_*` (`pipeline.py:108`, `:114`, `:204`) and the agent's tools call
`upsert_*` (`deep_agent.py:135-185`). Two names for two provenance tiers, and the
alias throws the difference away on its first line.

**Target.** Make the writer state which tier it is writing, and let the record
carry it:

- `AssetBag` takes the provenance source at the write, not a back-compat alias:
  deterministic seed writes land as a distinct source (or at minimum a distinct
  status), agent writes as `curator/proposed`.
- Pass `model_name` at the three `from_tables` sites, or delete the parameter. A
  parameter that no caller passes is not a seam.
- Either write `draft` when the adversary passes an asset, or delete the value and
  the two rank maps that read it.

**Not behaviour-preserving, deliberately.** This changes what lands in
the corpus, so it needs a decision before code: which of these tiers the project
wants to be able to distinguish, and whether the `curated_sme`-only reach of PIN is
intended. That question belongs in [design decisions](../design-decisions.md), not
here. Related: [open work](../open-work.md)'s corpus-coverage entry says
`activation`'s `on_match` is never emitted. `record_caveats:1039` does emit it when
`derive_keyword_triggers` returns anything, so that entry is stale for this
producer, and the live question is whether trigger derivation ever returns
non-empty on real SME answers.

## Defects found on the way, fixable without any refactor

These came out of the audits and do not depend on any workstream landing. Ordered
by consequence. The first three affect recorded data.

| # | Defect | Fix |
|---|---|---|
| D1 | **`eval/oracle.py:342` writes answer-key-derived turns into the durable run log**, stamped `producer=serve, serve_path=agent`, with no `oracle_rung` anywhere in provenance, distinguishable from real serve turns only by a `thread_id` prefix convention. The module's own docstring (`oracle.py:55-58`) says these can never be reported as system performance. At `oracle_tables` scale that is one row per question per rung. | `dataclasses.replace(settings, run_log_kind="off")`, mirroring `arms.py:430-434`. One line. |
| D2 | **`eval/refuse_gate.py:71` collapses an entire N-question run into one durable row.** It builds a fresh graph per question and defaults `n_human`, so `turn_id == f"{session_id}:1"` every time, and `append_run_record` UPSERTs by `turn_id`. It is the one serve call site that received neither the `_turn_n` fix (which `test_eval_run_log_turns.py:60` pins for `arms`) nor the AUDIT R6 index-cache fix, so it also re-embeds the whole corpus per question. | Pass `n_human=i+1`, reuse one graph, set `run_log_kind="off"`. |
| D3 | **`events.final` appends the durable record before `narrate` runs** (`agent.py:1265-1268`), so the stored row lacks the `narrate` stage whenever no narrator was passed. The re-append only happens on the narrator-ran path, which is the path the eval drivers never take. | Append after narration, or record the stage unconditionally. |
| D4 | **`schema_vectors` is passed by nothing**, so on a multi-schema corpus every live turn re-embeds every schema document, because the API paths rebuild the graph per turn. `index_cache` cannot cover it: `schema_router.py:224-231` short-circuits on `schema_vectors` *ahead of* the cache branch, so the stack's cache never sees the call. | Have the runtime own it lazily (W2), or thread `stack.schema_vectors` now. |
| D5 | **`oracle_tables` re-embeds a large corpus per question**, and the cause is not the missing `index_cache`: the cache key is the sorted asset-id tuple, and the gold table set differs per question, so every lookup is a guaranteed miss. `restrict_corpus` (`oracle.py:264`) also keeps **every** term, note and negative-example asset whole, and all three have non-blank documents. | Per-document embedding memo. A corpus-keyed cache cannot fix this rung. |
| D6 | **`licensed_physical_names` (`middleware.py:84`) is re-derived on every `run_query` attempt** as well as twice per question: ~29,000 asset visits per question through one 8-line function, for a value that changes only when `inspect_schema` licenses something. | Memoise per licensed-id set. |
| D7 | **`_excluded_identifier_tokens` (`tools.py:148`, `:397`, `:420`) is uncached** and visits 731 assets plus all 6,877 columns per call, once per `render_notes` / `read_notes` / `grep_notes`, for a pure function of the corpus. | Cache on the corpus (W1's `excluded_identifier_tokens()`). |
| D8 | **`api/app.py` omits `clarify_checkpointer`**, so `enable_clarify` is False and `ask_user` is **not bound at all**. The REST `/chat` agent has a different tool set from the streaming path, and nothing in provenance records that clarification was unavailable. | Decide whether REST should clarify; record the capability either way. |
| D9 | **`run_id` is a parameter the code discards** (`ingest` overwrites it unconditionally at `agent.py:518-525`). | Delete it, or stop overwriting. |
| D10 | **`run_datalake.py:4636-4668` compiles a serial solver's graph the pooled path never uses**, paying one full schema-document embed per arm. | Build lazily. |
| D11 | **`_force_row_limit` re-serialises under a hardcoded dialect** (`"sqlite"` / `"postgres"`) while `check()` parses under `gateway.catalog().dialect.value`. On Redshift the checked grammar is `redshift` and the executed grammar is `postgres`, since `redshift.py` inherits `PostgresConnector.execute`. Latent: Redshift is untested live. | Covered by W4's "checked tree is the executed tree". |
| D12 | **`rvgd.py:597` calls `corpus.by_id` T times** while the local id→asset dict built at `rvgd.py:488` is in scope, and the comment at `:485-487` explains precisely why that dict exists. | Use the local dict. |

D1 and D2 are the ones to do first: they are one-line changes, and both corrupt a
record the project relies on for audit rather than merely costing time.

## Sequence

**Before any of it: D1 and D2.** Two one-line changes that stop corrupting a
recorded artifact. They cost nothing and block nothing.

Then, ordered by payoff against risk:

1. **W1 Corpus**: fixes a measured live bug (ambiguous bare names, 9.2% of pooled
   table assets), removes an order-dependent nondeterminism, deletes a triplicated
   lookup, and pays back across 22 modules. Ship the `Corpus.concat` constructor in
   the same change or the index goes stale on the pooled path.
2. **W3 run_datalake**, starting with `run_artifacts.py`: zero patch cost, proves
   the re-export tactic, and every extracted test is pure upside. Independent of
   the deferred driver unification, and independent of W1.
3. **W2 ServeRuntime**: collapses five call sites and turns `graph_app`'s
   rebuild-to-resume loop into a second `invoke`. Decide the `session_id` /
   `n_human` lifetime question first; it is a recorded-key behaviour change.
4. **W4 LicensedSql**: smallest diff, largest governance payoff, and the scoped
   token is what keeps the graded-delivery asymmetry visible instead of burying it.
5. **W5 Settings** and **W6 TrainPair**: mechanical; do them while touching the
   neighbours. Note that nesting the routing knobs moves every serve digest unless
   the hash payload keeps the flat key names.
6. **W7 Provenance**: last in this doc and possibly first in importance. It needs
   a design decision before code, and it is the only item with a live measurement
   consequence.

W1 and W3 do not overlap and can run in either order. W2 touches `analyst/` and the
eval drivers' call sites, so it wants to land after W3's extractions rather than
during them.

## Out of scope

- Unifying the two eval drivers. Deferred by decision; see [open
  work](../open-work.md).
- Deleting tests. The `getsource` tests are load-bearing until the seams they
  substitute for exist.
- Anything touching Redshift.
- New behaviour of any kind. If a workstream turns out to need one, it stops and
  becomes a design question instead.

## Seams that are real, and seams that are hypothetical

Worth writing down so nobody mistakes a deferral for a validated abstraction. A
seam earns its name when something actually varies across it.

*Real* (two or more adapters): `Connector` (sqlite / postgres / redshift),
`ChatClient` and `Embedder` (LangChain live, `StaticChatClient` +
`HashingEmbedder` offline; the offline pair is what makes CI deterministic),
`Responder` (`SimulatedSme` / `StaticResponder`), `AnswerNarrator`.

*Hypothetical* (one adapter or none): `WorkingMemory`, with Episodic and
Correction deferred by D8; `NoteActivation.on_match`, whose PIN retrieval mode has
no data exercising it; `edit_mode="pr"`. All three are documented deferrals rather
than accidents, and `WorkingMemory` costs three methods, so they stay. They are
just not evidence of anything.

## Do not refactor these

They are the models the rest should converge on.

- **`guardrails.check()`**: five layers and ~900 lines of AST work behind one
  function returning one verdict type, fail-closed on its own exceptions, with an
  `on_layer` observer documented as unable to influence the verdict. Observation
  without authority is the detail worth copying.
- **`viz/presenter.py`**: pure `Corpus -> View` functions, no side effects, with
  `api/app.py` as a thin HTTP adapter over it. Complexity in the presenter,
  protocol concerns in the app.
- **`eval/metrics.py`**: a metric register as an enforced contract, with tests
  asserting nothing reaches an artifact undeclared. It is why W3 is a mechanical
  job rather than a risky one.
- **`stages.py`**: one outcome vocabulary shared by serve and eval. It is the
  module that made "a crash was being counted as a refusal" findable at all.

