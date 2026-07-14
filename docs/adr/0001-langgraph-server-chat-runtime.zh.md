# 0001 — Chat 通过 LangGraph Server + `useStream` 提供服务

_[English](0001-langgraph-server-chat-runtime.md) · [简体中文](0001-langgraph-server-chat-runtime.zh.md)_

- **状态：** Accepted（已接受） (2026-07-10)
- **决策者：** 项目负责人 + 设计会议
- **相关文档：** [ui-frontend-design.zh.md](../ui-frontend-design.zh.md), [ui-frontend-handoff.zh.md](../ui-frontend-handoff.zh.md)

## 背景

governed-bi 的 UI 在 Chat 界面上需要：来自后端的**实时逐步进度**（当前正在运行
哪个受治理阶段，包括护栏/修复事件）、**持久化的对话历史**，以及一个丰富、可恢复
的 agent UI，同时保持答案本身不流式输出（答案很短）。serve 路径已经是一个
**LangGraph `StateGraph`**（`server.graph.build_serve_graph`），在答案上与朴素的
`answer_question` 等价。

向 Next.js 前端交付实时进度 + 持久化，有两种可行方式：

1. **LangGraph Server + LangChain 的 `useStream` SDK。** `useStream` 对接的是
   LangGraph Server 协议；编译后的 serve 图通过 `langgraph.json` 暴露出来。开箱
   即提供节点流式、可持久化的线程/检查点、中断、时间旅行（回溯），以及原生的
   LangSmith 追踪。
2. **自建 FastAPI + 手写 SSE。** 将 `graph.stream(stream_mode="updates")` 包装为
   一个 SSE 端点；前端手动消费它（而非 `useStream`），线程持久化、重连与状态语义
   都要由我们自己构建。

## 决策

采用**方案 1**：Chat 由**LangGraph Server** 提供服务，前端通过 **`useStream`**
消费。

- **线程 = 持久化。** 对话历史就是运行时的可持久化线程状态；近期不设
  单独的对话数据库（这取代了此前“前端拥有 Neon/Drizzle、无状态 API”的决定）。
- **非图端点作为自定义路由。** `/schema`、`/graph`、`/corpus`、`/health` 以及
  `POST /corpus/edit` 被挂载为*同一个* LangGraph server 上的自定义路由，这样前端
  只需要一个 base URL。此前独立的 FastAPI 工作成为这些路由。
- **实时进度 = LangGraph 节点更新**，在 server 端被映射为带标签的阶段，以形成
  一个稳定的 UI 契约。

## 影响

**积极方面**
- 实时的节点/阶段流式输出、可持久化且可恢复的线程、中断（未来通向人工关口 D6
  的路径），以及检查点/时间旅行（回溯），后者非常适合一个*受治理、可审计*的
  系统。
- 原生的 **LangSmith** 追踪；大幅减少定制化的前端管道代码（SDK 拥有线程状态、
  流式与重连）。
- 复用现有的 LangGraph serve harness，而不是另建一条并行的 serve 路径。

**负面 / 代价**
- ~~**`ServeState` 必须支持检查点序列化。**~~ **(实现阶段以另一种方式化解了。)**
  这次重构并没有去序列化那张多节点的 `ServeState`。取而代之,server 图是一张薄的
  **chat** 图,持久化的 state 只有 `{messages, answer}`(两者都能 JSON 序列化),整条
  受治理流水线塞进一个节点里跑,该节点调用 `answer_question` 并经
  `get_stream_writer()` 发阶段事件。那些重对象(`networkx` 图、许可清单、pydantic
  的 `retrieval`/`context`)只当节点内的局部变量,绝不进检查点,所以 `server/flow.py`
  和 `server/graph.py` 都没动,`answer_question` 与图的等价关系依然成立。见
  [langgraph-rework-plan.md](../langgraph-rework-plan.zh.md) 第 1 节。
  *（前向指引：这种确定性单节点的描述是本决策当时的运行时状态；serve 之后已
  切换到 [ADR 0002](0002-governed-agentic-serve-runtime.md) 所述的受治理
  agentic 核心，取代了 `answer_question`/`server/flow.py` 以及那张陈旧的
  `server/graph.py` DAG。）*
- **更重的部署。** 本地 `langgraph dev` 很容易上手，但是**临时性**的；可持久化
  存储需要 Postgres（自托管 `langgraph up` → Postgres + Redis，或者一个托管的
  LangGraph Platform）。对公开演示而言，一个纯粹的 FastAPI 方案原本会更轻量。
- 对 LangGraph Platform 约定的**运行时/供应商耦合**。
- 取代了“无状态 API + 前端拥有持久化”；离线 / 无 `agents` 的运行档位仍保留
  一个非流式的 `/chat` 兜底路径。

## 已考虑的备选方案

- **自建 FastAPI + 手写 SSE**：已否决。重新实现了线程、持久化、重连与流式
  语义，并放弃了官方 SDK、中断与时间旅行（回溯）。部署更轻量，但需要更多定制化
  的 UI/运行时代码，agent-UI 支持也更弱。
