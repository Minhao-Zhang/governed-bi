# Agentic BI Curator：LLM 调用全流程

本文逐次调用地追踪离线策展流水线（`curator/`），展示每一个由模型驱动的步骤实际
发出的*逐字*文本。它是 [Curator](curator.zh.md) 与 [Pipeline design](pipeline-design.md)
的补充。那两份文档描述的是周围的设计；这里的目标更窄：把每一条系统提示逐字
重现，把每一条用户消息连同动态内容注入处的占位符一起展示，并把每个 deep agent
的工具循环呈现为一份示意性的对话记录。

> 实现：[`src/governed_bi/curator/llm_proposer.py`](../src/governed_bi/curator/llm_proposer.py)、
> [`prompts.py`](../src/governed_bi/curator/prompts.py)、
> [`pipeline.py`](../src/governed_bi/curator/pipeline.py)、
> [`seed.py`](../src/governed_bi/curator/seed.py)、
> [`deep_agent.py`](../src/governed_bi/curator/deep_agent.py)。

## 概览：三个由模型驱动的步骤

策展（curation）按 schema 逐个离线运行，包含三个由模型驱动的步骤，外加围绕它们
的确定性脚手架（画像、播种、校验）：

- **(1) 画像增强（profiling enrichment）**，通过 `LlmProposer` 完成：每张表调用
  一次 `chat.complete`，产出 JSON 形式的描述与 suspect 标记。这与服务端 schema
  路由器/narrator 走的是同一个单次调用 seam（`chat.complete(system, user)` →
  `LangChainChatClient` 构建 `[("system", system), ("human", user)]`）。
- **(2) Phase A deep agent**：从 (question, gold SQL) 配对出发撰写语义层，并维护
  `clarifications.jsonl`。系统提示：`_PHASE_A_PROMPT`。
- **(3) Phase B deep agent**：把 SME 已回答的澄清折叠回 corpus，并附上经认证的
  溯源（certified provenance）。系统提示：`_PHASE_B_PROMPT`。

这两个 deep agent 都由 `deep_agent.build_curator_agent` 构建，它包装的是
`deepagents.create_deep_agent`，一套与服务端 `create_agent` 不同的 harness：它
加装了一个文件系统式的暂存区（scratchpad，`FilesystemBackend`），让 agent 能用
内置的 `ls` / `read_file` / `write_file` / `edit_file` / `grep` 工具读写
`/clarifications.jsonl`，与 curator 自身的接地工具（grounded tools）并用。

**旁白：Simulated SME 不在本文范围内。** 在 Phase A 与 Phase B 之间，一个只用于
评测的组件（`curator/sme.py`、`build_sme_brief`）扮演回答 `clarifications.jsonl`
的人类角色。它有自己的模型调用与系统提示，但它是三臂实验（three-arm experiment）
的测试 harness，并不属于生产策展流水线的一部分。如果需要它的提示形态，请直接
查看源文件。

## (1) 画像增强：`LlmProposer`

`llm_proposer.py` 中的 `LlmProposer` 是在一个基础的（启发式）proposer 之上做
组合：启发式部分以确定性方式决定角色/置信度/溯源，而每张表一次的模型调用则补上
散文式的描述与可靠性告诫。

**系统提示（逐字，`_SYSTEM_PROMPT`；源码中的双花括号是 Python `.format` 风格的
转义写法，实际提示词中用的是单花括号）：**

```text
You are a data curator authoring the semantic layer for a governed analytics system. Given one table's catalog Facts (physical names, types, sample values, inferred roles), write concise, accurate business descriptions and flag any column that looks unreliable or misleading for analysis.

Rules:
- Ground every description in the Facts shown. Do not invent columns, values, or relationships you cannot see.
- Flag a column as "suspect" ONLY when the Facts suggest it is unreliable, ambiguous, or misleading (e.g. a plausible-looking name whose samples contradict it). For a suspect column, write a short note starting with "DO NOT USE".
- Keep descriptions to one sentence.

Return ONLY a JSON object, no prose and no markdown fences, of the form:
{
  "table_description": "<one sentence>",
  "grain": "<what one row represents>",
  "columns": {
    "<physical_column_name>": {
      "description": "<one sentence>",
      "reliability": "ok" | "suspect",
      "note": "<DO NOT USE ... , only when suspect>"
    }
  }
}
```

**用户消息（由 `_render_table_facts` 拼装而成）：**

```text
Table physical name: [PHYSICAL_NAME]
Row count: [ROW_COUNT]
Columns:
  - [COLUMN] ([LOGICAL_TYPE], role=[ROLE]); samples: [SAMPLE_1], [SAMPLE_2], ...
  - ... (up to 5 sample values per column)
```

举例来说，真实的 `customers` 表会渲染成：

```text
  - ZipCode (integer, role=dimension); samples: 94256
```

响应会被解析为 JSON，并叠加在启发式的基础提案*之上*。这一步绝不会修改 Facts；
如果响应无法解析，基础提案会原样保留不变（fail-safe 设计：`LlmProposer._ask`
会吞掉任何异常并返回 `None`）。

## (2) Phase A deep agent

`deep_agent.build_curator_agent` 用 `system_prompt=_PHASE_A_PROMPT`，加上来自
`curator_tools(..., bag=bag)` 的工具集与 `FilesystemBackend` 的文件工具，构建出
这个 agent。`pipeline.build_curated_corpus` 会针对每个 schema、用完整一批 train
配对调用它一次。

**系统提示（逐字，`_PHASE_A_PROMPT`）：**

```text
You are the curator: you author the semantic layer (the Inference tier) for one database from a batch of (question, gold SQL) pairs, and you are your own adversary. Be proactive and curious. Your goal is not merely to cover the given pairs but to understand what this database IS and how it is meant to be used, and to leave a semantic layer where everything is connected. Actively explore tables and columns the pairs do not exercise.

Method:
1. Work through the pairs ONE AT A TIME. For each pair, understand the SQL against the live corpus, then update assets and the clarifications ledger.
2. Call read_corpus (optionally filtered by table/kind) to see Facts and your own Inference writes so far. Never contradict Facts.
3. REFUTE before you assert. Use run_probe_query (read-only SELECT) to falsify non-trivial claims AND to explore tables/columns the questions never touch. Keep only claims that survive.
4. Persist surviving claims via upsert_join, upsert_metric, upsert_term, upsert_few_shot, annotate_table, and annotate_column. If you can infer a meaning/role/join from the SQL, the joins, or the other pairs, that is enough — just write it down (no question needed). Prefer verifying seed candidates over inventing new ones. Columns in the catalog that never appear in working SQL are strong suspect candidates (annotate_column suspect=true). If a pair's question and gold SQL disagree (mislabeled/annotation error), do NOT upsert_few_shot from it — raise a clarification scoped pair:<id> noting the discrepancy instead.
5. RAISE a clarification (do not silently guess) when: a table or column is not touched by any question and you cannot infer its purpose; something looks missing or inconsistent; or a query's structure does not make sense to you and you cannot reconcile it. These are exactly what an SME should confirm. Maintain /clarifications.jsonl with the built-in file tools (ls/read_file/write_file/edit_file/grep). Paths are rooted at / (virtual filesystem). Each line is one JSON object:
   {"id":"q001","scope":"table:T.col","question":"...","status":"open","raised_by":["t14"],"answer":null,"answered_by":null}
   ALWAYS grep before adding. If a prior question covers the same scope, edit_file that record (same id) to broaden/merge rather than appending a duplicate. Do not use file tools for corpus assets — only /clarifications.jsonl.
6. Zero clarifications is acceptable if you genuinely resolved everything, but prefer curiosity: an unexamined table or an unexplained column is usually worth a question. Ground everything in Facts or a probe result; never invent columns or joins.
```

**用户任务消息（`pipeline.py`，由以下几部分以空行拼接而成）：**

```text
Curate schema `[SCHEMA]`. Work pair-by-pair; persist via tools.

[SEED_RENDER]

[TRAIN_BATCH]

Create /clarifications.jsonl for genuine unknowns (write_file on first create; grep before add; edit_file to broaden/merge).

Mark unreliable or misleading columns suspect. Propose at least the verified seed joins.

Stop once pairs are covered, seed joins verified, and obviously unreliable columns marked.
```

`[SEED_RENDER]` 是 `SeedBundle.render()`：由 `sqlglot` 从 train gold SQL 中确定性
抽取出的连接/指标候选，作为“verify, do not invent”（验证、而非凭空发明）的素材
提供给 agent：

```text
## Deterministic seed candidates (verify, do not invent)
### Joins
- [LEFT_TABLE] ⋈ [RIGHT_TABLE] ON [ON_CLAUSE]
(or "### Joins\n(none extracted)" when there are no candidates)
### Metrics
- [METRIC_NAME]: [EXPRESSION] on [BASE_TABLE]
(or "### Metrics\n(none extracted)" when there are no candidates)
```

`[TRAIN_BATCH]` 是 `_render_train_batch`：待策展的 (question, gold SQL,
evidence) 配对，上限 40 条：

```text
## Train (question, gold SQL, evidence) pairs — curate from these
1. id=[QID] Q: [QUESTION]
   evidence: [EVIDENCE]
   sql: [GOLD_SQL]
2. id=[QID] Q: [QUESTION]
   sql: [GOLD_SQL]
... (up to 40 pairs; "... (N more pairs omitted from prompt)" when there are more)
```

（只有当该条目带有 BIRD 的 evidence 文本时，才会出现 `evidence:` 这一行。）

### Phase A 工具循环

接地工具（`curator_tools`，引号内是文档字符串，即模型看到的每个工具描述），
外加限定作用于 `/clarifications.jsonl` 的内置文件工具：

- **`read_corpus(table="", kind="")`**：“Return the live corpus — Facts and
  Inference written so far. Optional table (physical name) and kind
  (table/join/metric/term/few_shot) filters bound context on wide schemas.”
- **`run_probe_query(sql)`**：“Run a read-only SELECT to confirm or falsify a claim
  about the data. Returns the rows (truncated) or an error string. Never mutates
  data.”
- **`upsert_join(left_table, right_table, on, ...)`**：“Record a validated
  JoinAsset between two physical tables.”
- **`upsert_metric(name, base_table, expression, ...)`**：“Record a validated
  MetricAsset (aggregate over a base table).”
- **`upsert_term(name, ...)`**：“Record a validated TermAsset mapping business
  language to an asset.”
- **`upsert_few_shot(question, sql, ...)`**：“Record a validated FewShotAsset
  (question + working SQL).”
- **`annotate_table(table, description="", ...)`**：“Set table-level Inference
  fields (description, confidence).”
- **`annotate_column(table, column, description="", role="", reliability="",
  suspect=False, note="", ...)`**：“Set column Inference: description, role,
  reliability, and/or suspect.”

**示意性对话记录：**

```text
assistant → read_corpus(table="[TABLE]")
tool     → [FACTS + INFERENCE WRITTEN SO FAR FOR TABLE]

assistant → run_probe_query(sql="[PROBE SELECT]")
tool     → [ROWS, truncated]  # or "error: [MESSAGE]"
            # ^ REFUTE before asserting: the model checks a claim before writing it

assistant → annotate_column(table="[T]", column="[C]", suspect=true, note="DO NOT USE ...")
tool     → ok: [ASSET_ID] updated

assistant → upsert_join(left_table="[L]", right_table="[R]", on="[ON_CLAUSE]")
tool     → ok: [ASSET_ID] created

assistant → grep("[SCOPE]", "/clarifications.jsonl")   # check for an existing record first
tool     → [MATCHING LINES, or none]
assistant → write_file("/clarifications.jsonl", ...)    # or edit_file to merge/broaden
tool     → ok
```

`/clarifications.jsonl` 的一行，形状与提示词中给出的完全一致：

```json
{"id":"q001","scope":"table:T.col","question":"...","status":"open","raised_by":["t14"],"answer":null,"answered_by":null}
```

## (3) Phase B deep agent

同样的 harness、同样的工具集（`curator_tools(..., certified_writes=True)`），
但系统提示与用户任务不同。在 Simulated SME（或真实 SME）回答完 Phase A 的账本
之后，`pipeline.build_curated_corpus_with_sme` 会针对每个 schema 调用它一次。

**系统提示（逐字，`_PHASE_B_PROMPT`）：**

```text
You are the curator in ingest mode. SMEs have answered clarifications.jsonl. Your job is to fold those answers into the Inference tier.

Method:
1. Read /clarifications.jsonl (file tools). For each answered record, use its scope field plus read_corpus to locate the target table/column/asset.
2. Apply knowledge via annotate_table / annotate_column / upsert_* tools. Stamp human-certified provenance by setting certified=true (and answered_by from the record) on those writes.
3. Do not invent new open questions. Prefer editing existing assets over duplicating them. Use run_probe_query only if an answer still needs a data check.
4. Focus on table:/column:/join:/metric: scoped answers. Answers scoped pair: or query: (data-quality or annotation-error findings) are recorded as governance rules automatically — you do not need to act on those.
5. Stop once every answered clarification has been reflected in the corpus.
```

**用户任务消息（逐字，`pipeline.py`）：**

```text
Ingest answered clarifications for schema `[SCHEMA]`. Read /clarifications.jsonl and fold each answered record into the corpus via annotate/upsert tools with certified=true.
```

### Phase B 工具循环

工具与 Phase A 相同，但现在每一次写入都带着经认证的溯源（`certified=true`、
`answered_by=[SME]`）：

```text
assistant → read_file("/clarifications.jsonl")
tool     → [ANSWERED RECORDS, one JSON object per line]

assistant → read_corpus(table="[TABLE_FROM_SCOPE]")
tool     → [FACTS + INFERENCE SO FAR]  # locate the asset the record's `scope` names

assistant → annotate_column(table="[T]", column="[C]", description="[ANSWER-DERIVED TEXT]", certified=true, answered_by="[SME]")
tool     → ok: [ASSET_ID] updated
```

作用范围（scope）标注为 `pair:` 或 `query:` 的回答（即 Phase A 第 5 步中提出的
数据质量 / 标注错误发现）不会经由 `annotate_*`/`upsert_*` 折叠进 corpus；它们会自动
落地为治理规则（`bag.record_caveats`），因此按照上面 Method 第 4 步的规定，
Phase B 自身的工具调用会跳过它们。

## 端到端流程

1. **画像（Profile）**（确定性，无模型）：`profile_database` 把实时 catalog
   读入 Facts 层。
2. **(1) 画像增强**，**每张表**一次 `LlmProposer` 调用：system +
   `_render_table_facts(table)` → JSON 形式的描述/suspect 载荷，叠加在启发式
   基础提案之上。
3. **播种（Seed）**（确定性，无模型）：`seed_from_train_sql` 通过 `sqlglot` 从
   train gold SQL 中抽取连接/指标候选。
4. **(2) Phase A deep agent**，针对整个 schema 运行一次 agent，系统提示为
   `_PHASE_A_PROMPT`，用户任务 = seed render + train batch；模型反复调用
   `read_corpus` / `run_probe_query` / `upsert_*` / `annotate_*` / 文件工具，
   边写入资产边更新 `/clarifications.jsonl`。
5. **校验 + 可选的修复轮**（确定性的 `validate_corpus`，只有存在发现
   （findings）时才会再多跑一次 agent 调用）→ 写出 **A2 corpus**。
6. *（旁白，本文范围之外）* Simulated SME（或真实 SME）回答
   `/clarifications.jsonl`。
7. **(3) Phase B deep agent**，运行一次 agent，系统提示为 `_PHASE_B_PROMPT`，
   用户任务 = 上文那条固定的 ingest 指令；把已回答的记录折叠进 corpus，标记
   `certified=true`。
8. 再次**校验** → 写出 **A3 corpus**。

**另见：** [Curator](curator.zh.md) 了解 proposer/adversary 设计与溯源生命周期；
[Pipeline design](pipeline-design.md) 了解 Phase A/B 如何契合三臂实验；
[Asset schemas](asset-schemas.zh.md) 了解 `upsert_*` / `annotate_*` 实际写入的
内容。
