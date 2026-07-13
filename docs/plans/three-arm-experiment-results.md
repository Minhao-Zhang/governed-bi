# Three-Arm Experiment: Results (v3 — supersedes v1, v2)

_Recorded 2026-07-13. Companion to [three-arm-experiment-plan.md](three-arm-experiment-plan.md)
and [curator-rework-plan.md](curator-rework-plan.md). Raw `runs/` artifacts are
git-ignored/ephemeral — this doc is the durable record._

> **v3 supersedes v2.** v3 is the first run with the finished curator: SME as a
> read-only **deep agent**, the SME brief carrying **all** evidence (not a 40-cap),
> and `pair:`/`query:`-scoped clarifications landing as **`RuleAsset`s**. v1 (broken
> curator, seed-only) and v2 (real curator, capped SME brief) numbers are retired.

## TL;DR

- **Curation lifts EX substantially on BOTH databases now** — cs_semester
  0.348 → 0.565 (**+0.217**), ice_hockey 0.176 → 0.824 (**+0.647**).
- **But the numbers are high-variance run-to-run.** A2 (Phase-A curation only) is
  produced by the same code as v2, yet it jumped v2→v3 (cs 0.348 → 0.565; ice
  0.471 → 0.824). The cause is **agent nondeterminism**: the deep agent did more
  work this run (94 / 106 write-tool calls vs v2's 56 / 58). Trust the *direction*
  (curation helps, more where the schema is deceptive), not any single point
  estimate. **Multiple seeds are now clearly required.**
- **A3 = A2 this run** — the SME round-trip added no EX. Its `pair:`/`query:`
  caveats *did* land as rules (3 / 6), and its table/column answers folded, but
  none flipped an EX outcome at this N.

## Numbers (execution accuracy, lenient)

| DB | A1 | A2 | A3 | Δ A2−A1 | agent clarifications | caveats→rules |
|---|---|---|---|---|---|---|
| cs_semester | 0.348 | **0.565** | 0.565 | +0.217 | 3 | 3 |
| ice_hockey_draft | 0.176 | **0.824** | 0.824 | +0.647 | 6 | 6 |

Phase A `write_total` = 94 (cs) / 106 (ice); `ledger_source=agent`, `fold_mode=agent`.
decoy-touch → 0 under curation on both (ice A1 0.059 → 0).

## Setup

- Arms: **A1** no-layer · **A2** deep-agent curator over all train `(question, gold
  SQL)` pairs · **A3** A2 + Simulated SME (now a **read-only deep agent** that can
  `run_probe_query`), folded back by the ingest agent.
- DBs: `cs_semester` (test 23) / `ice_hockey_draft` (test 17) on `pg_rename_decoy`,
  single-schema per run. Model `gpt-5.6-sol`, live, tracing (Langfuse) on.
- SME brief now includes **all** unique BIRD evidence hints (uncapped).
- Grading: `eval/hash_grade.py` (byte-for-byte vs reference `_db.py`) +
  `execution_match` crosscheck. `grade_semantic_failures=true` (§6).

## Findings

### 1. Schema deceptiveness still drives the size of the lift
ice_hockey (FK-columns disguised as values: `PlayerInfo.height`/`.weight` →
`height_info`/`weight_info`) gets the bigger lift (+0.647) than cs_semester
(+0.217). The mechanism is unchanged from v1's SQL-level trace; a richer curated
corpus (94–106 writes this run) simply captures more of it.

### 2. Run-to-run variance is large — the headline caveat
A2 is Phase-A-only and its code did not change v2→v3, yet EX moved +0.217 (cs) and
+0.353 (ice). This is the **deep agent's nondeterminism**: how thoroughly it
explores and how many assets it commits varies per run. Consequently **single-run
deltas are not trustworthy** — the qualitative pattern (curation helps; more on
deceptive schemas) is, the exact numbers are not. Scaling to ≥3 seeds with
mean±spread is now the top priority.

### 3. The pair→rule fix works live
`pair:`/`query:`-scoped clarifications (trap / annotation-error findings) were
recorded as `RuleAsset`s: 3 (cs) / 6 (ice), confirmed in the manifest
(`caveats_recorded`) and on disk (`corpus_a3/<db>/rules/`). The knowledge now
reaches the served corpus instead of dying in the ledger.

### 4. The SME arm added no EX this run
A3 = A2 on both DBs. The SME answers folded (table/column annotations + caveat
rules) but changed no EX outcome. Possible reasons: A2 was already strong; the
remaining test failures aren't ones the SME's caveats address; or the value is in
reliability/coverage rather than raw EX at N=17/23. Worth watching across seeds.

## Threats to validity

- **High run-to-run variance** (Finding 2) + **tiny N** (17 / 23), single seed —
  the two together mean the point estimates are indicative, not conclusive.
- **Grader** is the self-contained hash compare (validated on a sample, not run
  head-to-head with the reference at full scale).
- **Recurring agent error:** `KeyError: 'cs_semester.RA'` and
  `'tbl_cs_semester_course'` this run (v2 had `'train_6985'`) — caught and recorded
  (non-fatal), but they truncate an agent turn early, likely a dict-lookup bug in a
  tool (qualified name / asset id), and may contribute to the variance.

## Post-v3 status of the rework (all landed)
SME = read-only deep agent; all-evidence brief; `pair:`/`query:` → `RuleAsset`;
empty-ledger non-fatal; proactive-clarification prompt; deps consolidated (tracing
live); SME-sees-gold-SQL documented as a non-issue. See
[curator-rework-plan.md](curator-rework-plan.md) §11–§12.

## Next steps (in priority order)
1. **Scale N / seeds** — ≥3 seeds with mean±spread; the variance in Finding 2
   makes this the blocker before any number is quotable.
2. **Fix the recurring agent `KeyError`** (dict lookup on a qualified name / asset
   id) — it truncates curation and adds variance.
3. **Data-lake run** — 2 schemas live, blind routing via the LLM `select_schema`
   node (§5.1), once wired into `flow.py`.
4. More opaque-schema DBs; investigate why the SME arm adds no EX (Finding 4).
