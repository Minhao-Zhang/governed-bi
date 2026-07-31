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
| N16 | `2c6cb3a` | Quotable corpus findings diverted by code; always-note verify accepted |
| N17 | this batch | Zero-question guard + runbook (full ladder / TOML / hard MDE) + 5q smoke |

---

## N15 named mismatches (tool vs report)

Canonical tooling path:
`runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z`.
Pinned in `tests/test_bird_basis_report.py`.

Population for twin/attractor cells: **BIRD-basis pick-stage** (gold shortlisted,
picker chose another; n=96). Report §3 twin/attractor cells look counted on a
broader `routed_hit=False` set; even that set does not fully reproduce every
attractor. Tool keeps the pick-stage definition; report is named high where it
diverges.

| Claim | Report | Tool | Verdict |
|---|---|---|---|
| Seeded §1 table / wrong_shape | 139 / 155 | 138 / 156 | **Report cell wrong**; sum of the two stages matches. Tool is the defined cascade. |
| Rank overrides (“44 misroutes overrode better rank”) | 44 in `…-results.md` | **41** (`schema_pick_report` rank_overrides on BIRD-basis pick stage) | **Report high / tool mechanical.** Tool is the shortlist-index comparison; results.md is high. |
| Twin: mondial_geo → world | 10 / 3 | **8 / 3** | **Report high / tool pick-stage.** Report counted a broader misroute set. |
| Twin: simpson_episodes → law_episode | 8 / 1 | **6 / 1** | **Report high / tool pick-stage.** Same broader-set story. |
| Twin: regional_sales → superstore | 7 | 7 | Match |
| Twin: food_inspection ↔ food_inspection_2 | 6 / 1 | 6 / 1 | Match |
| Twin: soccer_2016 → ice_hockey_draft | 3 | 3 | Match |
| Attractor: superstore | 12 | **11** | **Report high / tool pick-stage** |
| Attractor: world | 12 | **10** | **Report high / tool pick-stage** |
| Attractor: ice_hockey_draft | 9 | 9 | Match |
| Attractor: law_episode | 8 | **6** | **Report high / tool pick-stage** |
| Attractor: movies_4 | 7 | **5** | **Report high / tool pick-stage** |
| Attractor: food_inspection_2 | 7 | 7 | Match |
| Extra DISTINCT (stage-4) | 75 | **76** | Tool +1 vs report |
| Over-join (stage-4) | 113 | **110** | Tool −3 vs report |
| Missing DISTINCT / LIKE | 19 / 26 | 19 / 26 | Match |

Other §1 waterfall cells (except the seeded table/wrong_shape cell above), EX
rates, and the pick-wrong gold-rank histogram (26/31/39) match the
error-analysis doc on the fixed2 run. **The twin/attractor matrix does not
blanket-match** — mismatched cells are named in the table above.

---

## N15 questions.jsonl size

Side-car chosen over inlining question + gold on every generations row.

| Artifact | Rows | Size |
|---|---:|---:|
| Fixed2 recomputed sidecar (1351 unique qids from the ladder generations) | 1351 | **200853 bytes** |
| N17 5q smoke (`runs/datalake/20260731T220403Z/questions.jsonl`) | 5 | **3325 bytes** |

---

## N16 (already landed — pointer only)

- Always-note budget: per-turn scope already in `corpus/validate.py`; unit
  `tests/test_corpus.py::test_always_note_budget_is_per_schema_not_pooled`.
- Quotable copy by finding code: `corpus_finding_codes` + diverted messages in
  `eval/index.py` (`2c6cb3a`).
- `sme_noop_dbs` lottery: accepted this batch (no floor).
- Full `not_quotable_because == []` still waits on a future paid ladder (out of N17 scope).

---

## N17 runbook anchors

File: [experiment-runbook.md](experiment-runbook.md)

| Anchor / heading | What landed |
|---|---|
| `## Step 2 — the real run` | Full ladder with `--split` / `--build-workers` / `--workers` / `--replicate` / optional `--dbs`; **no `--model`** |
| Same section, TOML preflight | `Settings.for_env(Environment.dev).models.llm_model` then re-check `manifest.json` `model` |
| `### Hard MDE bound on the SME step` | Hard sentence: ~9.03% floor / MDE ≈ 2.3pp / 0.2pp SME → 「未检出」 not 「无效果」 |

---

## N17 zero-question schema guard

| Piece | Location |
|---|---|
| Pool partition | `run_datalake._quarantine_zero_question_schemas` — empty schemas leave `built` before corpora / census / routing |
| Summary field | `dbs_zero_questions` |
| Hygiene | `index.record_for_run` + `quotable()` reason (“zero questions… not built-but-unscored”) |
| Tests | `tests/test_zero_question_guard.py` (synthetic empty db; ledger block; absent-key not accused) |
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
