# Agentic BI 术语表

_[English](glossary.md) · [简体中文](glossary.zh.md)_

[Agentic BI System](system-overview.zh.md)的标准术语。当下文术语与某处的描述方式冲突时，以下文术语为准。

> **弃用词汇**
>
> 不使用 UDH.ai 的术语：`category` → **治理数据集**；`fabric object`
> → **治理数据集**（可选择物化）；`app_ci` → gateway 的
> 执行目标。

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
| **Server**（服务代理） | 在线的治理型代理，*使用*corpus 来回答问题。失败即拒（fail-closed）、可审计。 |
| **工具**（Tool） | 模型可自行决定调用的编码函数。 |
| **钩子**（Hook，中间件） | 在循环事件上触发的确定性代码，用于注入上下文和/或否决动作。 |
| **记忆**（Memory） | 四类存储：Working（工作记忆）/ Profile（用户画像）/ Episodic（情景记忆）/ Correction（纠错记忆）。 |
| **Working memory**（工作记忆） | 按会话逐字保留的上下文（checkpointer）。是临时性的，按身份限定作用域。 |
| **治理路径**（Governed path） | 从语义层出发回答问题（默认方式）。 |
| **探索路径**（Discovery path） | 针对语义层未覆盖的问题进行受限的原始数据探索。 |
| **晋升循环**（Promotion loop） | 经人工评审后，将发现的模式提炼为已认证的治理数据集/指标。 |
| **语义平面 / 数据平面**（Semantic plane / data plane） | 离线含义（通过 PR/CI 发布）与在线执行（受护栏把关）的对照。 |
| **反例**（Negative example） | 一种经人工整理的模式，用于标记某类问题在当前数据下无法回答，并触发预先设定的升级处理流程。 |
| **可靠性标记**（Reliability stamp） | 溯源注脚中，对尽力而为的答案所加的来源档位与置信度标记。 |
| **可靠性警示**（Reliability caveat） | 由 AI 推断、写在*列*上的自由文本警示，说明该列可能不可靠（`UNRELIABLE. DO NOT USE` 加上原因说明）。属于 corpus 一侧、由 curator 撰写的内容，与答案一侧的**可靠性标记**不同。它取代了带类型的诱饵标志，从而使该机制可以迁移到企业级部署中。 |
| **治理排除**（Governance exclusion） | 人工在列/表上设置的 `governance.excluded` 布尔字段，含义是“永不呈现”：该资产会从 server 能看到的一切内容中移除，覆盖所有环境，且是永久性的。由人工撰写（D6），与 curator 通过 AI 推断得到的**可靠性警示**不同。 |
| **澄清问题**（Clarification question） | 由 curator 提出、带 ID 追踪的、针对某个 corpus 资产的开放问题（例如“被重命名的列 `kunde_id` 是什么含义？”），等待 **Responder** 作答。它不同于**可靠性警示**（后者是 curator 自己的判断）：澄清问题是*提给人类*的，并期待对方给出回答。 |
| **Responder**（应答者） | 一个可插拔的角色，负责用*自由文本*外加可选的资源来回答**澄清问题**，从不做结构化编辑。它有两种实现，且都在引擎核心之外：生产环境中的人类 **SME**，以及评测中的 **Simulated SME**。 |
| **SME**（领域专家） | 生产环境中的人类 **Responder**：一位非技术的领域专家，用自由文本回答**澄清问题**。从不直接编辑 corpus，也不直接提 PR。 |
| **澄清答复**（Clarification answer） | **Responder** 针对**澄清问题**给出的自由文本回复（外加可选的资源）。在它进入 git 之前，会有一个*解析步骤*（由 curator/LLM 或一位数据工程师完成）把它转换成结构化的 corpus 编辑。其中的资源会落地为 `source_refs`。 |
| **Simulated SME**（模拟领域专家） | 评测框架中的一种 **Responder**：一个 LLM，被告知某个数据集的*领域含义*（各表/各列代表什么），且以 SME 知识的形式呈现，而非一份去混淆映射表，并逐个回答**澄清问题**。它从不会拿到留出的**测试**问题的 gold SQL（这是唯一的硬性防泄漏不变式）。在极限情况下，它可能逼近**Gold 语义层**，这是一个已被接受并记录在案的局限，因为 gold 是一条参考线，而不是上限。 |
| **执行准确率**（Execution accuracy，EX） | 智能体的结果与 gold 一致，通过重新执行 gold SQL 加以验证。 |
| **治理路径遵循率**（Governed-path adherence） | 通过语义层（而非原始表）解决的问题占比。 |
| **诱饵触碰率**（Decoy-touch rate） | 智能体使用了清单标记的虚假列/表的问题占比。 |
| **无语义层臂**（No-layer arm，基线） | 评测的下限：server 在*完全没有 corpus* 的情况下作答，只拿到原始的（被混淆的）schema 和问题。“基线（baseline）”专指这一行。（*避免*：不要用“基线”/“baseline”来指代仅含 Facts 的起点。） |
| **仅含 Facts 的 corpus**（Facts-only corpus） | 经自动画像分析得到的起始 corpus：物理类型、样本值，以及 FK 候选（`curator/profile.py`），**没有 Inference 层**。它是增长的起点那一行，早于任何 **SME** 交互。 |
| **Gold 语义层**（Gold semantic layer） | Arm-3 评测基线：一个确定性的去混淆神谕（rename map → real names，decoy manifest → exclusions，original schema → FK graph）。不涉及 AI，也没有所有者；是一条参考线，不是严格上限。仅适用于 BIRD。 |
