# The oracle ladder: what a stage's failures actually cost

> **Provenance of the numbers on this page.** Every "last full benchmark" figure
> (45.8%, 135 of 2030, 61%, 73.7%, …) comes from the pre-2026-07-25 analysis runs
> and is **retired** along with the rest of that instrument's output — see
> [`plans/datalake-run.md`](plans/datalake-run.md#status). They are kept because the
> *arithmetic* they illustrate (multi-class errors do not sum; question-level churn
> swamps small deltas) is the point, and a worked example with real magnitudes
> teaches it better than one with invented ones. They are **not current results**,
> and `plans/experiment-runbook.md` is right that this repo has never run the full
> split with a model. Re-derive before quoting any of them.

The [error taxonomy](measurement.md#error-taxonomy-by-stage-and-by-class) says
*where* wrong answers come from. It cannot say what fixing a stage would buy,
and the arithmetic that looks like it can — adding up per-class counts — is
wrong. On the last benchmark 61% of wrong answers were wrong along more than
one dimension at once, so "203 questions have the wrong table" is not 203
recoverable questions: fix the tables and most of those queries are still
wrong about something else. Summing per-class counts over-counts every
multi-class query, and it is how one report arrived at "+46 points available"
and then revised it to "3–5" with nothing in between to justify either.

`src/governed_bi/eval/oracle.py` answers the question directly instead of
estimating it. Hand one stage the gold answer, leave every other stage alone,
re-serve the question through the ordinary agentic core, and measure. The EX
difference *is* that stage's headroom, with the interactions already priced
in, because the rest of the pipeline still has to do its job.

## Four rungs

`OracleRung` has four members, in increasing cost and decreasing realism:

- **`oracle_sql`** — skip the model; submit gold SQL straight to the grader.
  Costs nothing and answers a question nobody had been asking: what does the
  *grader* score gold at? Anything below 1.0 is a grading gap — a frozen
  constant, a stale hash, a normalisation quirk — and it is the true ceiling
  every other number should be read against. Run this rung first: a ceiling
  of 0.81 makes an EX of 0.44 a very different result from what it looks like
  measured against an assumed 1.0.
- **`oracle_schema`** — pin the corpus to the gold schema, so routing cannot
  miss. EX lift over the fair ladder is everything schema routing costs,
  including the part that leaks into SQL generation when a model writes
  plausible-looking SQL against the wrong tables.
- **`oracle_tables`** — restrict the corpus further, to only the tables gold
  actually uses. EX lift over `oracle_schema` bounds the headroom from the
  model never touching a non-gold table. Read the next section before quoting
  it: this rung changes more than one thing, and on at least one real corpus
  the thing it changed was not table selection.
- **`oracle_tables_padded`** — the control for the rung above. Same gold
  tables, padded with non-gold tables drawn deterministically from the same
  schema, so the prompt stays about the same size and only the *identity* of
  the tables differs. Nothing else in the ladder can separate "we gave it the
  right tables" from "we gave it a shorter prompt"; this can.

  It pads up to the **retrieval table budget** (`retrieve`'s `top_k`, 8 by
  default), not up to the schema's table count. That distinction is the whole
  arm: padding to the schema count reproduces the schema corpus exactly, and
  the rung silently becomes `oracle_schema`. Measured live at 11/11 tables and
  a byte-identical context before the target was corrected. On a schema small
  enough that even the budget covers everything, the control cannot work at
  all, and the row says so with `oracle_padding_degenerate`.

The three corpus-narrowing rungs — `oracle_schema`, `oracle_tables`,
`oracle_tables_padded` — are ordinary arms: same serve path, same guardrails,
same grader. The only thing that changes is the corpus handed to
`build_serve_rails()`, nothing about the model call is mocked, and the model
still has to write the query. That is what makes their lift honest: the EX is
not simulated, it is measured, on a deliberately narrowed problem.

`oracle_sql` is not one of those. It never enters the graph at all — no model
call, no retrieval, no guardrail layers, no agent loop. It hands gold SQL
straight to the grader, so it is a **probe of the grader**, not an arm of the
system, and it shares only the last step. That is exactly what makes it useful
(it isolates the grading contract from everything else) and exactly why its
number answers a different question from the other three. Nothing about it
tells you how the system behaves.

## Reading the lift

Read each rung against the one below it, not against zero:

- `oracle_sql − 1.0` is the grading gap. If it is nonzero, every other rung's
  EX (and the fair ladder's) should be read as a fraction of `oracle_sql`,
  not of a perfect score that does not exist for this grader.
- `oracle_schema − ceiling of the fair ladder's best arm` is what schema
  routing is costing, including its knock-on effect on generation.
- `oracle_tables − oracle_schema` bounds what the model never touching a
  non-gold table would buy. It is an upper bound on the taxonomy's
  `table_select` class, not an attribution — see below.
- `oracle_tables_padded − oracle_schema` is the part of that bound attributable
  to *which* tables, with prompt size held roughly fixed.

Because the narrowing is applied on top of an otherwise unchanged serve path,
the difference between two adjacent rungs already folds in the interactions
with everything downstream: the model still writes SQL against the narrowed
corpus and that SQL still goes through the whole pipeline. This is the property
a taxonomy count can never have — `error_taxonomy` tells you a class is present
on a row, not what removing it would do to the other classes on the same row.

## What `oracle_tables` does not isolate

The rung swaps a corpus, and everything downstream is a function of the corpus.
Measured over 103 questions of a real 5-schema eval corpus, moving from
`oracle_schema` to `oracle_tables` changes at least six things at once:

| | `oracle_schema` | `oracle_tables` |
|---|---:|---:|
| rendered context (median chars) | 7,899 | 4,079 |
| licensed tables (median) | 7 | 2 |
| join edges offered (median) | 8 | 2 |
| suspect-column caveats (median) | 18 | 8 |

The licensed-set collapse is not an information change but an *enforcement*
one: `allowed_tables` shrinks with it, so wrong-table SQL stops being unlikely
and starts being blocked. And the decisive number is not in that table — on
103 of 103 questions, `oracle_schema` had **already licensed every gold table**.
There was no retrieval-level table-selection error left for the rung to remove,
so on that corpus the entire lift was distractor removal, join hand-over and
prompt shrinkage.

So "table selection" names two different things, and the rung only touches one
of them:

1. **Retrieval and licensing choose a neighbourhood.** Recall was already 100%
   here. Nothing to fix.
2. **The model chooses tables within that neighbourhood.** This is where the
   error lives, and the rung removes it by making it impossible rather than by
   informing a better choice.

That still measures something worth knowing — it is the honest upper bound on
the `table_select` error class — but it is an enforcement bound, not a
retrieval one, and it is not attributable to a single mechanism.

Two things make it interpretable rather than merely suggestive. Every oracle row
records `oracle_gold_tables` (what gold actually reads) and, separately,
`oracle_offered_tables` (what the rung handed over — the same set under
`oracle_tables`, gold plus distractors under the padded rung). Compare
`oracle_gold_tables` against the `licensed_tables` a fair arm recorded for the
same question: if licensing already held every gold table, the rung removed no
selection error and its lift is something else. The two fields are kept apart
because collapsing them would make exactly that check wrong on exactly the arm it
matters for.

And `oracle_tables_padded` splits the remaining bound: if it scores like
`oracle_tables`, the effect is table identity; if it scores like
`oracle_schema`, the effect was prompt size all along. Skip any row where
`oracle_padding_degenerate` is true — the control collapses onto a neighbour at
both ends, onto `oracle_schema` when the budget covers the whole schema and onto
`oracle_tables` when gold already needs the whole budget, and neither says
anything about identity.

One implementation note that matters for reading the number. Few-shots are
filtered by table as well as by schema under a table-narrowed rung. Before that
filter, 73.7% of exemplars rendered into an `oracle_tables` prompt cited a table
the turn was blocked from using — the rung was showing the model gold SQL it
would then be punished for imitating, which depresses its EX for a reason that
has nothing to do with the stage it is meant to isolate.

## The hard rule: diagnostic, never a product number

Every rung is test-aware by construction. `restrict_corpus()` builds
the narrowed corpus from `gold_tables_for(gold_sql)` — the corpus is built
from the answer key, which is exactly the thing a production serve path never
has. Reporting an oracle rung's EX as system performance would be reporting a
number the system could not produce without already knowing the answer.

This is why `OracleRung` has no corresponding member in `eval.arms.Arm`:
`Arm` is the fair ladder (`baseline`, `curated`, `curated_sme`) that a real
serve configuration can run, and keeping the oracle rungs out of that enum is
what stops one of their numbers from being quoted as a fair-ladder result by
accident — there is no `Arm.oracle_schema` for a careless comparison table to
pick up. `tests/test_oracle_and_probes.py::test_oracle_rungs_are_not_members_of_the_fair_arm_ladder`
pins the two enums as disjoint. Every row an oracle rung produces also stamps
`oracle_rung` in its metadata, so a row from one of these runs cannot be
mistaken for an ordinary arm's row later, even outside the enum check.

## How a rung runs

`oracle_solver(rung, corpus, gateway, settings, identity, model=..., gold=...)`
returns a solver matching the same `MetaSolver` protocol (`solve_with_meta`)
the fair ladder's `agent_solver` implements, so it drives through the same
scoring code (`run_arm` / the pooled driver's per-arm loop) rather than a
parallel path that could silently diverge from it.

- **`GoldIndex`** looks gold up by question *text*, because the `MetaSolver`
  protocol is `solve(question)` with no id. That is safe only if the mapping
  from text to gold is unambiguous, and on this benchmark it nearly isn't:
  five questions appear in both the train and test splits with byte-identical
  text. `GoldIndex.build()` tolerates that (the five share gold SQL) but
  raises on a real collision — two questions with the same text and
  *different* gold — rather than silently picking one, because a silent pick
  would hand one question the other's answer and inflate the rung it was
  meant to measure.
- **`restrict_corpus()`** narrows a corpus to one schema and, for
  `oracle_tables`, to a specific table set. Dependent assets follow their
  tables: a join whose endpoints are not both kept describes an edge to
  nowhere, and a metric over a dropped table advertises a column the model
  cannot reach, so both are dropped with their tables. Terms, notes, and
  few-shots are kept whole — they are scoped by their own machinery, and
  trimming them here would change more than the one variable a rung is
  supposed to isolate. If the requested tables match nothing, the result is
  an empty corpus, not a silent fallback to the full one: falling back would
  make the rung measure nothing while still looking like it ran.
- **`oracle_solver()`** never calls the model for `oracle_sql` — it returns
  gold SQL directly. For the other three rungs it builds one governed-serve
  graph (`build_serve_rails`) per distinct narrowed corpus and caches it:
  `oracle_schema` needs at most one graph per schema in the lake, while
  `oracle_tables` needs roughly one per question, since gold table sets rarely
  repeat. Graph construction is small next to a model call, but it is not
  free, and that is the honest cost of the most informative rung.

## Running one

The pooled driver takes `--oracle`, repeatable as a comma-separated list:

```bash
uv run python -m governed_bi.eval.run_datalake --oracle oracle_sql,oracle_schema
```

An unrecognised rung name is an error, not a fallback — the same rule the
prompt registry follows, and for the same reason: a typo that silently runs
something else produces a number nobody can trace.

Rungs are appended *after* the fair arms, so a run that dies partway still has
its results scored; the diagnostics are what you lose. They also run serial
even under `--workers N`, because each rung rebuilds a graph per narrowed
corpus and cannot share the per-arm worker factory.

`run_experiment.py`, the single-schema driver, has no equivalent flag. One rung
would be degenerate there anyway: with a schema already pinned, `oracle_schema`
is the ordinary arm.

Every oracle row is stamped `oracle_rung`, so a number from one cannot be
mistaken later for a product metric.

## Routing is bypassed, not failed

Pinning the corpus to one schema leaves the router with a single candidate, so
it never engages and stamps no routing provenance. Read literally that is
`routed_hit=False` on every row of the rung, and the error taxonomy would then
charge every wrong answer to the picker. A rung whose entire purpose is to
remove routing error would report that routing is the whole problem. The first
live run did exactly this before the guard existed: `oracle_schema` came back
with `by_error_stage={'schema_pick': 5}`.

So a rung reports the schema it pinned as the routed schema, which is the honest
value (routing here is correct by construction), and sets `routing_bypassed` so
the bypass is visible rather than inferred from a suspiciously perfect
`routing_recall`. `attribute_row()` skips routing attribution on those rows.

The same guard covers a pool that holds only one schema for any other reason,
including `--limit-dbs 1` and a build that dropped every other database. Any row
whose `total_schemas` is 1 or less is treated as bypassed, because the router was
never asked a question there either.
