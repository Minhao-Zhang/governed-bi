# Experiment Results (v5 — supersedes v1–v4)

_Recorded 2026-07-14 (run `20260715T010412Z_restaurant`, executed 2026-07-14
evening local). Method and decisions: [design-decisions.md](../design-decisions.md)
(D4 grading · D14 SME-growth benchmark + 2026-07-15 amendment · Audit dispositions).
Raw `runs/` artifacts are git-ignored/ephemeral — this doc is the durable record._

> **v5 is the first full end-to-end run on the _agent_ serve path — now the only
> serve path** (the [ADR 0002](../adr/0002-governed-agentic-serve-runtime.md) P2
> cutover deleted `flow.py` on 2026-07-14, commit `d2fdd6a`). Every prior
> number (v1–v4) was scored on the retired deterministic **flow** path.
> v5 is therefore the canonical record of the *shipped* system; **v4's flow
> figures are kept below only as the flow-path baseline** for the agent-vs-flow
> comparison, because that comparison can no longer be re-run (flow is gone).
> **Still a single seed** — the ≥3-seed blocker from v4 is unchanged; v5's
> headline finding (SME finally moved EX) is a signal to replicate, not a result
> to quote.

> **Terminology since superseded — read this before the numbers below.** This
> doc predates the [terminology refactor](terminology-refactor.md) and uses the
> arm labels current at recording time: `A1` (renamed `baseline`), `A2`
> (renamed `curated`), `A3` (renamed `curated_sme`); the retired de-obfuscation
> `gold` oracle is superseded by `ceiling`. **The mapping is a label rename
> only for `A2`/`A3`/`gold`; it is not a like-for-like rename for `A1`.** The
> `A1` scored here is the old raw-dump "no-layer" solver — the model goes
> straight to SQL with no semantic layer and no samples, over a **different,
> now-deleted serve mechanism**. The new `baseline` arm is a redefinition, not
> a relabeling: a deterministic, script-built corpus (table/column names,
> types, **sample values**, FK candidates — no curator LLM, no train-SQL-derived
> assets) served through the **same Analyst path as every other arm**, closing
> the "no content" vs. "no governed path" confound the old `A1` had. The new
> `baseline` has not been run under this benchmark; do not read any number below
> as its score. See [terminology-refactor.md](terminology-refactor.md) for the
> full ladder and rename map.

## TL;DR

- **Full-stack lift on the agent path is ~2.4×.** A1 no-layer → A3 SME EX
  **0.217 → 0.522** (**+0.304**); decoy-touch **0.391 → 0.0**. This is the
  largest end-to-end restaurant lift recorded, and the first on the agent path.
- **The SME round-trip added EX for the first time (+0.174).** A3 **0.522** vs A2
  **0.348** — an extra **4 questions** from folding **11 SME-authored rules**
  (`source: human`, `status: certified`). This **breaks the "SME adds no EX"
  pattern** that held across v3 (`cs_semester`, `ice_hockey_draft`) and v4
  (`restaurant`, flow path), where A3 ≈ A2. **Single seed — needs replication
  before it overturns the pattern.**
- **Curation still eliminates decoy-touch.** A2/A3 touch a decoy/suspect column on
  **0%** of questions vs **39%** for the no-layer baseline. The governance moat
  holds on the agent path exactly as it did on the flow path.
- **The run is internally clean.** EX cross-check agreement **1.0** on all arms;
  gold-hash self-check **5/5**; train/test disjoint and SME brief leakage-checked.
  One non-scoring defect: the A3 validation **fix-pass** crashed (see Findings 4).

## Numbers (execution accuracy, lenient = strict this run)

| Arm | EX | Δ vs A1 | Δ vs A2 | refusal | decoy-touch | SME rules |
|---|---|---|---|---|---|---|
| A1 baseline | 0.217 | — | — | 0.0 | 0.391 | — |
| A2 curated | **0.348** | +0.130 | — | 0.0 | 0.0 | 0 |
| A3 + SME | **0.522** | +0.304 | +0.174 | 0.0 | 0.0 | 11 |

`lenient == strict` on every arm (no partial-credit gap). Difficulty labels are
absent for `restaurant` (all `unknown`), so there is no by-difficulty breakdown.

**Comparison to the flow-path record (v4, same DB, same N, single seed each):**

| Arm | v4 flow EX | v5 agent EX |
|---|---|---|
| A1 no-layer | 0.217 | 0.217 |
| A2 curated | 0.304 | 0.348 |
| A3 SME | 0.348 | **0.522** |

The agent path ties-or-beats the flow at every arm and pulls decisively ahead at
A3 — directionally consistent with the earlier agent-vs-flow A/B (agent ≥ flow in
4/4 arm comparisons; the flow path is now deleted, so that comparison is
historical), now with a larger A3 gap. Both are single seeds, so the gap is not
yet an effect size.

## How we ran it

**One command, full pipeline, live model.** The harness builds the semantic layer
from scratch and scores all three arms on the held-out test split in a single
process:

```bash
uv run python -m governed_bi.eval.run_experiment \
  --db restaurant \
  --bird-dir ../BIRD-Data-Obfuscation \
  --pg-dsn "$PG_RENAME_DECOY_DSN"
# → runs/<ts>_restaurant/ : corpus_a2/, corpus_a3/, generations.a{1,2,3}.jsonl,
#   summary.json (arms + crosschecks + leakage), manifest.json
```

Wall-clock **~25 min** for the full run (deep-agent curation + SME + 3 arms × 23
test questions on the multi-call agent path). `PYTHONUNBUFFERED=1` was set only so
progress streamed to the log; it has no effect on results.

**The three arms** (they differ *only* by the corpus + solver; test items, gold,
grader, and serve policy are identical):

- **A1 — no-layer baseline.** Model → SQL with no semantic layer. The moat floor.
- **A2 — curated.** A deep-agent curator runs over all 94 train `(question, gold
  SQL)` pairs — deterministic Facts ingest (joins from gold SQL, metrics) then an
  agentic enrich/annotate/few-shot pass (adversary + validate as signals) — and
  serve runs over the built corpus. Built this seed: **34 few-shots, 6 joins, 20
  metrics, 12 terms, 5 tables** (77 assets, 0 rules).
- **A3 — A2 + Simulated SME.** A read-only "SME" deep agent (armed with
  `run_probe_query` against the live DB) answers the curator's clarifying
  questions; the ingest agent folds the answers back as **certified rules +
  caveats**. Built this seed: A2 + **11 rules** (88 assets); the A2→A3 delta is
  *exactly* those 11 rules.

**Fixed configuration:**

| | |
|---|---|
| Serve path | **agent** (ADR 0002 governed agentic core) — the only path since the P2 cutover |
| DB backend | `restaurant` on `pg_rename_decoy` (Postgres :5435), single schema; `n_train = 94`, `n_test = 23` |
| Model | `gpt-5.6-sol`, live (OpenAI Responses API) |
| Gold field | `sql_rename` (the obfuscated identifiers) |
| Agent bounds | `recursion_limit = 40`; `run_query` attempt cap `= 3`; curator `max_agent_steps = 25` |
| Serve policy | `hard_block_suspect_columns = false`, `grade_semantic_failures = true` (§6): coverage / L3–L5 / execution-exhaustion deliver SQL with unverified assurance; L2 + refuse-gate stay hard |
| Grading | `eval/hash_grade.py` (byte-for-byte result-set hash vs the reference `_db.py`) with an `execution_match` cross-check |

**Validity instrumentation baked into the run** (all recorded in `summary.json`):
a **gold-hash self-check** (re-executes a 5-row sample of gold SQL against live
Postgres and confirms the grader agrees), an **EX cross-check** (hash grader vs
`execution_match` on every arm), and **leakage checks** (`train_test_disjoint`,
`sme_brief_checked`).

## Findings

### 1. Full-stack lift is real and large; decoy-touch collapses
A1 → A3 is **0.217 → 0.522** (+0.304, ~2.4×) with decoy-touch **0.391 → 0.0**.
As in every prior run, the decoy collapse is the cleanest, lowest-variance
governance signal: the layer reliably steers generation off the renamed decoy
columns onto the intended ones.

### 2. The SME arm added EX for the first time — pattern-breaking, single seed
A3 folded **11 certified rules** and moved EX **+0.174 over A2** (4 questions).
Every prior measurement — v3 on two DBs, v4 on this DB via the flow path — showed
A3 ≈ A2, i.e. the SME round-trip lands correct knowledge but it does not surface
in execution accuracy. v5 is the first time it did. **Two candidate explanations,
not yet distinguished:** (a) the agent serve path *uses* the rules the flow path
left on the floor (rules reach the agent prompt / repair loop in a way the
one-shot flow generator did not exploit), or (b) this is a favorable seed and the
gap is noise. The A3 EX (0.522) is above every prior A3 (flow 0.348, agent A/B
0.435), which is exactly the shape a lucky seed would take — so this finding is
**explicitly provisional** until the multi-seed run.

### 3. Agent ≥ flow at every arm, decisive at A3
Against the retired flow baseline (v4), the agent path ties A1, beats A2
(+0.044), and beats A3 (+0.174). Consistent with the A/B's "agent ≥ flow in 4/4
comparisons," now with a larger A3 margin. Still single-seed on both sides.

### 4. Internal validity passes; one non-scoring defect (A3 fix-pass crash)
EX cross-check agrees **1.0** on all three arms; gold-hash self-check **5/5**;
`train_test_disjoint` and `sme_brief_checked` both true. The scoring is
trustworthy. **However**, A3's post-fold **validation fix-pass** crashed with
`KeyError: 'restaurant'` (swallowed by `_invoke_agent`,
[pipeline.py:149](../../src/governed_bi/curator/pipeline.py#L149), surfaced as
`fix_pass_error` in `corpus_a3/run_manifest.json`). Consequences:

- The A3 corpus that scored 0.522 is the **complete SME-folded corpus** (main
  fold `error: null`); only the *repair* of validate findings was skipped.
- **12 `dangling-ref` findings went unrepaired** — term bindings whose
  `asset_id` points at non-existent `column_*` ids (created by A2's fix-pass;
  present in both A2 and A3). They did not cost EX here, but they mean the shipped
  A3 corpus carries 12 broken term bindings.
- The crash is **A3-only and systematic** (A2's fix-pass ran fine; A3's will crash
  every seed), so it should be fixed before the multi-seed run to keep the SME arm
  clean. Tracked separately.

## Threats to validity

- **Single seed, tiny N (23), single DB.** Unchanged from v4. A2 is deterministic
  code but its output varies run-to-run with the deep agent's exploration, so a
  single-seed delta can move materially. **≥3 seeds with mean±spread is still the
  blocker before any number is quotable** — and it is what would confirm or kill
  Finding 2.
- **Finding 2 is the pattern-breaker most at risk from variance.** It rests on a
  single A3 that is higher than all prior A3s. Treat it as a hypothesis until
  replicated.
- **A3 fix-pass crash** (Finding 4) leaves 12 dangling term bindings in the A3
  corpus each seed until fixed.
- **Grader** is the self-contained hash compare, validated on a 5-row sample
  rather than head-to-head with the reference at full scale.
- **No difficulty stratification** for `restaurant` (labels absent).
- **Cost/latency:** the agent path is several model calls per question vs. one for
  the retired flow.

## Next steps (in priority order)
1. **Fix the A3 fix-pass `KeyError: 'restaurant'`** and the dangling term bindings
   it fails to repair — cheap, systematic, and it dirties every A3 corpus.
2. **Scale N / seeds** — ≥3 seeds on `restaurant` with mean±spread; this gates
   every quotable number and decides Finding 2 (does SME really move EX on the
   agent path, or was 0.522 a lucky draw?).
3. **Per-question win/loss inspection** — which test questions do the 11 SME rules
   fix that A2 misses? Turns Finding 2 from an aggregate delta into a mechanism.
4. **Data-lake run** — 2 schemas live, blind routing via the LLM `select_schema`
   node (§5.1).

## Known limitations (of this benchmark)

Recorded from the 2026-07-15 audit; full detail in [design-decisions →
Audit dispositions](../design-decisions.md#audit-dispositions-2026-07-15) and the
[D14 amendment](../design-decisions.md#d14-sme-growth-benchmark-on-bird-obfuscation).

- **Refuse-gate untested by EX.** Every BIRD question is answerable, so the arms
  never trigger a refusal and the **false-refusal rate is unmeasured** here.
- **Single-seed until scale.** Per-DB deltas are directional; the fix is the
  69-schema / 2,030-test scale run, not more small-DB seeds.
- **No gold ceiling line.** The de-obfuscation gold arm is retired; the recoverable
  ceiling is redefined as a **test-aware SME oracle** (not yet implemented).
- **Cross-schema is un-graded** (BIRD's db_ids are independent databases; D15).

---

_Prior versions (retired as current-system records; kept for history):_
- _**v4** — finished curator on `restaurant`, **flow** serve path, single seed:
  A1 0.217 → A2 0.304 → A3 0.348, decoy 0.609 → 0.0. Retained above as the
  flow-path baseline for the agent-vs-flow comparison (flow is now deleted, so it
  cannot be re-run)._
- _**v3** — finished curator on `cs_semester` (0.348 → 0.565) and
  `ice_hockey_draft` (0.176 → 0.824), flow path._
- _**v1–v2** — broken/seed-only then capped-SME-brief curators._

_What carries forward from every prior version is the **method** and the
**variance lesson** (single-seed deltas are directional, not conclusive), not
their figures._
