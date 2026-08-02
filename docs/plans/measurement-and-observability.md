# Measurement and observability — work plan

Opened 2026-08-01, after the three-model ladder and the worktree intake.

The ladder gave us EX numbers. It did not give us the two things needed to *raise*
them: a trustworthy ceiling to read them against, and a durable record of what the
agent actually did on each question. Everything below is aimed at those two gaps.

Status legend: **[ready]** runnable today, no code · **[small]** hours ·
**[medium]** a day or more · **[open]** needs a decision first.

---

## Why this, and not tuning

The measured pools, from the ladder:

```
EX|pick            64.1% (Opus) / 58.4% (luna) / 55.8% (deepseek)
                   → a third of correctly-routed questions still fail

error taxonomy over 563 wrong rows
  sql_generate  251     schema right, tables right, SQL wrong   ← largest
  schema_pick   131
  table_select  101
  gold_unusable  69     ← WRONG; the real figure is 4 (see A1)

within sql_generate
  wrong_projection   63%    ← column selection dominates
  wrong_aggregation  36%
  wrong_filter_literal 31%
  wrong_filter_column  30%
```

Three models across two vendors, two reasoning-effort settings, and one generation
gap (Opus 4.8 → Sonnet 5) all land in the same band. **Swapping models and raising
effort has not moved the ceiling.** The remaining leverage is in what we feed the
model and in knowing which part of the pipeline is actually costing us — neither of
which we can currently see.

---

## A. Three zero-cost measurements

### A1. `oracle_sql` — what does the grader score *gold* at? **[DONE 2026-08-02 — 99.70%]**

**Result: 1347 / 1351 = 0.9970.** Run dir `runs/datalake/oracle-sql/20260802T002412Z`.
No model, no API key, ~4 minutes.

The grader is sound. There is no meaningful grading gap hiding under the EX numbers,
and **56.3% should be read against ~100%, not against some lower hidden ceiling.** The
whole 43pp gap is real failure. This is the good version of the answer, and it makes
every downstream number more actionable, not less.

The four misses:

| question | db | cause |
|---|---|---|
| train_6763 | retails | `gold_unusable: ResultSetTooLarge` — gold returns >200,000 rows |
| train_6833 | retails | same |
| train_6845 | retails | same |
| train_8505 | mondial_geo | **grader bug** — see below |

The three `retails` misses are a *harness* cap (200k rows), not defective gold. Debatable
whether the cap or the question is at fault; either way it is 3 questions.

`train_8505` is a real defect. The submitted SQL is **byte-identical to gold**, both sides
return 16 rows, `nrows_match=True`, `gold_order_sensitive=False`, `error=None` — and it
grades `correct=False`. A query cannot differ from itself. Something in the comparison
(most likely row-order normalisation under `SELECT *`) is not doing what its flags claim.
One question in 1351, so it changes no headline, but it is a live bug in the instrument
and worth fixing now that it has a reproducer.

**Correction to a figure quoted earlier in this plan and in conversation:
`gold_unusable` is 4 questions, not 69.** Verified directly on the Opus curated arm:
591 wrong rows = 560 answered-but-wrong + 27 refusals + **4** gold_unusable, and the same
4 question ids in every arm. The "69 unwinnable questions" claim overstated that bucket by
roughly 17×. The unwinnable pool is negligible; essentially all of the gap is winnable.

<details>
<summary>original plan text (kept for the record)</summary>

Skip the model; submit gold SQL to the grader. Anything below 1.0 is a grading gap —
frozen constant, stale hash, normalisation quirk — and it is the true ceiling every
other number should be read against.

```bash
uv run python -m governed_bi.eval.run_datalake --oracle-only --bird-dir ../BIRD-Data-Obfuscation
```

Verified: `--oracle-only` defaults to the `oracle_sql` rung, needs no model and no API
key, and forces `effective_workers` to 1 without one. No code to write.

We assumed the ceiling was well below 1.0 and did not know whether it was 0.95 or 0.81.
Reading 56.3% against 0.81 would be a different result from reading it against 1.0.
*(Answered: 0.9970.)*

**Run this first.** It is free and it may re-scale every conclusion on this page.

</details>

### A2. Column-level recall **[small]**

Nothing in `src/` or `scripts/` measures columns — `gold_column`, `column_recall`,
`columns_used` all return zero hits. Table-level recall has been measured all along;
the column level, where `wrong_projection` lives, never has.

[`eval/retrieval_eval.py`](../../src/governed_bi/eval/retrieval_eval.py) already walks
gold SQL with sqlglot to extract table names (`gold_table_ids`). Columns come out of
the same traversal.

Three numbers, not one:

| metric | answers |
|---|---|
| corpus column coverage | does the corpus even contain the columns gold needs (curation gap)? |
| licensed column recall | after routing + licensing, are gold's columns inside tables the agent can see? |
| width distribution of gold-bearing tables | **the "wide table" question, directly** |

The third is the point. It turns `Settings.analyst_max_table_columns` from a knob with
p=0.23 observational support into a readable curve: *cap at 40 → drops gold columns on
N questions; cap at 20 → drops them on M*. That is what decides whether the A/B is
worth paying for.

Offline, no model, no cost.

### A3. Persist the tool calls **[small]**

Every tool call's full detail is already computed in
[`analyst/agent.py::_resolve_tool`](../../src/governed_bi/analyst/agent.py) — the
search query and its hit counts, the inspected `table_id` with its column count and
whether it licensed, the SQL with its guardrail verdict and layer and row count.

Then `GovEventStream._emit_event` opens with:

```python
if self._on_event is None:
    return
```

and the eval path's `ServeDeployment(...)`
([`eval/arms.py:461`](../../src/governed_bi/eval/arms.py)) passes no `on_event`. So on
every eval question the detail is computed and dropped.

The fix is not a callback. `StageRecorder` is `GovEventStream`'s *durable counterpart*
(its own docstring says so), and `Stage.search_corpus` / `Stage.inspect_schema` /
`Stage.sample_rows` are **already declared** in the enum with the comment "declared but
nothing emits them yet". Writing a stage record from `_resolve_tool` puts the tool calls
into `stage_events.jsonl` with `question_id` / `arm` / `db_id` / `run_id` / `turn_id`
already attached, for free.

Confirmed absent today — the last full ladder's `stage_events.jsonl`:

```
guardrail 9359   execute 8980
route / schema_pick / retrieve / assemble / agent_core / narrate   5404 each
```

Not one tool-call row.

**Correction to an earlier claim in this plan:** `generations.*.jsonl` does *not* carry
`n_tool_calls` as a bare scalar. It is a per-tool histogram:

```json
"n_tool_calls": {"search_corpus": 7, "inspect_schema": 5, "grep_notes": 1}
```

So counts per tool per question already exist. What is missing is the **ordered trace
with arguments and results** — which table was inspected, whether it licensed, what the
search query was, what came back. That is what A3 adds.

The histogram alone is already informative, and says something surprising (luna ladder,
mean calls per question):

| arm | run_query | search_corpus | sample_rows | inspect_schema | grep_notes |
|---|---|---|---|---|---|
| baseline | 2.0 | 1.9 | 1.8 | 0.4 | 0.3 |
| seeded | 1.7 | 1.6 | 1.1 | 0.1 | 0.2 |
| curated | 1.5 | 0.8 | 0.2 | **0.0** | 0.1 |

On the curated arm the agent **essentially never calls `inspect_schema`** — it writes SQL
straight off the assembled context. Whether that is the context doing its job or the
agent failing to verify is exactly the question A3's trace would settle.

---

## B. Local logging

### What already exists

[`analyst/run_log.py`](../../src/governed_bi/analyst/run_log.py) **is** the SQLite
store (ADR 0004): idempotent upsert keyed by `turn_id`, Tier A/B/C content gating,
30-day TTL pruning, permission tightening. Roughly 1100 lines, complete. Nothing needs
rebuilding.

It is switched off on the eval path by one line
([`eval/arms.py:456`](../../src/governed_bi/eval/arms.py)):

```python
log_settings = settings if enable_run_log else dc_replace(settings, run_log_kind="off")
```

`enable_run_log` defaults to `False` and `run_datalake` never passes it — there is no
flag and no plumbing.

### Why not simply switch it on

**Write contention.** `_upsert_sqlite` opens a connection and commits on every call.
Sixteen concurrent workers against one SQLite file produces `database is locked`, and
it would surface two hours into a paid run.

**Wrong shape.** The schema is `run_log(turn_id PRIMARY KEY, payload TEXT, updated_at)` —
one JSON blob per turn. "Show every `inspect_schema` call where `licensed=false`" means
`json_extract` over blobs. Workable at 5404 rows, but it is not an analytics schema, and
this store is *governance machinery with privacy tiers*. It should not be repurposed.

### Proposed shape: JSONL while running, SQLite after **[small]**

```
during the run   stage_events.jsonl        per-worker append, no lock contention
                 generations.<arm>.jsonl   (already exists)
after the run    scripts/load_run_db.py →  runs/<ts>/run.sqlite
UI               reads run.sqlite
```

Append-only JSONL is contention-free, and its durability is not hypothetical: during the
2026-08-01 worktree intake the `generations` files were lost and `stage_events.jsonl`
alone was enough to reconstruct the full crash diagnosis. SQLite is the **export**
format, not the runtime format.

A wide-table schema is enough:

```sql
events(run_dir, arm, db_id, question_id, turn_id, seq,
       stage, status, ms, detail_json)     -- every rail step and every tool call
turns (run_dir, arm, question_id, correct, outcome, failed_stage,
       pick_hit, routed_hit, n_tool_calls, tokens, cost, generated_sql, gold_sql)
```

With that, "do failing questions call `inspect_schema` less often" is one query.

---

## C. The experiment-inspection UI **[open — decision needed]**

[docs/viz.md](../viz.md) states plainly: **"This repo ships no bundled UI."** The repo
ships `presenter` view models and the HTTP API; the interactive UI lives in
`../governed-bi-ui`.

What is wanted here is a different artifact from that one. `../governed-bi-ui` is a
**chat frontend** consuming a live stream. This is an **experiment inspector**: after
1351 questions have run, open one of them and read the whole tool trajectory — what the
agent had available at each step, what it called, what came back. Offline, read-only,
single-machine.

| | approach | cost |
|---|---|---|
| **A** *(recommended)* | local tool under `scripts/`, reads `run.sqlite`, single-file HTML or streamlit | breaks the letter of no-bundled-UI, but it is a dev tool, not a product surface |
| **B** | extend `../governed-bi-ui` with an experiments page | keeps the rule; cross-repo, needs an API, much slower |
| **C** | no UI — ship `run.sqlite` plus query scripts | fastest, but drops the "visualise the toolset" goal |

Argument for A: viz.md itself calls the audit surface's *reading* engine-adjacent — a
dev/audit tool over the corpus. An offline experiment inspector sits on that side. Going
cross-repo turns this from a day into a week, and the current bottleneck is *not being
able to see the data*, not the absence of a frontend.

**Not yet decided.**

---

## D. LangSmith

The only tracer since 2026-08-02 (D20). Already wired, zero code
([`obs.py`](../../src/governed_bi/obs.py)):

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
```

Traces upload inputs and outputs verbatim, result rows included. That is the
decision, not an oversight: BIRD is public, obfuscated data with no real PII, and
sensitive columns are filtered at the datasource before they can reach a tool
message. There is no mask hook and no acknowledgement env var — both went with
Langfuse. Not a habit to carry to a real customer, which would need a masking
layer at this seam.

Billing is **one trace per root invocation**, so a whole agentic turn — dozens of
nested runs — is one trace. A 20-question debug pass costs 20 of the 5,000/month
free tier.

`tracing_config()` stamps `run_id` / `turn_id` / `arm` / `schema` /
`corpus_pin` / `corpus_content_hash` / `prompt_set_hash` into `metadata`, and
`arm` / `schema` into `tags`; LangSmith reads both natively, so filtering by arm
and by corpus version works.

> **This paragraph used to be false and it is worth knowing why.** The fields were
> declared on `RunContext` but the eval driver passed neither `arm` nor a corpus
> digest, so every trace of a four-arm ladder carried the single tag `governed-bi`
> — the axis you would most want to filter on was the one axis missing. Worse,
> `corpus_pin` *was* present and reads like a corpus identity while being a mode
> label (`"datalake"` for every pooled run); the manifest's real
> `corpus_content_hash` was absent. Fixed 2026-08-02 and pinned by
> `tests/test_trace_metadata.py`. A field existing on a dataclass is not evidence
> that anything fills it.

**Its role is drilling into one failing question.** It is a trace browser, not an
aggregator; "how many `inspect_schema` calls per question across 1351 rows" belongs in
`run.sqlite`. Use it *after* A1/A2 have identified which questions to look at — opening
it first means scrolling 1351 traces by hand.

---

## E. Experiment cost — the ladder cannot answer the questions we are asking

Running everything end to end is slow and expensive. The useful finding is not that we
should sample smaller: it is that **the expensive run is not buying the answer.**

### What a ladder actually costs

Recomputed from `token_sum` at current prices (the `cost_est_usd` on luna/Opus rows is
stale — it was written under a price table that has since been corrected):

| model | baseline | seeded | curated | curated_sme | **ladder** |
|---|---|---|---|---|---|
| Claude-Opus-4.8 | $557 | $543 | $806 | $875 | **$2,782** |
| gpt-5.6-luna | $13 | $14 | $16 | — | **$42** |
| deepseek-v4-flash | — | $2 | $4 | — | **$5** (2 arms) |

A 66× spread between the cheapest and dearest arm for the *same* measurement.

### What a ladder can resolve

Observed McNemar discordance between adjacent arms, all three ladders:

| run | pair | n_shared | discordant | rate | net | p |
|---|---|---|---|---|---|---|
| opus | baseline→seeded | 1351 | 252 | 18.7% | +86 | 6.5e-08 |
| opus | seeded→curated | 1351 | 269 | 19.9% | +111 | 1.0e-11 |
| opus | curated→curated_sme | 1351 | 122 | 9.0% | −2 | 0.93 |
| luna | baseline→seeded | 1351 | 225 | 16.7% | +119 | 7.4e-16 |
| luna | seeded→curated | 1351 | 268 | 19.8% | +152 | 2.1e-21 |

So the pipeline's discordance rate sits at **16–20%**. Feeding that to
`eval.power.minimum_detectable_effect`:

```
n       MDE @18% discordance
  300   20.6 questions =  6.86%
 1000   37.6 questions =  3.76%
 1351   43.7 questions =  3.23%     ← a full run today
 2030   53.6 questions =  2.64%     ← the ENTIRE BIRD test split
```

And inverted — the sample size required to resolve a given effect at 18% discordance:

```
3.0%  ->  n =  1,570
2.5%  ->  n =  2,261
2.0%  ->  n =  3,533
1.5%  ->  n =  6,281
1.0%  ->  n = 14,132
```

**This is the finding.** A full 1351-question ladder resolves ~3.2pp. Every question in
BIRD test resolves 2.6pp. The interventions on the table — `tblmax` at roughly +1.3pp,
R1 capped at ≤0.9pp, the column cap somewhere under 2pp — are **all below the floor of
the entire benchmark**. Resolving 1.5pp would take 6,281 questions, three times the
benchmark we have.

Spending $42 (or $2,782) to measure a 1.3pp effect does not produce a weak answer. It
produces **no answer**, at full price.

### What follows

**E1. Stop using EX as the readout for small interventions. [medium]**
Measure the thing the intervention actually changes, where it is deterministic and has
no sampling noise at all:

| intervention | current readout | noise-free readout | cost |
|---|---|---|---|
| `tblmax` | EX (MDE 3.2pp) | `shortlist_recall` — no model at all | $0 |
| column cap | EX | column recall + width curve (A2) | $0 |
| picker changes | EX | `pick_hit` — one model call/question, not a serve pass | ~$1–2 |

`shortlist_recall` is a deterministic function of corpus and embedder. `tblmax`'s
0.952 → 0.973 is a *measurement*, not an estimate with a confidence interval. Converting
it to EX is where the uncertainty enters — so report the recall and the conversion
factor separately instead of buying a noisy EX number that cannot see the difference.

**E2. Cost tiers, chosen deliberately. [small — mostly discipline]**

```
tier 0   offline ablation (routing/retrieval)   $0        deterministic
tier 1   pick-only pass                         $1-2      near-deterministic
tier 2   one arm, cheap model, 1351q            $4        +-3.2pp
tier 3   one arm, luna                          $16       +-3.2pp
tier 4   full 4-arm Opus ladder                 $2,782    +-3.2pp
```

Tiers 2 and 4 resolve **the same effect size**. The only thing $2,778 extra buys is a
different absolute EX. Pick the tier from what is being asked, not from habit.

**E3. Stop re-running the ladder. [free]**
`baseline < seeded < curated` has now been reproduced on three models across two
vendors, with agreeing direction and comparable magnitude (opus +86/+111, luna
+119/+152). It is a settled characterisation. A *future* experiment tests one
intervention and needs **two arms — control and treatment — not four.** That alone
halves every run.

**E4. Screen cheap, confirm dear. [free]**
DeepSeek's curated arm cost $3.80 for 1351 questions and agreed with luna on direction.
Use it to screen; spend Opus only on a result that already survived screening.

**E5. Missing: question-id subsetting. [small]**
`--dbs` and `--limit` exist, but `--limit` caps questions *per db* and takes the first N,
so there is no way to say "serve exactly these question ids". Two things need it:

- re-serving only the questions an intervention could plausibly change (e.g. only the
  131 `schema_pick` failures), which turns a 1351-question pass into a 131-question one
- a fixed stratified probe set, reused across experiments, so successive runs are
  comparable rather than each drawing its own sample

**E6. Missing: `--replicate-limit`. [small]**
`--replicate ARM` exists and costs a full extra serve pass. A capped replicate would
measure the discordance rate — which is a *property of the pipeline* and travels, per
`detectable_effect_for`'s own docstring — at a fraction of the cost. The MDE would then
be evaluated at the comparison's population, which is what that function already does
correctly.

### What is already efficient (do not "fix")

- **Resume works.** `--resume-from` replays questions already in
  `generations.<arm>.jsonl` and serves only the rest; corpora are reused unless
  `--no-resume`. `RESUME_DRIFT_KEYS` refusing a drifted resume is correct behaviour.
- **Corpus builds are cached** via `BUILD_COMPLETE`, so curator spend is not repaid on
  a re-run.
- **`--oracle-only`** already runs with no model and forces `workers=1`.

### Fixed in passing

`--help` on `run_datalake` crashed with `TypeError: %c requires int or char` — a bare
`%` in the `--no-serve-breaker` help string, which argparse interpolates. The driver's
entire CLI surface was unreadable. One character (`%` → `%%`).

---

## Proposed order

1. **A1 `oracle_sql`** — free, no code, may re-scale everything below
2. **A3 tool-call persistence** — so runs from here on keep the record
3. **A2 column recall** — the largest error class, and it makes an existing knob decidable
4. **B loader** — `run.sqlite`
5. **C UI** — after the decision, and after there is data to point it at

UI before persistence would be backwards.

E1–E4 are discipline, not code, and apply from the next experiment onward. E5/E6 are
small and should land before any run that would otherwise serve 1351 questions to learn
something about 131 of them.

---

## Open decisions

- [ ] **C**: which UI route — A (local tool here), B (cross-repo), or C (no UI)?
- [ ] **E1**: accept that EX is not the readout for sub-3pp interventions, and report
      the deterministic proxy plus a stated conversion factor instead?
- [ ] **E3**: is the ladder settled — do future experiments default to two arms?

---

## Additions

<!-- room for further items -->
