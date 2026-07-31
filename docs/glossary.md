# Agentic BI Glossary

_[English](glossary.md) · [简体中文](glossary.zh.md)_

Canonical terms for the [Agentic BI System](architecture.md). When a term
below conflicts with how something is being described, the term below wins.

> **Retired vocabulary**
>
> UDH.ai terms are not used: `category` → **governed dataset**; `fabric object`
> → **governed dataset** (optionally materialized); `app_ci` → the gateway's
> execution target.
>
> Also retired by the 2026-07-16 terminology refactor:
> `A1` / `A2` / `A3` — these mapped onto the old three-rung ladder
> (`baseline` / `curated` / `curated_sme`), which has since been split so that each
> adjacent step changes one thing, so there is no longer a 1:1 successor for the old
> labels: `seeded` sits between the first two. Use the arm names, not the letters; the `gold` arm /
> `build_gold_corpus` (→ `ceiling`, designed not built); `no_layer` and
> `facts_only` as standalone arms (folded into `baseline`); `certified` *as a
> reliability-stamp value* (→ `grounded` — `ProvenanceStatus.certified` and the
> metric `draft→certified` lifecycle are unaffected); the legacy single-axis
> tier `governed` / `lineage` / `fenced_raw` / `refused` (kept, if surfaced at
> all, only as a display-only projection of the two-axis stamp); `Server` *as
> the serve agent* (→ **Analyst**; "server" / "LangGraph Server" still mean
> infra only); `flow` / `flow_solver`. **`DataSourceConfig.db` is not retired** — it was, and it is live again with a different meaning (`config.py`: the lake identity behind `db:` note-scope sentinels, ADR 0003). The SQL schema pin is `corpus_pin` / `schema`.
>
> Also retired by [ADR 0003](adr/0003-governed-notes-tri-modal-retrieval.md) /
> **D17** (2026-07-22): `skill` (→ **Note**; `SkillFrontmatter` / `SkillKind`
> deleted, `RuleAsset` generalized into `NoteAsset`).
>
> Also retired 2026-07-17 ([D15](design-decisions.md#d15-multi-schema-serving-one-database-many-schemas)):
> `multi_schema` mode / single-schema mode as a toggle — the engine is now
> uniformly schema-qualified; only the number of schemas present differs.

## Homonym traps (not definitions)

These are searchable false friends. Each row says what the term is **not**. Full senses live in the ops/eval table below (or under **Reliability stamp** for the two stamp axes).

| Trap | What it is **not** |
|---|---|
| **graded_delivery** | Not “graded against gold.” It is **degraded** delivery: emit SQL/result with `semantic_assurance=unverified` instead of hard-refusing a semantic failure (`analyst/answer.py:236`). Every `grade` / `grader` / `gradeable` / `hash_grade` in `src/` means score-against-gold. |
| **safety_clearance=False** | Not “the SQL was unsafe.” Means the full guardrail+authorization delivery path did not clear; graded/semantic failures also stamp `False` while still returning SQL (`analyst/answer.py:236`). |
| **semantic_assurance=unflagged** | Not verified-correct and not “well grounded in retrieval.” Only “no uncertainty flag fired.” Already stated on **Reliability stamp**; this row exists so the bare enum value is searchable (`analyst/answer.py:47`). |
| **ledger** | Not one thing. Four unrelated senses (governance tool log, clarification origin, hygiene alias, runs registry) — see the ops/eval table. |
| **stamp** | Not only the answer **Reliability stamp**. Also ledger timing fields, eval stratum labelling, and manifest digests — see ops/eval. |
| **scope** | Not only L4 tables. Also note attachment, viz graph window, API graph routes, and which questions a run scored — see ops/eval. |
| **tier** | Not only the display `ReliabilityTier`. Also corpus Facts/Inference/Audit layers and clarification payload tags — see ops/eval. |

| Term | Definition |
|---|---|
| **Domain** | A business area the agent serves (e.g. Sales, Support, Inventory). |
| **Governed dataset** | The canonical, single-source-of-truth *logical* model for a domain's questions. Grain, entities, columns, joins, and hygiene filters are defined once. A materialized view is an optional physical optimization, not the definition. |
| **Metric** | A compiled measure/dimension over a governed dataset that yields the same number everywhere. The unit that is certified (SemVer, draft→certified). |
| **Semantic layer** | The compiled definitions: governed datasets + metrics + term/business-rule resolution. Human-owned; the source of truth. |
| **Note** (`NoteAsset`) | Governed annotation — routing rules, gotchas, query patterns, business rules, context — attachable to any asset or namespace (`schema:` / `db:` scope sentinels, or an asset id). Carries the full three-tier + `Governance` structure and provenance-aware, tri-modal retrieval (semantic / trigger-PIN / agent-fetch). Formerly the ungoverned Markdown **Skill / reference doc** (ADR 0003, D17). |
| **Corpus** | Umbrella for the shared human-owned substrate: semantic layer + notes + metadata/lineage + durable memory content. |
| **Gateway** | The read-only, policy-enforcing data-access boundary: credential isolation, row-cap + statement timeout, forced-LIMIT injection on unbounded SELECT/UNION (all dialects), audit/replay. The only path to data. **RLS-as-user is a deferred seam, not built:** `identity` reaches `Gateway.execute` and is used only for the audit row. |
| **Curator** (build agent) | Offline exploratory agent that *produces* the corpus (bootstrap + drift-repair). Writes are human-gated in prod. |
| **Analyst** (serve agent) | Online governed agent that *consumes* the corpus to answer. Fail-closed, auditable. Formerly "Server"; "server" / "LangGraph Server" now mean infra only. |
| **Tool** | A coded function the model may decide to call. |
| **Hook** (middleware) | Deterministic code firing on loop events to inject context and/or veto actions. |
| **Memory** | Four designed stores (Architecture §7). Only **Working** exists (built, session-scoped). **Profile**, **Episodic** and **Correction** are design, not code — their empty protocols and config were deleted 2026-07-28 (D8). |
| **Working memory** | Verbatim per-session context (checkpointer). Ephemeral; identity-scoped. |
| **Governed path** | Answering from the semantic layer (the default). |
| **Discovery path** | Fenced raw exploration for questions the semantic layer does not cover. |
| **Promotion loop** | Distilling a discovered pattern into a certified governed dataset/metric after human review. |
| **Semantic plane / data plane** | Offline meaning (published via PR/CI) vs online execution (guardrail-gated). |
| **Negative example** | A curated pattern marking a question class as unanswerable-from-this-data; fires the canned escalation. |
| **Reliability stamp** | The two-axis marking on a delivered answer (D5): `safety_clearance` (bool hard gate) and `semantic_assurance` (`unflagged` / `heuristic` / `unverified` — whether uncertainty flags fired). `unflagged` means no flag fired, **not** verified-correct and **not** "well grounded in retrieval"; thresholds uncalibrated (Audit R2 / C2). |
| **Reliability caveat** | An AI-inferred free-text warning on a *column* that it may be unreliable (`UNRELIABLE. DO NOT USE` plus a reason). Corpus-side and curator-authored, distinct from the answer-side **Reliability stamp**. It replaces a typed decoy flag so the mechanism transfers to an enterprise deployment. |
| **Governance exclusion** | A human-set `governance.excluded` boolean on a column/table meaning "never surface": the asset is removed from everything the **Analyst** sees, all environments, permanently. Human-authored (D6); distinct from the curator's AI-inferred **Reliability caveat**. |
| **Interaction signal** | A recorded observation of a user action on a served answer — a **Correction signal**, a rephrased re-ask, a regenerate, an abandonment, or an explicit rating — captured for *evaluation* (production quality, run against metrics) and *development* (passive semantic-layer improvement). Captured **raw** (capture-first); trust-tiering/interpretation is deferred until real usage shows what correlates with a wrong answer. v0 rides Langfuse/LangSmith trace feedback; a dedicated, queryable interaction log (keyed by turn + corpus-release hash) is future work. |
| **Correction signal** | The high-trust subtype of **Interaction signal**: a *user-initiated* observation that an answer was wrong in a specific, nameable way (e.g. "revenue should exclude refunds"). Distinct from a **Clarification question** (curator-initiated, addressed *to* a human) and from **Correction memory** (a store). A Correction signal is a *hypothesis*: it must be validated against the query and pass the human PR gate before it can change the corpus — never an auto-edit. |
| **Clarification question** | A curator-emitted, ID-tracked open question about a corpus asset (e.g. "what does renamed column `kunde_id` mean?"), awaiting a **Responder**'s answer. Distinct from a **Reliability caveat** (the curator's own judgment): a Clarification question is addressed *to a human* and expects an answer back. |
| **Responder** | The pluggable role that answers **Clarification questions** in *free text* plus optional resources, never structured edits. Two implementations, both outside engine core: a human **SME** (product) and a **Simulated SME** (eval). |
| **SME** (subject-matter expert) | The human **Responder** in production: a non-technical domain expert who answers **Clarification questions** in free text. Never edits the corpus or opens a PR directly. |
| **Clarification answer** | A **Responder**'s free-text reply (plus optional resources) to a **Clarification question**. A *parse step* (the **Curator**/LLM or a data engineer) translates it into a structured corpus edit before it enters git. Resources land as `source_refs`. |
| **Simulated SME** | An eval-harness **Responder**: an LLM briefed with a dataset's *domain meaning*, answering **Clarification questions** one at a time, never handed a held-out **test** question's gold SQL. Pull-based (answers only what the curator asks). Powers the `curated_sme` arm and the `ceiling`. |
| **Execution accuracy (EX)** | The agent's result matches gold, verified by re-executing gold SQL. |
| **Governed-path adherence** | Share of questions resolved via the semantic layer rather than raw tables. |
| **Decoy-touch rate** | Share of questions where the agent used a manifest-flagged fake column/table. |
| **Baseline** (eval floor) | The deterministic, script-built corpus — table/column names, types, **sample values**, FK candidates — with **no curator LLM** and **no train-SQL-derived** assets. Served through the same **Analyst** path as every arm. Isolates "what a script knows about the database." Replaces the old raw-dump no-layer arm **and** the facts-only row. |
| **Seeded arm** (`seeded`) | `baseline` + mechanical train-SQL joins and metrics, plus decoy / negative-space marking (columns train gold never touched). **No LLM and no few-shots** — few-shots are authored only on the curated agent path. Build cost is zero model calls. `baseline → seeded` is **not** a parsing-only causal estimate: it also drops baseline's naming-convention FK guesses and applies the train-conditioned column mask. See the experiment-runbook checklist. |
| **Curated arm** | `seeded` + the curator's LLM-authored **Inference tier** (descriptions, reliability caveats, terms, metrics, few-shots). `seeded → curated` isolates what the curator LLM adds over the free deterministic pass. It is measured against `seeded`, not `baseline`: the two interventions always co-occurred, so the older `baseline → curated` delta could not say which of them paid. |
| **Curated+SME arm** (`curated_sme`) | `curated` + the Simulated-SME clarification round, whose brief carries BIRD's human `database_description` CSVs. The growth axis. **`curated → curated_sme` bundles two mechanisms** — the protocol and that documentation — and cannot be split: the `curated_sme_blind` rung that tried was removed 2026-07-28 as meaningless, because it briefed the SME on train questions and evidence, which Phase A already has. `single_variable` is `false` on this step via its mechanism count, not via a skipped rung. |
| **Recoverable ceiling** (`ceiling`) | The dashed upper-bound line: a test-aware Simulated SME holding the held-out test questions + evidence (never test gold SQL) in its retrieval index. Deliberately-leaky oracle, walled off from the fair arms. Replaces the retired de-obfuscation "gold" arm. Designed, not yet built. |
| **Schema** (namespace) | The single-level namespace inside the one database a run connects to (D15): one YAML subtree (`corpus/<schema>/`) + the per-asset `schema` field. The run's database is connection config (`corpus_pin`), not a corpus level. |
| **Cross-schema relationship** | A `join` asset whose two endpoints live in *different* schemas. **Curated only** — declared by an **SME**, distilled from example SQL, or mined from usage; never probed from database foreign keys or guessed from names. With no such asset the engine **refuses** the cross-schema question rather than inventing a join (D15). |
| **Schema router** | The retrieval pre-stage (D15) that shortlists the schemas relevant to a question before table retrieval, so thousands of tables across many schemas stay tractable. **Join-aware**: it expands along curated cross-schema joins so a bridge table in an un-mentioned schema is not dropped. |
| **Qualified identifier** | A fully-qualified `schema.table` (or `schema.table.column`) reference. Used end-to-end, **always** — retrieval, the guardrail allow-set, generated SQL, and execution (D15, superseded 2026-07-17: uniformly schema-qualified). A *bare* reference resolves to the serving schema (`DataSourceConfig.serving_schema()`), or fails closed when the source spans all schemas with no default. |

## Ops and eval vocabulary

Descriptive of **what the code means now**. No renames. Product terms stay in the table above. Do **not** define **arm** as “equals **rung**” — `rung` is retired vocabulary for new writing; use **arm** for the fair-ladder unit.

| Term | Definition |
|---|---|
| **arm** | One fair-ladder treatment fed to the same serve path; enum `baseline` / `seeded` / `curated` / `curated_sme` (`eval/arms.py:67`). Named arms already have product rows above; this is the unit name those rows instantiate. |
| **rung** | A ladder position in driver/oracle prose (`skipped_rungs` at `eval/arms.py:174`, “oracle rungs”). On the fair ladder a position is an **arm**; do not reintroduce **rung** as the unit name in new writing. |
| **run** | Three senses that collide in paths and logs: (a) an **experiment** artifact directory under `runs/` (e.g. `runs/datalake/<ts>/`) — one campaign / one driver out-dir; (b) the opaque per-invoke **`run_id`** (sense below); (c) the English verb / LangGraph Server “run” API. **`runs/<something>/` is not keyed by `run_id`.** |
| **run_id** | Opaque id for one graph invoke / serve turn (`provenance.py:52` `new_run_id`). Eval mints a fresh one per question. Not the experiment directory name under `runs/`. |
| **turn_id** | Stable per-turn key `{thread_id}:{n_human}` (`provenance.py:43`); primary key of the durable `run_log`. Distinct from **`run_id`** (reminted each invoke; can diverge across clarify resume). |
| **ledger** | Four unrelated senses: (a) per-turn list of governed tool/guardrail records on agent state (`analyst/middleware.py:48`, `_ledger_stamp`); (b) curator clarification origin via `ledger_source` (`curator/pipeline.py:1288`); (c) `ledger_ok` alias of run-artifact hygiene, identical to `hygiene_ok` (`eval/index.py:590`); (d) the runs registry module that calls itself “a ledger of runs” (`eval/index.py:1`). |
| **stamp** | Four senses; only (a) is the product **Reliability stamp**: (a) two-axis answer mark (`analyst/answer.py:147`); (b) `_ledger_stamp` timing fields on ledger entries (`analyst/middleware.py:48`); (c) eval stratum labelling so a row is “stamped” into a pre-registered rate (`eval/index.py:204`); (d) writing digests onto a run manifest (`eval/metrics.py:450` `stamp_corpus_hashes`). |
| **scope** | Five senses: (a) note attachment ids on `NoteAsset.scope` (`corpus/schemas.py:404`); (b) L4 licensed/allowed table set for the turn; (c) viz graph window (`viz/scope.py:41`); (d) which API graph-scoping routes are enabled (`can_scope`); (e) which questions a run scored (`question_scope_hash`). |
| **tier** | Three senses: (a) display-only `ReliabilityTier` projection of the two-axis stamp (`analyst/answer.py:61`); (b) corpus field layers Facts / Inference / Audit; (c) clarification payload tag `"tier": "audit"`. |
| **verdict** | Three senses: (a) `GuardrailVerdict` from `check()`; (b) per-ledger-entry status string (`pass`/`block`/`cap`/…); (c) SQL-diff dimension outcome `match`/`mismatch`/`unknown`. |
| **block** | Guardrail- or middleware-level rejection of one tool/SQL attempt (may still repair mid-loop). Distinct from turn-level **refuse**. |
| **kind** | Generic type tag in many domains (note kind, connector kind, stream event kind, asset type, graph node kinds). Not one concept — always read the nearby type. |
| **db_id** | BIRD / eval schema identity for one lake member (corpus subtree name). Often equals `corpus_pin` for a single-schema pin; not the lake identity field `DataSourceConfig.db`. |
| **resume** | Continue an interrupted eval from an existing run dir (`--resume-from`); may re-serve crashed turns and then fail hygiene. |
| **budget (always-note)** | Cap on always-inject notes per turn: count and characters via `apply_always_budget` (`analyst/note_inject.py:191`). |
| **budget (tool-call / step)** | Curator per-schema ceiling in tool calls (`tool_call_budget` / `max_agent_steps`); recursion limit derived from it (`curator/pipeline.py:120`). |
| **budget (table)** | Oracle padded-tables target count (`table_budget`) in `oracle_tables_padded` (`eval/oracle.py:301`). |
| **budget (node)** | Viz graph node cap for scoped ER/KG views (`node_budget`, `viz/scope.py:41`). |
| **suspect** | Curator AI-inferred column reliability status / decoy allowlist set; drives decoy-touch metrics. Distinct from human **Governance exclusion** and from the answer-side **Reliability stamp**. Closest product term: **Reliability caveat**. |
| **outcome** | How a scored turn ended: `answered` / `refused` / `clarification` / `capped` / `crashed` (`stages.py:94`). Orthogonal to gradeability. |
| **pooled** | Multi-schema / multi-db lake mode: one serve corpus or one datalake driver pass over many schemas, not a single-db experiment. |
| **licensed** | Tables/assets seeded into this turn’s L4 allow-set during assemble (agent state channel `licensed`). |
| **driver** | Eval orchestrator that builds corpora, serves arms, writes artifacts (`run_datalake`); not the grader and not `eval/harness.py` helpers. |
| **refuse** | Turn-level decline: no delivered answer (`Outcome.refused` / refuse-gate / hard stop at `stages.py:107`). Stronger than a mid-loop **block**. |
| **quotable** | Run-artifact hygiene gate: crashes, build errors, resume re-serves, twin-stamp coverage, arithmetic floor, etc. cleared (`eval/index.py:590`). Not “publishable” and not `claim_ready`. Aliases: `ledger_ok` / `hygiene_ok`. |
| **solver** | `question → SQL \| None` callable for one arm (`Solver` / `agent_solver` in `eval/arms.py:211` / `:417`); what the driver invokes per question. |
| **twin** | Test gold SQL that has a structural twin in train; strata `ex_no_twin` / `ex_twin` (`eval/leakage.py`). |
| **comparable** | Whether two indexed runs may be contrasted: same comparability knobs / manifest schema / corpus hash identity (`eval/index.py`). |
| **headline** | The single pre-registered rate a run commits to quote (`HEADLINE_RATE` at `eval/metrics.py:607`, typically `ex_no_twin`); also the per-arm summary block carrying that rate. |
| **crashed** | `Outcome.crashed` (`stages.py:110`): solver/infra exception, not a deliberate refusal. Inflates refusal metrics if folded into `refused`. |
| **replicate** | Noise-floor control: re-serve an arm as `<arm>__replicate` (`--replicate` / `replicate_of`); required for the claim checklist, never auto-enables `claim_ready`. |
| **fold** | Not cross-validation. Senses: (a) SME clarification fold into corpus (`fold_mode` / `sme_fold`); (b) identifier/token normalization in guardrails/governance; (c) retired arms merged into another (e.g. `facts_only` into `baseline`). |
| **shortlist** | Schema-router candidate schemas before pick (`shortlisted_schemas`); also an assemble sub-stage name. |
| **graded_delivery** | Degraded delivery path (see trap above). Flag on provenance / generation rows when that path ran (`analyst/answer.py:236`). |
| **routing_escaped** | Delivered SQL used a schema outside the router’s `routed_schemas`. Judged from `tables_used`, not from `licensed_tables`. `None` = unobserved. |
| **promote** | Code sense: move a finished per-db build from `_staging` into the shared arm root (`_promote_build` at `eval/run_datalake.py:524`). Distinct from product **Promotion loop** (distill a discovery into a certified governed dataset/metric). |
| **licensed_tables** | Assemble-time seed license recorded on the generation row; not amended by later tool use. Do not use it to detect routing escape. |
| **claim_ready** | Always left `False` by the index (`eval/index.py:608`); lists `claim_ready_requires` (replicate/MDE/Holm/cluster/single-variable/twin) for the runbook checklist. Hygiene-only `quotable` is necessary but never sufficient. |
| **hygiene_ok** | Alias of `quotable` / `ledger_ok`: artifact hygiene only (`eval/index.py:590`). |
| **stage** | Two senses: (a) serve-pipeline position enum (`stages.py`); (b) temporary build directory (`_staging` / `_stage_roots` in `eval/run_datalake.py`). |
| **step** | Four senses: (a) stream/event step name beside `kind`; (b) curator/agent tool-call step under `tool_call_budget`; (c) ladder adjacent step / `STEP_MECHANISMS` (`eval/arms.py:138`); (d) LangGraph recursion / super-step counting. |
| **index** | Three senses: (a) retrieval index over corpus assets; (b) runs registry `eval/index.py`; (c) ordinal position (prefer `*_position` in new writing). |
| **layer** | Two senses: (a) guardrail layers L1–L5 (`GuardrailLayer` / `failed_layer`); (b) semantic layer / corpus meaning plane (product **Semantic layer**). |
| **pin** | Three senses: (a) `corpus_pin` — which schema subtree/DB the run serves; (b) `pin_triggers` / PIN — keyword hard-include of notes into prompt (ADR 0003); (c) `pinned_schemas` — schemas forced into the router shortlist. |
| **harness** | Loose umbrella: serve runtime, curator build runtime, eval scoring path, shared eval helpers (`eval/harness.py`), and test/fake-model fixtures — always disambiguate. |
