# Glossary

_[English](glossary.md) · [简体中文](glossary.zh.md)_

Canonical terms for the current tree. When a term conflicts with casual usage,
this page wins. Binding design detail is in the [ADRs](adr/).

| Term | Definition |
|---|---|
| **Corpus** | Human-owned semantic substrate: typed YAML assets (schemas, tables, columns, joins, metrics, terms, few-shots, notes, negatives) loaded by `corpus/`. |
| **Note** (`NoteAsset`) | Governed annotation attachable to assets or namespace scopes. See [ADR 0003](adr/0003-governed-notes-tri-modal-retrieval.md). |
| **Facet** | One retrieval channel (schema / term / metric / entity / example) with its own query rewrite. See [ADR 0011](adr/0011-two-model-split-and-facet-query-rewriting.md). |
| **Route / resolve / connect** | Schema selection, pass-two budgets, then Steiner-tree join completion over retrieved components. |
| **Assemble** | Render the retrieval context block injected into model calls. |
| **`agent_core`** | Nested `create_agent` loop with read-only tools (`read_body`, `inspect_schema`, `sample_rows`, `run_query`, `ask_user`). |
| **Governance / check** | Seven deterministic layers on SQL before execution (`PARSE`, `NO_WRITE`, `FUNCTIONS`, `BINDING`, `COLUMNS`, `TABLES`, `COST`). See [ADR 0006](adr/0006-execution-time-governance.md). |
| **Ledger** | Attempt book for governed tool calls (not the eval run registry). |
| **Reliability stamp** | Two axes on a delivered answer: `safety_clearance` (bool) and `semantic_assurance` (`unflagged` / `heuristic` / `unverified`). `unflagged` means no uncertainty flag fired — not “verified correct.” |
| **Graded delivery** | Emit SQL/result with `semantic_assurance=unverified` instead of hard-refusing some semantic failures. Not “graded against gold.” |
| **Knob** | Declared setting in `register/knobs.py` (defaults, roles, hash participation). |
| **Main model / utility model** | Large model for SQL generation vs small model for scope gate / facet rewrite ([ADR 0011](adr/0011-two-model-split-and-facet-query-rewriting.md)). |
| **Stage event** | Custom stream event for a visible turn timeline ([ADR 0010](adr/0010-live-stage-events.md)). |

## Homonym traps

| Trap | What it is **not** |
|---|---|
| **`semantic_assurance=unflagged`** | Not verified-correct and not “well grounded in retrieval.” |
| **`safety_clearance=False`** | Not only “the SQL was unsafe” — any uncleared delivery path stamps false. |
| **graded delivery** | Not scoring against gold (`grade` / `grader` / `hash_grade` mean score-against-gold). |
| **ledger** | Not the eval runs index; here it means the governance attempt book. |
| **server** | Infra (LangGraph Server). The product agent path is `serve/`, not a package named Analyst. |
