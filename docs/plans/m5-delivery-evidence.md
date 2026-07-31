# M5 delivery evidence (N15–N17 so far)

Local branch only — no `gh`. Against [batch-m5.md](batch-m5.md). Tip at write: see
git log on `impl/rebuild-first-batch`.

**This file does not claim the full paid 57-db `--replicate` ladder was run.** N17
delivers the command, guards, runbook text, and a 5-question smoke shaped like M4 N12b.

| Item | Commit(s) | Notes |
|---|---|---|
| N15.1 | `52d89f1` | `error_taxonomy` + `sql_diff` `__main__` mirroring `analysis.main` |
| N15.2–4 | `d38beff` | BIRD-basis funnel / twins / DISTINCT; questions sidecar; question view |
| N15.5 | `49033f2` | `run_datalake` auto-writes `analysis.json` + `questions.jsonl` |
| N16 | `2c6cb3a` + multi-schema verify below | Quotable corpus findings diverted by code; pooled corpus re-validated |
| N17 | this batch | Zero-question guard + runbook (full ladder / TOML / hard MDE) + 5q smoke |

Post-review (see [m5-review-findings.md](m5-review-findings.md)): adjudication and
wiring fixes below; tool mechanics largely kept.

---

## N15 named mismatches (tool vs report)

Canonical tooling path:
`runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z`.
Pinned in `tests/test_bird_basis_report.py`.

**Two populations, not one correction.**

| Population | Definition | What it reproduces |
|---|---|---|
| **Report misroute** (`schema_misroute_report`) | `routed_hit=False` and gold in `shortlisted_schemas` (no `correct`/refused filter; n=107 on fixed2 curated_sme) | Rank overrides **44** and all six §3 attractors exactly |
| **Tool pick-stage** (`schema_pick_report`) | BIRD-basis funnel stage `pick` (drops `correct=True` and refused; n=96) | Rank histogram 26/31/39; a stricter pick metric |

The earlier "report high / tool mechanical" verdicts on twin/attractor cells were
wrong: the report is correct on its own population. Tool pick-stage is a different
metric (it drops two `mondial_geo→world` rows that graded correct, which is the
entire `world` 12→10 gap).

| Claim | Report | Tool (pick-stage) | Report population | Verdict |
|---|---|---|---|---|
| Seeded §1 table / wrong_shape | 139 / 155 | 138 / 156 | — | **口径未定，无法判定** (AST parser → 138/156; naive parse → 140/155). Partition sum matches. |
| Rank overrides (“44 misroutes overrode better rank”) | 44 | 41 | **44** | **不同 population；报告在它自己的口径上正确.** Tool pick-stage is another metric. |
| Twin: mondial_geo → world | 10 / 3 | 8 / 3 | **10 / 3** | **不同 population；报告在它自己的口径上正确** |
| Twin: simpson_episodes → law_episode | 8 / 1 | 6 / 1 | **8 / 1** | **不同 population；报告在它自己的口径上正确** |
| Twin: regional_sales → superstore | 7 | 7 | 7 | Match |
| Twin: food_inspection ↔ food_inspection_2 | 6 / 1 | 6 / 1 | 6 / 1 | Match |
| Twin: soccer_2016 → ice_hockey_draft | 3 | 3 | 3 | Match |
| Attractor: superstore | 12 | 11 | **12** | **不同 population；报告在它自己的口径上正确** |
| Attractor: world | 12 | 10 | **12** | **不同 population；报告在它自己的口径上正确** |
| Attractor: ice_hockey_draft | 9 | 9 | 9 | Match |
| Attractor: law_episode | 8 | 6 | **8** | **不同 population；报告在它自己的口径上正确** |
| Attractor: movies_4 | 7 | 5 | **7** | **不同 population；报告在它自己的口径上正确** |
| Attractor: food_inspection_2 | 7 | 7 | 7 | Match |
| Extra DISTINCT (stage-4) | 75 | **76** | — | **Report cell wrong** (independent recomputation = 76; no reasonable variant yields 75) |
| Over-join (stage-4) | 113 | **41** (was 110 before frozen exclusion) | — | **Both report 113 and prior tool 110 include ~69 frozen-gold noise.** Tool now excludes `is_frozen_constant` gold. §5 recommendation #3 marked 待重算. |
| Missing DISTINCT / LIKE | 19 / 26 | 19 / 26 | — | Match |

Other §1 waterfall cells (except the seeded table/wrong_shape cell above), EX
rates, and the pick-wrong gold-rank histogram (26/31/39) match the
error-analysis doc on the fixed2 run.

**Stage-3 coverage gate note:** frozen / empty-`gold_tables` rows skip
`if gold_tables and ...` in `funnel_stage`, so they never enter stage 3 and land
in stage 4 — inflating stage 4 relative to stage 3. Documented; cascade left
unchanged to keep waterfall cells that currently match the report.

---

## N15 questions.jsonl size

Side-car chosen over inlining question + gold on every generations row.

| Artifact | Rows | Size |
|---|---:|---:|
| Fixed2 recomputed sidecar (1351 unique qids from the ladder generations) | 1351 | **200853 bytes** |
| N17 5q smoke (`runs/datalake/20260731T220403Z/questions.jsonl`) | 5 | **3325 bytes** |

---

## N16 (already landed — multi-schema verify)

- Always-note budget: per-turn scope already in `corpus/validate.py`; unit
  `tests/test_corpus.py::test_always_note_budget_is_per_schema_not_pooled`.
- Quotable copy by finding code: `corpus_finding_codes` + diverted messages in
  `eval/index.py` (`2c6cb3a`).
- `sme_noop_dbs` lottery: accepted this batch (no floor).
- Full `not_quotable_because == []` still waits on a future paid ladder (out of N17 scope).

**Multi-schema re-validation (B1 — the false positive needs a pooled corpus):**

```text
_load_built_corpus(fixed2/corpus_curated_sme, 4 schemas) → always-note-budget = 0
_load_built_corpus(fixed2/corpus_curated_sme, all 57)   → always-note-budget = 0
  (3070 assets, finding_count = 0)
```

Zero model calls — load already-built corpora and `validate_corpus`. A single-schema
smoke cannot reproduce the original pooled-budget false positive; this can.

---

## N17 runbook anchors

File: [experiment-runbook.md](experiment-runbook.md)

| Anchor / heading | What landed |
|---|---|
| `## Step 2 — the real run` | Full ladder with `--split` / `--build-workers` / `--workers` / `--replicate` / optional `--dbs`; **no `--model`** |
| Same section, TOML preflight | `Settings.for_env(Environment.dev).models.llm_model` then re-check `manifest.json` `model` |
| `### Hard MDE bound on the SME step` | Hard sentence: ~9.03% floor / MDE ≈ 2.3pp / measured SME −0.15pp (~0.2pp) → 「未检出」 not 「无效果」. MDE source = arm discordance (upper bound on decode noise), not re-serve replicate. |

---

## N17 zero-question schema guard

| Piece | Location |
|---|---|
| Pool partition | `run_datalake._prepare_scored_pool` → `_quarantine_zero_question_schemas` — empty schemas leave the *serve / census / routing* pool after build (corpora were still built; model cost already spent) |
| Leakage order | `_assert_train_test_disjoint` runs on the servable set only (after quarantine) |
| Summary field | `dbs_zero_questions` |
| Hygiene | `index.record_for_run` + `quotable()` reason (“zero questions… not built-but-unscored”) |
| Tests | `tests/test_zero_question_guard.py` — helper + **driver wiring** (`_prepare_scored_pool` → `dbs_zero_questions`; census scoped to servable) |
| Spec note | [eval-rebuild.md](eval-rebuild.md) §4 deferred note updated to “landed M5 N17” |

---

## N17 5q smoke (not the full ladder)

**Preflight**

1. Model: `Settings.for_env(Environment.dev).models.llm_model` → `gpt-5.6-luna`.
2. Manifest `model` on the smoke → `gpt-5.6-luna`.
3. Postgres: local decoy on `127.0.0.1:5435` via `GOVERNED_BI_PG_DSN` from `.env`
   `PG_RENAME_DECOY_DSN` (port check succeeded).

**Command**

```bash
uv run python -m governed_bi.eval.run_datalake --dbs beer_factory --limit 5 --arms baseline --workers 1 --out runs/datalake
```

**Result:** `runs/datalake/20260731T220403Z`

| Check | Result |
|---|---|
| Questions | `n_questions=5`, `generations.baseline.jsonl` = 5 rows |
| EX | baseline `ex_lenient=0.600` |
| Analysis | `analysis.json` written by driver |
| Zero-question field | `dbs_zero_questions=[]` |
| Quotable | Indexed **not** quotable — expected (`5 < MIN_QUOTABLE_QUESTIONS`). Smoke only. |

**Not run:** full 57-db four-arm `--replicate` paid ladder (~2h / ~200M tokens).
