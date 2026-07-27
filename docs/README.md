# governed-bi design

_[English](README.md) · [简体中文](README.zh.md)_

Design for an agentic BI / Generative-BI system: natural-language questions →
grounded, governed, auditable answers over enterprise relational data.

Near-term target is a **SQLite-proven showcase** (with dialect-pluggable seams
for other engines) that grows a reviewable semantic layer from a seed of
known-good queries — *seed-assisted growth*, not a zero-prior cold start.
Enterprise abstractions are seamed in but toggled off. Evaluated on the
self-built [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) dataset (execution accuracy; cost logged).

## Read in this order

1. [System overview](system-overview.md): what this is, the two harnesses, status.
2. [Architecture](architecture.md): the full design (spine, kernel, services, storage, flow, eval, environments).
3. [Design decisions](design-decisions.md): D1-D18 (+ 2026-07-15 audit dispositions) as ADRs, with alternatives and trade-offs.
4. [Asset schemas](asset-schemas.md): the per-asset YAML field spec (Facts / Inference / Audit tiers).
5. [Curator](curator.md): the build-side proposer + adversary loop. For the exact prompts, see [Curator LLM-call walkthrough](curator-llm-call.md).
6. [Analyst](analyst.md): the serve-side governed agentic core + guardrails. For the exact prompts, see [Analyst LLM-call walkthrough](analyst-llm-call.md).
7. [Viz](viz.md): the read-only audit surface — the presenter view models plus the `governed_bi.api` HTTP API to browse the layer and chat with the governed Analyst (the interactive UI is a separate project).
8. [Measurement](measurement.md): what the eval harness records and where a failure localises — read this when a number looks wrong.
9. [Prompt-variant experiments](prompt-experiments.md): the prompt registry, how a run selects a variant, what gets stamped where, and how to decide which variant a measured failure actually calls for.
10. [Glossary](glossary.md): canonical terms.

[External design sources](references.md) that ground the design.

## Using the repo

The design docs above describe the intended system. For what actually runs
today (the corpus layer and the dev workflow):

- [Walkthrough](walkthrough.md): clone → validate → ask your first question. **Start here.**
- [Usage](usage.md): install, the validate CLI, and the programmatic corpus API.
- [Corpus authoring](corpus-authoring.md): write and validate corpus assets step by step.

Two step-by-step call traces sit alongside the design docs, useful when reading
the code: [analyst sequence](analyst-sequence.md) and
[curator sequence](curator-sequence.md).

## Decision records (ADRs)

Point-in-time decisions. An ADR is never edited to match later reality — a
superseding ADR is added instead, so a stale-looking statement inside one is
intentional history.

| ADR | Status |
|---|---|
| [0001 LangGraph Server chat runtime](adr/0001-langgraph-server-chat-runtime.md) | Accepted 2026-07-10; partly superseded by 0002 |
| [0002 Governed agentic serve runtime](adr/0002-governed-agentic-serve-runtime.md) | Accepted / implemented (`d2fdd6a`) — the sole serve path |
| [0003 Governed notes, tri-modal retrieval](adr/0003-governed-notes-tri-modal-retrieval.md) | Accepted as design 2026-07-22 (D17); not built |
| [0004 Local-first conversation + run logging](adr/0004-local-first-conversation-run-logging.md) | Accepted 2026-07-22 (D18); build not started |

## Working docs (`plans/`) and reviews

Dated working docs, not canonical design. Where one disagrees with the docs
above, the docs above win. **No eval number anywhere in this repo is currently
quotable — every number produced before 2026-07-26 is discarded.**

*Live:*

- [Experiment runbook](plans/experiment-runbook.md): what to run, in what order, and what must be true before a number is worth quoting. **The entry point for any eval work.**
- [Data-lake run](plans/datalake-run.md): the pooled multi-schema run (D15) — runbook and status.
- [Eval audit backlog](plans/eval-audit-backlog-2026-07-22.md): open correctness / efficiency items on the eval harness.
- [Notes + run-logging build plan](plans/implementation-plan-notes-and-run-logging.md): the proposed build order for ADR 0003 + 0004.
- [Clarification + SME benchmark build plan](plans/clarification-sme-benchmark-build-plan.md): D12–D14; increments 1–2 shipped, the scale run still open.
- [HITL clarification contract](plans/hitl-clarification-contract.md): serve-time clarification, server ↔ frontend. Server side implemented.
- [Agent-step visualization](plans/agent-step-visualization.md): frontend spec for the governed serve stream.

*Closed records — kept for history, not for guidance:*

- [Eval ladder results](plans/eval-ladder-results.md): the v5 run. **Numbers discarded**; the arm definitions and terminology of the period survive.
- [Eval concurrency design](plans/eval-concurrency-design.md): the `workers` knob, shipped 2026-07-23.
- [Engineering gaps 2026-07-16](plans/engineering-gaps-2026-07-16.md): audit tracker; a few items still deferred.
- [Schema-qualification scale risk](plans/schema-qualification-scale-risk.md): resolved 2026-07-17 by removing the `multi_schema` mode.
- [Terminology refactor](plans/terminology-refactor.md): 2026-07-16 execution record, **superseded for ladder / arm claims** — use [glossary](glossary.md) and the runbook instead.
- [Pipeline design](pipeline-design.md): curator/build-side pipeline; its serve half shipped differently and was removed.

## The spine (non-negotiables)

- **Two planes.** A semantic/control plane (versioned config + markdown, published via PR/CI) stays separate from a data plane that executes only guardrail-passed SQL. Meaning is defined once and owned by humans.
- **Authority is deterministic; reasoning may be agentic.** The question can be wide and the model reasons in a bounded agentic loop, but *what may execute, what is trusted, and what is recorded* is fixed by middleware, not model discretion (ADR 0002 reversed the earlier "never an autonomous loop" rule). The SQL must be narrow.
- **Fail-closed.** Out-of-scope / missing-coverage / tripped-guardrail returns a refusal or a clarifying question, never a confident wrong number.

## How the docs map to the code

| Doc | Package area |
|---|---|
| [Asset schemas](asset-schemas.md), [Design decisions](design-decisions.md) D9 | `src/governed_bi/corpus/` |
| [Curator](curator.md) | `src/governed_bi/curator/` |
| [Analyst](analyst.md), [Architecture](architecture.md) §6 | `src/governed_bi/analyst/`, `gateway/`, `graph/`, `retrieval/`, `memory/` |
| [Architecture](architecture.md) §8, [Measurement](measurement.md) | `src/governed_bi/eval/`, `src/governed_bi/stages.py` |
| [Prompt-variant experiments](prompt-experiments.md) | `src/governed_bi/prompts/` |
| [Viz](viz.md) | `src/governed_bi/viz/` |
| [Architecture](architecture.md) §9 (environment toggles) | `src/governed_bi/config.py` |
