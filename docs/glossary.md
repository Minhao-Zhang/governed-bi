# Glossary

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
| **Governance / check** | Deterministic layers on SQL before execution: `PARSE`, `NO_WRITE`, `FUNCTIONS`, `BINDING`, `COLUMNS`, `TABLES` — **six that run** — plus `COST`, which is declared and unreachable (`cost_budget` ships `UNSET` and no deployment surface can set it), so it is absent from `layers_evaluated` on every served turn. See [ADR 0006](adr/0006-execution-time-governance.md). |
| **Ledger** | Attempt book for governed tool calls (not the eval run registry). |
| **Turn record** | What `stamp` projects, declared in `register/record.py`: `outcome`, `guardrail_errors`, `terminal_reason`, `execution` (the per-attempt ledger), `latency_sec`, and the treatment hashes. **There is no reliability stamp** — no field summarises a turn's trustworthiness, and a test bars `safety_clearance` and `semantic_assurance` from `src/`. |
| **Graded delivery** | Re-execute a statement after a `COST`-layer refusal instead of hard-refusing (ADR 0006 §5). Ships disabled with the cost layer. Not “graded against gold”, and it emits no assurance value — the record has no such field. |
| **Knob** | Declared setting in `register/knobs.py` (defaults, roles, hash participation). |
| **Main model / utility model** | Large model for SQL generation vs small model for scope gate / facet rewrite ([ADR 0011](adr/0011-two-model-split-and-facet-query-rewriting.md)). |
| **Stage event** | Custom stream event for a visible turn timeline ([ADR 0010](adr/0010-live-stage-events.md)). |

## Homonym traps

| Trap | What it is **not** |
|---|---|
| **`outcome=answered`** | Not verified-correct and not “well grounded in retrieval.” It means the ledger holds a passing attempt — nothing about whether the answer is right. |
| **`guardrail_errors=0`** | Not “no guardrail refused anything.” It counts attempts that died of an *exception inside* `check()`. A refusal is a working layer and is not an error. |
| **graded delivery** | Not scoring against gold (`grade` / `grader` / `hash_grade` mean score-against-gold). |
| **ledger** | Not the eval runs index; here it means the governance attempt book. |
| **server** | Infra (LangGraph Server). The product agent path is `serve/`, not a package named Analyst. |
