# 计划 · luna-max 路由与列选择实验

2026-07-31。目标是你说的那句:**知道什么有用、什么没用**。

> **执行状态(2026-08-01 更新)**
>
> | | |
> |---|---|
> | 批 A(测量完整性) | **全部落地**,`ed12445` / `f16af29` / `8b6fa91`。1825 passed,ruff 干净,CI 的 lint 门恢复 |
> | 批 B(实验使能) | **落地**,两个 context 预算 knob 默认关闭且逐字节等价(哈希钉住) |
> | E1(离线 shortlist 消融) | **已跑完** → [e1-shortlist-ablation.md](../experiments/e1-shortlist-ablation.md)。**结论改变了后续实验的靶子** |
> | E0 / E0b(冒烟) | 通过。luna / effort=max 端到端可用,crash=0.0,成本可计价 |
> | E4(三臂梯子) | **运行中**,`runs/datalake/luna-max/20260801T-ladder`,复用 Opus 语料(构建跳过已核实) |
> | E2 / E3 | 待 E4 完成后进行;E2 的处理变量(picker 表相关性排序)**尚未实现** |
>
> **E1 的三条结论,请先看这个,它们比本文档原来的判断更准:**
> 1. 20260731 那次 Opus 跑**本来就用的 `-3-large`**(3072 维),而 manifest 一个字没记 —— 换默认是**对齐**不是改变。
> 2. **检索不是瓶颈**:`-3-large` shortlist@10 已 0.953。瓶颈是 LLM pick(0.873),值 **+4.7pp EX**。
> 3. `schema_router` 里「BM25 太弱所以不融合」那条注释,**在 curated 语料上已不成立**(实测 @3:0.844 vs 0.852,@1 BM25 反而更高)。那条决策建立在一个失效的测量上。

这份计划的前半是**已经查清、不用花钱的事实**(全部由我在现有 artifact 上复现),后半是**要改的代码**和**要跑的实验**。

顺序是刻意的:先把零成本能回答的问题回答掉,再让付费的跑只去回答剩下的。

---

## 〇 · 已经查清的(零成本,已复现)

数据源:`runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z/generations.*.jsonl`,1351 题 × 4 臂,Claude-Opus-4.8 / high。

### 0.1 瓶颈不在检索,在 LLM 的 pick —— 而且报告里看不出来

`curated` 臂:

| | |
|---|---|
| 检索 shortlist@10 真实召回(gold ∈ `shortlisted_schemas`) | **95.2%** |
| LLM pick 准确率(`schema_pick == gold`) | **87.3%** |
| **gold 进了 shortlist、pick 却选了别的** | **106 题(7.8%)** |
| 这 106 题里最终答对的 | **3 题** |
| 其中 gold 在 shortlist 排**第 1** 位、pick 仍然选错 | **21 题** |

也就是说:**路由损失的三分之二,是 pick 把检索已经找到的答案扔掉了。**

而这件事在现有报告里**不可见**。`routed_hit` 与 `pick_hit` 在全部 1351 行上**逐行相等**:

```
gold in shortlisted_schemas : 1286  (95.2%)   <- 真实 shortlist 召回
schema_pick == gold         : 1180  (87.3%)   <- 真实 pick 准确率
recorded routed_hit         : 1180  (87.3%)   <- 报告叫它 routing_recall
routed_hit == pick_hit on every row? True
|routed_schemas| distribution: {1: 1351}
```

成因是 `agent.py:814`:`llm_pick` 打开时 `routed = frozenset([picked])`,所以 `routed_hit` 恒等于 `pick_hit`。**`summary.json` 里那个 `routing_recall` 不是 shortlist 召回,是 pick 准确率。**真实 shortlist 召回从来没有作为标量出现在任何 artifact 里。

天花板算术:

```
EX | 正确 pick                       = 64.1%
观测 EX                              = 56.3%
pick 完美(shortlist 不变)          = 95.2% x 64.1% = 61.0%   (+4.7pp)
路由完美                             = 100%  x 64.1% = 64.1%   (+7.8pp)
```

**+4.7pp 只需要修 pick,不需要动检索。**参照:seeded→curated 那一整级是 +8.3pp。

### 0.2 pick 为什么会输:它看到的是**按字母序前 15 张表**

`retrieval/schema_router.py:44`:

```python
# Hard cap on tables shown to the LLM schema picker per candidate. Not a settings
# knob: raising it invites papering over R1 (rank which 15 to show) instead of
# fixing it.
SCHEMA_PICK_MAX_TABLES = 15
```

`:362-367`:

```python
tables = sorted(
    _analyst_tables(corpus, frozenset({schema})).values(),
    key=lambda a: a.physical_name,          # ← 字母序,不是相关性
)
...
for a in tables[:max_tables]:
```

**没有任何按问题排序的步骤。**我用真语料渲染了 picker 实际看到的东西:

```
works_cycles: 73 张表,picker 看 15 张
SHOWN     : Address, AddressType, BillOfMaterials, BusinessEntity, BusinessEntityAddress,
            BusinessEntityContact, ContactType, CountryRegion, CountryRegionCurrency,
            CreditCard, Culture, Currency, CurrencyRate, Customer, Department
NOT SHOWN : Employee, Person, Product, ProductCategory, PurchaseOrderHeader,
            SalesOrderDetail, SalesOrderHeader, SalesPerson, SalesTerritory,
            Store, Vendor, WorkOrder, ... (共 58 张)
```

**一道关于销售订单、产品或员工的题,picker 拿到的 works_cycles 摘要里一张相关的表都没有** —— 只有一行 `… (58 more tables)`。而同一份 prompt 明确要求它「flag any part no table can supply」。

这个常量的 docstring 自己写着:提高它等于「papering over R1(排序该显示哪 15 张)而不是修它」。**R1 至今没修。**

对照证据:`world` 只有 5 张表,picker 看到它的**全部内容**,而 `world` 是最大的误选吸引子之一(10 次)。**小 schema 拿到完整证据,大 schema 拿到截断证据,两者在同一个排序里竞争。**

### 0.3 pick 之后,搜索空间几乎没有被收窄

| | |
|---|---|
| 被许可的表 == 该 schema 的**全部**表 | **51.1%** 的正确 pick 题 |
| `retrieved / licensed` 表数比,中位数 | **1.00** |
| `works_cycles` 中位:上下文 58,008 字符 / 输入 71,530 token / 许可 35 张表 | |

`retrieve()` 的 `top_k=8` 在多数 schema 上是虚设 —— grounding 加 1-hop FK 闭包之后就摊回整个 schema。

### 0.4 但「上下文太大导致答错」**不成立**

同一 schema 内部(难度受控)比较答对 / 答错的上下文大小:

```
36 个 schema 里,答错的上下文更大的:17 个
中位差:-9 字符
works_cycles:答对 60,210 / 答错 57,216   ← 答错的反而更小
```

**上下文体积不是杠杆。**这一条和 `docs/experiments/20260730T034522Z-curated-sme-error-analysis.md` §9 独立得到的结论一致。

### 0.5 宽表:pooled 有效应,但控制 schema 后**不显著**

你问的「非常 wide 的表找 column 很难」。分两层看,结论不一样。

**pooled(不控制 schema)**,按 gold SQL 里最宽那张表的列数分桶:

```
gold 表宽 01-14 列: n=631  EX=70.7%
gold 表宽 15-24 列: n=275  EX=68.7%
gold 表宽 25-39 列: n=102  EX=54.9%
gold 表宽   40+ 列: n= 70  EX=44.3%
```

单调下降 26pp,看着很强。

**同一 schema 内部按 gold 表宽度中位数切分**:

```
29 个 schema 里,宽的那一半分数更低的:17 个
中位差(宽 - 窄):-5.5%
单边符号检验 p = 0.2291
```

**不显著。**pooled 那条曲线主要是 schema 难度的混淆。

但**机制在代码里是确凿存在的**:analyst 的 prompt 里**没有任何一层做列选择**。`context.py:391-392` 逐列渲染,`retrieve()` 算出来的 `column_ids`(`rvgd.py:610-616`)**全仓无人消费**。`european_football_2.partido` 有 118 列,模型看到 118 列。

而 **router 的 prompt 是有列上限的**(`schema_pick_max_columns=12`),它的 docstring 写明理由:「a wide table would otherwise dominate the picker context」。**同样的论证从来没有搬到 analyst prompt 上。**

→ 所以这个问题**观测数据回答不了,需要一次干预实验**。见 E3。

### 0.6 错误分布:列选择是第一大错因

对 563 道答错的题做归因(`eval/error_taxonomy.py`):

```
failed_stage:  sql_generate 251 | schema_pick 131 | table_select 101 | gold_unusable 69
```

在 251 道「schema 对、表也对、SQL 错」里:

```
wrong_projection      158  (63%)   ← 选错了输出列
wrong_aggregation      91  (36%)
wrong_filter_literal   78  (31%)
wrong_filter_column    76  (30%)   ← 过滤在错的列上
```

**列选择是第 1 和第 4 大错因。**但这些失败拿到的上下文和成功的**无法区分**(20,898 vs 21,888 字符)。机制成立、剂量反应缺失 —— 又一次指向干预实验。

### 0.7 工具循环不是修复机制,是遇难信号

```
1 次工具调用 n=979  EX=69.8%
2 次         n=103  EX=46.6%
3-4 次       n= 81  EX=29.6%
5+ 次        n= 17  EX= 5.9%
```

`search_corpus` 只在 **2.4%** 的回合触发,`inspect_schema` **1.4%**。**这是一个一次性生成器,不是一个逐步收窄的 agent。**前置装配的上下文就是全部系统,而当它错了,没有恢复通道。

### 0.8 三个会污染下一次跑的测量缺陷

1. **`llm_reasoning_effort` / `embedding_model` / `embedding_dimensions` 不在 `MANIFEST_KNOBS`。**两次 Opus 跑的 manifest 里 effort 相关字段为**空**,两条台账记录**完全一样**。而 effort 在 baseline 上动了 +2.5pp,MDE 是 2.3pp —— **一个超过检测阈值的处理变量,对可比性门完全隐形**。紧挨着的 `llm_temperature` 注释写着 AUDIT E5 修的就是这件事。
2. **`routing_recall` 报的是 pick 准确率**(0.1)。
3. **宽度在 artifact 里没有任何字段。**`by_db` 没有 `n_tables`,`corpus_census` 是按臂不按 schema 的。做宽表分析必须自己去查 catalog。

### 0.9 Langfuse:**Opus 那两次跑的 trace 不在里面**

> **2026-08-02:Langfuse 已整体移除,LangSmith 是唯一 tracer**([design-decisions.md](../design-decisions.md) D20)。
> 本节的实测数字与结论作历史读。本节末尾列的三个缺陷里,**第一个已修**(`arm` 现在进
> `RunContext`,并且额外补了 `corpus_content_hash`);后两个仍在。

实测(`cloud.langfuse.com`,凭据可用):

```
2026-07-30: 23 traces
2026-07-31: 21 traces
```

一次完整梯子需要 5404 条。这 44 条是 5 题的冒烟跑。**Opus 梯子跑在远程机器上,那台机器没有配 Langfuse。**

→ **「和 Opus 在 Langfuse 里对比」做不到。**跨模型对比只能来自 run dir 的 artifact。Langfuse 对**本次新跑**仍然有用(逐调用的 prompt / 工具 IO —— run dir 里只存 `context_chars` 和 `context_hash`,不存正文),但它不是对比面。

另外三个已知缺陷:`arm` 没传进 `RunContext`(所有 trace 的 tag 都是 `['governed-bi']`);session id 是**每题**一个,不是每次跑一个;metadata 里没有模型名和 run dir。

---

## 一 · 代码改动

分三批。**A 阻塞实验,B 使能实验,C 不阻塞。**

### 批 A · 测量完整性(必须在跑之前落地)

| # | 改什么 | 判据 |
|---|---|---|
| **A1** | `MANIFEST_KNOBS` 加 `llm_reasoning_effort` / `embedding_model` / `embedding_dimensions`;`MANIFEST_SCHEMA_VERSION` 2→3 并注册快照;driver 从 `Settings` 读取后传入 | 两个 effort 不同的 manifest 过 `comparable()` 必须返回 False,理由里点名 effort |
| **A2** | 新增 `shortlist_recall` 指标(gold ∈ `shortlisted_schemas`),与 `schema_pick_accuracy` 并列;`routed_hit` 的 metric 描述改成如实说明它在 `llm_pick=True` 下等于 pick | 在 Opus artifact 上重算,shortlist_recall 必须 = 0.952 而 routing_recall = 0.873 |
| **A3** | 每题记录 `schema_route_channel` / `schema_route_degraded`(现在两个字段都进不了 `generations.jsonl`) | 全仓 grep `schema_route_degraded` 现在只有 2 处命中,改后 eval 行里要有 |
| **A4** | `by_db` 加 `n_tables` / `n_columns` / `max_table_columns`;每题加 `gold_table_max_columns` | 宽表分析不再需要外部 catalog 查询 |
| **A5** | `dirty` / `diff_sha256` 进 `RESUME_DRIFT_KEYS` | 改一行不提交再 resume,必须报致命 |
| **A6** | headline `ex_no_twin`(n=1085)与 `comparisons[].no_twin`(n=1236)统一到同一总体 | 同一份 summary 里同一个预登记量只能有一个值 |
| **A7** | `[routing]` TOML 复活:`--route-top-k` / `--no-llm-pick` 改 `None` 哨兵 | 设 `[routing] top_k = 3` 后 manifest 里必须是 3 |
| **A8** | `BUILD_COMPLETE.json` 不再由 `_has_yaml` 推导 | 见对抗审计 #4 |
| **A9** | `_PRICE_PER_1M` 加 Claude 条目 | Opus 的 USD 可回算,和 luna 可比 |
| **A10** | lint(9 个 import 排序 + 1 个未用 import) | CI 恢复绿 —— **已做**,`ruff check` 全过,1740 passed |

### 批 B · 实验使能

| # | 改什么 | 为什么 |
|---|---|---|
| **B1** | `embedding_model` 统一为 `text-embedding-3-large`(3072 维) | 你批准的;A1 保证它被记录 |
| **B2** | `SCHEMA_PICK_MAX_TABLES` 从硬常量变成可配置 knob,并**加一条按问题相关性排序的路径**(候选:复用 schema 文档的 per-table 向量或 BM25 分数) | 这是 E2 的处理变量。**不排序就只是把 15 抬到 30,正是那条 docstring 反对的做法** |
| **B3** | analyst prompt 加**每表列预算**(镜像 router 已有的 `schema_pick_max_columns`),按检索分数选列,`0 = 不限` | E3 的处理变量 |
| **B4** | ~~Langfuse:~~ **LangSmith(2026-08-02 起唯一 tracer)**:`RunContext` 传 `arm`、模型名、run dir;session id 改成每次跑一个 | 否则 5404 条 trace 无法按臂/按跑切片 |<br>**2026-08-02 部分落地(D20)**:`arm` 已传(fair 臂与 oracle rung 都传,replicate 保留自己的名字),并且额外传了 `corpus_content_hash` —— 这一项当年没看出来,而它比 `arm` 更要紧:metadata 里原本那个 `corpus_pin` **看着像语料身份、其实是模式标签**(每次 pooled 跑都是字面量 `"datalake"`)。模型名与 run dir **仍未传**;session id 仍是每题一个(它不进 trace metadata,只进 `turn_id`)。 |
| **B5** | `request_timeout_s` 60→900,`max_retries` 2→8 | **max effort 下 60 秒必超时** → `APITimeoutError` → `Outcome.crashed` → 整跑不可引用。仓库里**没有任何限流器、没有 429 退避**,SDK 的 2 次重试是唯一防线 |

### 批 C · 不阻塞(记录在案,排队)

join 置信度 fail-open 成 1.0、D15 在 graded delivery 上绕过、`stages.py` default 成 `refused`、`index_cache` 按 id 不按内容、L2 黑名单零契约测试、`quotable()` free-pass 门不可触发、`search_corpus` 非表资产跨 schema、空 route 级联到 `licensable_schemas=None`。

---

## 二 · 实验

### 隔离(先做,不可省)

```bash
cp runs/index.jsonl runs/index.jsonl.bak-preluna
```

- 新跑一律 `--out runs/datalake/luna-max/<阶段名>`,**绝不** `--resume-from` 指向两个 Opus 目录
- `governed_bi.local.toml` 加 `[logging] run_log_path = "data/logs/runs-luna-max.sqlite"`
- 语料是**每跑独立、建在 run dir 内**的(已核实:`corpus_dir = out_dir`),所以不会串

### 预算约束

| | |
|---|---|
| luna 速率上限 | ~500k TPM |
| Opus 那次完整 4 臂梯子 | 166.8M token → **500k TPM 下地板 5.6 小时** |
| 其中 router 占 | 25%(curated 臂 12.6M / 50.3M) |
| 仓库内限流 | **没有**。`--workers` 与 `--build-workers` 都是开环 |

**关键机会:`baseline` 和 `seeded` 的语料构建完全不需要模型。**只有 `curated` / `curated_sme` 要跑 curator。而 curator 正是 TPM 爆发的来源(仓库里有前科:`docs/plans/datalake-run.md:367` —— 200K TPM 上限下 curator 静默少策展了一个 schema)。

→ **复用 Opus 已建好的 curated 语料**(4 个臂的 57/57 `BUILD_COMPLETE.json` 都在)。这样:
- 零 curator token,零 TPM 爆发风险
- `corpus_content_hash` 与 Opus 跑**逐字节相同** → 唯一变量就是 serve 模型 + effort + embedder,**这正是想要的隔离**

### 阶段

| 阶段 | 内容 | 成本 | 回答什么 |
|---|---|---|---|
| **E0** | `--oracle-only --limit-dbs 3 --limit 5` | **$0**,1 分钟 | harness / Postgres / gold 通不通 |
| **E0b** | `--arms baseline --dbs address,authors --limit 3 --workers 2` | ~6 回合 | **luna + effort=max + `-3-large` 端点能不能用**;`"max"` 从没在本仓库跑过,`config.py` 不校验它 |
| **E1** | **离线 shortlist 消融**:{BM25-only, `-3-small`, `-3-large`} × top_k {3,5,10,20},1351 题算召回 | **不用 chat 模型**,只花 embedding token(~1M),几分钟 | 换 embedder 值不值。**已知 top_k=10 + `-3-small` 已经 95.2%,所以预期收益很小** —— 这一步就是去证伪 |
| **E2** | **picker 消融**:只跑 router,不生成 SQL。臂 = {现状(字母序 15 张)、相关性排序 15 张、相关性排序 30 张、`pick_max_columns` 0/12/24} | 12.6M token/臂 → **每臂 ~25 分钟** | **直击最大的一笔损失(+4.7pp 上限)。比一条完整梯子便宜 50 倍** |
| **E3** | **列预算干预**:在 E2 的赢家上,`--arms curated` 单臂,列预算 {不限, 40, 20} | 50M token/臂 → 1.7h/臂 | 宽表假设的**唯一**决定性检验(观测数据 p=0.23,不可结论) |
| **E4** | **三臂梯子** `baseline,seeded,curated`,复用 Opus 语料,E2/E3 的赢家配置 | 112M token → **3.7h 地板** | 模型泛化 + 最终数字。**`curated_sme` 退役**(三次 null,两个模型两个 effort 档),省掉每条梯子 25% 的成本 |

**顺序是刻意的:E1/E2 便宜且可能否证 E3/E4 的前提。**先跑贵的等于放弃这个选择权。

### 噪声底

按你说的,先用 **2.5pp** 作为经验噪声底。**它没有被本仓库测过**(两条 Opus 梯子都没跑 `--replicate`,`analysis.json` 里 `mcnemar_caveats.no_noise_floor` 明说了),所以:

- 计划里的每个判据都写成「> 2.5pp 才算动了」
- 这个数**记进实验文档,标明来源是你之前的实验而不是本仓库的测量**
- E4 如果跑得顺,追加一个 `--replicate curated` 把它测实(+1.7h)

---

## 三 · 分析

跑完之后要做的,现在就定好,免得事后挑数:

1. **路由分解**:shortlist 召回 / pick 准确率 / rank-1 覆盖率,luna vs Opus 逐 schema
2. **误选吸引子矩阵**:Opus 上是 `superstore` +12、`world` +10、`ice_hockey_draft` +8 —— 换模型后是不是同一批
3. **宽表**:E3 的三个列预算臂,配对 McNemar,只在 gold 表 ≥25 列的子集上
4. **错误分类**:`error_taxonomy.py` 跑两次跑,看 `wrong_projection` 占比是否随列预算下降
5. **成本**:A9 之后 USD 可比;token 一直可比
6. **可引用性**:`quotable()` 与 `comparable()` 的判定原样抄进实验文档,**包括它拒绝的理由**

---

## 四 · 不做什么

- **不再跑 `curated_sme`**。三次 null,量级比 MDE 小一到两个数量级。要么重新设计干预,要么退役。
- **不追求跑满 4 臂**。梯子的两级已经复现两次,再花 5.6 小时确认一遍不值。
- **不在 Langfuse 上做跨模型对比**。Opus 的 trace 不存在。
- **不动批 C**。它们真实,但不改变本次实验的任何数字。
