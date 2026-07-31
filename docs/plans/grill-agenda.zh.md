# 模拟 grill：八个议题，以及每个议题上我准备被问倒的地方

> **状态 2026-07-31 —— grill 已开完，结论在别处；还剩六处分析未搬。**
>
> 实际的 grill 走了另一条路径，八个 `T*.D` 里只有一部分被逐条回答，结论都在
> [rebuild-decisions.zh.md](rebuild-decisions.zh.md) 的 21 条决定里。这份文档现在的价值只剩下几处
> 没被吸收的分析。
>
> **待搬：**T6.Q5（「十三个 sink 逐个判存废」—— 文中自承「我没在任何分析里看到有人问过」，而清单 3.1
> 只写了「没有共享 key」）· T5.Q3（serve 的 1000 行截断与 eval 的 200000 行是两套语义，而
> **grader 怎么处理截断这条至今没人查代码** —— 若无专门处理，则 serve 那套配置从未被评过分）·
> T5.Q4（「默认配置必须能支撑它打印的每一个断言」，应写成清单 2.4 的一条规矩；`open-work.md` X9
> 只是它的一个实例）· T5.O2（serve-config 对照臂 —— 用发布配置跑同一批题，第一次给出治理的可用性
> 代价数字，可补成阶段 7 的第五个臂）· T3.Q2 的 O4（一次 LLM 调用改写成独立问题**要走 ADR 修正案，
> 不是一个 knob**）· T7.Q2（freshness 归 **semantic** 轴，与决定 18 的「越权走 safety 轴」合起来才是
> 一套自洽的 stamp 语义）。
>
> **已知错误：**T8.Q4 用了 `KMB` 这个词，而它在两个仓库里都是 0 命中 —— 那是我从外部带进来的术语，
> 两份新文档都登记了这条更正。读到时按「`graph/planner.py` 里那个 Steiner planner」理解。
>
> **不必搬：**T1 与 T4 的排位推荐、§10 的开场顺序（一次性流程物）、T1.Q5（已被 20260730 那次跑回答）。

这份文档把 2026-07-29 到 2026-07-30 五份分析（架构评审、book 对照、框架与日志审计、多轮对抗、治理 red-team、corpus 漂移）压成八个真正需要拍板的议题，并且预先把每个议题上最狠的反问写下来。

它不是 [build-sequence.md](build-sequence.md) 的翻译。build-sequence 回答「按什么顺序做」，这份回答「做之前有哪几件事没想清楚」。两份的结论有几处不一致，不一致的地方我都标出来了，那些正是要 grill 的点。

> **语言说明。** 仓库的 AGENTS.md 规定 docs/plans/ 只写英文，中文孪生只给九份固定清单里的文档。这份文档是你明确要求的中文件，不是任何英文文档的孪生，所以它偏离了那条规矩。记在这里，方便以后回滚。

---

## §0 怎么用，以及编号规则

真实 grill 时按编号点名，不必复述内容。

| 编号 | 含义 |
|---|---|
| `T1`…`T8` | 八个议题回合 |
| `Tn.F` | 事实（facts），只写代码里能核到的 |
| `Tn.O1`…`Tn.O4` | 选项（options） |
| `Tn.Q1`…`Tn.Q5` | 逼问（questions），这是这份文档的主体 |
| `Tn.R` | 我的推荐，以及推荐的强度 |
| `Tn.D` | 需要你拍板的那**一个**决定 |
| `K1`…`K6` | 反向裁剪：我建议从 41 项里砍掉或合并的 |

每个 `Tn.Q` 都是我认为能问倒某个选项的问题。有几条我自己也答不上来，都写了「这条我没把握」。

---

## §1 底账：八个议题覆盖了 41 项里的哪些

| 议题 | 一句话 | 覆盖 build-sequence 条目 |
|---|---|---|
| T1 | 先删假声明，还是先建测量臂 | 整个 Phase 0 / 1 / 2 的排法 |
| T2 | graded delivery 的越权面 | 0.2、0.3、3.5 |
| T3 | 多轮：两个正确的设计撞在一起 | 2.1、3.12、3.13、3.15、4.2、1.12 |
| T4 | 检索：先测还是先建 | 3.12、3.14、3.16、3.17、3.18、3.19 |
| T5 | eval 比 serve 宽松，修它要付什么 | 1.4、2.2、2.3 |
| T6 | 一个身份，五个适配器，砍到几个 | 1.1、1.2、1.9、3.9、0.1、4.3 |
| T7 | 漂移与新鲜度 | 0.5、0.6、1.7、1.8、2.4、4.5、4.11 |
| T8 | 重构深度与范围边界 | 3.1、3.2、3.3、4.14、全部 non-goals |

剩下没进议题的（1.5、1.6、1.10、1.11、3.4、3.6、3.7、3.10、3.11、3.20、3.21、3.22、3.23）都是无争议的小项，照做即可，不值得占 grill 的时间。

---

## T1 排序：先删假声明，还是先建测量臂

### T1.F 事实

build-sequence 的排法是 Phase 0（八条假声明）→ Phase 1（十二项仪器）→ Phase 2（四个臂）→ Phase 3（建）。它给出的理由是「Phase 1 是 Phase 3 排序的前提」。

### T1.O 选项

- **T1.O1** 照 build-sequence 原序走。
- **T1.O2** 测量优先：先做 1.3（`retrieval_eval` 吃 session）和 2.1（multi-turn 臂），拿到数字再回头排 Phase 3。
- **T1.O3** 只做安全线：0.2 + 2.2 + 2.3，其余全冻结，先把「能不能守住拒答」这半个产品做实。

### T1.Q 逼问

**T1.Q1** Phase 1 十二项里，真正是 2.1 前置的只有 1.3 和 1.12。`tracing_config`、`configure_logging`、`RetryPolicy`、print 分流这些对 Phase 3 的排序毫无影响。那「Phase 1 是排序前提」这句话是不是应该改成「Phase 1 里有两项是前提，另外十项在搭车」？搭车本身没错，但它让 Phase 1 看起来像一个必经关卡，而它不是。

**T1.Q2** Phase 0 八条里，会改变决策的只有 0.2（graded delivery 的越权测试）、0.4（Phase B 丢 note 和 negative example）、0.6（`/health` 报不出漂移）。0.5、0.7、0.8 是三句 docstring 修正，改完不影响任何下游判断。把「删除假声明」和「解锁决策」混成一个 phase，是不是让 Phase 0 显得比它该有的分量重？

**T1.Q3** 反过来问 O2：先跑 multi-turn 臂，跑出来的数字建立在一条已知有洞的 governance 路径上（T2 的 A1 未定），而 eval 恰好把 `grade_semantic_failures` 打开。那这批 multi-turn 数字是不是又一批将来要退休的数字？2026-07-25 已经退休过一批，第二次退休的代价你愿意付吗？

**T1.Q4** O3 的诚实版本是这句话：「这个 repo 现在最大的未知不是准确率，是拒答」。如果这句成立，Phase 3b 那十二项检索工作凭什么排在 2.2（pooled-valid out-of-scope 负例集）前面？拒答召回从来没在 pooled 规模上量过，而它是产品的一半。

**T1.Q5** 三个选项都假设「测量先于建造」这条纪律。但这条纪律在这个 repo 里已经被自己反噬过一次：X5 说 69-schema 规模跑只以 `--skip-agent` 跑过，也就是说最该出数字的那次跑，agent 没参与。纪律是对的，问题是我们有没有能力在合理时间内真的跑出数字。你对「一次完整规模跑要多久」有实测吗？没有的话，任何以「先测」开头的排法都建在一个未知的墙上。

### T1.R 推荐（强度：中）

O1 的骨架 + O2 的重排。具体三条：

1. Phase 1 拆成 1a（1.3、1.12，2.1 的真前置）和 1b（其余十项，可与 Phase 2 并行）。
2. Phase 0 只把 0.2、0.3、0.4、0.6 当阻塞项，0.5、0.7、0.8 三条 docstring 降为顺手做。
3. 2.2 提到 Phase 2 首位，理由是 T1.Q4。

### T1.D 待拍板

接受「Phase 1 只有两项是真前提」这个拆分吗？

---

## T2 graded delivery 的越权面（最高严重度）

### T2.F 事实

`governance.py:698` 在 graded delivery 前复检 SQL，传的是 `allowed_tables=None`。`guardrails.py:918` 是 `if allowed_tables is not None:` 才跑 L4，所以这条路径上 **L4 不执行**。而 `term_semantics` 又在 `_GRADED_DELIVERY_LAYERS`（`governance.py:115-119`）的豁免名单里，等于即使 L4 跑了，它的失败也会被原谅。

L3 仍然拦着：每个列引用必须在 allowlist 里，所以 `SELECT ssn FROM hr.employees` 过不去。

**假设（未证实，测试已命名）**：不含列引用的 SQL 能轻松过 L3，L4 又不跑，于是没有东西再约束表。候选是 `SELECT COUNT(*) FROM <未授权 schema>.<表>`。若成立，这条路径泄露的是任意表的存在性和行基数，而这恰好是 obfuscation 数据集的 decoy 设计想保护的信号。

另有一件事值得对照：`governance.py:108-113` 专门加固过「从未拿到 verdict 的 attempt 不得走 graded delivery」（audit Vuln 2）。A1 是同一条推理少走一步的产物。

### T2.O 选项

- **T2.O1 收紧**：复检传 `allowed_tables`，并把 `term_semantics` 从豁免名单移除。
- **T2.O2 记录**：行为不动，改 L4 的 docstring（`guardrails.py:775` 现在无条件承诺 fail-closed）。
- **T2.O3 中间**：复检传 `allowed_tables`（L4 会跑、会记录），但保留豁免（不阻断）。

### T2.Q 逼问

**T2.Q1** 顺序问题，而且我认为这条比写测试还该先做：graded delivery 走的是 `_out_of_band_ledger_entry`（AUDIT R4），它自己写 ledger；而 generation row 上记了 `base_provenance.routed_schemas`。如果这两个字段在历史 artifact 里都在，那「20260730 那次 curated_sme 跑里有没有执行过越权 SQL」是一次离线 join 就能回答的问题，不是假设。它是已发生事实的查询。你要不要先跑这个回溯，再决定修法？（前提是要先确认这两个字段真的都落盘了，我没核过。）

**T2.Q2** O1 的代价是 graded delivery 更常拒答。可是 graded delivery 存在的全部理由就是「宁可交付一个 `unverified`，也别硬拒」。如果它主要交付的正好是这类无列引用的聚合查询，收紧等于把它删掉。那诚实的做法是不是直接讨论「要不要保留 graded delivery」，而不是讨论怎么给它加约束？

**T2.Q3** O2 的代价是安全叙事降级：从「topology 保证 scope 收敛」变成「topology 保证 scope 收敛，除了 graded delivery」。ADR 0002 是整个 repo 的地基。你愿意在地基上刻一条例外，还是宁可让可用性下降？

**T2.Q4** O3（跑 L4 但豁免）看着两全，实际含义是「我们知道越权了，还是发了」。发出去的那次带 `unverified` 戳。可是 stamp 是两轴的：`safety_clearance` 管越权，`semantic_assurance` 管语义可靠。越权属于 safety 轴，而 graded delivery 降的是 semantic 轴。O3 等于用错轴的戳去承载越权信息。要 O3，就得先改 stamp 的语义，那就不是一个小改动了。

**T2.Q5** A6 已经记录过：`identity` 只写审计行，没有 RLS、没有行级过滤，所以「guardrails 强制访问控制」这句话在行级本来就是假的。如果 table 级也留一条例外，「governance = topology」这句话还剩什么可守的边界？反过来说：如果我们承认 topology 只保证 table 级、只在非 graded 路径上，那这句话是不是应该重写而不是打补丁？

### T2.R 推荐（强度：高）

回溯查询 → 写 0.2 测试 → 若确认走 O1，并把 `term_semantics` 的豁免限定为「不含未授权基表的情况」。理由就是 T2.Q5：A6 已经放掉了行级，table 级不能再放。

### T2.D 待拍板

接受 graded delivery 变得更容易拒答吗？

---

## T3 多轮：两个正确的设计撞在一起

### T3.F 事实

一轮问题的执行顺序里，`refuse_gate`（`agent.py:579`）、schema routing（`:699-706`）、`retrieve`（`:797`）三步全跑在**未解析的原问题**上。history 只在 `assemble_context`（`:840`）进来，也就是说等模型能解「那个」的时候，前三步已经定了。

AUDIT S4 把 `inspect_schema` 限定在 `routed_schemas` 内（`agent.py:1099-1101`），`filter_corpus_for_retrieval` 又把 `search_corpus` 也限定在同一集合内。所以误路由之后，agent 没有自救路径：要么用错 schema 答错，要么撞 L4 被当成 out-of-scope 拒掉。**一个正确的追问会被拒答，而导致它的控制正在正确工作。**

`lexical_coverage` 在功能词上偏高，于是一个检索是盲的轮次，stamp 报的是 `unflagged`。

每个 eval 问题都是自己的第一轮（`arms.py:417`）。所以这个 repo 产出过的每一个数字都是 turn 1。

### T3.O 选项

- **T3.O1**（原 F1）检索查询用「上一轮用户输入 + 本轮问题」拼接。
- **T3.O2**（原 F2）覆盖度地板 + 路由粘性：本轮内容词少且存在上一轮时，复用上一轮的 `routed_schemas`。
- **T3.O3**（原 F3）跨轮 licensing：把上一轮已授权的表并进本轮可授权集。
- **T3.O4**（原 F4）一次 LLM 调用，把（history, question）改写成独立问题，再路由和检索。

### T3.Q 逼问

**T3.Q1** O2 用 `lexical_coverage` 做路由决策，而分析文档自己说这个信号「未校准」。把一个未校准的信号从「只影响 stamp」升级成「决定路由」，是搬家还是消除风险？校准它需要 1.3 的 session 级 `retrieval_eval` 加一次阈值扫。那 O2 的真实成本不是「免费、确定性」，而是「免费的实现 + 一次扫参」。

**T3.Q2** O4 是 ADR 0002 删掉的 Query Understanding 从后门回来。分析文档已经点了这句。那诚实的做法是不是：O4 需要一份 ADR 修正案，而不是一个 knob？如果它最后被证明是唯一有效的，我们要不要承认 ADR 0002 删多了？我认为要，而且这句话现在说比将来说便宜。

**T3.Q3** O3 和 AUDIT S4 正面冲突。可能的辩护是「agent 不能自己扩，但系统按上一轮已授权集扩是安全的」。这个区分站不住：上一轮的授权也是 agent 用 `inspect_schema` 拿到的。所以 O3 等于把 agent 上一轮的自授权结果延长一轮，S4 的边界漏一轮。要 O3，就得先回答 T2（table 级唯一的守门人正在讨论中）。

**T3.Q4** 有一条没进任何分析文档：C13 说裸表名歧义覆盖 731 个表资产里的 67 个（9.2%），`pais` ×5、`kunden` ×4，命中时会返回「`tbl_beer_factory_kunden`: not licensed this turn」，泄露一个 agent 从未提过、且在其路由范围之外的表名。追问句更倾向用裸名（「那些 kunden」）。C13 在多轮里是不是更容易触发？如果是，它应该并进 T3 而不是留在 Correctness 清单里。

**T3.Q5** 数据来源三选：合成代词 follow-up（推荐）、gold 分解、SParC/CoSQL。合成的诚实边界在哪？如果用 LLM 生成代词化的 follow-up，然后测系统能不能解代词，测的是不是我们自己的生成器？我的答案是：只要代词替换是**机械的**（把 gold 涉及的实体名替换成「那些 / 它」），就不是测生成器，是测替换后的检索行为。但一旦让 LLM 自由改写，就变成测生成器了。这条界要在实现前划死。

### T3.R 推荐（强度：中高）

O1 和 O2 一起做，但把 O2 的阈值当成待扫参数而不是待拍常数。O4 等 O1+O2 测完再评估，若要做就写 ADR 修正案。O3 不做，除非 T2 先落地。

### T3.D 待拍板

O3（跨轮 licensing）直接列为 non-goal 吗？

---

## T4 检索：先测还是先建，以及「先测」在哪一项上做不到

### T4.F 事实

book 对照给出六条「书里做得比我们好」，其中三条进了 Phase 3b：覆盖度地板（B-2，低成本）、term binding 加权（B-3，中）、column 级检索单元（B-1，高）。

我们测到的瓶颈是 schema routing：BM25 recall@3 = 0.35（`datalake-run.md:128` 是 0.351）。ADR 0003 另测过 embedding recall@3 = 0.70、RRF = 0.535，结论是弱词法混进强 embedding 会拉低召回。

`retrieval_eval.py` 测的是 gold SQL 上的 **table** recall@k，无 LLM、可离线复现。

### T4.O 选项

- **T4.O1** 便宜先行：3.12 覆盖度地板 + 3.13 拼接查询 + 3.14 停用词移出 BM25 查询。
- **T4.O2** term binding 全接（3.16）：binding 感知排序 + prompt 里的绑定约束 + L4 的 term fidelity。
- **T4.O3** column 级检索单元（3.17）。
- **T4.O4** 先扩测量工具，再选 O2 或 O3。

### T4.Q 逼问

**T4.Q1** 「先测再建」这句口号在 3.17 上做不到。`retrieval_eval` 测表召回，而 column pruning 不改变表召回：它的收益是 prompt 变小而召回不掉。要测它，得先给 `retrieval_eval` 加 column 级召回和 prompt 尺寸两个量。这项成本 build-sequence 里没有列。承认这点，3.17 从 L 变成 L 加一段前置工作。

**T4.Q2** 更狠的一层：X1 说没有长度匹配的 placebo 臂，所以每个 curated 结果都和 prompt 长度混在一起。column pruning 直接改 prompt 长度。就算测出 EX 提升，也说不清是「噪音列少了」还是「prompt 短了」。X1 是不是 3.17 的硬前置？如果是，3.17 的真实依赖链是「X1 → 测量工具扩展 → 3.17」，三段，不该排在 Phase 3 中段。

**T4.Q3** term binding 被书认定为最强的反幻觉杠杆，在我们这里是死的（binding 存在但除了 schema document 组装之外不影响任何下游）。而我们测到的瓶颈正是 schema routing 的 0.35。term → schema 的绑定本身就是一个强路由信号。那 3.16 是不是该跳到 3b 第一位，而不是排在覆盖度地板后面？

**T4.Q4** 反方向打 T4.Q3：term binding 走的是词法通道，而 ADR 0003 已经测出「弱词法混进强 embedding 会拉低召回」。给一个词法信号加权，会不会重复踩同一个坑？我的分辨是：ADR 0003 测的是「把 BM25 的**排序**混进 embedding 排序」，而 term binding 是「精确命中一个受管术语就允许它进入」，属于 admission 而非 ranking。两者不同。但这条分辨是我推的，不是测出来的，所以它本身应该是一个待测假设，不是一个论据。

**T4.Q5** 3.14（停用词移出 BM25 查询）是 XS，但它会改变**所有**历史 retrieval 数字。要不要先冻一份基线快照，再改？这和 T5 是同一类问题。

### T4.R 推荐（强度：中）

O1 立刻做，三项都是 XS 或 S，而且 3.13、3.12 同时服务 T3。O2 提前到 3b 首位，但只动 ranking 与 admission 侧，不动 L4 的 term fidelity（那是 T2 的地盘）。O3 明确改标为「需要 X1 前置 + 测量工具扩展」，从 Phase 3 挪到 Phase 4。

### T4.D 待拍板

3.17（column 级检索单元）降级为 Phase 4 决策项吗？

---

## T5 eval 比 serve 宽松：修它要付什么

### T5.F 事实

三个 knob 上 pooled eval 比 serve 宽松：

| knob | serve | pooled eval | 进 manifest 了吗 |
|---|---|---|---|
| `grade_semantic_failures` | `False` | `True` | 是 |
| `hard_block_suspect_columns` | `True` | `False` | 是 |
| `Gateway(max_rows, timeout_s)` | 1000 / 30.0 | 200000 / 60.0 | **否**（连 `Settings` 字段都没有） |

第一条最关键：**eval 把 graded delivery 打开了，而 graded delivery 正是 T2 的 A1 所在。** 若 A1 成立，最可能已经踩过它的臂，正是我们跑得最多的那个。

### T5.O 选项

- **T5.O1** 三个 delta 全进 `Settings` + manifest + comparability gate 新键。
- **T5.O2** 加一个 serve-config 对照臂：用发布配置跑同一批题。
- **T5.O3** 现状不动，只在 eval 文档里写清三个 delta。

### T5.Q 逼问

**T5.Q1** O1 会给 comparability gate 加键，于是历史跑和新跑不可比。2026-07-25 已经退休过一批数字。第二次退休的成本比第一次高还是低？我判断更低，因为已有先例，而且 `runs/index.jsonl` 的 quotable/comparable 台账正是为这件事建的。但这是我的判断，你才是要在报告里解释它的人。

**T5.Q2** O2 看着最诚实，代价是每次实验成本翻倍，而且 `hard_block_suspect_columns=True` 会拦掉一部分题，EX 掉下来，读起来像「治理让我们变差了」。你准备好在报告里写这句话吗？如果准备好，那它其实是这个 repo 最有价值的一张图：治理的可用性代价，第一次有数字。

**T5.Q3** 200000 对 1000 的 200 倍差，理由是要拉全量结果集做 hash。可是 serve 的 1000 行上限意味着一个正确的大结果集查询在 serve 上会被截断，在 eval 里不会。这不叫宽松，这叫两套语义。截断算不算错答案？现在的 grader 怎么处理截断？**这条我不知道答案，要查代码。** 如果 grader 对截断没有专门处理，那 1000 行的 serve 是一个从未被评过分的配置。

**T5.Q4** X9 说 `--replicate` 默认 `None`，噪音下限缺席，但 p 值照印。这和 T5 是同一类病：默认配置产出它支撑不了的断言。要不要把「默认配置必须能支撑它打印的每一个断言」写成一条硬规矩，而不是逐条修？

### T5.R 推荐（强度：高）

O1 加上 T5.Q4 的硬规矩。O2 作为一次性对照跑，不进常规流程，但它的结果值得单独成文。

### T5.D 待拍板

接受第二次数字退休吗？

---

## T6 日志：一个身份，五个适配器，砍到几个

### T6.F 事实

十三个 sink，没有共享 key。Langfuse 和 LangSmith 的 trace 都不带 `run_id`，所以 trace 和 `stage_events.jsonl` 无法 join。

`print()` 105 次、`logger.` 32 次，而 `src/` 里没有任何 `logging.basicConfig`。代码自己诊断过这件事两次（`agent.py:621`、`run_log.py:498`）。库不该调 `basicConfig` 是对的，问题是**入口也没调**，于是必须被看见的诊断都写成了 print，写成 logger 的都看不见。

`Store` 用量为零，`RetryPolicy` 用量为零。

Langfuse 的 legacy `mask` 覆盖不到第三方 instrumentation 产生的 OTel span 属性，而 LangChain callback handler 就是第三方，所以 `run_query` / `sample_rows` 的行预览原样上传，`obs.py:84-88` 的声明与实际不符。

### T6.O 选项

- **T6.O1** 五个适配器全做。
- **T6.O2** 只做 1.1（`tracing_config`）+ 1.2（`configure_logging`），其余观察。
- **T6.O3** O2 加上四条「记录级事实」进 manifest，print 分流不做。

### T6.Q 逼问

**T6.Q1** mask 那条被标成「高危 / 隐私」。可是这是 greenfield、没有用户、数据是 BIRD。谁的隐私？诚实的理由是不是「`obs.py` 声明了一个不存在的保护」，而不是「发生了隐私事故」？如果是前者，它属于 Phase 0 的删假声明，不属于隐私高危。这个降级不会让它变得不该修，只会让它排在正确的位置，并且让「高危」这个词在这个 repo 里还有意义。

**T6.Q2** 105 个 print 分流是 M 号工作量。可证伪的收益是什么？如果答案是「以后 debug 更快」，这不可证伪。而分析文档自己的立场是只动四条（`asset_bag.py` 的丢弃 caveat、`pipeline.py` 的 seed 塌缩、reference 修复、`loader.py` 跳过的 corpus 文件），其余不动。那第三个适配器其实已经缩成 O3 了。为什么清单上还写五个？

**T6.Q3** Langfuse scores（适配器四）的收益是「trace UI 能筛出答错的」。但我们已经有 `generations.<arm>.jsonl` 的 70 键行加 `runs/index.jsonl`。这是第二条路径回答同一个问题。这算不算我们自己反对过的「第四条观测通道」的缩小版？我的判断：算，除非能说清一个只有 trace UI 能回答、JSONL 回答不了的问题。我暂时说不出这个问题。

**T6.Q4** `Store` 用量为零，被读成「D8 的 memory 该用 `Store`」。但也可以读成「我们不需要跨线程长期记忆，所以没用它」。哪个读法对？**这条我没把握。** 倾向前者，因为 curator 的跨 schema 记忆确实就是这个原语，但「没人用」本身不构成「该用」。

**T6.Q5** 十三个 sink 加一个共享 identity，听起来是收敛。可是它没有减少任何 sink。真正的收敛问题是：这十三个里有几个可以直接删？我没在任何分析里看到有人问过。

### T6.R 推荐（强度：中）

O3。适配器四降到 Phase 4。mask 归类为「删假声明」而不是隐私事故，并把理由写下来。另外把 T6.Q5 列成一项新工作：十三个 sink 里哪几个可以删。

### T6.D 待拍板

五个适配器砍成三个，同意吗？

---

## T7 漂移与新鲜度

### T7.F 事实

漂移只在 build time 检一次。`validate_corpus` 的七个调用点里，四个传 connector 的全是 build time；serve 路径从不拿 corpus 和数据库对账，也没有任何定时检查。

`corpus_health`（`viz/presenter.py:366`）每次请求都跑完整的 582 行 validator，**不传 connector**。所以 `/health` 会告诉你引用断裂、metric 表达式解析失败、note 预算超标，但**报不出「你一半的表没了」**。

`Provenance`（`corpus/schemas.py:175-187`）没有 verified-against-database 的时间戳，所以 assurance stamp 拿不到任何新鲜度信号。

`_generated/` 目录不存在。两处 docstring（`corpus/loader.py:11`、`retrieval/__init__.py:25`）说它存在。索引全在进程内建、只存在内存里。

### T7.O 选项

- **T7.O1** 一下午三件事：改两处 docstring、给 `corpus_health` 传 connector（带 flag 或按间隔）、加 `corpus doctor` 入口。
- **T7.O2** O1 加 `drift` 类别进 `error_taxonomy` 加 drift 臂（用 rename map）。
- **T7.O3** O2 加 `verified_at` 进 assurance stamp。

### T7.Q 逼问

**T7.Q1** drift 臂说「免费，因为已经有重命名过的 Postgres」。但 `cs_semester` 和 `ice_hockey_draft` 是 identity-rename（名字没变）。它们在这个臂里是污染，还是自带的无漂移对照组？如果是对照组，这个臂比文档说的更好，应该把这一点写进设计而不是留给实现者发现。

**T7.Q2** `verified_at` 进 stamp（O3）等于给 stamp 加第三个来源。stamp 现在是两轴的，freshness 是第三样东西。它进 safety 轴还是 semantic 轴？我认为 semantic：陈旧的 metadata 是语义不可靠，不是越权。但这个判断要和 T2.Q4 一起看，因为那里我主张越权必须进 safety 轴。两条合起来才是一个自洽的 stamp 语义。

**T7.Q3** 给 `/health` 传 connector 的代价是每次请求跑一遍 `list_tables` 加 `describe_table`。文档说「带 flag 或按间隔」。按间隔就是缓存，缓存就意味着 `/health` 报的是过去某时刻的真相。这个 staleness 要不要在响应里带时间戳？如果不带，我们就用一个会过期的健康检查替换了一个报不出漂移的健康检查。

**T7.Q4** 最狠的一问：漂移在这个 repo 的真实优先级有多高？corpus 是整体重建的，数据库是 BIRD 的固定快照。漂移是 production 问题，我们没有 production。那 O2 和 O3 是不是在为一个假想部署做工？

我的答案：是，如果动机写成「防漂移」。但换个动机就不是了。drift 臂真正的价值不在防漂移，在于它是一个便宜的「**metadata 说谎时系统怎么表现**」实验，而这和 decoy 实验是同一类问题——corpus 说的和数据库有的不一致时，系统会拒答、答错，还是耗尽步数走 graded delivery。这个动机比防漂移强得多，而且它把 drift 臂并到已有的 decoy 线上，不再是一条孤立的工作。

### T7.R 推荐（强度：中高）

O1 立刻做。drift 臂重新定位为 metadata-说谎实验，与 decoy 线并轨。`verified_at` 留在 Phase 4，并且和 T2.Q4 一起决定 stamp 的轴语义。

### T7.D 待拍板

接受把 drift 臂的动机改写成 metadata-说谎实验吗？

---

## T8 重构深度与范围边界

### T8.F 事实

架构评审九个候选，三个最强的是：`GenerationRow`（70 键无类型 dict，2 个 producer，12 个模块里 205 处 `.get()` 读，`metrics.py:525` 的注释已经记录了故障模式）、`ServeDeployment`（`build_serve_rails` 1032 行、13 个无法寻址的闭包，两个测试用 `inspect.getsource` 加手写括号匹配解析它的源码文本）、`Step`（graph 节点名 / 实时 wire 名 / 持久 `Stage` 三套字符串空间发散，`"schema_route"` 和 `Stage.schema_pick` 是同一步的两个名字，而这一步正是我们在测的那步）。

build-sequence 把 feature 扩张、graph DB、eval driver 合并都列为 non-goal。

### T8.O 选项

- **T8.O1** 做 3.1 + 3.2 + 3.3。
- **T8.O2** 只做 3.1。
- **T8.O3** 全部推迟到出现第二个消费者。

### T8.Q 逼问

**T8.Q1** 我们自己的规矩是「一个 adapter 是假 seam，两个才算真 seam」。`GenerationRow` 只有两个 producer，按 producer 数算刚好在线上。但真正的理由不是 producer 数，是 205 个 reader 和一处已经写进注释的故障模式。所以论证要从 reader 侧说，不是从 producer 侧说。这个区别不是措辞：如果按 producer 数论证，将来有人会用同样的话给任何两处重复辩护。

**T8.Q2** 那两个用 `inspect.getsource` 解析源码的测试是这个 repo 里最刺眼的一处。但刺眼不等于优先。它挡住了什么？答案是 3.3（`get_stream_writer` 放进发事件的节点，删掉五个签名里的 `on_event`），以及 REST 与 Server 两条路 timeline 不对称。如果这个不对称在实际使用中没人碰到，3.2 的优先级要下调。你碰到过吗？

**T8.Q3** feature 扩张列为 non-goal，理由是「在未测量的多轮路径上设计 feature 是猜」。这句对，但它可以无限期拖延。要不要给它一个解锁条件而不是一个否定？build-sequence 其实已经写了 blocked on 2.1，那就该写成 gate 而不是 non-goal。两者在读者眼里差别很大：non-goal 读作「不做」，gate 读作「2.1 出来就做」。

**T8.Q4** graph DB：4.14（term 关系层级）被列为唯一残留，建议用已有的 `networkx` 做原型。可是我们已经有 KMB Steiner planner 在跑（`graph/planner.py`），只是用于 L4 licensing 和 stamp，不生成 SQL。那 4.14 是不是该直接挂在这个 planner 上，而不是新起一张 `networkx` 图？如果是，4.14 从「唯一 graph 残留」变成「已有 planner 的一次扩展」，性质完全不同。

**T8.Q5** 反问 O3：没有用户、没有第二个消费者，为什么现在重构？诚实答案是 AI 可导航性：205 处 `.get()` 里任何一处拼错都是静默 `None`，而这个 repo 的主要读者是 agent。这个理由要不要明写？我认为要，因为它是这个项目真实的工作方式，藏着它反而让重构显得像洁癖。

### T8.R 推荐（强度：中）

O1，顺序是 3.1 先，然后 3.2 和 3.3 一起。feature 扩张从 non-goal 改成 gated-on-2.1。4.14 挂到现有 planner 上。

### T8.D 待拍板

feature 扩张改成 gate，还是保持 non-goal？

---

## §9 反向裁剪：我建议从 41 项里砍掉或合并的

grill 只往清单里加东西是失败的 grill。这六条是我认为该减的。

| # | 对象 | 理由 |
|---|---|---|
| **K1** | 3.19 token 单位预算 | 在 3.17 之前没有意义：预算的单位问题只有存在 column 级单元时才可控。并入 3.17，不单列。 |
| **K2** | 3.8 把 summariser 从 `run_datalake` 里提出来 | driver 合并已按决定推迟，这项是半步。1300 行统计搬家，收益是六个测试文件不再 import 下划线名。收益对不上工作量，建议降到 Phase 4 或直接删。 |
| **K3** | 4.6 DeepAgents `skills=` 给 curator 指令 | 要测才知道，而测一次就要一次 rebuild。成本高于收益，建议删掉并记录理由，免得被当成遗漏重新提出。 |
| **K4** | 4.11 持久化 retrieval 索引 | 文档自己说「只有测出冷启动成本才做」，而做了就引入我们现在**没有**的失效面（book §3.4 那次两天事故正是这个失效面）。建议直接改成 non-goal。 |
| **K5** | 4.12 checkpointed eval 路径上的 `durability="sync"` | 这是挂在 4.1 后面的一句注，不是一个决策项。合进 4.1。 |
| **K6** | 1.10 删 Langfuse v2 fallback | 和 1.5（抬依赖下限）是同一件事的两面，合成一项。 |

砍掉这六条，41 项变 36 项，其中两项（3.17、3.19）合并后依赖链变长而不是变短——这是好事，因为那条依赖链本来就存在，只是没写出来。

---

## §10 真实 grill 的建议开场顺序

| 顺位 | 议题 | 为什么排这里 |
|---|---|---|
| 1 | **T2** | 唯一一件可能**已经发生**的事。它的回溯查询不依赖任何其他决定，而它的结果会影响 T3.O3、T5 和 T7 的 stamp 语义。 |
| 2 | **T1** | 决定其余七个的排法。但它必须在 T2 之后，因为 T2 的结果会改变 Phase 0 的分量。 |
| 3 | **T5** | 和 T2 共用一个事实（eval 打开了 graded delivery），一起谈省一轮。 |
| 4 | **T3** | 最大的测量空洞。它的 O3 已经被 T2 卡住，所以放在 T2 之后谈会快很多。 |
| 5 | **T4** | 依赖 T3 的结论（多轮若是主模式，覆盖度地板的权重上升）。 |
| 6 | **T7** | 独立，但它的 stamp 轴问题要和 T2.Q4 一起收口。 |
| 7 | **T6** | 基本独立，可以最后谈，也可以跳过直接采纳 T6.R。 |
| 8 | **T8** | 纯工程判断，不阻塞任何测量工作。 |

最后一句实话：这八个议题里，只有 T2 让我觉得不安。其余七个都是「排序和取舍」，做错了浪费时间；T2 是「我们对外说的那句话到底成不成立」，而它有可能在已经跑过的数据里就有答案。所以真实 grill 请从那个回溯查询开始。
