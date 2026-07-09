# Architecture and Flow Diagrams

_[English](diagrams.md) · [简体中文](diagrams.zh.md)_

This directory is split by level of detail so individual Mermaid diagrams are
easy to review and fix. The diagrams intentionally distinguish implemented code
from design-level scaffolding.

> **Implementation note:** the whole ask -> answer pipeline is implemented and
> tested (corpus, gateway + five-layer guardrails, graph + Steiner planner,
> retrieval, context assembly, serve flow with self-repair + SQL cache, memory,
> eval, viz), and both agent harnesses (LangGraph serve DAG, deepagents curator)
> are built behind the `agents` extra. These diagrams show the contracts; a few
> nodes labelled "future" are seams (e.g. Neo4j, live-model curation).

## Recommended reading by complexity

### L0: orientation

1. [System overview diagrams](diagrams/overview.md)
   - Current code status
   - Target architecture
   - Two-harness split
   - Environment toggles

### L1: subsystem flows

2. [Corpus diagrams](diagrams/corpus.md)
   - Corpus consumption contract
   - Loader internals
   - Validation internals
   - Pydantic asset model
   - Graph projection edge taxonomy
3. [Server diagrams](diagrams/server.md)
   - Answer pipeline
   - Ask-question sequence
   - SQL semantic-cache sequence
   - Refuse-gate sequence
   - Reliability/governance enforcement
4. [Curator diagrams](diagrams/curator.md)
   - Build-loop data flow
   - Asset lifecycle state machine
   - Proposer/adversary sequence
5. [Viz diagrams](diagrams/viz.md)
   - Cockpit subsystem
   - Audit/certification sequence
6. [Evaluation diagrams](diagrams/eval.md)
   - Three-arm evaluation
   - Refuse-gate evaluation

### L2/L3: worked examples and deep dives

7. [Beer factory example diagrams](diagrams/beer-factory.md)
   - Example semantic mini-graph
   - Example top-rated-brand question sequence
   - Example refusal path

## Source map

| Diagram file | Main sources |
|---|---|
| [overview](diagrams/overview.md) | `docs/architecture.md`, `docs/system-overview.md`, `src/governed_bi/config.py` |
| [corpus](diagrams/corpus.md) | `src/governed_bi/corpus/`, `docs/asset-schemas.md`, `src/governed_bi/graph/projection.py` |
| [server](diagrams/server.md) | `docs/server.md`, `src/governed_bi/server/`, `gateway/`, `retrieval/`, `graph/` |
| [curator](diagrams/curator.md) | `docs/curator.md`, `src/governed_bi/curator/` |
| [viz](diagrams/viz.md) | `docs/viz.md`, `src/governed_bi/viz/` |
| [eval](diagrams/eval.md) | `docs/architecture.md` §8, `src/governed_bi/eval/` |
| [beer-factory](diagrams/beer-factory.md) | `corpus/beer_factory/` |


