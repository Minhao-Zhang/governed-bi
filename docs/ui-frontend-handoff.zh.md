# 前端交接文档 — governed-bi UI

_[English](ui-frontend-handoff.md) · [简体中文](ui-frontend-handoff.zh.md)_

**governed-bi** 前端的构建简报 + 契约。请与 [ui-frontend-design.md](ui-frontend-design.zh.md)
中的架构依据，以及 [ADR 0001](adr/0001-langgraph-server-chat-runtime.zh.md) 中的运行时决策
配合阅读。

> **状态：契约是 TARGET（既定目标）；后端重构正在进行中——尚不能据此开发。**
> 聊天运行时会迁移到由 **`useStream`** SDK 消费的 **LangGraph Server**，corpus/schema/
> 审计作为该服务器上的**自定义路由**，外加一个开发用的编辑端点和一个**完整知识
> 图谱**。参见"当下已实现 vs. 规划中"（§8）。现在就可以开始搭建脚手架（技术栈、
> 布局）；等后端重构落地后再接入真实数据。

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
  `/capabilities` 会报告 `has_live_model`、`can_stream`、`can_edit`、`environment`、
  `dialect`。

---

## 3. Chat —— 通过 `useStream`（LangGraph 协议）

```tsx
const stream = useStream<ServeState>({
  apiUrl: process.env.NEXT_PUBLIC_LANGGRAPH_URL!,
  assistantId: process.env.NEXT_PUBLIC_ASSISTANT_ID!,
});
```
- **消息/历史记录：** `stream.messages`（以线程为后盾；可按线程 id 重新加载/重新
  加入）。
- **实时步骤：** 从流中消费节点/阶段事件，并渲染带标签的阶段：**路由（Route）→
  检索（Retrieve）→ 生成 SQL（Generate SQL）→ 护栏（Guardrails）→ 执行（Execute）
  → 组装（Compose）**（修复会表现为 `generate`/`guardrail` 的重新触发）。这是
  *真实的*后端进度，不是一个计时器。
- **最终答案**以终态的形式到达；渲染出**答案卡片**：
  - 两个徽章，而不是一个分数：`safety_clearance`（布尔值）+ `semantic_assurance`
    （`certified|heuristic|unverified|none`）；档位标签为绿色/黄色/红色。
  - 英文答案文本；可折叠的**结果表格**（`columns`/`rows`，含截断提示）；只读的
    **SQL**；**溯源/审计抽屉**（route、tables_used、join_ids、min_join_confidence、
    attempts、uncertainty_flags 等）。
  - 拒答 → 显示升级提示，不给出 SQL/数字。

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
| `GET /graph` | **完整知识图谱**，即所有资产类型（table/column/metric/term/join/rule/few_shot/negative）之上的 `{ nodes, edges }` + 引用；可按 `node.kind` 过滤/分层；连接携带 `confidence`/`cardinality`/`low_confidence` |
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

- **已实现（更早阶段，离线测试过）：** `presenter` 视图模型、REST 读取端点（作为
  一个独立的 FastAPI）、`stack` 工厂、一个**表+连接（tables+joins）**图谱、一个非
  流式的 `/chat`、8 个 API 测试。
- **规划中（本次重构，真正交接之前必须完成）：** LangGraph Server +
  `langgraph.json`、`ServeState` 的可序列化性、节点→阶段的流式传输、**完整知识
  图谱** `/graph`、`POST /corpus/edit`（dev）、自定义路由的挂载、Langfuse/Langsmith
  追踪、重新导出的 OpenAPI。
- **推迟：** prod 环境下的 PR 编辑、公开演示的成本策略、鉴权/RLS。

现在就搭建 UI 的脚手架（技术栈、路由、`useStream` 骨架、组件外壳）；等规划中的
各项落地后再绑定到真实端点。

---

## 9. 建议的构建顺序

1. 搭建 Next.js + Tailwind v4 + shadcn 的脚手架；把 `useStream` 接到本地的
   `langgraph dev`；读取 `/capabilities`。
2. **Chat**：实时阶段 + 答案卡片 + 溯源抽屉（线程 = 历史记录）。
3. **Schema 与知识图谱**：React Flow + 详情。
4. **Corpus + 健康度**；**编辑**（dev，以 `can_edit` 为门槛）。
5. 部署：Vercel UI + 托管的 LangGraph Server；解决设计文档 §13 中的未决事项。
