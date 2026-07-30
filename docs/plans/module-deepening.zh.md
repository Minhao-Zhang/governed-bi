# 模块加深计划

_[English](module-deepening.md) · [简体中文](module-deepening.zh.md)_

结构性重构计划，2026-07-29 开始。这是工作文档，不是权威设计：与[架构](../architecture.zh.md)或[设计决策](../design-decisions.zh.md)冲突时，以后者为准。这里没有任何一项改变系统的行为。每一项在意图上都是行为保持（behaviour-preserving）的，不是的那几项会自己说明。

本文关心的是**接口该放在哪里**。仓库自己的词汇里已经有「缝（seam）」这个说法（D7 的 RLS 缝、`Embedder` 缝、`Responder` 缝），所以沿用它，再补两个必要的词：

- **接口（interface）**：调用方为了正确使用一个模块所必须知道的全部内容。不只是函数签名，还包括不变量、顺序约束，以及哪些参数在对象生命周期内恒定、哪些每轮都会变。
- **深度（depth）**：调用方为学会一份接口所付出的单位成本，能换回多少行为。当一个模块的接口复杂到接近「自己写一遍」时，它就是浅的；下文失败的正是这项检验。

## 测到了什么

五个包承担了主要体量（`src/` 共 35,767 行，`tests/` 32,546 行）：

| 包 | 行数 | 备注 |
|---|---|---|
| `eval` | 14,960 | 仅 `run_datalake.py` 就有 5,371 行 |
| `analyst` | 5,699 | `agent.py` 1,381，`run_log.py` 1,065 |
| `curator` | 4,328 | `pipeline.py` 1,340，`asset_bag.py` 1,197 |
| `gateway` | 1,901 | `guardrails.py` 930 |
| `retrieval` | 1,528 | |

包级导入图是分层的，且只有一个环（`eval` ↔ `curator`，见 W6）。入度集中在该集中的地方：`corpus.schemas` 28、`corpus` 23、`config` 21、`gateway` 16。问题出在出度：`run_datalake` 导入 26 个内部模块，`run_experiment` 21 个，`analyst.agent` 20 个，`curator.pipeline` 17 个。这四个同时也是四个最大的文件，这种相关性不是偶然：知道所有事情的模块，正是没人能给它套上接口的模块。

越过接口直接伸手进去的次数：

| 接口 | 泄漏证据 |
|---|---|
| `Corpus` | 22 个模块里共 67 处 `.assets` 引用（loader 之外 63 处），归并为 15 类不同查询；每个问题约 140 次全列表扫描 |
| `build_serve_rails` | 17 个关键字参数，被 `answer_question_agent` 原样转发，在 5 个调用点重新摊开；其中 4 个既不干净地属于 stack 作用域也不属于单轮作用域，2 个是死参数 |
| `run_datalake` | 25 个下划线私有名被 23 个测试文件导入；全仓 23 处 `inspect.getsource` 断言 |
| `Gateway.execute` | 接受 `str`；8 个调用点，2 个走护栏，而这 2 道护栏并不是同一道闸 |
| `Settings` | 33 个自有字段横跨 6 类关注点，被 21 个模块读取；3 组已嵌套，3 组没有 |

## W1：加深 `Corpus`

**发现。**[`Corpus`](../../src/governed_bi/corpus/loader.py) 就是一个 `list[Asset]` 加上 `by_id`、`tables()`、`for_analyst()`。做删除检验：把它换成一个裸列表，几乎什么都不会丢，因为 loader 之外的 22 个模块本来就是那样用的。唯一称得上挣到自己位置的成员是 `for_analyst()`，它在一处强制执行 loader 契约，而这条规则恰恰是调用方到处手工重推的那条。

审计把全部 63 处外部伸手点按其执行的*查询*归类，15 类查询就覆盖了全部。最大的三类：「所有 `TableAsset`」（11 处，而 `tables()` 早就存在，只是它们不用），「schema X 下的表资产」（9 处，其中两处是逐字节相同的推导式），以及按资产类型的过滤（约 20 处内联 `isinstance`）。

**三份重复的查找并不是它看起来的那个缺陷，而且它藏着一个更糟的。** `analyst/tools.py:38`、`analyst/middleware.py:118` 以及 `analyst/agent.py:465` 处的内联副本，在排除（excluded）过滤上确实彼此不一致，但那处分歧**不可达**：每个 serve 入口交给 `build_serve_rails` 的都是 `for_analyst()` 视图（`api/stack.py:242`、`run_datalake.py:4511`），而该视图会直接删掉被排除的资产。所以那是三处重复，不是缺陷。

三者共有的东西才是缺陷：

> **有歧义的裸物理名，会解析到最先加载的那个 schema。**
>
> 在真实的池化 corpus 上测得（`BIRD-corpus` HEAD：69 个 schema、731 个表资产、6,877 个列）：**27 个裸物理名存在歧义，覆盖 731 个表资产中的 67 个，即 9.2%。** `pais` 出现在 5 个 schema 里，`kunden` 4 个，`clients` / `produits` / `client` / `usuarios` 各 3 个。
>
> 可达的失败路径不需要任何攻击者。一个问题被路由到 `sales`。agent 调用 `inspect_schema("tbl_sales_kunden")`，也就是 `search_corpus` 打印出来的那个 id，`render_columns` 回答 `physical: sales.kunden`（`tools.py:75`）。接着 agent 调用 `sample_rows(table_id="kunden")`，用的是它刚读到的裸名。`middleware.py:125-132` 按加载顺序扫描，返回 **`tbl_beer_factory_kunden`**，于是 agent 被告知 `"tbl_beer_factory_kunden: not licensed this turn — call inspect_schema first"`：一张它从未提到的表，位于它被路由到的范围之外的 schema，而这条消息把那个 schema 名泄露了出去。它白白消耗一步预算；如果 agent 照办，`inspect_schema("kunden")` 又会过不了范围检查，形成死循环，最终以步数上限拒答收场，而评测会把它记成**agent** 的失败，而不是解析器的失败。
>
> 而且答案是**依赖顺序的**：它会随 `_load_built_corpus`（`run_datalake.py:850-865`）里 `built` 的顺序翻转，而那正是该函数的 docstring 存在的目的所要消除的运行间不确定性。
>
> 正确的策略仓库里已经有了，就在其中一个违规处 60 行开外。`rvgd.py:530-538` 在多于一张表带有同一裸名时把它映射为 `None`，注释写着：*「而不是解析到恰好最后被加载的那张表。」*这三处查找做的恰恰是那条注释所禁止的事。

**第二个活着的缺口，且与规模无关。** 任何一处查找都不接受 schema 限定名。种子上下文块把表渲染为 `### {schema}.{physical_name}`，完全不含资产 id（`context.py:380-388`），`render_columns` 打印的是 `physical: {schema}.{physical_name}`。于是 `sample_rows("sales.kunden")` 在 id 与裸名两条路径上都落空，返回 `"not available"`，而这个字符串正是系统刚刚展示给模型的那一个。相关的不对称：`middleware.py:163-171` 会修复 *SQL 内部*大小写不对的标识符，但 `sample_rows("KUNDEN")` 得不到同样的宽容。

**成本，已测量。** 在池化 corpus 上，每个问题在 agent 内核开跑之前：**约 103 次对 731 个元素的完整扫描**（约 47 次为常数项，再加 3 倍召回表数与 4 倍已授权表数）。一轮典型对话再加约 35 次。合计**约 140 次扫描、每个问题约 10 万次资产访问**，全部是纯 Python 且受 GIL 限制，这直接给 `workers` 这个旋钮能买到的收益设了上限。

最严重的是 `licensed_physical_names`（`middleware.py:84`），它是唯一同时坐在「每问一次」路径（经 `agent.py:196` 走两遍）和「每次工具调用」路径（`middleware.py:374`，每次 `run_query` 尝试都重算一遍）上的按 id 逐个 `by_id` 的辅助函数：**约 140 次扫描里的 40 次、每个问题约 29,000 次资产访问，全部经由一个 8 行的函数**，而它重算的值只在 `inspect_schema` 授权了什么东西时才会变。同一趟里值得一起修的次要项：

- `governance.py:213`：L 次完整扫描，唯一目的是对它已经持有的 id 做一次类型断言。
- `tools.py:148` / `:397` / `:420`：`_excluded_identifier_tokens(list(corpus.assets))` 访问 731 个资产**外加全部 6,877 个列** = 7,608 个元素，无缓存，每次 `render_notes` / `read_notes` / `grep_notes` 都跑一遍，而它是 corpus 的纯函数。
- `rvgd.py:597`：在 `rvgd.py:488` 建好的本地 id→资产字典仍在作用域内时，仍调用 `corpus.by_id` T 次；`:485-487` 的注释恰好解释了那个字典为什么存在。
- `schema_router.py:78`：`_analyst_tables` 会跑 `for_analyst()`，也就是 pydantic 的 `model_copy(deep=True)`，每个候选 schema 一次（每问 3 次）。`RetrievalIndexCache.schema_docs` 就是为了消掉 router 路径上的这项开销而加的；picker 路径从来没拿到缓存。

**目标接口。** 一次构建，全部是不可变资产列表的纯函数。吸收的调用点数量来自审计：

| 方法 | 吸收调用点 |
|---|---|
| `tables()`（走索引） | 11 |
| `tables_in(schemas)` | 9 |
| `table_by_id(id)`（带类型的 `by_id`） | 10 |
| `table_by_physical(name, schema=None)`，**裸名有歧义时返回 `None`** | 3 |
| `physical_index()`（限定名 + 裸名，歧义 → `None`） | 4 |
| `joins()` / `metrics()` / `terms()` / `few_shots()` / `notes()` / `negatives()` | 约 20 |
| `joins_within(ids)` / `metrics_over(ids)` / `few_shots_in(schemas)` | 13 |
| `column_owner(column_id)` / `columns()` | 4 |
| `counts_by_type()` | 5 |
| `excluded_identifier_tokens()`（带缓存） | 3 |
| `schemas()` | 2 |

有四类查询仍然是带调用方谓词的完整扫描，访问器只应提供类型过滤：negative example 的 Jaccard 匹配（`governance.py:164`）、触发词对问题的子串匹配（`triggers.py:43`）、对 note 正文的任意正则（`tools.py:423`），以及 presenter 的列反向索引。

**风险，现在很具体。** `eval/run_datalake.py:864` 用 `corpus.assets.extend(...)` 构建池化 corpus，也就是在改动一个已经构造好的 `Corpus`。任何在构造时建立的索引，到该 corpus 被服务时都已经过期。所以这次加深必须与 `Corpus.concat(...)` / `merged_with(assets)` 构造器一起交付，并把那处改动迁移过去。`api/app.py:399` 是第二处。

**完成标准：** `corpus/` 之外没有任何模块为访问器已能提供的查询去扫 `.assets`；三处查找合成一处，且在歧义时返回 `None`、并接受限定名；`licensed_physical_names` 改为每次授权变化算一次而不是每次尝试算一次；两处原地改动都走构造器。

## W2：`ServeRuntime` / `TurnRequest`

**发现。** `build_serve_rails` 有 **17** 个纯关键字参数（不是本计划最初写的 18 个）；`answer_question_agent` 有 18 个参数，并把那 17 个全部转发。五个生产调用点把它们重新摊开：`api/app.py:508`、`api/graph_app.py:163`、`eval/arms.py:436`、`eval/oracle.py:342`、`eval/refuse_gate.py:71`。

这份接口背后的实现是深的：约 950 行，编译出一张五节点的图，所以它不是浅模块。它是一个深模块，却穿了一件几乎和它隐藏的东西一样复杂的接口，而参数表把两种生命周期混在一起，还不说明哪个是哪个。

**生命周期审计。** 11 个参数干净地属于 stack 作用域，2 个干净地属于单轮作用域，剩下那些才有意思：

| 参数 | 判定 |
|---|---|
| `corpus`、`settings`、`model`、`embedder`、`narrator`、`clarify_checkpointer`、`index_cache` | Stack。构建时被闭包捕获；换成每轮取值会让派生出的图、允许列表和 prompt 解析全部失效。 |
| `on_event`、`clarify_resume` | 单轮，且很硬。`graph_app` 在节点*内部*通过 `get_stream_writer()` 取 writer，构建期捕获会把第 N 轮的事件流进第 1 轮的 writer。 |
| `working_memory`、`clarify_thread` | 单轮，但当前在构建期被捕获。两条 API 路径都为每个请求新建一份 memory，所以 stack 作用域的图会永远端出第 1 轮的 memory。 |
| **`gateway`** | **两者皆是：真正的拦路石。** 构建时用来推导 `dialect`（`:386`），每轮又被 `build_agent_core` 闭包捕获。但两条 API 路径都为每个请求打开*并关闭*连接器，所以在那里它属于单轮作用域，在评测里则是每 worker 一份。 |
| **`identity`** | 今天属于 stack，语义上属于单轮。它恒定只是因为 `ServeStack.identity` 是一个开发用身份。把它放进 `ServeRuntime` 会**把单租户烙进类型里**。 |
| **`session_id`**、**`n_human`** | 两者皆是，且不一致。构建期的 `session_id` 变成 `FinalizeCtx.thread_id`，因而成为运行日志主键 `f"{thread_id}:{n_human}"`；每轮那个则落在 `base_provenance` 和 memory 键上。在 state 里传一个不同的 `session_id` 会改变溯源和 memory，但**不会**改变运行日志主键。`n_human` 被转成构建期种子（`_turn_n = [n_human - 1]`），之后靠推导。 |
| **`run_id`** | **死参数。** `ingest` 无条件覆盖它（`:518-525`）。任何由调用方提供的 `run_id` 都到不了经由图落盘的日志行。 |
| **`schema_vectors`** | Stack，而且**没有任何生产调用方传它**。每张图都在构建时重算一遍 `embed_schema_documents`。 |

这套分组早就存在，只是长在缝的错误一侧：[`ServeStack`](../../src/governed_bi/api/stack.py) 几乎持有全部 stack 作用域依赖，但它住在 `api/`，而 `analyst/` 正确地拒绝依赖它，于是五个调用方又把它拆回关键字参数，三个评测 driver 各自手搓了一份等价物。

**目标。**

```
ServeRuntime  # corpus, settings, identity*, model, embedder, narrator,
              # clarify_checkpointer, index_cache, dialect
              # + 现在在构建期派生的全部内容：default_schema, graph_obj,
              #   allowlist, corpus schemas, 已解析的 prompt, router_chat,
              #   schema_vectors（惰性）, 路由后 corpus 的备忘
TurnRequest   # question, session_id, n_human, gateway*, working_memory,
              # on_event, clarify_thread, clarify_resume
```

把 `run_id` 与 `schema_vectors` 从公开表面移除：一个被丢掉，一个没人传。把 `dialect` 提升为 `ServeRuntime` 的显式字段，这样单轮的 gateway 就不必在构建期存在。服务 schema 的一致性校验（`:409-415`）移到 `ServeRuntime.__post_init__`；它是 corpus + settings 的纯函数，而 `test_governance_ledger.py:113-132`（钉住它在任何模型运行之前就抛错）仍然通过。

**为什么把 `gateway` 放进 `TurnRequest` 很便宜。** `build_agent_core`（因而 `make_tools` 与 `GovernanceMiddleware`）**已经是每轮运行**（`agent.py:965-985`），所以它可以直接拿本轮的 gateway，无需重建。其余用户只有三处 `_finish_unsuccessful` 调用，全都在每轮节点内部。

**最大的一项收益。** `graph_app.py:156-190` 恢复一次澄清的做法，是在 `while True` 循环里再调一次 `answer_question_agent`：**为了改一个值（`clarify_resume`）而重建整张图**。拆分之后，它变成在同一张已编译图上再 `invoke` 一次。

**拆分必须做选择，不能回避。** 让 `session_id` / `n_human` 变成单轮作用域，意味着 `FinalizeCtx` 在 `ingest` 里按轮构造，而不是在编译期。这是一次行为变更：今天 `state["session_id"]` 无法影响运行日志主键。它同时也是删掉 `_turn_n` 计数器和 oracle 那个 `f"{session_id}:{n_built}"` 变通做法的前提。

**对形状的一条约束。** `oracle_solver` 确实需要为每个收窄后的 corpus 各有一份 runtime，所以 `ServeRuntime` 必须**构建便宜**。昂贵的派生状态（`index_cache`，理想情况还包括按文档的 embedding 备忘）必须是*注入进*runtime 的，而不是由它拥有，否则这次拆分只是把每 corpus 的重建成本从 `build_serve_rails` 搬进 `ServeRuntime.__init__`。

**测试收益，如实计量。** 有七处补丁指向这两个构建函数。四处（`test_eval_arms_meta.py:36,89`、`test_stage_metrics_seam.py:120`、`test_prompt_attribution.py:483`、`test_oracle_and_probes.py:352`）会变成普通注入，**但前提是 `agent_solver` / `oracle_solver` 接受一个 runtime 或一张已编译图**；只拆 `build_serve_rails` 对它们没有帮助。两处（`test_prompt_attribution.py:103`、`test_agent_governance_fixes.py:259`）**不会**被这次拆分修好：它们断言的是 `agent_core_node` 内部的每轮 prompt *组装*，需要的是另外抽出一个 `compose_agent_prompt(base, context_block, now)`。还有一处会被主动**作废**：`test_oracle_and_probes.py:444-490` 断言的是*构建参数*，即一张被逐出后重建的图绝不重用 `session_id`，而那恰恰是拆分要搬走的东西。它守着一次真实发生过的冲突，所以必须改写成「整轮运行中 `turn_id` 唯一」，而不是删掉。

只有**两处** `inspect.getsource` 断言指向这两个函数（`test_retrieval_index_cache.py:327` 与 `:524`），只要 runtime *拥有*那个缓存，两者都能转成行为断言；同一文件里已经存在等价的行为版本。另外二十处源码文本断言属于 W3 与 curator，这里不动。

还有一条约束：七个测试文件里的九个调用点把 `build_agent_core` 当作普通行为缝直接使用。它的签名必须继续接受 `corpus, gateway, identity, model`，并保持 `settings` / `dialect` / `default_schema` 显式，否则这些调用点都要跟着改。

**完成标准：** 五个调用点构造两个对象；`graph_app` 通过再次 invoke 而不是重建来恢复；那四处可转换的补丁变成注入。

## W3：拆解 `eval/run_datalake.py`

**发现。** 5,371 行，约 50 个模块级函数，一份接口：`run_datalake(**28 个关键字)` 加 `main(argv)`。它背后装着暂存与晋级的文件系统机制、gold 预检、池化题目选取、manifest 续跑、arm 汇总（一个函数 632 行）、阶梯差值、arm 对比、价格判定、serve worker 工厂，以及 CLI。

[Open work](../open-work.md) 已经记了它的体量和那 23 处 `getsource` 断言。它没有点出的是更锋利的证据：**23 个测试文件从这个模块导入了 25 个不同的下划线私有名**：`_summarise_rows` 出现在 23 个导入点，`_compare_arms` 17 个，还有 `_stage_roots`、`_promote_build`、`_relocate_sidecars`、`_assert_build_coverage`、`_quarantine_curator_failures`、`_assert_gold_is_trustworthy`、`_check_resume_manifest`、`ladder_deltas`、`price_verdict`。一个被 23 个文件导入的私有名，就是一份贴错标签的公开接口。那些 `getsource` 测试不是测试风格问题；它们是你需要的接口不存在之后剩下的东西。

**先例就在这个文件里。** `run_build_phase`（第 412 行）正是为此被抽出来的，它的 docstring 把理由讲得比本计划更好：作为 `run_datalake` 内部的闭包，它「could only be tested by driving the whole harness — Postgres, gold, serve loop and all. So it never was.」这个动作已被验证过，只是需要再做四次。

**这一刀比预想的更干净。** 对全部 60 个模块级定义和 13 个常量做完普查后发现，**这些簇本来就是连续的行区间**，任何地方都没有交错。落出七个模块，本计划提的四个是对的；同一趟里还能再拿三个。

| 模块 | 行数 | 区间 | monkeypatch 代价 |
|---|---|---|---|
| `eval/summarise.py` | 935 | 1277-1316、2001-2895 | 无 |
| `eval/compare.py` | 676 | 1323-1998 | 无 |
| `eval/build_staging.py` | 544 | 227-770 | 1 处 |
| `eval/run_artifacts.py` | 407 | 868-1274 | 无 |
| `eval/build_corpora.py` | 258 | 2898-3155 | 1 处 |
| `eval/preflight.py` | 233 | 793-847、3158-3335 | 3 处 |
| `eval/serve_plan.py` | 159 | 3338-3496 | 4 处 |

`run_artifacts.py` 是本计划漏掉的那个，也是七个里最强的一个：运行目录 I/O 与续跑契约（`_RowSink`、`_read_rows`、`_stage_event_rows`、`_build_manifest`、`_check_resume_manifest`、`_RESUME_KNOBS`），一条内部边，零跨模块依赖，零补丁代价，并且有八个测试文件只碰它、不碰 driver 里的其他任何东西。就从这里开始。

**它是个 DAG。** 由对每个函数体内每个名字读取做 AST 遍历验证。拓扑序：`summarise, build_staging, preflight, run_artifacts, serve_plan` → `compare, build_corpora` → driver。`src/` 里根本没有任何模块导入 `run_datalake`，所以也不存在包级环。

有两条边值得知道。`compare → summarise` 确实存在，本计划没有预料到，但它只是一个 10 行函数（`_twin_stamps_complete`），方向是对的，而 `:1745` 的 docstring 是有意把 `_compare_arms` 的分层闸与 `_summarise_rows` 的绑在一起的。接受这条边。而唯一*会*出现环的地方（`build_staging ⇄ build_corpora`）**早已被打断**，靠的是 `run_build_phase` 上的 `build_one_db: Callable` 参数。任何地方都不需要新增注入。

**每一条顺序不变量都能在纯搬移下存活。** 从注释与 docstring 里清点出十六条，包括本计划标出的两条（gold 预检必须在构建阶段之前；replicate 必须最后追加进 `serve_order`）和另外十四条：残留物先修复再删除、每次尝试开始时清空暂存根、`Executor.map` 在 `with` 内部消费完、`stage_events.jsonl` 在 arm 循环之外只清一次、计数一律直接数而绝不靠相减。**没有一条跨越模块边界。** 它们要么在函数内部，要么在落进同一模块的两个函数之间，要么写在 `run_datalake` 自己的函数体里，而那部分不动。这才是让纯切分值得一试的原因。

**对本计划的两处归位修正。**

- `_assert_build_coverage` 与 `_quarantine_curator_failures` 是*闸*，与 `_assert_gold_is_trustworthy` 同类，不是暂存机制。要么把四个都放进一个 `gates.py`，要么把这两个留在 `build_staging.py`，但不要把它们彼此分开：`test_eval_curator_quarantine.py:26-29` 在一条语句里同时导入它们的三个常量。
- `_routing_escaped`、`_schema_of_assets` 和 `_fmt_rate` **不是**汇总逻辑。三者都不被 `_summarise_rows` 调用；前两个是每问一次的行标注，唯一调用方是 `_run_pool_arm`，而 `_fmt_rate` 的 14 处使用里有 12 处在 driver 的 stdout 块里。把它们放进 `summarise.py` 无害，但标签是错的。

**让这件事变便宜的迁移手法。** 在 `run_datalake.py` 里保留显式再导出。这样全部 23 个耦合测试文件**原样可用**：普通导入、`rd.X` 属性访问，以及跟随 `__code__.co_filename` 的 `inspect.getsource(rd.X)`。这把必须改的测试压缩到**四处 monkeypatch**，它们全都会响亮地失败而不是静默失效，而且只有两处落在本计划提出的四个模块里。之后再把导入迁过去，作为一次独立的、无行为变化的提交。

**六道便宜的缝，每道退役一个 `getsource` 测试。** 它们与模块搬移相互独立，每道 4 到 35 行。`build_serve_order(arms, oracles, replicate)` 是这个文件里单位行数价值最高的一道缝，一次退役两处源码断言。接着是把 `arm_corpus` 加宽到 `ArmServingPlan` 上、`collect_pool_curator_errors`、把 `LADDER_DELTA_METRICS` 提成模块常量、`stamp_serve_position`、`plan_db_builds`。只有那两个 gold 顺序测试需要更侵入的东西：给 `run_datalake` 加一个 `phase_hook` 回调，让一次打了桩、无模型的运行能断言自己的阶段序列。

**`_summarise_rows` 可以拆，但要放到第二次提交。** 它是 142 行推导，后面跟着单个 458 行、87 个键的 `return {…}`。它能拆成 5 到 6 个函数，而之所以能拆，是因为共享状态是*派生的，不是累积的*：每个子集都是 `rows` 的纯过滤，只有一个自成一体的累积循环。第一步是强制性的：做一个 `_populations(rows) -> Populations` 的 NamedTuple，把那十一个共享子集命名出来，因为 `:2354`（「Literally the population above. Recomputing it with its own filter is what let the two drift apart」）和 `:2399` 的注释警告的正是草率拆分会招来的那个错误。

有两项成本要在有人动手之前先记下来。**`summary.json` 的键顺序会变**：各主题是交错的而非成块的，所以 `{**grading, **routing, …}` 式的合并会重排已提交的产物。消费方全都按键读取，所以大概是安全的，但这必须是一个明说的决定，而不是副作用。还有 **`test_eval_metrics.py:806` 会静静地变空洞**：它从 `getsource(_summarise_rows)` 里正则出 `r.get("…")`，并逐一核对已声明的 `ROW_FIELDS`。模块搬移能保住它；一次拆分会把这些读取移出那份源码，而测试仍然通过，只是检查的东西少了。对动态键辅助函数，它其实已经有这个洞。

别动 `_bucket`。它重复了 `_group_by` 加一次 EX 计算，但它按字符串排序键，所以替换它会改变 `by_difficulty` 的键顺序，那是一次伪装成清理的行为变更。

**这和合并两个 driver 不是一回事。** 把 `run_experiment.py` 并进 `run_datalake.py` 已按决定推迟，并继续推迟。拆解与它无关，也更便宜，而且方向是有用的：[open work](../open-work.md) 把「合并」排在「让 driver 可被驱动」之前，但先抽出这些模块，能让那五个卡住合并的测试文件在合并真正发生时有地方可指。

**推荐顺序。** `run_artifacts`（热身，验证再导出手法）→ `summarise` → `compare`（就这个顺序，因为那一条边）→ `build_staging`（1 处补丁）→ `preflight`（3 处补丁）→ 可选的 `build_corpora` 与 `serve_plan` → 然后那六道缝 → 最后单独做 `_summarise_rows` 的拆分。

**完成标准：** driver 剩约 2,100 行，其中 `_run_pool_arm`、`run_datalake` 和 `main` 占 83%，也就是真正的 serve 循环、编排与 CLI；并且每个迁移过去的测试都断言行为而不是源码文本。

**顺手发现：** `_FROZEN_GOLD_RE` 被逐字定义了三次（`run_datalake.py:196`、`analysis.py:50`、`sql_diff.py:195`），而 `leakage.py:87` 记录了它们必须一致。这次抽取是把它们收拢到 `sql_diff.is_frozen_gold` 的自然时机。

## W4：把护栏的证明变成一个类型

**发现。**[`Gateway.execute(sql: str, identity)`](../../src/governed_bi/gateway/gateway.py) 接受任意字符串。那份接口里没有任何东西要求这段 SQL 已经过了 [`guardrails.check()`](../../src/governed_bi/gateway/guardrails.py)，而后者是同级模块里的一个自由函数。存在八个调用点。两个走护栏（`analyst/middleware.py:460`、`analyst/governance.py:720`）；六个不走（`curator/deep_agent.py:118`、`curator/sme.py:355`、`eval/ex.py:29`、`eval/hash_grade.py:351` 与 `:442`、`eval/run_experiment.py:557`），而且它们是正当的：curator 探测与 gold 执行。接口无法把这些和一次错误区分开，而第九个调用方可以免费拿到未审查的执行。

代码本身已经在为这个缺失的类型讲道理。`analyst/governance.py:97-114` 论证一个语义层的 `failed_layer` 是「a **proof**, minted by `check()` itself, that L1/L2/L3 passed」。这个证明存在于论证里，不存在于程序里。文档把这称为「governance = topology-not-trust」，那是对现状的诚实描述，而不是为它辩护。

**审计确认的部分。** 拓扑在它声称成立的地方确实成立。`Connector.execute` 在整个仓库里只有一个调用方（`gateway.py:61`），而 `run_query` / `sample_rows` 的工具体在中间件没有拦截时会无条件抛错（`tools.py:353-365`）。把治理中间件摘掉，系统是失败即拒而不是敞开。`wrap_tool_call` 的每个出口都被逐一列举，没有一个能在绕过 `middleware.py:376` 的 `check()` 之后到达 `execute`。没有任何未受治理的工具能执行 SQL。`verdict` 为 `cap` 或 `error` 时不带 `layer` 键，所以一次从未取得裁决的尝试无法进入分级交付。这些都是真的，也是这套设计正在起作用的部分。

**它另外发现的东西在下面的框里。** 重构的论点不变；这项发现是独立的，而且更大。

> ### 分级交付的复检不是同一道闸
>
> `governance.py:696` 在 `:720` 的第二次 `execute` 之前重跑 `check()`，并传入 **`allowed_tables=None`**，这会完全跳过 L4（term-semantics）。`:708` 还额外让 L5 `cost_estimate` 复检*失败*直接落到执行。在一个双 schema 池化允许列表上直接运行 `check()` 验证如下：
>
> ```
> licensed this turn = {'sales.orders'}
> "SELECT hr.salaries.base_pay FROM hr.salaries"
>   original check  -> BLOCKED (term_semantics, "table outside the retrieved scope")
>   graded re-check -> PASSED
> "SELECT COUNT(*) FROM pg_catalog.pg_authid"        # 根本不在 corpus 里
>   original check  -> BLOCKED (term_semantics)
>   graded re-check -> PASSED    (没有 Column 节点，L3 无从拒绝)
> ```
>
> 触发条件是普通的，不是精心构造的：问一个答案需要本轮未被路由到的 schema 的问题，并让它成为本轮最后一次 `run_query`。`extract_final_sql` 返回 `None`，`agent.py:1142-1149` 把那条 block 记录选作 `last`，`failed_layer == "term_semantics"` 使它可交付，复检通过，然后 `governance.py:737` 在一个 `(unverified)` 前缀之后把**真实的行**讲给用户。
>
> 范围要说公道：`grade_semantic_failures` 默认为 `False`，[架构](../architecture.zh.md)§1 也正确地说分级交付不是 serve 默认。但它在 `governed_bi.local.toml` 里是 `true`，在评测 driver 里是开着的。在单 schema 的 BIRD corpus 上，波及面很小。在 69 个 schema 的池化数据湖上，这是一次对未被路由、未被授权数据的跨 schema 读取，唯一的边界是全 corpus 的列允许列表，而那正是 D15 与 `_in_licensable_scope` 存在去强制的边界。
>
> `governance.py:677` 的注释诚实地说明 L4 就是分级交付所要宽恕的那一层，所以跳过 L4 是一次有意的权衡。有两件事不在那次设计的覆盖范围内：周边行文读起来像是复检与原检等价，以及 `:708` 的 L5 穿透宽恕了设计并未声称要宽恕的一层。已记入 [open work](../open-work.md)。

**目标接口。**

```
check(...) -> GuardrailVerdict        # 不变
LicensedSql                           # sql + verdict + 它被铸造时所处的作用域；
                                      # 只能由 check() 构造
Gateway.execute(licensed: LicensedSql, identity)
Gateway.execute_unchecked(sql, identity, *, exempt_reason: str)
```

令牌上的作用域不是装饰。如果由 `check()` 铸造令牌，而它两个生产调用方之一传的是 `allowed_tables=None`，那么一个不透明的 `LicensedSql` 会让分级交付的令牌与中间件的令牌在 `Gateway.execute` 处无法区分：类型系统会断言一个并不存在的等价证明，而上面那项发现会变得*更难*被看见，而不是更容易。让令牌带上 `allowed_tables` 这个 frozenset，或至少带一个「L4 已跳过」的标记，好让 `execute` 能拒绝、或对无作用域令牌单独审计。

**原本要先解决的那件事，现在有答案了。** `middleware.py:331` 的标识符规范化发生在检查*之前*，而 `:376` 的 `check()` 与 `:460` 的 `execute()` 之间没有对 `sql` 的任何赋值。这个顺序是对的。问题在下一层：`_force_row_limit`（`gateway/connectors/base.py:30`）跑在 `Connector.execute` *内部*，位于 `Gateway.execute` 之下，而且它是一次完整的 sqlglot 重解析加重序列化，不是字符串拼接：

```
'SELECT "CustomerID" FROM "demo"."customers"'
  -> 'SELECT "CustomerID" FROM "demo"."customers" LIMIT 1001'
```

所以令牌只能声称「这棵*树*通过了」，永远不能声称「这些*字节*跑过了」。更糟的是，`_force_row_limit` 使用硬编码的方言（`"sqlite"` / `"postgres"`），而 `check()` 收到的是 `gateway.catalog().dialect.value`。在 Redshift 上，被检查的语法是 `redshift`，重序列化的语法是 `postgres`，因为 `redshift.py` 原样继承了 `PostgresConnector.execute`。这是潜伏的，因为 Redshift 没有线上验证过，但它恰恰是令牌本该让其不可能发生的那类偏离。修法：把 LIMIT 注入提到检查之上，或者让 `check()` 返回已解析的 AST，由连接器从它序列化，而不是重新解析。

**代价。** 八个生产调用点加定义本身。两个拿到真令牌（`middleware.py:376` 与 `governance.py:696` 是仓库里唯二的生产 `check()` 调用方）。四个改用带原因的 `execute_unchecked`（`curator_probe`、`sme_probe`、`gold_reexecution`、`harness_smoke`）。其中两个，`eval/hash_grade.py:351` 与 `eval/ex.py:29` 的预测那一半，**不**该被豁免：把令牌穿过 solver 的返回值，能让「评分器只重跑已授权 SQL」这条性质由结构保证，而不是碰巧成立。今天它是碰巧成立的：`extract_final_sql` 只从 ledger 记录取 SQL，所以被评分的 SQL 一直是过了检查的，但没有任何东西强制这一点；而 `run_datalake.py:3715` 用的是一个**未固定 schema** 的 gateway，所以同一个字符串是在比它被检查时更宽的 `search_path` 下重跑的。测试侧代价：8 个文件里 19 个假 gateway 替身。

**该记录而不是该修的一项。** curator 的探测路径（`deep_agent.py:118`、`sme.py:355`）在 `all_access` 身份下把 LLM 写的 SQL 交给 `execute`，不做检查，这是站得住的：curator 正是构建允许列表的那一方，所以它没有可对照的东西。但 L2 策略黑名单（`pg_read_file`、`query_to_xml`、`dblink`）同样没有保护它，而[架构](../architecture.zh.md)§1 的「只执行通过护栏的 SQL」并没有为它留出例外。要么对 curator 探测跑 L1/L2（很便宜：它们不需要允许列表），要么把这个例外写明。

**完成标准：** serve 路径无法在没有带作用域令牌的情况下抵达数据平面；每条豁免路径都在审计日志里写明自己的原因；被检查的树就是被执行的树。

## W5：给 `Settings` 分组

**发现。**[`Settings`](../../src/governed_bi/config.py) 有 33 个自有字段，并嵌套了 `ModelConfig`（13 个）、`DataSourceConfig`（7 个）和 `NoteGovernance`（5 个，是参数对象而不是字段）。范式已经立起来了，然后只在六组里用了三组。

一次逐字段的用法审计纠正了本计划最初的两个说法，而两处纠正都重要：

- **`serve_config_hash` 并不哈希那三个评测 worker 旋钮。** 它精确地哈希 13 样东西（`provenance.py:84-102`），三个 `eval_*` 字段不在其中。任何地方都没有反射式读取。改一个评测并发旋钮不会移动任何服务摘要，也不会把任何运行标记为不可比较：worker 数以 `serve_workers` / `build_workers` 进入 manifest，它们位于 `MANIFEST_OPERATIONAL` 之下，标题写着「Recorded, deliberately NOT gate keys」（`eval/metrics.py:181-188`），而 `COMPARABILITY_KEYS` 只从 `MANIFEST_KNOBS` 派生。
- **`auto_accept_corpus` 是唯一「被哈希但什么都不门控」的字段，而且完全没有死字段。** 其余每个被哈希的字段都有一个活的、非记录用途的读取方。这次审计确认了 [open work](../open-work.md) 里已有的条目，没有增加任何东西。值得记下的是 `auto_accept_corpus` 从 *serve* 路径也可达，不只是评测：`finalize_and_log` 在 `analyst/run_log.py:888` 调用 `serve_config_hash`，所以不能在不改变已记录摘要的前提下，简单地把它从 analyst 拿到的对象上摘掉。

**留下来的部分，形式更锋利。** `for_env`（`config.py:362-380`）对那三个 `eval_*` 字段没有任何关键字；只有 `load_settings` 会设置它们。两个 driver 都通过 `for_env` 重建 `Settings`（`run_datalake.py:4153`、`run_experiment.py:568`），所以在一次评测运行期间，serve 循环与中间件实际持有的 `Settings` 报告的是 `eval_workers=1, eval_serve_workers=None, eval_build_workers=None`，**与真实并发无关**；真实并发是作为 CLI 解析出的函数参数另行送达的（`run_datalake.py:5181-5189`）。

`NoteGovernance.from_settings` 当初为 `[notes]` 表修的就是同一种漂移（`config.py:176-181`），今天它只是潜伏着，因为 serve 路径没有读这些字段。所以这处错位是双份的：对象错了，而且在它声称描述的那些运行期间，那个对象上的值也是错的。

**目标。**

| 分组 | 字段 | 能否从 analyst 的对象上摘掉？ |
|---|---|---|
| `EvalConcurrency` | `eval_workers`、`eval_serve_workers`、`eval_build_workers` + `serve_worker_count()` / `build_worker_count()` | **能**，serve 行为零变化：3 个调用点，全在评测 CLI 入口 |
| `RunLogConfig` | 9 个 checkpointer / 运行日志 / full-content 字段 | 不能：`FinalizeCtx.settings` 从 serve 路径触达全部九个 |
| `SchemaRoutingConfig` | `schema_route_top_k`、`schema_route_llm_pick`、`schema_pick_max_columns` | 不能：三个都门控 serve 路由 |

审计浮出的两条约束：

- **嵌套 `SchemaRoutingConfig` 会改变每一个服务摘要**，除非哈希载荷保留扁平键名。`provenance.serve_config_hash` 今天读的是扁平的那三个。
- **`RunLogConfig` 必须对 curator 可达，不能只对 serve 可达。** `curator/sme.py:507` 读 `log_full_content` 来决定是否持久化 SME 答案的逐字文本。

还有第四组，原计划里没有：`can_stream`、`allow_edit`、`serve_api_key_env`、`cors_origins`、`corpus_root` 和 `single_all_access_identity` **只**在 `api/` 内被读取，`analyst/`、`retrieval/`、`gateway/` 里都没有。一个 `HttpServeConfig` 同样可以从 analyst 的对象上摘掉，但它是对 `api/stack.py` 的真实重构而不是机械搬移，而且 `single_all_access_identity` 是一道值得保持可见的安全闸。暂不纳入范围。

`grade_semantic_failures` 不属于任何一组。让它保持扁平，或者未来与 `hard_block_suspect_columns` 一起折进一个 `ServePolicy`。`environment` 不能被嵌套：它是 `for_env` 用来分支的判别式。

**完成标准：** `EvalConcurrency` 离开 analyst 的对象；`for_env` 能表达剩下的一切；serve 路径真正的 `Settings` 依赖能从类型上读出来。

## W6：`TrainPair`，以及 curator 声明的词汇

**先更正。** 本计划开头把 `eval` ↔ `curator` 称为仓库唯一的包环。在运行期它不是环：两条 curator→eval 的边都只在 `TYPE_CHECKING` 下（`curator/pipeline.py:49-52`、`curator/sme.py:20-22`），而 `eval/harness.py:211` 那条 eval→curator 的边是 `_sme_fold_signal` 内部的函数局部导入。这个环只存在于声明层面：读者和 mypy 看得见，导入期不成立。修它买到的是清晰，不是正确性。工作量按此界定。

**发现。** `curator/pipeline.py` 与 `curator/sme.py` 把训练输入的类型写成 `Sequence[EvalItem]`，而这个类型由 `eval/dataset.py` 拥有。字段级用法：

| `EvalItem` 字段 | curator | eval |
|---|---|---|
| `question` | Phase A prompt、SME brief | 到处都用 |
| `sql` | Phase A prompt、`seed_from_train_sql` | 评分、泄漏检查 |
| `evidence` | Phase A prompt **以及 SME brief 的领域提示** | 没有任何地方读它 |
| `question_id` | Phase A prompt 里的配对标签（有位置兜底） | 到处作为行键 |
| `difficulty` | 不用 | 两处分层读取 |
| `answerable_by_template` | 不用 | **`src/` 里没有任何地方用** |

**`answerable_by_template` 是死字段。** 它点名的 `TemplateSqlGenerator` 在 `src/` 里已不存在。ADR 0002 记录了它的删除。`eval/bird_loader.py` 从不设置它，所以在每次真实的 BIRD 运行里，它对每个条目都是默认的 `False`。删掉它的代价是 `tests/test_eval.py:148` 里的一行（在一个 `@requires_live_serve` 测试内部）、`eval/dataset.py` 里两个关键字参数，以及一段 docstring。

**`TrainPair` 需要四个字段，不是两个。** `question`、`sql`、`evidence`、`question_id`。`evidence` 的承重方式是原计划漏掉的：`curator/sme.py:252-266` 用它构建 SME brief 的领域提示段落，去重且刻意**不设上限**，注释写着丢掉任何一条都会「starves the SME of exactly what it needs to answer」。把 `TrainPair` 收窄到两个字段，会无声地掏空 `curated_sme` 这一档。

**只要引用 SME 档的提升幅度，就值得同时说明的一个事实。** BIRD 的 `evidence` 是人类撰写的提示文本，与 gold SQL *并排*写成，目的就是让每个问题可解。它逐字、完整地进入模拟 SME 的系统 brief（`curator/sme.py:263-266`），也进入 curator 的 Phase A prompt（`curator/pipeline.py:84-85`）。这只涉及训练切分，而且 `assert_brief_no_leakage`（`curator/sme.py:276-295`）强制其中不含 gold SQL、不含测试问题文本，所以它**不是**测试集污染。它是一条通道：为了让 gold 可推导而写下的知识，变成了 SME 的领域专长。这一点应当紧挨 [open work](../open-work.md) 的 X7，即「`curated_sme` 的差值永远无法归因于澄清协议」那一条。它是把 X7 讲得更锋利，而不是新增一条：被混淆的第二个机制不只是描述 CSV，还包括这些逐题提示。

**目标。** 由 curator 侧拥有的 `TrainPair(question, sql, evidence, question_id=None)`。两个 driver 里八处转换点，三处签名变更（`pipeline.py:837`、`:1066`、`sme.py:74`），删掉两处 `TYPE_CHECKING` 导入，以及大约 14 处测试构造需要更新。

另一条边独立而且更小：`_corpora_differ`（`curator/pipeline.py:761`）是一个纯文件系统函数：它对 `sorted((root/schema).rglob("*.yaml"))` 做 sha256 并比较两个摘要，不触碰任何 curator 状态。把它移到中立的位置（`eval/atomic.py` 已经拥有文件系统原语），让两个调用方都从那里导入。把「只看 `*.yaml`」那条范围注释一起带过去。

## W7：`Provenance` 是一份只有一个恒定写入方的已声明接口

**发现。** `corpus/schemas.py` 声明了 `ProvenanceSource`（`curator` / `gold` / `human`）与 `ProvenanceStatus`（`proposed` / `draft` / `certified`）。curator 实际写入的只有一个值。

每一次非 certified 的写入都经由 `AssetBag._audit()`（asset_bag.py:1181）进入 `_inference_audit(model=self.model_name)`，其默认值是 `source=curator, status=proposed`。而 `AssetBag.from_tables` 接受一个 `model_name`，却**没有任何生产调用点传它**：`curator/pipeline.py:234`、`:876`、`:1210` 三处全都省略，所以在每个生成 corpus 的每个资产上，`Provenance.model` 都是 `None`。同时 `ProvenanceStatus.draft`（注释为「adversary passed it」）在 `src/` 里**没有任何写入方**；只有测试和 `corpus/beer_factory/notes/` 下手写的示例 corpus 用到它。

于是一个生成 corpus 在实践中只有两种溯源状态：所有东西上的 `curator/proposed`，以及模拟 SME 回答过的东西上的 `human/certified`，而后者这一档 [open work](../open-work.md) 已经标注为由模型铸造。中间那一档是空的，而本该说出「谁写了这个资产」的 model 字段永远是 null。

**四个消费方因此退化。**

1. `analyst/note_inject.py:33-35` 按状态给 note 排序，权重是 `certified=0, draft=1, proposed=2`。在生成 corpus 上这个排序是惰性的。
2. `retrieval/triggers.py:50` 用 `publication_status == certified` 门控 PIN，而 `governed_bi.toml` 里 `pin_require_certified = true`。唯一能产出「已 certified 且带触发词」的 note 的地方是 `AssetBag.record_caveats`（asset_bag.py:1035-1047），它折入 SME 的澄清答案。**所以 PIN 通道只在 `curated_sme` 这一档可达。** 跑 `--pin-triggers`，`baseline` / `seeded` / `curated` 测到零 PIN 事件是由构造决定的，不是由结果决定的。这与 `NoteGovernance` 的 docstring（config.py:176-181）当初要堵住的失败形状相同，只是往里深了一个字段。
3. `viz/presenter.py` 在七个视图模型上把 `provenance_status` 呈现给审计 UI，人类审计者在那里看到的全是 `proposed`，无法据此分流。
4. 评测阶梯没法查询它。`tests/test_curator_seed_joins.py:270` 去读 `inspect.getsource(build_curated_corpus)` 来回答「机械路径会不会写 few-shot」，而这本该由资产自己的溯源直接回答。又一个因为字段不携带信息而存在的 `getsource` 测试。

命名其实已经知道这份记录丢掉的那个区分。`AssetBag` 保留着 `propose_join` / `propose_metric` / `propose_term` / `propose_few_shot`，作为对 `upsert_*` 方法的 `*args, **kwargs` 转发（asset_bag.py:810-820），标注为向后兼容。但这个区分是活的、有意义的：确定性种子调用 `propose_*`（`pipeline.py:108`、`:114`、`:204`），而 agent 的工具调用 `upsert_*`（`deep_agent.py:135-185`）。两个名字对应两个溯源档次，而别名在它的第一行就把这个差别扔掉了。

**目标。** 让写入方说明自己在写哪一档，并让记录承载它：

- `AssetBag` 在写入处接受溯源来源，而不是靠一个向后兼容别名：确定性种子的写入落成一个独立的 source（或至少一个独立的 status），agent 的写入落成 `curator/proposed`。
- 在三处 `from_tables` 调用点传 `model_name`，否则就删掉这个参数。一个没有调用方传的参数不是缝。
- 要么在对抗器通过某个资产时写入 `draft`，要么删掉这个取值以及读它的那两张排序表。

**这一项刻意不是行为保持的。** 它改变落进 corpus 的内容，所以在写代码之前需要一个决定：项目希望能区分这些档次中的哪些，以及 PIN 只覆盖 `curated_sme` 是不是本意。那个问题属于[设计决策](../design-decisions.zh.md)，不属于这里。相关：[open work](../open-work.md) 的 corpus 覆盖条目说 `activation` 的 `on_match` 从未被产出。`record_caveats:1039` 在 `derive_keyword_triggers` 返回任何东西时确实会产出它，所以那条对这个产出方来说已经过期，而真正待答的问题是：在真实的 SME 答案上，触发词推导到底会不会返回非空。

## 顺手发现、无需任何重构即可修的缺陷

这些出自各项审计，不依赖任何一条工作线落地。按后果排序。前三项影响已记录的数据。

| # | 缺陷 | 修法 |
|---|---|---|
| D1 | **`eval/oracle.py:342` 把从答案键派生出的轮次写进持久运行日志**，戳的是 `producer=serve, serve_path=agent`，溯源里任何地方都没有 `oracle_rung`，与真实 serve 轮次的唯一区别是一条 `thread_id` 前缀约定。该模块自己的 docstring（`oracle.py:55-58`）说这些永远不能作为系统性能上报。在 `oracle_tables` 规模下，那是每题每档一行。 | `dataclasses.replace(settings, run_log_kind="off")`，照 `arms.py:430-434` 的做法。一行。 |
| D2 | **`eval/refuse_gate.py:71` 把整个 N 题运行压成持久日志里的一行。** 它为每题新建一张图并让 `n_human` 取默认值，于是每次 `turn_id` 都是 `f"{session_id}:1"`，而 `append_run_record` 按 `turn_id` 做 UPSERT。它是唯一既没拿到 `_turn_n` 修复（`test_eval_run_log_turns.py:60` 为 `arms` 钉住了这一点）、也没拿到 AUDIT R6 索引缓存修复的 serve 调用点，所以它还会每题重嵌整个 corpus。 | 传 `n_human=i+1`，复用一张图，设 `run_log_kind="off"`。 |
| D3 | **`events.final` 在 `narrate` 运行之前就追加了持久记录**（`agent.py:1265-1268`），所以只要没传 narrator，落盘的行就缺少 `narrate` 阶段。重新追加只发生在 narrator 跑过的那条路径上，而那正是评测 driver 从不走的路径。 | 改到叙述之后再追加，或者无条件记录该阶段。 |
| D4 | **`schema_vectors` 没有任何地方在传**，所以在多 schema corpus 上，每一次线上轮次都会重嵌每一个 schema 文档，因为 API 路径是每轮重建图的。`index_cache` 覆盖不到它：`schema_router.py:224-231` 在缓存分支*之前*就对 `schema_vectors` 短路了，所以 stack 的缓存根本看不到这次调用。 | 让 runtime 惰性拥有它（W2），或者现在就把 `stack.schema_vectors` 穿过去。 |
| D5 | **`oracle_tables` 每题都把整份收窄后的 corpus 重嵌一遍**，而原因不是缺 `index_cache`：缓存键是排序后的资产 id 元组，而 gold 表集合每题不同，所以每次查找都必然 miss。`restrict_corpus`（`oracle.py:264`）还会把**每一个** term、note 和 negative example 资产整体保留，而这三类的文档都非空。 | 按文档做 embedding 备忘。按 corpus 做键的缓存修不了这一档。 |
| D6 | **`licensed_physical_names`（`middleware.py:84`）在每次 `run_query` 尝试时都重算一遍**，此外每题还要算两次：每题约 29,000 次资产访问，全经由一个 8 行函数，而它算的值只在 `inspect_schema` 授权了什么东西时才变。 | 按已授权 id 集合做备忘。 |
| D7 | **`_excluded_identifier_tokens`（`tools.py:148`、`:397`、`:420`）无缓存**，每次调用访问 731 个资产加全部 6,877 个列，每次 `render_notes` / `read_notes` / `grep_notes` 都跑一遍，而它是 corpus 的纯函数。 | 缓存在 corpus 上（W1 的 `excluded_identifier_tokens()`）。 |
| D8 | **`api/app.py` 省略了 `clarify_checkpointer`**，于是 `enable_clarify` 为 False，`ask_user` **完全没有被绑定**。REST `/chat` 的 agent 与流式路径拥有不同的工具集，而溯源里没有任何地方记录澄清不可用。 | 决定 REST 是否应支持澄清；无论哪种，都把该能力记录下来。 |
| D9 | **`run_id` 是一个被代码丢掉的参数**（`ingest` 在 `agent.py:518-525` 无条件覆盖它）。 | 删掉它，或者不再覆盖。 |
| D10 | **`run_datalake.py:4636-4668` 编译了一张池化路径永不使用的串行 solver 图**，为此每档多付一次完整的 schema 文档嵌入。 | 改成惰性构建。 |
| D11 | **`_force_row_limit` 在硬编码方言下重序列化**（`"sqlite"` / `"postgres"`），而 `check()` 是在 `gateway.catalog().dialect.value` 下解析的。在 Redshift 上被检查的语法是 `redshift`，被执行的是 `postgres`，因为 `redshift.py` 继承了 `PostgresConnector.execute`。潜伏项：Redshift 没有线上验证。 | 由 W4 的「被检查的树就是被执行的树」覆盖。 |
| D12 | **`rvgd.py:597` 调用 `corpus.by_id` T 次**，而 `rvgd.py:488` 建好的本地 id→资产字典就在作用域内，`:485-487` 的注释恰好解释了那个字典为什么存在。 | 用那个本地字典。 |

D1 与 D2 应当最先做：它们都是一行改动，而且都在污染项目用于审计所依赖的记录，不只是费时间。

## 顺序

**在一切之前：D1 与 D2。** 两个一行改动，止住对已记录产物的污染。它们不花成本，也不阻塞任何东西。

然后，按收益对风险排序：

1. **W1 Corpus**：修一个已测量的活缺陷（裸名歧义，占池化表资产的 9.2%），消除一处依赖顺序的不确定性，删掉三份重复查找，并在 22 个模块上回本。`Corpus.concat` 构造器必须在同一次改动里交付，否则索引在池化路径上会过期。
2. **W3 run_datalake**，从 `run_artifacts.py` 开始：零补丁代价，验证再导出手法，而且每个被抽出去的测试都是纯收益。与被推迟的 driver 合并无关，也与 W1 无关。
3. **W2 ServeRuntime**：收拢五个调用点，并把 `graph_app` 的「重建以恢复」循环变成再一次 `invoke`。先把 `session_id` / `n_human` 的生命周期问题定下来；那是一次涉及已记录主键的行为变更。
4. **W4 LicensedSql**：改动最小，治理收益最大，而带作用域的令牌正是让分级交付的不对称保持可见、而不是被埋起来的那个东西。
5. **W5 Settings** 与 **W6 TrainPair**：机械活；顺手在改邻居时一起做。注意除非哈希载荷保留扁平键名，嵌套路由旋钮会移动每一个服务摘要。
6. **W7 Provenance**：在本文里排最后，在重要性上可能排第一。它需要先有设计决策才能写代码，而它是唯一带有活的测量后果的一项。

W1 与 W3 不重叠，先后皆可。W2 会碰 `analyst/` 和评测 driver 的调用点，所以它希望落在 W3 抽取之后，而不是与之同时进行。

## 不在范围内

- 合并两个评测 driver。已按决定推迟；见 [open work](../open-work.md)。
- 删测试。在那些 `getsource` 测试所替代的缝真正存在之前，它们是承重的。
- 任何触碰 Redshift 的事。
- 任何新行为。如果某条工作线发现自己需要一个新行为，就停下来，把它变成一个设计问题。

## 哪些缝是真的，哪些是假想的

写下来，好让没人把一次推迟当成一个已被验证的抽象。只有当某样东西真的会在缝两侧变化时，这道缝才配得上这个名字。

*真的*（两个或更多适配器）：`Connector`（sqlite / postgres / redshift）、`ChatClient` 与 `Embedder`（线上用 LangChain，离线用 `StaticChatClient` + `HashingEmbedder`；正是这对离线实现让 CI 具备确定性）、`Responder`（`SimulatedSme` / `StaticResponder`）、`AnswerNarrator`。

*假想的*（一个适配器或没有）：`WorkingMemory`，其 Episodic 与 Correction 按 D8 推迟；`NoteActivation.on_match`，其 PIN 检索模式没有数据在验证；`edit_mode="pr"`。三者都是有记录的推迟而非疏忽，而 `WorkingMemory` 只花三个方法，所以都留着。它们只是不构成任何证据。

## 这些不要重构

它们是其余部分该向其收敛的样板。

- **`guardrails.check()`**：一个函数背后是五层、约 900 行 AST 工作，返回单一裁决类型，对自身异常失败即拒，还有一个被文档明确写成「无法影响裁决」的 `on_layer` 观察者。观察而无权限，是最值得抄的那个细节。
- **`viz/presenter.py`**：纯 `Corpus -> View` 函数，无副作用，`api/app.py` 是它之上一层薄薄的 HTTP 适配器。复杂度在 presenter 里，协议关切在 app 里。
- **`eval/metrics.py`**：把指标登记册做成一份被强制执行的契约，并有测试断言没有任何东西能未经声明就进入产物。正因为它，W3 才是机械活而不是危险活。
- **`stages.py`**：serve 与评测共享的一套结局词汇。正是这个模块，让「一次崩溃被算成了拒答」这件事变得可发现。
