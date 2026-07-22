# governed-bi UI — 设计

_[English](ui-frontend-design.md) · [简体中文](ui-frontend-design.zh.md)_

一个独立于 `governed-bi` 引擎的 **Next.js + React + Tailwind CSS v4 + TypeScript**
前端，构建在 **LangGraph Server** 之上的 **LangChain 前端 SDK（`useStream`）**，外加
该 server 暴露的 corpus/schema/audit 路由。

> **状态：后端重构已落地；契约已生效。** 下文的决策均已锁定（见 §2）。LangGraph-Server
> 聊天运行时、自定义的 corpus/schema/audit 路由、完整的知识图谱序列化器、dev 编辑端点
> 以及 tracing 均已发布（本文 §5 与阶段列表 §14 早于这些改动，如今更多作为依据/历史阅读）。
> **新前端工程师应从交接文档入手——[ui-frontend-handoff.zh.md](ui-frontend-handoff.zh.md)：**
> §9（构建顺序）、§11（已解决的未决问题）、§12（现在可做 vs. 依赖 D15 多 schema
> 后端构建）。此次运行时转向记录在
> [ADR 0001](adr/0001-langgraph-server-chat-runtime.zh.md) 中。

---

## 1. 目标与非目标

**目标**
- 一个覆盖受治理 serve 流程的 UI：带**实时逐步进度**的
  聊天（受治理流水线，由后端流式推送）、双轴可靠性标记、结果表格，以及
  溯源/审计下钻（drill-down）。
- **将整个语义层**（"每一份记忆片段"）可视化为一个可过滤的**知识图谱**——
  table、column、metric、term、join、rule、few-shot、negative——外加带治理
  标志的 table/column schema 详情。
- **加载 + 校验 + 编辑** corpus：编辑会发起一次 API 调用；在 dev 环境下由后端
  写入 YAML 文件，在 prod 环境下则会开启一个 PR。
- **追踪 agent**，使用 Langfuse + Langsmith。
- 一套代码库，本地运行、作为公开演示运行（内置 SQLite），以及（之后）在
  内部环境中对接真实数据库运行——全部通过配置区分。

**非目标（近期）**
- 多租户鉴权 / 真实的 RLS 身份画像（引擎的 RLS 是一个尚未实现的预留接口
  （seam））。
- 将本地对话的持久化存储当作硬性需求（本地线程是临时的；持久化随部署后的
  Postgres 一起到来——见 §7）。
- 公开演示的成本加固（推迟——见 §12）。

---

## 2. 决策日志

| 决策 | 结论 | 取代对象 |
|---|---|---|
| **Chat 运行时** | **LangGraph Server + `useStream` SDK** | 自建的 FastAPI `/chat` |
| **实时进度** | 通过 SDK 使用 LangGraph 的**节点流式（node streaming）**，映射到带标签的阶段 | 客户端模拟的加载指示器 |
| **持久化** | **LangGraph 线程/检查点（threads/checkpoints）**——本地临时（`langgraph dev`），部署后为持久化的 Postgres | 前端自持的 Neon/Drizzle |
| **非图端点** | **LangGraph server 上的自定义路由**（`/schema`、`/graph`、`/corpus`、`/health`、`/corpus/edit`） | 独立 FastAPI 作为唯一后端 |
| **编辑** | **当前 dev 环境直接写文件**（先 validate → 再通过既有原语写入），PR 流程稍后再做 | “推迟 / 只读” |
| **可视化** | **全 corpus 知识图谱**，可按资产类型过滤 | 仅 table+join 的 ER 图 |
| **可观测性** | **Langfuse + Langsmith**，由环境变量门控，未设置时为 no-op（新增 `tracing` extra） | （新增） |
| **身份** | 单一的演示身份；API 具备身份感知能力 | — |
| **前端语言** | **仅英文**；仓库所有文档保持双语 | — |

有两点后果：采用 LangGraph Server 之后，API 不再是"一次请求 → 一次响应"
（它会流式推送并保存持久化的线程状态），也不再是只读的（dev 环境下的编辑会写
文件）。这两点都是刻意如此。

---

## 3. 系统架构

```
┌──────────────────────────────┐   useStream (LangGraph protocol)   ┌───────────────────────────┐
│  Next.js UI (pure client)    │ ─────────────────────────────────▶ │  LangGraph Server         │
│  @langchain/react useStream  │   threads · node stream · state     │   assistant = serve graph │
│  React Flow · shadcn · TW v4 │ ◀───────────────────────────────── │   (langgraph.json)        │
│                              │                                     │   ── custom routes ──     │
│                              │   GET /schema /graph /corpus /health│   presenter view models   │
│                              │   POST /corpus/edit                 │   POST /corpus/edit       │
└──────────────────────────────┘                                     │   ── graph nodes ──       │
                                                                     │   route→retrieve→gen→     │
                                                                     │   guardrail→execute→stamp │
                                                                     └────────────┬──────────────┘
                                                                        threads/checkpoints
                                                                     ┌────────────▼──────────────┐
                                                                     │ ephemeral (langgraph dev) /│
                                                                     │ Postgres (deployed)        │
                                                                     └────────────────────────────┘
                                     data source (per profile): SQLite | Postgres | Redshift
```

- **一个后端、一个 base URL：** LangGraph Server 同时承载 serve graph（chat）
  *与*自定义的读/编辑路由。前端将 `useStream` 指向它以进行 chat，并对
  schema/graph/corpus/edit/health 等自定义路由发起 `fetch`。
- **线程 = 持久化。** 对话历史就是运行时的持久化线程状态——近期不需要单独的
  chat 数据库。在 `langgraph dev` 下是临时的；自托管/部署后为持久化的
  Postgres。（如果需要本地持久化，则是一个 checkpointer 配置项——见 §7。）
- **引擎保持配置驱动**：connector（数据源）、corpus、模型与环境均通过注入
  方式提供，因此同一个 server 可以运行三种运行档位（见 §11）。

---

## 4. 代码仓库

两个仓库：

- **`governed-bi`（本仓库）**——引擎 + LangGraph app + 自定义路由：
  ```
  langgraph.json                 # points the server at the serve-graph factory
  src/governed_bi/api/
    graph_app.py                 # graph factory for the LangGraph runtime (checkpoint-safe state)
    routes.py                    # custom routes (schema/graph/corpus/edit/health) mounted on the server
    stack.py                     # config-driven serve stack (exists; feeds the factory)
    schemas.py                   # pydantic response models (exists; extend for graph/edit)
  src/governed_bi/viz/presenter.py   # UI-agnostic view models (exists; add corpus_graph)
  pyproject: agents (langgraph/langchain), api (custom routes), tracing (langfuse)
  ```
- **`governed-bi-ui`（新建）**——Next.js 应用（App Router；`@langchain/react`、
  React Flow、shadcn、Tailwind v4）。其布局请参见交接文档。

---

## 5. 后端（本仓库需要构建的部分）

目前已构建的状态（来自上一阶段）：`presenter` 视图模型、读取端点、`stack`
工厂、pydantic schemas，以及离线测试。需要按上文重构：

1. **LangGraph app + `langgraph.json`。** 一个由 server 实例化的 graph
   factory（部署依赖来自 `stack`）。Chat 由运行时提供服务；`useStream` 消费
   节点更新。
2. **`ServeState` 的可序列化性重构** *（真正的工作量所在）。* 目前 graph
   state 中存放着一些活对象（`networkx` 图、gateway 许可清单、pydantic 的
   `retrieval`/`context`/`generated`）。LangGraph Server 会对 state 打检查点
   （checkpoint），因此被持久化的 state 必须是可序列化的——把重量级对象移出
   被检查点记录的 channel（作为部署依赖持有 / 按节点重建），只持久化消息 +
   轻量级结果。必须保留 graph 与 `answer_question` 之间的等价关系（测试会
   断言这一点）。
3. **阶段标签化。** 将节点名映射为带标签的阶段（`route`→"Routing"、
   `retrieve`→"Retrieving"、`generate`→"Generating SQL"、`guardrail`→"Checking
   guardrails"、`execute`→"Executing"、stamp/narrate→"Composing"），以形成一个
   稳定的 UI 契约。修复循环 = `generate`/`guardrail` 重新触发。（更丰富的
   逐条护栏细节，将来通过 LangGraph 的 `stream_mode="custom"` 提供。）
4. **自定义路由**（挂载在 server 上）：`GET /capabilities`、`/health`、
   `/schema`、`/graph`（完整知识图谱）、`/corpus/assets`；
   `POST /corpus/edit`。这些路由序列化 `presenter` 的视图模型（大部分已
   构建）。
5. **`presenter.corpus_graph()`**——从仅覆盖 table+join 扩展为一个覆盖所有
   资产类型及其引用关系（table、column、metric、term、join、note、
   few-shot、negative）的可过滤知识图谱。
6. **`POST /corpus/edit`**——解析 → `validate_corpus`（有 finding 就拒绝）
   → 在 `dev` 环境下，通过 `corpus.serialize.dump_asset`/`write_corpus`
   写入 YAML，返回校验结果与 diff；`can_edit` 只有在 dev 环境（或显式设置的
   flag）下才为 true。Prod 的 PR 路径推迟实现。
7. **Tracing**——Langsmith 通过环境变量启用（LangGraph 原生支持）；Langfuse
   通过挂在模型/graph 上的一个 LangChain `CallbackHandler` 实现，该 handler
   由新增的 `tracing` extra 提供。二者都只在各自的 key 被设置时才激活。
8. **兜底方案**——为离线/无 `agents` 的运行档位保留一个非流式的
   `POST /chat`（即普通的 `answer_question`）；由 `/capabilities.can_stream`
   告知 UI 该使用哪一种。

---

## 6. 前端

- **Next.js（App Router）+ React 19 + TypeScript（strict）**，**Tailwind
  v4**（CSS-first 的 `@theme`），**shadcn/ui**，**React Flow**（知识图谱），
  **zod**。
- **Chat** 通过 **`@langchain/react` 的 `useStream`**（`apiUrl` = LangGraph
  Server，`assistantId` = graph 名称）。该 hook 免费提供了响应式消息、
  **节点/阶段事件**（实时进度）、线程状态（历史记录），以及重连能力。
- **路由表：** `/` Chat · `/schema` Schema 与知识图谱 · `/corpus` 资产
  （Assets）+ notes（当 `can_edit` 为真时支持行内编辑）· `/health` 审计/health。
- 配置项：`NEXT_PUBLIC_LANGGRAPH_URL`（server 地址）、
  `NEXT_PUBLIC_ASSISTANT_ID`。非 chat 的读取/编辑请求会打到同源的自定义
  路由。客户端中不含任何密钥。

---

## 7. 持久化（线程）

- 对话历史**就是** LangGraph 的线程/检查点状态——`useStream` 按 id 加载/
  重新加入一个线程。近期不需要单独的对话数据库。
- **本地环境（`langgraph dev`）是临时的**；如果需要本地持久化，则是一个
  checkpointer 配置项（例如在自托管运行中使用 Postgres/SQLite saver）。
- **部署环境** = Postgres（自托管 `langgraph up`，或托管的 LangGraph
  Platform）。
- 一个轻量的应用元数据数据库（线程标题、标签）只有在运行时自带的线程元数据
  不够用时才需要——留待以后再评估。

---

## 8. Chat 交互体验

- `useStream` 驱动整个对话记录（transcript）。提交问题后，渲染用户回合，
  以及一个助手回合——随着节点事件陆续到达，展示**实时的带标签阶段**
  （Route → Retrieve → Generate SQL → Guardrails → Execute → Compose；修复
  会以重新触发的形式展示），最后呈现最终答案。
- **答案卡片：** 双轴标记以两个徽章的形式呈现（`safety_clearance` +
  `semantic_assurance`；档位标签绿/黄/红），英文答案本身，一个可折叠的
  **结果表格**，只读的 **SQL**，以及一个**溯源/审计抽屉（drawer）**。拒答时
  只展示升级说明（escalation），不展示 SQL/数字。

---

## 9. Schema 与知识图谱视图

- 基于 `GET /graph` 的**知识图谱**（React Flow）：节点按资产类型区分
  （schema/table/column/metric/term/join/rule/few-shot/negative），边 = 引用关系；
  提供**按类型划分的过滤器/图层**以控制密度；低置信度的 join 以及
  suspect/excluded 的资产会以不同样式呈现。点击 → 从 `GET /schema` /
  `GET /corpus/assets` 获取详情。
- **表浏览器：** 列及其类型、角色（role）、`suspect`/`excluded` 徽章、
  样例值、溯源信息。

> **D15（Multi-Schema Serving）。** D15 新增了一个 schema 命名空间层级
> （`schema` → `table`；`corpus/<schema>/`），因此知识图谱多出一个 **schema
> 分组/图层**，且经策展的**跨 schema join**会获得独立的样式/过滤器——按 D15，
> 跨 schema join 仅限策展来源、且仅限 Postgres。

---

## 10. 编辑

只有当 `capabilities.can_edit` 为真时，UI 才会展示编辑相关的操作入口
（affordance）。后端按 `Environment` 执行相应的改动：**dev → 写入 YAML
文件**（先校验）；**prod → 开启一个 PR**（推迟实现）。UI 自身绝不直接写
文件——"Git 是唯一的真相源"这一原则依然成立。

---

## 11. 部署——三种运行档位（仅为配置差异）

| 运行档位 | UI | 运行时 | 数据 | 持久化 | 编辑 |
|---|---|---|---|---|---|
| **local-dev** | `next dev` | `langgraph dev`（本地） | SQLite（仓库内） | 临时线程 | 写文件 |
| **public-demo** | Vercel | 托管的 LangGraph Server | 内置 SQLite | Postgres 线程 | 关闭 |
| **internal** | 自有主机 | LangGraph Server（自托管 / Platform） | Postgres/Redshift | Postgres 线程 | PR |

---

## 12. 可观测性、安全与成本

- **Tracing：** Langfuse + Langsmith，由环境变量门控（未设置 key 就不产生
  trace）。
- **密钥：** 模型 key 以及 Langfuse/Langsmith 的 key 都放在 server 端；
  绝不出现在客户端。CORS 只允许 UI 的 origin。
- **推迟事项：** 公开演示的 LLM 成本/滥用策略（预算内实时调用 / 离线 /
  门控）——需要在公开部署前做出决定。

---

## 13. 待定决策

1. 公开演示的模型策略（成本/滥用）——推迟。
2. LangGraph Server 的托管目标（自托管 `langgraph up` 对比托管的
   LangGraph Platform）+ dev 环境下是否需要持久化的本地线程。
3. 美学方向（建议采用以深色为主的技术仪表盘（instrument）风格）。
4. 公开的读取/聊天访问方式（开放 vs 共享 token vs 限流 + 机器人检测）。
5. 应用元数据数据库（只有在线程元数据不足时才需要）。

---

## 14. 构建阶段

1. **后端重构** *（本仓库，下一步）：* `langgraph.json` + graph factory +
   `ServeState` 重构；自定义路由；`presenter.corpus_graph()`；
   `/corpus/edit`（dev）；tracing。保持离线测试全绿；为自定义路由重新生成
   OpenAPI。
2. **UI 骨架**——Next.js + Tailwind v4 + shadcn；`useStream` 接入 server；
   `/capabilities` 门控。
3. **Chat**——实时阶段 + 答案卡片 + 溯源抽屉（线程 = 历史记录）。
4. **Schema 与知识图谱**——React Flow + 详情。
5. **Corpus + health + 编辑**（dev）。
6. **公开部署**——Vercel UI + 托管的 LangGraph Server；解决 §13 中的问题。

---

## 附录——ADR

- [0001 — Chat 通过 LangGraph Server + `useStream` 提供服务](adr/0001-langgraph-server-chat-runtime.zh.md)
  （线程 = 持久化；非图端点作为自定义路由）。
- 接下来值得记录的候选项：配置驱动的运行档位；dev 环境下的直接写文件编辑
  （相对于 UI 自持写入）。
