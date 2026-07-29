# 0002: 服务运行时改为受治理的 agentic 内核

_[English](0002-governed-agentic-serve-runtime.md) · [简体中文](0002-governed-agentic-serve-runtime.zh.md)_

- **状态：** Accepted / Implemented（已接受并实现）。2026-07-13 的设计评审上被反复拷问并据此修订；切换于 2026-07-14 落地 `main`（提交 `d2fdd6a`）。
- **决策者：** 项目负责人 + 设计会议
- **相关文档：** [0001](0001-langgraph-server-chat-runtime.zh.md)、pipeline-design.md（§8 不变量；该文档已删除，见 git 历史）、[design-decisions.zh.md](../design-decisions.zh.md)（D2、D5、D11、D15）
- **取代：** pipeline-design §8 的那条不变量——*「服务侧保持确定性 DAG；LLM 只作为有界的节点操作出现，绝不作为自主循环」*——以及 §5 的说法*「LLM = 节点分类器，绝不是 ReAct」*。
- **已验证的技术栈：** `langchain 1.3.12`、`langgraph 1.2.8`、`deepagents 0.6.12`——`create_agent` + `AgentMiddleware`（`wrap_tool_call` / `wrap_model_call`）与 `FakeListChatModel` 在锁定的环境里都能正常导入。
- **机制已由一次 spike 验证（2026-07-13）。** 一次端到端 spike 在已安装的技术栈上证明了那个承重机制：`wrap_tool_call` 能读 `request.state`，并通过 `Command(update=...)` 写自定义 state 通道；一个受治理工具可以扩充按轮计的 `licensed` 通道，供之后某次 `run_query` 的护栏读取（不变量 #4）；而每一次 `run_query`——无论放行还是拒绝——都从同一个拦截点写下一条 ledger 记录（不变量 #10）。**同时发现一处约束：** 工具调用必须**串行**（自定义 state 的更新在两次调用之间提交）；同一个模型轮次里的并行工具调用会让 `run_query` 抢在 `inspect_schema` 完成授权之前跑掉。详见构建指南。

## 背景

服务运行时既没用足 LangGraph，推理又是「确定性但看不见东西」。

- **部署出去的图只有一个节点。** `langgraph.json` → `graph_app.py:make_graph` → `build_chat_graph` 就是 `START → answer → END`，其中 `answer` 调用 250 行的巨石函数 `flow.py::answer_question`。LangGraph 在这里只承担线程持久化与自定义事件流——**没有**节点级编排、没有按节点的可观测性、没有按节点重试，也没有 human-in-the-loop。
- **真正的 DAG 存在，但没人用，而且已经腐化。** `analyst/graph.py::build_serve_graph` 是一个 9 节点的 `StateGraph`，可服务路径里没有任何东西导入它，它也已经和 `flow.py` 漂移开了（没有 `graded_delivery`；`narrator` 落后）。我们在维护两份实现，部署的却是其中较差那份的较笨封装。
- **推理是确定性的，但是笨的。** 意图路由靠关键词匹配；schema 选择只看检索分数；**SQL 生成是盲的单次生成**——模型从不查看真实的表结构，这正是 `RA` 未加引号那次执行失败的直接原因（「它根本看不到表结构」）；修复则是手写的 `while attempts < 3` 循环。
- **既有的不变量恰好禁止了这个修法。** pipeline-design §8 宣布服务侧绝不能跑自主循环——而这恰恰就是让生成器*先看再跳*所需要的东西。

本次会议作出的决定：把运行时做成真正有智能的（一个**完整的 agentic 内核**），并推翻「绝不自主循环」这条不变量——**但前提是治理由构造保证**，而不是靠约定俗成。这是设计意图，拓扑结构落实了它的大部分——但不是全部：语义缓存命中与分级交付都在 `wrap_tool_call` 之外执行（各自写自己的 ledger 记录），分级交付用 `allowed_tables=None` 重新检查、因此跳过了 L4，持久运行日志的写入也是尽力而为的。这些是约定，本文如实记录，而不是把它们说没了。

## 决策

采用**受治理的 agentic 内核**：外层是一个**确定性的 `StateGraph`**（很薄的治理护轨），包裹内层一个 **`create_agent` 推理循环**；循环的治理由 **`AgentMiddleware`** 强制执行，而它对数据的每一次触碰都是一个**只读、带护栏的工具**。

组织原则：

> **治理是一道强制的拦截层，不是 agent 的自由裁量。**
> agent 可以自由推理，但每一次工具调用都要穿过中间件，由它执行护栏*并且*记录审计；而答案由 agent 无法影响的确定性代码盖章。自主权只授予*怎么找到答案*——绝不授予*什么可以执行*、*什么被信任*、*什么可以不被记录*。

### 我们推翻的那条不变量，以及为什么现在它是安全的

旧不变量把**自主**和**不受治理**混为一谈。这两件事是可以分开的。一个 agent 如果（a）只能通过受治理的工具行动，并且（b）其输出由它控制不到的确定性代码盖章，那它在**推理**上是自主的，在**权限**上并不自主。于是我们把

> ~~服务侧保持确定性 DAG；LLM 只作为有界的节点操作，绝不是自主循环。~~

换成

> **服务侧的*权限*是确定性的；它的*推理*可以是 agentic 的。**

### 2026-07-13 设计评审上锁定的决策

| # | 问题 | 决策 |
|---|---|---|
| Q1 | 这个内核在 LangGraph 里怎么搭？ | **`create_agent` + `AgentMiddleware`**，外面套一层很薄的 `StateGraph`。治理 = `wrap_tool_call`（护栏 + 审计）与 `wrap_model_call`（串行化工具调用 + token 采集）——*不是*手工接线的节点，*也不是*一个不透明的 `create_react_agent`。此处曾把「按身份限定工具范围」写成已构建，但其实并没有——`wrap_model_call` 从未引用过 `identity`。 |
| Q2 | 探索会扩大执行权限吗？ | **受治理边界内的动态授权。** 探索类工具尊重 `governance.excluded`（被排除的资产永远不会浮现）；经由受治理工具浮现出来的表会被加进按轮计的 `licensed` 集合，`run_query` 的护栏把它当作 L4 的 `allowed_tables` 来读；L3 仍然逐列把关。这里接受了一次策略转移：L4 的底线从*「检索召回 + FK 拓扑」*挪到了*「curator 的 `excluded` 标记 + L3 逐列」*。 |
| Q3 | 审计要多持久？ | **（a）先落在 `Answer` 的 provenance 上**；同时把一个持久化 sink **（c）** 设计成接口，由同一个咽喉点供给；以后再迁移到持久的 **（b）/（c）**。 |
| Q4 | 保留两条生成路径吗？ | **不——一套 agentic 架构，并且必须有 key。** `TemplateSqlGenerator` 作为服务路径被移除；CI / 离线的确定性改由一个 `FakeListChatModel` 的 agent harness 承担。 |
| Q5 | 什么数据可以到 LLM？ | **公开数据——全部发送，现阶段不设出口限制。** 数据隐私 / 出口治理是未来一条独立分支；把工具边界的形状留好，以后能插进一个出口开关。 |
| Q6 | agent 的边界？ | `recursion_limit ≈ 15` 个超级步；**`run_query` 尝试次数上限 = 3**，在 `wrap_tool_call` 里强制；耗尽 → §6 的分级交付 / 拒答；单一模型档位（`settings.models.llm_model`）。 |

### 架构：护轨上的 agent

**外层 `StateGraph`（确定性——就是护轨）：**

```
START → ingest → refuse_gate ──（命中负例）──────────────► REFUSE   （硬拒）
                     │
                     ▼
              prepare → cache ──（命中，重新过护栏）──────► narrate ─► END
                     │
                     ▼
               ┌───────────────────────────────────────┐
               │      agent_core = create_agent(...)     │  ← 智能所在
               │  由 AgentMiddleware 治理：                │
               │   • wrap_tool_call  → 每次调用先规范化 + 护栏 + 审计
               │   • wrap_model_call → 串行化工具调用 + token 采集
               └───────────────────────────────────────┘
                     │ （sql, rows）        │ 预算耗尽 / 主动放弃
                     ▼                       ▼
                 finalize            graded_delivery | refuse  （确定性，§6）
              （确定性的双轴盖章            │
                + 写缓存）                  ▼
                     │                     END
                     ▼
                 narrate  （LLM 重述已交付的答案；对拒答 / 没有 narrator 时是
                     │      no-op；narrator 失败则保留确定性文案）
                     ▼
                    END
```

**受治理的工具（只能是只读）：**

| 工具 | 做什么 | 治理 |
|---|---|---|
| `search_corpus(query)` | 检索表 / 术语 / join / 指标 / few-shot | 只读；尊重 `excluded`；每条命中都会**扩充本轮的 `licensed` 集合** |
| `inspect_schema(table_id)` | 已授权表的列、类型、样例值 | 只读；尊重 `excluded`——**修掉了「模型从来看不到表结构」** |
| `sample_rows(table_id, n)` | 行预览 | 只读，**以调用者身份运行**（RLS） |
| `run_query(sql)` | **通往数据的唯一路径** | `wrap_tool_call`：规范化（`sqlglot identify=True`）→ 在当前 `licensed` 集合上跑 `check()` L1–L5 → 只读连接器；失败作为 `ToolMessage` 返回；尝试上限 = 3；L2 直接硬停 |

agent 从不直接调用 `gateway.execute`，也从不自己设置可靠性印章。它只负责推理；治理由中间件和护轨完成。

### 由构造保证的治理不变量（安全主脊）

1. **refuse-gate 在 agent 之前跑**（D5）——命中负例的问题根本到不了 agent。
2. **每个数据工具都是只读且限定范围的**——L3 列白名单 / L4 授权在 `wrap_tool_call` 里强制；被 `excluded` 的资产永不浮现。
3. **`run_query` 在中间件里被规范化、过护栏、设上限**——agent 无法执行未受治理的 SQL；L2 策略拦截是硬停，绝不喂回去教它绕（对齐 `_NON_REPAIRABLE_LAYERS`）。
4. **授权来自受治理的探索，而不是 agent 的自我声明**——`allowed_tables` = 本轮*经由受治理工具*浮现出来的表，再做 FK 扩展。召回变成 agentic 的（修掉 RA 的召回不足），同时不给一个失控 agent 自我授权 `excluded` 表的机会。*一旦越出确定性的「检索 + FK」基线，`semantic_assurance` 就降级*（推荐的默认行为），这样「agent 跑偏了」在印章上是看得见的。
5. **可靠性印章是确定性的**——`safety_clearance` / `semantic_assurance` 由 `finalize` 根据实际发生的事情算出来，**绝不是自报的**。agent 无法宣称自己 `unflagged`。
6. **`safety_clearance` 保持二元的硬判定**——只有 `semantic_assurance` 是分级的（§6 的「先交付再分级」不变）。
7. **有界**——`recursion_limit ≈ 15` 加 `run_query` 尝试上限 3；耗尽 → 分级交付或拒答。
8. **泄漏边界不变**——gold SQL / 答案永远不进入服务侧。
9. **生产服务的是一个被钉住、经过评审的语料库版本**——不变（§1）。
10. **强制执行与审计共用同一个拦截点**——那个做护栏的 `wrap_tool_call` 中间件同时写治理记录。每一轮累积一份**只追加的治理 ledger**（一个带 `operator.add` reducer 的 state 通道）：每个受治理动作一条记录（refuse-gate 结果；提供了哪些工具；每次探索浮现出的资产、被 `excluded` 过滤掉的资产与授权增量；每次 `run_query` 规范化后的 SQL + 逐层 L1–L5 判定 + `allowed_tables` + 结果元信息；印章的推导过程）。你不可能在没有记录的情况下执行（或拒绝）——「治理一路贯穿到底」由构造成立。现在它落在 `Answer` 的 provenance 上（Q3-a）；持久化 sink 是留给以后的接口。

### 一套架构，必须有 key（Q4）

**只有一套服务架构**——agentic 内核。理由：这次返工本来就是为了杀掉两份实现的漂移（`flow.py` 巨石 vs. 腐化的 `graph.py`）；再留一条并行的确定性路径，等于把那个问题重新打一遍。

- **治理不会漂移**，因为它是单一共享模块（`check` / `column_allowlist` / `_licensed_table_ids` / refuse-gate / `_finalize_success`），由中间件和任意节点共同调用——不是两条互相承诺会一致的路径。
- **CI / 离线的确定性**来自 **`FakeListChatModel` agent harness**（`test_curator_deep_agent.py:111` 已经是这个套路）——比被删掉的模板引擎更有代表性，因为它走的是真实的 agent 路径。
- **等价性测试换了契约**：从*「同一个 `Answer`」*（对一个非确定性 agent 来说不可能）变成**「同一套治理不变量」**——两条路径拒同样的负例、拦同样的 L2 SQL、盖出同样的 `safety_clearance`。
- **`TemplateSqlGenerator` 作为服务路径被移除**；没有 key 时 `stack.py` 大声失败（不做静默的离线降级）；`flow.py` 里的 `or TemplateSqlGenerator` 兜底被删；narrator / embedder 变成始终存在。`eval/dataset.py` 里那个模板子集 helper 可以留作测试工具。

### LangGraph / LangChain 原语替掉了什么

| 现在（手工写的） | 受治理的 agentic 内核 |
|---|---|
| `while attempts < 3` 修复循环（`flow.py:640`） | agent 自身的工具反思循环；`run_query` 上限放在 `wrap_tool_call` |
| `_emit` / `on_event` 回调垫片（`flow.py:247`） | 原生 `stream_mode` + 每个工具的 LangSmith trace |
| 单次生成的 `SqlGenerator` 协议 | `create_agent` 配受治理工具 |
| 每个阶段临时的 `try/except` | 按节点的 `RetryPolicy` + 把 LLM 可恢复的工具错误当作 `ToolMessage` |
| 治理散落在 `flow.py` 各处 | `AgentMiddleware`（`wrap_tool_call` 护栏 + 审计，`wrap_model_call` 限定范围） |
| 没有澄清机制（模型自己猜） | `interrupt()` + checkpointer（与持久化一起推迟） |
| 两份分叉的实现 + 模板路径 | 一套架构；CI 里靠假模型保持确定性 |

## 后果

**正面**
- 修掉了根因：模型在产出之前先查看结构（不会再有盲的 `RA`）；agentic 召回修掉了召回不足；按失败类型自我纠正取代了基于文本 diff 的重试。
- 在 Studio / LangSmith 里有完整的按节点**以及按工具**的可观测性。
- 治理 ledger（#10）让 agent 路径上的「受治理、可审计」成为字面意义上的事实。
- 只有一份实现；顺手删掉了腐化的重复 DAG *和*模板路径。

**负面 / 代价**
- **成本 + 延迟：** 每个问题多次 LLM 调用，而不是 1–3 次。用缓存、`recursion_limit`，以及（以后）一个更便宜的工具选择模型来缓解。
- **现在必须有 key**——没有离线 / 无 LLM 的服务模式。
- **服务是非确定性的**——CI 的确定性依赖假模型 harness。
- **治理面变大**——靠不变量 1–10（拦截 + 确定性盖章）来守，而不是靠禁止自主。L4 的底线现在压在 curator 的 `excluded` 标记上（Q2）。
- **部署更重**——*等到*持久审计 / HITL 落地时（Postgres checkpointer / 审计 sink，承接 0001 的部署注记）；目前推迟。

## 考虑过的备选方案

- **保留单节点封装（现状）：** 否决——没有可观测性 / 重试 / HITL，盲生成也依然存在。
- **手工接线的 `StateGraph` 工具循环（不用 `create_agent` / 中间件）：** 一度是 Q1 的默认选项；在验证了 `AgentMiddleware` 能在工具边界上强制护栏之后被否决——中间件是框架层面强制的，所以把循环拆成节点属于不必要的定制接线。
- **只把有界工具循环用在*生成*环节：** 更保险的中间方案。否决——召回和修复才是失败真正发生的地方。**保留为兜底方案**，万一成本 / 延迟无法接受。
- **并行保留确定性模板路径（Q4）：** 否决——那是同一个漂移陷阱；它唯一的非 CI 用途（无 key 演示）根本答不了目标问题。

## 迁移（分阶段，每阶段可独立发布）

- **Phase 0 —— 治理内核 + CI harness（准备，不改行为）。** 把共享治理模块收成单一来源；搭起 `FakeListChatModel` agent 测试 harness；补上治理不变量的等价性测试。
- **Phase 1 —— 外层护轨 + 开关后面的 `agent_core`。** 很薄的外层 `StateGraph`（`ingest` / `refuse_gate` / `prepare` / `cache` → `agent_core` → `finalize` / 分级交付）；`create_agent` + 中间件 + 受治理工具；治理 ledger 落在 `Answer` 上。在 BIRD 上做 A/B。
- **Phase 2 —— 切换。** 让 agent 内核成为唯一的服务路径；强制要求 key；删掉 `TemplateSqlGenerator`（服务侧）、`flow.py` 巨石，以及腐化的 `analyst/graph.py`。
- **Phase 3 —— 推迟的分支。** HITL（`interrupt()` + 持久 checkpointer）、持久审计 sink（Q3-b/c）、数据隐私 / 出口治理（Q5）。

## 待定问题（各自推到自己的分支）

- HITL 的范围，以及背后的持久 checkpointer（Postgres）。
- 持久审计 sink 的形状（Q3-c）与保留期。
- 数据隐私 / 出口治理（Q5）。
- 对着 eval 调 `recursion_limit` / 模型档位；以及一个非确定性 agent 与 eval / eval 阶梯之间的相互作用（种子、temperature 0）。
- 既然确定性 DAG 是被退役而不是被部署，迁移的排序细节要重新看。

## 修正 1（2026-07-13）：agent 必须拿到语义层

**状态：** Proposed（提议中）——阻塞 P2。**触发原因：** 第一次线上服务路径 A/B（固定语料库、`cs_semester`、N=15）显示 agent 内核相对确定性 flow **退化了**——curated / curated_sme 的 flow EX 是 0.667，agent 只有 0.267，而且 curated == curated_sme（经由 agent，策展没带来任何东西）。

> 注：上面这些数字产出于 2026-07-26 之前，按[实验操作手册](../plans/experiment-runbook.md)已全部作废，不可引用。它们保留在这里，是因为它们是当初触发本修正的那个观察。

**根因。** P1 的工具只暴露了*名字*：`search_corpus` → 资产 id + 分数，`inspect_schema` → 列。它们**完全没有**暴露策展语义层里那些高价值内容——**few-shot 范例（Q→gold-SQL）、join 的 `ON` 子句、指标表达式、术语映射、规则 / 注意事项**——而这些正是 flow 经由 `assemble_context` → `PromptContext.render()` 注入的。于是 agent 是在裸 schema 上做 NL→SQL（≈ 无语义层的基线），策展所丰富的一切（从 gold SQL 提取的 join、few-shot、规则——也就是 curated→curated_sme 的差值）全都不可见。这个缺口的源头就在本 ADR 自己的工具表，以及构建指南把 `search_corpus` 画成返回 id 而不是内容。

**决策——先播种再精修（不是只靠工具）。** 在 `agent_core` 之前跑一个确定性的 **`assemble` 节点**，复用 flow 的前半段原样（`route_intent` → `retrieve` / `route_schemas` → `detect_missing_join_path` → `_licensed_table_ids` → `assemble_context`）。然后：

1. **给 prompt 播种。** 把 `PromptContext.render()`（表、join、术语、指标、few-shot、注意事项、skill——与 flow 喂给它自己生成器的完全一致）作为一个 `## Governed context` 块注入 agent 的 system prompt。
2. **给范围播种。** 预先把基线表 id（检索到的 + FK 邻域 + Steiner 点）填进 `licensed` state 通道，这样常见问题可以直接进 `run_query`，不必先绕 `inspect_schema`。
3. **工具从此是精修，不是发现。** `inspect_schema` 用于播种范围*之外*的表（仍然会授权它们，不变量 #4）；`search_corpus` 返回**内容**（匹配到的 few-shot 的 Q/SQL、指标表达式、术语映射），并限定 top-k；`sample_rows` / `run_query` 不变。
4. **改 prompt。** 把「在你 inspect 之前你看不到任何表」换成「下面的受治理上下文已经授权好了；只有要碰不在列表里的表时才用 `inspect_schema`」。

**理由。**
- **保底不低于 flow：** agent 从 flow 的完全相同的上下文出发（0.667），所以它不可能因为缺少语义层而退化到 flow 以下。
- **更少的超级步：** 播种省掉了搜索 / inspect 的往返，而正是这些往返（在串行工具调用的前提下，G1）把步数预算耗光了——这与 15→40 的上调是互补的。
- **不变量 #4 保住了：** 播种进去的就是 flow 那条*确定性*的 L4 底线（不是 agent 自己声明的）；扩展仍然只能经由受治理的 `inspect_schema`；护栏的 L4 = 播种 ∪ 已 inspect。范围严格 ≥ flow，且绝不自我授权。
- **治理不变：** 每次 `run_query` 仍然要规范化 → `check()` → ledger（不变量 #2/#3/#10）；播种加的是上下文，不是权限。

**被否决的备选——只靠工具**（让 agent 通过 `get_joins` / `get_few_shots` / `get_metrics` 自己去发现语义层）。它足够纯粹、在超大 schema 上省 token，但它会重演 A/B 的那个失败模式（模型可能压根不去取 join / few-shot），而且把步数翻上去。这些扩展工具作为「精修」那一半保留下来，只是不作为主路径。

**下一步要诚实测掉的后果。** 播种在构造上就把 agent 摆到了 flow 的分数上。那么它的正当性就完全落在**之上**那个循环——`run_query` 反馈驱动的修复、在真实数据上 `sample_rows`，以及检索召回不足时的范围扩展。重跑的 A/B 必须显示播种后的 agent 在难题上**赢过** flow；如果只是打平，那么对这个工作负载来说 agentic 这个论点就是弱的，P2 不应以 EX 为理由推进（治理 / 可观测性的收益就得独自撑起理由）。

**待定（修正 1）：** 播种的检索预算（沿用 flow 的默认值）；在大型企业 schema 上的 prompt 窗口大小（BIRD 没问题）；`search_corpus` 的内容检索是共用 flow 的 retriever，还是自己另设 top-k。

## 修正 2（2026-07-14）：治理 ledger 实时流出

**状态：** Implemented（已实现，P1）——随 `agent_serve` 开关后面的 agent 路径一起落地。

**是什么。** 那份只追加的治理 ledger（不变量 #10）现在会作为**实时事件流**发出，而不再只是挂在完成的 `Answer` 上。每一轮，agent 路径通过既有的 `on_event` 回调推出三类事件（前端在 `stream_mode="custom"` 上消费）：

- `rail` —— 每一个确定性的外层步骤（`route`、`refuse_gate`、`cache`、`assemble`）；
- `tool` —— agent 循环内每一个受治理动作（`search_corpus` / `inspect_schema` / `sample_rows` / `run_query`），形式是一个 `start` 后跟一个 `ok` / `blocked` / `error` / `cap` / `miss` 的落定，两者由 tool-call id 配对；
- `final` —— 终态答案的双轴印章。

每个事件带 `{seq, kind, step, status, id?, detail, serve_path?}`；一轮里的第一个事件标记 `serve_path:"agent"`，这样 UI 会选时间线渲染器而不是 flow 那个固定步进条。受治理工具的 `detail` 是**从 ledger 记录里构建的**，所以实时流和最终落在 `Answer.provenance` 上的 `governance_ledger` 不可能漂移——实时步骤视图*就是*那份 ledger，只是提前流出。这把不变量 #10 从一份事后审计转储，变成了对修复循环的逐次实时审计，也就是把本 ADR 论点里可观测性那一半做成了产品表面。

**怎么做的。** `GovEventStream`（`analyst/governance.py`）是架在原始 `on_event` 回调之上的按轮发射器（单调 `seq`、`serve_path` 标记、best-effort）。`agent_core_node` 把 `agent.invoke` 换成了 `agent.stream(stream_mode=["updates","values"])`：模型节点的工具调用变成 `start` 事件，工具节点的结果变成落定，最后累积的 state 是最后一个 `values` chunk。事件是从外层节点经由捕获到的回调重新发出的（**不是**在 agent 内部用 `get_stream_writer()`），所以发射集中在一处，并且越过 ToolNode 的工作线程仍然线程安全。共享的 finalize helper 在这条路径上以 `on_event=None` 运行，因此只会发出那份更丰富的契约。确定性 flow 路径不变——它继续发旧的 `{stage}` 事件。

前端规格与完整事件契约：[`docs/plans/agent-step-visualization.md`](../plans/agent-step-visualization.md)。测试：`tests/test_agent_step_events.py`。

## 实现注记（2026-07-14）：P2 切换已落地 `main`

**状态：** Implemented（已实现），提交 `d2fdd6a` 在 `main` 上。

上面描述的 Phase 2 切换已经发布：agentic 内核现在是**唯一**的服务路径。`analyst/flow.py`（`answer_question`）与那个腐化未用的 `analyst/graph.py` DAG 都已删除；聊天图和 `/chat` 一律走 `answer_question_agent`；那个已成摆设的 `agent_serve` 开关也没了——没有任何切换，agent 路径是无条件的。在没有配置线上模型时，服务在启动阶段就**失败即拒**（`make_graph` 抛错），而不是回落到某条确定性或模板路径，`/chat` 返回 `503`。治理（护栏 L1–L5、refuse-gate、L4 授权、双轴印章、ledger）不变且共享。eval 的 `flow_solver` / `flow_refuser` 换成了 `agent_solver` / `agent_refuser`，顺带给 agent 路径补上了 refuse-gate 的覆盖；`run_experiment` 只跑 agent。

## 修正 3：narration 成为一个节点 + 单 handler 追踪

**状态：** Implemented（已实现）。

**是什么。** 对 agent 路径护轨的两处修正，与治理正交：

1. **narration 变成一个专门的 `narrate` 节点**，接在 `agent_core` 之后（缓存命中之后也一样）：`ingest → refuse_gate → prepare → cache → assemble → agent_core → narrate`。此前 LLM narrator 是作为一次旁路调用埋在各个 finalizer 里的（`_finalize_success` / `_try_cache_hit` / `_finish_unsuccessful`，经由 `_answer_text`）；现在这些 finalizer 只产出确定性的兜底文案，由 `narrate` 节点里的 `narrate_answer`（`analyst/governance.py`）负责 LLM 措辞。**为什么：** narrator 的模型调用从此是一个一等的、可单独追踪的图步骤，而不是一次归不到任何节点头上的游离模型调用。缓存命中路径（`cache → narrate`）和 agent 路径（`agent_core → narrate`）都流经它，所以缓存的答案和新生成的答案以同一种方式收尾。对拒答（refuse-gate 命中、缺边：没有结果网格可措辞）以及没有配置 narrator 时，它是 no-op；narrator 失败则保留确定性文案。分级交付（unverified）的答案保留它的「⚠️ Unverified」横幅。
2. **每轮只有一个追踪（Langfuse）handler，并向下继承。** 外部追踪（`obs.tracing_callbacks()`）现在只在 `answer_question_agent` 的外层 `graph.invoke` 处挂一次，其下的一切经由 LangChain 的 run 上下文继承它。这修掉两个 bug：`agent_core` 里内层的 `agent.stream(...)` 不再挂第二个自己的 handler（第二个 handler 会让每次模型调用被记两遍——同一个 LangChain `run_id` → 两个 Langfuse generation 挂在不同父节点下 → trace 的成本 / token 约翻倍）；以及 `LangChainChatClient.complete()`（`llm/langchain_client.py`）在图节点内被调用时（服务路径的 narrator 和多 schema 的 schema 路由器）现在会继承环境 run 的回调，而不是另开一个游离的 Langfuse 根 trace。它只在被独立调用时（eval 的基线 solver、curator）才挂自己的 handler。**净效果：** 整个问答轮次是一条 Langfuse trace，成本 / token 聚合不再重复计数。LangSmith 不受影响，它自己从环境做插桩。

## 修正 4（2026-07-14）：HITL 澄清已在服务端落地

**状态：** Implemented（服务端已实现）；持久化仍然推迟。

Q6 那一行（上面「没有澄清机制（模型自己猜）」）以及 Phase 3 都把 HITL（`interrupt()` + checkpointer）列为推迟项。**中断机制此后已在服务端落地**：`analyst/tools.py::ask_user` 调用 `interrupt()`，`analyst/clarify.py` 承载澄清请求 / 响应的形状，`api/graph_app.py` 处理 `ClarificationPending` 的 resume 循环，`stack.py` 接上 `can_clarify` 与一个 `clarify_checkpointer`（由 `tests/test_serve_clarify.py` 覆盖）。仍然推迟的只有**持久** checkpointer（Postgres）——今天的 checkpointer 在内存里，所以一次澄清挺不过进程重启。前端契约在 [hitl-clarification-contract.md](../plans/hitl-clarification-contract.md)；前端自身的构建状态在 [`governed-bi-ui`](https://github.com/Minhao-Zhang/governed-bi-ui)，不在这里。所以「待定问题 → HITL」现在的范围是*持久化*，而不是机制。
