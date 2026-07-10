# Agentic BI 架构

_[English](architecture.md) · [简体中文](architecture.zh.md)_

[Agentic BI 系统](system-overview.zh.md)的完整设计。术语见[术语表](glossary.zh.md)。
每项选择背后的理由与备选方案见[设计决策](design-decisions.zh.md)。

## 1. 设计脊柱（不可妥协项）

1. **两个平面。** 语义/控制平面以版本化的配置与 Markdown 承载业务含义，通过 PR/CI 离线发布；它与只执行通过护栏（guardrail）的 SQL 的数据平面保持分离。含义只定义一次，由人类拥有。
2. **确定性 DAG + 条件路由，而非自主式 ReAct。** 问题可以很宽泛，但 SQL 必须收窄。路由是硬编码且可审计的。自由探索被限定在一个受控的范围内。
3. **失败即拒（fail-closed）。** 超出范围、覆盖缺失，或护栏被触发时，返回拒答或澄清问题，绝不给出一个自信却错误的数字。

## 2. 同一共享基座上的两套 harness

Curator 与 server 的风险特征相反。二者使用不同的 harness，但在循环（loop）之下共享所有内容。

| | Curator（构建） | Server（服务） |
|---|---|---|
| 输出 | 持久化产物（corpus） | 给用户的一个答案 |
| 校验方 | 发布前由人类校验 | 无人校验（用户无法验证） |
| 失败代价 | 低廉、可恢复 | 灾难性（静默给出错误答案） |
| 自主度 | 最大（探索） | 最小（失败即拒） |
| Harness | `deepagents` | `LangGraph` + 中间件 |

*已实现：* 两套 harness 都存在于 `agents` extra 之后，构建在基于 LangChain 的模型客户端之上。Server = `server.graph`（一个 LangGraph 的 `StateGraph` DAG，在答案上与朴素的 `answer_question` 等价）；curator = `curator.deep_agent`（一个运行在 Facts 画像（profiling）与只读探测（probe）工具之上的 deepagents agent，其构造已离线验证，实际运行则受模型门控）。

> **Curator = 永久维护者**
>
> 它不是一次性的引导程序（bootstrapper）。冷启动（cold-start）是它的第一项工作，但漂移
> 修复（drift-repair）是持续进行的。疏于维护的 corpus 会腐化（据 *How Anthropic
> enables self-service data analytics with Claude*，约以 95%→65%/月的速度）。完整
> 循环（proposer 与 adversary）见[Curator](curator.zh.md)。

## 3. 内核原语（经受模型升级而存续）

- **受治理的 gateway**：只读、RLS-as-user（以用户身份执行的行级安全）、凭证隔离、强制 LIMIT/超时、可审计/可重放。它可以访问一切，但一个上下文层会优先路由到受治理数据集（governed dataset）。理想情况下，原始表永远不会被触及。
- **Agentic 循环**：永久的控制循环。
- **工具（Tools）**：模型可能调用的编码函数。数量要精简、要精准。
- **Hooks（中间件）**：在循环事件上运行的确定性代码。`before_model` 注入上下文（工作记忆、RLS 范围、语义层路由器）；`wrap_tool_call` 对动作进行门控或否决（AST 许可清单、cost/EXPLAIN、PII、RLS）。失败即拒正是在这里落地。

> **引擎与燃料**
>
> 内核是引擎。**corpus 是燃料**，由 hooks 输送进循环。随着模型能力提升，你要做的是
> 删减工具、精简 hooks，而不是重写内核。

## 4. 四个共享服务

只分叉 harness，共享基座（substrate）。共享有三个方向，它们决定了契约（contract）存在的位置：

- **Curator 写 → server 读：** 语义层、skill、元数据/索引。契约：发布/认证（版本化）。
- **Server 写 → curator 读：** 审计日志、修正、情景信号。契约：采集（harvest，用于闭合该循环）。
- **两者共读同一份定义：** gateway 策略、评测集与 ground truth、身份/访问模型、工具注册表、溯源（provenance）格式。

1. **Gateway 服务**：访问、策略执行与审计（一个边界，两套权限画像）。
2. **Corpus 服务**：语义层、skill、元数据与索引（发布/读取 API，版本化）。
3. **记忆服务**：工作记忆、用户画像、情景记忆与纠错记忆。纠错记忆是跨 agent 的信道。
4. **评测/遥测服务**：ground truth 与运行历史，即共享的记分板。

## 5. 存储：让表示形式匹配访问模式（RVGD）

| corpus 的部分 | 表示形式 |
|---|---|
| skill、参考文档、坑点（gotcha）、过程性知识 | Markdown（git，与转换逻辑同置） |
| 指标（metric）/维度（dimension）/规则（rule）定义 | 编译后的配置（MetricFlow / MDL / OSI 风格） |
| Schema、连接（join）、外键（FK）连通性、血缘（lineage） | 图（FK 图 → 规模化时用 Neo4j） |
| “哪个文档/表/示例是相关的？” | 向量索引 + BM25 |
| 记忆（工作/用户画像/情景/纠错） | Postgres + pgvector |

Markdown 优先。图（graph）只有在连接与血缘场景中才值得使用。重量级的 LLM 知识图谱被推迟。理由：策展（curation）与结构胜过表示形式的精巧程度。Anthropic 的一项零结果（null result）显示，对原始 corpus 直接做 grep 检索，准确率的提升不到 1 个百分点。参见 *Data Agent Memory Design Overview*。

*当前已实现：* 检索运行纯 Python 实现的 **BM25** 词法通道，外加基于 corpus 关系的确定性接地（grounding），以及一条 **向量/语义通道**（embedding，通过 Reciprocal Rank Fusion 与 BM25 融合），该通道位于注入式的 `Embedder` 接缝（seam）之后，只有在传入 embedder 时才会开启，默认关闭。FK 图是驱动 Steiner 连接规划的内存态 `networkx` 投影；Neo4j 则作为企业级规模下的投影保留。模型选择（可替换的 OpenAI `gpt-5.5` LLM 与 `text-embedding-3-small` embedder）存放在项目配置文件中（`governed_bi.toml`，由 `config.load_settings` 解析）；API key 从环境变量读取，从不落盘存储。客户端代码位于 `governed_bi.llm`，置于 `ChatClient` / `Embedder` 协议之后，二者均带有确定性的离线默认实现，使流水线在没有模型或网络时也能运行。

> **corpus 契约 = Git+YAML 类型化资产，由 curator 编写 / 人类审核（D9）**
>
> “编译后的配置”这一行，是以 *《从数据到智能》* 风格实现的类型化 YAML 资产
> （`table/column/join/few_shot/term/metric/rule`）。curator 编写这些资产；人类通过
> viz 界面进行审核。**Git 是唯一的真相源（single source of truth）。图（对 BIRD 是
> 内存态，企业级规模时以 Neo4j 作为派生投影）、向量、BM25 与 Postgres 全都是可重建的
> 投影，绝不直接编写。** 列的可靠性由 AI 推断得出的*文字说明*（"UNRELIABLE, DO NOT
> USE"）表示，而不是一个类型化的诱饵（decoy）标志，因此该机制可以迁移到企业级部署中。
> 见[设计决策](design-decisions.zh.md)中的 D9。

## 6. 运行时查询流程（Server）

```
ask → supervisor → query understanding → intent route → SQL cache check →
RVGD retrieval → Steiner-tree join plan → SQL gen → five-layer guardrails →
execute (as-user) → answer + provenance
```

完整的分阶段设计见[Server](server.zh.md)，以及 curator 推断驱动 server 行为的三个关键点。

> **SQL 语义缓存快速路径**
>
> 对问题做 embedding → 与已缓存 SQL 库做余弦相似度比较，阈值 ≥0.92 → 命中则跳过
> 检索、规划与生成，但**始终重新执行**缓存中的 SQL（新鲜度优先于延迟；只缓存 SQL
> 文本，从不缓存结果，这与 D7 的身份范围划定一致）。未命中则走完整流水线，成功后再
> 写回缓存。TTL 为 15 分钟。使用单一的全局阈值，这是一个已知的缺口，尚未按领域分别
> 调优。参见 *Data Agent Memory Design Overview* §5。

护栏按顺序排列（任一触发即失败即拒，五层全部强制执行）：语法 → 策略黑名单 → AST 列许可清单 → term 语义 → 成本。AST 许可清单具备 scope 感知能力（针对每一列自身所在的查询 scope 进行解析，并拦截星号投影）；term 语义会为检索到的表，以及它们的 FK 连接邻域和连接规划所桥接经过的 Steiner 点授权（而不是精确的检索命中集合，因此它与检索召回率相解耦），并拦截跨命名空间的表名。成本层目前是一道结构性的交叉连接防护；基于数值化 EXPLAIN 的成本（Postgres / Redshift）是未来按方言展开的工作。逐阶段细节见[Server](server.zh.md)第 8 步。

> **有界自修复（生成 → 护栏 → 执行）**
>
> 生成、护栏与执行构成一个有界循环（bounded loop）。护栏拒绝或执行错误会被反馈给
> 生成器以进行下一次尝试，而不是直接拒答；每一次尝试都会重新经过护栏，因此未经审查
> 的 SQL 永远不会被执行。当生成器重复生成同一个查询时（说明没有进展）会提前停止，
> 并在达到一个较小的次数上限后失败即拒。经过修复后的答案会被标记为 `lineage`，而不
> 是 `governed`。

> **拒答与尽力而为（两个并发的关口，而非瀑布式流程）**
>
> - **拒答关（Refuse-gate）**（人工整理的反例）：匹配上即触发预设升级（canned
>   escalation）（含负责人联系方式）。这是失败即拒路径。
> - **硬护栏（Hard guardrails）**（`wrap_tool_call`）：无论如何都可以否决任何查询。
> - **否则尽力而为：** governed → lineage → fenced-raw，并附带一个**可靠性标记**
>   （provenance 档位加上触发的不确定性标志）。这些档位是未经校准的治理/不确定性
>   启发式规则，基于评测调优而成：`governed` 只意味着安全、在范围内、且未触发任何
>   不确定性标志，**并不**意味着已验证正确。护栏是安全/治理层面的关卡，不是正确性
>   的判定者（oracle），因此一个看似合理却错误的查询（合法、在许可清单内、但计算
>   有误）会在这里以及失败即拒路径中被捕捉，而不是在某一层护栏处。让标记真正具有
>   约束力：低可靠性的答案要接受差异化处理。
> - **高风险（leadership / PII）场景：** 需要人工签核（sign-off），或仅返回 SQL。

## 7. 记忆策略

- 工作记忆：始终开启（会话级，按身份限定范围）。
- 情景记忆与纠错记忆：默认关闭。只有当评测证明其价值时才按领域启用，启用后使用
  价值感知（value-aware）的检索方式。
- 持久记忆（durable memory）与 corpus 一样要经过 PR 关卡 → 记忆与 corpus 之间的区分
  由此消解。纠错记忆约等于“采集修正（correction-harvesting）→ 提交 PR 写入参考
  文档”。被提升的情景记忆约等于受门控的少样本（few-shot）示例。只有工作记忆/临时
  记忆不受此关卡约束。

> **可复用的数值**（起点；在采用前需针对 BIRD-Obfuscation 评测进行调优）
>
> | 参数 | 取值 |
> |---|---|
> | 工作记忆 | 按会话划定范围，会话结束即清除 |
> | 用户画像 TTL | 365 天 |
> | 情景记忆 TTL | 90 天 + 每天衰减 0.02 |
> | 纠错记忆 TTL | 180 天 |
> | SQL 缓存 TTL | 15 分钟 |
> | 缓存命中门限 | 余弦相似度 ≥ 0.92（见 §6） |
> | 少样本（few-shot）召回门限 | 余弦相似度 ≥ 0.95，置信度 ≥ 0.9，fail_count ≤ 3 |
> | 路由记忆预算（用户画像/情景/纠错） | nl2sql 5/2/5 · kpi_lookup 2/0/1 · knowledge_qa 3/1/1 · deep_analysis 8/8/4 |
> | 少样本晋升门限 | `pending_review` → 人工 `approve` → 检索时阈值校验 |
>
> 来源：该书中可直接复用的蓝图。参见 *Data Agent Memory Design Overview* §5。

## 8. 评测

- 近期：[BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation)（4 个数据库版本，约 1 万条经过验证的问答对，decoy manifest，rename map）提供经过验证的 ground truth。参见 *BIRD Bench Obfuscation Methodology*。
- 核心指标：执行准确率（execution accuracy）相对 gold 的比较。不对语义层做人工评分。
- **数据切分（沿用 BIRD-Obfuscation 的方案）：** 按数据库进行**80/20 带随机种子的留出划分（seeded holdout）**：8,134 条训练 / 2,030 条测试，69 个数据库两边都有。**curator 只读取 `train_final.jsonl`**（经过蒸馏，而非整体倾倒）→ 在留出的 `test_final.jsonl` 上打分。由于划分不相交且带随机种子，数据泄漏（leakage）在结构上被杜绝。
- **变体：** 三臂（3-arm）语义评测运行在 **`rename_decoy`** 实例上（晦涩的命名与真实生效的诱饵，此时语义层的价值最大），并以 `base` 作为合理性检查的参照基准。server 始终针对同一个物理数据库执行。各臂之间只有 corpus 不同。
- 三个臂，全部按 EX 打分：（1）无语义层；（2）curator 构建；（3）gold 语义层（从 manifest 自动推导）。**护城河（moat）= curator 挽回的那部分因混淆导致的准确率下降；臂 3 = 可挽回的上限。** 臂 2 与臂 3 的对比即 curator 的质量。
- 来自 manifest 与日志的免费行为信号：诱饵触碰率、治理路径遵循率。成本与效率（耗时、token 数、行数；BIRD 的 VES 可复用）会被记录，但不作为核心指标。
- **拒答关（Refuse-gate）评测：** 一个留出的**不可回答**集合，由跨数据库（cross-DB）与覆盖被移除（removed-coverage）的情形构成（自动生成），外加一个小规模、人工构建的超出范围（out-of-scope）集合。评分维度为**拒答准确率**（能否拒答不可回答的问题）*以及* **误拒率**（false-refusal rate）（在可回答的测试集上）。这正是拒答的精确率与召回率。
- **仓库边界：** BIRD-Obfuscation 产出经过验证的数据与 manifest，并明确将“利用这些陷阱（trap）的下游 agent”排除在自身范围之外。而这个下游 agent 正是*本*系统。
- 之后：在企业级部署上开展规模化检索（retrieval-at-scale）评测（Recall@K / MRR / nDCG，经语义层回答的问题占比）。

> **评测缺口**
>
> BIRD 规模小、数据干净 → **不能**测试规模化检索。BIRD 的问题全部可回答 →
> **不能**测试拒答关（需要一个留出的不可回答集合）。**延伸臂（stretch arm）：** 对
> 若干个整库保留其训练集不用，以测试*零种子（zero-seed）*冷启动（约 69 个陌生的
> “公司”）。这一项被推迟，不是首先要构建的内容。

## 9. 环境（是开关，不是架构分叉）

| 关注点 | Dev / test（BIRD） | Prod（企业级） |
|---|---|---|
| 人工关口 | 自动接受 corpus 变更 | 每次变更都要经过 PR + 负责人（owner） + CI |
| 身份 / RLS | 单一的全权限身份 | 真实用户，在 gateway 处执行 RLS |
| 服务方式 | 单进程 + 文件 + SQLite | 无状态的 server 集群（fleet）；curator 作为异步任务；gateway/corpus/memory/eval 作为独立服务；图数据库；缓存 |

现在就把这些抽象打底（identity 对象、关口、按范围划定的 memory/cache），这样上线到 prod 只是一次配置切换，而不是重写。
