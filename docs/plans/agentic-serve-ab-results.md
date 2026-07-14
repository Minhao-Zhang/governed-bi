# Agentic serve path — A/B results & methodology

_Evaluates [ADR 0002](../adr/0002-governed-agentic-serve-runtime.md) (governed
agentic serve core) and its [Amendment 1](../adr/0002-governed-agentic-serve-runtime.md#amendment-1-2026-07-13-the-agent-must-receive-the-semantic-layer)
(seed-then-refine). Question: **does the governed agent serve path beat the
deterministic flow on EX, at equal governance?** Runs: 2026-07-13/14._

## TL;DR

- The governed **layer** works on a fresh, decoy-obfuscated schema (restaurant):
  EX 0.217 → 0.348 and **decoy-column touches 61% → 0%**.
- The **agent** serve path, after Amendment 1, is **≥ the deterministic flow in
  4/4 arm comparisons** across two schemas and **strictly beats it on both arms of
  the decoy schema** (restaurant A3: 0.435 vs 0.391).
- **But the edge (+1 question per arm) equals the run-to-run noise floor** — the
  flow itself swung ±1 question on the *same corpus* between runs. Direction is
  consistent and positive; the effect is **not yet statistically proven**.

## Methodology

### Two experiment shapes

1. **Full end-to-end** (`eval/run_experiment.py`): builds the semantic layer from
   scratch — deterministic Facts ingest → **deep-agent curator** over the train
   `(question, gold SQL)` pairs (Arm A2) → **Simulated-SME** enrichment (Arm A3) —
   then scores three arms on the held-out test split:
   - **A1 no-layer** — model → SQL with no semantic layer (baseline / moat floor).
   - **A2 curated** — serve over the curator-built corpus.
   - **A3 SME** — serve over the SME-enriched corpus.
2. **Serve-path A/B** (`scratchpad/ab_serve*.py`): holds **one fixed corpus**
   constant and swaps **only the solver** — the deterministic `flow_solver`
   (`LlmSqlGenerator`, one-shot over `assemble_context`) vs. the `agent_solver`
   (ADR-0002 `create_agent` + governance middleware). This isolates the
   serve-path variable: identical corpus, identical test items, identical gold.
   A2/A3 route through serve; A1 is serve-independent so it is not re-run.

### Metrics (`eval/arms.py::run_arm`)

- **EX** — execution match: run predicted SQL and gold SQL against the live
  Postgres, compare result sets (`eval/ex.py::execution_match`). Primary metric.
- **decoy-touch rate** — share of produced queries referencing a manifest-flagged
  decoy/suspect column. The governance signal (should be ~0 under the layer).
- **governed-path adherence** — share of questions that produced SQL (vs refused).

### Fixed parameters

| | |
|---|---|
| DB backend | `pg_rename_decoy` (Postgres :5435), one schema per DB |
| Gold field | `sql_rename` (the obfuscated identifiers) |
| Model | configured OpenAI reasoning model (gpt-5.6, Responses API) |
| Serve policy | `grade_semantic_failures=true`, `hard_block_suspect_columns=false` (§6: semantic failures deliver-and-grade; L2 + refuse-gate stay hard) |
| Agent bounds | `recursion_limit=40`, `run_query` attempt cap `=3` |
| Corpus | one fixed A2 + A3 per schema; the serve A/B reuses the corpus the full run built (both paths share it) |

### Schemas tested

- **cs_semester** — English→identity rename (decoy injection, trap-avoidance).
- **restaurant** — genuine rename **+ decoy** (`schema_rename_map` + `decoy_map`);
  harder and more representative. 94 train / 23 test.

## Results

### Bug/finding history (why the first agent numbers were invalid)

The live A/B surfaced two defects the 512 offline (fake-model) tests could not:

1. **Recursion-limit crash.** `recursion_limit=15` (ADR Q6 guess) was too low —
   sequential tool calls (G1) inflate step count — and `GraphRecursionError` was
   uncaught, crashing the whole arm. Fixed: limit → 40; exhaustion now fails
   closed to §6.
2. **Blind-agent regression → Amendment 1.** First clean run: agent **0.267** vs
   flow **0.667** on cs_semester, A2==A3. Root cause: the tools exposed only
   table/column *names*, not the curated few-shots/joins/metrics/rules. Fixed by
   seeding `PromptContext.render()` into the agent prompt + seeding the licensed
   scope; `search_corpus` now returns content.

### cs_semester — serve-path A/B (N=15, fixed corpus)

| arm | flow | agent (blind, pre-Amdt 1) | agent (seeded) |
|---|---|---|---|
| A2 | 0.667 | 0.267 | **0.667** (tie) |
| A3 | 0.667 | 0.267 | **0.733** (+1 q) |

### restaurant — full end-to-end (N=23)

Full pipeline (flow serve):

| arm | EX | decoy-touch |
|---|---|---|
| A1 no-layer | 0.217 | **0.609** |
| A2 curated | 0.304 | 0.000 |
| A3 SME | 0.348 | 0.000 |

Serve-path A/B on the built corpus (all 23):

| arm | flow | agent |
|---|---|---|
| A2 | 0.348 | **0.391** (+1 q) |
| A3 | 0.391 | **0.435** (+1 q) |

**Full stack:** A1 no-layer 0.217 → A3 SME + agent **0.435** (≈2×), decoy 61%→0%.

## Interpretation

- **Governance moat holds.** The layer eliminates decoy touches (61%→0% on
  restaurant) and lifts EX at every arm (A1→A2→A3). This is the core result and it
  is well beyond noise.
- **Agent ≥ flow, consistently but marginally.** Across both schemas the agent
  ties-or-beats the flow in every arm (4/4) and strictly wins both arms on the
  harder decoy schema — consistent with the hypothesis that the inspect/repair
  loop helps more where identifiers are deceptive. Amendment 1 (seeding) was
  necessary to get here; without it the agent regressed badly.
- **Not yet significant.** Each agent win is +1 question. The flow itself moved +1
  per arm on the *same corpus* between the `run_experiment` and `ab_serve` runs
  (A2 0.304→0.348, A3 0.348→0.391) — pure LLM nondeterminism. So the agent's
  advantage sits at the ±1-question noise band.

## Threats to validity

- **Small N** (15 / 23) and **single seed** — ±1 question ≈ 4–7% swings.
- **LLM nondeterminism** — reasoning model, non-zero effective variance run-to-run.
- **Single model, single corpus per schema** — curator nondeterminism is excluded
  from the serve A/B *by design* (fixed corpus), but the full-run A-arm deltas do
  carry it.
- **Cost/latency** — the agent path is several model calls per question vs. one for
  the flow.

## Reproduction

```bash
# Full end-to-end (builds corpus + scores A1/A2/A3, flow):
uv run python -m governed_bi.eval.run_experiment --db restaurant
#   → runs/<ts>_restaurant/ (corpus_a2, corpus_a3, generations.*, result.json)

# Serve-path A/B (flow vs agent) on a fixed corpus:
#   edit DB / FIXED / LIMIT at the top of the harness, then:
uv run python scratchpad/ab_serve_restaurant.py
```

The full run also supports `--agent-serve` to route A2/A3 through the agent core,
and `--limit N` to cap test questions.

## Next to settle the agent-vs-flow question

1. **≥3 seeds per arm** on both schemas (or repeat runs) → real effect size vs.
   the noise band.
2. **Per-question win/loss diff** (agent vs flow on the same items) → does the loop
   *fix cases the flow misses*, or just trade different ones? This is the decisive
   check on whether the agent's autonomy earns its cost.

Until then: the agent path is **at least parity, plausibly better on hard
schemas**, and the P2 cutover decision should rest on the governance-ledger /
observability / HITL benefits as much as on EX.
