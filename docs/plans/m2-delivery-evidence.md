# M2 delivery evidence (N5–N8)

Paste into each PR body. Recorded 2026-07-31 while closing the memoryless review gaps against [batch-m2.md](batch-m2.md).

## N5 · glossary

- Seven homonym traps sit **before** the product term table (section `## Homonym traps`).
- Ops/eval senses cite `file:line` where a concrete anchor exists.
- `docs/glossary.zh.md` untouched (AGENTS.md mid-work English-only).
- Coverage test: `tests/test_glossary_covers_load_bearing_terms.py` (`REQUIRED` hardcoded).
- Post-review add: **`run` / `run_id` / `turn_id`** (rebuild-checklist 1.4.1 — omitted from the original batch-m2 42-word list; `runs/<dir>/` is not keyed by `run_id`).
- Deferred to M3 **N10a**: rvgd `phys_to_table` ↔ `Corpus.table_by_name` consistency pin (do not fold hot path into O(n²)).

## N6 · consolidations

### Slug empty-hit scan

Scanned `corpus/` and sibling `../BIRD-corpus` schema + table `physical_name` / directory names through `corpus.ids.slug(..., fallback="")`:

| Root | Names surveyed | Empty-slug hits |
|---|---|---|
| `corpus/` | 6 | **0** |
| `../BIRD-corpus/` | 758 | **0** |

**Result: zero hits — defensive fix only** (profile’s missing `or "x"` never fired on current corpora).

### Also in this item

Fourth `_FROZEN_GOLD_RE` copy in `eval/leakage.py` folded onto `is_frozen_constant` (same byte-identical regex as `analysis` / `run_datalake`). Related to 6a; called out so it is not silent scope creep.

`_render_*` renamed distinctly — **not** merged.

## N7 · `table_by_name` behavior change

On BIRD-corpus, **27 bare physical names** are carried by more than one table (67 table assets). Call sites previously returned the **first** matching `TableAsset` in `corpus.assets` order. They now return **`None`** via `Corpus.table_by_name`.

**Eval impact (directionally good, numbers move):** turns that silently bound the wrong schema’s table will refuse or retry instead of answering from a sibling. Do not treat EX / refusal deltas on those names as noise when comparing pre/post N7 runs.

**Exclusion order (intentional):** `Corpus.table_by_name` does **not** skip `governance.excluded` (same contract as `by_id`). A bare name that is unique among Analyst-visible tables but shared with an excluded sibling is still ambiguous corpus-wide → `None`. Old call-site scans skipped excluded assets while matching and could return the live table. Tests: `test_table_by_name_does_not_apply_exclusion_filter`, `test_tools_excluded_sibling_makes_bare_ambiguous`.

C13 closed in `docs/open-work.md`.

## N8 · version pins + `uv sync -U` / `uv lock -U` probe

**Mechanism A** (`[tool.uv] constraint-dependencies`) chosen because this package does not import `langgraph_api` / `langgraph_sdk`; they own the Server wire protocol transitively via `langgraph-cli[inmem]`.

Declared ranges: see `docs/architecture.md` §9 and `pyproject.toml`.

Probe (2026-07-31): `uv lock -U` refreshed the lock, then **restored** with `git checkout -- uv.lock` (N8 must not land upgrades). Observed resolutions still inside bounds:

| Package | Locked at land | After `-U` (not committed) | Bound |
|---|---|---|---|
| `langgraph` | 1.2.8 | 1.2.10 | `>=1.0,<2` |
| `langgraph-cli` | 0.4.30 | 0.4.31 | `>=0.4,<0.5` |
| `langgraph-api` | 0.11.0 | 0.11.2 | `>=0.11,<0.12` |
| `langgraph-sdk` | 0.4.2 | (unchanged in `-U` output) | `>=0.4.2,<0.5` |

`uv sync -U` hit Windows file lock on `.venv/.../langgraph.exe` (process in use); lockfile-only `uv lock -U` was used for the version check. Committed `uv.lock` diff is **constraints + declared specifiers only** — no package version bumps.
