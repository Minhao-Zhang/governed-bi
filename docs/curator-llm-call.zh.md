# Agentic BI Curator：LLM 调用全流程

本文逐次调用地追踪离线策展流水线（`curator/`）：哪一步发出哪条提示词、
用户消息在动态内容注入处长什么样，以及每个 deep agent 的工具循环呈现为
一份示意性的对话记录。它是 [Curator](curator.zh.md) 与
[Pipeline design](pipeline-design.md) 的补充——那两份文档描述的是周围的
设计。

**提示词文本本身不在这里重现。** `_PHASE_A_PROMPT` 与 `_PHASE_B_PROMPT`
（`curator/prompts.py`）是 `governed_bi.prompts` 里 `curator_phase_a` /
`curator_phase_b` 两条记录的再导出——`src/governed_bi/prompts/registry.py`
才是唯一真相源，所以在这里逐字引用，只要其中一个被编辑、或加了一个新
变体，就会立刻脱节。请直接查 registry，以及
[提示词变体实验](prompt-experiments.zh.md)了解一次运行怎么选定一个变体、
这两个阶段今天为什么只有 `v1`。

> 实现：[`src/governed_bi/curator/llm_proposer.py`](../src/governed_bi/curator/llm_proposer.py)、
> [`prompts.py`](../src/governed_bi/curator/prompts.py)、
> [`pipeline.py`](../src/governed_bi/curator/pipeline.py)、
> [`seed.py`](../src/governed_bi/curator/seed.py)、
> [`deep_agent.py`](../src/governed_bi/curator/deep_agent.py)、
> [`prompts/registry.py`](../src/governed_bi/prompts/registry.py)。

## 概览：生产流水线里两个由模型驱动的步骤

策展（curation）按 schema 逐个离线运行，包含两个由模型驱动的步骤，外加
围绕它们的确定性脚手架（画像、播种、校验）：

- **(1) Phase A deep agent**——registry 阶段 `curator_phase_a`。从
  (question, gold SQL) 配对出发撰写语义层，并维护 `clarifications.jsonl`。
- **(2) Phase B deep agent**——registry 阶段 `curator_phase_b`。把 SME
  已回答的澄清折叠回 corpus，并附上经认证的溯源（certified provenance）。

这两个 deep agent 都由 `deep_agent.build_curator_agent` 构建，它包装的是
`deepagents.create_deep_agent`，一套与 Analyst 的 `create_agent` 不同的
harness：它加装了一个文件系统式的暂存区（scratchpad，`FilesystemBackend`），
让 agent 能用内置的 `ls` / `read_file` / `write_file` / `edit_file` /
`grep` 工具读写 `/clarifications.jsonl`，与 curator 自身的接地工具
（grounded tools）并用。
`pipeline.build_curated_corpus` / `pipeline.build_curated_corpus_with_sme`
现在也都接收一个 `settings` 参数，并从它给每次构建发出的运行记录打戳，
而不再用一次重新调用的 `load_settings()` 去重新推导配置——为什么这对
提示词归因很要紧，见
[提示词变体实验](prompt-experiments.zh.md#为什么-curator-与-sme-的生产者不能再重新推导-settings)。

**`LlmProposer` 没有被接入这条流水线。** `curator/llm_proposer.py` 里的
`LlmProposer` 在一个基础的（启发式）proposer 之上做组合，通过每张表一次
模型调用来补充逐表描述与 `suspect` 可靠性标记，它是一个真实存在、有测试
覆盖的组件——但去 `curator/pipeline.py` 和 `curator/deep_agent.py` 里
搜一下，找不到任何实例化它的调用点。它只出现在 `curator/__init__.py`
的导出列表里，以及它自己的测试（`tests/test_llm_proposer.py`）里。
`pipeline.build_curated_corpus` 用另一种方式构建表描述：下文的 Phase A
deep agent 通过 `annotate_table`/`annotate_column` 来写，驱动它的是
(question, gold SQL) 配对与它自己的探索，而不是一遍逐表的 `LlmProposer`
扫描。在有什么东西在测试之外真正调用 `LlmProposer(...)` 之前，请把它
当成一个已实现但没接线的组件，而不是一个生产步骤。

**旁白：Simulated SME 不在本文范围内。** 在 Phase A 与 Phase B 之间，
一个只用于评测的组件（`curator/sme.py`、`build_sme_brief`）扮演回答
`clarifications.jsonl` 的人类角色。它有自己的模型调用与系统提示（registry
阶段 `sme_rules`——只是那一块固定的规则文本；brief 的其余部分是 BIRD 的
列描述与训练集 evidence，registry 不管这部分的版本）。它是评测阶梯实验
（eval-ladder experiment）的测试 harness，并不属于生产策展流水线的一部分。
如果需要它的提示形态，请直接查看源文件。

## (1) Phase A deep agent

`deep_agent.build_curator_agent` 用
`system_prompt=prompt_text("curator_phase_a", prompt_variants)`（默认
`v1`，也就是 `_PHASE_A_PROMPT`），加上来自 `curator_tools(..., bag=bag)`
的工具集与 `FilesystemBackend` 的文件工具，构建出这个 agent。
`pipeline.build_curated_corpus` 会针对每个 schema、用完整一批 train
配对调用它一次。

**系统提示：** `curator_phase_a` 这条 registry 记录。它把 curator 设定
为自己的对手——逐个处理 (question, gold SQL) 配对，调用 `read_corpus`
查看 Facts 与之前写下的 Inference，用 `run_probe_query` 在断言一个说法
之前先反驳它，通过 `upsert_*`/`annotate_*` 工具持久化经得住考验的说法，
并在一张表或一列的用途推断不出来时，提出一条 `clarifications.jsonl`
记录，而不是悄悄猜测。

**用户任务消息（`pipeline.py`，由以下几部分以空行拼接而成）：**

```text
Curate schema `[SCHEMA]`. Work pair-by-pair; persist via tools.

[SEED_RENDER]

[TRAIN_BATCH]

Create /clarifications.jsonl for genuine unknowns (write_file on first create; grep before add; edit_file to broaden/merge).

Mark unreliable or misleading columns suspect. Propose at least the verified seed joins.

Stop once pairs are covered, seed joins verified, and obviously unreliable columns marked.
```

`[SEED_RENDER]` 是 `SeedBundle.render()`：由 `sqlglot` 从 train gold SQL
中确定性抽取出的连接/指标候选，作为"verify, do not invent"（验证、而非
凭空发明）的素材提供给 agent：

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

## (2) Phase B deep agent

同样的 harness、同样的工具集（`curator_tools(..., certified_writes=True)`），
但系统提示与用户任务不同。在 Simulated SME（或真实 SME）回答完 Phase A
的账本之后，`pipeline.build_curated_corpus_with_sme` 会针对每个 schema
调用它一次。

**系统提示：** `curator_phase_b` 这条 registry 记录
（`system_prompt=prompt_text("curator_phase_b", prompt_variants)`，
默认 `v1`，也就是 `_PHASE_B_PROMPT`）。它把 agent 设定为 ingest 模式：
读取已回答的 `/clarifications.jsonl`，通过 `annotate_*`/`upsert_*`
应用每一条回答，附上 `certified=true` 与取自记录的 `answered_by`，并在
每一条已回答的澄清都反映进 corpus 之后停止。`pair:`/`query:` 范围的
回答（Phase A 中提出的数据质量或标注错误发现）不会用这种方式折叠进去——
它们会自动落地为治理规则。

**用户任务消息（逐字，`pipeline.py`）：**

```text
Ingest answered clarifications for schema `[SCHEMA]`. Read /clarifications.jsonl and fold each answered record into the corpus via annotate/upsert tools with certified=true.
```

### Phase B 工具循环

工具与 Phase A 相同，但现在每一次写入都带着经认证的溯源
（`certified=true`、`answered_by=[SME]`）：

```text
assistant → read_file("/clarifications.jsonl")
tool     → [ANSWERED RECORDS, one JSON object per line]

assistant → read_corpus(table="[TABLE_FROM_SCOPE]")
tool     → [FACTS + INFERENCE SO FAR]  # locate the asset the record's `scope` names

assistant → annotate_column(table="[T]", column="[C]", description="[ANSWER-DERIVED TEXT]", certified=true, answered_by="[SME]")
tool     → ok: [ASSET_ID] updated
```

作用范围（scope）标注为 `pair:` 或 `query:` 的回答（即 Phase A 第 5 步中
提出的数据质量 / 标注错误发现）不会经由 `annotate_*`/`upsert_*` 折叠进
corpus；它们会自动落地为治理规则（`bag.record_caveats`），因此按照其
系统提示方法部分的第 4 步，Phase B 自身的工具调用会跳过它们。

## 端到端流程

1. **画像（Profile）**（确定性，无模型）：`profile_database` 把实时
   catalog 读入 Facts 层。
2. **播种（Seed）**（确定性，无模型）：`seed_from_train_sql` 通过
   `sqlglot` 从 train gold SQL 中抽取连接/指标候选。
3. **(1) Phase A deep agent**，针对整个 schema 运行一次 agent，系统提示
   来自 `curator_phase_a` 这条 registry 记录，用户任务 = seed render +
   train batch；模型反复调用 `read_corpus` / `run_probe_query` /
   `upsert_*` / `annotate_*` / 文件工具，边写入资产边更新
   `/clarifications.jsonl`。
4. **校验 + 可选的修复轮**（确定性的 `validate_corpus`，只有存在发现
   （findings）时才会再多跑一次 agent 调用）→ 写出 **`curated`** corpus。
5. *（旁白，本文范围之外）* Simulated SME（或真实 SME）回答
   `/clarifications.jsonl`。
6. **(2) Phase B deep agent**，运行一次 agent，系统提示来自
   `curator_phase_b` 这条 registry 记录，用户任务 = 上文那条固定的
   ingest 指令；把已回答的记录折叠进 corpus，标记 `certified=true`。
7. 再次**校验** → 写出 **`curated_sme`** corpus。

**另见：** [Curator](curator.zh.md) 了解 proposer/adversary 设计与溯源
生命周期；[Pipeline design](pipeline-design.md) 了解 Phase A/B 如何契合
评测阶梯实验；[提示词变体实验](prompt-experiments.zh.md) 了解 registry、
变体选择与端到端归因；[Asset schemas](asset-schemas.zh.md) 了解
`upsert_*` / `annotate_*` 实际写入的内容。
