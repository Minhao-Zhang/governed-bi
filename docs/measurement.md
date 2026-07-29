# What we measure, and where a failure shows up

A three-arm data-lake run once had to be discarded because the harness could
say a turn failed but not *where*. A solver crash and a deliberate refusal
both arrived as `error="refusal"`, so `refusal_rate` absorbed the crash count
and EX absorbed the loss — by a different amount per arm, since the arms did
not crash equally. A `NameError` in a serve-path tool helper surfaced only as
`refused_by="model_error"`, indistinguishable from a model hiccup. This doc is
what to read when a number looks wrong: where the measurement lives, and which
file localises which kind of failure. The retirement itself is recorded in
[`plans/datalake-run.md`](plans/datalake-run.md).

> **Provenance of the numbers on this page.** Every "last full benchmark" figure
> (45.8%, 135 of 2030, 61%, 73.7%, …) comes from the pre-2026-07-25 analysis runs
> and is **retired** along with the rest of that instrument's output — see
> [`plans/datalake-run.md`](plans/datalake-run.md#status). They are kept because the
> *arithmetic* they illustrate (multi-class errors do not sum; question-level churn
> swamps small deltas) is the point, and a worked example with real magnitudes
> teaches it better than one with invented ones. They are **not current results**,
> and `plans/experiment-runbook.md` is right that this repo has never run the full
> split with a model. Re-derive before quoting any of them.

## Two axes, kept apart on purpose

`src/governed_bi/stages.py` is the shared vocabulary both the serve path and
the eval harness import. It holds text and pure functions only — no I/O, no
settings, no model — so a change to it cannot silently diverge between build
and eval.

**`Outcome`** — what happened to the turn: `answered`, `refused`,
`clarification`, `capped`, `crashed`. `crashed` is the member the old harness
could not express, which is why the run above had to go: a crash is a bug in us,
a refusal is the product working, and a metric that adds them together is
measuring two things and reporting one.

**`Stage`** — where in the pipeline it happened: the graph's own rails
(`route`, `refuse_gate`, `assemble`, `agent_core`, `narrate`,
`finalize`) plus the sub-stages inside `assemble` (`shortlist`, `schema_pick`,
`retrieve`, `license`) and `agent_core` (`search_corpus`, `inspect_schema`,
`sample_rows`, `guardrail`, `execute`, `repair`). A schema-pick miss and a
guardrail block both read as "assemble/agent_core failed" to the graph;
telling them apart is the difference between fixing retrieval and fixing
generation.

`classify_outcome()` decides both from a turn's raw signals, in this order:
an `exception` always wins (a turn that raised did not refuse, whatever else
its metadata claims); then `generated_sql` present means `answered`; then
`refused_by` is looked up in `REFUSED_BY_TO_STAGE`. `refused_by="model_error"`
maps to `Outcome.crashed`, `Stage.agent_core` — the serve path stamps that
value when it catches its own internal exception and degrades to a refusal so
the turn fails closed. Failing closed is correct; scoring it as a refusal is
not. `refused_by="exhausted"` (the repair-loop cap) maps to `Outcome.capped`,
not `refused` — the loop didn't decline, it ran out of attempts.
`refused_by` is free text with no central declaration, so a value absent from
`REFUSED_BY_TO_STAGE` is counted honestly rather than silently bucketed: the
third element `classify_outcome()` returns is `False` in that case, and every
row-level caller (`_grade_one` in `run_datalake.py`) prints a warning and rolls
it into `n_unmapped_refused_by` instead of guessing a stage. `classify_row()`
is the same classification applied to a scored row, preferring a stamped
`outcome`/`failed_stage` when a row already carries one so a row scored by a
newer classifier is never silently re-derived by an older one.

Gradeability is deliberately not a third value here. Whether a gold hash was
usable to compare against is orthogonal to outcome: a question with no gold hash
was still answered or still refused, and folding that into the outcome
vocabulary is how a grading gap starts reading as a model failure.
`error="missing_gold_hash"` / `"gold_unusable:..."` on an otherwise-answered
row still classifies as `Outcome.answered` — see
`tests/test_stages.py::test_grader_gradeability_errors_are_not_crashes`.

## Instrumentation: what gets recorded, per turn

`StageRecorder` (`src/governed_bi/analyst/governance.py`) is a per-turn
accumulator: one instance per turn, owned by the turn's `GovEventStream` so both
reset at the same boundary. That ownership is a correctness requirement, not a
preference. The eval harness serves several graphs at once when
`serve_workers > 1`, and a module-global accumulator would interleave two turns
into one unreadable record.

- `stage(name, **detail)` times one stage as a context manager. On an
  exception the record is stamped `status="error"` and the exception is
  **re-raised** — swallowing it would leave a stage that died looking like one
  that never ran.
- `skipped(name, **detail)` records a stage that deliberately did not run (no
  LLM schema pick on a single-schema corpus). `ms` is `None`, not `0`: a stage
  that never ran did not take zero milliseconds, and an absent record would
  read as a stage this build cannot measure at all.
- `count_tool_call(name)` counts every tool invocation by name, independent of
  the governance ledger (which only records `run_query`/`sample_rows` —
  widening it would widen what claims to be governed). Before this,
  `search_corpus`/`inspect_schema` — most of a turn's tool calls — left no
  durable trace anywhere.
- `guardrail_layer(layer, passed)` counts one guardrail layer's decision. A
  layer that ran and blocked nothing is keyed at `0`; a layer that never ran
  this turn (L4 term-semantics is skipped with no retrieval scope) is
  **absent**. Collapsing those two would report a confident zero for a layer
  nobody executed.
- `provenance()` returns `{"stage_events": [...], "n_tool_calls": {...},
  "by_guardrail_layer": {...}}` — the three keys the rest of this chain reads
  by name.

The guardrail-layer counter is fed by an observer passed into
`gateway.guardrails.check(..., on_layer=...)`. It is observation-only by
construction: nothing in `_observe()` can influence a verdict, and an observer
that raises is swallowed — a governance regression there would be far worse
than a missing metric. It warns once per process (not once per query) if the
observer ever raises, because a counter that dies silently on the first call
would otherwise leave the layer histogram permanently empty while every run
looked healthy — the same shape of failure this instrumentation exists to
end. See `tests/test_stage_metrics.py`'s `check()`-observation tests and
`tests/test_stage_metrics_seam.py` for the end-to-end contract (one real
turn, no stubbed provenance — a key rename anywhere in the chain fails there).

`GovEventStream.final()` stamps the recorder's `provenance()` onto the
answer *before* the portable append, so the durable run log carries a turn's
own timings and counters too, not only the eval harness. Which provenance
keys must survive onto every terminal `Answer` is pinned in
`METADATA_PROVENANCE_KEYS` (`src/governed_bi/analyst/run_log.py`), which
includes `stage_events`, `n_tool_calls`, `by_guardrail_layer`,
`attempts` (the `_INSTRUMENTATION_KEYS` block) alongside the run-identity
fields (`turn_id`, `run_id`, `outcome`, `model`, ...). An unmeasured
instrumentation field is written as `None`, never `0` or `{}` — a producer
that measured nothing must say so, and an absent key would be indistinguishable
from a metric this build cannot record. `strip_stage_events_for_log()` keeps
the numeric shape of `stage_events` (`stage`/`status`/`ms`) but drops every
string-valued `detail` key before the durable write: `detail` is free-form at
the source, so the durable projection cannot trust it by key name — a later
`detail["query"]` would otherwise put the user's own words into a
metadata-only log (ADR 0004 H11 Tier A).

## From one question, to one run, to the ledger

**Per question.** Every scored row in `generations.<arm>.jsonl` carries
`outcome`, `failed_stage`, `refused_by`, `n_tool_calls`, `by_guardrail_layer`
(computed by `_grade_one` in `eval/run_datalake.py`, which classifies the row
at the one point a solver exception is still distinguishable from every other
kind of `error` string). The same turn's per-stage timings are flattened by
`_stage_event_rows()` into `<run_dir>/stage_events.jsonl` — one JSON line per
stage per question, tagged with `question_id`/`arm`/`db_id`. **A row replayed
on `--resume` contributes no `stage_events` rows**: it has no fresh timings,
and synthesising one — or copying the row's total latency onto a stage — would
put a fabricated number in the one file whose purpose is attributing time. So
on a resumed run `stage_events.jsonl` is a subset of `generations.<arm>.jsonl`,
joinable by `(question_id, arm)`, and a question re-served after a torn write
can appear there more than once — the row file stays the authority on what was
actually scored.

**Per run.** `_summarise_rows()` (`run_datalake.py`) aggregates from the
**rows on disk**, not from in-flight results, which is what lets a resumed run
summarise identically to an uninterrupted one — replayed and freshly-scored
rows go through exactly this function. `run_experiment.py`'s `ArmSummary`
aggregates the single-db ladder driver's rows the same way and now carries
the same crash fields — this parity is recent: an adversarial review found the
single-db driver still scoring a crash as a refusal, the exact defect that
forced the pooled-driver retirement above, just sitting in the other driver.
Both now write `by_outcome` (the complete outcome partition, so
`n_answered`/`n_refused`/`n_crashed` can be checked against `n`),
`by_failed_stage` (a bucket appears only when something actually observed it),
`n_unmapped_refused_by`, and `crash_rate` (separate from `refusal_rate`, which
is now genuine refusals only). Both drivers stamp a crash as
`f"{type(err).__name__}: {err}"`, never bare `str(err)`: `str(KeyError("schema"))`
is just `'schema'`, naming neither the failure kind nor the frame that raised
it, and this string is the only surviving record of what happened.

**Every rate is `None`, never `0.0`, when its denominator is empty** —
`ex_lenient`, `ex_strict`, `ex_gradeable`, `refusal_rate`, `crash_rate`,
`routing_recall`, `cond_ex_given_routing`, `decoy_touch_rate`,
`conditional_ex_lenient` and `share_with_a_note` all follow this rule now. An arm
that scored zero rows measured nothing; `0.0` would read as "measured everything
and got none of it right" instead, and the run ledger's quotability check keys on
exactly this distinction (an arm whose `crash_rate` was never recorded — `None` —
has not shown it was crash-free, and is treated as not-quotable; see Per project
below). `share_with_a_note` counts over the rows that actually recorded note
injection rather than over every row, so a run predating the field reports `None`
instead of the delivery failure `0.0` would claim.

`eval/analysis.py`'s `gradeable_report()` follows the same rule for the three
names it shares with the summary (`ex_lenient`, `ex_gradeable`,
`decoy_touch_rate`), and the same frozen **plus order-sensitive** exclusion for
`ex_gradeable` / `n_gradeable` via `leakage.is_gradeable_eval_row`. They appear in
both `summary.json` and `analysis.json`, so a difference in what they mean at the
edges would make two files disagree about one run.

One consequence worth knowing, because it bit once: anything that formats these
for a console has to tolerate `None`. `run_datalake`'s per-arm progress line goes
through `_fmt_rate()`, which renders `n/a`. Applying a format spec directly would
raise `TypeError` after the whole serve loop and before `summary.json` is written.

Two further corrections landed in `_summarise_rows()` alongside the
crash/refusal split, both the same shape as it: one metric must not silently
absorb another's failure.

- **`routing_recall`'s denominator excludes crashed turns and bypassed ones.**
  A crashed turn returns no meta at all, so its row records
  `routed_hit=False` whether or not the router ever ran; counting it charged
  the crash to the router. A *bypassed* turn is the mirror image: when the
  corpus holds one schema there is no routing decision to score, and the serve
  path says so (`routing_bypassed`). Counting those as misses reported `0.0`
  recall for a pool with nothing to route; counting them as hits reports `1.0`,
  which credits a router that never ran. They are excluded, so the metric reads
  `None` (not measured) rather than either lie, and `n_routing_bypassed` says how
  many rows that was.

  A third shape is a turn that recorded no routing decision *at all* — it ended
  before `assemble` ran. `routed_hit` is `None` there, not `False`, and the
  denominator is defined on positive evidence (a recorded decision) rather than on
  the absence of a bypass flag. `n_routing_unrecorded` counts the exclusion. Without
  this the whole-split `--skip-agent` ceiling published `routing_recall: 0.0` over
  every row, with `n_routing_bypassed: 0` beside it, for a router never invoked.

  Two situations produce a bypassed turn, and the code treats them identically
  because measurement-wise they *are* identical — no routing decision was made, so
  there is nothing to score. A single-schema pool (`--limit-dbs 1`) bypasses because
  only one schema exists; an oracle rung bypasses because `restrict_corpus` pinned
  the gold schema for it. Only the stakes of getting it wrong differ: on the pool a
  `1.0` would be merely vacuous, while on the rung it would be the rung taking credit
  for a schema it was handed.
- **`cond_ex_given_routing` takes both terms from routed rows.** It used
  to divide every correct row (routed or not) by only the routed ones, which
  could read above `1.0` the instant a question was answered correctly off a
  schema the router had missed. Correct answers therefore partition **five** ways,
  one per population excluded from the routing denominator:

  ```
  n_correct == n_correct_routed
             + n_correct_unrouted
             + n_correct_bypassed
             + n_correct_routing_unrecorded
             + n_correct_routing_crashed
  ```

  All five are needed, all five are counted directly rather than by subtraction, and
  `n_correct_unaccounted` publishes the residual so a sixth exclusion shows up as a
  number rather than as a silently wrong bucket. The three-term form stated here
  previously was already failing: a correct answer on a turn that recorded no routing
  decision fell outside all three. `EX` is computed over *every* row while the routing
  terms are not, so once bypassed rows exist the identity
  `EX == routing_recall × cond_ex_given_routing` stops holding while
  `n_correct_unrouted` — the field that used to be the escape hatch — still
  reads `0`. `n_correct_bypassed` is the missing term.
- **The router is not a gate, so the identity is not expected to hold in the first
  place.** `n_correct_unrouted` was described as "normally 0"; structurally it is not.
  `agent_core_node` builds the agent with the **pooled** corpus, not the routed
  `retrieval_corpus` that `assemble` used — so the agent's `search_corpus` tool retrieves
  across every schema regardless of what the router selected. Verified directly: with the
  router selecting only `address`, retrieval over the pooled corpus returns tables from
  `airline`, while the same query over the routed corpus returns nothing.

  That is arguably good for EX — the agent can recover from a routing miss rather than
  refusing — but it means the routing terms describe a *ranking step the answer is free to
  ignore*, not a filter it must pass. So `routing_recall` and `cond_ex_given_routing` are
  two useful measurements rather than two factors of EX, and a delta that moves only one of
  them does not localise where an arm helped in the way the pair suggests.

  How often it happens is now measured rather than assumed: `routing_escape_rate` (over
  `n_routing_escape_observed`) and `n_correct_via_routing_escape` — correct answers that
  used a schema the router had excluded, which are wins the router did not enable.

  The verdict is computed from `tables_used`, the tables parsed out of the SQL that was
  actually delivered, resolved to schemas through the arm's own corpus (asset ids look like
  `tbl_<schema>_<name>`, but schema names contain underscores, so splitting the string
  guesses wrong). **Not** from `licensed_tables`: that is the assemble-time seed license,
  computed from the *routed* corpus and never amended, so it cannot contain an
  out-of-routed schema however far the agent went. A first version of this metric used it
  and therefore scored a demonstrated escape — `search_corpus`, then `inspect_schema`
  licensing an out-of-routed table, then the guardrail passing it — as compliant. There are
  zero cross-schema `JoinAsset`s across every corpus built so far, so that version could
  only ever return `False` or `None`.

  Asset ids that do not resolve are no longer dropped silently. Each row records
  `tables_used_unresolved` / `n_tables_used_unresolved`. Contract: if any *resolved*
  schema is outside the routed set, `routing_escaped` is `True` (known escape). If
  unresolved ids remain and no resolved escape is proven, `routing_escaped` is `None`
  with `routing_escape_unknown=True` — unknown, not "unobserved / nothing to judge".
  Genuinely empty or missing `tables_used` stays unobserved (`routing_escaped=None`
  without the unknown flag). The escape **rate** denominator
  (`n_routing_escape_observed`) counts only definitive True/False rows;
  `n_routing_escape_unknown` sits beside it so undercount is visible.

  A turn that produced no SQL used no tables, and is left unmeasured rather than counted as
  compliant: it did not stay inside the routed set, it did not go anywhere. Note this is a
  narrower exclusion than "refusals are excluded" — a refusal that got as far as generating
  and then had its query blocked *does* have tables to judge, and is judged.

  If `routing_escape_rate` comes back high, read the routing half of the ladder with that
  in mind: an arm can improve `routing_recall` without improving EX, because the agent was
  already reaching past the router, and it can improve EX without improving
  `routing_recall` for the same reason.

`_summarise_rows()` also writes `tool_calls` and `by_guardrail_layer` (summed
across rows), `n_with_difficulty` (BIRD leaves ~85% of rows with no difficulty
label, so `by_difficulty` collapses into one `"unknown"` bucket; without this
count that reads as a uniform distribution instead of an empty measurement),
`n_gold_unusable` (a gold hash existed but the grading artifact recorded it as
unusable — those rows still score `correct=False` and sit in every EX
denominator, so without the count the understatement is nameless), and, at
the run level rather than per arm, `decoy_manifest_missing_dbs` in
`summary.json` (a db with no trap manifest loaded cannot produce a meaningful
decoy-touch rate; naming it keeps a `0.0` there from reading as "clean" when
it means "untested" — datalake-only: `run_experiment.py` loads trap columns
for its one db the same way but does not track or report manifest presence).
All of this lands in `summary.json`.

**Per project.** `runs/index.jsonl` (`src/governed_bi/eval/index.py`) is a
flat ledger, one record per run, appended automatically at the end of
`run_datalake()` and available standalone via
`uv run python -m governed_bi.eval.index --add runs/datalake/<ts>`. Every
record answers two questions that used to live only in someone's memory:

- **`quotable` / `ledger_ok` / `hygiene_ok`** — is this run's *artifact hygiene*
  good enough to consider quoting? A run is *not* ledger-ok if any arm crashed
  (`crash_rate` truthy), if any arm's `crash_rate` was never recorded at all, if a
  db failed to build, if a curator build error was swallowed, if the run scored the
  `train` split (the curator read those questions, so it is a diagnostic, not a
  result), or if `n_questions` is below the Holm arithmetic floor for the arm
  family on the record (`arithmetic_floor_for_arms`; default four-arm floor is 8,
  five arms need 9). The unrecorded-crash-rate case fails closed on purpose: a run
  predating the crash/refusal split has not shown that it had no crashes, because
  its `refusal_rate` and EX would have absorbed them either way.
  `not_quotable_because` / `not_ledger_ok_because` names every reason, not just the
  first. **`quotable: true` is not "publishable."** Statistical claim readiness
  (replicate, MDE, Holm, cluster, single-variable, twin) lives in the
  experiment-runbook checklist; the ledger always sets `claim_ready: false` and
  lists `claim_ready_requires` rather than pretending to evaluate those conditions.
- **`comparable(a, b)`** — may two runs be put in the same sentence? Only if
  `split`, `model`, `llm_temperature`, `prompt_set_hash`, `corpus_content_hash`,
  `route_top_k`, `route_llm_pick`, `schema_pick_max_columns` and `use_embedder`
  all match. That list is **derived** from `MANIFEST_KNOBS` minus a documented
  `COMPARABILITY_EXCLUSIONS`, not spelled out a second time, so a knob added to
  the register joins the gate by default — it was spelled out separately once,
  and `llm_temperature` was simply missing from it, so two runs decoded at
  different temperatures compared as the same experiment. `git_sha` and
  `skip_agent` are excluded here and checked as resume drift instead: two runs
  at different commits are the normal case, but a commit changing *within* one
  run directory corrupts that run.

  A knob absent on both sides counts as matching (two runs that both predate a
  knob did not differ in it) — which is sound only because every knob is now
  guaranteed present, so `manifest_schema_version` must be on both records or
  the pair is refused outright rather than silently read as agreement. Comparing
  across a changed knob without noticing is the specific mistake that produced
  the retired numbers.

Neither check blocks a run. What they do is put the reasons in the artifact and
in the rendered table, so quoting a number means reading past them first.
`tests/test_eval_index.py` pins both rules against the two mistakes that
already cost a set of results.

## The conditional metrics are observational, not causal

`SUMMARY_CONDITIONALS` (`src/governed_bi/eval/metrics.py`) adds six splits that
condition an outcome on something the turn itself recorded:
`ex_by_semantic_assurance`, `ex_by_tier`, `ex_by_note_injected`, `ex_by_repair`,
`decoy_touch_by_caveat`, and `guardrail_cost_ceiling`. They exist because the
summary previously reported governance signals and EX side by side without ever
crossing them, so nothing said whether the signals tracked correctness. Field
definitions are in [Eval metrics](eval-metrics.md).

**None of them measures an effect.** Every conditioning variable is downstream of
the treatment, so a difference between strata is post-treatment selection, not a
causal contrast:

- **`ex_by_semantic_assurance`, `ex_by_tier`** — the split is on an *output of the
  system*. Read *within* one arm this is calibration and answers a real question
  (if `unflagged` does not out-score `heuristic`, the stamp is decoration). Read
  *across* arms it is invalid: the arms do not stamp the same questions, so the
  strata are not the same population.
- **`ex_by_note_injected`** — a note injects when retrieval matched, i.e. on the
  questions the corpus already covers. The split therefore measures **coverage**,
  not what a note is worth. The counterfactual it looks like it answers — the same
  question with the note withheld — is only available from a prompt-variant or
  ablation arm.
- **`ex_by_repair`** — the "with repair" stratum is, by construction, the questions
  that already failed once. It compares two different difficulty populations, and
  the repair stratum scoring lower is the expected result, not evidence that repair
  hurts.
- **`decoy_touch_by_caveat`** — same shape: a caveat is injected when a suspect
  column is in scope, so the two strata are different questions.
- **`guardrail_cost_ceiling`** — a **bound**, not a cost. Blocked SQL cannot be
  graded without executing un-guardrailed SQL, so this counts turns where a layer
  blocked *and* the turn ended wrong. The true cost is at most this and may be zero.

## `eval/analysis.py`: attribution after the run

`analyse_run()` (`src/governed_bi/eval/analysis.py`) reads
`generations.<arm>.jsonl` after a run and computes what the run itself does
not. The corrections below landed alongside the taxonomy work:

- **`incomplete_arms` was inverted.** It used to flag the *complete* arm
  relative to the shortest one. It now compares every arm's question-id set
  against the **union** of all arms' ids, so an arm reads as incomplete
  exactly when it is missing a question another arm scored — which also
  catches two equally-sized arms that cover different questions, a case a
  max-length rule would miss.
- **The split is never guessed.** `analyse_run()` raises rather than picking a
  gold file for rows that predate the `split` field, because the wrong gold
  file would make every question unmatchable — which used to read as a clean
  "no gold to compare" instead of the actual defect. Pass `--split` explicitly
  for a legacy run.
- **Refusals are excluded from table-selection attribution.** A row with no
  `generated_sql` made no table selection at all; counting its empty table set
  as "every gold table absent" would manufacture a table-selection failure out
  of a refusal. `table_selection_report()` now buckets those rows into
  `n_no_sql` and drops them from the comparison, and `table_mismatch_rate` /
  `mean_table_recall` / `mean_table_precision` are `None` — not `0.0` — when
  nothing was comparable, because a zero mismatch rate over zero comparisons
  reads as "the tables were fine," the opposite of what happened.
- **Its pairwise tests are corrected, and say what they bundle.** `analyse_run()`
  pairs every arm with every other, so four arms is six tests and six uncorrected
  tests at α=.05 carry a ~26% chance of at least one false positive. It reports
  `p_value_holm` and `n_family` alongside the raw `p_value`, over the pairs that
  actually produced one — an errored pair is left out of the family, because
  tightening the others on behalf of a test that never ran spends significance for
  nothing. Each pair also carries `single_variable`, and when compound the
  `bundles` list, from the same `arms.skipped_rungs` the driver uses: a pair can be
  consecutive among the arms that ran and *still* bundle, which is exactly the case
  of `curated → curated_sme` changing two mechanisms at once.
  What this report cannot do is state resolution — it has no replicate arm — so it
  carries `mcnemar_caveats.no_noise_floor` pointing at `summary.json` rather than
  letting a small p-value imply the run could resolve the delta behind it.

## Error taxonomy: by stage and by class

`stages.py`'s `Outcome`/`Stage` vocabulary above only reaches turns that
refused, capped, or crashed. That leaves the largest population in a
benchmark run unattributed: turns that ran cleanly, produced SQL, executed
it, and returned the wrong rows. On the last full benchmark those were 45.8%
of every question, and they all landed in one bucket called "right schema,
wrong SQL" — a bucket too big to act on, which is why an earlier estimate of
what fixing it was worth ranged over an order of magnitude.
`src/governed_bi/eval/sql_diff.py` and `src/governed_bi/eval/error_taxonomy.py`
split that bucket two ways, both computed by diffing `generated_sql` against
gold — no database, no model, and re-runnable over an archived
`generations.*.jsonl` as easily as over a live row.

**By stage**, attribution is a strict cascade from the outside in, which is
what makes the buckets mutually exclusive and therefore summable: each wrong
answer is charged to exactly one stage, the outermost thing that went wrong.
In order: `embedding_wall` → `wrong_schema` → `execution_error` →
`unparseable_sql` → `wrong_table` → the SQL-construction classes (join graph,
join keys, join type, aggregation, group by, filter columns, filter literals,
projection, projection order, distinct, order/limit, set ops) → `value_level`.
`attribute_row()` walks this list and stops at the first class present; a
question that reached the wrong schema is a routing failure whatever else is
wrong with its SQL, because the SQL was written against tables the model
should never have seen — scoring it as a join bug would blame generation for
a mistake generation had no chance to avoid. `execution_error` is charged when
the statement parsed but raised under the grader (type error, unknown column,
division by zero): it never returned rows, so no structural dimension can be
compared, and it must not fold into `value_level`. Only once the schema is right
does a wrong table set become a table-selection failure, and only once the
tables are right does anything else become a SQL-construction failure.
`gold_unusable` sits outside the cascade entirely: a gold statement that
hardcodes its rows (`is_frozen_constant`) or does not parse cannot be reached
from schema by any model, so it is charged to no stage and dropped from
`gradeable`.

The two routing failures are told apart rather than folded into one.
`embedding_wall` means the gold schema never made the shortlist — a
retrieval miss before any picker ran. `wrong_schema` means the shortlist held
the right schema and the picker chose a different one anyway — a prompt
problem, not a retrieval one. `attribute_row()` decides between them from
`gold_in_shortlist`, derived from the row's recorded `shortlisted_schemas`;
when that was never recorded, the row attributes to `wrong_schema`, the
conservative reading that blames the component actually observed rather than
the one that cannot be seen.

**By class**, the *dimensions* that differ (`sql_diff.Dimension`) are reported
as a set on every wrong, gradeable row, not collapsed into the single label
`stage` uses. A wrong query is routinely wrong along more than one dimension
at once — the measured distribution runs as high as eleven simultaneous
classes on one query — so `error_taxonomy` counts class incidence and
records `n_classes` per row so the overlap is visible rather than assumed.
This is why per-class counts do not sum to a headroom estimate: on the last
benchmark 61% of wrong answers were multi-class, so "N questions have the
wrong table" does not mean N questions become correct once table selection is
fixed — most of them are still wrong about something else too.
`summarise_attributions()` records `multi_class_share` in the artifact for
exactly this reason, so a reader does not have to rediscover the
non-additivity before reading `error_class_incidence` as if its rows were
independent levers. The report that once published per-class point estimates
without this number arrived at "+46 points available," revised it to "3–5"
with nothing in between to justify either, and neither figure could be
re-derived from the artifact it came from.

The cascade gives mutually exclusive stage buckets; it does not give causal
headroom. "How much EX would fixing table selection buy?" is a counterfactual
question, and summing counts from a taxonomy that admits multi-class rows
over-answers it. The honest answer comes from substituting gold at one stage
and re-measuring — see [The oracle ladder](oracle-ladder.md).

Both blocks are computed once per arm at run-aggregation time, not stamped
per row: `summarise_attributions(attribute_rows(rows, gold, shortlists=...))`
runs in both `run_datalake.py` and `run_experiment.py`, and its output lands
in `summary.json` at `arms.<arm>.errors` — `n`, `n_wrong`,
`n_wrong_gradeable`, `n_gold_unusable`, `by_error_stage`, `by_error_primary`,
`error_class_incidence`, `classes_per_query`, and `multi_class_share`. The offline
taxonomy key is deliberately **`by_error_stage`**, not the live serve summary's
`by_failed_stage` (Outcome/Stage from `classify_row`): the two meanings used to
share a name and mixed debugging. It is `None`, not an empty dict, when no gold
was supplied to the run: an empty dict would assert that nothing was
miscategorised, and `None` says the question was never asked.

**Dimensions** (`sql_diff.Dimension`) — the syntactic facts compared, in
roughly outside-in order:

| Dimension | What it compares |
|---|---|
| `schema_set` | which schemas (`db` qualifiers) the statement's tables belong to |
| `table_set` | which physical tables the statement reads |
| `join_graph` | which tables are joined to which, as an unordered edge set (insensitive to join order) |
| `join_keys` | the exact `table.column` pairs used as join equalities |
| `join_type` | the join kinds present (`INNER`/`LEFT`/...) and how many of each |
| `projection` | the selected columns/expressions, in order (`order_only` flags a right-set-wrong-order mismatch) |
| `filter_columns` | columns referenced in `WHERE`/`HAVING` |
| `filter_literals` | literal values compared in `WHERE`/`HAVING` (case-folded, so casing differences are a value error, not a structural one) |
| `aggregation` | aggregate functions (`SUM`/`AVG`/`MIN`/`MAX`/`COUNT`) and the columns they wrap |
| `group_by` | the `GROUP BY` columns |
| `order_limit` | `ORDER BY` keys plus `LIMIT`, order-sensitive |
| `distinct` | whether `SELECT DISTINCT` is present |
| `set_ops` | `UNION`/`INTERSECT`/`EXCEPT` usage and counts |

**Error classes** (`error_taxonomy.ErrorClass`) — what each dimension
mismatch, or its absence, is called:

| Class | Meaning |
|---|---|
| `embedding_wall` | gold schema never made the shortlist — a retrieval miss before any picker ran |
| `wrong_schema` | shortlist held the gold schema; the picker chose another |
| `execution_error` | statement parsed, then raised when the grader executed it — charged to `execute`; not a harness crash and not a structural/value class |
| `unparseable_sql` | the generated text does not parse as SQL at all |
| `gold_unusable` | the gold statement is a frozen constant or does not parse — not the model's failure, excluded from the cascade and from `gradeable` |
| `wrong_table` | `table_set` mismatch |
| `wrong_join_graph` | `join_graph` mismatch |
| `wrong_join_key` | `join_keys` mismatch |
| `wrong_join_type` | `join_type` mismatch |
| `wrong_projection` | `projection` mismatch — wrong columns/expressions selected |
| `projection_order` | `projection` has the right elements in the wrong order |
| `wrong_filter_column` | `filter_columns` mismatch |
| `wrong_filter_literal` | `filter_literals` mismatch |
| `wrong_aggregation` | `aggregation` mismatch |
| `wrong_group_by` | `group_by` mismatch |
| `wrong_order_limit` | `order_limit` mismatch |
| `wrong_distinct` | `distinct` mismatch |
| `wrong_set_op` | `set_ops` mismatch |
| `value_level` | every dimension resolved and matched, and the answer is still wrong — the difference is in a value. Charged to `sql_generate`: the statement executed fine and returned exactly the rows it asked for, so the defect is in what the generator wrote, not in the executor |
| `unresolved_diff` | at least one dimension came back `unknown` (alias/scope resolution failed), so "no mismatch found" is not "everything matched". Charged to no stage, and counted in `n_unattributed` so the gap has a size |

Each wrong row also carries `result_shape` — `both_empty` / `empty_result` /
`row_count_differs` / `same_row_count` — derived from the `pred_nrows` and
`gold_nrows` the grader already recorded. It is descriptive only and never decides
a stage: knowing a query came back empty tells you what a bad literal did, not that
a different component is at fault. It costs no extra query, which is why an earlier
design that re-executed gold and generated per wrong row was removed rather than
wired up: the two round-trips bought a distinction grading had already paid for,
and `Stage.execute` stays reserved for a statement that genuinely failed to run
(stamped live via `refused_by="execution"`).

## Treatment verification: did the intervention reach the model

An experiment compares arms that are supposed to differ. `eval/treatment.py`
exists because on this project the arms sometimes did not differ at all, and
nobody noticed until a conclusion built on top of the null had already
shipped. The rule the module enforces: an intervention's effect may not be
reported until the intervention is shown to have been applied. A corpus on
disk, an arm that ran, and rows that came out are evidence that nothing
crashed — they are not evidence that anything was delivered.

Two incidents forced this rule into code. The Simulated-SME arm read its
clarification ledger from a path a build step had already moved; it folded
nothing, every run produced a corpus byte-identical to the arm it was
supposed to improve on, and "SME adds no accuracy" was reported for weeks
before the ledger bug was found. The "oracle" corpus — 9,154 gold business
rules built to establish the ceiling on what any semantic layer could be
worth — wrote every note's scope as `scope: ['<schema>']`. Scope matching
wants `schema:<name>`, a `db:` prefix, or a bare asset id; a bare schema name
matches none of those. All 9,154 notes silently failed to match, none
reached a prompt, and the median per-question prompt changed by one token.
The resulting "+5 questions, not significant" was published as proof that
enriching the semantic layer is an exhausted lever, and a roadmap was written
on top of it. Both failures were silent because nothing in the pipeline
asserted that the treatment had been delivered — every check that existed
passed.

A third incident marks the limit of what divergence can see. The Simulated SME
was briefed from BIRD's `database_description/*.csv`, which are the *original*
BIRD descriptions and were never re-keyed to the obfuscated schema. On the 55 of
69 databases that carry a real rename, the SME therefore talked about
`PurchaseDate` while the agent was choosing between `kaufdatum`,
`bewertungsdatum` and `transaktionsdatum`. Every statement it made was true and
none of it could land on a column — precisely the German date-column
substitutions seen in beer_factory. Two smaller defects rode along: the address
was read from `column_name`, a human label ("customer id"), rather than
`original_column_name`, the identifier; and 83 of the 597 CSVs open with a BOM
that corrupts the first header name, blanking `original_column_name` for every
row of those files. `build_sme_brief` now takes the db's `rename_map` and
translates every identifier it emits, dropping described columns the map does
not cover — BIRD ships full-dataset docs for subset databases, so those columns
are not in the schema at all. `bird_loader.description_dir()` also searches both
BIRD trees; hardcoding `train_databases/` found no CSVs for the 11 dev-tree
schemas and built their SME arm blind.

Note what this costs the check above: the arm *did* diverge. The brief had
content, clarifications folded, `context_hash` moved. Divergence proves an
intervention was delivered, not that it was delivered in a form the model could
use, and no automated gate in this repo distinguishes those two. Reading an
actual brief remains the only way to catch a treatment that is well-formed and
misaddressed. Measured against the BIRD SQLite schemas: 8.6% of emitted
identifiers named a real physical column before the fix, 99.6% after.

Unblinding the dev tree also broke a second guard, which is worth recording
because the failure mode is the same shape. `assert_brief_no_leakage` matched
`\bSELECT\b` case-insensitively, and european_football_2's `Player_Attributes.csv`
says "implies that the player will select the attack actions he will join in", so
that schema began failing the leakage assert and dropping out of the pool *after*
its baseline, seeded and curated corpora had been built and paid for. The guard is
now case-sensitive; all 30,492 gold statements spell the keyword `SELECT`, so it
loses nothing.

The graded database is `rename_decoy`, which is two transformations, and the
above fixes only the first. Alongside the real schema sit 1,486 invented columns
and 162 invented tables. None has a BIRD description or a rename-map entry, so
none can reach the brief — and the drop rule now guarantees it. Measured against
the SQLite schemas: all 2,893 real physical column names are described and zero
decoy names are, which makes "absent from the brief" a sound signal rather than a
coverage gap. Under `sme_rules` v1 the SME simply had nothing to say about
precisely the columns a trap-avoiding curator most needs help on. `sme_rules` v2
turns the absence into the answer — *I do not recognise that identifier, it is not
part of the documented schema, I would not rely on it* — which is derivable from
the brief alone and needs no trap manifest. Feeding it the manifest would be the
tempting version and the wrong one: it hands the SME arm ground truth no other arm
has, and the lift would be leakage wearing the costume of expertise.

v2 is **not** the default. It is a falsifiable candidate — refuted if
`decoy_touch_rate` does not fall on the SME arms, with `refusal_rate` and
clarification volume watched for the over-refusal it could buy instead — and
`curated -> curated_sme` is a step this doc already flags as compound. Select it
with a `[prompts]` entry (`sme_rules = "v2"`); the prompt-set hash moves, so a run
can prove which rules block it sent.

Two limits survive, both from the rename map being *flat* — one namespace for
table and column names per db, so it cannot express a per-table column rename.
Six emitted `(table, column)` pairs are columns that exist in the schema but not
in the table they are printed under: BIRD's Northwind docs describe
`retail_world.Customers.Phone`, the physical `kunden` has no phone column, but
`Phone` maps via `Suppliers.Phone` and so is emitted anyway. And the
case-insensitive fallback used to match a misfiled CSV name flattens eight dbs'
case-distinct keys (hockey has both `G` and `g`); only three lookups in the whole
corpus resolve through that fallback today and none of them is ambiguous, but
`mondial_geo` is one CSV filename away from it mattering. Closing either needs the
physical schema, not the map.

Delivery is now measured from the rows a run already produces, at two levels.

**Per arm**, `fingerprint_arm()` builds an `ArmTreatment` from an arm's
generation rows: how many rows recorded delivery fields at all
(`n_rows_observed` — absence here is unverified, not zero), how many carried
injected notes (`n_rows_with_notes`, `n_notes_injected`, `distinct_note_ids`),
and how many carried a `context_hash` (`n_rows_with_context_hash`,
`distinct_context_hashes`, `mean_context_chars`). `observed` is `True` only
once at least one row recorded something; `note_injection_rate` is `None`,
not `0.0`, when nothing was observed — the same empty-denominator rule the
rest of this doc follows. This lands per arm in `summary.json` at
`arms.<arm>.treatment`.

**Per pair**, `compare_arms()` builds a `PairDivergence` from two arms'
`context_hash` values on the questions they share. `context_hash`
fingerprints everything the prompt actually assembled for a question —
corpus notes, few-shots, schema context — so two arms that hand the model the
same hash on a question handed it the same prompt, whatever their corpora
say on disk. Only questions where *both* sides recorded a hash count toward
`n_comparable`; a question missing a hash on either side is excluded and
counted separately in `reasons`, so a run that predates hash recording reads
as unverified rather than as agreement. `divergence` is
`n_different / n_comparable`, and `delivered` is `True` only once that clears
`DEFAULT_MIN_DIVERGENCE` (0.05) — not 1.0, because two corpora can
legitimately agree on questions where neither has anything extra to say, and
on a wide benchmark most questions touch only a handful of tables. But
agreement on nearly every question means the arms are the same experiment run
twice: the oracle failure above sat at exactly 0.0 divergence, and a working
treatment on the same benchmark moved essentially every row. This lands in
`summary.json` as `treatment_divergence`, one entry per arm pair, rendered
for the console by `divergence_table()`.

A missing `context_hash` therefore reads as **unverified**, never as
**delivered**: `PairDivergence.delivered` is `False` whenever `n_comparable`
is `0`, and its `reasons` say explicitly that whether the arms differed is
unverified, not verified-equal. This is the same fail-closed rule
`crash_rate` follows elsewhere in this doc: an absent measurement is not a
clean one.

This gates whether a run's own numbers may be quoted. `eval/index.py`'s
`record_for_run()` reads `treatment_divergence` and each arm's `treatment`
block back out of `summary.json` and folds any non-delivery into
`treatment_not_delivered`, which `quotable()` appends to its reasons — a run
whose arms never actually diverged, or whose corpus injected no notes despite
holding some, is not quotable in the ledger, without anyone having to
remember the SME or oracle incidents to know to check.
`treatment.treatment_reasons()` is the same check available as a standalone
function over a set of fingerprints and divergences (`tests/test_failure_attribution.py`
exercises it directly); the ledger's `record_for_run()` reads the identical
artifacts through its own `_undelivered()` helper rather than calling it, so
the two are equivalent in what they detect, not the same call path.

## Noise floor and minimum detectable effect

The serve path is not deterministic, and it cannot be pinned: the model sits
behind a proxy that drops the `temperature` parameter, so whatever sampling
noise exists is a fixed cost of the setup, not a knob this project can turn
down. `eval/power.py` exists because that noise had never been measured,
which is what let a genuinely unresolved comparison get reported as a
genuinely null one.

**The floor is measured, not assumed.** `--replicate ARM` on `run_datalake.py`
serves one arm's corpus a second time under the name `ARM__replicate`,
appended last in the serve order so a run that dies partway still has its
real arms scored. `measure_floor()` then compares the two runs'
`correct_by_question()` maps and counts how often they disagree — re-running
one arm against itself on the last full benchmark moved 135 of 2030
questions, even though the headline EX barely moved. `NoiseFloor.suspect` is
a sanity check on the replicate itself: if the *net* of the disagreement is
large relative to its scatter (`abs(net) > 2 * sqrt(n_discordant)`), the two
runs were not actually the same configuration, and the floor derived from
them is not a floor.

**The minimum detectable effect follows from the floor.**
`minimum_detectable_effect()` turns the measured discordance rate and
question count into the smallest true difference a McNemar test on this run
could call significant at `alpha=0.05` and `power=0.80`. At 135 discordant of
2030 questions that works out to roughly 33 questions, or about 1.6 points of
EX. It is reported *before* any delta is shown, because a reader needs to
know what the run was capable of seeing before being shown what it saw. This
is also what makes the earlier mistake concrete: a previously published "+5
questions, not significant" result was roughly 6.5x below this run's own
resolution — not evidence the intervention did nothing, evidence that the
experiment could not have told the difference from noise either way.

**Every comparison is paired**, never a difference of marginal rates.
`mcnemar()` computes an exact two-sided p-value over only the questions two
arms both answered, using the exact binomial tail rather than the
chi-square approximation, because the discordant counts here are often small
enough for the approximation to mislead (`_binomial_two_sided` falls back to
a normal approximation only past `_EXACT_LIMIT` — 4000 discordant pairs —
where it is accurate well past the digits reported). Pairing matters because
it cancels question difficulty, the largest source of variance on a
benchmark whose questions range from trivial to unanswerable: a difference of
marginal rates spends its power re-discovering that some questions are hard,
and a paired test does not have to, which is what recovers most of the
resolution the unpinnable temperature costs.

`comparison_report()` bundles one `McNemarResult` with the run's
`NoiseFloor` and `DetectableEffect` into a single dict — `net_questions`
never appears in `summary.json` without `detectable` and `reading` beside
it, so a delta cannot be read without also reading whether the run could have
resolved it. Without a replicate, `reading` says so plainly: "no noise floor
measured for this run — significance is reported without knowing what the
run could resolve." This is a per-run measurement, not a constant, and today
only `run_datalake.py` computes it — `run_experiment.py`'s single-db ladder
does not wire `--replicate` or `power.py` at all, so a single-db run reports
McNemar-free deltas with no stated resolution.

**Zero observed discordance is not zero noise.** `minimum_detectable_effect()`
used to return `0.0` questions when a replicate happened to agree with itself
everywhere, which made `resolves()` true for *any* effect — including no
effect. That inverts the module's purpose, and it bit hardest on small runs,
where zero disagreements is unremarkable rather than informative. Zero events
in `n` trials bounds the rate at roughly `3/n` (the rule of three), so the
floor now falls back to three discordant pairs and marks itself
`from_zero_discordance` — a bound, not a measurement. With no paired questions
at all the result is `measured=False`, `resolves()` answers `False`, and
`reading` says the resolution is unknown; "we could not tell" must never read
as "yes".

## Two things the question-level test cannot do

**Multiple comparisons.** A run with four arms produces six pairwise McNemar
tests. At a nominal `alpha=0.05` each, the chance of at least one false
positive across the family is about 26%, and every one of them used to be
reported as though it stood alone. `holm_adjust()` applies a Holm–Bonferroni
step-down across the family, and each comparison carries `p_value_holm`,
`family_size` and `significant_holm` beside its raw `p_value`.

The family is the pairs that actually tested a hypothesis the run is asking, which
is narrower than "every pair on disk" in three ways. Diagnostic **oracle** rungs are
out. So is the **`--replicate`** arm and every pair it forms: it exists to measure
the noise floor, and each pair it makes duplicates the one its source arm already
makes, so a four-arm run plus one replicate would otherwise correct across ten tests
where six distinct questions are asked. So are pairs that **shared no questions**,
whose `p_value = 1.0` comes from an empty discordance count — the arithmetic of
having nothing to compare rather than a measurement. All three exclusions are the
same reason: spending significance on a test nobody asked makes every real
comparison harder to call, which is the correction working against its own purpose.
Excluded pairs are still reported with their raw `p_value`; they just carry no
adjusted one.

Holm is used rather than plain Bonferroni because it is
uniformly more powerful and needs no independence assumption — which matters,
since six comparisons that share arms are anything but independent.

**Clustering.** The paired test treats every question as one independent
observation. They are not: they are nested in ~69 databases of very different
difficulty and schema shape, so a corpus change that happens to suit five of
them yields a hundred correlated "wins" and a p-value that is
anticonservative by an unknown factor. `cluster_sign_test()` moves the unit of
analysis up — score each database by how many questions each arm answered
correctly, then ask how many databases improved versus regressed, as an exact
sign test. It is deliberately less powerful than the question-level test; the
extra power the question-level test appears to have is largely borrowed
against an independence assumption the data does not support. Every comparison
carries a `cluster` block. Read both: agreement is reassuring, and a
question-level win the cluster test cannot see is a result resting on a handful
of schemas, which is worth knowing before it becomes a claim.

## The ladder: one variable per step

Deltas are reported only between *adjacent* rungs, and the rungs are ordered so that
each step changes exactly one thing. `ladder_steps()` derives them from the arms a run
actually scored rather than from a fixed list, so a partial `--arms` chains what it
has; `skipped_rungs()` names what any non-adjacent step bundles, and the driver both
records that in `deltas.*_bundles` and prints it.

`deltas.*_correct_answers` is the **paired** net gain on identical question-id
sets only (same pool as pricing). Equal-N different pools, missing ids, or unequal
N leave it `null` with `*_correct_answers_unmeasured_because`. A raw
`n_correct` subtraction may still appear as `*_unpaired_n_correct_delta` — that
name is deliberate and must not be quoted as answers gained.

| Step | Adds | Costs to build |
|---|---|---|
| `baseline → seeded` | train-SQL joins + metrics, decoy / negative-space marking; drops baseline's FK-name guesses. **No few-shots.** | nothing — no model calls |
| `seeded → curated` | the curator LLM agent (including few-shots), over that same seed | one curator pass per db |
| `curated → curated_sme` | the SME clarification protocol **and** BIRD's human column documentation, together | one SME round per db |

Two of these exist because the comparison above them was compound.

`baseline → curated` bundled the *mechanical* half of `build_curated_corpus`
(train-SQL-derived joins and metrics, plus marking columns absent from gold as
decoys / negative space) with the *LLM* half that authors few-shots and the rest
of the Inference tier. Both always ran together, so the delta was equally
explainable by the free deterministic seed — itself multi-mechanism, not
"parsing only" — as by the curator agent. `seeded` is the same code path with
`run_agent=False`: joins and metrics only, no few-shots, zero model-call build
cost. Do not quote `baseline → seeded` as few-shot lift or as a single-mechanism
parse effect.

`curated → curated_sme` bundles the clarification protocol with a new information
source, and **this one cannot be split.** The Simulated SME's brief is built from
BIRD's `database_description/*.csv` — human-authored column and value descriptions
— and Phase A never receives that directory. So the delta is equally consistent
with "the protocol works" and "we handed the pipeline a better knowledge source for
the first time", and the headline claim is the former.

A `curated_sme_blind` rung existed for this and was removed 2026-07-28. It ran the
identical round with the brief built from train questions and evidence only —
inputs the curator *already has* — so it compared the curator against itself
re-asked through a Q&A round-trip, and the only thing it genuinely added was
`certified` provenance stamping. Splitting this confound needs a knowledge source
the curator lacks and a simulated SME does not supply.

Removing it did not hide the confound: the step is now adjacent, so nothing is
"skipped", but `single_variable` is `not bundles and len(mechanisms) == 1` and the
step declares two mechanisms. Same shape as `baseline → seeded`, which is adjacent
and changes three things.

## The train split, and the gap

`--split both` builds the corpora once and scores the held-out and training splits
against the same corpora, writing `split_gap.json` with `train − test` per arm.

The training questions are the ones the curator read: `seed_from_train_sql` extracts
its joins and metrics from their gold SQL, and `_mark_columns_absent_from_gold`
derives the decoy mask from it. So a curated arm's train EX is partly recall of
statements it was built from, and `eval.index.quotable` refuses a train-scored run
for exactly that reason. Do not quote it, and do not average the two splits.

What the pair buys is the gap, which is the overfitting measure this benchmark
otherwise has no number for:

- a **small** gap says the corpus encodes something reusable;
- a **large** gap says it encodes the training statements.

That distinction is the same one `ex_twin` / `ex_no_twin` addresses per question, at
corpus level instead. It is a within-arm quantity, so it is not paired and gets no
p-value — read the sign, not the digits. And note that on train every scored
statement is its own train twin by construction, so `ex_no_twin` is empty there;
the train split's headline is the gap, not its own EX.

Both splits must share one build. The curator is stochastic, so a rebuild between
them mixes overfitting with curator variance and the gap stops meaning either — which
is why `run_datalake` takes `corpus_dir` separately from `out_dir`.

## Concurrency, and what it is allowed to change

Two independent knobs, because they exhaust different resources. `--workers` fans out
the per-question serve loop and is bounded by Postgres `max_connections`;
`--build-workers` runs whole curator builds concurrently and is bounded by the model
provider's rate limit, since each holds a connection *and* a deep-agent conversation.

Neither is a resume knob. They change how long a run takes, never what a scored row
means, and per-build isolation makes resuming at a different width safe — so they are
recorded in the manifest and deliberately excluded from `_RESUME_KNOBS`.

The build loop was serial until now, and the thing that made it serial was not the
databases (they are independent, each with its own schema and connector) but the
*filesystem*: the curator writes its five sidecars at the
**arm root**, not per schema, and points the deep agent's `FilesystemBackend` there
too. Two concurrent builds would interleave writes to one `clarifications.jsonl`,
which for the SME arm means one schema's clarification text folded into another's
corpus. Each build therefore runs in a private staging root and is promoted on
success, which keeps every path relationship *inside* a build byte-identical to the
serial case. That is deliberate: the one time those paths were re-pointed, the SME arm
read its ledger from a directory a build step had moved, and every SME number for
weeks was a no-op.

Staging is cleared at the start of every build; only a durable
`BUILD_COMPLETE.json` counts as finished, so a kill mid-build cannot leave
partial YAML that resume would later adopt as a complete corpus.

A rate-limit storm is legible rather than silent: those turns classify as crashes (not
refusals), which blocks quotability, and `arms.<arm>.by_error_type` says whether the
crashes were `RateLimitError` — re-run narrower — or something else.

## Symptom → field → file

| You observed | Look at | It lives in |
|---|---|---|
| EX dropped and you can't tell if it's a bug or a real refusal | `outcome` (`crashed` vs `refused`), `failed_stage` | `generations.<arm>.jsonl`; `governed_bi.stages.classify_row` |
| `refusal_rate` moved and you don't know which layer decided | `by_failed_stage`, `by_guardrail_layer` | `summary.json` |
| The single-db ladder run's crash count, separate from its refusals | `crash_rate`, `n_crashed`, `by_outcome` on `ArmSummary` | `summary.json` (`run_experiment.py`) |
| Something in the serve path threw and you need to know where | `stage_events` entries with `status=="error"` and `detail.error_type` | `stage_events.jsonl` |
| A `refused_by` value you don't recognise | `n_unmapped_refused_by`; the printed `*** WARNING: unrecognised refused_by=...` | `summary.json` / driver stdout; `REFUSED_BY_TO_STAGE` in `stages.py` |
| `routing_recall` looks impossibly high or low | `n_routing_observed` (excludes crashed AND bypassed turns) | `summary.json` |
| `routing_recall` is `null` on an arm that clearly ran | `n_routing_bypassed` — a one-schema pool or an oracle rung has no routing decision to score, and "not measured" beats both 0.0 and a self-congratulatory 1.0 | `summary.json` |
| `routing_recall` is `null` and nothing was bypassed either | `n_routing_unrecorded` — the turns ended before `assemble`, so there is no decision to score. Non-zero on a live arm means the serve path is losing provenance | `summary.json` |
| `cond_ex_given_routing` doesn't square with EX / routing_recall | the five `n_correct_*` buckets — EX is over every row, the routing terms are not, so `n_correct == routed + unrouted + bypassed + routing_unrecorded + routing_crashed`, with `n_correct_unaccounted` as the check | `summary.json` |
| A rate reads `0.0` and you can't tell if anything was measured | the field's denominator count (`n`, `n_produced`, `n_routing_observed`, ...) — `None` means unmeasured, `0.0` means measured-and-zero | `summary.json` |
| EX denominator looks padded by rows with no usable gold | `n_gold_unusable` (alongside `n_missing_gold`) | `summary.json` |
| A db's decoy-touch rate reads suspiciously clean | `decoy_manifest_missing_dbs` | `summary.json` (`run_datalake.py`) |
| "How much exploring did this arm do?" | `n_tool_calls` (per row), `tool_calls` (summed) | `generations.<arm>.jsonl`; `summary.json` |
| Is this run's EX safe to quote | `ledger_ok` / `quotable` (hygiene only), then the runbook claim checklist; never `claim_ready` from the ledger alone | `runs/index.jsonl` |
| Are two runs actually the same experiment | `comparable(a, b)` diff list | `runs/index.jsonl` via `eval.index` CLI |
| A wrong answer used the right schema — retrieval or generation? | `table_selection_report()`: `n_retrieval_miss` vs `n_selection_miss` | `analysis.json` via `eval.analysis` |
| A wrong answer's stage and kind, beyond "right schema, wrong SQL" | `by_error_stage`, `by_error_primary`, `error_class_incidence`, `n_classes` | `summary.json` (`arms.<arm>.errors`); `governed_bi.eval.error_taxonomy` |
| Per-class counts look additive and you're tempted to sum them into a headroom number | `multi_class_share` | `summary.json` (`arms.<arm>.errors`) |
| What a stage's failures actually cost, not just how many there are | oracle rungs (`oracle_sql`/`oracle_schema`/`oracle_tables`) | `governed_bi.eval.oracle`; see [Oracle ladder](oracle-ladder.md) |
| Two arms might be the same experiment run twice | `treatment` (per arm), `treatment_divergence` (per pair) | `summary.json`; `governed_bi.eval.treatment` |
| A wall of crashes and you can't tell a rate limit from a bug | `by_error_type` (per arm), `error_type` (per row) | `summary.json`; `generations.<arm>.jsonl` |
| A wrong answer that returned nothing vs one that returned gold's shape | `by_result_shape` (per arm), `result_shape` (per attribution) | `summary.json` (`arms.<arm>.errors`) |
| Wrong answers charged to no stage at all | `n_unattributed` — unusable gold, plus rows the differ could not resolve (`unresolved_diff`) | `summary.json` (`arms.<arm>.errors`) |
| A delta that looks significant across a 4-arm run | `p_value_holm`, `family_size` — six pairwise tests at nominal 0.05 is a ~26% family-wise error rate | `summary.json` (`comparisons[]`) |
| A question-level win you suspect is carried by a few schemas | `cluster` — a sign test over databases instead of questions | `summary.json` (`comparisons[]`) |
| A delta between arms that are not adjacent rungs | `deltas.<hi>_minus_<lo>_bundles` — the rungs the step skipped, so it changed more than one thing | `summary.json` |
| Cost per added correct / how many answers a rung bought | `deltas.*_correct_answers` (paired identical question-id sets only), `*_usd_per_added_correct`; when pools differ, `*_correct_answers` is `null` with `*_correct_answers_unmeasured_because`, and any raw count delta is only under `*_unpaired_n_correct_delta` | `summary.json` |
| A run's EX isn't quotable because a treatment never landed | `treatment_not_delivered` | `runs/index.jsonl` |
| A "+N questions" delta might just be sampling noise | `comparisons[].detectable`, `comparisons[].noise_floor`, `comparisons[].reading` | `summary.json` (`run_datalake.py`, only when `--replicate` was used) |
| `routing_recall` is 0.0 and every wrong answer blames the picker | `routing_bypassed` — true means the router never engaged (a pinned oracle corpus, or a pool holding one schema), so it cannot have missed | `generations.<arm>.jsonl` |
| Whether a number came from a fair arm or a rung that read the answer key | `oracle_rung` (`None` on every fair arm), `arms_run` vs `fair_arms` | `generations.<arm>.jsonl`; `summary.json` |
| An arm looks short in a paired comparison | `question_coverage.incomplete_arms` | `analysis.json` |
| A turn's own latency/tool-call/guardrail history outside eval | `load_run_record(turn_id, settings)` | the portable run log (ADR 0004), not just eval artifacts |

## Prompt attribution

`prompt_set_hash` is one of the `comparable()` keys, and both drivers write it now:
`run_datalake.py`'s pooled manifest and `run_experiment.py`'s single-db manifest
each stamp `prompt_variants` + `prompt_set_hash`, so two single-db runs on
different prompt variants are flagged incomparable the same way two pooled runs
are. The full attribution chain — registry to stamped row to manifest to ledger,
plus the fail-closed contract and the decision table for which variant a measured
failure actually calls for — is in [Prompt-variant experiments](prompt-experiments.md).
