# Plan: terminology refactor

> **Status: EXECUTED (2026-07-16).** All phases applied via fanned-out agents +
> an independent checker pass. Offline suite green (466 passed / 11 skipped).
> `db → corpus_pin` was the final field name (not `default_schema`; it would have
> collided with the existing `schema` field). Intentional residue left for a
> later optional pass: internal plumbing identifiers (`a2_root` kwarg,
> `corpus_a2/_a3` scratch-dir strings). The serve-agent docs were renamed
> `server.md` / `server-llm-call.md` → `analyst.md` / `analyst-llm-call.md`
> (+ `.zh`), with all inbound links updated.
> `docs/plans/eval-ladder-results.md` keeps its filename (a historical
> 3-arm run) with a superseded-terminology note.

Working doc (English only). Consolidates the messy, drifted vocabulary into one
set of names. This doc was the agreed target; the canonical
[glossary.md](../glossary.md) (+ `.zh`) now matches it.

Companion to [design-decisions.md](../design-decisions.md) (D4 / D14 / D16 +
Audit dispositions R2/R-gold), which this reconciles.

## Why

Four term families each carry two or three parallel vocabularies for the same
thing, and two are outright stale:

| Family | Vocabularies in the wild | Stale? |
|---|---|---|
| Eval arms | `A1/A2/A3` · `no_layer/curator/gold` (code) · `No-layer arm / Facts-only / Recoverable ceiling` (glossary) | `Arm.gold` names a **retired** oracle; enum has no value for the SME arm |
| Serve harness | `Server` (agent) · `serve` (graph id / `[serve]` config) · `LangGraph Server` (infra) · `server/` (module) | "Server" collides four ways |
| Reliability | two-axis `safety_clearance`+`semantic_assurance{certified,heuristic,unverified}` · legacy tier `{governed,lineage,fenced_raw,refused}` · pending `grounded` | legacy tier is a redundant projection |
| Namespace | corpus `schema` (D15) · `DataSourceConfig.db` · BIRD `db_id` | `.db` lingers post-D15 |

## Locked decisions

1. **Serve agent `Server` → `Analyst`.** `Curator` (build) + `Analyst` (serve)
   are the harness pair. Module `server/ → analyst/`. "server" / "LangGraph
   Server" now mean **infra only**. `serve` stays as the phase/verb (graph id
   `serve`, `[serve]` config table).
2. **Retire the legacy single-axis reliability tier** (`governed / lineage /
   fenced_raw / refused`). It is a strict 1:1 projection of the two axes. Removed
   as a vocabulary from prose + code; if the UI wants a one-word badge it is
   computed **display-only** in the presenter, never a parallel concept.
3. **Eval arms: semantic names only.** `A1/A2/A3` and `gold` are deleted.
4. **`certified` (reliability stamp) → `grounded`** (Audit R2). **Scoped rename —
   see the watch-out below.**
5. **Baseline = deterministic-max, DB-derivable only.** The eval floor is
   everything a script can pull from the database with **no curator LLM**
   (names, types, **sample values**, FK candidates), served through the **same
   Analyst path** as every other arm. Anything learned from the train
   `(question, SQL)` pairs — seed joins, few-shots — belongs to `curated`, not
   `baseline`. This collapses the old thin raw-dump `no_layer` and the
   `facts_only` row into one `baseline`.

## The eval ladder (target)

Four fair rungs plus a walled-off ceiling. Each differs from the one above it by
**exactly one** input, and — critically — all run through the **same serve path**
(the old `no_layer` raw-dump confounded "no content" with "no governed path";
this fixes that).

| Rung | = old label | What it adds | Built by | Status |
|---|---|---|---|---|
| `baseline` | A1 + facts_only (merged) | names, types, sample values, FK candidates | deterministic script, **no LLM** | corpus buildable today; **not yet scored as this arm** |
| `curated` | A2 / autonomous curator | Inference tier (descriptions, reliability caveats, terms, metrics) **+ train-SQL-derived** seed joins & few-shots | LLM curator agent | ✅ built & run |
| `curated_sme` | A3 / +SME | Simulated-SME clarification round(s) | curator + Simulated SME | ✅ built & run (one round, stuffed brief today; retrieval-based multi-round is designed, not built) |
| `ceiling` | replaces retired `gold` | test-aware oracle (sees test questions+evidence, never test gold SQL); dashed upper-bound line | Simulated SME, split-scoped index | ❌ designed, **not built** |

The reading: `baseline → curated` isolates **what the AI-authored semantic layer
adds over raw structured metadata**; `curated → curated_sme` isolates **the lift
from SME answers**; `ceiling` bounds **recoverable knowledge** (`1.0 − ceiling` =
irreducible SQL-gen error; `ceiling − curated_sme` = test-relevant knowledge a
train-bounded SME cannot reach).

## Rename map

| Retired | Replacement | Main touch-points |
|---|---|---|
| `Server` (agent), `server/` module | `Analyst`, `analyst/` | module dir, imports, ~320 doc mentions, glossary |
| `no_layer` (raw dump), `Arm.curator`, `Arm.gold` | `Arm.baseline / curated / curated_sme / ceiling` | [eval/arms.py](../../src/governed_bi/eval/arms.py), [run_experiment.py](../../src/governed_bi/eval/run_experiment.py), experiment + design docs |
| `no_layer_solver`, `baseline_solver.py` | `build_baseline_corpus()` (script, `model=None`, samples on) → `agent_solver` | eval module |
| `eval/gold.py`, `build_gold_corpus` | deleted (R-gold) | eval module |
| `SemanticAssurance.certified` | `SemanticAssurance.grounded` | [answer.py](../../src/governed_bi/analyst/answer.py), cache gate [governance.py:554](../../src/governed_bi/analyst/governance.py), presenter, tests |
| tier `governed/lineage/fenced_raw/refused` | display-only projection (or gone) | answer.py, presenter, ~55 doc hits |
| `DataSourceConfig.db` | `default_schema` (or `corpus_pin`) | [config.py:90](../../src/governed_bi/config.py), datasource docs |
| `flow` / `flow_solver` residue | removed | arms.py docstring, stray docs |

## Watch-outs (scoped renames — do NOT blanket-replace)

- **`certified` is overloaded three ways. Only one is renamed.**
  - `SemanticAssurance.certified` (reliability stamp) → **`grounded`**. ✅ rename.
  - `ProvenanceStatus.certified` ([schemas.py:55](../../src/governed_bi/corpus/schemas.py)) — a **human sign-off** (D6), used heavily in `deep_agent.py` (`certified_writes`). **Keep as-is.**
  - Metric lifecycle `draft → certified` (glossary "Metric"). **Keep as-is.**
  A naive find/replace of "certified" corrupts the latter two. Rename by symbol,
  not by string.
- **Cache admission** gates on `semantic_assurance == certified`
  ([governance.py:554](../../src/governed_bi/analyst/governance.py)) — update the
  gate + its tests with the enum rename.
- **`baseline` splitting out is a behavioral change, not cosmetic** — it adds a
  newly-scored arm (deterministic corpus through the Analyst path) and drops the
  raw-dump solver. Belongs in the eval-work phase, not the mechanical-rename phase.
- **`db → schema` collapse was deferred once** (D15). Expect `DataSourceConfig`
  consumers (BIRD `db_id`, default write subtree) to ripple.

## Phased rollout (on green-light)

1. **Mechanical, low-risk:** arm enum + docs, `SemanticAssurance.certified →
   grounded` (scoped), `DataSourceConfig.db → default_schema`, `flow` residue.
   Offline suite green after each.
2. **Retire the legacy tier** (code + prose; keep display projection only).
3. **Eval ladder behavior:** add `build_baseline_corpus()`, rewire `run_experiment`
   so `baseline` runs `agent_solver` over the deterministic corpus; delete
   `no_layer_solver` + `gold.py`.
4. **The big rename:** `server/ → analyst/` module + full doc sweep.
5. **Reconcile the glossary** (EN + zh) as the enforced source of truth; optional
   CI guard flagging retired terms (`A1`, `gold`, `certified`-as-stamp,
   `fenced_raw`, `Server`-as-agent).

Every phase keeps offline tests green and updates EN + zh docs together
(`qu-ai-wei` on the Chinese).

## Docs to reconcile (currently disagree on arms)

[glossary.md](../glossary.md), [design-decisions.md](../design-decisions.md) (D4,
D14 + amendment), [eval-ladder-results.md](eval-ladder-results.md),
[architecture.md](../architecture.md) §8, and the eval module docstrings all
describe the arms differently. After the rename they must all use the ladder above.

---

## Proposed glossary (FOR REVIEW — not yet applied)

Full revised term list. **Changed / new** rows are marked; unmarked rows are
unchanged from today's [glossary.md](../glossary.md). Review this before we begin.

| Term | Definition | Change |
|---|---|---|
| **Domain** | A business area the agent serves (e.g. Sales, Support, Inventory). | — |
| **Governed dataset** | The canonical single-source-of-truth *logical* model for a domain's questions. | — |
| **Metric** | A compiled measure/dimension over a governed dataset. The unit that is certified (SemVer, draft→certified). | — (metric-lifecycle "certified" stays) |
| **Semantic layer** | The compiled definitions: governed datasets + metrics + term/rule resolution. Human-owned. | — |
| **Skill / reference doc** | Markdown procedural + descriptive knowledge per domain. | — |
| **Corpus** | Umbrella for the shared human-owned substrate: semantic layer + skills + metadata/lineage + durable memory. | — |
| **Gateway** | The read-only, policy-enforcing data-access boundary. The only path to data. | — |
| **Curator** (build agent) | Offline exploratory agent that *produces* the corpus. Writes human-gated in prod. | — |
| **Analyst** (serve agent) | Online governed agent that *consumes* the corpus to answer. Fail-closed, auditable. Formerly "Server"; "server" / "LangGraph Server" now mean infra only. | **RENAMED** from Server |
| **Tool** | A coded function the model may decide to call. | — |
| **Hook** (middleware) | Deterministic code firing on loop events to inject context and/or veto actions. | — |
| **Memory** | Four designed stores (Architecture §7): Working (built) + Profile / Episodic / Correction (off-by-default seams). | — |
| **Working memory** | Verbatim per-session context (checkpointer). Ephemeral; identity-scoped. | — |
| **Governed path** | Answering from the semantic layer (the default). | — |
| **Discovery path** | Fenced raw exploration for questions the semantic layer does not cover. | — |
| **Promotion loop** | Distilling a discovered pattern into a certified governed dataset/metric after human review. | — |
| **Semantic plane / data plane** | Offline meaning (PR/CI) vs online execution (guardrail-gated). | — |
| **Negative example** | A curated pattern marking a question class unanswerable-from-this-data; fires the canned escalation. | — |
| **Reliability stamp** | The two-axis marking on a delivered answer (D5): `safety_clearance` (bool hard gate) and `semantic_assurance` (`grounded` / `heuristic` / `unverified` — how well-grounded). `grounded` means safe + in-scope, **not** verified-correct; thresholds uncalibrated (Audit R2). | **REVISED**: `certified`→`grounded`; legacy tier sentence removed |
| **Reliability caveat** | An AI-inferred free-text warning on a *column* (`UNRELIABLE. DO NOT USE` + reason). Corpus-side, curator-authored. Distinct from the answer-side Reliability stamp. | — |
| **Governance exclusion** | A human-set `governance.excluded` boolean meaning "never surface". Human-authored (D6); distinct from the AI-inferred Reliability caveat. | — |
| **Interaction signal** | A recorded observation of a user action on a served answer, captured for evaluation + development. Raw (capture-first). v0 rides Langfuse/LangSmith; a dedicated interaction log is future work. | — |
| **Correction signal** | The high-trust subtype of Interaction signal: a user-initiated observation that an answer was wrong. A *hypothesis* — validated + PR-gated, never an auto-edit. | — |
| **Clarification question** | A curator-emitted, ID-tracked open question about a corpus asset, awaiting a Responder's answer. | — |
| **Responder** | The pluggable role that answers Clarification questions in *free text*. Two impls: human SME (product), Simulated SME (eval). | — |
| **SME** (subject-matter expert) | The human Responder in production. Answers in free text; never edits the corpus directly. | — |
| **Clarification answer** | A Responder's free-text reply; a parse step translates it into a structured corpus edit before git. | — |
| **Simulated SME** | An eval-harness Responder: an LLM briefed with a dataset's *domain meaning*, answering Clarification questions one at a time, never handed a held-out test question's gold SQL. Pull-based (answers only what the curator asks). Powers the `curated_sme` arm and the `ceiling`. | **REVISED**: "A3" → `curated_sme` |
| **Execution accuracy (EX)** | The agent's result matches gold, verified by re-executing gold SQL. | — |
| **Governed-path adherence** | Share of questions resolved via the semantic layer rather than raw tables. | — |
| **Decoy-touch rate** | Share of questions where the agent used a manifest-flagged fake column/table. | — |
| **Baseline** (eval floor) | The deterministic, script-built corpus — table/column names, types, **sample values**, FK candidates — with **no curator LLM** and **no train-SQL-derived** assets. Served through the same **Analyst** path as every arm. Isolates "what a script knows about the database." Replaces the old raw-dump no-layer arm **and** the facts-only row. | **NEW** (replaces "No-layer arm" + "Facts-only corpus") |
| **Curated arm** | `baseline` + the curator's LLM-authored **Inference tier** (descriptions, reliability caveats, terms, metrics) **and** train-SQL-derived assets (seed joins, few-shots). `baseline → curated` isolates what the semantic layer adds. | **NEW** |
| **Curated+SME arm** (`curated_sme`) | `curated` + one or more Simulated-SME clarification rounds. The growth axis. | **NEW** |
| **Recoverable ceiling** (`ceiling`) | The dashed upper-bound line: a test-aware Simulated SME holding the held-out test questions + evidence (never test gold SQL) in its retrieval index. Deliberately-leaky oracle, walled off from the fair arms. Replaces the retired de-obfuscation "gold" arm. Designed, not yet built. | **REVISED**: names the `ceiling` arm; "A3" refs dropped |
| **Schema** (namespace) | The single-level namespace inside the one database a run connects to (D15): one YAML subtree (`corpus/<schema>/`) + the per-asset `schema` field. The run's database is connection config (`default_schema`), not a corpus level. | **REVISED**: notes `db → default_schema` |
| **Cross-schema relationship** | A `join` asset whose endpoints live in different schemas. **Curated only**; else the engine refuses (D15). | — |
| **Schema router** | The retrieval pre-stage (D15) that shortlists relevant schemas before table retrieval. Join-aware. | — |
| **Qualified identifier** | A fully-qualified `schema.table[.column]` reference. Used end-to-end in multi-schema mode; single-schema stays bare (D15). | — |
| **Multi-schema mode** | The run mode where the connector spans every schema and cross-schema joins are executable (Postgres/Redshift, v0). | — |

### Retired vocabulary (to purge)

`A1` / `A2` / `A3`; `gold` arm / `build_gold_corpus`; `no_layer` (as an arm);
`facts_only` (as a standalone arm — folded into `baseline`); `certified` *as a
reliability-stamp value* (→ `grounded`); the tier `governed` / `lineage` /
`fenced_raw` / `refused` (→ display-only); `Server` *as the serve agent* (→
`Analyst`); `flow` / `flow_solver`; `DataSourceConfig.db` (→ `default_schema`).
