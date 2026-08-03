# M4 delivery evidence (N12a / N11 / N13 / N14 / N12b)

Local branch only — no `gh`. Against [batch-m4.md](batch-m4.md). Tip at write: `ec7be1c` on `impl/rebuild-first-batch` (pushed).

| Item | Commit | Notes |
|---|---|---|
| Pre-M4 bump guard | `1ea5c57` | Per-version `MANIFEST_KNOBS` snapshots |
| N12a | `526f21a` | `RunContext`, `configure_logging`, `tracing_invoke_config` |
| N11 | `477b453` | Progress / ETA, `run.log`, arms/deltas off stdout |
| N13 | `afe7776` | `git_branch` / `main_git_sha` / `dirty` / `diff_sha256` (operational) |
| N14 | `099833a` | Shared `ServeStack`; usage from call returns |
| N12a sink join | `ec7be1c` | `run_id`/`turn_id` on `stage_events.jsonl`; one INFO line per fair question in `run.log` |
| N12b | this file | Paid 5q accept run below |

---

## N12b preflight

1. **Model:** `Settings.for_env(Environment.dev).models.llm_model` → `gpt-5.6-luna`. Manifest `model` on the accept run → `gpt-5.6-luna`.
2. **Push:** `git push -u origin HEAD` before the accept run; tip `ec7be1c` tracked on `origin/impl/rebuild-first-batch`.
3. **Working tree:** clean (`dirty=false`, `diff_sha256=null`) for the accept run.

Command:

```bash
python -m governed_bi.eval.run_datalake --dbs beer_factory --limit 5 --arms baseline --workers 1 --out runs/datalake
```

(`GOVERNED_BI_PG_DSN` from local Postgres decoy.)

---

## Accept run: `runs/datalake/20260731T195022Z`

| Check | Result |
|---|---|
| **N11** stdout | **25** lines (≤ 50). Progress + ETA on every serve line; arms/deltas only path summaries. |
| **N12a** three sinks | Probe `question_id=train_5247`, `run_id=2ae1f03b9dfc4bd68b7ab0e1b75314a6` present in `generations.baseline.jsonl`, `stage_events.jsonl`, and `run.log`. Langfuse `sessions get` → `id` matches, `n_traces=1`, HTTP 200. (One `run_id` per question by design.) *(Evidence as taken. Langfuse was removed 2026-08-02, D20; the third sink is now a LangSmith trace, whose root-run metadata carries the same `run_id` — re-verified live on 2026-08-02 via `Client().list_runs(is_root=True)`.)* |
| **N13** | `git_branch=impl/rebuild-first-batch`, `git_sha=ec7be1c…`, `main_git_sha=214b678…`, `dirty=false`, `diff_sha256=null` |
| **M1 ledger** | All 5 rows carry projected `governance_ledger` (`action` / `verdict` / `layer` / `sql` / `allowed` / `row_count`); no `result`; `json.dumps` ok |
| Quotable | Indexed **not** quotable — expected (`5 < MIN_QUOTABLE_QUESTIONS` floor of 8). Smoke only. |

Earlier paid probe (pre-sink-fix): `runs/datalake/20260731T194453Z` — used to discover empty `run.log` / missing stage `run_id`; superseded by the run above after `ec7be1c`.

---

## Free items (already landed)

- N14 unit: `tests/test_n14_serve_stack.py` + updated narrator stale-usage tests.
- N13 unit: `tests/test_working_tree_state.py`.
- N12a unit: `tests/test_run_context_logging.py`.
- N11 unit: `tests/test_serve_progress.py`.
