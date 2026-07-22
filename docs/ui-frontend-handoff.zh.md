# 前端交接文档 — governed-bi UI

_[English](ui-frontend-handoff.md) · [简体中文](ui-frontend-handoff.zh.md)_

**governed-bi** 前端的构建简报 + 契约。请与 [ui-frontend-design.md](ui-frontend-design.zh.md)
中的架构依据，以及 [ADR 0001](adr/0001-langgraph-server-chat-runtime.zh.md) 中的运行时决策
配合阅读。

> **状态:后端重构已落地,本契约已生效。** 聊天由一台 **LangGraph Server**(图
> id 为 `serve`)提供,前端用 **`useStream`** SDK 消费;corpus/schema/审计作为同一
> 台服务器上的**自定义路由**,再加一个开发用编辑端点和一张**完整知识图谱**。用
> `langgraph dev` 启动它(§2),直接据此开发即可。运行时理据见
> [ADR 0001](adr/0001-langgraph-server-chat-runtime.zh.md) 与
> [ADR 0002](adr/0002-governed-agentic-serve-runtime.md)。

---

## 1. 技术栈（已确定）

- **Next.js（App Router）+ React 19 + TypeScript（严格模式）** · **Tailwind CSS v4**
  （CSS-first 的 `@theme`）· **shadcn/ui**。
- **Chat：`@langchain/react` 的 `useStream`**，对接 **LangGraph Server**：提供响应式
  消息、**实时的节点/阶段事件**、可持久化的**线程（thread）**状态（历史记录）、工具
  调用生命周期，以及重连能力。
- **React Flow** 用于知识图谱 · **TanStack Query** 用于自定义 REST 读取 · **zod**
  用于校验自定义路由的响应。
- 该 UI 是一个**纯客户端**：聊天使用 `useStream`，自定义路由使用 `fetch`；它会根据
  `GET /capabilities` 自适应。

前端所需的环境变量：
```
NEXT_PUBLIC_LANGGRAPH_URL=http://localhost:2024   # LangGraph Server (chat + custom routes)
NEXT_PUBLIC_ASSISTANT_ID=serve                    # graph name in langgraph.json
```

---

## 2. 运行后端（待重构落地后）

在 engine 仓库中：
```bash
uv sync --extra agents --extra api                # agents = LangGraph/LangChain; api = custom routes
uv run --extra agents --extra api langgraph dev   # LangGraph Server at :2024 (chat + custom routes)
```
- 真实模型（自然语言应答 + 自由格式 SQL）：设置 `OPENAI_API_KEY`（环境变量或仓库
  根目录下的 `.env`）。
- 追踪（可选；见 `.env.example`）：
  - LangSmith：`LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true`（或旧名 `LANGCHAIN_TRACING_V2=true`）
  - Langfuse：`uv sync --extra tracing`，再设 `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`
- CORS：在 `governed_bi.toml` 的 `[serve].cors_origins` 中配置（默认含 `http://localhost:3000`）。
- 在 `langgraph dev` 下，**本地线程是临时性的**（持久化落在已部署的 Postgres 上）。
  `/capabilities` 会报告 `has_live_model`、`can_stream`、`can_edit`、
  `environment`、`dialect`。

---

## 3. Chat —— 通过 `useStream`（LangGraph 协议）

```tsx
type ChatState = { messages: Message[]; answer: GovernedAnswer | null };

const [threadId, setThreadId] = useState<string | null>(null);
const stream = useStream<ChatState>({
  apiUrl: process.env.NEXT_PUBLIC_LANGGRAPH_URL!,
  assistantId: process.env.NEXT_PUBLIC_ASSISTANT_ID!, // "serve"
  threadId, onThreadId: setThreadId,                  // 持久化 thread id(即历史记录)
  onCustomEvent: (data, { mutate }) => mutate((p) => ({ ...p, stage: data })), // 实时阶段
});
stream.submit(
  { messages: [{ type: "human", content: q }] },
  { streamMode: ["values", "messages", "custom"] },  // 阶段事件需要 "custom"
);
```
- **消息/历史记录:** `stream.messages`(以线程为后盾;按 `threadId` 重新加载/重新
  加入)。线程即持久化,前端不拥有任何对话数据库。
- **实时步骤:** 该图会发出带类型的治理事件,通过 `onCustomEvent(data, { mutate })`
  这个选项送达(该次 run 的 `streamMode` 必须含 `custom`)。当前的事件形状是来自
  `GovEventStream` 的 `{seq, kind: "rail"|"tool"|"final", step, status, id?, detail,
  serve_path?}`——这是一条**动态的进度轨(rail)+ agent 工具循环**(`search_corpus` /
  `inspect_schema` / `sample_rows` / `run_query`),**不是**固定的 6 阶段列表。权威的
  事件契约与 `buildStepsFromLedger` 映射见
  **[agent-step-visualization.md](plans/agent-step-visualization.md)**。这反映的是
  *真实的*后端进度,不是计时器。
- **最终答案**是一个自定义的 **`answer` state 通道**:读 `stream.values.answer`
  (即 `AnswerResponse` 的形状)。渲染出**答案卡片**:
  - 两个徽章，而不是一个分数：`safety_clearance`（布尔值）+ `semantic_assurance`
    （`grounded|heuristic|unverified|none`）；档位标签为绿色/黄色/红色。
  - 英文答案文本；可折叠的**结果表格**（`columns`/`rows`，含截断提示）；只读的
    **SQL**；**溯源/审计抽屉**（route、tables_used、join_ids、min_join_confidence、
    attempts、uncertainty_flags 等）。
  - 拒答 → 显示升级提示，不给出 SQL/数字。

包说明:`@langchain/langgraph-sdk/react` 提供 `onCustomEvent` 和 `stream.values`;
更新的 `@langchain/react` 超集另加选择器 hook(`useChannel`)和 `stream.respond`。
两者都能对接本服务器。

（`POST /chat` 是相对于 LangGraph 流的非流式 REST 替代方案——但**不是离线回退
方案**：根据 ADR 0002，serve 只有 agent 一条路径，因此 `/chat` 同样需要真实模型，
没有模型时会返回 `503`。）

---

## 4. 自定义路由（REST，位于同一服务器上）

使用 `fetch` 从 `NEXT_PUBLIC_LANGGRAPH_URL` 请求这些路由。数据形态与
`governed_bi.viz.presenter` 保持一致；重构完成后会重新导出一份机器可读的 schema。

| 方法 + 路径 | 用途 |
|---|---|
| `GET /capabilities` | `{ environment, dialect, can_edit, edit_mode, can_stream, can_scope, can_search, can_clarify, has_live_model, model }`——据此控制 UI 功能的启用 |
| `GET /health` | corpus 健康度：计数、`ci_green`、问题项、`n_suspect_columns`、`n_excluded`、`n_low_confidence_joins` |
| `GET /schema` | 表 + 列（类型、角色、`reliability`、`excluded`、溯源）。命名空间字段为 **`schema`**。可选 `?schema=&limit=&offset=`（无参 = 全量 dump）。**不接受 `?db=`。** |
| `GET /schema/summary?schema=&limit=&offset=` | **精简目录** `{ total, items }`，供虚拟化列表 + 客户端搜索索引使用；每个 item 为 `{ id, physical_name, schema, row_count, n_columns, excluded, has_suspect, provenance_status, columns:[{physical_name, physical_type, role, reliability, excluded}] }`（重字段已丢弃；`total` 为分页前计数） |
| `GET /schema/{table_id}` | 单张表的**完整** `TableResponse`（含 `schema`），在打开详情时惰性拉取；未知 id 返回 `404` |
| `GET /graph` | **ER 图** `{ nodes, edges, boundary?, meta? }`（节点带 `schema`/`row_count`/`n_columns`/`has_suspect`；边带 `on`/`cardinality`/`confidence`/`low_confidence`）。可选 D15 划范围：`?schema=&focus=&radius=&node_budget=`——划范围响应含 `boundary` + `meta`（回显 `scope` 供 `engineScopeMatches`）。无参 = 全图 |
| `GET /knowledge-graph` | **完整知识图谱** `{ nodes, edges, boundary?, meta? }`；表节点带 `schema`。与 `/graph` 相同的划范围参数，外加 `?kinds=`（逗号分隔） |
| `GET /corpus/assets?type=` | 非 table 资产（`note` 取代了原来的 `rule`；`skill` 已移除，`?type=rule` 会返回 422） |
| `POST /corpus/edit` *（仅 dev；以 `can_edit` 为门槛）* | 校验提交的资产 → 写入 YAML（dev）/ 提交 PR（prod）；返回校验结果 + diff |

---

## 5. 知识图谱视图（React Flow）

- 节点按 `kind` 分类；自定义节点卡片；**按类型划分的过滤器/分层**。
- 边的样式由关系类型 + `low_confidence`（虚线/红色）+ `cardinality` 决定。
- `excluded` / `has_suspect` 的徽章。点击 → 从 `/schema` 或 `/corpus/assets` 获取
  详情。

---

## 6. 持久化

由**LangGraph 运行时**处理（线程/检查点）；前端**不**拥有对话数据库。`useStream`
按线程 id 加载/重新加入线程。本地是临时性的；部署后由 Postgres 持久化。（只有当
线程元数据不足时，才会加入一个轻量的应用元数据数据库。）

---

## 7. 编辑（dev）

当 `capabilities.can_edit` 为真时，为 corpus 资产展示编辑表单；提交到
`POST /corpus/edit`。后端会先校验、再写入文件（dev）；把返回的校验问题项 + diff
呈现出来。在 prod 环境下，这会变成一个 PR（已推迟）；UI 侧的路径是一样的。

---

## 8. 当下已实现 vs. 规划中

- **已实现（本次重构,离线测试 + `langgraph dev` 验证过）:** LangGraph Server 聊天
  图(`serve`)+ `langgraph.json`;一个薄的 `{messages, answer}` 聊天 state(无需
  序列化 `ServeState`);经 `get_stream_writer()` 的阶段流式;挂载的自定义路由
  (`http.app`);`GET /knowledge-graph`(完整图)与 `GET /graph`(ER)并存;
  `POST /corpus/edit`(dev);LangSmith + Langfuse 追踪(按需开启);重新导出的
  [openapi.json](openapi.json)。外加更早的 `presenter` 视图模型、REST 读取、`stack`
  工厂,以及非流式的 `/chat` REST 端点(同样需要真实模型)。
- **后端已上线(server 侧):** 人工把关的**澄清中断(clarification interrupts)**——
  `analyst/tools.py::ask_user` 中的 `interrupt()`,经 `submit(command.resume)` 恢复,
  以 `capabilities.can_clarify` 为门槛(契约见
  [hitl-clarification-contract.md](plans/hitl-clarification-contract.md))。前端的构建
  进度请以 [`governed-bi-ui`](https://github.com/Minhao-Zhang/governed-bi-ui) 为准——本仓库
  不再跟踪。中断的持久化(Postgres)检查点仍推迟。
- **推迟:** prod 的 PR 编辑(如今 dev 是直接写文件)、公开演示的成本策略、鉴权/RLS、
  持久化的人审(HITL)状态。

以上全部在 `langgraph dev` 后面已经跑通,现在就据此开发。

---

## 9. 建议的构建顺序

1. 搭建 Next.js + Tailwind v4 + shadcn 的脚手架；把 `useStream` 接到本地的
   `langgraph dev`；读取 `/capabilities`。
2. **Chat**：实时阶段 + 答案卡片 + 溯源抽屉（线程 = 历史记录）。
3. **Schema 与知识图谱**：React Flow + 详情。
4. **Corpus + 健康度**；**编辑**（dev，以 `can_edit` 为门槛）。
5. 部署：Vercel UI + 托管的 LangGraph Server；解决设计文档 §13 中的未决事项。

---

## 10. 多 schema 服务（D15 —— 线上更名 + 图划范围已落地）

引擎连接的是**一个数据库容纳多个 schema**，并支持可执行的**跨 schema 连接**
（[design-decisions.md](design-decisions.zh.md) D15）。多 schema 服务（限定 SQL +
护栏 + 缺失边拒答）、**API 线上字段更名**与 **服务端图划范围**已落地；
[openapi.json](openapi.json) 与之一致。

> **已发布（线上契约 + serve + 图划范围）：**
> - 命名空间字段为 **`schema`**（`TableResponse` / `TableSummary` /
>   `SkillResponse` / 图节点）。过滤只用 **`?schema=`**——硬切断，无 `?db=` 别名。
> - `GET /schema/summary`、`GET /schema/{table_id}`、`can_scope` / `can_search`。
> - Postgres/Redshift 默认多 schema；SQLite 保持单 schema（BIRD）。
> - 跨 schema 且无策展 join → 拒答（`refused_by: "missing_edge"`）并带 D12
>   `clarification_hint`。
> - **`GET /graph` / `GET /knowledge-graph`** 接受 `?schema=` / `focus` /
>   `radius` / `node_budget`（KG 另有 `kinds=`）。划范围响应含 `boundary`
>   （跨 schema 桩边）+ `meta`（截断信息 + 回显 `scope` 供 `engineScopeMatches`）。
>   无参 = 全图（兼容）。默认：ER 预算 60、KG 150、focus 半径 1；硬上限与之对齐。
> - **磁盘 corpus：** YAML / `TableAsset.schema`（硬切断；原为 `db`）。加载/写入
>   API 资产带 `schema=`；serve 加载全部 corpus 子树（无环境变量钉选）。
> - **Schema 路由器：** 多 schema 服务先短名单 schema，再沿策展跨 schema join
>   扩展，然后进入 RVGD（provenance 中有 `routed_schemas`）。
>
> **仍推迟：**
> - 服务端 `/search`（按 Q6，客户端 Fuse 仍为默认）。
> - `DataSourceConfig.corpus_pin`（BIRD db_id / 默认写入子树）仍与 Postgres pin
>   字段 `schema` 分开。

对 UI 的契约要点：单条 `schema` 边栏；跨 schema join 可导航；缺失边拒答；当
`meta.scope` 与请求一致时优先信任引擎划范围（`engineScopeMatches`），旧引擎仍可
客户端回退。

---

## 11. 已解决：前端的未决问题（`DESIGN_QUESTIONS.md` §9）

后端负责人对前端 `DESIGN_QUESTIONS.md` 中八个问题的答复。凡是会改变契约的答复，
§10 已经承载。

| # | 问题 | 答复 |
|---|---|---|
| Q1 | 两级 `db → schema` 树，还是扁平？ | **扁平。** 一个数据库容纳多个 schema；corpus 建模的是 `schema → table`，不存在 `db`/连接层级（数据库是服务端配置常量）。以单条 `schema` 边栏导航；**不要**构建两级树。 |
| Q2 | 真实部署会把数百张表放进单个 schema 吗？ | 在 BIRD 里不会（约 11 张表/schema；beer_factory = 9），但在真实企业 schema 里**会**。因此仅凭 schema 边栏就几乎覆盖了 BIRD 规模；**Phase 2**（focus/radius + 命名空间内再分组）仅在面对大型单 schema 时才是必需的——等某个目标 corpus 需要时再做。Phase 1 无论如何都值得做。 |
| Q3 | 线上字段用 `schema` 还是 `schema_name`？ | **`schema`**（贴合领域；`/schema` 是路由路径，zod 用 `schema` 键也没问题）。仅当 zod 的使用体验受影响时才退回 `schema_name`，且绝不拆成两个名字。 |
| Q4 | `node_budget` 如何取值；由谁强制？ | **服务端强制一个硬上限；客户端可请求更低的值。** 起点取 **50–60 个 ER 卡片**、**约 150 个语义图字形（glyph）**——这些是关于 DOM 负担的估计值，需在目标硬件上实测，并非最终数字。 |
| Q5 | 命名空间内再分组以什么为键？ | **连通分量（connected component）**（连接可达性 = 与查询相关的簇）对审计者最有意义；**表名前缀**是廉价且确定的默认项；**粒度（grain）**需要 curator 输入。默认用连通分量，并以表名前缀兜底。 |
| Q6 | 服务端 `/search` 值得构建吗？ | **在预期规模下不值得。** 在 `/schema/summary` 之上建一个客户端 Fuse 索引就足够；服务端 FTS 是尚未明确的实打实工作，继续推迟（只有到数万张表规模才有意义）。 |
| Q7 | 跨库边界当作治理警告吗？ | **不——它反转了。** 只有一个数据库时，跨 *schema* 连接是可执行的，因此把它渲染为普通的可导航关系（见 §10）。跨*数据库*（联邦）不在范围内，这里也不会出现。 |
| Q8 | 引擎能返回稳定的截断顺序吗？ | **能。** 当 `node_budget` 截断某个邻域时，保留集是确定的：**从 focus 节点做 BFS，按边置信度降序、再按 id 升序排列**。已缓存的范围与"展开"绝不会重新洗牌。 |

---

## 12. 从哪里开始（现在可做 vs. 仍依赖后端）

**现在可对着做：**

- 线上命名空间只有 **`schema`**（§4 / [openapi.json](openapi.json)）。UI 发
  `?schema=`；zod 只认 `schema`（无 `db` 双接受）。
- `/schema`、`/schema/summary`、`/schema/{id}` 过滤/分页正确。
- 图端点接受划范围参数并返回 `boundary` / `meta`（§10）。
- 聊天、拒答（含 `missing_edge`）、`can_edit` 时的编辑。

**仍推迟：**

- 服务端 `/search`（客户端 Fuse 仍为默认）。

**新工程师第一步：** 对着已落地的 `schema` 线上契约做 Schema 标签页；图上优先信任
与请求一致的引擎 `meta.scope`。

---

## 13. 可靠性与交付并分级（新设计）

来源：[D5（双轴标记 + 分级交付）](design-decisions.zh.md#d5拒答与尽力而为)；关于
已钉住 corpus 的语境见 [pipeline-design.md §1](pipeline-design.md)。动机：可靠性
处理方式就是引擎**交付并分级（deliver-and-grade）**这一决策的产品呈现面——一次
覆盖范围 / L3–L5 / 执行失败会带着 `unverified` 标记交付 SQL,而不是直接拒答,这样
经过策展的分支就不会因为它只能带着说明作答的、本可回答的问题而被扣分。（缺失的
跨 schema **join** 仍会硬性拒答,遵循 D15;只有覆盖范围/修复这一类才会走分级。）

本节是可靠性契约的**唯一权威来源**。它**取代**§10/§12 中对 `missing_edge` 的
"按其他拒答一样展示"指引——该情形正在被重新分类(见下文)。

### 13.1 模型:安全是二元的,把握程度是分级的

两个相互独立的轴(都已在 `AnswerResponse` 上)：

- **`safety_clearance: boolean` —— 硬性的,永不会被分级掉。** 为 false 时表示该次
  查询没有通过某道安全关卡(**L2 策略**:DDL/DML/注入,或经过策展的**反例**拒答
  关卡)。安全失败在**任何**可靠性分数下都**绝不会被交付**。不存在"把分数调低然后
  照样跑"这种事。
- **`semantic_assurance: grounded | heuristic | unverified | none` —— 分级的。**
  这才是应当据以着色的可靠性指标。由 `provenance.uncertainty_flags`(触发的信号)
  驱动:`low_confidence_join`(join 计划置信度 < 0.7)、`suspect_in_scope`(用到了
  curator 标记过的诱饵/可疑列)、`repaired`(超过 1 次生成尝试)、
  `fenced_raw_fallback`。没有任何标志 → `grounded`;`fenced_raw_fallback` →
  `unverified`;其他任意标志 → `heuristic`。

`tier`(`governed | lineage | fenced_raw | refused`)是 `semantic_assurance` 的
一个遗留的、**只用于展示的一一映射**——继续把它渲染成一个档位标签(chip),但
分支逻辑要落在上面这两个轴上,而不是 `tier` 上。

### 13.2 三种渲染状态(精确规则)

| 状态 | 如何判定 | 渲染方式 |
|---|---|---|
| **正常答案** | `sql != null` 且 `semantic_assurance ∈ {grounded, heuristic}` | 正常的答案卡片;绿色/中性档位标签。`heuristic` = 一条轻量的提醒。 |
| **分级交付** | `sql != null` 且(`semantic_assurance ∈ {unverified, none}` **或** `provenance.graded_delivery === true`) | **照常展示 SQL + 结果表格**,但包裹在一个明显不同的**警示处理**里(琥珀色/红色边框 + 横幅):*"We produced this answer but could not fully verify it."* 外加**"为什么"这一行**(§13.4)。这是大多数 UI 会做错——直接把它隐藏掉——的新状态。 |
| **硬性拒答** | `sql == null`(始终 `tier=refused`、`safety_clearance=false`、`result=null`) | 现有的拒答框:升级提示文字,没有 SQL/数字。 |

值得依赖的关键不变式:**硬性拒答永远 `sql == null`/`result == null`;分级交付永远
带着真实的 `sql` + `result`。** 所以 `sql == null` 才是"拒答"和"已交付(不论把握
程度如何)"之间可靠的判定依据。不要用 `tier === "refused"` 作为门槛——一次分级交付
的 `tier` 是 `fenced_raw`,不是 `refused`,但它仍必须带着警示感呈现。

### 13.3 契约里今天已经上线的 vs. 后端仍欠你的

- **契约里今天已经上线的:** 两个轴;`provenance.graded_delivery` 标记;
  `provenance.uncertainty_flags`;`graded_delivery` **流事件**;以及整条交付并
  分级的代码路径(`analyst/answer.py::graded_delivery`、`_finish_unsuccessful`)。
  §13.1–13.4 可以直接对着现有的这些形状去构建。
- **闲置,等一个开关翻转:** 交付并分级功能藏在引擎设置
  **`grade_semantic_failures`** 后面,该设置**在服务时默认 `false`**(目前只在
  评测 harness 里开着)。在后端为 serve 打开它之前,每一次语义失败仍会以**硬性
  拒答**(`sql=null`)的形式到达——所以分级交付这条分支逻辑是对的,只是暂时不会
  触发。**上线耦合关系:§13.2 的 UI 必须先于或随该开关一起上线**,否则用户会突然
  收到只带一个不起眼徽章提醒的 `fenced_raw` 答案。
- **拒答理由**(在 `provenance.refused_by` 里):`refuse_gate`、`no_coverage`、
  `guardrail`、`execution`、`missing_edge`。当 `grade_semantic_failures` 打开后,
  只有 `refuse_gate` 和 **policy_blacklist** 护栏会继续保持硬性拒答;其余的
  (`no_coverage`、可修复的 `guardrail`、`execution`、`missing_edge`)都会变成
  分级交付。**`missing_edge` 被重新分类:** 它不再是硬性拒答(取代 §10/§12 的说法)
  ——它会变成一次单 schema 内的答案,或者一次分级交付。

### 13.4 "为什么"这一行(把标志位翻译成人话)

一次分级交付必须在卡片上(不能只在抽屉里)告诉用户它*为什么*被标记。把
`provenance.uncertainty_flags` 映射成文案:

- `low_confidence_join` → "Joined tables on a relationship we're not fully sure of."
- `suspect_in_scope` → "Used a column that may be unreliable (flagged during curation)."
- `repaired` → "Needed multiple attempts to produce valid SQL."
- `fenced_raw_fallback` → "Fell back to a raw query without the governed layer."

`min_join_confidence` 和 `attempts` 已经在 `provenance` 里(并且已经渲染在抽屉
里)。如果 `suspect_columns` 被加进 `provenance`(见 13.6),就在这句话里指名具体
是哪一列。

### 13.5 需要向后端申请的契约新增项(尚未上线)

都是小的、增量式的;都不会破坏现有的形状:

1. **`AnswerResponse` 上的 `delivery: "governed" | "graded" | "refused"`** ——
   一个一等字段,让 UI 据此分支,而不是靠 `sql == null` + `tier` +
   翻找 `provenance.graded_delivery` 去推断。推荐。
2. **`provenance.suspect_columns: string[]`** —— 这样"为什么"这一行就能指名
   具体是哪一列。
3. **`provenance.selected_schema`(+ `candidate_schemas`)** —— 见 13.6。
4. **`provenance.corpus_version`(git hash)** —— 见 13.7。

在(1)落地之前,按 §13.2 在客户端推导 `delivery`。字段(2)–(4)一旦出现就会自动
渲染出来(provenance 是一个开放的 `Record`;把它们加进抽屉的 `PREFERRED_ORDER`
以确定展示位置)。

### 13.6 Schema 选择展示(以后端为门槛)

设计目标(§5.1):检索先短名单出约 3 个 schema → 由一个**LLM 节点挑选其中一个**
→ 下游只使用那一个 schema;UI 展示是哪个 schema 给出了这次应答。

- **尚未构建。** 引擎今天做的是确定性的**BM25 短名单 + 策展 join 扩展成一个集合**
  (`schema_router.route_schemas`),对外表现为 `provenance.routed_schemas`(一个
  无序集合)和一个 `schema_route` 流事件。目前**没有单一的"已选定"schema,没有
  候选打分/排名,也没有 LLM 挑选**这一步。
- **UI 现在(过渡方案):** 可以把 `provenance.routed_schemas` 展示为"已考虑的
  schema",并在阶段步进器里加一个**"正在选择 schema"**步骤(只是给现有的
  `schema_route` 事件加一条 `STAGE_ALIASES` 映射——不需要改组件)。
- **UI 以后(等后端加上 LLM 挑选 + `selected_schema` 之后):** 在答案卡片上加一个
  小标签——"answered using schema `X`"——并在抽屉里展示候选列表。单 schema 的
  数据库(SQLite/BIRD)永远不展示这个。

### 13.7 Corpus 版本指示(以后端为门槛)

设计(§1):生产推理读取的是一个**钉住的 corpus git hash**,绝不是当前工作副本。
出于可复现性/可信度,答案应当展示是哪个 corpus 版本产出的它。

- **今天什么都没有** —— 契约里任何地方都没有 corpus hash/版本字段。需要先做
  后端接线(corpus 加载器 → `provenance.corpus_version` → presenter),才谈得上
  任何 UI。
- **UI(字段出现之后):** 在溯源抽屉或聊天页头里放一个低调的"corpus @ `abc1234`"
  指示。优先级低;字段一旦上线,做起来很简单。

### 13.8 SME 澄清界面(范围决策——这里可能不在范围内)

设计(§4):一个异步往返循环,由**人类 SME 回答 curator 提出的开放澄清问题**,再经
`accept_answer` 折返回去。

- UI 里**什么都没有**(corpus 的"编辑"按钮目前是一个 `toast()` 占位,尽管
  `POST /corpus/edit` + `EditResponse` 的管线是真实存在的)。
- **未决决策,不要想当然:** 按[范围边界](design-decisions.zh.md)的说法,corpus
  编辑 + 保存为 PR 是由企业应用 / git+CI 承担的,**不**属于本仓库。所以 SME 作答
  界面可能属于别处。如果它*确实*在 `governed-bi-ui` 的范围内,那它会是最大的一块
  净新增界面:一份开放澄清问题的列表(问题、目标资产、上下文)→ 一个作答表单 →
  提交 → `accept_answer` → 展示产生的 corpus diff。开工前先确认归属。

### 13.9 §13 的构建顺序

1. **答案卡片的三态渲染 + 可靠性处理 + "为什么"这一行**(13.2、13.4)——纯 UI
   工作,对着现有契约就能做;价值最高的改动。复用现有的 `ReliabilityStamp` 和
   开放的 `provenance`。
2. 申请 **`delivery`** 字段(13.5#1);字段落地后把分支逻辑切过去。
3. 加上**"正在选择 schema"**的步进器别名和过渡期的 `routed_schemas` 展示
   (13.6);答案时的那个小标签要等后端做完 LLM 挑选才能上。
4. Corpus 版本指示(13.7)和 SME 界面(13.8)——都是以后端为门槛 / 决策待定。

---

## 14. 列 → 相关语义资产

让 UI 能做到**"点一列 → 看到每一个触及它的语义层资产。"** corpus 本来就在列这一
粒度上持有全部这些链接;`presenter.knowledge_graph` 会把它们**折叠**到表这一
粒度(一个以列为目标的绑定/范围会被重定向到它所属的表,经由 `col_to_table`,所以
`/knowledge-graph` 永远不会把列暴露出来)。这个端点在**不**打乱那张图的前提下,把
列这一粒度重新展示出来。

> **状态:已上线。** `GET /columns/{column_id}/related` 已实现
> (`presenter.related_to_column`、`ColumnRelatedResponse`),并已写入
> [openapi.json](openapi.json)。**UI 的 Phase 1 完全不需要后端做任何事**——
> FK 出/入本来就在 `ColumnResponse.references` 上,join 也可以从 `/schema` +
> `/graph` 按表粒度展示出来。**更丰富的单列视图**(term、rule、rule 的 scope、
> 精确到"这个 join 碰到了这一列")才需要用这个端点。

### 14.1 端点

`GET /columns/{column_id}/related`

- `column_id` 是**派生出来**的列 id:`col_<不带 'tbl_' 前缀的表 id>_<物理列名>`
  ——例如 `col_beer_factory_customers_CustomerID`(见
  `corpus.ids.derive_column_id`)。这与 `Column.references`、
  `TermBinding.asset_id`、`NoteAsset.scope` 条目所用的是**同一套 id**。
- 当该 id 无法解析到一个已知列时返回 `404`。

### 14.2 响应(`ColumnRelatedResponse`)

```jsonc
{
  "column": {
    "id": "col_beer_factory_customers_CustomerID",
    "table_id": "tbl_beer_factory_customers",
    "table_physical_name": "customers",
    "schema": "beer_factory",          // 命名空间字段,与其他地方的约定一致
    "physical_name": "CustomerID"
  },
  "terms": [                            // TermAsset.binding 指向这一列(KG 关系:"grounds")
    { "id": "term_customer_id", "name": "customer id", "synonyms": ["cust id"],
      "confidence": 0.9, "provenance_status": "draft" }
  ],
  "rules": [                            // NoteAsset 的 `scope` 里含这一列的 id(KG:"scopes";wire key 仍是 `rules`)
    { "id": "note_active_customer", "kind": "business_rule",
      "statement": "…", "confidence": 0.8, "provenance_status": "draft" }
  ],
  "fk_out": {                           // 这一列自身的 Column.references,已解析;不是 FK 则为 null
    "column_id": "col_beer_factory_orders_CustomerID",
    "table_id": "tbl_beer_factory_orders", "physical_name": "CustomerID"
  },
  "fk_in": [                            // 其他地方那些 `references` 指向这一列的列
    { "column_id": "col_beer_factory_orders_CustomerID",
      "table_id": "tbl_beer_factory_orders", "physical_name": "CustomerID" }
  ],
  "joins": [                            // ON 谓词碰到这一列的 JoinAsset(服务端解析)
    { "id": "join_customers_orders", "left_table": "tbl_beer_factory_customers",
      "right_table": "tbl_beer_factory_orders", "other_table_id": "tbl_beer_factory_orders",
      "on": "customers.CustomerID = orders.CustomerID",
      "cardinality": "one_to_many", "confidence": 0.95, "low_confidence": false }
  ],
  "metrics": [                          // 仅表粒度——见 14.4
    { "id": "metric_customer_count", "name": "customer count", "granularity": "table" }
  ],
  "meta": { "column_resolvable": true }
}
```

所有列表字段为空时都是 `[]`(绝不是 `null`);`fk_out` 是唯一可为 null 的字段。
每一项都带着自己的 `provenance_status` / `confidence`,这样 UI 就能像在别处一样
标出 `draft`/低置信度的链接。

### 14.3 Id 编码方案规则(唯一一个坑)

两套不同的列标识方案同时存在——这里搞错的话 join 就对不上号:

- **资产 id 方案** —— `col_<table>_<physical_name>`(来自 `derive_column_id`)。
  被 `TermBinding.asset_id`、`NoteAsset.scope`、`Column.references` 使用。
  `terms`、`rules`、`fk_out`、`fk_in` 都直接用它做键。
- **物理谓词方案** —— `JoinAsset.on` 是一个基于**物理**列名(而**不是**列 id)的
  原始 SQL 相等字符串(`"customers.CustomerID = orders.CustomerID"`)。所以
  `joins` 是**服务端**解析出来的:每个 `JoinAsset` 本来就带着 `left_table` /
  `right_table` 作为**资产 id**,于是服务端把 `on` 解析成
  `(physical_table, physical_column)` 对,再经由 `derive_column_id` 针对这两张
  端点表把它们映射回列 id。**前端不能自己拿一个 `col_` id 去匹配 `on` 字符串**——
  那些字符串是物理层面的,而物理名字在不同 schema 之间是可能撞车的。

### 14.4 指标只有表粒度

`MetricAsset` 只有 `base_table`(一个表 id)+ `expression`(语义化的表述,不是
SQL);**没有**结构化的物理列。所以 `metrics` 返回的是 `base_table` 就是这一列
所在表的那些指标,并标记 `"granularity": "table"`。UI 必须把它们标注为**"这张表
上的指标"**,而不是"用到这一列的指标"。列级精确的指标解析要等 SQL-生成级别的
表达式解析能力出现之后才在范围内。

### 14.5 为什么是一个端点,而不是在 `/knowledge-graph` 里加列节点

被否掉的方案:给全局 KG 加列节点。KG 有节点预算上限(KG 默认/上限 **150**;见
§10),而"列不是节点"是 `viz/scope.py`、边界检测和 ER 视图过滤器都依赖的一个
不变式。一张真实的表远不止 150 列,列节点会直接击穿预算,而截断逻辑会开始丢掉
真正的资产。一个**聚焦于单列的端点**更省成本,还能保住这条图的不变式。(如果以后
真的想要图形化呈现,优先考虑 `/knowledge-graph?focus=<col_id>` 这种语义,而不是
把列节点全局物化。)

### 14.6 §14 的构建顺序

1. **Phase 1(不需要后端工作）：** 列详情面板,展示来自 `ColumnResponse.references`
   的 FK 出/入,以及来自 `/graph` 的表粒度 join。现在就可以上。
2. **Phase 2(这个端点——已上线）：** 接上 `GET /columns/{column_id}/related`;
   渲染 term、rule、服务端解析过的 join,以及表粒度的指标。用
   [openapi.json](openapi.json) 里的 `ColumnRelatedResponse` 对响应做
   `zod` 校验。

---

## 15. 答案上的治理台账(契约说明)

在讨论列相关功能时提出;之所以记在这里,是因为这是一个活的契约要点,而不是一个
列相关的功能。

- **Agent serve 路径:** `answer.provenance.governance_ledger` **确实**被填充了——
  一份 `{action, verdict, sql, allowed, licensed_ids, layer, reason, result,
  attempt}` 记录的列表——并通过 `presenter.answer_view`(它会把整个 provenance
  字典原样拷贝过去)流入 `AnswerResponse.provenance`。`analyst/agent.py` 甚至还有
  一道双重保险的兜底逻辑,在缺失时把它补上。所以在 agent 路径上,前端的
  `buildStepsFromLedger` 有了它稳固的数据来源:这条轨迹能撑过一次页面刷新,也能
  撑过一次非流式的答案,不依赖实时事件流。
- **现在只有一条 serve 路径。** ADR 0002 的 P2 切换删掉了那条确定性流程,所以
  **每一个**被服务出去的答案都带着一份治理台账;看起来缺失治理台账的,是一个
  较旧的构建版本,不是第二条路径。

`provenance` 是一个开放的 `Record`;只要抽屉知道去找 `governance_ledger`,它就会
自动渲染出来(把它加进抽屉的 `PREFERRED_ORDER` 以获得确定的展示位置)。
