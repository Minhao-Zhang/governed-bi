# What we built vs. the book we started from

> **STATUS 2026-07-31 — KEEP. This is the evidence base for A0–A7, and it has known errors.**
>
> Decision 15 opened the reference book's seven structural choices as **seven separate projects,
> each with its own baseline**. [rebuild-checklist.md](rebuild-checklist.md) X.5.10 gives
> each one or two lines; the book-side specification and our own mechanics are written **only
> here**. It is also the only place holding the warning that the book's 95%+ / +12% / +24% / 35%
> figures are its own extrapolations and **must not be used as targets**.
>
> **Seven confirmed errors — fix these before citing any affected section.** Four are in
> checklist X.5.8; three more were found later:
>
> | | Where | What is wrong |
> |---|---|---|
> | a | §3.4 table | Lists the Corrective-RAG thresholds in the Book column without flagging that §4.4 **admits it never implemented them** ("no retrieval confidence score") |
> | b | §3.2 | "G runs serially over the **union** of collected asset_ids" — the book's code is `collect_asset_ids(vector_results)`, **V's results only** |
> | c | §3.2 | "Each engine owns a slot; nothing competes across slots" — **the book contains no such statement.** V owns the whole container, D is parasitic inside V's `few_shots`, R's output is an underscore-prefixed view. This sentence was the basis of a decision that had to be reversed (decision 15) |
> | d | §3.1 | Adopts §9.2's "8 embedding tables" without noting §4.1 says **7** |
> | e | §2.5 | Says the word `certified` was retired "→ grounded" — the actual enum value is `unflagged`, and `analyst/answer.py:48` says verbatim `NOT "well grounded in…"`. §7 of this same file uses `unflagged`, so the file contradicts itself |
> | f | §3.4 / §3.6 | Treats `top_k=8` as an **output** cap. It is a seed cap — grounding runs after budgeting, returning 9–12 tables (19 pooled), and nothing anywhere bounds the final count. B-4's cost argument depends on this |
> | g | §3.4 | Cites the book's V threshold 0.65 / top_k 20 as evidence the book is stricter — **falsified by our own harness**: `works_cycles`/`top_k=8`, truncate to 20 → 0.538, to 8 → 0.554, no truncation → **0.600** |
>
> Add to O-2: `HashingEmbedder` is a **weaker lexical** channel than BM25, not a weak semantic one
> (`cosine('revenue','earnings')=0.0`), so it cannot serve A0's offline A/B.
>
> All 38 `file:line` pointers here are pinned to `2187ead`; after checklist X.5.5 splits
> `rvgd.py` into seven files they refer to a file that no longer exists.

A faithful comparison between this repo and the reference document it grew out of —
*《从数据到智能：企业级数据平台的构建、演进与 Agentic BI 实践》* (henceforth **the book**),
40 chapter notes in the Obsidian vault under
`Books/从数据到智能/`, dated 2026-06-25.

This is an assessment, not a plan to converge. Some of what follows is deliberate
divergence with a recorded reason; some is drift nobody decided; some is a gap the
book has too. The point of the exercise is to know which is which — and in
particular to stop treating "the book does X" as either an obligation or an
irrelevance without checking.

**Method.** Every chapter of the book was read; §2–§7 in full and directly, §8–§10
via extraction. Every claim about *this repo* below was verified against code at
`2187ead`, with file:line. Where the book states a number, the number is quoted
verbatim so the divergence is visible rather than paraphrased away.

**A caution about the book's numbers.** The book's benchmark table (10.2, 表 10.5)
is labelled 内部评测与行业合理推演的量级 — internal evaluation and reasonable
industry extrapolation — and its Steiner-tree accuracy table (6.2, 表 6.4) says
数据按比例缩放自真实评测. They are illustrative, not reproducible. Our own eval
discipline (`eval/index.py`'s quotable/comparable gates, the retirement of every
pre-2026-07-25 number) exists precisely because numbers of that kind are not
usable. **Do not adopt the book's figures as targets.** They appear below only to
show what the design was aiming at.

---

## 0. The one inversion that reframes everything else

The book's §2.4 is titled **确定性 DAG + 条件路由 vs ReAct 自治的取舍** — the
trade-off between a deterministic DAG with conditional routing, and ReAct autonomy.
It weighs them in a table and picks the DAG, explicitly:

> 选确定性 DAG，最根本的理由是"企业级可靠性"…… ReAct 灵活归灵活，但"每次路径不同"
> 这七个字对审计和排障来说就是噩梦。

It also narrates having tried the other way and backed out:

> 第三次加厚是把"路由"（④）从 LLM 自主决策改成了确定性条件路由。最初用 ReAct 模式让
> LLM 自己决定"下一步做什么"，结果路径不可追踪，审计根本没法做。

**We took the opposite side, on purpose, and deleted the DAG.** ADR 0002 (SHIPPED
to main 2026-07-14, `d2fdd6a`) makes the governed agentic core the *only* serve
path; the deterministic `flow.py` DAG and its stale graph were removed. The reason
recorded there is **governance = topology, not trust**: the guardrails sit at the
tool seam (`analyst/middleware.py:289–497`, `_GOVERNED_TOOLS` at :45), so every
data access the model makes is gated regardless of what order it decides to do
things in. Auditability is recovered not by fixing the path but by recording it —
`stage_events.jsonl`, the live ledger stream, `runs/index.jsonl`.

This is the single largest deliberate divergence in the codebase, and it cascades:
most of what the book puts in *nodes* we put in *tools*, and most of what the book
decides in *conditional edges* our model decides inside one ReAct loop.

| | Book (§2.3, §5.2) | This repo (`analyst/agent.py:391–1422`) |
|---|---|---|
| Shape | 9 nodes, 7 conditional-route functions | 5 rails nodes around 1 ReAct agent core |
| Steps | Supervisor → QU → Router → R/V/G/D → Planner → SQL gen → Guardrails → Exec → Viz | ingest → refuse_gate → assemble → agent_core → narrate |
| Who picks the next action | `_route_after_*` functions on `intent_type` | the model, via tool calls |
| Where safety lives | between nodes (護欄 node ⑧) | at the tool seam (`GovernanceMiddleware.wrap_tool_call`) |
| Intent classification | a dedicated LLM node (③ Query Understanding) | **does not exist** |
| Retries | `retry >= 2` → fail-safe | agent loop + `GovernanceHardStop`, recursion limit |

Two consequences worth stating plainly:

- **We have no intent taxonomy at all.** The book's seven routes (标准查询 / 缓存命中 /
  元数据查询 / 深度分析 / 图推理 / 知识问答 / 澄清请求) have no counterpart. There is no
  `intent_type` field anywhere in `src/`. A metadata question ("what tables are
  there") goes through the same path as a KPI lookup and is answered by the model
  calling `inspect_schema`. This is a real capability difference, not just a
  structural one: the book's 元数据查询 route answers *without* touching the SQL
  pipeline, and 知识问答 answers from documents. We do neither as a distinct path.
- **The refuse gate is the one piece of intent routing we kept**, and it is
  inverted: instead of classifying what the question *is*, it checks whether the
  question matches a curated `NegativeExampleAsset` and refuses immediately
  (`agent.py:579–594`). The book has no equivalent; its 澄清请求 route asks a
  follow-up instead of refusing.

---

## 1. Routing — the deepest divergence

The user asked to focus here, and it deserves it: **the book and this repo use the
word "routing" for two different things, and neither implements the other's.**

### 1.1 The book routes by intent. We route by schema.

The book's Router (§5.2) maps `intent_type` → execution path via a lookup table
`_INTENT_TO_ROUTE` (explicitly 非 ML). It never chooses *which data* to look at —
there is one semantic layer over one Redshift database, and the retrieval step sees
all of it.

Our router chooses *which schemas the question is about*, out of up to 69, and never
chooses a path. `retrieval/schema_router.py`:

- `schema_documents(corpus)` (:136) builds one language surface per schema by
  concatenating `asset_document` for every Analyst-visible table, plus metrics /
  few-shots / terms **grounded to a table in that schema** (:109–134).
- `shortlist_schemas(corpus, question, top_k=…)` (:180) ranks those documents —
  embedding cosine when an embedder is configured, BM25 otherwise, fail-open if
  neither (:229–255). It records which channel actually ran, so a silent
  degradation is attributable (:250–255).
- `expand_schemas_via_curated_joins` (:295) widens the shortlist along declared
  cross-schema joins.
- `pick_schema` (:534) optionally asks the LLM to choose one schema from the
  shortlist, with `SCHEMA_PICK_MAX_TABLES = 15` (:44) and a parsed reply
  (`_parse_schema_reply`, :450).

**This has no counterpart in the book whatsoever**, and it exists because of D15
(multi-schema serving: one database, many schemas, executable cross-schema joins),
which the book's single-database assumption never faced. It is also, per the
2026-07-19 finding, our measured bottleneck: schema-routing recall at BM25@3 was
0.35 — the routing layer the book doesn't have is the layer costing us the most.

**Assessment: deliberate, correct, and unavoidable.** Nothing to converge. But note
what it costs us in comparison terms: the book's accuracy figures assume the
right schema is a given. Any comparison of end-to-end numbers to the book is
therefore meaningless in our favour's direction and misleading in ours.

### 1.2 Term binding: the book's strong routing, half-present here

§4.3 is the book's self-declared **核心创新**: when Engine R hits a term carrying
`mapped_asset_id` (GMV → `metric_gmv`), that binding propagates as a *hard* signal
through five stages:

| Book stage | What the binding does there | Our state |
|---|---|---|
| ① Reranker | bound column → score **0.95**; its parent table **+0.25**; tables with no binding **−0.10** | **absent** — we have no reranker at all |
| ② Prompt Assembler | injects a `## 术语绑定约束` block naming which column to read from | **partial** — `render_terms` (`analyst/tools.py:126`) renders terms into context; there is no dedicated binding-constraint block and no "you must use this" framing |
| ③ SQL generation | forces the metric's definition (`SUM(amount) WHERE status='completed'`) | **absent as a constraint** — metrics are context, not enforcement |
| ④ Guardrails L4 | term-semantics layer blocks SQL that reads the term from the wrong table | **present but different** — our L4 (`guardrails.py:763`) checks *table scope*, not term-to-table fidelity (see §5.2) |
| ⑤ Corrective Retrieval | on a binding violation, pull the correct table first | **absent** |

What we *do* have is one use of term binding the book does not: `_term_binding_table`
(`schema_router.py:88–107`) resolves a term's binding through table / metric /
column to an owning table, so **a bound term contributes its language surface to the
schema its binding grounds to**. That is term binding used for *schema* routing —
our problem — rather than for column selection.

**Assessment: mostly unintentional drift, and the largest missed opportunity in the
comparison.** Nobody decided not to build binding propagation; the mechanism simply
never got past the corpus schema (`TermAsset.binding` exists,
`corpus/schemas.py`) into retrieval scoring or generation constraints. Given that
the book identifies this as the single strongest anti-hallucination lever it has,
and given that our own eval shows retrieval as the bottleneck, this is worth a
deliberate decision rather than continued silence.

### 1.3 The reranker: entirely absent

§4.2 specifies a four-stage reranker: ① term-binding boost (weights above), ② QU-signal
rerank with a bonus for `certified` assets, ③ dynamic column pruning (keep
metric/dimension/filter-relevant, PK/FK, time columns; drop the rest), ④ join-path
simplification by fanout risk.

We have none of the four stages. `retrieve` (`rvgd.py:429`) produces a BM25 ranking,
optionally fuses an embedding ranking with RRF (`fuse_rankings`, :483), applies
per-type budgets so tables aren't crowded out by few-shots, and uses curator
`confidence` as a mild tie-breaker. That is *ranking*, not *reranking* — there is no
second pass over the retrieved set at all.

Two of the four stages are things we'd have to invent a substitute signal for
(② needs QU output we don't produce), but ① and ③ map cleanly onto data we already
have: `TermAsset.binding`, and the column-role information in `TableAsset.columns`.

**Assessment: unintentional. ①③ are cheap and available; ②④ are blocked on design
we deliberately don't have.**

### 1.4 Token budget: different scheme, same problem

§4.5 allocates the context budget **by asset category** — 30% tables / 25% columns /
15% metrics / 10% few-shots / 10% join rules / 10% business rules — after explicitly
rejecting relevance-based truncation, because "有些资产分数不高但不可或缺（比如 join
规则）". The ratios came from ~200 real questions and the chapter is candid that they
are not theoretically optimal.

Our budgeting is per-type *count* caps and per-note *character* caps, not a
proportional split of one budget: `always_note_global_max = 8`,
`always_note_char_max = 2000`, `pin_max = 3`, `NOTE_TEXT_MAX_CHARS = 4000`
(`note_inject.py:297`), `schema_pick_max_columns = 12`, plus `render_few_shots(limit=3)`.
`context_chars` and `context_hash` are recorded per row, so the total is measured
even though it isn't apportioned.

The book's insight — *a low-scoring join rule is still load-bearing* — is one we
arrive at differently: `assemble_context` takes joins **by licensed scope rather
than by retrieval score** precisely because `asset_document` gives `JoinAsset` no
language surface (stated in `retrieval/__init__.py`). Same conclusion, different
mechanism, arrived at independently.

**Assessment: divergent by accident, but not obviously worse.** A category-proportional
budget would be a real change with an unclear payoff; the honest gap is that we
cannot currently answer "what fraction of the prompt was tables vs notes" for a
given run, which the book's scheme answers by construction.

### 1.5 Corrective-RAG: we are in the same place the book admits it is

§4.4 designs a three-tier retrieval-quality assessment (high: term hit + score > 0.8;
medium: 0.65–0.8; low: < 0.65 → widen threshold / top_k, or supplemental retrieval).
Then its own callout retracts most of it:

> Agentic BI 的 corrective_retrieval 是 CRAG 的简化版：**不评估检索质量（无 retrieval
> confidence score）**，而是在护栏失败后才触发补充检索…… 改进方向是把检索评估前置。

We are in exactly that position, and one step further back: our repair loop is the
agent re-calling tools after a guardrail block, with no supplemental-retrieval step
keyed to the failure type. `lexical_coverage` (`rvgd.py:100`) exists and is
explicitly documented as *not* a cross-question-comparable confidence score.

**Assessment: a gap in the book too.** Do not score this against us. If we build
pre-generation retrieval assessment we will be ahead of the reference, not caught up.

---

## 2. The semantic layer — closest correspondence, sharpest divergence in governance

### 2.1 Three-layer governance: we have L1 and L3, and L2 is thin

The book's §3.1 splits the semantic plane into **L1 元数据契约** (structure),
**L2 术语治理** (business term → technical asset), **L3 业务规则** (how to compute
correctly), with the relationship `L3 → constrains → L2 → binds → L1`.

Our correspondence:

| Book layer | Book example | Ours |
|---|---|---|
| L1 元数据契约 | `table: fact_orders, columns: [...]`, `grain`, `sensitivity: P2` | `TableAsset` with `columns`, `grain`, `governance` — **strong match**, plus a reliability caveat mechanism the book lacks |
| L2 术语治理 | `term: "GMV" → metric: metric_gmv` + `synonyms` | `TermAsset.binding` (asset_type + asset_id) + synonyms — **structurally present, behaviourally inert** (§1.2) |
| L3 业务规则 | `rule: GMV = SUM(amount) WHERE status='completed'`, standalone rule assets | `MetricAsset.expression` + `filter`, and `NoteAsset` for everything else — **restructured deliberately** |

The L3 restructuring is a decision worth stating clearly. The book keeps **规则资产**
(`business_rule`) and **上下文资产** (`business_context`) as separate asset types
consumed by "直接注入 Prompt + 护栏校验". ADR 0003 / D17 (2026-07-22) went the other
way: we deleted `skill` and generalised `RuleAsset` into **`NoteAsset`** — a single
annotation type attachable to any asset or namespace via scope prefixes
(`schema:` / `db:` / an asset id), carrying the full three-tier + `Governance`
structure and tri-modal retrieval (semantic own-vector / regex-trigger PIN /
agent-fetch via `read_notes` + `grep_notes`).

**Assessment: deliberate and, I think, better.** The book's rule/context split is a
taxonomy imposed at authoring time; ours is a scope expressed at attachment time.
Ours also gives notes their own retrieval path, which the book's "直接注入 Prompt"
does not — the book's rules are always-injected or not present, whereas ours can
fire on a trigger.

### 2.2 Asset types: 9 vs 7, and the two we dropped

| Book (§3.2, 9 types) | Ours |
|---|---|
| 表资产 Table | `TableAsset` ✅ |
| 列资产 Column | **folded into `TableAsset.columns`** — deliberate; a column is not independently addressable but has a derived id (`derive_column_id`) |
| 指标资产 Metric | `MetricAsset` ✅ |
| Join 资产 | `JoinAsset` ✅ (with `cost`, `cardinality`, `confidence`) |
| 术语资产 Term | `TermAsset` ✅ |
| 规则资产 Rule | → `NoteAsset` (§2.1) |
| Few-shot 资产 | `FewShotAsset` ✅ |
| 上下文资产 Context | → `NoteAsset` (§2.1) |
| **权限资产 Permission** (数据访问策略) | **absent — deliberate, see §5.4** |
| — | **`NegativeExampleAsset`** — ours only; marks a question class as unanswerable and fires the refuse gate |

Two structural notes:

- The book's `term_relationship` (L2, consumed by Cypher graph traversal for
  synonym/hypernym networks) has no counterpart — we have flat `synonyms` on
  `TermAsset` and no graph engine (§3.1).
- The book's **ID naming convention** (`tbl_<domain>_<name>`, `col_<table>_<column>`,
  `metric_<name>`, `term_<name>`, CI-enforced by regex) we match closely:
  `corpus/ids.py` owns `ID_PATTERNS` and `ID_PREFIX`. Ours is enforced at parse
  rather than only in CI.

### 2.3 Git + YAML: same choice, and we went further

§3.2's Git-vs-database table and its trade-off callout ("语义资产变更频率低……但治理要求高")
is our choice too, and the pipeline in §3.3 (pre-commit → CI → PR review → merge →
publish → sync) is recognisably ours: corpus in its own repo, git as the checkpoint
pin, `corpus_pin` / `schema` on every run.

Where we diverge:

- **The book publishes to S3 and syncs into a live PG Supernode**; we load YAML
  directly and cache rebuildable projections under `corpus/_generated/`. No
  publish-to-object-store step, no polling sync agent, no soft-delete `deleted_at`.
  Deliberate — we have no separate retrieval service to sync *to*.
- **The book's 增量发布** (`build_changeset.py` from git diff, full republish only on
  first publish or a MAJOR bump) has no counterpart; we rebuild indexes from the
  corpus and cache by content key (`corpus_index_key`, `rvgd.py:303`). Equivalent
  effect, different mechanism.
- **Corpus edit + save-to-PR is explicitly out of scope for this repo** (recorded
  scope boundary): we own the write primitives and a read-only viz; the git/PR
  round trip belongs to CI or an enterprise app. The book assumes an admin UI plus
  Git; we ship `/corpus/edit` (`api/app.py:349–472`) as a write primitive and stop
  there.

### 2.4 CI validation: four checks vs four checks, one missing

§10.2 lists exactly four CI checks. Ours, from `corpus/validate.py`:

| Book check | Blocking? | Ours |
|---|---|---|
| 语义资产一致性 (reference integrity; missing reference → block **and roll back to last green**) | 阻断 | `validate_corpus` reference checks + `_check_join_on_columns` (:474) ✅ (no roll-back-to-green concept) |
| 术语-指标映射 (every term must bind; metric's dimensions must exist) | 阻断 | `_check_metric_expressions` (:517) covers metric→column; **term→binding-target existence is checked**, but there is no "every term must have a binding" requirement — an unbound `TermAsset` is legal here and illegal in the book |
| 规则可计算性 (rule syntax parseable, dependencies declared, no cycles) | 阻断 | partial — `_check_always_note_budget` (:366) is a budget check, not a computability check; note bodies are prose, not rules, so this doesn't map |
| few-shot 质量 (example SQL parses, referenced objects exist, matches schema) | **不阻断** — drop + alert + record a quality score | **absent** — few-shot SQL is not validated against the schema at all |

We also have a check the book doesn't: `_check_physical_existence` (:553) hits the
live connector to confirm the tables and columns an asset claims actually exist. The
book's CI is corpus-internal; ours can be corpus-vs-database.

**Assessment: few-shot SQL validation is a real, unintentional gap** and a cheap
one — we already have `sqlglot` and a connector-backed existence check. The book
even tells us the right blocking level (drop and warn, don't block).

### 2.5 SemVer + certification lifecycle: the sharpest governance divergence

§3.4 is emphatic, and narrates an incident behind it: an unbumped version left the
retrieval index on stale embeddings and two days went into diagnosing it. So CI
enforces bump-on-change, MAJOR on breaking change, with a sync consequence per level
(MAJOR → full re-vectorisation, MINOR → incremental, PATCH → no re-vectorisation).
Then a lifecycle `draft → reviewed → certified → deprecated` with `quality_score`,
where **certified assets are boosted by the reranker** and deprecated ones leave the
index. Human review is required for certification — deliberately, because "CI 能检查
引用完整性，但检查不了 'GMV 不含退货订单' 这个业务规则对不对" — at a cost of 2–3 days
per asset.

Ours:

- **No SemVer.** `version: str | None = None` exists on an asset
  (`corpus/schemas.py:187`) and nothing enforces, bumps, or reads it. Versioning is
  entirely at the corpus level: a git hash pin. That is a coherent choice for a
  corpus that is built and rebuilt wholesale by a curator — but it means the book's
  incident (stale index vs new definition) is prevented by a different mechanism
  (content-keyed index cache), and the per-asset change history the book relies on
  is only recoverable from git.
- **A different lifecycle, on a different axis.** `ProvenanceStatus` is
  `proposed → draft → certified` (`corpus/schemas.py:74–77`) where the transitions
  mean *proposer emitted it → adversary passed it → human signed off (prod only, D6)*.
  There is no `reviewed`, no `deprecated`, and no `quality_score`; `confidence` is the
  nearest thing and is curator-authored.
- **Status does not drive retrieval priority.** This is the important one. The book's
  whole reason for the lifecycle is that `certified` assets get a reranker boost. We
  have no reranker (§1.3), so status has no ranking effect. `require_certified` gates
  trigger firing (`retrieval/triggers.py:23`) and that is the extent of it.

**Assessment: mixed.** The corpus-level pin instead of per-asset SemVer is
deliberate and defensible. The absence of a *quality signal in ranking* is
unintentional and is the second concrete consequence of having no reranker. Note
also that we retired the word `certified` for the answer-side assurance axis
(→ `grounded`) while keeping `ProvenanceStatus.certified` for the asset — a
terminology split the book doesn't need because it has no answer-side stamp at all.

### 2.6 Where we exceed the book on the semantic layer

The book's §9.4 is candid that authoring the YAML is the core cost — 步骤② 编写资产,
成本量级 **高（核心成本）** — and its mitigation is one LLM call producing a draft
(`generate_yaml_draft`, ~15 lines) for a human to check into a PR.

**Our entire Curator subsystem is a 2,441-line answer to that one paragraph**:
batch-planned Phase A exploration (`curator/pipeline.py:1040–1377`), a bounded write
surface where what the agent *cannot* write is a governance boundary
(`curator/deep_agent.py:269`), an adversary review pass that gates
`proposed → draft`, deterministic FK inference from naming conventions
(`_fk_candidates_from_names`, :226), an SME clarification round-trip
(`curator/sme.py`, `curator/clarifications.py`), and a validate/repair loop.

That is a genuine extension beyond the reference, and it is the part of this repo
with no counterpart in the book at all. Worth noticing: it also means our semantic
layer is *AI-authored, human-gated* whereas the book's is *human-authored,
AI-drafted*. The book's certification requirement ("business correctness can only be
judged by someone who knows the business") is the same principle as our D6
human-only `governance.excluded` and prod-only `certified` — arrived at from
opposite starting points.

---

## 3. Retrieval (RVGD) — systematic analysis

This is the area where the two designs look most similar by name and are most
different in mechanism. The section is organised by the axes on which a retrieval
system can actually differ, because an engine-by-engine table hides the divergences
that matter: the book's four engines are **four strategies over four indexes with
four admission rules feeding four slots**; ours are **two scorers over one index,
fused into one ranking**. That single structural fact generates most of what follows.

Our own module docstring (`retrieval/__init__.py`) already scores us honestly on
engine coverage — R and V ship, G is not built, D is half-built. The analysis below
goes past that self-assessment, and in one place corrects it.

### 3.1 Axis 1 — the retrieval unit (what is a document)

| | Book | Ours |
|---|---|---|
| Index count | **17** relational tables (`semlayer.*`) for R, **8** `*_embedding` tables for V, 1 AGE property graph for G, 1 cache table for D (§9.2) | **one** BM25 index + **one** embedding index over `{asset_id: document}` (`build_index`, `rvgd.py:298`; `build_embedding_index`, `embedding.py:45`) |
| Columns | first-class `column_asset` with its own id convention (`col_<table>_<column>`) and its own embedding table | **not documents.** A column's `physical_name`, `description`, and `role` are concatenated into its *table's* document (`asset_document`, `rvgd.py:132–139`) |
| Joins | AGE graph edges | no language surface at all (`asset_document` returns `""`), so unreachable by either channel — stated in the docstring |

Folding columns into the table document has three consequences we did not choose and
do not currently measure:

1. **Wide tables are systematically penalised.** BM25 length-normalises with
   `b=0.75` (`rvgd.py:164`), and a 60-column table's document is an order of
   magnitude longer than a 4-column table's. The same single-column match therefore
   scores lower on the wide table. The book, indexing the column separately, has no
   such bias — the column competes at its own document length.
2. **We cannot rank or prune columns.** `column_ids` is *derived*: for every selected
   table, every column (`rvgd.py:595–601`). `_ordered()` sorts them by
   `score_map.get(i, 0.0)` — and column ids are never in `score_map`, because
   columns are never indexed. So the effective order is **alphabetical by id, and
   nothing is ever dropped**. `assemble_context` then takes them all
   (`context.py:200`, `columns=[_column_view(c) for c in table.columns]`); the only
   filter anywhere is `governance.excluded`.
3. Therefore **§4.2's stage ③ 动态列裁剪 is not something we do differently — it is
   something we structurally cannot do.** The book prunes to
   metric/dimension/filter-relevant columns plus PK/FK and time columns. We have the
   `role` information that would drive exactly that rule, on every column, and no
   relevance signal to combine it with.

This is the strongest single "the book does it better" finding in the retrieval
comparison, and it is upstream of the reranker gap rather than an instance of it.

### 3.2 Axis 2 — channels, and how they combine

**Book (§4.1).** R, V, D run concurrently (`asyncio.gather`); D's hits are *appended
into V's `few_shots` slot*; `build_term_bindings` runs over R's and V's term hits;
then G runs **serially** over the union of collected `asset_ids` at `depth=3`,
because graph traversal needs to know which assets to walk from. Each engine owns a
slot; nothing competes across slots.

**Ours (`retrieve`, `rvgd.py:429`).** BM25 ranks the corpus; if an embedder is
configured, cosine ranks the *same documents*; the two rankings are fused by
**Reciprocal Rank Fusion** (`fuse_rankings`, `embedding.py:70`, `k=60`,
`weights=[1.0, vector_weight]`). One ranking comes out. Then per-type budgets cut it,
then deterministic grounding expands it to a fixpoint, then triggered notes are
unioned in.

Our design has two real virtues the book's does not: RRF needs **no score
normalisation** across incomparable scales, and the whole path is deterministic and
offline-testable (`HashingEmbedder`). The cost is expressiveness:

- **In the book, an exact term hit cannot be crowded out** — it is in R's pool, and
  R's pool is returned. In ours, a `TermAsset` that matched exactly is one row in a
  fused ranking competing under `term_k=5` (`rvgd.py:436`). Exact match is privileged
  by architecture there; here it is a rank position.
- **The fusion discards magnitude.** RRF keeps only positions, which is what makes it
  normalisation-free — and also means "matched every content word" and "matched one
  rare token" are indistinguishable once fused. `RetrievalResult.scores` documents
  this scar directly (`rvgd.py:238–242`): RRF values ~1/(60+rank) were displayed to
  the model as "BM25 score" (AUDIT R8), so anything reasoning about magnitude was
  reading the wrong scale.

**A correction to our own docstring.** It claims "R exact (id / physical-name lookup,
and exact hits on a term's synonyms)" ships. Reading `retrieve()`, there is no
exact-lookup step in the matching path at all. Synonyms enter the *BM25 document*
(`asset_document`, `rvgd.py:141`) and are scored, not matched. `phys_to_table`
(`rvgd.py:531`) does physical-name lookup, but in **grounding** — resolving a
few-shot's gold SQL to table ids — not in matching the question. The one genuine
R-channel in the codebase is `fire_triggers` (`triggers.py:18`): a casefolded
substring test of a curator-authored keyword against the question, hard-included and
never fused (`rvgd.py:545`, "Keyword PIN (never RRF)"). That is exact matching with
unconditional admission — the book's R semantics exactly — **and it applies only to
`NoteAsset`, only via explicitly authored `triggers`, and regex triggers are
deferred** (`triggers.py:55`). So: R is real, and its coverage is one asset type out
of seven.

### 3.3 Axis 3 — what the query is

**Book:** the retrieval query is a *structured extraction*. QU (an LLM node) produces
`search_terms` and entities, R matches them term-by-term, and §4.1's code comment
notes this costs nothing extra ("复用 QU 的 LLM 输出，零额外调用").

**Ours:** the raw question string. `index.rank(question)` (`rvgd.py:467`) and
`embedder.embed_one(question)` (:479).

This follows from §0 — we have no QU node — and it has a measurable side effect that
the code already half-recognises. `rvgd.py:82–92` defines `_QUESTION_STOPWORDS` with
this justification:

> BM25's IDF is supposed to discount them, but on a single-schema corpus (a handful of
> documents) every term looks rare, so "what is the airspeed of a swallow" scores as
> well as a real question.

That reasoning is about **ranking on a small corpus**. But the stopword list is only
consumed by `content_terms` → `lexical_coverage` (`rvgd.py:119`). `BM25Index.rank`
uses `tokenize(question)` (`rvgd.py:221`) — the full token stream, stopwords
included. Since documents differ in how much prose they carry, the noise does not
cancel: a table with a written `description`/`grain` accumulates matches on
`the`/`of`/`is` that a bare physical name cannot, so the query's function words act
as a **mild prose-density prior on the ranking**. Whether that is desirable is
arguable — it happens to align with the `_SEMANTIC_BOOST` thesis of preferring
curated language — but it is not currently a decision, and it is unmeasured.

### 3.4 Axis 4 — admission control vs. pure ranking

This is the second structural divergence, and it is where the book is most clearly
stricter.

| | Book | Ours |
|---|---|---|
| V admission | cosine **≥ 0.65** *and* top-k 20 | `s > 0.0` (`embedding.py:40`) — for embeddings that is nearly always true |
| D admission | **≥ 0.95** *and* `approved` *and* `confidence ≥ 0.9` *and* `fail_count ≤ 3` | none; few-shots ride the fused ranking under `few_shot_k=3` |
| Quality tiers | > 0.8 high / 0.65–0.8 medium / < 0.65 low → behaviour changes (§4.4) | none |
| Floor | an asset below threshold is **not retrieved** | there is no floor; the docstring says so — "the per-type budget in `retrieve` has no minimum" (`rvgd.py:106`) |

So a question about something the corpus does not contain still comes back with eight
tables. We know this, precisely, and we built an instrument for it rather than a
gate: `lexical_coverage` (`rvgd.py:100`) measures the fraction of the question's
content terms that appear anywhere in the index vocabulary, and its docstring is
explicit that it is deliberately *not* a score threshold, because "a fused RRF rank
is not comparable across questions, and raw BM25 on a few-document corpus is
dominated by IDF noise."

That reasoning is sound and it is a genuine insight the book does not have — the
book's 0.65 threshold on pgvector cosine *is* the cross-question comparison it
cannot justify either. But note the asymmetry in what each side then does:

- The book's low tier **changes behaviour**: widen the threshold, raise `top_k`, or
  fetch supplementally.
- Our coverage signal **changes only the report** — it feeds `UncertaintySignals` →
  the assurance axis of the reliability stamp (`governance.py:808`,
  `_weak_retrieval`). Retrieval itself proceeds unchanged.

**We measure the condition that would justify acting, and do not act on it in
retrieval.** That is the honest statement of the gap. It is also the cheapest
high-value thing in this section: the signal exists, is per-question, and is already
plumbed.

### 3.5 Axis 5 — the second pass

Book: four rerank stages (§4.2). Ours: none — there is no second pass over the
retrieved set at all, only one ranking, one budget cut, and deterministic expansion.

| Book stage | Mechanism | Our state | Blocked on |
|---|---|---|---|
| ① term-binding boost | bound column → **0.95**; parent table **+0.25**; unbound table **−0.10** | absent | nothing — `TermAsset.binding` is populated (§1.2) |
| ② QU-signal rerank + `certified` bonus | boost assets matching the extracted metric/entity; boost certified | absent | the QU half needs §0's node; **the `certified` half needs nothing** |
| ③ dynamic column pruning | keep metric/dimension/filter-relevant + PK/FK + time | **structurally impossible** | per-column relevance (§3.1) |
| ④ join-path simplification by fanout risk | dedupe, prefer low-risk | absent | we have `cardinality` on `JoinAsset`; nothing consumes it for ranking |

Stage ② is worth isolating because it is the mechanism the book's whole certification
lifecycle exists to feed (§2.5): asset quality is supposed to influence what the
model sees. Since we have no rerank pass, `ProvenanceStatus` affects ranking nowhere.
The one place asset status gates anything is `fire_triggers`'
`require_certified` (`triggers.py:50`) — again, notes only.

### 3.6 Axis 6 — budget

| | Book (§4.5) | Ours |
|---|---|---|
| Unit | **tokens**, as proportions of one budget | **item counts** per type, plus per-note character caps |
| Split | 30% tables / 25% columns / 15% metrics / 10% few-shots / 10% join rules / 10% business rules | `top_k=8` tables, `few_shot_k=3`, `term_k=5`, `metric_k=5`, `note_k=5` (`rvgd.py:433–438`); `always_note_global_max=8`, `always_note_char_max=2000`, `pin_max=3`, `NOTE_TEXT_MAX_CHARS=4000` |
| Few-shot count | complexity-graded **2 / 4 / 6** | flat 3 (`render_few_shots(limit=3)`) |
| Within-category order | by relevance, with term-bound and `certified` first | by fused rank, `confidence` as tie-break only (`rvgd.py:494–501`) |
| Provenance | ratios tuned on ~200 real questions; the chapter admits they are not optimal | untuned; `_SEMANTIC_BOOST` held at 1 pending a calibration run (`rvgd.py:262–274`) |

The consequential difference is not the ratios, it is the **unit**. Because we budget
by asset count, a table with 60 columns and a table with 4 both consume one of eight
slots — so our budget does not bound prompt size at all. `context_chars` and
`context_hash` measure the result after the fact, which is more than the book records
per run, but the book's scheme bounds it by construction and can answer "what
fraction of the prompt was tables vs notes." We cannot.

Both designs independently reached the same insight from opposite directions, which is
worth noting as convergent rather than divergent: the book rejected relevance-based
truncation because "有些资产分数不高但不可或缺（比如 join 规则）", and we reached the
same conclusion mechanically — `assemble_context` takes joins **by licensed scope,
not by retrieval score**, because `JoinAsset` has no language surface to score
(`context.py:234`).

### 3.7 Axis 7 — reaching structure (the G engine)

Book: AGE Cypher, `depth=3`, over `ttd_governance` — Table/Column/Metric/Term nodes
and **13 edge types** — run after the first stage because it needs upstream
`asset_ids`. Its output is join paths for the Steiner planner, plus term-relationship
traversal (synonym/hypernym networks).

Ours: no G retrieval channel. Structure is reached two other ways, and the split
matters:

- **Join reachability**: not retrieved. `assemble_context` includes every join asset
  internal to the licensed set (`context.py:234`), and the licensed set is widened by
  the Steiner planner's intermediate points (`agent.py:828`). So a bridging table the
  question never mentions *does* arrive — via planning, not recall. **Equivalent
  outcome by a different route**; not a gap.
- **Term relationships**: genuinely missing. The book's graph carries term-to-term
  edges, so a hypernym query ("解热镇痛药" reaching 阿司匹林) traverses. We have a flat
  `synonyms` list on `TermAsset` (`asset_document`, `rvgd.py:141`), which handles
  aliases and cannot handle hierarchy. This is a real capability gap, and the book's
  §3.5 argument for building its own semantic layer rests partly on it ("阿司匹林 =
  乙酰水杨酸 = ASA" is the alias case it cites; the graph is what generalises it).

### 3.8 Axis 8 — accumulation (the D engine)

The book's D is the only place its design is *more* adaptive than ours, and it is
also where the book is most careful about safety:

- `record_success` writes with `review_status="pending_review"` — **new entries do not
  participate in RAG**.
- `approve` is a separate admin action.
- `search_similar` gates on `approved` AND similarity ≥ 0.95 AND `confidence ≥ 0.9`
  AND **`fail_count <= 3`**.

Two things there we have no counterpart for. The obvious one: nothing accumulates
here — few-shots are curator-authored at build time and a successful serve teaches
the corpus nothing. The less obvious one: **`fail_count` is a per-asset failure
signal**. A few-shot that keeps preceding wrong answers gets demoted automatically.
We have no per-asset feedback of any kind; our nearest concept is the interaction
signal, captured raw with trust-tiering deliberately deferred.

The book's rationale for the approval gate — "如果错误 SQL 被存入 few-shot，会污染后续
生成" — is the same concern that made us defer, so this is a difference in how far the
design got, not in what either side believes. But the book's three-step workflow is a
usable blueprint for the thing our own roadmap already wants.

### 3.9 Axis 9 — measurability

The one axis where we are clearly ahead, and it is worth stating because it changes
what the other eight gaps cost.

| | Book | Ours |
|---|---|---|
| Retrieval quality measure | Ragas `context_precision` / `context_recall`, LLM-judged | `eval/retrieval_eval.py`: **table recall@k over gold SQL, no LLM** — deterministic, repeatable, free |
| Per-run retrieval record | none | `routed_schemas`, `shortlisted_schemas`, `schema_pick`, `pick_hit`, `routing_bypassed`, `retrieved_tables`, `n_notes_injected`, `n_few_shots_injected`, `context_chars`, `context_hash` — declared in `metrics.py`'s row register |
| Channel attribution | none | `shortlist_schemas` records which channel actually ran, so a silent embedding degradation is attributable (`schema_router.py:250–255`) |
| Index cost control | pgvector, always warm | `RetrievalIndexCache` (`rvgd.py:316`) — the embedding rebuild was ~55% of the serve path's non-model CPU; caching is keyed by routed-corpus content |

So every gap in §3.1–3.8 is a *measurable* gap here. The book cannot tell you what its
reranker weights are worth; we can tell you what ours would be worth, for free, before
building them.

### 3.10 Ledger

**The book does these better — six, in rough order of what they'd buy us:**

| | What | Why it matters here | Cost |
|---|---|---|---|
| B-1 | **Column-level retrieval units**, enabling relevance-ranked and pruned columns (§3.1) | We ship every column of every licensed table, alphabetically, unpruned. On a wide obfuscated schema that is the bulk of the prompt and the bulk of the decoy surface | High — a second index and a new id-addressable unit |
| B-2 | **Absolute admission thresholds** that change behaviour, not just the report (§3.4) | We already compute the signal (`lexical_coverage`) and act on it only in the stamp | Low |
| B-3 | **Term-binding boost in ranking** (rerank ①) (§3.5, §1.2) | The book's strongest anti-hallucination lever; our bindings are inert | Medium |
| B-4 | **Token-unit budgets** instead of item counts (§3.6) | Our budget does not bound prompt size; 8 tables can be 200 columns | Medium |
| B-5 | **Few-shot accumulation with an approval gate and `fail_count`** (§3.8) | The only adaptive loop in either design; the book also shows how to make it safe | Medium, and gated on interaction signals |
| B-6 | **Term-relationship graph** for hypernym/hierarchy generalisation (§3.7) | Flat synonyms cannot express "this term subsumes those" | High |

**We do these better — three:**

| | What |
|---|---|
| O-1 | **Deterministic, offline, LLM-free retrieval measurement** (`retrieval_eval.py`) plus per-row retrieval provenance. The book's equivalent is LLM-judged and unreproducible |
| O-2 | **Normalisation-free fusion** (RRF) and a fully deterministic path testable with `HashingEmbedder` — no threshold to tune per corpus, no cross-question score comparison to justify |
| O-3 | **Honest treatment of the out-of-corpus question**: `lexical_coverage` measures vocabulary overlap rather than pretending a similarity score is comparable across questions — a problem the book's 0.65 threshold has and does not acknowledge |

**Equivalent by different means — two:** bridging-table discovery (their G traversal
vs. our Steiner points widening the licensed set), and the "a low-scoring join rule is
still load-bearing" insight (their category budget vs. our joins-by-licensed-scope).

**Shared gaps — not a deficit against the reference:** query decomposition for
multi-part questions, model-based (cross-encoder) reranking, and routine
pre-generation retrieval assessment. Their §4.4 and §4.6 concede all three.

### 3.11 Reading of the whole axis set

The pattern across the nine axes is consistent: **the book's retrieval is
architecturally opinionated and empirically unmeasured; ours is architecturally thin
and thoroughly measured.** The book privileges exact matches by giving them their own
pool, gates admission with absolute thresholds, prunes at column granularity, and
budgets in tokens — four opinions, each tuned on ~200 questions it cannot show us.
We have one index, one fused ranking, no thresholds, no pruning, count budgets — and
an instrument that can price every one of those four opinions before we adopt it.

That suggests the order of work is not "close the gaps" but "measure the gaps",
starting with the two that are nearly free to test: unpruned columns (B-1's payoff is
directly visible as a recall@k-vs-prompt-size curve) and the coverage floor (B-2 is a
one-line branch on a signal already computed).

---

## 4. The query planner — we have the algorithm and use it for something else

This is the most interesting single finding, and it corrects an assumption I would
otherwise have made from the commit history.

§6.2 makes the Steiner tree the book's flagship technical claim: "join 路径选择从此
从 'LLM 猜' 变成了 '算法算'", with KMB (Kou-Markowsky-Berman), the approximation
ratio `2(1-1/|T|)`, a four-step algorithm (度量闭包 → MST → 替换边 → 重建子树), a
four-signal weighted cost function (0.4 output rows / 0.3 FK cardinality / 0.2 manual
annotation / 0.1 inverted query frequency), and an empirical table claiming
+12% on complex queries and +24% on bridge-table queries.

**We have it.** `graph/planner.py` is titled "Steiner-tree join planning (Analyst
step 6)" and uses `networkx.algorithms.approximation.steiner_tree` — the same KMB
approximation. `JoinAsset.cost` exists and is commented `# Steiner-planner input`
(`corpus/schemas.py:313`). We even added something the book doesn't have: a
confidence penalty in the planning weight,
`cost * (1 + LOW_CONFIDENCE_PENALTY * (1 - confidence))`.

**But it does not generate SQL.** Its own docstring says so:

> The plan currently feeds two consumers: L4 licensing (its Steiner points widen the
> term-semantics scope, alongside the FK `join_neighborhood`) and the reliability
> stamp (`min_confidence`). A plan-consuming SQL generator that emits the joined
> query is the LLM seam; today the deterministic template generator handles the
> single-metric path.

So in the book, the planner *decides the joins and the LLM fills in the rest*. Here,
**the model decides the joins and the planner audits the scope** — it computes which
tables the model is allowed to touch (`agent.py:828`) and how confident the join path
was (`governance.py:803`). The algorithm is identical; its position in the pipeline
is inverted.

The cost model is also much thinner than the book's: we have `JoinAsset.cost`
(curator- or human-authored, the book's 0.2 signal) and `confidence`. We have none of
output-row statistics, FK cardinality from a catalog, or query-frequency
down-weighting — all three of which the book sources from "企业已有数据平台前三年的
元数据积累", which we do not have and cannot synthesise.

**Assessment: deliberate positioning (it follows directly from §0's ADR 0002 — if
the agent decides, the planner cannot pre-decide), with an unintentional consequence:
the book's headline claim is untested here.** We cannot say whether algorithmic join
planning beats model-chosen joins, because we never run the arm where the planner
drives generation. That is a *measurable* question on our existing ladder and it is
not currently on any tracker.

The book's degradation design is worth noting too: on a disconnected terminal set it
distinguishes `Heal` ("missing join definition X-Y, ask the data team") from `Reject`
("no path, confirm your question"). We have `detect_missing_join_path` and a
`missing_edge_refusal` (`governance.py:216`) — the same distinction, wired to a
refusal rather than a repair request.

Also from §6.3, three algebraic rewrites — chasm-trap (aggregate-before-join),
redundant-join elimination, `EXISTS` for `DISTINCT+JOIN` — gated to complex queries
only by the Router's complexity judgement. **We have none of these**, and no
complexity classification to gate them with. The chasm trap in particular is the
book's most-cited failure mode (§1.6, §6.4: 80% of errors in three trap patterns).
Our `eval/error_taxonomy.py` classifies outcomes but does not name fan-out
double-counting as a category.

---

## 5. Guardrails — the closest match in the whole comparison

### 5.1 The five layers line up exactly

| # | Book §7.1 | Ours (`GuardrailLayer`, `guardrails.py:54`) |
|---|---|---|
| ① | 语法 syntax | `syntax` |
| ② | 策略黑名单 policy blacklist (DROP/DELETE/TRUNCATE) — **拒绝不重试** | `policy_blacklist` — `_HARD` in `middleware.py:44`, immediate refuse |
| ③ | AST 列白名单 | `ast_column_allowlist` |
| ④ | 术语语义 term semantics | `term_semantics` |
| ⑤ | 成本估算 cost estimate | `cost_estimate` |

Same five, same order, same fatal-vs-repairable split (② is a red line; the rest
route to repair). Both use `sqlglot`. This is the part of the design that survived
contact with reality unchanged, on both sides.

### 5.2 …but ④ and ⑤ mean different things

**Layer ④.** The book's L4 validates *term fidelity*: does the SQL compute GMV
per `metric_gmv`'s definition, and does it read the bound column from the bound
table? Ours (`_layer_terms`, :763) validates *scope*: every base table the query
touches must be in the licensed set (retrieval hits + Steiner points), with
schema-qualification rules and ambiguous-bare-reference refusal. Ours is a stronger
containment check and a weaker semantic check. Given §1.2 (no binding propagation),
we have nothing to check term fidelity *against*.

**Layer ⑤.** The book's L5 calls `EXPLAIN` and compares estimated scan against
`cost_limit_gb=500`, feeding a `Heal` hint ("扫描量过大，规划器调整 join 策略"). Ours
(`_layer_cartesian`, :667) is a structural union-find over join predicates: two or
more base tables in a scope with no connecting equality is an accidental cartesian
product, blocked fail-closed. Its docstring is explicit that "numeric EXPLAIN-based
cost (Postgres / Redshift) is future per-dialect work" (:686).

**Assessment: the L5 numeric-cost path is a known, documented, unbuilt piece** —
and it is the layer most directly protective of a production data plane. The
structural guard catches a different (and real) failure the book doesn't address at
all; they are complements, not substitutes.

### 5.3 Prompt injection: we defend the corpus, the book defends the user input

§7.1's injection defence is three-part: ① input-layer pattern detection on the user's
question (7 patterns: `"ignore previous"`, `"disregard above"`, `"system prompt"`,
`"DROP TABLE"`, `"DELETE FROM"`, `"--"`, `"/*"`), ② put sensitive system-prompt
instructions *after* user input, ③ least privilege so a successful injection has a
bounded blast radius.

We have ① — but pointed the other way. `note_inject.py:302–316` carries
`_INSTRUCTION_PREFIXES` (`"ignore previous"`, `"ignore all previous"`, `"system:"`,
`"you are now"`, `"forget everything"`, `"override:"`, …) matched at the start of a
stripped line and replaced with `[redacted: instruction-shaped line in corpus
content]`. That is defence against **an injected note in the corpus**, i.e. the
indirect-injection vector the book's own callout admits it does not solve
("精心构造的间接注入（如数据表里藏的指令）仍可能绕过"). Our docstring is equally
careful: "Defence in depth, not a claim to have solved prompt injection."

There is **no pattern check on the user's question** anywhere in `src/`.

**Assessment: we solved the book's admitted weakness and skipped its shipped
defence.** The user-input check is ~10 lines and its absence is unintentional. Worth
noting the asymmetry is partly justified — our threat model has a curator writing to
the corpus, which the book's human-authored corpus doesn't face — but that justifies
adding ①, not omitting it.

### 5.4 Execution safety: same primitives, one whole axis out of scope

| Book §7.3 | Ours |
|---|---|
| 强制 LIMIT — auto-inject `LIMIT N` | `_force_row_limit` in the connectors + `max_rows` fetch cap ✅ (and see the finding below) |
| 查询超时 — `statement_timeout` | `SET statement_timeout` (postgres), `set_progress_handler` (sqlite) ✅ |
| PII 分级暴露 — RLS/CLS + masking UDF, by role | **out of scope, recorded.** `identity` reaches `Gateway.execute` and is used only for the audit row. Our nearest primitive is `governance.excluded` (human-set, permanent, all environments) — a corpus-side exclusion, not a per-role runtime filter |
| 执行隔离 — separate Redshift Serverless instance | separate DB / credentials, no Serverless concept |

The book's principle — **AI 的权限 ≤ 用户的权限**, RLS enforced at the database so
even a WHERE-less generated query is filtered — is one we agree with and explicitly
placed outside this repo (multi-schema design, 2026-07-11: no RLS in this repo). Our
`权限资产` gap (§2.2) is the same decision seen from the corpus side.

**One live defect surfaces here, found in the architecture review of the same
codebase and worth repeating because the book's framing makes it sharper:** the book
treats the row cap as a governance property of the data boundary. Ours is a
constructor default (`Gateway.__init__(max_rows=1000, timeout_s=30.0)`) with **no
`Settings` field**, so the eval drivers pass `max_rows=200_000` at six call sites
while the API takes 1000 — a 200× divergence on a documented governance parameter,
recorded in no manifest. The book would call that a governance failure, and it is
right.

---

## 6. Memory, cache, and models

### 6.1 Four memory layers → one

§8.1's four stores, with cognitive-science framing and concrete TTLs:

| Book layer | Store | TTL | Ours |
|---|---|---|---|
| Working | LangGraph Checkpointer (PG) | session | ✅ **built** — checkpointer-backed, session-scoped |
| Profile | `ttd_memory_records` kind=profile | 365 d | **design only; empty protocols and config deleted 2026-07-28 (D8)** |
| Episodic | kind=episodic | 90 d + decay 0.02/day | same |
| Correction | kind=correction | 180 d | same |

The book also specifies **route-aware memory budgets** (`MemoryFacade`:
`nl2sql_query` 5/2/5, `kpi_lookup` 2/0/1, `business_knowledge_qa` 3/1/1,
`deep_analysis_workflow` 8/8/4) — which cannot exist here because we have no routes
(§0).

**Assessment: deliberate deferral, honestly documented.** The glossary already says
"Only Working exists (built, session-scoped). Profile, Episodic and Correction are
design, not code". The book's own callout on 自治记忆 vs 触发式记忆 lands where we
did — it chose trigger-based memory for auditability and notes the evolution path is
"LLM proposes within limits, written after approval", which is exactly our
capture-first / human-gate stance on interaction signals.

### 6.2 The SQL semantic cache: we deleted ours

The book gives the cache a whole route (缓存命中, §5.2), a threshold (**≥ 0.92**,
top_k 1), a TTL (**15 分钟**), a deliberate choice of SQL-only over result caching
("宁可命中缓存仍执行 SQL，也不要返回脏数据"), and a claimed **~35% hit rate** carrying
a third of the LLM-cost saving.

We had one and removed it: `2f86547 refactor(analyst): delete the never-wired
semantic cache and the dead memory knobs`. There are zero references to
`semantic_cache` / `sql_cache` in `src/` today.

**Assessment: deliberate, and correct for where we are.** It was never wired — a
cache that no path consults is indirection, not a cache. But note what we gave up in
comparison terms: the book's cost argument depends on it, and its P50 (~8s) is an
average over a 35%-cached distribution. Any latency or cost comparison to the book is
apples-to-oranges until this is either rebuilt or ruled out.

### 6.3 Model registry, fallback chain, circuit breaker

§5.4 specifies a `ModelRegistry` with per-task model assignment
(`query_understanding` → fast, `sql_generation` → strongest with a 2-model fallback
chain, `insight` → long-form), admin online override, and a circuit breaker (3
consecutive failures → 60s pause → `ModelUnavailableError` when the whole chain is
open).

Ours: one `ModelConfig` (`config.py:37`) naming one `llm_model` plus a provider
(`openai` | `bedrock`), reasoning effort, and max output tokens. No per-task mapping,
no fallback chain, no circuit breaker. We do record decoding temperature on every run
because it was identified as the largest source of run-to-run variance (AUDIT E5) —
which the book does not mention at all.

**Assessment: per-task assignment is meaningless without the book's node taxonomy
(§0), so it is deliberate-by-implication. The circuit breaker is a genuine gap** and
one our own eval already feels: `error_type` was added to the generation row
specifically so "a wave of `model_error`" can be told apart from a bug — i.e. we
built the *observability* for the failure the breaker would prevent, and not the
breaker.

---

## 7. Evaluation and observability — where we are ahead

This is the one area where the comparison runs the other way, and it should be stated
as plainly as the gaps.

| Book §10 | Ours |
|---|---|
| **LLM-as-a-Judge**, 4 dimensions (correctness / explainability / safety / efficiency), 1–5 scale, plus DeepEval + Ragas for `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall` | **Execution-accuracy grading against gold** (BIRD), result-hash comparison strict and lenient (`hash_grade.py`), `sql_diff`, an error taxonomy, oracle rungs that isolate where accuracy comes from, statistical comparison with paired tests and power analysis (`power.py`) |
| Self-declared benchmark table (95%+ / 85–90% / P50 ~8s / heal ~70% / cache ~35% / satisfaction 4.2/5.0), labelled as scaled-from-real | **A metric register** (`metrics.py`, 66 declared manifest fields, 70 declared row fields), **quotable/comparable gates** (`index.py`), a run ledger, and the explicit retirement of every number produced before 2026-07-25 because four defects made them untrustworthy |
| 四通道可观测 — Langfuse (trace/span/generation), AutoMQ/Kafka event stream, structlog, Prometheus, unified behind `ObservabilityFacade`, correlated by `trace_id` | Langfuse + LangSmith for traces/cost; `stage_events.jsonl` per (question, arm, stage); `runs/index.jsonl`; durable run log with tiering (ADR 0004). **No Kafka event stream, no Prometheus, no unified facade** |
| Certification lifecycle drives reranker priority | status does not affect ranking (§2.5) |

Two specific things we have that the book has no equivalent for:

- **The two-axis reliability stamp** (D5): `safety_clearance` (hard boolean gate) and
  `semantic_assurance` (`unflagged` / `heuristic` / `unverified`), computed in one
  place from `UncertaintySignals` (`governance.py:808`) and impossible for a caller
  to construct inconsistently. The book's answer carries no such marking; its quality
  signal is on the *asset*, not on the *answer*.
- **Falsifiable prompt variants** — a prompt registry where the variant is part of
  the run identity and `prompt_set_hash` hashes the text so an in-place edit cannot
  masquerade as the same configuration (`prompts/registry.py:758`).

What the book has and we should want: the **structured audit event stream** is not
just observability, it is the "谁查了什么数据" record that its 合规 argument rests on
(the 可归属 requirement, `trace_id` extending the data platform's `batch_id`). We
have the material (the run log, the ledger) and no stream. And **Ragas'
`context_recall`** is close to a metric we actually need — our schema-routing recall
problem is exactly "did we retrieve the context required to answer" — though
`eval/retrieval_eval.py` measures table recall@k over gold SQL without an LLM, which
is better for our purposes than an LLM-judged version.

---

## 8. The book's own admitted limits, checked against ours

§10.5 lists five things the book says its system cannot do. Worth checking because a
"gap" that is shared is not a gap in the comparison:

| Book limitation | Us |
|---|---|
| 跨库关联 — cannot join Redshift with an external source | Same shape, different bound: one database, many schemas, executable cross-schema joins (D15). Cross-schema joins are something the book cannot express at all |
| 复杂窗口函数 — ROW_NUMBER/RANK accuracy drops | Untested as a category; `error_taxonomy` does not name it |
| 非结构化推理 — "analyse why GMV dropped" needs causal reasoning | Same. We have no analytical agent and no ML registry (§8.3's six capabilities) |
| 实时流查询 — "last 5 minutes" impossible on a batch warehouse | Same |
| 超长复杂查询 — 10+ table joins drop to ~60% | Untested at that size |

§4.6's four architectural limits are also largely ours: no **query decomposer** for
multi-part questions ("对比华东和华北的 GMV 趋势"), rule-based rather than model-based
reranking (we have neither), retrieval assessment not routine, and a single-machine
vector ceiling (the book's 百万级 OK / 千万级 degrades, with `VectorStore` abstraction
named as the escape hatch — we have `Embedder` as that seam already).

---

## 9. Classification: deliberate, drifted, or shared

The whole point of the exercise. Ordered by how much I think each deserves a
decision.

### Deliberate divergences with a recorded reason — do not reopen

| # | Divergence | Where recorded |
|---|---|---|
| D-1 | Deterministic DAG + 7 intent routes → **governed agentic core as the only serve path** | ADR 0002; governance = topology, not trust |
| D-2 | `business_rule` + `business_context` asset types → **one `NoteAsset`** with scope prefixes and tri-modal retrieval | ADR 0003 / D17 |
| D-3 | Per-asset SemVer → **corpus-level git pin** as the version | corpus-in-its-own-repo decision (D13) |
| D-4 | RLS / CLS / PII grading / 权限资产 → **out of scope for this repo** | multi-schema design 2026-07-11 |
| D-5 | Human-authored corpus with LLM-drafted YAML → **AI-authored, human-gated corpus** (the whole Curator) | pipeline design 2026-07-12 |
| D-6 | Semantic SQL cache → **deleted** because it was never wired | `2f86547` |
| D-7 | Profile / Episodic / Correction memory → **design, not code** | D8, glossary |
| D-8 | Steiner planner drives SQL → **Steiner planner audits scope and stamps confidence** | `graph/planner.py` docstring; follows from D-1 |
| D-9 | LLM-as-a-Judge + self-declared benchmarks → **execution-accuracy grading with a metric register and comparability gates** | experiment-integrity overhaul 2026-07-25 |

### Unintentional gaps — nobody decided these

| # | Gap | Cost to close | Why it matters |
|---|---|---|---|
| U-1 | **Term-binding propagation** (§4.3): bindings exist in the corpus and affect nothing downstream except schema-document composition | Medium | The book's strongest stated anti-hallucination lever; our bottleneck is retrieval |
| U-2 | **No reranker at all** (§4.2, §3.5): no second pass, so no term boost, no `certified` preference, no fanout-aware join ordering | Medium | Also the reason asset quality status has no effect on what the model sees (§2.5) |
| U-11 | **Columns are never ranked or pruned** (§3.1): every column of every licensed table reaches the prompt, alphabetically, because columns are folded into the table's document and so carry no relevance score | High (needs a column-level index) | The largest slice of the prompt and of the decoy surface; upstream of U-2's stage ③ |
| U-12 | **No admission floor** (§3.4): `lexical_coverage` is computed per question and consumed only by the assurance stamp; retrieval returns `top_k` tables regardless | Low | We measure the condition that would justify acting and don't act on it |
| U-13 | **The BM25 query includes stopwords** (§3.3): `_QUESTION_STOPWORDS` reaches `lexical_coverage` but not `BM25Index.rank`, so function words act as an unmeasured prose-density prior | Low | The comment justifying the list is about ranking noise on small corpora |
| U-14 | **Budgets are item counts, not tokens** (§3.6): eight tables can be forty columns or four hundred, so nothing bounds prompt size ex ante | Medium | `context_chars` measures it after the fact; the book's split bounds it by construction |
| U-15 | **R is exact-match for notes only** (§3.2): `fire_triggers` is the one unconditional-admission path, keyword-only (regex deferred), `NoteAsset`-only — and `retrieval/__init__.py` overstates R's coverage | Low (the docstring); Medium (the mechanism) | Exact synonym hits on terms compete in the fused ranking instead of being admitted |
| U-3 | **Few-shot SQL is never validated** against the schema (§10.2's fourth CI check) | Low | We already have `sqlglot` + connector-backed existence checks; the book even tells us to warn-not-block |
| U-4 | **No input-layer prompt-injection check** on the user's question (§7.1 ①) | Low | ~10 lines; we built the harder half (corpus-content sanitisation) already |
| U-5 | **L5 numeric cost** (`EXPLAIN` vs a scan budget) is documented as future work | Medium, per dialect | The layer most directly protective of a production data plane |
| U-6 | **No circuit breaker / model fallback chain** (§5.4) | Low–medium | We built `error_type` to *observe* the failure this prevents |
| U-7 | **Algebraic rewrites** — chasm trap, redundant join, `EXISTS`-for-`DISTINCT` (§6.3) — absent, and no complexity signal to gate them | High | The book attributes 80% of its failures to these trap patterns; our `error_taxonomy` does not name fan-out double-counting |
| U-8 | **No unbound-term CI rule**: a `TermAsset` with no binding is legal here, illegal in the book | Low | An unbound term is a term that cannot route |
| U-9 | **Dynamic few-shot accumulation** (§8.2) — retrieval works, nothing accumulates from a verified success | Medium | Depends on interaction-signal work already designed |
| U-10 | **The row cap / statement timeout have no `Settings` field**, so eval runs at 200× the served cap, unrecorded | Low | A governance parameter that is neither configurable nor quotable |

### Gaps the book shares — not a deficit against the reference

Pre-generation retrieval-quality assessment (§4.4's own retraction), query
decomposition for multi-part questions, model-based reranking, causal/unstructured
analysis, real-time streaming, 10+ table joins, complex window functions, and the
single-machine vector ceiling. Building any of these puts us **ahead of** the
reference rather than level with it.

### Capabilities we have that the book has no counterpart for

The two-axis reliability stamp (D5); negative examples driving an immediate refuse
gate; corpus-vs-database validation (`_check_physical_existence`); reliability
caveats on columns; the curator's bounded write surface as a governance boundary
(enforced-by-absence, tested); notes with regex-trigger PIN and agent-fetch
(`read_notes` / `grep_notes`); prompt variants as part of run identity; the whole
comparability apparatus; multi-schema serving with executable cross-schema joins;
corpus-content injection sanitisation; SME clarification round-trip on the build side.

---

## 10. What I would do with this

Three things, in order, and none of them is "converge on the book".

1. **Decide on term binding (U-1) explicitly.** It is the book's flagship
   mechanism, it is inert in our corpus, and our measured bottleneck is retrieval.
   Either wire it — binding-aware ranking, a binding-constraint block in the prompt,
   term fidelity in L4 — or record why not. The current silence is the worst option.
   This subsumes most of U-2, because a reranker with only stage ① is still a
   reranker.
2. **Run the arm the book's headline claim needs (D-8).** We have KMB, a cost field,
   and an eval ladder. Nobody has measured planner-driven join generation against
   model-chosen joins in this codebase. The book asserts +12% / +24%; we are in a
   position to find out, and it is a one-arm addition rather than a redesign.
3. **Price the retrieval gaps before closing them (§3.10, §3.11).** The book's four
   retrieval opinions — privileged exact-match pool, absolute thresholds, column-level
   pruning, token budgets — were tuned on ~200 questions it cannot show us. We have
   `eval/retrieval_eval.py`, which measures table recall@k over gold SQL with no LLM
   and no cost. Two of the four are nearly free to test: **U-12** (a coverage floor is
   a one-line branch on a signal already computed) and **U-11** (whose payoff shows up
   directly as a recall@k-versus-prompt-size curve). Test, then decide. This is also
   the natural home for the `_SEMANTIC_BOOST` calibration that `rvgd.py:269` has been
   waiting on.
4. **Close the cheap ones (U-3, U-4, U-8, U-10, U-13, U-15's docstring).** All are
   small, most are governance-shaped, and all are the kind of gap that reads as a
   decision when it isn't.

Two things explicitly *not* recommended: rebuilding the semantic cache (D-6 was
right, and rebuilding it before there is a real query distribution would be
premature), and adopting the book's four observability channels (our two-tracer +
ledger setup answers the same questions without three more services to run — though
the audit event stream in §7 is worth a separate look on compliance grounds, not
observability grounds).

---

## Provenance

Book chapters read: all 40 notes under `Books/从数据到智能/`. §2.1–2.4, §3.1–3.5,
§4.1, §4.3, §5.2 read directly and in full; §4.2, §4.4–4.6, §5.3–5.4, §6.2–6.4,
§7.1–7.3, §8.1–8.3, §9.2, §9.4, §10.2–10.3, §10.5, §1.3, §1.6 extracted in detail.
§1.1–1.2, §1.4–1.5, §2.5, §5.1, §6.1, §9.1, §9.3, §9.5, §10.1, §10.4, and the
Appendix were surveyed but carry no comparison-bearing content not covered above.

Repo state: `2187ead` on `docs/prune-finished-plans`. Every claim about this repo
carries a file:line; anything unverifiable is marked as such in place. Prior plan
documents and reviews were deliberately not consulted, so this is an independent
reading rather than a restatement.
