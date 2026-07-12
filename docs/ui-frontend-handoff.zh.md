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
- 追踪（可选）：设置 Langsmith（`LANGSMITH_API_KEY`、`LANGCHAIN_TRACING_V2=true`）
  和/或 Langfuse（`LANGFUSE_*`）密钥；未设置时不生效。
- CORS：允许 UI 的来源（`http://localhost:3000`）。
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
| `GET /capabilities` | `{ environment, dialect, can_edit, edit_mode, can_stream, has_live_model, model }`——据此控制 UI 功能的启用 |
| `GET /health` | corpus 健康度：计数、`ci_green`、问题项、`n_suspect_columns`、`n_excluded`、`n_low_confidence_joins` |
| `GET /schema` | 表 + 列（类型、角色、`reliability`、`excluded`、溯源） |
| `GET /graph` | **ER 图**,表节点 + 连接边的 `{ nodes, edges }`(节点带 `row_count`/`n_columns`/`has_suspect`;边带 `on`/`cardinality`/`confidence`/`low_confidence`) |
| `GET /knowledge-graph` | **完整知识图谱**,覆盖每种资产(table/join/metric/term/rule/few_shot/negative_example)的 `{ nodes, edges }`;边带类型 `join`/`measures`/`grounds`/`related:*`/`scopes`/`exemplifies`;按 `node.kind` 过滤/分层(表 + 连接即还原 ER 视图)。列在 `/schema` 里,这里不作为节点 |
| `GET /corpus/assets?type=` | 非 table 资产（metric/term/join/rule/few_shot/negative） |
| `GET /skills` | skills（markdown） |
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

## 10. 多 schema 服务（已决定 —— D15，尚未落地）

引擎正转向**一个数据库容纳多个 schema**，并支持可执行的**跨 schema 连接**
（[design-decisions.md](design-decisions.zh.md) D15）。这是一个**已决定的方向，
尚未落地**：上文的当前契约——以及 [openapi.json](openapi.json)——仍使用扁平的
`db` 字段，并只服务单个 schema。现在请照当前契约开发；把本节视为对前端自己那份
`DESIGN_QUESTIONS.md` 中导航方案的后端答复。

预期的契约变化（请与发布协同一致）：

- **`db` → `schema` 字段更名。** `TableResponse.db` 与 `SkillResponse.db` 变为
  `schema`，ER/知识图谱节点也携带 `schema`。这是**唯一对外可见的 OpenAPI 破坏性
  变更**——请在引擎发布时于 UI 侧同步更名。**不存在**单独的 `db`/连接层级
  （数据库是服务端配置常量），因此导航主干是**单条 schema 边栏**，而不是两级的
  `db → schema` 树。
- **按需划定范围，而非整仓 dump。** 前端提议的精简、可划范围、分页端点
  （`/schema/summary?schema=`、`/schema/{id}`，以及图上的
  `?schema=&focus=&radius=&node_budget=`）被采纳为目标，并以新的能力标志为门槛。
  以搜索优先的落地页加客户端 Fuse 索引为默认；服务端 `/search` 仍推迟。
- **跨 schema 连接是可导航、可执行的关系，而非警告。** 由于只有一个数据库，跨
  schema 连接*确实*能执行，因此前端的 Q7 反转了：把它渲染为可以进入的普通边界，
  而不是治理警告。旧的跨*数据库*警告情形在这里不存在。
- **拒答是一等的答案状态。** 当没有已策展的关系为某个问题连接两个 schema 时，引擎
  会**拒答**，而不是硬造一个连接（D15）。请像现有拒答一样呈现它（升级提示，不给
  SQL / 不给数字），并可选地作为一个通过澄清循环请求该关系的入口。
- **新增能力标志**（`can_scope`、`can_search`）让 UI 点亮新流程，并在面对 D15
  之前的引擎时回退到当前的扁平行为。

以上都不触及聊天传输或答案卡片；它重塑的是 **Schema 标签页**的导航，并更名一个字段。
