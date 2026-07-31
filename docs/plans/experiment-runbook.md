# Experiment runbook

> **STATUS 2026-07-31 — MOVE, not delete. This is a standing manual, not a plan.**
>
> It belongs at `docs/experiment-runbook.md`. Reason it cannot simply be deleted:
> `eval/index.py:612` writes the string **"claim readiness is the experiment-runbook checklist"**
> into the `claim_ready_blocked_because` field of **every** `runs/index.jsonl` record. Also cited
> by `eval/power.py:119` and by `docs/README.md`:59 via the anchor
> `#the-result-that-would-make-us-abandon-the-corpus-thesis`, which must survive the move.
>
> **Seven factual errors to fix during the move** (checklist D1 lists six):
> 1. 69 db / 2030 questions → **57 / 1351**
> 2. `--limit-dbs 3` selects `address, airline, app_store` → `address, airline, **authors**`
>    (`app_store` is no longer in the split)
> 3. stratified pilot 166 questions → **135**
> 4. twin rate 182/1627 → **115/1200**
> 5. "this repo has never run the full split with a model" → the 20260730 run did
> 6. the `--pg-dsn` default shown with a password → `run_datalake` reads `GOVERNED_BI_PG_DSN`, no password
> 7. **new** — the ladder table's baseline→seeded row says the step "applies a train-conditioned
>    column mask covering ~86% of test-question gold columns". **That mask was deleted**
>    (`_mark_columns_absent_from_gold` is gone). `docs/glossary.md`:70's Seeded-arm entry copied
>    the same sentence and must change with it.
>
> Also: Step 0/1 use `--oracle-only` (M3 N10 Option A / checklist 0.2): empty fair
> arms + `oracle_sql`, no global `--skip-agent`.
>
> Content to merge in from [datalake-run.md](datalake-run.md) — once, not twice: rate-limit
> backoff, the `build-workers` / `workers` division, and the resume contract.

What to run, in what order, and what has to be true before a number is worth
quoting. Written for someone with the machine and the data who did not build the
harness.

Every number produced before 2026-07-26 is discarded. Nothing below depends on a
prior result.

## What you need

- The obfuscated BIRD checkout (`../BIRD-Data-Obfuscation`), with
  `eval_dataset/{train,test}_final.jsonl` and
  `data/train/train_databases/<db_id>/database_description/*.csv`.
- Postgres holding the obfuscated schemas (`pg_rename_decoy`, port 5435 locally).
  `run_datalake` takes the DSN from `--pg-dsn` (default
  `host=127.0.0.1 port=5435 dbname=bird user=bird password=bird`). It does **not**
  auto-read `PG_RENAME_DECOY_DSN`; that env var is for product `[datasource]` overlays
  and live Postgres integration tests, not this CLI, unless you pass its value
  explicitly as `--pg-dsn "$PG_RENAME_DECOY_DSN"`.
- `OPENAI_API_KEY` in the repo-root `.env`.
- `uv sync`.

## Step 0 — prove the grader before spending anything on a model

```bash
uv run python -m governed_bi.eval.run_datalake --oracle-only --oracle oracle_sql
```

`oracle_sql` submits gold SQL straight to the grader. No model call, no retrieval, no
agent loop — it shares only the last step with a real arm, which is exactly why its
number answers a different question: **what does the grader score gold at?** Anything
below 1.0 is a grading gap (a frozen `VALUES` constant, a stale hash, a normalisation
quirk), and every later number should be read against that ceiling rather than
against an assumed 1.0.

`--oracle-only` is what makes this free (M3 N10 Option A: no-model is inferred from empty fair arms, not a global flag): it costs zero model calls, so run it over the
whole split rather than a sample. The `baseline` arm alongside it will refuse
everything and score 0 — that is expected and not what you are reading. Read
`arms.oracle_sql.ex_gradeable`, and read the list of questions it got wrong: those
are the ones no arm can ever win.

Do not skip this step. It is the only thing that calibrates everything after it, and
it costs nothing.

**The run does its own gold pre-flight too, before the build phase.** It samples gold
per schema, executes it against Postgres and compares against the recorded hashes —
about 40 ms per row per schema, so a few seconds over the whole split. It runs *before*
any model call, so a wrong DSN, an unloaded schema, or gold read from the
un-obfuscated `sql_sqlite` costs you seconds rather than a full curator pass over 69
schemas.

It aborts when more than a quarter of schemas cannot execute their gold, which is a
configuration fault — with a floor of 2 failing schemas, so on a 3-schema pilot one
failure (33%) warns rather than aborts. The floor is there because a single awkward
query in a tiny pool is not a misconfiguration; on the full split the fraction is what
binds. Below that it warns and records the schemas in
`gold_hash_self_check.exec_error_dbs`, which blocks quotability — one query crossing
the 60 s gateway timeout should not make the split unrunnable, but a score for a schema
whose gold nothing confirmed is not a number to quote. If that happens, raise the
sample rather than ignoring it:

```bash
uv run python -m governed_bi.eval.run_datalake --oracle-only --oracle oracle_sql --gold-per-db 3
```

A schema counts as verified when *any* sampled row executes and agrees, so raising this
buys redundancy against one awkward row rather than more ways to fail.

Keep `--oracle-only` on it. Everything in this step is meant to cost
nothing, and the bare command inherits the full default ladder — four arms, and a fifth
serve pass if you add `--replicate`. That is the whole Step 2 budget, launched from the
section that promises to spend none of it.

**The run also refuses to serve a pool that mostly failed to build.** If fewer than
half the requested schemas build, it stops after the build phase rather than scoring
the remainder — the pooled router would be ranking against a corpus that was never
built, and `quotable()` refuses the run on `build_errors` anyway, so serving it spends
the serve budget on a number nobody could quote. A handful of failed schemas is fine
and does not stop the run; they are named in `summary.json` → `build_errors`.

That threshold and the gold one are deliberately separate. The gold share asks "is this
a systematic misconfiguration across what we asked for", so it is measured against the
pool the run set out to build; build attrition is a different failure and gets its own
check rather than being inferred from a gold denominator.

**Three kinds of attrition, three separate signals.** They have different fixes, so the
artifacts keep them apart rather than reporting one "coverage" number that means three
things:

| What went missing | Where it shows | What it does |
|---|---|---|
| Requested, but the schema is not loaded on Postgres | `dbs_absent_from_postgres`, `n_dbs_requested` | Warns; blocks quoting |
| Loaded, but the corpus build failed | `build_errors` | Blocks quoting; aborts below 50% |
| Built, but its gold would not execute | `gold_hash_self_check.exec_error_dbs` | Blocks quoting; aborts above 25% |

The first is the one to check before a scale run: neither of the other two gates can see
it, because both measure against the schemas that were actually present. A default run
against a partially-loaded Postgres will happily score 40 of 69 schemas and report full
coverage of what it attempted — so confirm your Postgres has every schema you intend to
measure before spending a model budget.

## Step 1 — offline smoke, no model

```bash
uv run python -m governed_bi.eval.run_datalake --oracle-only --limit-dbs 3 --limit 5
```

Exercises the harness around the model — build, pool, grade, summarise, index — with a
refuse-all solver. Every arm scores 0; that is expected. What you are checking is
that it completes, writes `summary.json`, and appends a row to `runs/index.jsonl`.

**It does not exercise routing.** The refuse-all solver returns before the graph is
built, so no embedder is constructed (the manifest records `use_embedder: false` even
when the knob is on), no schema shortlist is retrieved and the LLM pick never runs.
Every routing number in a quoted result — `routing_recall`, `schema_pick_accuracy`,
`by_gold_rank` — is measured under a configuration this step leaves untouched. The
first exercise of that path is the paid run, so treat an early routing anomaly there
as untested wiring rather than a finding.

## Step 1b — the pre-run pilot (do this before Step 2, it cannot be recovered after)

Two questions can only be answered *before* the full run, because both need a second
draw of something Step 2 draws once.

**Is the corpus itself stable?** `--replicate` re-serves the *same* corpus, so the
noise floor it measures is serve-side sampling only (`noise_floor.source:
"serve_replicate"`). But `curated`'s corpus is one draw from a stochastic agent, and
every `curated`-or-later delta is tested against a floor that excludes that variance.
Start free: build `curated` twice over the stratified pilot into two fresh directories
and diff the corpora — asset counts, join sets, few-shot ids, note text. One curator
pass over 6 schemas, no serving.

If the two builds are near-identical, the concern collapses and you can quote the
serve-only floor honestly. If they differ materially, spend the paid version: run the
pilot twice with `--replicate curated`, which gives you three numbers on the same 166
questions — serve-only discordance from within one run, build+serve discordance across
the two runs, and the difference between them as the build-attributable term. You are
not trying to measure build variance precisely, only to learn whether it is much
smaller than serve variance, comparable, or much larger. That answer sets the honest
multiplier on every full-split interval, and finding it out for the price of a pilot
beats finding it out after the write-up.

**Is the provider drifting under you?** Arms serve sequentially over hours, so provider
drift maps monotonically onto the ladder and looks exactly like a ladder effect. On one
pilot pass use `--replicate baseline` instead: that puts the control at serve position 1
and its replicate last, the longest lever arm in the run. `noise_floor` already carries
a signed `net` and a `suspect` flag for `|net| > 2√d`; across that gap a large signed net
is drift, not sampling. If the pilot's net is near zero, a monotone full-split result is
much safer to quote. Keep `--replicate curated` for the full split itself.

## Step 2 — the real run

**Model comes from TOML, not the CLI.** There is no `--model` flag on
`run_datalake`. The serve path reads `[models].llm_model` from
`governed_bi.toml` (today `gpt-5.6-luna`). Changing the model means editing that
file; forgetting to change it back does not error — it only leaves a `model`
field in `manifest.json` you may not notice. **Before spending:**

```bash
uv run python -c "from governed_bi.config import Settings, Environment; print(Settings.for_env(Environment.dev).models.llm_model)"
```

After the run, re-check `manifest.json` → `model`. Same preflight M4 N12b used.

Full ladder (test split, all schemas the split names, four fair arms, serve-side
replicate of `curated`):

```bash
uv run python -m governed_bi.eval.run_datalake \
  --split test \
  --build-workers 6 \
  --workers 8 \
  --replicate curated \
  --out runs/datalake
```

Defaults already cover all databases in the test split and arms
`baseline,seeded,curated,curated_sme`. To pin an explicit schema list (stratified
pilot, smoke, or a subset you intend to quote), pass `--dbs`:

```bash
uv run python -m governed_bi.eval.run_datalake \
  --split test \
  --dbs address,movies_4,beer_factory,european_football_2,language_corpus,superhero \
  --build-workers 6 \
  --workers 8 \
  --replicate curated \
  --out runs/datalake
```

**Know the size before you start it.** The test split is **69 databases / 2030
questions**. With the four default arms plus `--replicate curated` that is five serve
passes, so **10,150 scored turns** — each one an agent loop, not a single completion.
On top of that the build phase runs an LLM curator over 69 databases twice: once for
`curated` and once for the SME round. `baseline` and `seeded` cost no model calls to
build.

Cost and latency are already instrumented per turn — `arms.<arm>.cost` in
`summary.json` (`total_tokens`, `total_cost_est_usd`, `n_rows_priced`), and the same
fields in Langfuse — so if you want a figure before committing to the whole split,
pilot first and multiply. The per-stage token split is on each row in
`generations.<arm>.jsonl` under `token_usage`; it is not aggregated into the
summary, so attributing spend to a stage means re-reading the row file. Do that rather
than trusting an estimate from here; this repo has never run the full split with a
model, so any number in this document would be a guess.

**Do not pilot with `--limit-dbs`.** It takes the first N schemas alphabetically, and
the obfuscation is not distributed alphabetically: 14 of the 69 schemas are
*identity-rename* (their table and column names were left unchanged), carrying 467 of
2030 questions — and `--limit-dbs 3` selects `address, airline, app_store`, which are
all three of them. A pilot drawn entirely from the un-obfuscated stratum will
understate difficulty and therefore cost, because a curated corpus has much less work
to do when the identifiers already match the question's words. Use an explicit list
stratified across both strata instead:

```bash
uv run python -m governed_bi.eval.run_datalake --dbs address,movies_4,beer_factory,european_football_2,language_corpus,superhero --replicate curated
```

That is 2 identity-rename and 4 renamed schemas, 166 questions, all median-sized for
their stratum. The same caution applies to reading any *result*: part of what a corpus
buys on a renamed schema is restoring the name↔meaning alignment the obfuscation
removed, which is not the same thing as governed metadata being valuable. If you quote
a delta, check whether it holds in both strata.

**Sizing the two knobs.** They exhaust different things and are deliberately
separate. `--build-workers` runs whole curator builds concurrently — each holds a
Postgres connection *and* a deep-agent conversation, so this is the one to size
against your model provider's rate limit. `--workers` fans out the per-question serve
loop; size it against Postgres `max_connections` minus headroom.

There is a second, smaller cost to raising `--workers`: the retrieval index cache is
per-worker (that is what makes it thread-safe), so each worker builds its own
embedding index over each routed corpus. At `--workers 8` over 69 schemas that is up
to ~550 index builds instead of 69. Each one is cheap next to a model call and they
overlap, so it does not change the shape of the curve above — but it is why the first
few questions on a wide run are slower than the steady state.

The serve loop scales close to linearly, which is measurable rather than hoped for.
Driving the real rails graph over real BIRD questions with a 1.5 s stand-in for model
latency, one graph and connector per thread exactly as the pool does:

| `--workers` | speedup | efficiency |
|---:|---:|---:|
| 2 | 2.00× | 100% |
| 4 | 3.92× | 98% |
| 8 | 7.32× | 92% |

That holds because the per-question CPU outside the model call is ~18 ms against a
model call measured in seconds — roughly 1%. It is worth knowing why: that number was
56 ms until the router stopped deep-copying the whole corpus per question, and
`deepcopy` is pure Python, so it held the GIL. A CPU-bound hot spot on the hot path
does not just cost wall-clock, it caps what raising `--workers` can buy at all. If you
add work to `assemble`, re-measure this table before trusting a high worker count.

If `arms.<arm>.by_error_type` in `summary.json` comes back full of `RateLimitError`,
lower `--build-workers` and re-run — those rows count as crashes and will correctly
block quotability rather than silently deflating a score.

**`--replicate curated` is not optional if you intend to quote a delta.** It serves
one arm a second time so the run can measure its own noise. Without it every
comparison reports a p-value and no resolution, and this project has already
published a null that sat inside the noise. It costs one extra serve pass.

**But be precise about which noise it measures.** It serves the *same corpus* twice, so
the floor is serve-side sampling: decoding, the LLM schema pick, tool-call ordering. It
is recorded as `noise_floor.source: "serve_replicate"` for that reason. It does **not**
measure variance in the corpus, and on this ladder the corpus *is* the treatment — each
`(arm, db)` corpus is a single draw from a stochastic curator agent, n=1. So a delta that
clears `detectable.mde_questions` has cleared serve noise, and says nothing about whether
a second curator run on the same schema would have produced the same corpus.

Nothing in the harness measures that today, and it is the gap most likely to turn a
believed result into a retracted one. To close it you would rebuild one arm for a subset
of schemas under a fresh agent, serve both copies, and take the floor from that pair —
roughly one extra curator pass over 20 schemas plus one serve pass. Until then, treat any
`curated`-or-later delta as bounded below by serve noise only, and say so when quoting
it.

## Step 3 — the counterfactual rungs (separate run)

```bash
uv run python -m governed_bi.eval.run_datalake --arms baseline --workers 8 --oracle oracle_schema,oracle_tables,oracle_tables_padded
```

Run these *after* the fair ladder and read them as headroom bounds, never as system
performance — each is built from the answer key. `oracle_tables_padded` is the
control for `oracle_tables`: same gold tables, padded back to a comparable count with
non-gold tables, so table *identity* varies and size roughly does not. Skip any row
where `oracle_padding_degenerate` is true.

`--workers` applies to the rungs. Each worker gets its own solver, connector and
graph cache, the same isolation the fair arms use; the per-worker graph cap is divided
by the worker count, which holds the total flat up to `--workers 8`. Past that a floor
of 4 graphs per worker takes over and the total does grow (16 workers → 64, 32 → 128) —
the floor is deliberate, since a cap of 1 defeats the reuse that matters.

**Know the size before you start it.** The rungs are served **in addition to**
`--arms`, not instead of it — `--arms baseline --oracle X,Y,Z` is **four** serve
passes, so **8,120 scored turns**, about 80% of Step 2's serve budget, each one an
agent loop. Serving them serially, as this step used to, is the longest avoidable wait
in this document.

`oracle_base` is the **last** arm in `--arms`, and the rungs narrow *that* arm's
corpus. The command above passes `--arms baseline`, so what it measures is baseline's
headroom.

**To bound the top of the ladder instead — `--arms curated_sme` — budget for a full
build.** Corpora live inside the run directory, so a separate run rebuilds from
scratch: naming `curated_sme` turns on both the curator and the SME round, which is an
LLM curator pass *and* an SME pass over every schema in the pool, on top of the four
serve passes. That is not a cheaper variant of the command above; it is closer to a
second Step 2.

## Where the wall-clock goes

Two knobs, and they bind on different things. `--build-workers` runs whole curator
builds concurrently — each holds a Postgres connection *and* a deep-agent
conversation, so it is the one to size against your model provider's rate limit.
`--workers` fans the per-question serve loop.

The build is not doing redundant work: `seeded` is the `curated` code path with the
agent switched off (no model calls), and both SME arms build *on top of* the finished
`curated` corpus rather than from scratch, so the default ladder costs one curator pass
and one SME round per schema — not one per arm.

**Arms serve one after another, and that costs nothing.** Each arm has 2030 questions
against at most a few dozen workers, so every arm saturates the pool on its own;
overlapping them would not raise utilisation, it would only raise the peak request rate
against the same rate limit.

**The one structural win left is not taken.** `baseline` and `seeded` need no model to
build, but they still wait for the whole curator phase before serving, because build
and serve are strictly sequential. Interleaving them would hide the curator phase
behind those two arms' serve time. It is deliberately not done: it means restructuring
the phase boundary that resume, the stage-event stream and the per-build staging roots
all key on, and that is not a change to make immediately before a paid run. If the run
is a long pole for you, that is the first thing to build afterwards.

So the honest advice is: raise `--build-workers` until you see `RateLimitError` in
`arms.<arm>.errors.by_error_type`, then back off one step. The binding constraint is
your model provider, not this code.

## Step 4 — read the artifacts in this order

1. `runs/index.jsonl`, last row. Prefer `ledger_ok` / `hygiene_ok` (aliases of
   `quotable`). If false, read `not_ledger_ok_because` / `not_quotable_because` and
   stop. When you do quote a number, quote the `EX*` column, which is the
   pre-registered headline named by the row's own `headline_rate` (`ex_no_twin`
   since 2026-07-30). `EX_bird` beside it is `ex_lenient`, kept because it is the
   denominator published BIRD numbers use, and explicitly not the headline. A `!`
   next to a value means that arm recorded unstamped twin rows, so its strata are
   not trustworthy. Every reason there is a thing that makes the numbers mean something other than
   what they appear to mean. **`ledger_ok: true` is hygiene only** — never treat it as
   `claim_ready`. The ledger always keeps `claim_ready: false` and lists
   `claim_ready_requires`; the checklist below is the claim gate. The field
   `arithmetic_floor_questions` is the Holm family floor for *this* arm count (four
   arms → 8, five → 9), not a sufficiency test for publishing.

   If the run ended on a `PermissionError` from the ledger write rather than a row,
   nothing is lost — `summary.json` and `manifest.json` are already on disk and the
   append is idempotent. Re-index it:

   ```bash
   uv run python -m governed_bi.eval.index --add runs/datalake/<timestamp> --quiet
   ```

   Pass `--quiet` unless you want the whole table: the rendered comparison block is
   O(n²) in the number of records, and at ~120 records it is several thousand lines.

   The ledger accumulates every run anyone makes on the box, smoke tests included. To
   get it back to runs you can actually inspect:

   ```bash
   uv run python -m governed_bi.eval.index --prune --prune-outside-repo --reindex --quiet
   ```

   `--prune` drops records whose directory is gone, `--prune-outside-repo` drops
   scratch runs written outside the repo, and `--reindex` recomputes every surviving
   verdict under the current gates — a record written before a gate existed was never
   judged by it.
2. `summary.json` → `treatment_divergence`. Did the arms actually deliver different
   context? An arm pair that delivered identical context is one experiment run twice.
3. `summary.json` → `comparisons[]`. Read `reading` before `net_questions`. Then
   `p_value_holm` (not the raw `p_value` — four arms is six tests) and the `cluster`
   block, which treats each database as one observation instead of each question.
   Every entry says what it is: `single_variable` false plus a `bundles` list means
   the delta covers more than one intervention, and `ladder_descending` means the
   pair is alphabetical rather than ladder-ordered so its sign reads backwards. Both
   are on the entry itself; you do not need to cross-reference the `deltas` block.
4. `summary.json` → `arms.<arm>.errors` for where the wrong answers went, and
   `arms.<arm>.errors.by_result_shape` for whether a wrong answer came back empty or
   came back with gold's row count and different contents.
5. `summary.json` → `arms.<arm>.by_db.<db>` when a run-wide number needs explaining.
   Each database carries the **same** diagnostic block as the arm — EX, routing
   recall, `cond_ex_given_routing`, outcome and crash breakdowns, the error taxonomy,
   cost — because it is the same function over that database's rows. So a pooled
   number is always decomposable: if `routing_recall` is 0.71 run-wide, the per-db
   block says whether that is a few schemas the router cannot see at all or a thin
   miss everywhere, and those need different fixes. Pair it with
   `comparisons[].cluster.dbs_worse`, which names the databases that regressed but
   not why.

6. `summary.json` → `deltas.*_usd_per_added_correct`. What each rung's extra correct
   answers cost. The ladder's mechanism is that later rungs inject more context, and
   context is billed, so a rung that buys accuracy buys it with tokens. Read alongside
   `deltas.*_usd` (what the step cost in total) and `deltas.*_correct_answers` (what it
   bought). When pricing is refused, `deltas.*_not_priced_because` says why in words.
   Read that rather than matching against a list here — `price_verdict` in
   `eval/run_datalake.py` is the enumeration, its outcomes are published as
   `PRICE_VERDICT_TAGS`, and a test asserts every one is reachable. Two earlier attempts
   to restate the list in this document both went stale within a commit.

   **`*_correct_answers` is paired-only.** Pricing and the canonical gain field both
   require **identical question-id sets** on the two arms (not merely equal N). When
   the pools differ, `*_correct_answers` is `null` and
   `*_correct_answers_unmeasured_because` names the reason; a raw `n_correct`
   subtraction may still appear as `*_unpaired_n_correct_delta` under that
   unmistakable name and must not be quoted as answers gained. Prefer paired
   discordant gains in `comparisons[]` when you care about which questions moved.

   **A step that lost answers is priced under `deltas.*_usd_per_lost_correct`,** not
   under `_usd_per_added_correct`, and only when the cost totals cover every row. That
   split exists because the sign of a per-added-answer figure is uninterpretable once the
   denominator goes negative: a rung that lost 10 answers *and* got cheaper — an
   over-cautious layer refusing more, since refusals are cheap and wrong — used to price
   at **+0.05**, reading as "5 cents per additional correct answer" for a regression. If a
   regression's coverage is partial the key is *absent*, not `null`, so grepping for it
   finds nothing; `_not_priced_because` is what tells you.

   Signs, now that the two keys are separate:

   | key | when present | negative means | positive means |
   |---|---|---|---|
   | `_usd_per_added_correct` | the step gained answers | gained **and** got cheaper — the best case | gained, and paid for it |
   | `_usd_per_lost_correct` | the step lost answers | lost answers **and** got cheaper | lost answers **and** paid more — the worst case |

   So each key's sign is now interpretable on its own; what you cannot do is compare signs
   *across* the two keys, because a negative figure is the best case under one and not the
   other. Read `_correct_answers` first to know which key you are looking at.

7. `summary.json` → `arms.<arm>.ex_by_tier`, `ex_by_semantic_assurance`,
   `graded_delivery_rate`, `safety_clearance_rate`. **EX alone cannot support the claim.**
   The benchmark exists to show that governed metadata improves answers, and reliability
   is graded on `semantic_assurance` — so an arm that raises EX while shifting mass from
   `unflagged` toward `unverified` or `none`, or by delivering more answers below the assurance bar
   (`graded_delivery_rate` up), has traded governance for score rather than improving the
   product. Each is also reported as a ladder delta, so the trade is visible per step
   rather than only per arm.

   The two `ex_by_*` conditionals replace the raw `by_tier` / `by_semantic_assurance`
   counts for this reading: the counts show only how mass is distributed, while the
   conditionals show whether the levels rank correctness at all (if `unflagged` does not
   out-score `heuristic`, the stamp is decoration). Read them **within** an arm only —
   the split is on an output of the system, so comparing a stratum across arms is
   post-treatment selection, not an effect. See
   [measurement](../measurement.md#the-conditional-metrics-are-observational-not-causal).

   Read the `n_*_observed` denominators beside the rates. A rate is `null` when nothing
   recorded the field, which is different from a rate of `0.0`. The three boolean rates
   are conditioned on **delivery** — the rows that handed back SQL — because a refusal
   stamps `safety_clearance=False` and delivers nothing, so averaging refusals in made a
   heavily-refusing arm look like the best-governed one. Refusal behaviour is
   `refusal_rate`'s job.

   **These three are the one part of the summary no offline run exercises.** A
   an `--oracle-only` / refuse-all path refuses everything and stamps none of them; `oracle_sql` stamps
   `tier` and `semantic_assurance` but not the booleans. So the first real run is the
   first time they carry values — check `n_*_observed` is non-zero before reading the
   rates, and treat a `null` there as "the instrumentation did not reach this path"
   rather than as a result.

8. `summary.json` → `arms.<arm>.serve_index` / `serve_started_utc` / `serve_seconds`.
   **Arms serve sequentially, not interleaved.** On a scale run that is hours between the
   first arm and the last, against a hosted provider, so any drift in provider behaviour
   maps monotonically onto the ladder and is indistinguishable from a rung's effect.

   The harness does not remove this — interleaving per question would restructure the
   serve loop, the per-arm generations files and the resume contract — so it records the
   position instead. Check whether EX tracks `serve_index` rather than the ladder before
   believing a monotone result. Two things bound the risk: the replicate is served
   **last**, maximally distant from the arm it replicates, so the noise floor already
   absorbs drift across at least one arm's serve rather than being a within-moment
   figure; and the cluster test treats each database as one observation, which a
   run-wide drift affects uniformly.

## The result that would make us abandon the corpus thesis

Stated before the run, because a harness that can only return one answer is not an
experiment (AUDIT C8). **One sentence:**

> If `seeded → curated` on the **test** split, over the twin-free stratum, at
> 69 schemas, yields a paired McNemar point estimate below **+2.0 EX points** with a
> Holm-adjusted *p* > 0.05, across **three independent curator draws** (three builds
> from the same inputs, different agent seeds), then the LLM curator does not earn
> its cost and the corpus-as-moat claim fails at this scale.

Every term is mechanical and already computed:

| Term | Where it comes from |
|---|---|
| arm pair | `seeded → curated` — the rung that isolates the LLM curator from the free deterministic pass. Not `baseline → curated`, which bundles two mechanisms. |
| metric | `comparisons[].net_questions` / `ex_lenient` delta, paired (`eval.analysis`), never a point-estimate difference across runs |
| stratum | the twin-free stratum (§Quote the twin-free stratum) — gold-SQL twins in train make recall and generalisation indistinguishable |
| effect size | +2.0 EX points. Below the ~+1.6-point band the MDE machinery treats as resolvable, a "win" is not separable from decoding noise. |
| curator draws | three. `n=1` on a stochastic agent is a sample of one from the treatment distribution, which `power.py` says in its own docstring. |

### Hard MDE bound on the SME step (read before quoting SME)

Measured on the 20260730 fixed2 run: 31 byte-identical `context_hash` pairs flipped
`correct` on 4 questions (12.9%); full-split discordance **122/1351 = 9.03%**;
paired SE ≈ 0.0082 → 80% power **MDE ≈ 2.3pp**. The SME step under debate is on
the order of **0.2pp**.

> Under a ~9.03% noise floor (MDE ≈ 2.3pp), no affordable N resolves a 0.2pp SME
> step. `--replicate` only licenses **「未检出」**, not **「无效果」**.

That is a conclusion, not a soft caution. A paid run that clears serve-replicate
gates still cannot promote a sub-MDE SME delta into "SME does nothing" — only into
"not detected at this budget".

Two honest caveats on the abandon criterion above:

- It is a statement about **this benchmark at this scale with this model**, not about
  semantic layers in general. A null result here would not show that curation cannot
  help; it would show that *this* curator, on obfuscated BIRD, does not pay for
  itself — which is the decision the project actually faces.
- The MDE it leans on is derived from **serve** noise (re-serving one corpus) while
  the treatment is **corpus-level**. That bound is therefore optimistic: it measures
  decoding variance, not curator variance. The three-draw requirement exists to cover
  the gap, and until a three-draw run exists the criterion cannot be evaluated.

The quotability gates enforce the symmetric half of this: a run whose correct rows are
mostly free passes (empty gold, no `FROM`, zero table overlap) is now non-quotable, so
a flattering result can be disqualified the same way a crashed one can.

## What the ladder does and does not tell you

Each step is **adjacent**, which is not the same as changing one thing — see
`mechanisms_changed` on every comparison, and `single_variable`, which now means
exactly one mechanism rather than merely one rung:

| Step | What it adds | What its delta means |
|---|---|---|
| `baseline → seeded` | Train-SQL-derived joins and metrics, decoy / negative-space marking. Also *drops* baseline's naming-convention FK guesses. **No LLM, no few-shots.** | Multi-mechanism: train gold SQL's joins/metrics **and** its negative space (plus dropping FK-name guesses). **Not** a parsing-only or few-shot estimate. |
| `seeded → curated` | The LLM curator agent (few-shots included), on top of that same seed. | What the curator LLM adds over the free deterministic pass. |
| `curated → curated_sme` | The Simulated-SME clarification round. | **Confounded — see below.** |

`baseline → curated` is *not* reported as a step, because it bundles the first two
and cannot say which paid. If you drop a rung with `--arms`, the resulting compound
step is still reported but labelled with what it bundles — on the pair itself as
`comparisons[].bundles`, in `deltas.*_bundles`, and on stdout. All three come from
the same function, so they cannot disagree.

**The SME confound.** The SME's brief is built from BIRD's human-authored
`database_description/*.csv`, and the curator never sees that directory. So a
positive `curated → curated_sme` delta is as consistent with "we handed the pipeline
a new knowledge source for the first time" as with "the clarification protocol
works". To split it, add the opt-in rung:

```bash
uv run python -m governed_bi.eval.run_datalake --arms baseline,seeded,curated,curated_sme --build-workers 6 --workers 8 --replicate curated
```

**`curated → curated_sme` bundles two mechanisms and cannot be split**, so on its
own it does not support the claim the benchmark exists to make. A
`curated_sme_blind` rung existed for exactly this and was removed 2026-07-28: it
briefed the SME on train questions and evidence, which Phase A already has, so it
compared the curator against itself re-asked through a Q&A round-trip. Splitting the
confound needs a knowledge source the curator lacks and a simulated SME does not
supply. Until then, report the SME delta with its two mechanisms named —
`single_variable` is `false` on that step and `mechanisms_changed` says why.

## Before quoting anything

- [ ] `oracle_sql` was run and its EX is known. Every other number is read against it.
- [ ] The last `runs/index.jsonl` row says `ledger_ok` / `hygiene_ok` / `quotable: true`
      (aliases for the same artifact-hygiene gate). That is **not** claim readiness:
      the ledger always sets `claim_ready: false` and lists `claim_ready_requires`.
      Clear hygiene, then walk this checklist.
- [ ] The number you are about to quote is the row's `headline_rate` (the `EX*` column),
      not `EX_bird`. If you have a reason to quote `ex_lenient` instead, state that you
      are quoting the BIRD-comparable denominator rather than the pre-registered
      headline, and give both. Silently switching between them is the post-hoc selection
      X11 existed to prevent.
- [ ] The run had a `--replicate` arm, so `comparisons[].reading` says what it could
      resolve rather than "no noise floor measured".
- [ ] The delta you want to quote clears `comparisons[].detectable.mde_questions`
      (per comparison, not a top-level field; the floor it is read against is
      `comparisons[].noise_floor`). If `from_zero_discordance` is true, the floor is a rule-of-three bound, not a
      measurement — treat it as the weakest claim in the report. And note the floor is
      `serve_replicate`: it bounds serve-side sampling, not corpus-build variance, so on
      any `curated`-or-later step it is a floor on the wrong quantity. Say so when you
      quote the delta.
- [ ] `p_value_holm` is below 0.05, not just `p_value`.
- [ ] The `cluster` block agrees. A question-level win the database-level test cannot
      see is carried by a handful of schemas; name them rather than averaging them.
- [ ] `treatment_divergence` shows the arms in that comparison actually differed.
- [ ] The step you are quoting says `single_variable: true` on its own
      `comparisons[]` entry. If it carries a `bundles` list, the delta cannot be
      attributed to any one of the things in it. If it says
      `ladder_descending: true`, the pair is alphabetical rather than ladder-ordered
      and `net_questions` is signed backwards relative to "did this rung help".

      **`single_variable` means "adjacent rung", not "one mechanism".** It is computed
      from ladder adjacency alone (`arms.skipped_rungs`), so it is `true` on every
      adjacent pair including `baseline → seeded` — which in fact *adds* train-derived
      joins and metrics, *removes* baseline's naming-convention FK guesses, and *adds*
      a train-conditioned column mask that flags every column train gold never touched
      as suspect. That mask covers the gold columns of ~86% of test questions. So
      `baseline → seeded` measures "what train gold SQL is worth, including its negative
      space", not "what parsing training SQL is worth". `seeded → curated` is the step
      that genuinely isolates one thing: the curator agent, same code path with the
      model switched on.

## Resuming

```bash
uv run python -m governed_bi.eval.run_datalake --resume-from runs/<dir> \
  --arms <the original arms> --dbs <the original dbs> --oracle <the original rungs> \
  --replicate <the original replicate> \
  --limit <the original limit if any> --limit-dbs <the original limit-dbs if any> \
  --build-workers 6 --workers 8
```

**Repeat every scope flag.** `--arms`, `--dbs`, `--oracle`, `--replicate`, `--limit`,
and `--limit-dbs` are read from the command line, not from the directory, so omitting
one does not mean "keep what was there" — it means "use the default". Omitting `--arms`
on a `--arms baseline` run picks up all four defaults; omitting `--dbs` on the
stratified pilot widens the pool from 166 questions to all 2030. The run **refuses** a
resume whose scope differs from the manifest rather than quietly obeying, so a wrong
resume costs you an error message instead of a curator pass over 69 schemas. The flags
are recorded in `manifest.json` under `arms` / `db_ids` / `oracles` / `replicate_of` /
`limit` / `limit_dbs` — read them from there if you no longer have the original command.

Questions already in `generations.<arm>.jsonl` are replayed, not re-served. Changing
a prompt variant between the original run and a resume is fatal and refused, and so
is changing from `--oracle-only` to a paid model run — **a step 1 smoke directory is not resumable into step 2.**
Its rows are construction-refusals scoring 0, and replaying them would mix them into
a paid arm's denominator. Start step 2 in a fresh directory. Concurrency knobs are
safe to change (they are recorded in the manifest but are not
resume knobs, because per-build isolation makes the width irrelevant to what a row
means).

**Build completeness.** Resume, staging seed, skip, and promote treat a durable
`BUILD_COMPLETE.json` under `<db>/_build/` — not "any `*.yaml`" — as finished.
Partial YAML without that marker is debris and is rebuilt. With `--build-workers > 1`,
staging is cleared at the start of every build and only a promoted complete build is
trusted.

**Promote and generations rewrite.** Promote installs a finished db by same-filesystem
swap (incoming → live, with the prior tree moved aside and removed only after the new
tree lands), so a kill mid-promote leaves either the old or the new corpus, not an
empty hole. Crash-row resume rewrites `generations.<arm>.jsonl` through an atomic
temp + replace, so a kill mid-rewrite keeps the previous file.

**Code SHA on resume.** After a code edit, resume is refused on `git_sha` drift
(always fatal as of M3 N10 — the smoke warn / `--allow-git-sha-drift` dual track is
gone). Use a fresh `--out`. The ledger still marks a run unquotable when rows span
more than one SHA.

**Push the branch before a quotable run (M4 N13).** The manifest records
`git_branch`, `main_git_sha`, `dirty`, and `diff_sha256` as operational fields.
A run whose tip was never pushed cannot be checked out elsewhere — treat an
unpushed tip as not quotable, even when `dirty` is false.

## Quote the twin-free stratum

**182 of the 1627 gradeable test questions (11.2%) have a gold statement that already
exists in that schema's train split, identical once literals are blanked.** That is the
denominator to quote. `seeded` derives its seed from train gold SQL and `curated` runs
an agent over train, so on those questions an EX gain is consistent with recall as well
as with generalisation, and EX cannot tell them apart. The rate is not uniform —
`student_loan`, `university` and `video_games` are the worst — so a per-schema result
can be largely twins.

The denominator is the **gradeable** rows, not all 2030. Frozen `VALUES(...)` gold all
collapses onto one canonical shape once the constant is blanked, so those rows twin each
other trivially; they are already outside `ex_gradeable` and can never reach either
stratum. The 25 order-sensitive questions come out for the same reason, so this
denominator is exactly `ex_gradeable`'s. An older unfiltered recount over the full test
split was **246/2030**; that figure is historical and must not be mixed into gradeable
claims.

The id-level disjointness check (`leakage.train_test_disjoint`) does not see this. It
proves no question *id* is in both splits, which is true and is a different claim.

**The pipe is direct, not hypothetical.** `curated`'s few-shot assets are literal
(train question, gold SQL) pairs — `curator/pipeline.py` hands the agent `id / Q /
evidence / sql` triples and `asset_bag.upsert_few_shot` persists them. (`seeded` has
none of these.) At serve time
`retrieval/rvgd.py` selects few-shots by relevance to the *test question text*, and
then expands retrieval to the tables the chosen exemplar's SQL names. So a twin does
not merely supply a shape to imitate: retrieving it also fixes table selection. That
is a working nearest-neighbour lookup from a test question to a train twin's answer.

Note also that the strict twin rate is a **floor** on exposure, not the whole of it.
Loosen the definition and it climbs steeply — questions sharing a table set *and* a
query shape are a majority of the split, and questions merely sharing a table set are
almost all of it. Which means the honest reading is comparative rather than absolute:
if the delta is flat across the twin and twin-free strata, that disfavours recall; if it
concentrates in the twin stratum, it is recall. Say which one the numbers show.

Every run now stamps `gold_twin_in_train` per row and reports both strata. Twin rates
and `comparisons[].no_twin` require **full stamp coverage** on the scored rows: any
unstamped row (`n_twin_unstamped > 0`) makes the strata `null` rather than emitting a
rate over a stamped subset while pooled metrics still include the rest.

| Field | Read it as |
|---|---|
| `arms.<arm>.ex_no_twin` | **the defensible headline** — the curator had nothing to recall |
| `arms.<arm>.ex_twin` | the recall-flavoured stratum, worth reporting, not the claim |
| `comparisons[].no_twin` | the same paired test restricted to the twin-free stratum |
| `leakage.structural_gold_twins` | the rate, the worst schemas, and the per-schema counts |

Check `comparisons[].no_twin` separately rather than assuming it follows the pooled
result. Dropping 11% of the split widens the interval, so a delta can stop being
resolvable on the stratum — and if it does, the honest statement is that the effect is
not established on questions the pipeline could not have recalled.

Two labels on that block, both true and both easy to misread past: `p_value_is_raw`
(Holm runs over the top-level family only, so do **not** compare this p against the
pooled `p_value_holm` — that comparison is biased toward "the effect survives") and
`floor_from_full_split` (its noise floor and MDE come from the full-split replicate, so
they describe a population that still contains the twins; conservative, but not the
stratum's own resolution). `n_twin_unstamped` is non-zero on a partial resume across
the stamp boundary or any other incomplete stamp set — the strata read `null` there
rather than silently becoming the pooled numbers.

This is not a gate. Twins are a property of the benchmark, and refusing to score them
would discard an eighth of the split and change the denominator every published BIRD
number uses.

## Known limitations to state alongside any result

- **The eval does not exercise the product's entry point.** Scored runs drive
  `build_serve_rails` directly. The HTTP `/chat` route and the LangGraph server graph
  are never exercised, and neither is the narrator's LLM call, the SQL cache
  (unconstructed on every path), working memory, HITL clarification, or run logging.
  EX measures the analyst core, not the deployed system.
- **The eval's routing configuration is reachable in production, but is not the
  default.** `schema_route_top_k`, `schema_route_llm_pick` and
  `schema_pick_max_columns` are now read from a `[routing]` table, so a deployment
  *can* run what the benchmark measured — but only if it is configured to. The
  dataclass defaults are still shortlist@3 with no LLM pick, while the pooled eval
  defaults to shortlist@10 *with* the pick. If you quote a data-lake number, say
  which routing configuration it was measured under, and put the matching
  `[routing]` block in the deployment's TOML:

  ```toml
  [routing]
  top_k = 10
  llm_pick = true
  pick_max_columns = 12
  ```
- **`curated` contains no notes.** The only `NoteAsset` producer in the system is
  `AssetBag.record_caveats`, reached only from the SME build. The curator agent has no
  note-writing tool. So the notes machinery is exercised only by the SME arms.
- **The few-shot leakage guard is inert.** Nothing populates `source_refs` and no call
  site passes `train_refs`. What actually protects the split is the call site and the
  disjointness assertion, both sound but both enforcement-by-construction.
