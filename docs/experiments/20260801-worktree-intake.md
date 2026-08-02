# Worktree intake, 2026-08-01

Two experiment worktrees (`../gbi-provider-b`, `../gbi-recursion80`) were folded back
into this repo and removed. `runs/` is git-ignored, so everything below is local disk,
not history — this file is the record.

## What is usable

**Exactly one artifact:** `runs/datalake/provider-b/20260801T-ladder-v3`, the `curated`
arm only.

| field | value |
|---|---|
| model | `deepseek-v4-flash`, `reasoning_effort=max` |
| embedder | `text-embedding-3-large` (OpenAI — deliberately *not* moved) |
| corpus_content_hash | `ec728fb6aa89943e` (byte-identical to the luna arm's) |
| n | 1351 questions, 57 schemas |
| crash_rate | 0.000 |
| refusal_rate | 0.005 |
| `ex_gradeable` | 0.508 |
| `ex_lenient` | 0.487 |
| `ex_no_twin` (headline) | 0.484 |
| `schema_pick_accuracy` | 0.868 |

The run as a whole is **not quotable** and the ledger says so:

```
not_quotable_because: ["arms crashed during serve (baseline=1.0, seeded=0.4715)"]
```

That is the gate working. The `curated` arm is clean in isolation and is what the
"DeepSeek 55.8% EX|pick" comparison was drawn from; the *run* cannot be quoted as a
ladder because two of its three arms are crash records.

## What is a crash record, not a result

| run | arm | n | outcome |
|---|---|---|---|
| `20260801T-ladder-v3` | baseline | 1351 | 100% crashed |
| `20260801T-ladder-v3` | seeded | 1351 | 47% crashed |
| `20260801T-ladder` | baseline | 1351 | 48% crashed (EX 0.122) |
| `20260801T-ladder` | seeded | 1351 | 100% crashed |
| `20260801T-ladder` | curated | 4 | aborted |
| `20260801T-ladder-v2` | curated | 245 | partial, EX 0.469 |
| `recursion80/20260801T-baseline` | baseline | 5 | smoke only |

### Cause, from the surviving logs

`run.log` for both DeepSeek ladders is dominated by **HTTP 402 Insufficient Balance**,
not by rate limiting:

| | 402 / "Insufficient" | 429 |
|---|---|---|
| `20260801T-ladder` (v1) | 2168 / 2113 | 100 |
| `20260801T-ladder-v3` | 2031 / 1973 | 65 |

This **revises an earlier attribution.** The v1 ladder's collapse was previously
described as an OpenAI-embedding TPM blowout at high worker count. The 429s are real
but are two orders of magnitude rarer than the 402s in the log that survived; the
dominant killer was the DeepSeek account running out of balance mid-run. The embedding
blowout may still explain some of the 429s — it is not the explanation for the crashed
arms.

Crash rows carry `error="refusal"` with `outcome="crashed"`, which is
`CRASH_REFUSED_BY` / `model_error` doing its job: an internal exception degrading to a
fail-closed refusal, correctly classified as a crash rather than absorbed into
`refusal_rate`. This is the machinery added on 2026-07-25 catching exactly the failure
mode it was written for.

## Files lost in intake — my error

The copy loop for the two crash-record runs expanded `generations.*.jsonl` against the
**destination** working directory instead of the source, so the `[ -f ]` guard silently
matched nothing and those files were never copied. `git worktree remove --force` then
deleted the source tree. Not recoverable.

Lost:

- `20260801T-ladder/generations.{baseline,seeded,curated}.jsonl`
- `20260801T-ladder-v2/generations.curated.jsonl`

Survived for those two runs: `manifest.json`, `questions.jsonl`, `run.log`,
`stage_events.jsonl`.

**Cost.** Low but not zero. The crashed arms had no result value, and their diagnosis
survives intact in `stage_events.jsonl` (per-question `agent_core` `error` counts, which
is how the table above was reconstructed) and `run.log`. The one real loss is
`-v2`'s 245 `curated` rows: its manifest differs from `-v3` **only** in
`serve_workers` (6 vs 16) and timestamps, so it was the same treatment at lower
concurrency and the row-level 6-vs-16 concurrency check is no longer possible. The
arm's full 1351-row measurement at 16 workers is unaffected.

`20260801T-ladder-v3` was copied with `cp -r` and is complete — all three
`generations.*.jsonl` at 1351 rows, plus `analysis.json`, `arms_summary.json`,
`deltas.json`, `summary.json`, corpus trees.

## Also carried over

- `runs/index.jsonl` — the `20260801T-ladder-v3` ledger row appended (7 rows total).
  The three `runs/smoke*` rows were **not** merged: their directories did not come
  across, and a ledger pointing at absent runs is worse than a short ledger.
- `runs/datalake/provider-b/governed_bi.local.toml.provider-b-record` — the DeepSeek
  config, kept as a record. Deliberately *not* installed over the working
  `governed_bi.local.toml`. Scanned: no secrets.
- `runs/datalake/provider-b/runs.sqlite` — the ADR-0004 local run log (168 KB, smoke
  traffic only; the eval path forces `run_log_kind="off"`).
- Console logs (`console.log`, `console-v2.log`, `console-v3.log`).

Corpus trees for `-ladder` and `-ladder-v2` were skipped on purpose: all three runs
share `corpus_content_hash=ec728fb6aa89943e`, so `-v3`'s copy is the same bytes.

## Branches

- `exp/provider-b` — merged into `main`, worktree removed, branch deleted.
- `exp/luna-max-routing` — identical to `main` (`dd4589e`), branch deleted.
- `exp/recursion80` — **kept.** One unmerged commit (`e7f6a15`) holding exactly one
  line, `AGENT_RECURSION_LIMIT = 40 → 80` in `analyst/middleware.py`. It is an
  experiment *treatment*, not a fix, and must not be merged to `main`. Trivially
  reproducible from this note if the branch is ever dropped.
