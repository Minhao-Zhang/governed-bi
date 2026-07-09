# Agentic BI 服务端

_[English](server.md) · [简体中文](server.zh.md)_

[Agentic BI System](system-overview.zh.md) 的服务侧代理（serve-side agent）。它是在线运行的受治理代理，*消费* corpus（语料库）以生成答案，做到**失败即拒（fail-closed）且可审计**（两套 harness 拆分；`LangGraph` + 中间件（middleware））。是 [Curator](curator.zh.md) 的对应方，消费 [Asset schemas](asset-schemas.zh.md) 中定义的资产。

> 实现：[`src/governed_bi/server/`](../src/governed_bi/server/)，护栏（guardrail）/gateway 位于 [`gateway/`](../src/governed_bi/gateway/)，连接规划（join planning）位于 [`graph/`](../src/governed_bi/graph/)，RVGD 位于 [`retrieval/`](../src/governed_bi/retrieval/)。

## 形态

一个**确定性、带条件路由的 LangGraph DAG**（design-spine #2，绝不是自主式 ReAct）。中间件：`before_model` 注入上下文（工作记忆（working memory）、RLS 范围、语义层路由器（semantic-layer router））；`wrap_tool_call` 运行护栏，失败即拒就是在这里实现的。

> *已实现：* 在同一套经过测试的构建块（building block）之上，提供两个入口（entry point）。`server.flow.answer_question` 是纯粹的确定性参考实现；`server.graph.build_serve_graph` / `answer_question_graph` 则是 **LangGraph `StateGraph`** harness（节点：ingest → refuse_gate → prepare → cache → retrieve+context → generate → guardrail → execute → stamp；自修复循环（self-repair loop）就是图上回到 `generate` 的一个环）。`before_model` / `wrap_tool_call` 中间件分别对应上下文节点和护栏节点。两者按构造对 `Answer` 是等价的（图节点调用的是同一组辅助函数），测试中有相应断言。LangGraph harness 需要 `agents` extra。

## 流程

1. **摄入（Ingest）**：问题 + 身份（identity）（D7、以用户身份（as-user）） + 工作记忆（D8、会话级（session-scoped））。
2. **查询理解与术语绑定（term binding）**：通过 `term` 资产解析业务语言。同义词与 `term_relationship` 把各种措辞映射到规范资产（强路由（strong-routing），而非 LLM 猜测）。
3. **意图路由（intent routing）**：硬编码路由（`nl2sql | kpi_lookup | knowledge_qa | deep_analysis`），每条路由各自拥有检索与记忆预算。
4. **SQL 语义缓存快速路径**：问题嵌入 → 与缓存 SQL 的余弦相似度 ≥0.92 → 命中则跳过检索（retrieval）/规划/生成，但**始终重新执行**（仅 SQL 文本、以用户身份、D7）。TTL 15 分钟；成功后写回。*已实现：* `server.cache.SqlCache`（默认关闭，以注入方式提供）。命中的缓存条目还会针对其存入时所持有的已授权表**重新过护栏**，然后再执行；已过期或现已被拦截的命中会回落到完整流水线（失败即拒）。缓存准入以**语义**轴为门槛，绝不只凭安全：只有 `certified`（干净运行、未触发任何不确定性标志）的答案才会被写回。
5. **RVGD 检索**：R 精确匹配 / V 语义 / G 图 / D 词典。四阶段重排，受 token 预算约束，并有 Corrective-RAG 回退。**仅限 Facts 层与 Inference 层**（加载器契约（loader contract））；Audit 层与 `excluded` 资产永不被检索。*已实现：* 纯 Python 的 **BM25** 词法通道，加上确定性接地（grounding）（一个已绑定的 term 会带出它的目标对象，一个 metric 会带出它的基表，一张 table 会带出它的列），以及 **V（向量）通道**（`retrieval.embedding`）：注入的 `Embedder`（OpenAI `text-embedding-3-small`，或确定性的离线 `HashingEmbedder`）按余弦相似度排序，并通过 Reciprocal Rank Fusion 与 BM25 融合。除非传入了 embedder，否则该通道关闭，因此默认是纯 BM25。图通道（G）与 Corrective-RAG 重排仍是后续切片。
   - **上下文组装（context assembly）**（`server.context.assemble_context`）：检索返回的是 id；该步骤将 L4 授权的表范围解析为一个 `PromptContext`（物理 schema、带置信度的连接路径、terms、metrics、suspect 列告诫、gold 标准样例、skills）。护栏的 `allowed_tables` 由此派生，因此**生成器能看到的，正是 L4 所允许的**。
6. **Steiner 树连接规划**：在推断出的 FK 图上进行。
7. **SQL 生成**（一个可插拔的扩展点（pluggable seam），`SqlGenerator`）：设计愿景中的生成器在 LLM 之上叠加一个系统提示（角色 → schema 约束 → 安全 → 输出），输出**经过混淆的物理标识符**。*已实现：* `TemplateSqlGenerator`（确定性，单表 metric 聚合，不依赖模型）以及 `LlmSqlGenerator`（读取 `PromptContext`，调用注入的 `ChatClient` = OpenAI `gpt-5.6-sol` low，对修复循环具有反馈感知能力，并以 `CANNOT_ANSWER` 哨兵值拒答）。运行哪一个由注入方式决定。
8. **五层护栏**（`wrap_tool_call`，任一环节失败即拒，五层全部强制执行）：语法 → 策略黑名单 → AST 列许可清单 → term 语义 → 成本。**L3 具备 scope 感知能力**（sqlglot `traverse_scope`）：它针对每个列自身所在的查询 scope 进行解析，检查每一个列节点（包括裸的 `HAVING` 引用以及 `USING` / `NATURAL` 连接键），并拦截许可清单无法背书的星号投影（`SELECT *` / `t.*`）。**L4（term 语义）**授权的范围是检索到的表，加上它们的 FK 连接邻域（一跳，可调），以及连接规划所桥接经过的 Steiner 点，而不是精确的检索命中集合，因此它与词法检索的召回率相解耦；它还会拦截跨命名空间（db/schema 限定）的表名。L3 仍然守卫每一个列，因此扩大表范围绝不会泄漏 `excluded` 或 `suspect` 列（邻居表只会暴露它自身已被允许的列）。**L5** 是一道结构性的交叉连接 / 笛卡尔积防护；基于数值化 EXPLAIN 的成本（Postgres / Redshift）是未来按方言展开的工作。refuse-gate 与其**并发**运行（D5）。
9. **以用户身份执行**：gateway 的 RLS、强制 LIMIT/超时、审计/重放。
10. **应答与可靠性标记（reliability stamp）**：一个**双轴**标记——`safety_clearance`（护栏 + 授权已通过，一个闸门）与 `semantic_assurance`（`certified` → `heuristic` → `unverified`，接地程度如何）。单轴档位（governed → lineage → fenced-raw）是二者的紧凑投影。高风险（high-stakes）→ 签核（sign-off）/ 仅 SQL（SQL-only）。

**自修复（第 7-9 步构成一个有界循环）。** 生成、护栏与执行以循环方式运行：*可修复的*护栏拒绝或执行报错会反馈给生成器，供其再次尝试，每次尝试都会重新过护栏，因此未经审查的 SQL 永远不会被执行。当生成器已无法再改进（重复输出同一条查询）时会提前停止，并在达到一个较小的次数上限后失败即拒。经过修复的答案的 `semantic_assurance` 为 `heuristic`（档位 `lineage`），绝不会是 `certified`/`governed`。**并非所有失败都可修复：** 一个硬性策略/DDL 阻断（L2 `policy_blacklist`）会立即失败即拒，因为把它回传只是施压让生成器规避策略；范围失败（L3/L4）按决定保持可修复（FK 邻域 + 修复循环是刻意的降低误拒机制；[D11](design-decisions.zh.md#d11开放决策外部评审2026-07-09)）。这能够从畸形的 SQL 中恢复，且从不放行未经检查的查询；但它无法捕捉*看似合理却错误*的 SQL（语法有效、在许可清单内，但计算逻辑是错的），这正是双轴标记以及拒答 / 仅 SQL 路径存在的原因。护栏是安全/治理层面的关卡，不是正确性的判定者（oracle）。

## curator 推断驱动 server 行为的三个关键点

Inference 层起的是*引导*作用，不是装饰。这正是 server 区别于通用 text-to-SQL 流水线的地方。

1. **可靠性告诫 → 规避诱饵（decoy avoidance）。** `suspect` 列的告诫会被注入 SQL 生成环节（例如「DO NOT USE …」），并可在护栏 L3（AST）处被检查。**诱饵触碰率（decoy-touch rate）**的胜负正是在这里决定的。
   - **执行方式的环境开关：** dev/BIRD 环境会**硬性拦截**任何引用 `suspect` 列的 SQL（诱饵本就用不上 → 把诱饵触碰率推向 0）；prod/企业环境则**软性警告，并丢弃可靠性档位（reliability tier）**（误报标记绝不能悄悄拦掉一个本应给出的真实答案）。
2. **连接 `confidence` → 规划与不确定性。** 置信度较低的推断连接，在 Steiner 规划中会受到**成本惩罚**；若最终选定路径中含有低于阈值的连接，就会**传播到可靠性标记上**。
3. **Skills → 塑形 SQL 生成**（路由 / 坑点）。这正是让 **Arm 2** 超越 Arm 3 的 gold 上限的关键杠杆。

**不确定性汇聚 → `semantic_assurance`：** 使用了低置信度连接 · fenced-raw 回退 · 触发了 Corrective-RAG · suspect 列在范围内 · SQL 经过修复 → 把 `certified` 下调为 `heuristic`（或 `unverified`）→ 差异化处理（D5，让标记真正具有约束力）。这只是*语义*轴；`safety_clearance` 是另一个独立的通过/失败闸门，它并不表示数字有多正确。这些档位是**未经校准的治理/不确定性启发式**，需要在评测中调优：`certified` 意味着安全、在范围内，且没有触发任何不确定性标记，**不**意味着已验证正确。由于失败即拒本身带有误拒代价，评测中的 `false_refusal_rate`（见 [Architecture](architecture.zh.md) 第 8 节）正是用来制衡它的指标。

## 治理性排除（硬性、人工设定）

区别于 curator 通过 AI 推断出的 `reliability.suspect`：由人工负责人在审阅之后，在某个列/表上设置 `governance.excluded: true` → 该资产会从 server 所能看到的一切（检索、呈现的 schema、图）中**彻底移除**，在**所有环境中生效、没有开关、永久有效**。它仍会出现在 viz / 审计界面上（带标记与原因），因此这次排除是可审计的；护栏 L3 也会对它进行硬性拦截，作为纵深防御（defense-in-depth）。升级路径：curator 标记 `suspect` → 人工审阅（D6） → 维持原样，或升级为 `excluded`。这一机制**不计入自主评测 arm**（这样可以让 Arm 2 保持纯粹依赖 curator 的状态）；它是面向企业部署的人机协同治理能力（human-in-the-loop）。规范见 [Asset schemas](asset-schemas.zh.md)。

## 拒答 / 尽力而为决策树（fail-closed，D5）

拒答由一个经过整理的信号驱动（`negative_example` 资产），而不是靠覆盖率启发式：一次与硬性护栏并发运行的语义相似度匹配。

- refuse-gate 命中（negative example），**或**硬性护栏否决 → **拒答**（预设升级（canned escalation））
- 否则，若有 governed 覆盖 → **应答：governed**（高档位标记）
- 否则，若可通过 lineage 推导 → **应答：lineage**（中档位标记）
- 否则，若可给出 fenced-raw → **应答：fenced-raw**（低档位标记）
- 否则，若没有任何路径高于置信度下限（confidence floor） → **拒答 / 请求澄清**（失败即拒）
- 高风险（leadership / PII）场景 → 无论如何都需要签核或仅 SQL

绝不给出一个自信满满却错误的数字。

延伸阅读：[Design decisions](design-decisions.zh.md)（D5 拒答 · D6 归属 · D7 身份 · D8 记忆 · D10 curator） · [Asset schemas](asset-schemas.zh.md) · [Curator](curator.zh.md) · [Architecture](architecture.zh.md) 第 6 节。
