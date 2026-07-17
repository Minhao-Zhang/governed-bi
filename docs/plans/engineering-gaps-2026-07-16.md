# Engineering gaps — 2026-07-16

Durable companion to the visual audit
[`docs/engineering-gaps-2026-07-16.html`](../engineering-gaps-2026-07-16.html)
(Chinese). That file is the full write-up with evidence per gap; this doc is the
tracker: what was fixed, and what is **deferred for discussion**.

Snapshot: `main` after commit `5228f87`. Scope: curator / analyst / eval pipeline.

## Theme

The dangling-binding bug fixed earlier this session was a symptom, not a one-off:
corpus integrity was enforced weakly (no green gate before scoring) and repaired
stochastically (an LLM fix-pass), with failures swallowed. The fixes below close
that seam so integrity is enforced **offline, deterministically, and never
silently skipped**.

## Fixed (2026-07-16)

All offline; each landed with regression tests; full suite green (477 passed).

| # | Gap | Fix |
|---|---|---|
| 1 | No CI-green gate before scoring | `run_experiment` validates each arm corpus; `validate_finding_count` + findings in `summary.json` (`corpus_validation`); loud warn if not green |
| 2 | Reference repair was term-only | `AssetBag.repair_references()` covers `column.references`, `metric.base_table`, join endpoints, `term.binding`, `rule.scope`; `repair_term_bindings` kept as alias |
| 3 | Join `on`-clause unchecked offline | `validate_corpus` parses `on` with sqlglot, flags columns in neither joined table (`join-on-unresolved` / `join-on-unparseable`) |
| 4 | Write tools couldn't update by id | `upsert_term` / `upsert_metric` update in place when `name` is an existing asset id (kills the 6→12 doubling) |
| 5 | Swallowed fix-pass failures invisible | `run_experiment` lifts curator `error` / `fix_pass_error` from per-corpus manifests into `summary.json` (`curator_errors`) + loud warn |
| 6 | Fold & fix-pass shared one agent | `_validate_fix_pass` takes an agent **factory**; the fix-pass gets a fresh agent (clean filesystem backend); only the corpus (`bag`) crosses invokes |
| 10 | Refuse-gate false-refusal unmeasured | Wired `eval_refuse_gate` into `run_experiment` (behind live model): refusal accuracy on a cross-DB negative set (`load_cross_db_unanswerable`), false-refusal reuses the curated_sme arm's refusal_rate; written to `summary.json` (`refuse_gate`) |
| 12 | Broad exception swallows | Narrowed the sqlglot parse swallows (seed.py ×2, pipeline.py, arms.py) to `sqlglot.errors.SqlglotError` so a real bug surfaces instead of "skip this SQL" |
| 13 | Stale bytecode / retired modules | Removed 47 orphaned `.pyc` (incl. the whole stale `server/__pycache__`) and the empty leftover `server/` dir; `__pycache__/` already gitignored |

Note on #10: the **scoring harness already existed** (`eval/refuse_gate.py`) and was
tested behind `@requires_live_serve`; the gap was that nothing wired it into the
headline run or supplied a per-DB negative set. Both are now in place. The
headline refusal-accuracy number still requires a live-model run to produce.

## Deferred — for discussion

Left untouched by request; recorded here so they are not lost.

### #7 — `refute()` is a `NotImplementedError` stub
- **Where:** [`src/governed_bi/curator/adversary.py`](../../src/governed_bi/curator/adversary.py) (`refute`), design in `docs/curator.md` / ADR 0002.
- **What:** The independent LLM adversary (refute-first) from the design is not
  built; the pipeline runs only the structural `review()` signal.
- **Why deferred:** Needs a live model to build and evaluate meaningfully.
- **Discussion:** Is refute-first worth building before the scale run, or is the
  structural `review()` + the new deterministic `validate_corpus` gate enough
  adversarial coverage for now?

### #8 — grader self-check validated on a 5-row sample
- **Where:** [`run_experiment.py`](../../src/governed_bi/eval/run_experiment.py) `validate_gold_hashes_live(..., sample=min(5, len(test)))`.
- **What:** The gold-hash self-check compares the grader against the reference on
  only a 5-row sample, not head-to-head at full scale.
- **Why deferred:** A full-scale head-to-head needs the live DB + run.
- **Discussion:** Raise the sample, or do a one-off full alignment run to bless
  the grader, then keep the cheap 5-row check as a per-run canary.

### #9 — `multi_schema=True` never exercised end-to-end
- **Where:** [`run_experiment.py`](../../src/governed_bi/eval/run_experiment.py) pins `multi_schema=False`. Already tracked in
  [`schema-qualification-scale-risk.md`](schema-qualification-scale-risk.md).
- **What:** The 69-schema scale run is `multi_schema=True`, a regime the eval
  harness has never run end-to-end; the mode-flag threading is unverified
  (guardrail unit logic *is* tested).
- **Why deferred:** Verification needs the live multi-schema DB. (Instrumentation
  — a refusal-reason counter — and a 2–3 schema pre-flight are the offline-ish
  prep already noted in the scale-risk doc.)
- **Discussion:** Do the pre-flight + counter before committing to the full 69.

### #11 — single seed everywhere
- **Where:** documented in [`eval-ladder-results.md`](eval-ladder-results.md) (Threats to validity / Known limitations).
- **What:** All arms are single-seed; `curated` output varies run-to-run with the
  deep agent's exploration, so deltas are directional, not conclusive.
- **Why deferred:** Needs ≥3 live runs per DB.
- **Discussion:** The ≥3-seed run is the gate on every quotable number and on the
  v5 "SME first moved EX" finding — sequencing vs. the scale run.
