# Glossary

<!-- plain-names-mapping -->
Files under `runs/` and the accepted ADRs keep the words they were written with.
Those words, and what they mean in the current tree:

| Old word | Current word |
|---|---|
| facet | retrieval channel (`channel` in code) |
| licensed, the licensed set | retrieved tables (`retrieved_tables`) |
| arm | configuration |
| treatment | the setting being tested; treatment hashes are the config hash |
| control (of a comparison) | baseline |
| paired (of two runs) | side by side. The paired test keeps its statistical name |
| knob | setting |
| comparability | whether two runs can be compared |
| gate | check |
| stamp | `finish`, the step that writes the turn record |
| register | registry |
| verdict | result |
| seam | boundary |
| envelope | wrapper |
| witness | evidence |
| `nothing_licensed` | `nothing_retrieved` |
| `licensed_table_evicted` | `retrieved_table_dropped` |
| `r_table_not_licensed` | `r_table_not_retrieved` |
| `knobs_resolved` | `settings_resolved` |
| `facet_hits` | `channel_hits` |
| `facet_degraded` | `channel_degraded` |
| `facet_channels` | `channel_scoring` |
| `n_licensed` | `n_retrieved_tables` |
<!-- /plain-names-mapping -->

Canonical terms for the current tree. When a term conflicts with casual usage,
this page wins. Binding design detail is in the [ADRs](adr/).

| Term | Definition |
|---|---|
| **Corpus** | Human-owned semantic substrate: typed YAML assets loaded by `corpus/`. **Eight types**, enumerated in `corpus/schema.py::ASSET_CLASSES`: schema, table, column, join, metric, term, few-shot, negative-example. There is no note or skill asset — ADR 0003 proposed one and [ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) reversed it, putting `summary` / `body` and a `Governance` block on all eight instead. |
| **Retrieval channel** | One of schema, term, metric, entity, or example, each with its own query rewrite. In code the name is `channel`. See [ADR 0011](adr/0011-two-model-split-and-facet-query-rewriting.md). |
| **Route / resolve / connect** | Schema selection, pass-two budgets, then Steiner-tree join completion over retrieved components. |
| **Assemble** | Render the retrieval context block injected into model calls. |
| **`agent_core`** | Nested `create_agent` loop with read-only tools (`read_body`, `inspect_schema`, `sample_rows`, `run_query`, `ask_user`). |
| **Governance / check** | Deterministic layers on SQL before execution: `PARSE`, `NO_WRITE`, `FUNCTIONS`, `BINDING`, `COLUMNS`, `TABLES`. Six layers. See [ADR 0006](adr/0006-execution-time-governance.md). |
| **Retrieved tables** | The table ids **retrieval found this turn** — channel hits, join endpoints pulled in by `resolve`, and Steiner points added by `connect` (ADR 0006 §8). Closed at `connect`; no tool widens it. It is the TABLES layer's only question and it is **not** a permission. The record field is `retrieved_tables`. |
| **Ledger** | Attempt book for governed tool calls (not the eval run registry). |
| **Turn record** | What `finish` writes, declared in `registry/record.py`: `outcome`, `guardrail_errors`, `terminal_reason`, `execution` (the per-attempt ledger), `latency_sec`, and the config hash. **There is no reliability summary** — no field summarises a turn's trustworthiness, and a test bars `safety_clearance` and `semantic_assurance` from `src/`. `finish` reads 32 `ServeState` fields effectively and writes exactly one (`answer`) plus one `final` stage event; 7 of the 32 are the *decision's* and are declared as `serve/outcome.py::OutcomeInputs`, the other 25 are the registry's own fields read by name. |
| **Read-view** | A frozen dataclass naming every state field one decision reads, with a `from_state` classmethod that is the only reader of the dict. Two exist: `serve/abstention.py::AbstentionInputs` (8 fields) and `serve/outcome.py::OutcomeInputs` (7). The point is that `(state, config) -> dict` declares no read set, and the cost of that was a check treating `Outcome.clarification` as evidence the turn had reached `finish` and dropping those rows from a denominator. Not applied to a field a node merely copies onto the record — see [design-decisions](design-decisions.md) for where the line is. |
| **Turn log** | Retired term (2026-08-18). There is no log: `api/trace_store.py` and `runs/serve/*.jsonl` are deleted. Use **turn record** for what a finished turn produces and **conversation store** for where it lives. The name survives on the wire as `/audit/turns`' `meta.log_dir` and on the `turn_log` constructor parameter, both kept so the swap changed no payload. |
| **Conversation store** | `runs/conversations.sqlite`, an `AsyncSqliteSaver` that `langgraph.json` mounts at `checkpointer.path` (`serve/checkpointer.py::conversation_checkpointer`). One `TurnEntry` per turn accumulates on `ServeState.turns`, which is why a checkpoint now holds every turn of a conversation and not only the newest — `PER_TURN_RESET` clears the per-turn fields, `ACCUMULATING` survives it. A conversation survives a restart and, on this deployment, is **never deleted**: `langgraph.json` sets `checkpointer.ttl` to `strategy: delete` at `default_ttl: 129600` (minutes, so 90 days), but `langgraph-runtime-inmem`'s `sweep_ttl` is `return (0, 0)` ("Not implemented for inmem server") and nothing in `site-packages` calls it, so the sweep cannot fire under `langgraph dev` and the file grows monotonically — measured 2026-08-20, 0 freelist pages (ADR 0014 §4). TTL is live only on the postgres/gRPC path, which this deployment does not run. `langgraph-runtime-inmem` is pinned `>=0.35,<0.36` in `pyproject.toml` (moved from 0.32 on 2026-10-05, with `sweep_ttl` re-checked and still `return (0, 0)`), under a comment saying why, so a minor release that *implements* `sweep_ttl` cannot arrive unannounced and start deleting 90-day-old threads. See [ADR 0014](adr/0014-one-conversation-store.md). The CLI and eval get a second file, `runs/harness-checkpoints.sqlite`, because a benchmark is not a conversation; neither may ever be the analytics warehouse (`assert_not_a_warehouse`). Not `runs/eval/`, which is the measurement driver's own artifact. |
| **Graded delivery** | Re-execute a statement after a `COST`-layer refusal instead of hard-refusing (ADR 0006 §5). `graded_delivery_enabled` ships `True`, but `govern/check.py::graded_delivery_eligible` requires a `COST` failure and `COST` never runs, so nothing in `serve/` calls it: the predicate has tests and no production caller. Not “graded against gold”, and it emits no assurance value — the record has no such field. |
| **Abstention policy** | `serve/abstention.py` — a named, versioned rule set of deterministic predicates over state the turn already recorded, evaluated between `assemble` and `agent_core`, that declines before the agent spends its `run_query` budget ([ADR 0013](adr/0013-the-declared-abstention-policy.md)). `abstention_policy_enabled` ships `False`. It computes **no** score: there is no confidence or certainty field and there will not be one. The rules take `AbstentionInputs`, a typed read-view of the eight fields they need, and never see the state dict; the LangGraph node is `serve/nodes/abstain.py`, which is the adapter between the two and nothing else. Both files were one file until 2026-08-25, when 470 lines against a 400-line soft cap paid for the split at the boundary the read-view had already drawn. |
| **Setting** | A declared default in `registry/settings.py`, including which ones enter the config hash. |
| **Main model / utility model** | Large model for SQL generation vs small model for the scope check and for retrieval-channel rewrite ([ADR 0011](adr/0011-two-model-split-and-facet-query-rewriting.md)). |
| **Stage event** | Custom stream event for a visible turn timeline ([ADR 0010](adr/0010-live-stage-events.md)). |
| **Corpus release** | A tag in the corpus repository plus its `corpus_content_hash`. What a configuration records, instead of a floating directory pointer. `corpus_release` is a declared setting in `registry/settings.py`, and `registry/config_profiles.py::recorded_corpus_release` reads it off a row and refuses a profile that claims a different one. Before 2026-08-23 there was no such name, so a configuration whose difference was the corpus could not declare it. There is no `CorpusRelease` type — the setting names a tag. |

## Homonym traps

| Trap | What it is **not** |
|---|---|
| **`outcome=answered`** | Not verified-correct and not “well grounded in retrieval.” It means the ledger holds a passing attempt — nothing about whether the answer is right. Before 2026-08-18 it also covered turns that executed **no** statement; those are `outcome=no_sql` now, and old rows still carry the old label. |
| **`outcome=no_sql`** | Not a refusal and not a crash: nothing decided and nothing failed. It means the turn ended having run no governed statement. It does **not** say why — the stub, an answer read off the delivered context and a model declining in prose are indistinguishable in the record, deliberately ([ADR 0006](adr/0006-execution-time-governance.md) §5). |
| **`guardrail_errors=0`** | Not “no guardrail refused anything.” It counts attempts that died of an *exception inside* `check()`. A refusal is a working layer and is not an error. |
| **graded delivery** | Not scoring against gold (`grade` / `grader` / `hash_grade` mean score-against-gold). |
| **ledger** | Not the eval runs index; here it means the governance attempt book. |
| **server** | Infra (LangGraph Server). The product agent path is `serve/`, not a package named Analyst. |
| **`r_table_not_retrieved`** | Not "you are not allowed." It means retrieval did not find the table this turn. There is no permission rule: the engine has no principal and no grant. |
| **`r_column_excluded`, `r_column_suspect`, `r_column_not_allowed`** | The corpus hides a column from everyone, the corpus marks it unreliable, or the corpus declares no such column at all. |
| **Return path, observation, patch** | Deleted ([ADR 0015](adr/0015-the-return-path.md)). They named a loop from a reader's note to a corpus edit. An observation here is not `measure/signals.py::Signal`. A patch is not the model's proposed SQL in `eval/projection.py`. |
| **row predicate** | Declared, **never applied**. This engine refuses a statement touching a table whose predicate it cannot enforce; it does not inject a `WHERE` clause and there is no enforcement mode that does (ADR 0012 §5). Row-level security belongs on the database role. |
