# Measurement

Run an evaluation arm over the BIRD data lake, read what the run wrote, and
decide whether the number may be quoted.

The entry point is
[`tools/run_datalake_eval.py`](../tools/run_datalake_eval.py). It serves every
question in the dataset through `serve.graph.compile_graph`, grades each answer
against gold, appends a row per question as it completes, and prints a report
over the whole artifact.

That is the same `build_graph` topology the server runs, and it is **not** the
server's graph: `api/graph_app.build_serve_graph` wraps the same nodes with
`accept` in front of `guard` and `record` after `stamp`, which is the trust
boundary a client crosses and an eval question does not. Every stage from
`guard` to `stamp` — the whole measured path — is shared.

Findings produced this way live in [failure modes](failure-modes.md); what they
imply is in [open work](open-work.md).

## Before you run

- A Postgres DSN in `GOVERNED_BI_PG_DSN` or `PG_RENAME_DECOY_DSN`. See
  [usage](usage.md#environment).
- A credential for each model surface you use. The driver checks the agent
  surface, the utility surface, and — with `--embed` — the embedding surface,
  and exits before spending anything if one is missing. The `proxy` provider is
  skipped in that check, because it authenticates itself from a secret it looks
  up at call time.
- A corpus. The default is the sibling checkout `../BIRD-corpus`, resolved from
  the driver's own location rather than from your working directory.
- The dataset. The default is `../BIRD-Data-Obfuscation/eval_dataset`, and the
  driver reads `test_final.jsonl` from it.

The corpus is the treatment identity of every number: `corpus_content_hash`
digests the tree, so quote the corpus commit alongside any figure.

## Run an arm

```bash
uv run --frozen python tools/run_datalake_eval.py \
  --model gpt-5.6-luna \
  --effort xhigh \
  --workers 2 \
  --max-retries 8 \
  --prompt-variant analyst=v4 \
  --resume
```

That run writes
`runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical_analystv4.jsonl`,
one JSON object per line, flushed as each question finishes. It prints progress
every ten rows with a rate and an ETA, then prints the report described below.

A full arm takes hours. Expect to interrupt it and resume it.

### Flags

**Corpus, dataset, and output**

| Flag | Default | What it does |
|---|---|---|
| `--corpus-dir` | `../BIRD-corpus` | The corpus to serve. The driver refuses to start if the corpus has fatal problems, and prints each one |
| `--dataset` | `../BIRD-Data-Obfuscation/eval_dataset` | Directory holding `test_final.jsonl` and the dataset's own quality lists |
| `--out` | derived from the tag | Artifact path. Set it only when you want a name the tag rules would not produce |
| `--limit` | all | Cap the total number of questions |
| `--per-schema` | all | Cap questions per schema |

**Provenance**

| Flag | Default | What it does |
|---|---|---|
| `--arm` | unnamed | The arm this run is, by name, from [`register/arms.toml`](../src/governed_bi/register/arms.toml). The profile's declared `corpus_content_hash` is reconciled against the session's **before the first paid question**, and every row is reconciled again in the report. An unknown name or a malformed file exits 2; a mismatch exits 5. Unnamed runs are allowed and are simply unreconciled — but a comparison against an arm with no profile is `cannot_evaluate`, because the profile is also where the arm's `treatment` is declared |

**Models**

| Flag | Default | What it does |
|---|---|---|
| `--model` | `gpt-5.6-luna` | The agent model id |
| `--effort` | `xhigh` | Reasoning effort for the agent model. Pass `--effort ''` to send none |
| `--provider` | `openai` | Gateway for every surface: `openai`, `bedrock`, or `proxy`. `bedrock` needs `uv sync --extra bedrock` and a region; `proxy` reads its credentials from AWS Secrets Manager |
| `--utility-model` | `--model` | Separate model for the guard's scope gate and the facet rewriters |
| `--utility-effort` | none | Reasoning effort for the utility model. Requires `--utility-model`; alone it would be accepted and dropped, so the driver refuses it |
| `--utility-provider` | `--provider` | Put the utility surface on a different gateway. Recorded as `llm_utility_provider` |
| `--embedding-provider` | `--provider` | Put the embedder on a different gateway. Recorded as `embedding_provider` |
| `--embedding-model` | the provider's default | Embedding model id. The default is not the same string across providers |

**Retrieval**

| Flag | Default | What it does |
|---|---|---|
| `--embed` | off | Build the index with an embedder, turning the semantic channel on. Off by default, so the lexical arm is the reproducible baseline |
| `--top-n` | the register default | Override `route_top_n`, the number of schemas the router shortlists |
| `--replay-routing` | none | Reuse a prior run's schema shortlist instead of routing. See [Pin the routing](#pin-the-routing) |

**Prompts**

| Flag | Default | What it does |
|---|---|---|
| `--prompt-variant` | registry defaults | Select a non-default variant, as `NAME=VARIANT`. Repeatable. See [Select a prompt variant](#select-a-prompt-variant) |

**Observation**

| Flag | Default | What it does |
|---|---|---|
| `--reflect` | off | Turn on the post-hoc reflector (`reflect_enabled`). It writes a verdict and changes no control flow, so EX should not move — that is the arm's own sanity check. Costs one call per turn on `reflect_model` if one is set and on the utility model otherwise, and it is a comparability knob, so a reflected arm and an unreflected one are two arms. Measured once: [risk coverage](analysis/risk-coverage-v4.md) §6 |

**Policy**

| Flag | Default | What it does |
|---|---|---|
| `--abstain` | off | Turn on the declared abstention policy (`abstention_policy_enabled`, ADR 0013). **Not an observer**, unlike `--reflect`: it decides, before the agent spends a `run_query` attempt, whether the turn should be answered, so EX *and* coverage both move and the arm is only readable against an unabstained pair. Costs no model call. A comparability knob, and it enters the artifact tag — a resume that merged an abstaining run into a committing one would report one arm's coverage with the other's accuracy |

**Concurrency and robustness**

| Flag | Default | What it does |
|---|---|---|
| `--workers` | `2` | Threads, each with its own graph and connector. Higher counts trade throughput against rate limits, and a 429 raised inside a node is marked `crashed` — a lost measurement, not a slow one |
| `--max-retries` | `8` | Provider SDK retries per call. Recorded as the `llm_max_retries` comparability knob, so keep it identical across arms you intend to compare |
| `--timeout` | `240.0` | Per-request timeout in seconds. Without one a worker can block indefinitely |
| `--resume` | off | Keep measured rows, requeue crashed ones. See [Resume](#resume) |
| `--force-fresh` | off | Relax the abort when `--resume` finds no artifact but sibling artifacts exist. Non-destructive: it removes nothing |
| `--truncate` | off | **Destructive.** Discard the artifact at `--out` and start over. The only flag that deletes a measured run; prints the row count first, and contradicts `--resume` |

### Where the artifact lands

Unless you pass `--out`, the path is `runs/eval/live_full_<tag>.jsonl`, where
the tag is built from the model, the effort, `--top-n`, the retrieval channel
(`embed` or `lexical`), the provider when it is not `openai`, the prompt
variants when there are any, and then one segment each for `--reflect`,
`--abstain` and `--replay-routing` when they are on (`_reflect`, `_abstain`,
`_pinned`).

Each of those is in the name because each is an arm rather than a detail. Two
runs that differ in one of them are two treatments, and a shared filename would
let `--resume` read one arm's rows as the other's.

### Resume

`--resume` reads the existing artifact, keeps every row that is a measurement,
and drops every row whose `outcome` is `crashed` — requeueing those questions.
A crashed row is not a measurement, so keeping it would bake a hole into the
artifact and compute the final score over a denominator that silently included
it.

If `--resume` finds no artifact but does find siblings named for the same
model, the driver lists them and exits rather than starting over. A changed tag
input renames the artifact, and that is a far more common cause than a genuine
first run. Pass `--force-fresh` when you mean it. It relaxes that abort and
nothing else. An artifact that *does* exist at `--out` is a separate refusal,
and the only way past it is `--truncate`, which discards those rows and says how
many it is discarding first.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | The arm finished, or there was nothing left to do |
| `2` | No credential for one of the model surfaces, no database credential, or `--arm` names a profile that cannot be loaded |
| `3` | The corpus has fatal problems; each one is printed |
| `4` | `--resume` found no artifact but sibling artifacts exist and `--force-fresh` was not passed; or an artifact already exists at `--out`, `--resume` was not passed, and `--truncate` was not passed; or `--resume` found an artifact whose recorded identity contradicts this run's |
| `5` | `--arm` was passed and the profile's declared corpus is not the corpus this session loaded. Raised before the first paid question |

## Select a prompt variant

```bash
--prompt-variant analyst=v4
```

The flag is repeatable, and each value is `NAME=VARIANT` against the [prompt
registry](#the-prompt-registry). An unknown prompt name or an unknown variant
is refused at startup, not three stages later when a node asks for text and
silently receives the default.

Two consequences follow, and both are the point of the flag:

- **The selection moves `prompt_set_hash`.** The digest covers the active
  variant names *and* their text, so an arm records which wording produced it.
  Editing a variant in place also moves the digest.
- **The artifact filename carries the variant.** An A/B differing only in
  `--prompt-variant` would otherwise auto-name both arms to one path, and
  `--resume` would read the first arm's rows as the second's — two treatments,
  one artifact, and no error anywhere.

The driver prints the resolved selection and the resulting hash before the run
starts.

## Pin the routing

```bash
--replay-routing runs/eval/live_full_<prior-arm>.jsonl
```

This reads the `schemas` field of each row in a prior run's artifact and pins
that shortlist onto the matching question. It reads only the prior run's own
routing decisions. It never reads gold.

It exists because `route` is deterministic but the five facet rewriters above
it are model calls, so two runs of the same question can hand `route`
different hits. The shortlist then differs, `licensed` differs, and the agent
is asked a different question. Pinning removes that source of variance, so a
single-knob A/B measures the knob.

Three things to know before you quote a pinned run:

- **An absolute EX from a pinned run is not the engine's unaided number.** The
  arm was handed a shortlist it did not produce. Use a pinned run for the
  paired comparison it was built for, and quote an unpinned arm for the
  engine's own score.
- **Pinning does not freeze everything.** Pass two still re-searches inside the
  pinned schemas, so `licensed` can still move. The driver measures that
  residual and prints it as an identical rate plus a mean Jaccard over the rows
  that moved.
- **Some fraction of a pinned arm is not pinned.** A question the prior
  artifact does not cover routes for itself, and a row whose prior `schemas`
  was empty is skipped rather than pinned to an empty shortlist — replaying an
  empty shortlist would freeze a retrieval failure into the treatment arm as
  though it were a decision. The driver prints the pinned and unpinned counts,
  and each row carries `routing_pinned`.

## The prompt registry

Every prompt the engine sends is declared in
[`src/governed_bi/register/prompts.py`](../src/governed_bi/register/prompts.py),
so that `prompt_set_hash` covers the whole set.

| Prompt | Stage | Variants | Default |
|---|---|---|---|
| `analyst` | `agent_core` | `v1`–`v5` | `v4` |
| `bi_scope` | `guard` | `v1` | `v1` |
| `narrate` | `narrate` | `v1` | `v1` |
| `reflect` | `reflect` | `v1` | `v1` |
| `facet_schema_query` | `facet_schema` | `v1`, `v2` | `v2` |
| `facet_term_query` | `facet_term` | `v1` | `v1` |
| `facet_metric_query` | `facet_metric` | `v1` | `v1` |
| `facet_entity_query` | `facet_entity` | `v1` | `v1` |
| `facet_example_query` | `facet_example` | `v1` | `v1` |

`facet_schema_query` is registered and hashed but not sent: the schema facet
searches the raw question, and the prompt stays in the registry as an unsent
baseline.

### The ANALYST variants

`analyst` is the SQL-writing agent's system prompt, and the only prompt with
more than two variants. **`v4` is the default.**

| Variant | What distinguishes it |
|---|---|
| `v1` | The base rules: use only the provided context and tools, prefer `run_query`, call `ask_user` only when a missing fact blocks a correct answer, write every table reference as `schema.table`, and spell and quote identifiers exactly as the context gives them |
| `v2` | Drops the "prefer `run_query`" line and adds the tool contract: tool arguments are asset ids rather than SQL names, and `inspect_schema`, `sample_rows` and `read_body` are available before writing SQL |
| `v3` | `v2` byte-for-byte, plus two paragraphs on the shape of the result: select exactly what the question asks for and nothing else, and choose `DISTINCT` on what the question means rather than as a precaution |
| `v4` | `v3` plus the star rule: name your columns, because a bare `SELECT *` or `t.*` is refused at the `BINDING` layer, with `COUNT(*)` and `COUNT(DISTINCT col)` as the carve-out |
| `v5` | `v4` minus the projection paragraph, and nothing else. It was written to lose, so that the paragraph's contribution could be priced; it costs 4.07pp. See [failure modes](failure-modes.md#11-the-projection-rule-how-much-of-this-ex-is-shape-matching) |

## The measurement row

`project_turn` in
[`src/governed_bi/eval/harness.py`](../src/governed_bi/eval/harness.py) projects
one serve final state into one row. Every field it writes:

**Identity**

| Field | Meaning |
|---|---|
| `question_id` | The dataset's question id |
| `arm` | The arm name the driver built from the tag |
| `db_id` | The gold schema, taken from the question. Every funnel stage below routing is conditional on it |

**Treatment**

| Field | Meaning |
|---|---|
| `corpus_content_hash` | Digest of the corpus tree the turn was served from |
| `prompt_set_hash` | Digest of the active prompt variant names and their text |
| `knobs_resolved` | The resolved value of every comparability knob, or `null` when the turn recorded none. Absent stays absent: `{}` would read as a configuration in which every knob resolved to `null` |
| `context_hash` | Digest of the assembled context block. `null` on paths that skip `assemble` |
| `context_evicted` | What the character budget dropped before the model saw it. Absent when the block fit |
| `delivery_hash` | The context plus every tool-delivered body. **Not** deterministic — it depends on which `read_body` calls the model chose — so it is a diagnostic and never a gate |
| `tool_delivered` | `call_id` → sha256 of every corpus- or database-derived tool return. `null` when the agent loop did not run, which is a different fact from `{}` |

**Outcome and grade**

| Field | Meaning |
|---|---|
| `outcome` | `answered`, `refused`, `clarification`, `capped`, or `crashed` |
| `correct` | `true`, `false`, or `null`. `null` means the grader had no gold to compare against, and is **not** a wrong answer. Propagate it; do not coerce it |
| `crashed` | Whether `outcome` is `crashed` |
| `grade_detail` | Why the grade came out as it did |
| `gold_fingerprint` | Fingerprint of the gold result set |
| `pred_fingerprint` | Fingerprint of the prediction's result set |
| `quality_flags` | What the *dataset* says is wrong with this question — leakage, a gold with no total order, a degenerate gold. Carried rather than filtered, so one artifact can be read under more than one exclusion policy |
| `terminal_reason` | Why the turn ended without answering |
| `refused_by` | Which stage refused |
| `failed_stage` | Which stage failed |
| `error_type` | The exception class on a crashed turn |
| `guard` | The guard's `outcome` and `rule_id`, written on every turn including `clear`. A gate that leaves a trace only when it fires cannot afterwards be told from one never wired up. The free-text `detail` is dropped |
| `abstention` | What the declared abstention policy decided and the evidence behind it: `{policy, outcome, reason, rules_evaluated, evidence}`. `null` when the turn ended before the node; `{"outcome": "disabled", …}` when `--abstain` was off, which is what makes a control arm nameable rather than merely silent |
| `reflect_verdict` | The post-hoc observer's `{verdict, reason, model, prompt_sha256, signals}`. `null` means reflection did not run — never "the turn looked fine" |

**SQL**

| Field | Meaning |
|---|---|
| `generated_sql` | The statement the engine executed |
| `gold_sql` | The gold statement for this question |
| `attempts` | Per attempt: `layer`, `reason_code`, `passed`, `path`. This is what tells a `r_table_not_licensed` retrieval failure apart from a guardrail working as designed |

**Abstention pricing**

| Field | Meaning |
|---|---|
| `computed_fingerprint` | The abstained turn's last statement, re-executed. `null` on answered turns, on turns with no statement, and on statements that will not run |
| `computed_correct` | Whether that fingerprint matches gold. **Never folded into `correct`** — an engine that would not commit to a statement gets no credit for it |

**Retrieval**

| Field | Meaning |
|---|---|
| `schemas` | The schema shortlist the router chose |
| `licensed` | The tables the turn was licensed to query |
| `routing_pinned` | Whether this row's shortlist **was** replayed — the pin was attached *and* the turn ran on it. A turn that ended before `route_node` reads `false`, and so does a partial pin |
| `facet_channels` | Per facet per channel: `ran`, `not_configured`, or `failed` |
| `facet_degraded` | Whether some facet ran on fewer channels than declared |
| `schema_ranking` | Where the gold schema placed among **all** scored schemas: `n_scored`, `gold_rank`, `gold_score`, and the top ten with their scores. Without it, "the gold schema was not a candidate" and "it ranked 4th" are the same observation |
| `facet_hits` | Per facet: the `queries` it searched (kept whole — the only part of retrieval a model sampled), `n_hits` before truncation, and the top ten asset ids |
| `pulled_in` | `n_resolve`, `n_connect`, and the `connect_ids` the join walk added |
| `lexical_coverage` | How much of the question the corpus vocabulary covers. A float or `null`, never `0.0` for "not measured" |
| `budget_dropped` | `asset_type` → how many hits a per-type cap discarded before anything downstream saw them. Without it a cap and a retrieval miss are the same row |
| `budget_best_dropped_score` | `asset_type` → the highest score the cap cut. A drop at 0.97 and a drop at 0.01 want opposite fixes |
| `crossings` | Cross-schema Steiner points, verbatim |

**Health counters**

| Field | Meaning |
|---|---|
| `guardrail_error` | Whether `check()` swallowed any exception on this turn. `null`, not `false`, when the turn never recorded the counter — absent is not clean |
| `re_served` | Whether the turn was re-served. `false` on every production path: `n_re_served` is a frozen zero and is not a quotability gate |
| `negative_failed_open` | Whether the negative gate errored and failed open |

**Cost**

| Field | Meaning |
|---|---|
| `usage` | A list of per-call token rows. See below |
| `latency_sec` | Wall clock for the turn, or `null`. The drivers serialise with `default=str`, so a `Measured` absence must never reach this field — it would land as a string that then sorts like a value |

The harness adds `run_id` to the row after projection.

The seven `proxy_*` arms in `runs/eval/` predate many of these fields and do not
carry the full set — 27 keys on `run1` and `run2`, 33 to 35 on the five arms
after them. A field absent from an old artifact is **absent**, not a measured
zero; that distinction is what the gates read, and collapsing it is how an
uninstrumented arm once passed every one of them.

### Two things people get wrong

**`model_calls` is a key inside each `usage` entry, not a top-level row field.**
Each entry in the `usage` list carries `turn_index`, `stage`, `model`,
`input_tokens`, `output_tokens`, and `model_calls`, plus `cache_read_tokens`,
`cache_write_tokens` and `reasoning_tokens` when the provider reported them. It
is inside the entry because `agent_core` aggregates a whole tool loop into one
entry: counting rows there reports one call for a turn that made several, which
understates the repeated share of the input by an order of magnitude.

**The row carries two treatment identities, not one.**
`corpus_content_hash` and `prompt_set_hash` are both on every row. A comparison
needs both: the corpus is the treatment, and so is the prompt wording. An A/B
whose two artifacts cannot be told apart on both is not an A/B.

## What the driver prints

After the last row, the driver reads the **whole artifact** back — a resumed
run is one run — and prints:

- The row count, and — when `--arm` named a profile — the reconciliation of
  every row against the corpus digest that profile declares.
- How many rows the grader could not judge. Those are excluded from every EX
  below rather than counted as wrong.
- `EX`, `EX over attempted` (excluding clarifications), and `EX over clean`
  (excluding the three lists the dataset itself warns about: order-sensitive
  golds, `exec_failed` golds, and split leakage — 29 questions on the v4 arm).
  The exclusions are printed with their counts, never applied silently.
  Frozen-literal golds are **not** excluded: they are flagged `degenerate` on
  the row, and the flag is derived here rather than published by the dataset.
- The outcome counts, and the exception classes behind any crashes.
- `all gold tables licensed` — the EX ceiling. A question whose gold tables
  were never licensed could not have been answered by any model.
- The abstention block: how accurate the committed answers are, and how
  accurate the abstained ones would have been if forced to commit.
- Refused turns by declared reason, and by the stage that owns each reason,
  with an `UNATTRIBUTED` bucket for a reason in no register. This is a
  different histogram from the one below it: this one counts *turns* through
  `terminal_reason` and `refused_by`, so it can see a withheld turn, which
  writes no ledger row at all.
- Failed attempts by layer and rule, counted over answering attempts in the
  ledger — a refused `sample_rows` probe is not governance declining to answer.
- The residual licensed drift, when the run was pinned.
- The retrieval funnel, each stage conditional on the one above it.
- The quotability gates.
- Gold-schema reachability, EX among reachable rows, the clarification split,
  and the token totals.

## What makes a number quotable

The gates are declared on turn-record fields in
[`register/record.py`](../src/governed_bi/register/record.py) and implemented in
[`measure/gates.py`](../src/governed_bi/measure/gates.py). The driver evaluates
every declared gate over the arm and prints each verdict.

| Gate | Condition |
|---|---|
| `outcome` | No turn is classified `crashed` |
| `guardrail_errors` | No turn recorded a swallowed `check()` exception |
| `negative` | No turn's negative gate errored and failed open |
| `facet_channels` | On turns where the fan-out ran, no channel state differs from its declared expectation. Stage-conditional: zero such turns is `cannot_evaluate`, never a pass |
| `knobs_resolved` | Every row in the arm agrees on `knobs.resume_drift_keys()` — one arm is one configuration |
| `context_hash` | Every turn carries a `context_hash`. The cross-arm half of the condition needs two arms |
| `corpus_content_hash` | Every turn that reached `stamp` carries the same non-null corpus digest. Stage-conditional for the same reason as `facet_channels`: a turn paused on `ask_user` never reaches `stamp`, so a clarification legitimately carries none. The **cross**-arm half — that two arms did not silently run on two corpora — is still evaluated by nothing, because `corpus_content_hash` is a record field and not a comparability knob. `--arm` and `arms.toml` are what close that within one arm |

A gate returns one of three verdicts, and only the first is a pass:

- **`pass`** — the condition held over a population large enough to have failed.
- **`fail`** — the condition was violated.
- **`cannot_evaluate`** — the inputs were not there. **This blocks quotation.**
  A check that did not happen is not a check that passed.

The driver prints `ALL GATES PASS -- these numbers are quotable as a single
arm` only when every gate passed. It prints the gates rather than enforcing
them: a driver that refused to report a run would lose the run.

Further conditions apply to a comparison rather than to one arm, and live in
[`eval/report.py`](../src/governed_bi/eval/report.py):

- `context_hash` existence: both arms must have assembled a context on every
  shared question, or those questions cannot be compared at all.
  `comparison_quotable` substitutes this for the single-arm coverage check.
- `knobs_comparable`: the arms differ in the **declared treatment** and in
  nothing else in `comparability_keys()`. The treatment is read from the arm's
  profile in `arms.toml`, or named by the caller; a pair that cannot name one is
  `cannot_evaluate`, because "nobody said what changed" is not "nothing
  changed". A declared treatment that is *identical* on both arms is a `fail` —
  that is a replicate wearing an arm's name. A treatment naming something that
  is not a comparability knob refuses rather than being ignored. A knob absent
  from either arm is `cannot_evaluate` — absent is not a value, and `dict.get`
  collapsing it into a recorded `None` is how a gate certifies a configuration
  it never saw.
- Populations must share units and filters before `paired_ex` runs McNemar
  over them.

`context_hash` distinctness used to be the treatment test: at least 95% of shared
questions had to have differing hashes. It was retired by audit D9. Retrieval is
nondeterministic, so hashes differ whether or not the treatment did — the gate
passed at **0.9993** on `run1`/`run2`, which differ only by a random seed, and
across all 21 pairs of the seven `proxy_*` arms on disk it never falls below
**0.9882**, hitting exactly 1.0000 on 11 of them (corpus `30872d3`, recomputed
2026-08-12). It believed it asked "did the treatment change" and measured "is
there retrieval noise", to which the answer is always yes. The judgement now
reads declared knobs instead of inferring from a hash.
