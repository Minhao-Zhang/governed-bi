# 前端交接文档 — governed-bi UI

_[English](ui-frontend-handoff.md) · [简体中文](ui-frontend-handoff.zh.md)_

**governed-bi** 前端的构建简报 + 契约。请与 [ui-frontend-design.md](ui-frontend-design.zh.md)
中的架构依据，以及 [ADR 0001](adr/0001-langgraph-server-chat-runtime.zh.md) 中的运行时决策
配合阅读。

> **状态:后端重构已落地,本契约已生效。** 聊天由一台 **LangGraph Server**(图
> id 为 `serve`)提供,前端用 **`useStream`** SDK 消费;corpus/schema/审计作为同一
> 台服务器上的**自定义路由**,再加一个开发用编辑端点和一张**完整知识图谱**。用
> `langgraph dev` 启动它(§2),直接据此开发即可。实现细节见
> [langgraph-rework-plan.md](langgraph-rework-plan.zh.md)。

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
- **实时步骤:** 图每个阶段发一条**自定义事件**,经 `onCustomEvent(data, { mutate })`
  这个选项送达(该次 run 的 `streamMode` 必须含 `custom`)。渲染带标签的进度轨:
  **路由(Route)→ 检索(Retrieve)→ 生成 SQL(Generate SQL)→ 护栏(Guardrails)→
  执行(Execute)→ 组装(Compose)**;修复表现为 `generate`/`guardrail` 带更高的
  `attempt` 再次触发,`guardrail` 事件带 `passed` 和 `failed_layer`。这是*真实的*
  后端进度,不是计时器。
- **最终答案**是一个自定义的 **`answer` state 通道**:读 `stream.values.answer`
  (即 `AnswerResponse` 的形状)。渲染出**答案卡片**:
  - 两个徽章，而不是一个分数：`safety_clearance`（布尔值）+ `semantic_assurance`
    （`certified|heuristic|unverified|none`）；档位标签为绿色/黄色/红色。
  - 英文答案文本；可折叠的**结果表格**（`columns`/`rows`，含截断提示）；只读的
    **SQL**；**溯源/审计抽屉**（route、tables_used、join_ids、min_join_confidence、
    attempts、uncertainty_flags 等）。
  - 拒答 → 显示升级提示，不给出 SQL/数字。

包说明:`@langchain/langgraph-sdk/react` 提供 `onCustomEvent` 和 `stream.values`;
更新的 `@langchain/react` 超集另加选择器 hook(`useChannel`)和 `stream.respond`。
两者都能对接本服务器。

（engine 还保留了一个非流式的 `POST /chat` 回退方案，用于没有 `agents` 可选依赖组
的离线模式；`capabilities.can_stream=false` 会选中它。）

---

## 4. 自定义路由（REST，位于同一服务器上）

使用 `fetch` 从 `NEXT_PUBLIC_LANGGRAPH_URL` 请求这些路由。数据形态与
`governed_bi.viz.presenter` 保持一致；重构完成后会重新导出一份机器可读的 schema。

| 方法 + 路径 | 用途 |
|---|---|
| `GET /capabilities` | `{ environment, dialect, can_edit, edit_mode, can_stream, can_scope, can_search, has_live_model, model }`——据此控制 UI 功能的启用 |
| `GET /health` | corpus 健康度：计数、`ci_green`、问题项、`n_suspect_columns`、`n_excluded`、`n_low_confidence_joins` |
| `GET /schema` | 表 + 列（类型、角色、`reliability`、`excluded`、溯源）。命名空间字段为 **`schema`**。可选 `?schema=&limit=&offset=`（无参 = 全量 dump）。**不接受 `?db=`。** |
| `GET /schema/summary?schema=&limit=&offset=` | **精简目录** `{ total, items }`，供虚拟化列表 + 客户端搜索索引使用；每个 item 为 `{ id, physical_name, schema, row_count, n_columns, excluded, has_suspect, provenance_status, columns:[{physical_name, physical_type, role, reliability, excluded}] }`（重字段已丢弃；`total` 为分页前计数） |
| `GET /schema/{table_id}` | 单张表的**完整** `TableResponse`（含 `schema`），在打开详情时惰性拉取；未知 id 返回 `404` |
| `GET /graph` | **ER 图** `{ nodes, edges, boundary?, meta? }`（节点带 `schema`/`row_count`/`n_columns`/`has_suspect`；边带 `on`/`cardinality`/`confidence`/`low_confidence`）。可选 D15 划范围：`?schema=&focus=&radius=&node_budget=`——划范围响应含 `boundary` + `meta`（回显 `scope` 供 `engineScopeMatches`）。无参 = 全图 |
| `GET /knowledge-graph` | **完整知识图谱** `{ nodes, edges, boundary?, meta? }`；表节点带 `schema`。与 `/graph` 相同的划范围参数，外加 `?kinds=`（逗号分隔） |
| `GET /corpus/assets?type=` | 非 table 资产（metric/term/join/rule/few_shot/negative） |
| `GET /skills` | skills（markdown）；每条带 **`schema`**。部分 corpus 可能为空——形状仍以 OpenAPI / zod 为准 |
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
  工厂、非流式 `/chat` 回退。
- **推迟:** prod 的 PR 编辑(如今 dev 是直接写文件)、公开演示的成本策略、鉴权/RLS、
  人审中断(运行时已支持,经 `stream.interrupt` + `submit(command.resume)`)。

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
> - `DataSourceConfig.db`（BIRD db_id / 默认写入子树）仍与 Postgres pin 字段
>   `schema` 分开。

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
