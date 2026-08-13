# Glossary

Canonical terms for the current tree. When a term conflicts with casual usage,
this page wins. Binding design detail is in the [ADRs](adr/).

| Term | Definition |
|---|---|
| **Corpus** | Human-owned semantic substrate: typed YAML assets loaded by `corpus/`. **Eight types**, enumerated in `corpus/schema.py::ASSET_CLASSES`: schema, table, column, join, metric, term, few-shot, negative-example. There is no note or skill asset — ADR 0003 proposed one and [ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) reversed it, putting `summary` / `body` and a `Governance` block on all eight instead. |
| **Facet** | One retrieval channel (schema / term / metric / entity / example) with its own query rewrite. See [ADR 0011](adr/0011-two-model-split-and-facet-query-rewriting.md). |
| **Route / resolve / connect** | Schema selection, pass-two budgets, then Steiner-tree join completion over retrieved components. |
| **Assemble** | Render the retrieval context block injected into model calls. |
| **`agent_core`** | Nested `create_agent` loop with read-only tools (`read_body`, `inspect_schema`, `sample_rows`, `run_query`, `ask_user`). |
| **Governance / check** | Deterministic layers on SQL before execution: `PARSE`, `NO_WRITE`, `FUNCTIONS`, `BINDING`, `COLUMNS`, `TABLES` — **six that run** — plus `COST`, which is declared and unreachable (`cost_budget` ships `UNSET` and no deployment surface can set it), so it is absent from `layers_evaluated` on every served turn. See [ADR 0006](adr/0006-execution-time-governance.md). |
| **Licensed** | The table ids **retrieval found this turn** — facet hits, join endpoints pulled in by `resolve`, and Steiner points added by `connect` (ADR 0006 §8). Closed at `connect`; no tool widens it. It is the TABLES layer's first question and it is **not** a permission. |
| **Authorized** | The table ids **this principal may read**, from an `AccessPolicy` ([ADR 0012](adr/0012-access-seam-principal-and-authorization.md)). A second, independent set: it can only narrow `licensed`, never widen it, and the licence is asked first so the pair is not an oracle for which tables exist. Ships open: `api/graph_app.py::access_policy_from_environment` builds an `OpenAccessPolicy`, which authorizes everything, unless `GOVERNED_BI_ACCESS_POLICY` names a `StaticRoleAccessPolicy` file. |
| **Principal** | The subject a turn is executed for: an id and a set of roles. This repository has exactly one (`govern/access.py::LOCAL_PRINCIPAL`), because `api/auth.py` checks one shared key. No tenant, no user store. |
| **Grant** | What an `AccessPolicy` returns for a principal: a reach (`every_table` / `listed`), authorized tables, denied columns, and declared row predicates. Validated on construction; folded against the corpus's spelling by `govern/access.resolve_grant`. |
| **Ledger** | Attempt book for governed tool calls (not the eval run registry). |
| **Turn record** | What `stamp` projects, declared in `register/record.py`: `outcome`, `guardrail_errors`, `terminal_reason`, `execution` (the per-attempt ledger), `latency_sec`, and the treatment hashes. **There is no reliability stamp** — no field summarises a turn's trustworthiness, and a test bars `safety_clearance` and `semantic_assurance` from `src/`. |
| **Turn log** | The append-only JSONL under `runs/serve/<date>.jsonl` that `api/trace_store.append_turn` writes, one line per finished turn. It is the conversation history: checkpoints hold only a thread's newest turn (`PER_TURN_RESET`), and no checkpointer here is durable. `/audit/turns` projects it. See [ADR 0004](adr/0004-local-first-conversation-run-logging.md). Not `runs/eval/`, which is the measurement driver's own artifact. |
| **Graded delivery** | Re-execute a statement after a `COST`-layer refusal instead of hard-refusing (ADR 0006 §5). `graded_delivery_enabled` ships `True`, but `govern/check.py::graded_delivery_eligible` requires a `COST` failure and `COST` never runs, so nothing in `serve/` calls it: the predicate has tests and no production caller. Not “graded against gold”, and it emits no assurance value — the record has no such field. |
| **Abstention policy** | `serve/nodes/abstain.py` — a named, versioned rule set of deterministic predicates over state the turn already recorded, evaluated between `assemble` and `agent_core`, that declines before the agent spends its `run_query` budget ([ADR 0013](adr/0013-the-declared-abstention-policy.md)). `abstention_policy_enabled` ships `False`. It computes **no** score: there is no confidence or certainty field and there will not be one. |
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
| **`r_table_not_licensed`** | Not "you are not allowed." It means retrieval did not find the table this turn — 19 of the v4 arm's 20 refusals, and every one of them a retrieval miss. The permission answer is `r_table_not_authorized`, which is a different rule ([ADR 0012](adr/0012-access-seam-principal-and-authorization.md) §3). |
| **`r_column_excluded` vs `r_column_not_authorized`** | The first is the corpus hiding a column from **everyone**; the second is **this principal** being denied one. A third, `r_column_not_allowed`, means the corpus declares no such column at all. |
| **row predicate** | Declared, **never applied**. This engine refuses a statement touching a table whose predicate it cannot enforce; it does not inject a `WHERE` clause and there is no enforcement mode that does (ADR 0012 §5). Row-level security belongs on the database role. |
