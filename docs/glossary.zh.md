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
| **执行准确率**（Execution accuracy，EX） | 智能体的结果与 gold 一致，通过重新执行 gold SQL 加以验证。 |
| **治理路径遵循率**（Governed-path adherence） | 通过语义层（而非原始表）解决的问题占比。 |
| **诱饵触碰率**（Decoy-touch rate） | 智能体使用了清单标记的虚假列/表的问题占比。 |
| **Gold 语义层**（Gold semantic layer） | Arm-3 评测基线：一个确定性的去混淆神谕（rename map → real names，decoy manifest → exclusions，original schema → FK graph）。不涉及 AI，也没有所有者；是一条参考线，不是严格上限。仅适用于 BIRD。 |
