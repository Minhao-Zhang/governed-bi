# Three-Arm Experiment: Results (v4 — supersedes v1–v3)

_Recorded 2026-07-14 (run `20260714T020041Z`, executed 2026-07-13 evening local).
Companion to [three-arm-experiment-plan.md](three-arm-experiment-plan.md) and
[curator-rework-plan.md](curator-rework-plan.md). Raw `runs/` artifacts are
git-ignored/ephemeral — this doc is the durable record._

> **v4 supersedes v3 and is the canonical benchmark going forward.** The reference
> DB is now **`restaurant`** on `pg_rename_decoy`, a clean single-seed run of the
> finished curator (crosschecks + gold-hash + leakage all verified below). The v3
> point estimates on `cs_semester` / `ice_hockey_draft` are **retired** — they are
> not the reference numbers anymore. What carries forward from v1–v3 is the
> **method** and the **variance lesson** (single-seed deltas are directional, not
> conclusive), not their figures.

## TL;DR

- **Curation lifts EX and eliminates decoy-touch.** A1 → A2 EX **0.217 → 0.304**
  (**+0.087**); decoy-touch **0.609 → 0.0**. The decoy elimination is the cleaner
  governance signal at this N than the EX bump.
- **The SME round-trip added 8 certified rules for +0.043 EX.** A3 EX **0.348**
  (+0.043 over A2, +0.130 over A1). A2 produced **zero** rules; A3 folded **8**
  SME-authored (`source: human`, `status: certified`) rules — yet raw EX barely
  moved. Consistent with v3's "A3 ≈ A2" pattern: the SME value is not showing up in
  execution accuracy at N=23.
- **The run is internally clean.** EX cross-check agreement **1.0** on all arms;
  gold-hash self-check **5/5**; train/test disjoint and SME brief leakage-checked.
  So the numbers are trustworthy *for this seed* — the open risk is variance across
  seeds, not this run's integrity.

## Numbers (execution accuracy, lenient = strict this run)

| Arm | EX | Δ vs A1 | Δ vs A2 | refusal | decoy-touch | SME rules |
|---|---|---|---|---|---|---|
| A1 baseline | 0.217 | — | — | 0.0 | 0.609 | — |
| A2 curated | **0.304** | +0.087 | — | 0.0 | 0.0 | 0 |
| A3 + SME | **0.348** | +0.130 | +0.043 | 0.0 | 0.0 | 8 |

`lenient == strict` on every arm (no partial-credit gap). Difficulty labels are
absent for `restaurant` (all `unknown`), so there is no by-difficulty breakdown.

## Setup

- Arms: **A1** no-layer · **A2** deep-agent curator over all train `(question, gold
  SQL)` pairs · **A3** A2 + Simulated SME (read-only deep agent with
  `run_probe_query`), folded back by the ingest agent.
- DB: **`restaurant`** on `pg_rename_decoy` (`127.0.0.1:5435`), single schema.
  `n_train = 94`, `n_test = 23`. Model `gpt-5.6-sol`, live.
- Serve path: **`flow`** (deterministic pipeline, `agent_serve=false`); curator
  `max_agent_steps = 25`. `grade_semantic_failures=true` (§6): coverage / L3–L5 /
  execution-exhaustion deliver SQL with unverified assurance; L2 + refuse-gate stay
  hard. Refusal rate was 0.0 on all arms regardless.
- Grading: `eval/hash_grade.py` (byte-for-byte vs reference `_db.py`) with an
  `execution_match` cross-check.

## Findings

### 1. Curation's clearest win here is decoy avoidance, not EX
A1 touches a decoy/suspect (renamed) column on **61%** of questions; A2 and A3 touch
one on **0%**. EX rises a modest +0.087 at the same time. At N=23 the decoy-touch
collapse is the stronger, lower-variance evidence that the governed layer is steering
generation onto the intended columns — EX is the noisier proxy for the same effect.

### 2. The SME arm still adds no EX — now with 8 rules on the table
A3 folded 8 SME-certified rules (e.g. a dataset-specific `review > 2` interpretation)
and moved EX only +0.043 over A2. This repeats v3's result on two other DBs: the SME
round-trip lands real, correct knowledge in the served corpus, but it does not flip
EX outcomes at this N. Whether its value is in reliability/coverage rather than raw
EX remains the open question (v3 Finding 4).

### 3. Internal validity checks all pass
EX cross-check (hash grader vs `execution_match`) agrees **1.0** on all three arms;
the gold-hash self-check matched **5/5** sampled gold rows; `train_test_disjoint` and
`sme_brief_checked` are both true. Nothing in this run's plumbing is suspect — the
caveats below are about statistical power, not correctness.

## Threats to validity

- **Single seed, tiny N (23), single DB.** The point estimates are indicative, not
  conclusive. The v1–v3 lesson stands: A2 is deterministic code but its output
  varies run-to-run with the deep agent's exploration, so a single-seed delta can
  move materially. **≥3 seeds with mean±spread is still the blocker before any number
  is quotable.**
- **Grader** is the self-contained hash compare, validated on a 5-row sample rather
  than head-to-head with the reference at full scale.
- **No difficulty stratification** for `restaurant` (labels absent), so the lift
  cannot be attributed to easy vs hard questions.

## Next steps (in priority order)
1. **Scale N / seeds** — ≥3 seeds on `restaurant` with mean±spread; this gates every
   quotable number.
2. **Explain the SME-adds-no-EX pattern** (Finding 2, now seen on three DBs) —
   measure reliability/coverage deltas, not just EX, and inspect which test failures
   the SME rules *should* have fixed.
3. **Re-run under the agent serve path** (`agent_serve=true`) once parity is
   confirmed, to compare against this `flow` baseline.
4. **Data-lake run** — 2 schemas live, blind routing via the LLM `select_schema`
   node (§5.1), once wired into `flow.py`.

---

_Prior versions (retired): v1 broken/seed-only curator; v2 real curator, capped SME
brief; v3 finished curator on `cs_semester` (0.348 → 0.565) and `ice_hockey_draft`
(0.176 → 0.824). Their figures are superseded by this `restaurant` run and are kept
only as historical context in git history._
