# When this engine answers with wrong SQL, what is actually wrong with the SQL

**Arm**: `runs/eval/proxy_v4_corpus30872d3.jsonl` (v4, EX 0.6758) — **engine** `3c0079a`,
**corpus** `../BIRD-corpus` @ `30872d3`, **prompt** ANALYST v4, routing pinned to v3-fold.
**Replication arm**: `runs/eval/proxy_v3_fold_opus_high_corpus30872d3.jsonl` (v3-fold, EX 0.6640).
**Database**: `PG_RENAME_DECOY_DSN` (the obfuscated rename-decoy lake), read-only.

Every number below is on corpus `30872d3`. Numbers from either arm are stated with the arm
named; the two arms are *not* combined anywhere, they are reported side by side so a pattern
that is one run's noise is visible as such.

This document opens the largest winnable bucket in [failure-modes.md](../failure-modes.md) §4:
**answered, full gold-table coverage, wrong** — 257 questions on v4, 262 on v3-fold. It does not
restate that page; it goes inside its §4 and asks what the statements got wrong.

---

## 0. Method, and what makes these numbers trustworthy

Three disciplines, all house style (`docs/failure-modes.md` §方法):

**Control group.** Every feature is computed on the 865 *correct* full-coverage answers as well,
and reported as lift. Three features that look damning on failures turn out to be more common on
correct answers, and one of them (`aggregate_arg`, lift 0.23) would have been a headline.

**Causal repair.** A controlled output-shape fix is applied to each wrong prediction, the
statement is **re-executed against the live database and re-fingerprinted**. That converts
"correlated with failure" into "n questions recovered", an upper bound.

**Comparator read first.** `eval/grade.py::result_fingerprint` hashes *row tuples only* — column
names are explicitly not hashed, element order within a row is significant, row order is relaxed
except for the 97 `order_sensitive` ids. So an extra projected column is a guaranteed miss (it
lengthens every tuple) and a renamed column is free.

### Instrument validity

| Check | Result |
|---|---|
| 60 random recorded predictions re-executed | **59/60 reproduce the recorded `pred_fingerprint`** |
| the one that does not (`train_3093`) | `ORDER BY … LIMIT 1` over a non-total order; its gold is a frozen literal, so it is outside this population |
| gold re-executed for all 257 | **247/257 reproduce the recorded `gold_fingerprint`** |
| the 10 that do not | gold is not a function of the query (`ORDER BY` without a total order, or a >200k-row gold truncated at the connector cap) |
| predictions that no longer execute | 1 (`train_2214`, 90 s statement timeout; the harness ran without one) |

**The 10 unstable golds forced a correction that changes the answer.** The first pass graded
repairs against a *freshly executed* gold and reported three extra recoveries. All three were
questions whose gold returns something different on each execution. Every repair below is graded
against the row's **recorded** `gold_fingerprint` — the digest the run was actually scored on —
and falls back to a fresh execution only where the artifact carries none.

---

## 1. The taxonomy

Mutually exclusive: each of the 257 lands in exactly one row. Shape buckets are assigned by the
**causal repair** (§3); everything left is assigned by the *first* structural divergence between
gold and prediction under a stated upstream→downstream priority — table set, then joins, then
filter columns, then aggregate function, then filter literals, then grain, then aggregate
argument, then ordering/limit, then output shape. The priority is a choice, so §2 reports the
same features priority-free with lift.

| Bucket | v4 | % | v3-fold | % |
|---|---:|---:|---:|---:|
| **semantic: wrong table set** | **52** | 20.2% | **52** | 19.8% |
| semantic: wrong filter literal | 40 | 15.6% | 44 | 16.8% |
| semantic: wrong filter column | 38 | 14.8% | 32 | 12.2% |
| semantic: wrong join (count or key) | 20 | 7.8% | 21 | 8.0% |
| semantic: wrong aggregate function | 20 | 7.8% | 20 | 7.6% |
| **repaired: DISTINCT missing** | **19** | 7.4% | **22** | 8.4% |
| semantic: shape differs, values also differ | 14 | 5.4% | 15 | 5.7% |
| **repaired: DISTINCT spurious** | **12** | 4.7% | **15** | 5.7% |
| **gold is unstable** (dataset defect) | **10** | 3.9% | **10** | 3.8% |
| semantic: no static difference at all | 8 | 3.1% | 10 | 3.8% |
| **repaired: over-projection** | **7** | 2.7% | **6** | 2.3% |
| semantic: wrong grain (GROUP BY / HAVING) | 5 | 1.9% | 5 | 1.9% |
| semantic: aggregate over the wrong column | 5 | 1.9% | 4 | 1.5% |
| **repaired: column order** | **3** | 1.2% | **1** | 0.4% |
| semantic: wrong ordering or limit | 2 | 0.8% | 2 | 0.8% |
| **repaired: projection + DISTINCT** | **1** | 0.4% | **1** | 0.4% |
| repaired: safety LIMIT | 0 | — | 1 | 0.4% |
| instrument: prediction no longer executes | 1 | 0.4% | 1 | 0.4% |
| **total repaired by output shape** | **42** | **16.3%** | **46** | **17.6%** |
| **total genuinely semantic** | **204** | **79.4%** | **205** | **78.2%** |

**The taxonomy replicates.** No bucket moves by more than 3 questions between two arms whose
prompts differ, and the two largest (wrong table set, wrong filter literal) are within one
question of each other. This is a property of the question set, not of the prompt.

Result-set relationship for the 204 semantic failures (v4): 136 **disjoint** values, 31 differing
arity, 13 pred ⊂ gold, 10 pred ⊃ gold, 8 partial overlap, 4 same set / different multiplicity,
2 pred empty. The dominant mode is *computing a different quantity*, not returning the right
quantity badly filtered.

---

## 2. Feature lift against the correct-answer control

Priority-free: every feature computed on 257 failures and 865 correct answers.

| Feature | v4 fail % | v4 control % | **v4 lift** | v3-fold lift |
|---|---:|---:|---:|---:|
| projection wider than gold | 8.6 | **0.0** | ∞ | ∞ |
| projection narrower than gold | 7.4 | **0.0** | ∞ | ∞ |
| **literals differ only in case** | **2.7** | **0.1** | **23.6** | ∞ (7 vs 0) |
| HAVING present on one side only | 1.9 | 0.1 | 16.8 | ∞ |
| gold and prediction share no table | 4.3 | 0.3 | 12.3 | 11.9 |
| **DISTINCT missing** | 12.1 | 1.5 | **8.0** | 7.3 |
| prediction reads a table gold does not | 13.2 | 1.7 | 7.6 | 10.6 |
| GROUP BY differs | 16.0 | 3.0 | 5.3 | — |
| join key set differs | 12.8 | 2.9 | 4.4 | 6.1 |
| prediction omits a gold table | 14.8 | 3.4 | 4.4 | 4.5 |
| extra join | 10.9 | 2.4 | 4.5 | 5.7 |
| prediction has a literal gold does not | 31.1 | 8.7 | 3.6 | 3.7 |
| subquery structure differs | 14.8 | 4.0 | 3.7 | 3.4 |
| missing join | 11.3 | 3.7 | 3.1 | 3.0 |
| aggregate *function* differs | 23.0 | 8.6 | 2.7 | 2.5 |
| WHERE column set differs | 29.6 | 11.3 | 2.6 | 2.7 |
| ORDER BY differs | 12.1 | 4.5 | 2.7 | 2.5 |
| **DISTINCT spurious** | 17.1 | **8.3** | **2.1** | 1.8 |
| aggregate over a different *column* | 1.9 | 8.6 | **0.23** | 0.19 |
| statements structurally identical | 7.4 | 59.5 | 0.12 | 0.11 |

Three readings that a failures-only count would have got wrong:

- **Aggregating over a different column is a *negative* signal** (lift 0.23). 74 correct answers
  aggregate a different column than the gold — `COUNT(*)` against `COUNT(id)`, `COUNT(pk)` against
  `COUNT(fk)` — and it almost never changes the number. A "match the gold's aggregate argument"
  rule would be aimed at noise.
- **Spurious DISTINCT is nearly harmless** (lift 2.1, but 72 correct answers carry one). Measured
  causally: of the 116 v4 statements with a DISTINCT the gold lacks, only **12 (10.3%)** are wrong
  *because* of it. Missing DISTINCT is the real signal — lift 8.0, and 20 of the 44 appearances
  are recovered by adding it.
- **Projection-width lift is infinite and is a tautology, not a diagnosis.** Zero correct answers
  have a width mismatch, because the comparator hashes tuples. `docs/failure-modes.md` §11 already
  priced this: v5 (v4 minus one prompt paragraph about projection) loses 4.07 pp. The only useful
  question is what is left after the extra columns are dropped, which §3 answers.

---

## 3. Causal repair: the recovered upper bound

Grid, least invasive first, oracle-selected (so: **upper bound**):
drop the blanket `LIMIT 200001` → keep an order-preserving subset of the projected columns of
size = gold arity → DISTINCT on (dedupe) or off (re-execute with the top-level DISTINCT stripped)
→ finally, reported separately because it is strictly more oracle-ish, permute the kept columns.
Every candidate is re-executed and re-fingerprinted against the recorded gold digest.

```
v4        population (answered, full coverage, wrong) : 257
  RECOVERED:distinct_on            19       add DISTINCT / dedupe
  RECOVERED:distinct_off           12       the model's DISTINCT was the error
  RECOVERED:projection              7       drop the surplus columns
  RECOVERED:column_order            3       same columns, wrong element order
  RECOVERED:projection+distinct     1
  gold unstable                    10       no verdict possible
  semantic                        204
  -> pure output-shape recoverable : 42/257 = 16.3%
```

**EX ceiling if output shape were always right:**

| | v4 | v3-fold |
|---|---:|---:|
| measured EX | 0.6758 | 0.6640 |
| + all shape repairs | **0.7069 (+3.11 pp)** | **0.6980 (+3.40 pp)** |
| + comparator tolerance (below) | 0.7180 (+4.22 pp) | 0.7098 (+4.59 pp) |

A second, separately-labelled repair acts on the **comparator** rather than the SQL: re-fingerprint
both sides after rounding numerics to k significant digits, and after canonicalising anything that
parses as a date/time. Over the 204 semantic failures this recovers **15** more (v4) / **16**
(v3-fold): 4 date-spelling, 11 numeric precision. Those are questions where the engine computed the
right quantity and lost on `REAL` vs `numeric` rounding or on `1986` against `1986-07-13`.

**This bucket has shrunk since the earlier arm and that is expected.** `docs/failure-modes.md` §4.2
recovered 79/292 (27.1%) on run1 under prompt v2; v4's projection paragraph already harvested most
of the projection half, leaving 8 projection-type recoveries against run1's 52. What remains is
mostly DISTINCT, and DISTINCT is the one where the two error directions cancel (19 need it added,
12 need it removed) — a directional prompt rule is a coin flip and must be run as an arm, not
asserted.

---

## 4. Three things the corpus already handles

Before blaming the semantic layer, three probes ask whether the layer is silent where the engine
went wrong. All three come back negative, each with a control.

**Decoy avoidance.** The lake ships 162 decoy tables; **140 of them were licensed** to at least one
turn in this arm. Across all 1,344 statements the arm produced, **exactly one touches a decoy
table** (`train_7160`, `works_cycles.individual_contact`, a failure). The corpus documents each
decoy explicitly — `address.exclusion_zips`: *"Every column … is unreliable … Do not use this
table … Use avoid … instead."* For scale, WrenAI on the same 1,351 questions and the same database
reports `decoy_touch` on 66 — that figure comes from **its own grader on its own definition**, so
it bounds the contrast rather than making it exactly commensurable. This class is solved.

**Join declarations.** Of the failures whose gold needs a join, only **1.5% (3/201)** join a pair
the corpus does not declare — against **1.4% (9/643)** of correct answers. Lift ≈ 1.0. Inside the
`semantic:join` bucket, **20 of 20** gold join pairs are declared corpus join assets. Join errors
are not the corpus failing to state the join.

**Literal grounding.** Every `col = 'literal'` / `LIKE` in every prediction was executed against the
column it filters. Predictions containing a literal that matches **zero rows**: v4 1/257 failures
against 8/865 correct answers (lift 0.42); v3-fold 5/262 against 6/847 (lift 2.69). n is tiny in
both directions. The engine is not inventing values that do not exist — the corpus's column bodies
already carry the enumerations (`CBSA_type`: *"Exactly two values appear: 'Metro' … 'Micro'"*).

The one clean exception, and it is small: **literals that differ from gold only in case** — 7
failures against 1 correct answer on v4, 7 against 0 on v3-fold, lift 23.6. `= 'Horror'` against a
column holding `horror`. Postgres compares case-sensitively and the grader lowercases only *after*
execution, so this is a total loss. The corpus does state the casing where it was checked
(`movie_3.category.name`: *"Genre label, e.g. Action, Animation, … Horror"*); the engine did not
carry it into the predicate.

---

## 5. Hand audit of the semantic remainder

40 of the 204 semantic failures, drawn at random (seed 20260810), read case by case with the
question, the dataset's `evidence` hint, the gold and the prediction. Single rater, so the
intervals are wide and are stated.

| Coding | n/40 | % | 95% CI |
|---|---:|---:|---|
| **gold defect, total** | **28** | **70.0%** | [54.6%, 81.9%] |
|  · gold is broken, or contradicts the question or its own `evidence` | 20 | 50.0% | [35.2%, 64.8%] |
|  · gold projection convention (surrogate key instead of the label; extra or missing column) | 8 | 20.0% | [10.5%, 34.8%] |
| engine reasoning error | 8 | 20.0% | [10.5%, 34.8%] |
| question genuinely ambiguous, both readings defensible | 4 | 10.0% | [4.0%, 23.1%] |
| **corpus gap** — information the semantic layer lacked or misstated | **0** | **0.0%** | **[0.0%, 8.8%]** |

Two cases initially coded as corpus gaps were checked against `../BIRD-corpus` and reclassified,
because the corpus **did** carry the discriminator:

- `train_5342` (beer_factory): the engine read `standort.bundesland` for a sales state. The corpus
  says of that column, *"Every sampled value is 'CA' (California)"*, and of
  `wurzelbiermarke.bundesland`, *"For example 'CA', 'MA', or 'WA'"*. Prompt/engine, not corpus.
- `train_5694` (language_corpus): the engine filtered `mot = 'system'`. The corpus says the column
  holds *"Free-text word forms in Catalan"*. (The gold filters `'sistema'`, and `'system'` also
  exists in the table — this one is a gold defect as well.)

A mechanical, conservative check agrees with the direction: **10 of the 204** semantic failures have
a gold that returns the scalar `0` while the prediction returns a non-zero value — a gold whose
filter matches nothing. It replicates exactly on v3-fold (10 of 205). That is a floor on "gold is
broken", not an estimate; the audit's 50% is the estimate.

The rate is consistent with [Pervasive Annotation Errors Break Text-to-SQL
Benchmarks](https://arxiv.org/abs/2601.08778) (CIDR 2026), which measures 52.8% annotation error on
BIRD overall. It should be *higher* here, because we are conditioning on the engine having failed.

---

## 6. Judgement per category

| Category | v4 n | Verdict | Evidence |
|---|---:|---|---|
| wrong table set | 52 | **mostly irreducible**, 5 engine-fixable | 20 skipped a table, 17 added one (mostly to resolve a label the gold answers with a key), 10 same-schema, 5 cross-schema. Corpus documents the discriminator in every audited case; cross-schema use appears in **5/257 failures and 0/865 correct answers** and is a pooled-lake artifact (57 schemas in one database, `world` against `mondial_geo`). |
| wrong filter literal | 40 | **irreducible**, minus 7 prompt-fixable | Dominated by gold-vs-`evidence` contradictions (gold `>= '2012-01-01'` where the evidence says `> '2011'`). The one clean slice is case-sensitive literals: 7 failures, 1 control, lift 23.6. |
| wrong filter column | 38 | **irreducible / prompt-fixable** | Split between gold defects (a gold filtering the wrong column and returning 0) and the engine adding an unrequested predicate (`AND height > 0`). The second is prompt-shaped: "add no filter the question did not ask for." |
| wrong join | 20 | **not corpus-fixable** | 20/20 gold joins are declared corpus assets. Undeclared-join rate is identical on failures and correct answers (1.5% vs 1.4%). |
| wrong aggregate function | 20 | **irreducible** | Mostly `MAX(x)` against "the row that achieves `MAX(x)`" — a gold convention, not missing knowledge. |
| DISTINCT missing | 19 | **prompt-fixable, but only as a measured arm** | Lift 8.0 and causally proven. But 12 failures need DISTINCT *removed* and 72 correct answers carry a harmless spurious one, so a directional rule is not obviously net-positive. |
| DISTINCT spurious | 12 | **prompt-fixable, low ceiling** | Harmful in 10.3% of its 116 appearances. |
| shape differs and values differ | 14 | **irreducible** | Shape repair already failed on these; the underlying quantity is wrong too. |
| gold unstable | 10 | **irreducible, dataset defect** | Re-executing gold gives a different digest. Exactly 10 on both arms. |
| over-projection / column order | 11 | **prompt-fixable, mostly already harvested** | v4's projection paragraph is worth 4.07 pp (`failure-modes.md` §11); this is the residue. |
| no static difference | 8 | **irreducible / comparator** | `REAL` against `numeric`, `POWER(x,2)` against `x*x`. Comparator tolerance recovers 15 across the whole semantic remainder. |
| grain / aggregate argument / order-limit | 12 | **irreducible** | Small, and `aggregate_arg` has lift 0.23 — a non-signal. |

---

## 7. Worked examples

### Wrong table set — the engine answers with the label, the gold with the key

```sql
-- train_2886 (professional_basketball)  "Name the team in which the coach won 'NBA Coach of the Year' in 2010."
GOLD: SELECT DISTINCT T1.id_equipe FROM entraineurs T1 JOIN recompenses_entraineurs T2 …
PRED: SELECT T1.nom     FROM equipes T1 JOIN entraineurs T2 … JOIN recompenses_entraineurs T3 …
```
The question says *Name the team*; the gold returns the team id. The engine joined one extra table
to produce a name and was scored wrong for answering the question as asked.

```sql
-- train_8229 (mondial_geo)  "When did 'Bulgaria' gain independence?"
GOLD: SELECT T2.du_li FROM mondial_geo.guo_jia T1 JOIN mondial_geo.zheng_zhi T2 …
PRED: SELECT anio_independencia FROM world.pais WHERE nombre = 'Bulgaria'
```
Two schemas in the pooled lake hold country facts. The engine picked the other one. Not a retrieval
miss — both were licensed — a disambiguation miss inside the licensed set.

```sql
-- train_5342 (beer_factory)  "amount difference between bottles of root beer sold from Louisiana and Missouri"
GOLD: … JOIN wurzelbiermarke T2 …  WHERE T2.bundesland = 'LA' / 'MO'
PRED: … JOIN standort        T2 …  WHERE T2.bundesland = 'LA' / 'MO'   -- returns 0
```
Two tables carry `bundesland`. The corpus says every sampled value on `standort.bundesland` is
`'CA'`. The engine had the discriminator and did not use it.

### Wrong filter literal — the gold contradicts its own hint

```sql
-- 1060 (european_football_2)  "How many players were born after 1990?"
--   evidence: "born after 1990 refers to strftime('%Y', birthday) = '1990'"
GOLD: … WHERE TO_CHAR(fecha_nacimiento,'YYYY') > '1990'      -- 3028
PRED: … WHERE EXTRACT(YEAR FROM fecha_nacimiento) = 1990     -- 696
```
The hint says `= '1990'`, the gold uses `> '1990'`, and the question text supports the gold. The
engine followed the hint. Unwinnable without guessing which of the two to trust.

```sql
-- train_9249 (movie_3)  "Among the movies, what percentage are horror?"
GOLD: SUM(CASE WHEN T2.name = 'horror' …)     PRED: SUM(CASE WHEN T1.name = 'Horror' …)
```
Case. The stored value is `Horror`, so here the *gold* matches nothing. The engine was right and
lost. In the other direction — engine writes `'Horror'` where the data holds `horror` — the same
mechanism costs 7 questions on each arm at lift 23.6.

### Wrong filter column — the engine adding what nobody asked for

```sql
-- 729 (superhero)  "average height of the superheroes from Marvel Comics"
GOLD: SELECT AVG(gr_e_cm) …  WHERE verlagsname = 'Marvel Comics'
PRED: SELECT AVG(gr_e_cm) …  WHERE verlagsname = 'Marvel Comics' AND gr_e_cm > 0
```
A defensible data-cleaning instinct that the benchmark does not want. Prompt-shaped.

### Recovered by shape

```sql
-- train_4666 (disney)  "List all the songs associated with drama movies."   [DISTINCT missing]
GOLD: SELECT cancion … WHERE genero='Drama' GROUP BY cancion      -- 3 rows
PRED: SELECT T1.cancion … WHERE T2.genero='Drama'                 -- 4 rows, one duplicate
```

```sql
-- 1017 (formula_1)  "location coordinates of the circuits whose lap record is 1:29.488"  [DISTINCT spurious]
GOLD: SELECT T3.latitud, T3.longitud …                            -- 12 rows
PRED: SELECT DISTINCT c.latitud, c.longitud …                     -- 7 rows
```
The same keyword, both directions, in the same arm. That is why the rule cannot be written
directionally.

```sql
-- 989 (formula_1)  "Who is the champion of the Canadian Grand Prix in 2008? Indicate his finish time."  [over-projection]
GOLD: SELECT T1.hora …
PRED: SELECT T3.nombre, T3.apellido, T2.hora …
```
The question asks two things and the gold answers one. Dropping the two surplus columns
re-fingerprints to a match.

### Gold defects that no engine change reaches

```sql
-- train_5390 (sales)   Q: "…products purchased by customer called Adrian."   GOLD: … WHERE vorname = 'Adam'
-- train_7856 (world)   Q: "How many cities are in the Philippines?"          GOLD: … WHERE nombre = 'PHL'   -- city-name column; returns 0
-- train_6759 (retails) Q: "…customers in the building marketing segment…"    GOLD drops the c_mktsegment filter entirely
-- train_4803 (legislator)  GOLD hard-codes seven Twitter ids in a CASE expression
```

---

## 8. Where the remaining winnable accuracy is, and what would win it

Of the 438 v4 failures, 257 are answered with full table coverage — and inside those, **42 are
recoverable by getting the output shape right (an oracle-selected upper bound, +3.11 pp EX), a
further 15 by a more forgiving comparator, 10 have a gold that changes between executions, and the
remaining 204 are, by a 40-case hand audit, about 70% benchmark annotation defect, 20% genuine
engine reasoning error, 10% irreducible question ambiguity, and 0% corpus gap (95% upper bound
8.8%).** Three independent mechanical probes agree with that last figure and are the load-bearing
result of this analysis: the corpus declares 20 of 20 of the joins the engine got wrong; predicted
filter literals fail to match a row *less* often on failures than on correct answers (lift 0.42);
and the engine touches a decoy table once in 1,344 statements while an ungoverned comparison system
on the same questions touches one 66 times. The semantic layer is not the bottleneck any more — it
is doing the work it was built for, and the questions it still gets wrong are ones where it already
said the right thing and the model did not act on it. **So the roadmap should stop buying corpus
prose for accuracy.** What is actually left divides into three unequal pieces. The largest, ~2 pp,
is scoreboard-shaped: DISTINCT and projection discipline, worth running as a paired arm rather than
asserting, and honestly bookkept as scoreboard adaptation the way `failure-modes.md` §11 books the
v4 projection paragraph — it makes the number go up and would not help a real customer. The second,
maybe 0.5–1 pp and the only part that is real engineering, is a short list of narrow, testable
engine behaviours: case-exact literals (7 questions, lift 23.6), no unrequested predicates, and
disambiguating between adjacent schemas inside the licensed set (5 questions, and 0 of 865 correct
answers ever cross a schema boundary, so the signal is clean). The third and by far the biggest
piece — over half the remaining failures — is not winnable at all by changing this engine, because
the reference answer is wrong, contradicts its own hint, or answers a different question. The
honest consequence is that **EX on this benchmark is close to exhausted as a steering signal for
this system**, and the next measurement investment should go into an instrument that can tell
"the engine was wrong" apart from "the answer key was wrong" — because at 0.676 the arm is already
spending most of its remaining error budget on the second.

---

### Reproduction

Analysis scripts are scratch and not committed. Everything here recomputes from
`runs/eval/proxy_v4_corpus30872d3.jsonl`, `runs/eval/proxy_v3_fold_opus_high_corpus30872d3.jsonl`,
`../BIRD-corpus` @ `30872d3`, `../BIRD-Data-Obfuscation/eval_dataset/`
(`test_final.jsonl`, `order_sensitive_qids.json`, `trap_manifest.json`,
`trap_table_manifest.json`), and `PG_RENAME_DECOY_DSN` via
`governed_bi.datasource.postgres.PostgresConnector`. The population is
`outcome == "answered"` ∧ `table_coverage == full` (`eval.datalake.gold_tables`) ∧
`correct is False`; the control is the same with `correct is True`.
