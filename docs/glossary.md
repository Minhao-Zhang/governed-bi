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

The words for the current tree. When a term here disagrees with a casual use of the same word, this page wins. The decisions are in the [ADRs](adr/).

| Term | Meaning |
|---|---|
| **Corpus** | The semantic layer: YAML assets loaded by `corpus/`. Eight types, in `registry/assets.py`: schema, table, column, join, metric, term, few-shot, and negative example. |
| **Retrieval channel** | One of schema, term, metric, entity, or example. Entity covers tables, columns, and joins. Example covers few-shot questions. Each channel can score a lexical match, a semantic match, or both. Example is semantic only. In code the scoring kinds are `lexical` and `semantic`, and the five searches are stages named `channel_schema` and so on. |
| **Retrieved tables** | The tables retrieval found on this turn, including tables added while joining. The TABLES check asks only whether a statement's tables are in that set. It is not a permission. The record field is `retrieved_tables`. |
| **Check** | One of six deterministic passes over SQL before it runs: `PARSE`, `NO_WRITE`, `FUNCTIONS`, `BINDING`, `COLUMNS`, `TABLES`. See [ADR 0006](adr/0006-execution-time-governance.md). |
| **Assumption** | One choice the answer depends on. `basis` is `layer` (a definition in the corpus, with an `asset_id`), `convention`, `guess`, or `found_in_query` (the server found it in the SQL and added it). See [ADR 0018](adr/0018-every-answer-states-its-assumptions.md). |
| **Turn record** | What `finish` writes. It includes `outcome`, the SQL, the assumptions, and the config hash. It has no field that scores how much to trust the answer. |
| **Outcome** | How the turn ended: `answered`, `refused`, `clarification`, `capped`, `crashed`, or `no_sql`. |
| **Conversation store** | `runs/conversations.sqlite`, the checkpointer `langgraph.json` mounts for the server. The one-question command and the evaluation use `runs/harness-checkpoints.sqlite` instead, so a benchmark does not become the conversation list. Neither file may be the warehouse. See [ADR 0014](adr/0014-one-conversation-store.md). |
| **Abstention policy** | `serve/abstention.py`. Named rules that can decline a turn before the agent runs a query. The setting `abstention_policy_enabled` defaults to off. The data-lake driver turns it on with `--abstain`. See [ADR 0013](adr/0013-the-declared-abstention-policy.md). |
| **Setting** | A declared default in `registry/settings.py`. A `GOVERNED_BI_*` environment variable overrides one for a run. Secrets are not settings. |
| **Chat model and utility model** | The chat model writes the SQL. The utility model rewrites retrieval queries and runs the scope check. Both default to `gpt-6-luna`. See [ADR 0011](adr/0011-two-model-split-and-facet-query-rewriting.md). |
| **Stage event** | A stream event the page turns into the one status line. See [ADR 0010](adr/0010-live-stage-events.md). |
| **Corpus release** | The `corpus_release` setting: a tag for the corpus a measurement used, together with the content hash of the tree. |

## Easy to misread

| Word | What it means |
|---|---|
| `outcome=answered` | A query passed the checks and ran. It does not mean the answer matches what the person asked, and it does not mean the rows are the right ones. |
| `outcome=no_sql` | The turn ended without running a checked statement. The record does not say which of several causes that was. |
| `outcome=refused` | The engine declined. On the page this is "I can't answer this from your data." |
| `r_table_not_retrieved` | Retrieval did not find that table on this turn. It does not mean the caller lacks permission. There is no per-user permission in this app. A deployment that needs one uses database roles. |
| `r_column_excluded` | The corpus hides that column. |
| `r_column_suspect` | The corpus marks that column unreliable. |
| `r_column_not_allowed` | The corpus declares no such column. |
