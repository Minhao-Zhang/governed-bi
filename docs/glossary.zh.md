# Agentic BI 术语表

_[English](glossary.md) · [简体中文](glossary.zh.md)_

[Agentic BI System](system-overview.zh.md)的标准术语。当下文术语与某处的描述方式冲突时，以下文术语为准。

> **弃用词汇**
>
> 不使用 UDH.ai 的术语：`category` → **治理数据集**；`fabric object`
> → **治理数据集**（可选择物化）；`app_ci` → gateway 的
> 执行目标。
>
> 本次[术语重构](plans/terminology-refactor.md)还弃用了以下词汇：`A1` /
> `A2` / `A3`（→ `baseline` / `curated` / `curated_sme`）；`gold` 臂 /
> `build_gold_corpus`（→ `ceiling`，已设计尚未构建）；作为独立臂存在的
> `no_layer` 与 `facts_only`（并入 `baseline`）；作为可靠性标记取值的
> `certified`（→ `grounded`——`ProvenanceStatus.certified` 与指标的
> `draft→certified` 生命周期不受影响）；旧的单轴档位 `governed` /
> `lineage` / `fenced_raw` / `refused`（如仍出现，也只作为双轴标记的展示层
> 投影保留）；作为服务代理的 `Server`（→ **Analyst**；"server" /
> "LangGraph Server" 仍只指基础设施）；`flow` / `flow_solver`；
> `DataSourceConfig.db`（→ `corpus_pin`）。

| 术语 | 定义 |
|---|---|
| **领域**（Domain） | 智能体所服务的一个业务领域（例如销售、支持、库存）。 |
| **治理数据集**（Governed dataset） | 针对某个领域相关问题的标准、单一权威来源的*逻辑*模型。粒度、实体、列、连接和数据清洗过滤条件只需一次性定义。物化视图只是一种可选的物理优化，并非定义本身。 |
| **指标**（Metric） | 基于治理数据集编译得到的度量/维度，在任何地方计算都得到同一个数字。是通过认证的单元（遵循 SemVer 版本号，从 draft 到 certified）。 |
| **语义层**（Semantic layer） | 编译产出的各类定义：治理数据集 + 指标 + 术语/业务规则解析。归人类所有，是权威来源。 |
| **技能 / 参考文档**（Skill / reference doc） | 按领域组织的 Markdown 程序性与描述性知识（路由规则、易错点、查询模式）。 |
| **Corpus**（语料层） | 共享的、人工所有基底的统称：语义层 + 技能 + 元数据/血缘 + 持久化记忆内容。 |
| **Gateway**（网关） | 只读、强制执行策略的数据访问边界：凭证隔离、RLS-as-user（以用户身份执行的行级安全）、强制的 LIMIT/超时、审计/重放。是访问数据的唯一路径。 |
| **Curator**（构建代理） | 离线探索型代理，*生成*corpus（自举 + 漂移修复）。生产环境下的写入需经人工把关。 |
| **Analyst**（服务代理） | 在线的治理型代理，*使用*corpus 来回答问题。失败即拒（fail-closed）、可审计。原名"Server"；如今"server"/"LangGraph Server" 仅指基础设施。 |
| **工具**（Tool） | 模型可自行决定调用的编码函数。 |
| **钩子**（Hook，中间件） | 在循环事件上触发的确定性代码，用于注入上下文和/或否决动作。 |
| **记忆**（Memory） | 设计上的四类存储（架构 §7）：**Working**（已实现，会话级）外加三类默认关闭、仅在评估证明其价值后才按域采用的持久存储——**Profile**（用户画像）、**Episodic**（情景）、**Correction**（纠错）。目前仅 Working 已实现；Episodic/Correction 为尚未实现的协议接缝；Profile 仅有配置（路由预算 + `profile_ttl_days`，尚无存储接缝——优先级最低的持久存储）。 |
| **Working memory**（工作记忆） | 按会话逐字保留的上下文（checkpointer）。是临时性的，按身份限定作用域。 |
| **治理路径**（Governed path） | 从语义层出发回答问题（默认方式）。 |
| **探索路径**（Discovery path） | 针对语义层未覆盖的问题进行受限的原始数据探索。 |
| **晋升循环**（Promotion loop） | 经人工评审后，将发现的模式提炼为已认证的治理数据集/指标。 |
| **语义平面 / 数据平面**（Semantic plane / data plane） | 离线含义（通过 PR/CI 发布）与在线执行（受护栏把关）的对照。 |
| **反例**（Negative example） | 一种经人工整理的模式，用于标记某类问题在当前数据下无法回答，并触发预先设定的升级处理流程。 |
| **可靠性标记**（Reliability stamp） | 对已交付答案的双轴标记（D5）：`safety_clearance`（布尔硬关卡）与 `semantic_assurance`（`grounded` / `heuristic` / `unverified`——有据程度）。`grounded` 意为安全 + 在范围内，**而非**已验证正确；阈值尚未校准（审计处置 R2）。 |
| **可靠性警示**（Reliability caveat） | 由 AI 推断、写在*列*上的自由文本警示，说明该列可能不可靠（`UNRELIABLE. DO NOT USE` 加上原因说明）。属于 corpus 一侧、由 curator 撰写的内容，与答案一侧的**可靠性标记**不同。它取代了带类型的诱饵标志，从而使该机制可以迁移到企业级部署中。 |
| **治理排除**（Governance exclusion） | 人工在列/表上设置的 `governance.excluded` 布尔字段，含义是“永不呈现”：该资产会从 **Analyst** 能看到的一切内容中移除，覆盖所有环境，且是永久性的。由人工撰写（D6），与 curator 通过 AI 推断得到的**可靠性警示**不同。 |
| **交互信号**（Interaction signal） | 对某次已服务答案上用户动作的一条记录——一个**纠正信号**、一次重新表述的追问、一次重新生成、一次放弃，或一个显式评分——用于*评估*（生产质量，对一系列指标运行）与*开发*（被动改进语义层）。**原始**记录（先记录），置信度分级/解释推迟到真实使用显示出哪些信号与错误答案相关之后。v0 依托 Langfuse/LangSmith 的轨迹反馈；一个专用、可查询、以 turn + corpus-release 哈希为键的交互日志是未来工作。 |
| **纠正信号**（Correction signal） | **交互信号**的高置信子类：由*用户发起*、指出答案在某个具体可命名之处出错的观察（例如“营收应剔除退款”）。不同于**澄清问题**（由 curator 发起、*提给*人类）与**纠正记忆 / Correction memory**（一种存储）。纠正信号是一个*假设*：必须先对查询验证、并通过人工 PR 把关，才能改动 corpus——绝非自动编辑。 |
| **澄清问题**（Clarification question） | 由 curator 提出、带 ID 追踪的、针对某个 corpus 资产的开放问题（例如“被重命名的列 `kunde_id` 是什么含义？”），等待 **Responder** 作答。它不同于**可靠性警示**（后者是 curator 自己的判断）：澄清问题是*提给人类*的，并期待对方给出回答。 |
| **Responder**（应答者） | 一个可插拔的角色，负责用*自由文本*外加可选的资源来回答**澄清问题**，从不做结构化编辑。它有两种实现，且都在引擎核心之外：生产环境中的人类 **SME**，以及评测中的 **Simulated SME**。 |
| **SME**（领域专家） | 生产环境中的人类 **Responder**：一位非技术的领域专家，用自由文本回答**澄清问题**。从不直接编辑 corpus，也不直接提 PR。 |
| **澄清答复**（Clarification answer） | **Responder** 针对**澄清问题**给出的自由文本回复（外加可选的资源）。在它进入 git 之前，会有一个*解析步骤*（由 curator/LLM 或一位数据工程师完成）把它转换成结构化的 corpus 编辑。其中的资源会落地为 `source_refs`。 |
| **Simulated SME**（模拟领域专家） | 评测框架中的一种 **Responder**：一个 LLM，被告知某个数据集的*领域含义*，逐个回答**澄清问题**，从不会拿到留出**测试**问题的 gold SQL。拉取式（只回答 curator 所问）。驱动 `curated_sme` 臂与 `ceiling`。 |
| **执行准确率**（Execution accuracy，EX） | 智能体的结果与 gold 一致，通过重新执行 gold SQL 加以验证。 |
| **治理路径遵循率**（Governed-path adherence） | 通过语义层（而非原始表）解决的问题占比。 |
| **诱饵触碰率**（Decoy-touch rate） | 智能体使用了清单标记的虚假列/表的问题占比。 |
| **基线**（Baseline，评测下限） | 确定性的、由脚本构建的 corpus——表/列名称、类型、**样本值**、FK 候选——**没有 curator LLM**、也**没有从训练集 SQL 派生**的资产。通过与其他所有臂相同的 **Analyst** 路径提供服务。隔离出“一个脚本能从数据库了解到什么”。取代了旧的原始转储无语义层臂**以及** facts-only 那一行。 |
| **策展臂**（Curated arm） | `baseline` 加上 curator 撰写的 LLM **Inference 层**（描述、可靠性警示、术语、指标）**以及**从训练集 SQL 派生的资产（种子连接、few-shot）。`baseline → curated` 隔离出语义层带来的增量。 |
| **策展+SME 臂**（Curated+SME arm，`curated_sme`） | `curated` 加上一轮或多轮 Simulated-SME 澄清。增长轴。 |
| **可恢复上限**（Recoverable ceiling，`ceiling`） | 虚线上界：一个测试感知的 Simulated SME，把留出测试问题 + evidence（绝不含测试 gold SQL）纳入其检索索引。**刻意泄漏的预言机**，与公平臂隔离。取代已退役的去混淆 "gold" 臂。已设计，尚未构建。 |
| **Schema**（命名空间） | 一次运行所连接的那个数据库内部的单级命名空间（D15）：一个 YAML 子树（`corpus/<schema>/`）加上逐资产的 `schema` 字段。一次运行的数据库本身是连接配置（`corpus_pin`），不是 corpus 的一个层级。 |
| **跨 schema 关系**（Cross-schema relationship） | 两个端点位于*不同* schema 的 `join` 资产。**只靠策展得到**——由 **SME** 声明、从示例 SQL 蒸馏、或从使用中挖掘；绝不从数据库外键探测、也不从名称猜测。若没有这样的资产，引擎会**拒答**该跨 schema 问题，而不是硬造一个连接（D15）。 |
| **Schema 路由器**（Schema router） | 检索的前置阶段（D15），在表检索之前先筛选出与问题相关的 schema，使跨越众多 schema 的成千上万张表仍可处理。它**连接感知**：沿着已策展的跨 schema 连接扩展，使位于某个未被提及 schema 的桥接表不被丢弃。 |
| **限定标识符**（Qualified identifier） | 一个完全限定的 `schema.table`（或 `schema.table.column`）引用。在**多 schema 模式**中贯穿始终——检索、护栏许可集、生成的 SQL 与执行。单 schema 路径保持**裸的/未限定**（D15 按模式区分的限定规则，用以保护 SQLite/BIRD 被评分的那条路径）。 |
| **多 schema 模式**（Multi-schema mode） | 连接器覆盖该单个数据库内每一个 schema、且跨 schema 连接可执行的运行模式（v0 仅限 Postgres/Redshift）。与*单 schema* 模式（SQLite，或一个固定的单 Postgres schema）不同，后者保持不变并输出裸 SQL。由一个显式信号选择，绝不以 `schema` 未设置为判据。 |
