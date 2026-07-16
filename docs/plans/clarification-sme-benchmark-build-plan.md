# Build plan: clarification protocol + SME-growth benchmark (D12-D14)

Working implementation plan for the decisions recorded as
[D12-D14](../design-decisions.md). This is a working doc, not a canonical design
doc (English only). Decisions are settled; this is the how and the order.

> **Status (2026-07-15).** Increments 1–2 (offline foundation + curator
> clarification loop) are **shipped**. Increment 3 (corpus repo + multi-DB
> harness) is the still-open **scale run** (Audit dispositions R1: 69 schemas,
> 8,134 train / 2,030 test). Increment 4's Simulated SME is shipped, **but its
> gold arm is superseded**: the de-obfuscation `build_gold_corpus` oracle is
> **retired** and the ceiling is redefined as a *test-aware SME oracle* (index-
> scoped retrieval, pull-based) — see the
> [D14 amendment](../design-decisions.md#d14-sme-growth-benchmark-on-bird-obfuscation)
> and [Audit dispositions R-gold](../design-decisions.md#audit-dispositions-2026-07-15).
> Treat the `round 1 / round 2 / gold` framing below as replaced by that ladder.

## The four workstreams

1. **Engine core (D12).** The two primitives the engine owns: a typed
   `Clarification` block on the `Audit` tier, and an `accept_answer()` write
   primitive (the generalization of the once-sanctioned `certify()`).
2. **Corpus repo + multi-DB harness (D13, D14).** The semantic layer moves to its
   own git repo, loaded via `[paths].corpus_root`; the benchmark harness iterates
   `db_id`s, growing one per-DB corpus each and scoring the pooled test set. (Per
   D15, the corpus namespace field is `schema`, not `db`; for BIRD each `db_id`
   maps to a single schema. `db_id` here is BIRD's dataset/connection identifier
   and is kept as-is.)
3. **Curator clarification loop (D12, D14).** The curator detects a knowledge gap
   while working a train question, emits a `Clarification`, receives an answer
   through a `Responder`, and ingests it via `accept_answer → write_corpus →
   validate`.
4. **Simulated SME + arm/table runner (D14).** The eval-only `Responder` (an LLM
   briefed with domain meaning, never a held-out test answer) plus the runner that
   produces the point-estimate table (`no-layer` / `facts-only` / round 1 / round
   2, gold optional).

## Dependency order

```
WS1 (engine core)  ──►  WS3 (curator loop)  ──►  WS4 (simulated SME + runner)
        │                                              ▲
        └────────────►  WS2 (corpus repo + harness) ───┘
```

WS1 is the root. WS2's harness and WS4's `bird_loader` are independent of WS1 and
can be built in parallel. WS3 and WS4's runner need WS1.

## Build increments

### Increment 1 — offline foundation (this pass)

Two independent, fully offline, unit-tested units. No live model, no external
repo, no network.

- **WS1 engine core.**
  - `corpus/schemas.py`: add `ClarificationStatus(open|answered)` and a
    `Clarification` model (`status`, `question`, `asked_by?`, `answer?`,
    `answered_by?`, `at?`), and an optional `clarification: Clarification | None`
    field on the `Audit` tier (never served, so questions never leak to the
    Analyst). Reuse the existing Inference tier + low `confidence` + `suspect`
    caveat for the provisional guess; no new field for that.
  - `corpus/clarify.py` (new): `accept_answer(asset, *, by, answer, edits=None,
    reason=None, at=None, status=certified)` returns a deep copy with the
    clarification flipped to `answered` (answer + `answered_by` recorded), any
    `edits` applied to the Inference tier, and `Provenance` stamped
    (`source=human`, `status`, `by`/`reason`/`at` via the tier's `extra="allow"`).
  - `corpus/__init__.py`: export `Clarification`, `ClarificationStatus`,
    `accept_answer`.
  - `tests/test_clarification.py`: an asset with an open clarification →
    `accept_answer` → assert answered, provenance `source=human`, edits applied,
    and that the clarification never appears in `for_analyst()`.

- **WS4 partial — `bird_loader`.**
  - `eval/bird_loader.py` (new): `load_bird_items(dataset_dir, db_id,
    split="test") -> list[EvalItem]` reading `test_final.jsonl` /
    `train_final.jsonl`, filtering by `db_id`, mapping `question` + `sql_sqlite`
    (the un-obfuscated gold) into `EvalItem`. Pure stdlib json; no dependency on
    the sibling repo at import time (dir is a parameter).
  - `tests/test_bird_loader.py`: write a tiny fixture jsonl to a tmp dir, load it,
    assert filtering by `db_id` and the `question`/`sql` mapping. No dependency on
    the real BIRD checkout.

### Increment 2 — the curator clarification loop (WS3)

- A `curator` step that, given a Facts-only asset it cannot confidently describe
  (low `confidence` / `role=unknown`), emits a `Clarification` instead of (or
  alongside) a guess. Detection reuses the existing confidence/`suspect` signals.
- A `Responder` protocol (`answer(question, context) -> str`) with a trivial
  offline double for tests. The human/CSV path stays downstream (out of engine
  scope, per D6 / 2026-07-08).
- The ingest path: parse the free-text answer into a structured edit, call
  `accept_answer`, then `write_corpus` + `validate`.

### Increment 3 — corpus repo + multi-DB harness (WS2)

- Create the separate corpus repo (external; needs a git repo the engine points
  at via `[paths].corpus_root`). Cannot be created from inside this repo alone.
- A benchmark harness (`eval/` or `scripts/`): a `db_id -> {connector config,
  corpus path}` registry, iterating the BIRD DBs, growing each corpus, scoring the
  pooled test set. Harness-only; not the production serving path.

### Increment 4 — Simulated SME + arm/table runner (WS4)

- `SimulatedSme` (eval-only `Responder`): an LLM briefed with domain meaning,
  answering one question at a time, never handed a held-out test question's gold
  SQL. Needs a live model to run.
- `NoLayerSolver` (Arm 1): same serve stack as `flow_solver`, given only raw
  (obfuscated) schema, no corpus.
- `build_gold_corpus()` (Arm 3, `eval/gold.py`): implement the deterministic
  de-obfuscation oracle from the BIRD manifests (`schema_rename_map.json` +
  decoy/trap manifests + original schema). Needs the manifest formats + original
  schema; the hardest, XL piece.
- The table runner: `no-layer` / `facts-only` / round 1 / round 2 (+ gold),
  serve-time compute held identical across arms, SME cost as the training-time
  axis, `beer_factory` first then pooled across DBs.

## Deferred and why

- **Corpus in its own repo (WS2 external repo)** — needs a new git repository
  outside this one; set up by the user, then pointed at via `[paths].corpus_root`.
- **Live-model pieces (Simulated SME, NoLayerSolver runs, the real table)** —
  need `OPENAI_API_KEY` and a run; gated exactly like the existing live smoke.
- **`build_gold_corpus`** — XL, and gated on the BIRD manifest formats + original
  schema; deferred to Increment 4.

## Test + verify

Every increment ships offline unit tests. Run `uv run pytest` after each. The
live pieces (Increment 4) verify against a real model separately, like the
existing `scripts/live_smoke.py`.
