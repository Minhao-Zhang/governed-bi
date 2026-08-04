# Eval on the pooled data lake — 2026-08-04

What was measured, what it cost, and which numbers are not yet trustworthy. Written from
one session's runs; every figure here is reproducible from
`src/governed_bi/eval/datalake.py` against `corpora/gold-semantic-layer-20260804`.

## Setup

- **Engine:** the `v2` branch at `fb9e0bf` (the 57-question run predates the component fix
  and is marked as such below). One Postgres (`pg_rename_decoy`), 57 curated
  schemas pooled, no schema pin.
- **Questions:** `BIRD-Data-Obfuscation/eval_dataset/test_final.jsonl`, 1 351 of whose
  `db_id` the corpus carries. The other 13 schemas are uncurated and are **excluded**: a
  question whose schema is not in the corpus is a curation gap, and scoring it as a
  retrieval failure would report one as the other.
- **Gold:** `sql_rename` — the statement written against the obfuscated schemas. `sql_base`
  names the un-renamed originals and `sql_sqlite` is a different engine; either fails to
  execute here, and a gold statement that fails grades every prediction wrong.
- **Grading:** executed **result sets**, fingerprint-compared (`eval/grade.py`). Never SQL
  text — that would mark a correct answer wrong for choosing a different join order.
- **Model:** `gpt-5.6-luna` via the Responses API.

## Stage A — routing, free

A session with `agent_model=None` serves the stub answer path, so facets, routing,
retrieval, resolve and connect all run for real and no provider call is made. 1 351
questions in 69 s.

| | value |
| --- | --- |
| `recall@1` | **0.442** |
| `recall@3` (the shortlist) | **0.609** |
| `recall@5` | 0.702 |
| `recall@10` | 0.836 |
| `recall@20` | 0.905 |
| `recall@57` | 0.976 |
| gold schema never scored at all | 33 / 1 351 |
| median rank when scored | 2 |

> **Superseded — see [`retrieval-ceiling-2026-08-04.md`](retrieval-ceiling-2026-08-04.md).**
> This section concluded that schema routing is the dominant bottleneck. The numbers below
> are right and they measure the wrong stage. Licensing **every** schema
> (`route_top_n = 57`) moves the gold-table-coverage ceiling only from 0.444 to 0.561, so
> perfect routing is worth ≤12 pp. The binding constraint is *table-level* retrieval: a
> table whose summary shares no token with the question is not a candidate at any budget or
> candidate depth, and adding an embedder lifts the ceiling 6–9 pp for about a cent.

The gold schema is scored somewhere 97.6% of the time and first only 44.2%. The index here
is lexical only (BM25, no embedder).

Worst schemas at the shortlist (n ≥ 15): `car_retails` 0.16, `world_development_indicators`
0.33, `movie_platform` 0.35, `works_cycles` 0.35 (n=77), `student_club` 0.43,
`olympics` 0.43, `retails` 0.43.

### The finding that changed the engine

`connect_node` kept **one** connected component of the licensed tables. Over the same
1 351 questions that discarded **226 of the 823 shortlist hits — every one of them ranked
2nd or 3rd**:

| component rule | `reached_gold` |
| --- | --- |
| keep the component holding the top-ranked schema | 0.442 |
| keep the highest pass-two-scoring component | 0.417 |
| **connect each component, license every one that connects** | **0.608** |

`reached_gold` under the first rule is *exactly* `recall@1`, which is the signature of a
rule that only ever keeps the router's first guess. Scoring components instead was worse,
and that is the useful half: **picking is the thing that throws the candidates away**, so
no pick rule fixes it.

Licensing every connected component is sound rather than lax. `licensed` is a table
allowlist; a statement can only reach a table it names and `check()` refuses any it does
not. What `connect` guarantees is a *retrieval* property — that the prompt carries a join
path for the tables it offers — and that holds per component. A turn now declines only
when **no** component connects, which is what `missing_join_path` means.

`route_top_n` is consequently a real dial for the first time. Stratified sample, ≤6
questions per schema (n=342):

| `route_top_n` | `reached_gold` | mean licensed schemas |
| --- | --- | --- |
| 1 | 0.447 | 1.00 |
| 3 | 0.637 | 2.98 |
| 5 | 0.716 | 4.84 |
| 10 | 0.845 | 7.93 |

Reachability tracks the shortlist at every setting. Whether the *model* converts a wider
shortlist into more correct answers is a Stage B question and is **not** answered here —
raising the default on this table alone would be a guess.

## Stage B — end to end, paid

Two runs. The second is the one to read: it is three questions per schema and it ran after
the component fix above, so it is the current engine.

### 171 questions (≤3 per covered schema), `route_top_n=3`

| | value |
| --- | --- |
| EX | 12 / 171 = **0.070** |
| EX over attempted (excluding clarifications) | 12 / 138 = 0.087 |
| gold schema reachable | 100 / 171 = **0.585** |
| EX among reachable | 12 / 100 = **0.120** |
| answered / clarification / capped / refused / crashed | 131 / 33 / 6 / 1 / **0** |
| mean licensed schemas | 2.41 |
| tokens | 3 031 175 in / 69 803 out (17 726 in per question) |

**The result worth pulling out: the engine asks instead of guessing, and it does so
exactly when it should.**

| | clarification rate |
| --- | --- |
| gold schema **was** reachable | **0 / 100** |
| gold schema was **not** reachable | **33 / 71** |

Not one of the 100 turns that had the right schema asked for a clarification, and 46% of
the 71 that did not asked for one. That separation is not a tuned threshold — nothing in
the engine knows what the gold schema is. It is what a governed context does when the
tables it needs are absent: the model has nothing to write SQL against and says so. The
same property is why EX is a floor here rather than a verdict.

The funnel, in order:

```
171 questions
 → 100 gold schema reachable        (routing: recall@3)
 →  94 answered                     (5 hit the attempt cap, 1 refused)
 →  88 wrote SQL                    (6 answered without a query)
 →  12 correct                      (75 result_mismatch, 1 missing prediction)
```

So **75 of the 88 SQL-writing reachable turns produced the wrong result set** — that is
where the remaining loss is, and quantifying how much of it is BIRD-EX strictness rather
than wrong SQL is the highest-value unmeasured thing on this page.

Cost of the component fix: 17.7 k input tokens per question against 12.9 k at 1× before it,
and ≈17 s per question against 6.5 s. Licensing every connected component buys +19 pp of
reachability for roughly 1.4× the context and 2.6× the wall clock. Crashes went from 1 to 0.

### 57 questions (1 per covered schema), `route_top_n=3` — before the component fix

| | value |
| --- | --- |
| EX | 3 / 57 = **0.053** |
| gold schema reachable | 33 / 57 = 0.579 |
| EX among reachable | 3 / 33 = 0.091 |
| answered | 45 |
| asked for clarification | 10 |
| attempt cap reached | 1 |
| crashed | 1 (`agent_core/OpenAIContextOverflowError`) |
| answered with no SQL at all | 13 |
| observed spend | ≥ $0.04 · 736 128 input / 13 346 output tokens |
| wall clock | 371 s (6.5 s/question) |

**Cost is not the constraint.** ~$0.04 per 57 questions; the 171-question run spent 3.0 M
input tokens. The full 1 351 is on the order of **$1–3 and three to six hours**. Any
decision here can be made against a real run rather than a sample.

EX is a floor, not a verdict, for three reasons that are separable and are only partly
separated:

1. **Reachability caps it at 0.585.** Fixing routing is worth more than anything else on
   this list.
2. **Turns without the gold schema ask rather than answer** — 33 of 71 above. That is the
   engine behaving correctly on a wrong-schema context, and it is a routing symptom
   counted as an EX miss.
3. **BIRD's EX is strict in ways this engine loses on.** Sampled by hand: a prediction
   returning `Description, COUNT(*)` against a gold of bare `COUNT(*)` (over-answering);
   a gold applying `TO_CHAR(date, 'YYYY-MM-DD HH24:MI:SS') || '.0'` where the prediction
   returns the raw date; a gold selecting `id` where the question reads like it wants a
   name. These are documented BIRD characteristics, not engine defects, and the share is
   **not yet quantified** — doing so needs a hand audit of a labelled subsample.

**So: do not quote 0.070 as this engine's accuracy.** It is the accuracy of this engine
plus this router plus this grader on this sample, and the router is the term that dominates.

## Four measurement defects found before any of the above could be trusted

Recorded because each one made a number lie, and three of them lied in the direction that
looks like a finding.

1. **`generated_sql` was the model's raw tool argument, not what ran.** Canonicalisation
   rewrites identifiers to the corpus's declared spelling and quotes them (ADR 0008 D2)
   and `apply_row_limit` appends the cap. The ledger hashed the *executed* string, so one
   record carried the hash of one statement beside the text of another — and an eval that
   re-executes `generated_sql` fails on every mixed-case identifier the model happened to
   write unquoted, understating EX for 11% of the lake. Fixed:
   `AttemptRecord.executed_sql`.
2. **A turn paused on `ask_user` was scored as a crash.** `project_turn` defaulted to
   `"crashed"` when a turn had no `answer`, and a paused turn has none. 8 of 57 were
   reported as engine crashes with no stage and no exception class. `python -m
   governed_bi.serve` has exit code 4 for this distinction; the harness had none.
3. **The graded row carried neither `licensed` nor `usage`**, so a miss could not be
   attributed to retrieval versus generation and the run could not be priced. The first
   run reported `reached_gold 0/57` against a corpus measured at 0.608 — the number was
   absent, and absent read as zero.
4. **`_base_turn` fabricated `corpus_content_hash` as `f"corpus-{arm}"`**, so two runs over
   two *different* corpora compared equal — v1's `corpus_content_hash == "unknown"` with a
   friendlier spelling. `run_arm(session=...)` now takes every turn from `Session.turn`.

A fifth, in the measurement code rather than the engine: the first `routing_recall` called
the facet nodes and `route_node` by hand and merged their returns with `dict.update`,
which replaced the `facets` channel four times instead of merging it. It reported **0.000
recall with every gold schema "never scored"** — a plausible catastrophe. Assembling
state by hand is a second answer to how a turn runs.

## What to do next, in order

1. **Measure the embedder's lift on `recall@1`.** Free-ish (≈420 k embedding tokens for
   13 981 summaries, a few cents), and it moves the ceiling for every downstream number.
   Routing is a ranking problem here, which is what embeddings are for.
2. **Decide `route_top_n` against Stage B, not Stage A.** Reachability says 5 or 10;
   whether the model uses a wider context or drowns in it is unmeasured, and the context
   overflow already seen at 3 says the trade is real.
3. **Quantify the BIRD-EX artifact share** on a hand-labelled subsample — specifically on
   the 75 `result_mismatch` rows among reachable turns, which is where the loss now is.
   Without it EX cannot separate "wrong answer" from "differently-shaped right answer",
   and every intervention will be measured against a moving floor.
4. **Then** run the full 1 351 for a quotable baseline. It is ~$1 and ~2.4 hours; doing it
   before 1–3 buys a precise number about a configuration nobody will keep.
