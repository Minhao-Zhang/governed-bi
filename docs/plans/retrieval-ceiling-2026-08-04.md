# Where the EX ceiling actually is — 2026-08-04

A chain of **free** measurements (no model call: a session with `agent_model=None` serves the
stub path, so facets, routing, retrieval, resolve and connect all run for real) that locates
the binding constraint on execution accuracy. It **corrects** the conclusion in
[`datalake-eval-2026-08-04.md`](datalake-eval-2026-08-04.md), which named schema routing as
the dominant bottleneck.

Every figure below is over the same stratified sample: 171 questions, ≤3 per schema, from
`test_final.jsonl`, against `corpora/gold-semantic-layer-20260804`.

## The metric: gold-table coverage, not schema reachability

EX is bounded by something decided **before the model is called**: whether every table the
gold statement reads was licensed. `eval/datalake.table_coverage` measures it.

On the live `xhigh` arm at 514 rows, **after** the grader fix below:

| | |
| --- | --- |
| all gold tables licensed | **0.518** ← the ceiling |
| *schema* reachability | 0.625 ← overstates it by 11 pp |
| EX | **0.126** |
| conversion among answerable | **0.244** |

So roughly half the questions were **unanswerable under this retrieval**, and of the half that
were answerable the engine converted about one in four. A single EX number cannot say that,
which is why it should not be quoted alone.

### The grader was understating all of it by 2.4x

`eval/grade._normalise` put column **names** in the result fingerprint, so
`SELECT COUNT(*) AS paper_count` graded wrong against a gold of `SELECT COUNT(*)` with both
returning `100`. BIRD's own EX compares values; ours was stricter than the benchmark it
implements, and the penalty tracked how verbose the model was about aliasing rather than
whether it was right.

Found by classifying the 234 answerable-but-wrong turns (re-executing both statements, no
model), on a sample of 80:

| | share | reading |
| --- | --- | --- |
| extra columns, **gold values all present** | 33.8% | a human would accept it; BIRD does not |
| extra columns, gold values absent | 22.5% | answered a different question |
| same columns, different row count | 13.8% | genuine miss |
| prediction does not re-execute | 12.5% | **my classifier's bug** — see below |
| single cell, different value | 7.5% | genuine miss |
| same shape, values in a different order | 5.0% | `(url, 2028)` vs `(2028, url)` |
| single cell, **identical value** | 5.0% | pure grader artifact |

Re-scoring 514 rows with `tools/regrade.py`: **27 → 65 correct, 38 flips, none in the other
direction.** The fix is replayable at all because EX grades executed result sets rather than
SQL text — a grader change costs a database sweep, not a re-run.

The relaxation stops at names. An extra column still fails, because it makes a longer row
tuple, which is how BIRD catches over-answering; values swapped within a row still fail. Both
are pinned by tests.

**The 12.5% "prediction does not re-execute" row was my classifier's bug, not the engine's,
and it is worth writing down because it looked exactly like a defect.** All 14 were `capped`
turns. `generated_sql` means two things by design: on an *answered* turn it is the statement
the engine sent — canonicalised, quoted, row-limited — and on a refused or capped turn nothing
was sent, so it falls back to the last statement the model *proposed*. Those proposals are
unquoted mixed-case (`FROM authors.Paper`), which is precisely why they were refused. Replaying
them fails, correctly. `project_turn` already gates grading on `outcome == "answered"`, so no
score was ever affected; only my ad-hoc classifier ignored the gate. The register row for
`generated_sql` now states the dual meaning so the next reader does not repeat it.

That also explains the `capped` bucket: a capped turn is one where **all three attempts were
refused**, and the model was asking for tables it had not been licensed — the same
gold-table-coverage failure, seen from the governance side rather than the retrieval side.

Found by diagnosing the 9.7% of turns that hit the attempt cap: 23 of 24 had the gold schema
reachable, their executed SQL referenced tables that were *not* licensed, and one had exactly
8 licensed tables — the declared table budget.

## Four candidates, eliminated in order

### 1. Schema routing — **not** the constraint

| `route_top_n` | 1 | 3 | 5 | 10 | 20 | **57 (all)** |
| --- | --- | --- | --- | --- | --- | --- |
| all gold tables licensed | 0.357 | 0.444 | 0.462 | 0.515 | 0.567 | **0.561** |
| mean licensed tables | 6.3 | 13.7 | 16.6 | 20.3 | 23.3 | 25.2 |

With **every** schema shortlisted, routing is removed as a variable entirely — and the ceiling
is 0.561, statistically the same as at 20. **Perfect schema routing would move the ceiling
from 0.444 to at most ~0.56.** That is worth having and it is not where the problem is.

This is the correction. The earlier document measured schema `recall@k` (0.442 at 1, 0.609 at
3) and concluded routing dominates. Those numbers are right; they measure the wrong stage.

### 2. The table budget — **not** the constraint

`ASSET_REGISTER[table].budget` is 8. Patched in a measurement script (it is a declared
register constant, not a knob — measuring a value the register does not offer is how you find
out whether it should):

| table budget | 8 | 16 | 30 | 60 |
| --- | --- | --- | --- | --- |
| all gold tables licensed | 0.468 | 0.468 | 0.468 | 0.468 |
| mean licensed tables | 16.6 | 19.5 | 20.1 | 20.1 |

Flat. Raising the cap licenses more of the *same* tables and plateaus at ~20.

### 3. The candidate pool — **not** the constraint

`candidate_depth` is a real knob (50):

| candidate_depth | 50 | 200 | 800 |
| --- | --- | --- | --- |
| all gold tables licensed | 0.474 | 0.474 | 0.474 |
| mean licensed tables | 20.1 | 20.1 | 20.1 |

Flat, and identical at 16× the depth. Which pins the mechanism: `route` keeps only
`score > 0` and pass two keeps only `float(sc) > 0.0`, so **a table whose summary shares no
token with the question is not a candidate at any depth.** Under obfuscation that is the
common case — an English question about root beer brands has zero lexical overlap with
`wurzelbiermarke`, `zu_zhi` or `guo_jia`, and the corpus's summaries are largely mechanical
(`"Airlines (Airlines): FL_DATE, OP_CARRIER_AIRLINE_ID, …"`).

### 4. The retrieval channel — **this is it**

Same 171 questions, same budgets, same depths. The only change is an `OpenAIEmbedder` on the
index, so the semantic channel scores:

| `route_top_n` | lexical only | with embedder | lift |
| --- | --- | --- | --- |
| 3 | 0.444 | **0.503** | +5.9 pp |
| 5 | 0.462 | **0.544** | +8.2 pp |
| 10 | 0.515 | **0.608** | +9.3 pp |

Two things to notice. Embeddings at `top_n = 10` (0.608) beat **lexical at any width**,
including all 57 schemas (0.561). And they do it with *fewer* tables licensed — 12.9 against
13.7 at `top_n = 3` — so the gain is better targeting, not a wider net that happens to catch
more.

Cost: about **420 k embedding tokens** for 13 981 summaries, roughly **$0.01**.

## What this changes

1. **Build the index with an embedder for any run whose number will be quoted.** It is the
   cheapest intervention available and it moves the ceiling more than any knob. The paid arm
   currently in flight is lexical-only, which makes its EX a floor under a floor.
2. **Stop treating schema routing as the headline problem.** Perfect routing buys ≤12 pp of
   ceiling; the retrieval channel buys 6–9 pp for a cent and compounds with it.
3. **`route_top_n` is worth raising *with* embeddings**, not instead of them — 0.608 at 10
   against 0.503 at 3. The cost is context width, which the live arm should measure rather
   than assume; one `OpenAIContextOverflowError` has already been seen at `top_n = 3`.
4. **The table budget and `candidate_depth` are not levers here.** Both are flat. Leave them.

## What is still not measured

- **EX with embeddings.** Everything above is the *ceiling*, not the score. Raising the
  ceiling is necessary, not sufficient — the current arm converts 24% of answerable
  questions, and nothing here says an embedder changes that conversion rate.
- **Whether the remaining ~39% is reachable at all.** At `top_n = 10` with embeddings, 24% of
  questions have *no* gold table licensed. Whether those are curation gaps (a summary that
  describes nothing), genuinely hard questions, or a ranking failure is unknown.

