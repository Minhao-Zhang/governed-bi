# Agentic BI 系统

_[English](system-overview.md) · [简体中文](system-overview.zh.md)_

> **这是什么**
>
> 一个 agentic BI / Generative-BI 系统的设计：自然语言问题 → 基于企业关系型数据的
> 接地、受治理、可审计的答案。近期目标 = 一个**在 SQLite 上得到验证的展示项目**
> （个人 GitHub；对其他引擎留有方言可插拔接口），从一批已知良好的种子查询出发、
> 逐步扩展出一个可审阅的语义层——这是*种子辅助的生长*，而非零先验的冷启动。
> 企业抽象已内置于系统中，但处于关闭状态。在自建的
> [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) 数据集上
> 进行评测（执行准确率；记录成本）。一个私有的**企业分支(fork)**（第二阶段）
> 在企业规模上复用该引擎，面临同样的无人负责、无人力的处境。

## 要点

- 两个 harness 共享同一套基座：**curator**（构建 corpus）与**analyst**（负责应答）。语义层是护城河。失败即拒（fail-closed）。
- 设计文档：
    - [架构](architecture.zh.md)：完整设计
    - [设计决策](design-decisions.zh.md)：D1-D18（+ 2026-07-15 审计处置），含备选方案与权衡取舍
    - [资产模式](asset-schemas.zh.md)：按资产划分的 YAML 字段规范（Facts 层 / Inference 层 / Audit 层）
    - [Curator](curator.zh.md)：构建侧的 proposer + adversary 循环
    - [Analyst](analyst.zh.md)：服务侧的 agent + 护栏（[ADR 0002](adr/0002-governed-agentic-serve-runtime.md) 受治理 agentic 内核如今已是唯一的服务路径；此前的确定性流程已退役）
    - [Viz](viz.zh.md)：只读审计面(audit surface)——presenter 视图模型 + `governed_bi.api` HTTP API，用于浏览语义层 + 与 analyst 对话
    - [术语表](glossary.zh.md)：规范术语
- 本设计依据[外部设计来源](references.zh.md)。

## 状态

> **已决定(D1-D18 + 2026-07-15 审计处置)**
>
> 目标 · 治理单元 · 评测 · 评分 · 拒答 · 归属 · 身份 · 记忆 · corpus 契约 ·
> curator 关卡 · 外部评审 · 澄清协议 · corpus 独立仓库 · SME 成长基准 ·
> 多 schema 服务（单库、多 schema、可执行的跨 schema 连接）。参见[设计决策](design-decisions.zh.md)。

> **已构建（代码）**
>
> corpus (schemas / loader / validate / serialize) · 图投影 + Steiner 连接
> 规划器（基于内存的 networkx）· gateway + 五层护栏 · RVGD 检索（BM25 + 接地
> 扩展，外加一个由 embedder 门控、经 RRF 与 BM25 融合的向量通道）· 检索→上下文
> 组装 · [ADR 0002](adr/0002-governed-agentic-serve-runtime.md) 所定义的受治理
> agentic 服务内核（`analyst.agent`：确定性轨道 + `create_agent` + 治理中间件 +
> 只读工具），自 P2 切换以来它是唯一的服务路径 · 工作记忆 · 评测脚手架 · 只读的
> viz presenter 视图模型 + `governed_bi.api` HTTP API · 模型配置(`governed_bi.toml`)
> 以及 `ChatClient` / `Embedder` 扩展点（原生 OpenAI + LangChain + 确定性的离线
> 默认实现）· 基于 LLM 的 curator proposer（描述 + `suspect` 警示）·
> **deepagents curator harness**（`curator.deep_agent`，构造）。corpus / curator /
> 检索这一切片可以在无模型、无网络的情况下端到端运行，agent 路径的 CI 确定性则
> 来自一个 `FakeListChatModel` agent harness。（此前的确定性服务流程 `server.flow`
> 与那个陈旧、未被使用的 `server.graph` DAG 均已删除；如今没有实时模型时服务会
> 失败即拒：`build_stack()` 仍可离线构建以支撑只读审计 API，但服务进程会在
> 启动时报错，`/chat` 返回 503，直到配置好模型为止。）

> **待完成（代码）**
>
> 对 Inference 层剩余资产 (joins / terms / metrics / rules / notes) 的 LLM
> 撰写，以及逐资产、实际运行的 adversary `refute` · curator 的自评估 train-EX
> 循环 · **完整**规模的经混淆 BIRD 评测 jsonl（在数据到位之前，由内置的小型
> beer_factory 数据集充当替身；评测阶梯框架已在本地 Postgres 上以真实模型实际运行）
> · **D15** 多 schema 构建继续推进：线上
> 更名 + 多 schema 服务 + 缺失边拒答 + 服务端图划范围 + 磁盘 YAML `schema` 字段 +
> 连接感知 schema 路由器均**已落地**。仍推迟：服务端 `/search`（客户端 Fuse）。
> 旧的 `DataSourceConfig.db`（一个 schema pin）已并入 `corpus_pin`；此后又
> 重新引入了一个新的 `db` 字段（`config.py`），作为 `db:` 笔记范围哨兵
> （ADR 0003）的数据湖身份标识，与 `corpus_pin` 是两回事。缺少评测数据，
> 目前还无法通过这些
> 评测臂(arm)体现出护城河效应。

> **未决（设计层面）**
>
> - 可靠性推断信号：curator 究竟使用哪些证据（深化 Curator 第二阶段的内容）
> - 拒答关卡 + 负例(negative example)构建 + 留存的(held-out)不可回答问题集合
> - analyst 工具注册表（少而精）：具体的工具列表（流程见 [Analyst](analyst.zh.md)）
> - curator 的探索策略：探测式查询(probe-query)策略（对应的循环见 [Curator](curator.zh.md)）
>
> *搁置(parked)（开发层面，依照“design-first”原则）：* 构建顺序 / 关键路径。
> *已解决 → 归入笔记/决策：* 存储布局 (D9) · gold 自动推导 (D4) · train/test
> 划分 (§8) · corpus 模式（[资产模式](asset-schemas.zh.md)）· curator 循环
> ([Curator](curator.zh.md)) · analyst 流程 ([Analyst](analyst.zh.md)) · viz/审计
> ([Viz](viz.zh.md))。
