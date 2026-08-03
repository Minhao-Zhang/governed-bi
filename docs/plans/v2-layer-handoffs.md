# v2 layer handoffs

The file inventory for every unbuilt layer, written so a layer can be handed to an
engineer who has read `docs/adr/0005`, `docs/adr/0006` and
`docs/lessons-from-v1.md` and nothing else.

Until 2026-08-03 this decomposition existed only in a conversation. That is the
same defect class as the ones the register layer exists to prevent — a contract in
prose that nothing can import and nobody can check — so it is written down before
any of it is parcelled out.

**Status: in progress.** `ACCEPTED = {B, D, E}` in `tests/contracts.py`; `C`, `F` and `G`
carry code that no design holder has signed off. §7 and §8 are **rework** plans as of
2026-08-03 — both parcels were built and self-graded before they had contracts, and both
came back with a defect a contract would have caught. Every parcel that had one came back
sound. The critical path is now **C**: with no working connector, no governed query can
execute anywhere, so F's and G's contract tests cannot be written honestly.

---

## 0. Two corrections this document forced

### 0.1 `Measured[T]` belongs in `register/`, not `measure/` · **done; the first argument for it was wrong**

The plan had `Measured[T]` — the three-valued *measured / not_measured /
not_applicable* wrapper that L-R1 requires everywhere — in `measure/quantity.py`,
layer 3. It now lives in `register/quantity.py`, layer 2.

**The reason I first gave was too strong, and it is worth recording as wrong**, since
it is the kind of argument that sounds structural and is not. I wrote that layer 2
"cannot name the type of the thing it is declaring". But `register/record.py` already
had an `Absence` column with a `not_measured` member — it *could* declare
three-valuedness, and did. So that argument does not carry the decision.

**The real reason, found by implementing it.** `missing_required()` is the presence
test, and it checked `value is None`. `Measured.unmeasured("provider reported no
token count")` is not `None` — so a required field carrying an **explicit
non-measurement passed the presence test**. Introducing `Measured` reopened the exact
hole the previous fix had closed, and the fix requires `record.py` to recognise the
type, which requires importing it, which requires it to be in this layer or below.

That makes three appearances of one shape:

1. v1's `corpus_content_hash == "unknown"` comparing equal to itself, so two runs
   with no recorded treatment passed comparability.
2. `missing_required` checking key-presence only, while `project` writes every key.
3. This one — created by the type introduced to prevent the class.

Each fix was correct and each left the next instance reachable, because the defect is
not any particular sentinel: **a check for absence has to know every way absence can
be spelled.** Reducing that to one spelling, in one importable type, is the actual
argument. `tests/conformance/test_quantity_presence.py` holds all three cases
including the complement — a *measured* zero must still count as a value, or the fix
gets reverted for being too strict.

What stays in `measure/`: `population.py`, `stats.py`, `price.py`, `gates.py` —
things that *compute*, not things that *declare*.

### 0.2 The curator is not in the layer stack, and it is the largest piece

`ADR 0005` assumes an agent produces asset `body` text. No layer owns it. It is
also the thing this whole rewrite started from — *"完全重新设计 curator,以便问更好、
更详细的问题"*.

It does not fit the stack because it is not a layer: it is a **second application**
that shares `register/`, `corpus/`, `datasource/` and `model/` with serve, and
shares nothing else. Proposed: `curate/` as a sibling of `serve/`, same layer
index, with an explicit rule that neither imports the other.

---

## 1. What can actually be handed out, and when

The layer list is not the parcel list. Dependencies:

```
ports ── register ── measure
             │          │
             ├── datasource ──┐
             ├── corpus ──────┼── retrieve ──┐
             ├── govern       │              │
             └── model ───────┴── curate     ├── serve ── record ── eval ── api
                                             │
                                    (serve needs all of the above)
```

| parcel | layers | can start | blocked by | why it is a clean parcel |
|---|---|---|---|---|
| **A** | `measure/` + the two missing CI gates | **now** | — | pure computation over declared tables; no DB, no model, no network |
| **B** | `govern/` | **now** | — | ADR 0006 is a complete spec including the bypass list it must close; testable with a SQL string and nothing else |
| **C** | `datasource/` + `corpus/seed.py` | **now, and it is the critical path** | a reachable Postgres (have one) | **Postgres only** (#40). `PostgresConnector` is 69 lines with five stub raises, so nothing in the tree can execute a governed query — F and G both wait on it |
| **D** | `corpus/` (schema, store, hash, identity) | **now** | — | the asset types and their validation; file I/O and dataclasses |
| **E** | `retrieve/` | after **D** | corpus asset types | the largest self-contained algorithmic piece |
| **F** | `serve/` | **rework** — see §7 | **C**, for an honest end-to-end test | code exists, not accepted; five localised defects, sound topology |
| **G** | `eval/` | **rework** — see §8 | **C** and **F-1** | code exists, not accepted; the grader executes outside governance |
| **H** | `curate/` | after **D** | corpus asset types | its own project, not a layer |

**Four parcels can start immediately; one engineer per parcel; A/B/C/D are
genuinely independent.** Everything after that serialises.

---

## 2. Parcel A — `measure/`

Five files. `register/quantity.py` moves out of this parcel per §0.1 but the
engineer owns its tests.

| file | what it holds |
|---|---|
| `register/quantity.py` | `Measured[T]`, three-valued, **and its rounding and formatting helpers** |
| `measure/population.py` | one `Population` per metric: the row set, its n, and how it was filtered |
| `measure/stats.py` | **one** McNemar; MDE at the comparison's own n; the rule-of-three floor |
| `measure/price.py` | dated price table; unknown model → `not_measured`, never `0.0` |
| `measure/gates.py` | quotability predicates, reading `register/record.py`'s gate conditions |

**Interface**

```python
# register/quantity.py
class Measured(Generic[T]):
    @classmethod
    def of(cls, value: T) -> Measured[T]: ...
    @classmethod
    def not_measured(cls, why: str) -> Measured[T]: ...
    @classmethod
    def not_applicable(cls, why: str) -> Measured[T]: ...
    def rounded(self, places: int) -> Measured[float]: ...
    def render(self, places: int, unit: str = "") -> str:
        """Never returns "0" or "0.0%" for an unmeasured quantity."""

# measure/population.py
class Population:
    """The row set a metric was computed over, carried with the metric.

    Constructed once and passed to BOTH the headline and the significance test.
    """
    rows: Sequence[Mapping[str, object]]
    n: int
    filtered_by: tuple[str, ...]
    def restrict(self, predicate, label: str) -> Population: ...

# measure/stats.py
def mcnemar(a: Population, b: Population, key: str) -> McNemarResult: ...
def mde(n: int, base_rate: float, alpha: float = 0.05, power: float = 0.8) -> Measured[float]: ...
def rule_of_three_ceiling(n: int) -> Measured[float]:
    """0 observed in n trials bounds the rate at 3/n. A BOUND, and must render as one."""
```

**Why `Population` is an object and not two arguments.** L-R3: v1 computed the
headline over one row set and the significance test over another, and the
discrepancy was invisible because each call site filtered independently. One
object passed to both makes the divergence unrepresentable.

**Acceptance tests, written before the code:**

1. `Measured.not_measured("no price").render(1, "%")` does not contain `0`.
2. A `round(` or a `:.` format spec anywhere outside `register/quantity.py` fails
   a CI grep. (This gate does not exist yet — parcel A builds it.)
3. `mcnemar` and the headline given the same `Population` report the same n, and
   `mcnemar` **raises** if handed two populations with different `filtered_by`.
4. `rule_of_three_ceiling(200)` renders as a bound (`≤ 1.5%`), never as `0.0%`.
5. `price.estimate(model="unknown-model", ...)` returns `not_measured`, and no
   arithmetic in the module can turn a `not_measured` into a number.

**The two CI gates parcel A must build**, because every other parcel needs them
and none of them can add them for itself:

- **file length**: soft 400 (warn), hard 800 (fail). ADR 0005 §6 declares it
  CI-enforced; nothing enforces it. v1 reached 17 files over 1,000 lines.
- **duplicate concept**: one implementation per concept, one import name. v1 had
  two McNemars (`eval/analysis.py:572`, `eval/power.py:338`) and two
  `LOW_CONFIDENCE_JOIN` constants **with different comparison operators**. With
  parcels running in parallel, two McNemars is the *default* outcome, not a slip —
  and the gate proved it by catching a real collision within a day of being written:
  `ports.Row` (`tuple[Any, ...]`, a database row) against `measure.Row`
  (`Mapping[str, object]`, a recorded turn), two different types under one import
  name. Renamed to `TurnRow`.

  A third claim carried here from ADR 0005 §6 — "two EX definitions" — **could not be
  sourced** and has been struck from both documents: v1 had one `execution_match` in
  `eval/ex.py`, imported everywhere. It was found by an agent that went looking for
  the incident behind a rule it was asked to enforce, which is the check the rest of
  this section is asking for.

---

## 3. Parcel B — `govern/`

ADR 0006 is the spec. Four files.

| file | what it holds |
|---|---|
| `govern/layers.py` | the seven-member `Layer` IntEnum, and which layer each rule sits at |
| `govern/guard.py` | pre-model input guard: length, encoding, the rule set |
| `govern/check.py` | sqlglot parse → allowlist → the whole-row-aggregate rule |
| `govern/ledger.py` | the governance ledger entry, one per decision |

**Interface**

```python
def guard(question: str, knobs: Knobs) -> GuardVerdict: ...
def check(sql: str, licensed: LicensedSet, knobs: Knobs) -> CheckVerdict: ...
```

Both return a verdict carrying **the layer that refused and the rule id**, never a
bare bool — `register/stages.py` maps `refused_by` to a stage and an outcome, and a
bool cannot feed it.

**The traps, all from ADR 0006:**

- **B1–B10 is a checklist, not prose.** Each of the ten bypasses needs a test
  that reproduces it against the new code. One of the ten (B2) was found in that
  ADR's own first draft.
- **The whole-row-aggregate rule.** `json_agg(t)` exfiltrates every column of `t`
  while containing **zero `Column` nodes**, so a column-level allowlist walking the
  AST for `Column` sees nothing to reject. Found while writing the ADR.
- **A positive allowlist, hashed by content.** Not a denylist. The allowlist's
  content goes into the config hash, so widening it moves the hash and breaks
  comparability with earlier runs — which is correct and must not be worked around.
- **`sqlglot` version is pinned** and is a comparability knob: canonical function
  names are release-dependent, so an unpinned upgrade silently changes what the
  allowlist matches.

---

## 4. Parcel C — `datasource/` + `corpus/seed.py`

| file | what it holds |
|---|---|
| `datasource/postgres.py` | `Connector` adapter; the obfuscated BIRD DBs are Postgres-only |
| `datasource/sqlite.py` | `Connector` adapter for the unobfuscated dev path |
| `corpus/introspect.py` | tables, columns, physical types, declared FKs (shapes live under `corpus/` so the seed does not import upward into `datasource/`) |
| `corpus/seed.py` | introspection → a valid asset for every table and column, **zero model calls** |

**Why the seed is in this parcel and not `corpus/`.** It is the thing that makes
ADR 0005's "steps 6–9 are measurable with no model at all" true: the seed
guarantees a non-empty `summary` on every asset, so retrieval can be measured
before a curator exists. It is bounded by what introspection returns, so it
belongs with introspection.

**Interface**

```python
def seed(introspection: Introspection, schema: str) -> tuple[list[Asset], list[Problem]]: ...
```

**Traps**

- **`execute` returns `(columns, rows, truncated)`**, and `truncated` is derived
  from a `max_rows + 1` limit — never inferred by the caller from a row count.
  Three of four v1 grader-ceiling misses were row-cap related.
- **`OperationalError` is not an infrastructure failure.** SQLite wraps *"no such
  column"* in it. Classifying that family as a crash hides wrong answers as
  crashes, which is the inversion that retired a set of numbers.
- **The seed writes the qualified form** `table.column (text)` for a column
  summary, because at seed time there is nothing better — and a curator that
  later rewrites it shorter, dropping the qualifier, **must not fail validation**
  (decision #6).

---

## 5. Parcel D — `corpus/`

| file | what it holds |
|---|---|
| `corpus/identity.py` | path-component validation (`\A...\Z`); column id derivation |
| `corpus/schema.py` | the eight asset dataclasses: `summary`, `body`, `rules`, tags |
| `corpus/validate.py` | summary ≤ 250 chars; identifier present; tag rule satisfied |
| `corpus/store.py` | `CorpusStore` adapter over YAML; per-item error isolation |
| `corpus/hash.py` | `corpus_content_hash` — **one** implementation |

There is **no** corpus sanitizer. ADR 0005 §1.6 / decision #37: the corpus is
trusted; injection is checked once on the incoming question by `govern.guard`.

**Traps**

- **`load` returns `(assets, problems)` and never raises for a bad item.** v1's
  loader raised on the first unparseable file, inside a `try/finally` with no
  `except`, and **one truncated YAML discarded a fully paid 69-schema build with no
  clue why**. The opposite failure is equally real: a silent skip turns "a corpus
  that lost half its assets" into "a corpus that merely looks small", and this
  project has already published a result on top of that.
- **`corpus_content_hash` must never have an "unknown" sentinel.** v1's compared
  equal to itself, so two runs with no recorded treatment passed comparability.
- **Summary is the only indexed field (I1) and body is what the system uses on hit
  (I2).** A validator that lets a 4,000-character summary through has broken the
  index; one that requires a body has broken the seed.
- **Path-component validation is accident prevention, not anti-poisoning** (B8 /
  ADR 0006 §9). Identifier fields that become directories are refused, never edited.

---

## 6. Parcel E — `retrieve/` (after D)

**Status: built** (scoring contract green; `UNBUILT` is `{"F", "G"}`).

Nine files, the largest algorithmic parcel.

| file | what it holds |
|---|---|
| `retrieve/result.py` | `Hit` (facet, asset, both channel scores, every query that hit), `RetrievalResult` |
| `retrieve/index.py` | build both indices over `summary` only |
| `retrieve/lexical.py` | BM25, saturating `s/(s+k)`, **global IDF** |
| `retrieve/semantic.py` | cosine; cache keyed on `(model, dimensions)` |
| `retrieve/fuse.py` | `hybrid` = weighted sum, renormalised by **active** channels |
| `retrieve/route.py` | per-facet max, summed; the two-pass structure |
| `retrieve/resolve.py` | reference closure — a **total** function of the hit set |
| `retrieve/connect.py` | Steiner, bounded; can pick a wrong path, so it records which |
| `retrieve/budget.py` | per-type budgets from `register/assets.py` |

**Traps**

- **`cosine` must raise on a width mismatch, not return `0.0`.** v1 returned 0.0,
  so a cross-model cache hit degraded routing to "nothing scores" with no error
  anywhere. This is why `Embedder` is a port at all: `model` and `dimensions` are
  part of the interface, not a convention each caller remembers.
- **A channel that did not run is not a channel that scored zero.** `Hit.lexical`
  on the `example` facet is `not_applicable` — by design, since term-frequency
  matching between two natural-language questions rewards shared function words.
  On `entity` the same absence means the index died. `register/facets.py` decides
  which; `retrieve/` must never decide it locally.
- **`fuse` renormalises by active channels.** Otherwise a one-channel facet scores
  systematically lower than a two-channel one and the budget silently prefers the
  richer facet.
- **IDF is global, not within-schema** (settled; within-schema is an open question
  in ADR 0005, not an option here).
- **`resolve` is total, `connect` is not.** Conflating them is how a bounded
  best-effort path gets reported as a closure.

---

## 7. Parcel F — `serve/`

**Status: code exists and is not accepted.** 21 files, 2,876 lines, written without a
design-holder contract and graded by its own implementer. `tests/serve/test_turn_contract.py`
now exists — 12 specifications, strict xfail, bodies unwritten. `ACCEPTED` does not
contain `F`.

**This is a rework plan, not a build plan**, and the distinction matters for who does it.
An adversarial review with independent reproduction found the graph wiring, the reducers,
the HITL interrupt path and the record projection **sound**. What is wrong is five
localised judgement calls, all of one kind. So this is not a rewrite, and whoever picks it
up should be told that plainly — a rework brief that reads like a condemnation gets
answered with a rewrite, and the good parts get thrown away with the bad.

### The one rule all five violations share

> **The record must describe what happened, not what was supposed to happen.**

Every v1 number this project retired died of that: a field reporting the *configuration*
rather than the *observation*, so a broken run and a clean run produced identical
artifacts. `serve/` reintroduced it five times, and in three of those the field had been
added specifically to prevent it.

### The five

| # | what it does now | why it is wrong | the fix |
|---|---|---|---|
| **F-1** | `outcome: answered` on a turn where **every** SQL attempt was refused; `execution.terminal: "answered"` beside `passed: false` | `has_sql` is derived from the tool-call *arguments*, so producing a string counts as producing an answer. A governed refusal recorded as an answer is the crash-counted-as-refusal inversion that retired the pre-2026-07-25 numbers, pointing the other way | terminal state derives from the **ledger**, not from whether a string exists. A turn with no passing attempt is a refusal or a decline |
| **F-2** | `_channels_for` returns `expected_channel_state(...)` verbatim, so a facet with **no index and no model** reports `{'lexical': 'ran', 'semantic': 'ran'}` | this is the field's entire purpose inverted. `register/facets.py` is three-valued (`ran` / `not_configured` / `failed`) *precisely* so "should have run and did not" is expressible | observe, then compare to the declaration. A facet that could not consult an index has a **failed** channel |
| **F-3** | nothing writes `facet_degraded`; `channel_anomaly` and `is_degraded` have **zero call sites outside tests** | so `measure/gates.py` passes vacuously — `[pass] facet_channels 0.0000 over 'stub' n=3 (fan-out ran)` on an arm with no index. A gate whose input nobody produces is worse than an absent gate, because the summary says the run was checked | call `is_degraded` per facet per channel and write the result |
| **F-4** | `pass_two_retrieve` scores `facet_example` on `lexical`, which the same record declares `not_configured` | ADR 0005 §2 is explicit that `register/facets.py` decides a facet's channels and `retrieve/` must never decide locally. `facets.py` has the `Channel.lexical in FACET_CHANNELS[stage]` guard; `pass_two` does not | add the guard. It is one condition, and its absence let a few-shot outrank an entity hit on a channel that never ran |
| **F-5** | `stamp.py` substitutes `{"outcome": "error_failed_open"}` for an **absent** `guard`; `agent_core.py` writes literal `input_tokens: 0` for a real model call; `tools.py` defaults an absent corpus to `analyst_corpus_from_keys(allowed=())` | three flavours of L-R1. The first **fabricates a security event** — that sentinel means the guard ran, errored, and let the question through, and `register/record.py` gates on it. The second is a measured zero that `measure/price.py` will bill as free, which is v1's two-ladders-with-no-USD failure. The third records "the corpus was never wired up" as `r_column_not_allowed` with `guardrail_errors: 0` | `Measured.unmeasured(why)` for the token count; absent guard stays absent; the corpus default **raises** — production `check()` already does, and `serve/` must not catch it and substitute |

### What must not be touched

The graph topology, the channel reducers, `ask_user` identity binding, and
`register/record.py`'s projection. The review drove a real turn through the whole path
— real seed, real BM25 index, route, resolve, connect, assemble, `create_agent`, stamp —
and `missing_required` came back clean with a real `context_hash`. That part works.

Three runtime `pytest.xfail("waiting on Agent B")` escape hatches in
`tests/serve/test_pass_two_and_context.py` must go. None is currently taken, which is
exactly why they are dangerous: they will silently absorb a regression.

### The dependency, and it is hard

**F-1's contract test cannot be honest until `PostgresConnector` exists.** A test asserting
"a turn whose SQL was refused is not `answered`" needs a turn whose SQL could otherwise
have *succeeded*; with no working connector there is no such turn, and the test would pass
for the wrong reason. Same for the end-to-end test, whose predecessor was satisfied by
`STUB_ANSWER` precisely because nothing real was reachable.

So: **implement `PostgresConnector` first** (parcel C), then write F's contract bodies,
then fix F. Doing F before C produces a green suite that proves nothing, which is the state
this parcel is already in.

---

## 8. Parcel G — `eval/`

**Status: code exists and is not accepted.** 7 files, 876 lines. `ACCEPTED` does not
contain `G`. `tests/eval/test_grading_contract.py` now exists — 7 specifications, strict
xfail, bodies unwritten. The pre-existing `tests/eval/test_eval_contract.py` is
**implementer-authored** and claims in its header to be written "against the plan, not the
impl"; that claim is unverifiable and it is the authorship pattern `tests/contracts.py`
exists to prevent. It may stay as internal tests; it is not an acceptance criterion.

### The blocking defect

`harness.py` calls `connector.execute(str(generated_sql))` **with no `govern.prepare`**.
So the grader re-runs a statement governance refused, grades it against gold, and reports:

```
[tool] run_query refused: r_table_not_licensed
outcome: answered   generated_sql: SELECT count(*) FROM customers
-> project_turn -> {"correct": true, "grade_detail": "match"}
```

Two separate things are wrong and both must be fixed.

**The grader must not be able to execute at all.** A component that reaches the database on
a path `check()` does not guard is the topology breach ADR 0002's surviving principle
forbids — *governance is topology, not trust*. Fixing the one call site leaves the next one
reachable; the defect is that `eval/` **can** execute. That is why G's contract asserts it
structurally, not just behaviourally.

**A refused turn has no result to grade.** It is not an incorrect answer and not a correct
one. It is a refusal, and it belongs in its own stratum — which `register/stages.py`
already provides.

This is also **why #39a hid for a day**: the out-of-band re-execution meant the numbers
looked fine while the intersection of "govern permits" and "the connector executes" was
empty. The `scripted` arm's `ex=1.00` was produced with **zero** successful in-turn
executions. Every EX this harness has ever reported is void.

### The absence coercions

Four lines, each turning "we did not record this" into "this did not happen", feeding
exactly the fields the quotability gates read:

| line | now | must be |
|---|---|---|
| `harness.py:107` | `str(... or ... or "crashed")` — a missing outcome **counts as a crash** | `not_measured`; a population containing one is not quotable |
| `harness.py:160` | `int(record.get("guardrail_errors") or 0)` | absent ≠ zero |
| `harness.py:161` | `int(state.get("n_re_served") or ... or 0)` | absent ≠ zero |
| `harness.py:176` | `bool(record.get("facet_degraded") or False)` | absent ≠ clean — and nothing writes it (F-3), so this made the degradation gate vacuous on every run |

`measure/gates.py` already distinguishes `cannot_evaluate` from `pass`. The harness must
hand it the distinction rather than resolving it, because resolving it is the resolution
the gate exists to refuse.

### What is sound

The harness/arms/oracle/report scaffolding, and `report.py`'s `Measured` discipline —
every quantity goes through it, `unmeasured` on all four cannot-evaluate branches. It had
exactly one bypass, an f-string `.4f` on the line that prints the gate's own verdict, and
that is fixed. The cross-arm `context_hash` ≥95% distinctness gate works and correctly
refused a 3-question probe run.

### The property that would have caught all of it at once

**An arm with zero successful in-turn executions must not publish an EX.** Not a patch on
any of the above — a single precondition that makes the whole class visible, and the
execution count must be *recorded* so the refusal is checkable rather than inferred. It is
in G's contract as `test_an_arm_with_zero_successful_executions_is_not_quotable`.

### Order

After C (a connector) and after F-1 (a truthful terminal state), because grading a turn
requires the turn's outcome to mean something. G's own rework is small; its dependencies
are not.

---

## 8b. Parcel H — `curate/`

See §0.2. The largest piece and the origin of this rewrite. Not specified here: it needs
its own ADR first, because *"ask better and more detailed questions"* is a design question,
not an implementation one.

---

## 9. What this decomposition does badly

Stated because a handoff plan that only lists its strengths is not reviewable.

1. **Layer boundaries are the wrong seam for parcelling *this* codebase.** Nearly
   every v1 defect was two places that had to agree and did not. Splitting by layer
   puts each of those defect classes exactly on a handoff boundary. What makes it
   survivable is that `register/` is stdlib-only and checks itself at import, so the
   shared vocabulary is an import that fails rather than a document that drifts —
   but the register covers **declarations only**. A question like *"what does
   `Hit.lexical` mean when the lexical channel did not run"* spans `register/`
   (declares), `retrieve/` (produces), `record/` (stores) and `measure/` (gates it).
   Four parcels, one semantics, no single owner.

2. **Real parallelism is four, not twelve.** Everything after E serialises, and the
   two hardest pieces (`serve/`, `curate/`) are last.

3. **Most parcels have no acceptance test yet.** A and B have them above; C, D, E
   have trap lists, which is weaker. v1's evidence is that a test which re-derives
   the logic it is checking passes while the logic is broken — its gold-gate test
   re-computed `share > THRESHOLD` itself, so deleting the gate, flipping the
   comparison, and reversing the denominator **all passed**. So each parcel needs
   its acceptance test written *before* handoff, by whoever holds the whole design.

4. **Two CI gates ADR 0005 declares do not exist** — file length and duplicate
   concept — and they constrain every parcel. They are in parcel A, so parcels
   B–E will run for a while without them.
