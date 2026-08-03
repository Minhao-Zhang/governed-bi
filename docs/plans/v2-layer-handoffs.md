# v2 layer handoffs

The file inventory for every unbuilt layer, written so a layer can be handed to an
engineer who has read `docs/adr/0005`, `docs/adr/0006` and
`docs/lessons-from-v1.md` and nothing else.

Until 2026-08-03 this decomposition existed only in a conversation. That is the
same defect class as the ones the register layer exists to prevent — a contract in
prose that nothing can import and nobody can check — so it is written down before
any of it is parcelled out.

**Status: proposed.** Two corrections in §0 must be settled before the skeleton
`.py` files are generated, because both move code between layers.

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
| **C** | `datasource/` + `corpus/seed.py` | **now** | needs Postgres access | produces the seeded corpus everything downstream consumes; measurable with zero model calls |
| **D** | `corpus/` (schema, store, hash, sanitize) | **now** | — | the asset types and their validation; file I/O and dataclasses |
| **E** | `retrieve/` | after **D** | corpus asset types | the largest self-contained algorithmic piece |
| **F** | `serve/` | after A–E | everything | **do not parcel** — see §7 |
| **G** | `eval/` | after **F** | a working serve path | |
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
| `datasource/introspect.py` | tables, columns, physical types, declared FKs |
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
| `corpus/schema.py` | the eight asset dataclasses: `summary`, `body`, `rules`, tags |
| `corpus/validate.py` | summary ≤ 250 chars; identifier present; tag rule satisfied |
| `corpus/store.py` | `CorpusStore` adapter over YAML; per-item error isolation |
| `corpus/hash.py` | `corpus_content_hash` — **one** implementation |
| `corpus/sanitize.py` | default-deny redaction driven by `register/assets.py` |

**Traps**

- **`load` returns `(assets, problems)` and never raises for a bad item.** v1's
  loader raised on the first unparseable file, inside a `try/finally` with no
  `except`, and **one truncated YAML discarded a fully paid 69-schema build with no
  clue why**. The opposite failure is equally real: a silent skip turns "a corpus
  that lost half its assets" into "a corpus that merely looks small", and this
  project has already published a result on top of that.
- **Sanitisation is default-deny.** Every string field is sanitized; the exemptions
  are `verbatim_fields` in `register/assets.py`. v1 sanitized note text only, so a
  column *description* was the cheaper poisoning vector.
- **`corpus_content_hash` must never have an "unknown" sentinel.** v1's compared
  equal to itself, so two runs with no recorded treatment passed comparability.
- **Summary is the only indexed field (I1) and body is what the system uses on hit
  (I2).** A validator that lets a 4,000-character summary through has broken the
  index; one that requires a body has broken the seed.

---

## 6. Parcel E — `retrieve/` (after D)

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

## 7. Parcel F — `serve/`: do not hand this out

`serve/` is where every other parcel's assumptions meet. Handing it to a seventh
engineer means the integration is done by the person with the least context on all
six inputs.

Its own traps are LangGraph-specific and are the reason a skill read is mandatory
before touching it:

- **Five facets writing one state key in one super-step raises
  `InvalidUpdateError`** — it does not overwrite. Concurrently-written channels
  need a declared reducer. ADR 0005's first draft asserted this shape was
  "concurrency safe"; it is not, and a review agent found it only by reading the
  LangGraph skill.
- **No `from __future__ import annotations`** in modules loaded by file path.
- Fan-out buys **latency, not cost**: `max(branches)` wall-clock,
  `sum(branches)` spend. That is why the two facets calling no model are the cheap
  ones.

| file | what it holds |
|---|---|
| `serve/state.py` | the state schema **and its reducers** |
| `serve/graph.py` | node registration and the fan-out edges |
| `serve/nodes/` | `gate`, `facets`, `route`, `agent`, `stamp` |
| `serve/tools.py` | `read_body`, `inspect_schema`, `sample_rows`, `run_query`, `ask_user` |
| `serve/context.py` | rendering, the eviction order, `context_budget_chars` |

`tests/conformance/test_register_closure.py` already carries the acceptance test
for this parcel as `xfail(strict=True)`: **a real turn on every terminal path
writes every required field.** Strict, so it fails the suite the moment it starts
passing and someone has to turn it into a real test.

---

## 8. Parcels G and H

**G — `eval/`** (`harness`, `arms`, `grade`, `report`). Needs a working serve path.
Its trap list is the whole of `lessons-from-v1.md` §1–§5; the single most expensive
one: **a crash counted as a refusal**, which contaminated every arm-to-arm delta by
a different amount, because arms do not crash at the same rate.

**H — `curate/`** (see §0.2). The largest piece and the origin of this rewrite. Not
specified here; it needs its own ADR before it is parcelled, because "ask better
and more detailed questions" is a design question, not an implementation one.

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
