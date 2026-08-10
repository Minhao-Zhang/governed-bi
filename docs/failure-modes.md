# 失败模式 — 这个引擎在 BIRD 上答错的时候，是怎么错的

**当前臂**：`proxy_v3_fold_opus_high_corpus30872d3.jsonl`，**EX 0.664**（clean 0.6641）
**引擎**：`4f7430a`　**语料**：`../BIRD-corpus` @ `30872d3`　**prompt**：ANALYST v3

前三臂（run1 0.579 / run2 0.570 / v3-pinned 0.611）跑在更早的引擎上。下面每一节标注它的
测量臂 —— 一个数字只对它测出来的那个引擎成立，而这四臂之间引擎变了两次。

**证据**：逐题带 `licensed` / `gold_sql` / `generated_sql` / `gold_fingerprint`，v3-fold 起
还带 `attempts` / `context_evicted` / `model_calls` / 两个处理身份 hash。
**臂**：agent = Claude-Opus-4.8/high，utility = Claude-Sonnet-5/high，embed top-n=10，
workers=10，`run_query_attempt_cap=5`

> 语料是每一次测量的处理身份。本文所有数字只在语料 `30872d3` 上可比。语料已入 git，
> 但不能从任何已提交的东西重新生成。

待办清单在 [open-work.md](open-work.md)；本文是它引用的证据。

---

## 方法

三件事让下面的数字比"在失败集合上数特征"更有力：

**对照组。** 每个特征同时在正确答案上计算，给出 lift。只在失败集合上计数会犯基率错误：
如果 37% 的错误答案存在过度投影，而 35% 的正确答案也存在，那么过度投影就不是病因。

**离线重放治理层。** 把被拒绝的语句连同当时的 `licensed` 集合喂回 `check()`，直接读出
失败在第几层，而不是从 `refused_by` 猜。

**因果修复实验。** 连上评测数据库，对错误预测施加一个受控修复（只改输出形状），
**重新执行、重新指纹比对**。这把"相关"变成"可计数的因果上界"。

**方法有效性校验**：随机取 60 条已记录预测重新执行，60/60 复现了记录中的 `pred_fingerprint`。
数据库状态与运行时一致，因此所有重执行结论成立。

---

## 一、总账（v3-fold）

```
1351 题，正确 897，EX = 0.664       clean（剔除 29）= 0.6641
失败 454   ← run1 时是 569
```

| 桶 | n | 性质 |
|---|---:|---|
| **满覆盖 answered 答错** | **262** | 真语义错误 |
| 表覆盖不全 | 86 | 检索 |
| 冻结字面量 gold | 85 | 数据集缺陷，不可赢 |
| capped | 57 | run1 时 133 |
| refused | 23 | **全部**覆盖不全，零个满覆盖 |
| clarification | 6 | 全部零授权 |

**refused 和 clarification 现在 100% 是检索失败** —— 没有一个是"看得见数据却拒绝"。

### 历史臂（run1，引擎 `d121c34`）

按表覆盖分层（`table_coverage()`，其 docstring 称之为 "The EX ceiling"）：

| 表覆盖 | n | EX |
|---|---:|---:|
| full | 1132 | **0.647** |
| partial | 67 | **0.119** |
| none | 25 | **0.000** |
| tableless（冻结字面量 gold） | 127 | 0.331 |

**真 gold 上的表覆盖率是 0.925。** 检索基本到位——这一点很重要，因为它决定了下面每一
桶该怎么读：覆盖不全只影响 92 道题，但在这 92 道题上引擎几乎全灭。

outcome 与覆盖的交叉表是全表最干净的一条结构信号：

| outcome | full | partial | none | tableless |
|---|---:|---:|---:|---:|
| answered | 1024 | 40 | 11 | 114 |
| capped | 106 | 14 | 3 | 10 |
| **clarification** | **0** | **0** | **6** | 2 |
| **refused** | **2** | **13** | **5** | 1 |

拒绝和澄清几乎完全落在覆盖不全的格子里；capped 几乎完全落在覆盖完整的格子里。
这三个桶不是三种病。

---

## 二、拒绝：治理层在报告检索失败〔run1 / 21 例；v3-fold 23 例，结论不变〕

把 21 条语句连同各自的 `licensed` 重放进 `check()`：

| 失败层 | 数量 | reason_code |
|---|---:|---|
| **Layer 6 TABLES** | **18** | `r_table_not_licensed` |
| Layer 4 BINDING | 1 | `r_star_projection`（`SELECT *`） |
| 重放通过 | 2 | — |

治理层给出的理由是逐字打印出来的：

```
beer_factory.kunden resolves to beer_factory.kunden, which this turn does not license
works_cycles.EmailAddress resolves to works_cycles.emailaddress, which this turn does not license
```

21 个里有 19 个的 gold 表根本不在 `licensed` 里。**这不是治理误伤，是治理正确地报告了
一次检索失败。** 放宽这一层等于让引擎去查它没被授权的表，那测出来的就不再是一个
governed engine。

> `works_cycles` 的报错里有大小写外观（`Product` → `product`），查过 `normalise_table_key`：
> 两侧都规范化，对称，**没有 bug**。那只是在显示规范化后的形式。

剩下 2 条（train_667、train_5044）重放**通过**——记录下来的 `generated_sql` 与被拒绝的
那一次尝试不是同一条。未解释，值得单独查。

---

## 三、澄清：全部是零授权〔run1 / 8 例；v3-fold 6 例，仍全部零授权〕

八次澄清的 `licensed` 全部为空，`schemas` 全部为空。agent 的上下文里什么都没有，
它说"我需要更多信息"是唯一正确的反应。

这不是"agent 太谨慎"，而且不能用"在 eval 下偏向尽力回答"来处理——那是逼一个看不见任何
schema 的 agent 凭空编 SQL，把一个诚实的信号换成一个必然错误的捏造答案。

真正该修的是**为什么这 8 轮路由返回了零个 schema**（`licensed` 中位数 26，全库只有这
8 行低于 5）。

---

## 四、答了但错了〔run1 / 292 例；v3-fold 262 例〕

这一层的 EX 是 0.715。

### 4.1 特征 lift（对照组是 732 个正确答案）

| 特征 | 错误率 | 正确率 | **lift** |
|---|---:|---:|---:|
| 投影列数不符（多 107 / 少 12） | 0.366 / 0.041 | **0.000** | **∞** |
| **缺 DISTINCT** | 0.068 | 0.007 | **10.0x** |
| GROUP BY 不符 | 0.103 | 0.014 | 7.5x |
| 多 join | 0.106 | 0.019 | 5.6x |
| ORDER BY 不符 | 0.082 | 0.029 | 2.9x |
| 子查询结构不符 | 0.110 | 0.041 | 2.7x |
| 少 join | 0.099 | 0.041 | 2.4x |
| 聚合不符 | 0.182 | 0.082 | 2.2x |
| **多余 DISTINCT** | 0.096 | **0.072** | **1.32x** |
| LIMIT 不符 | 0.781 | 0.898 | 0.87x |
| 形状完全一致 | 0.271 | 0.795 | 0.34x |

两个必须记住的读数：

- **多余 DISTINCT 基本无害。** lift 1.32，**53 个正确答案也多加了 DISTINCT**。一条方向性的
  "少用 DISTINCT"规则会打坏它们。真正的信号是**缺** DISTINCT。
- **投影列数不符的 lift 是无穷**——零个正确答案有列数差异。grader 对结果集做哈希，
  所以这是失败的充分条件。但作为诊断它是同义反复；真正的问题是删掉多余的列之后剩下的
  查询对不对。
- 安全 `LIMIT 200001` 是惰性的（lift 0.87，695 个正确答案也带着它）。

### 4.2 因果修复：修完再执行一遍

修复格 = 丢掉安全 LIMIT × DISTINCT 开/关 × 保留任意 k 个投影列（k = gold 的列数）。
由 oracle 挑选最优修复，因此是**上界**。

```
population (answered, wrong, full-coverage) : 292
  RECOVERED:projection      52
  RECOVERED:distinct        27      （15 例"加上"，12 例"去掉"）
  semantic                 213

纯输出形状可救回 : 79/292 = 27.1%
不可约的语义错误 : 213/292 = 72.9%
```

**输出形状完全正确时：EX 0.579 → 0.637（+5.85 pp）。**
单看投影：107 个过度投影中 **51 个（47.7%）删掉多余列就正确**。

典型形态是"问一个东西，返回那个东西 + 用来排序/筛选的指标"：

```sql
-- train_5274 「2016 年销量最高的根汁汽水属于哪家酒厂」
-- GOLD: SELECT brauerei_name … GROUP BY … ORDER BY COUNT(...) DESC LIMIT 1
-- PRED: SELECT brauerei_name, COUNT(wurzelbier_id) AS cnt …     ← 多一列，哈希不匹配
```

---

## 五、capped〔run1 / 133 例〕：桶里躺着已经算对的答案

```
表覆盖 full = 106,  partial = 14,  none = 3,  tableless = 10
gold 需要 join : 103 / 133
预测含有 join  : 26 / 133
【表覆盖完整 + gold 需要 join + 预测没有 join】= 69
```

106/133 表覆盖完整——表就在上下文里，agent 还是没把 join 拼出来。缺的不是"哪些表"，
是"怎么连"。

> **这一节的主因后来被找到并修掉了，读的时候要知道。** 本节写于只能看到 outcome 的时候。
> 加上 `attempts` 字段后发现：run1/v3-pinned 的 capped 里,**112/150 是
> `r_ambiguous_fold` 在 Layer 1 拒掉的**（见 §九），不是 join 装配失败。修掉之后
> capped 从 150 掉到 **57**。下面关于"join 拼不出来"的部分对剩下的 57 仍然成立，
> 但它不再是这个桶的主因。

**不是超时。** `authors` 有 18/21 是 capped，实测其最终语句都是秒级：

```
train_3518: 1.0s  rows=5      train_3515: 0.2s  rows=1      train_3510: 0.0s  rows=3
```

而 train_3518 的最终语句与 gold 语义等价。把 133 条 capped 的最终语句全部重新执行：

```
ALREADY_CORRECT   23
wrong            103
exec_error         7
```

> **23 个 capped 轮次的最后一条语句就是正确答案，被记 0 分。**

机制在 `eval/harness.py`：预测只在 `outcome == "answered"` 时才被执行；`grade_turn` 对
`capped` 直接返回 `correct=False`，不看 SQL。

**这是一个成立的记分政策**——耗尽尝试、没有对答案背书的引擎，不该拿到它自己都不敢交付
的分。但它有代价，而代价此前不可见。现在 `computed_correct` 记录它、`--replay-routing`
之外的报告打印它，记分规则不变。

配套机制（`serve/tools.py`）：每次 `run_query` 都扣配额，**包括被治理拒绝的那一次**；
只有基础设施异常才 refund；agent 从未被告知还剩几次。所以它在盲目预算：

```
train_5116 (address)  gold 需要 congress ⋈ zip_congress
           PRED: SELECT DISTINCT district_zip FROM address.zip_congress LIMIT 5
train_3510 (authors)  gold 需要 Journal ⋈ Paper
           PRED: SELECT Keyword FROM authors.Paper WHERE Year=2008 LIMIT 3
```

---

## 六、不可约语义错误〔run1 / 213 例〕

把 pred 和 gold 都执行，按结果集关系分类：

| 差异形态 | n | 占比 | 含义 |
|---|---:|---:|---|
| **值完全不相交** | **183** | **85.9%** | 算的根本不是同一个量 |
| 少行（pred ⊂ gold） | 9 | 4.2% | 多了过滤 / join 丢行 |
| 多行（pred ⊃ gold） | 7 | 3.3% | 少了过滤 / 缺 DISTINCT |
| 部分重叠 | 6 | 2.8% | join 粒度错 |
| 行数相同值不同 | 4 | 1.9% | 去重语义 |
| pred 为空 | 3 | 1.4% | 过滤过紧 / 字面量错 |

**183 个"值完全不相交"里 151 个是单行结果**——主导性的语义失败是「算出了一个标量，
而这个标量是错的」。不是列表问题，是计算问题。

### 病例

**字面量落地失败 — train_5821 (airline)**

```sql
-- GOLD
SELECT COUNT(*) FROM airline.Airlines WHERE FL_DATE='2018/8/1' AND ORIGIN='JFK'
-- PRED
SELECT COUNT(*) FROM airline.Airlines T2 JOIN airline.Airports T1 ON T1.Code=T2.ORIGIN
WHERE T2.FL_DATE='2018/8/1' AND T1.Description LIKE 'New Yo%'
```

agent 不知道 `ORIGIN` 直接存机场代码，跑去 join 机场表按描述模糊匹配。这是语料的
列值/枚举描述该承担的活。

**跨 schema 串台 — 全库 22 例**，配对全是语义相邻的诱饵对，见
[open-work §1.4](open-work.md)。这些轮次 gold schema **是被路由到了的**——是授权集内部
的消歧问题，不是召回问题。

**gold 本身可议 — train_7810 (hockey)，340 vs 339 行。** gold 带一个冗余的
`AND NOT spieler_id IS NULL`。属于 [Pervasive Annotation Errors Break Text-to-SQL
Benchmarks](https://arxiv.org/abs/2601.08778)（CIDR 2026）在 BIRD 上测到的 52.8% 标注
错误率那一类。

**方言级去重 — train_8833 (food_inspection)。** gold 用 Postgres 专有的
`DISTINCT ON (betrieb_id)`，pred 用普通 join，行数相同值不同。

---

## 七、冻结字面量 gold（127）〔数据集属性，各臂一致〕

127 道题的 gold 不是查询，是硬编码的答案字面量：

```sql
SELECT "v"."c0" FROM (VALUES ('captain eli''s')) AS "v"("c0")
```

引擎写真实查询，只能靠复现冻结的形状才能对上——匹配了 42 个，基本靠运气。
这是数据集的属性，不是引擎的。

现在由 `attach_quality_flags` 的第四个 flag `degenerate` 自动标注，判据与
`table_coverage` 的 `gold_reads_no_table` 是同一条规则（`gold_tables()` 返回空集），
两处读同一个判断而不是各写一份。

**口径纪律**：对外报 **0.579**（与公开 BIRD 同口径）。剔除后的 0.604 只能作为补充并
注明是单边剔除——把 10.0pp 的差距说成 7.5pp 是口径不一致，不是结果。

---

## 八、弃权质量：这个引擎真正与众不同的地方〔含 v3-fold〕

| 臂 | committed | 弃权率 | 弃权若强行提交 | 弃权精确率 |
|---|---:|---:|---:|---:|
| run1 | 0.658 (n=1189) | 12.0% | 0.204 | 0.796 |
| run2 | 0.655 | 12.9% | 0.168 | 0.832 |
| v3-pinned | 0.702 | 12.9% | 0.195 | 0.805 |
| **v3-fold** | **0.709** | **6.4%** | — | **0.812** |

**v3-fold 是这个论点最强的一版**：弃权率腰斩（12.9% → 6.4%）而精确率保持，
说明减少的是 bug 造成的弃权，不是判断力。而且它的 refused/clarification
**全部**落在检索失败的题上。

交付集准确率是弃权集的 **3.2 倍**。若弃权是随机的，弃权集应该也在 0.579 附近。

这与 EX 正交：一个 EX 更高的系统，其错误答案里没有任何信号告诉你是哪些。而这个引擎的
弃权行为不是叙事，是机制——18 个拒绝是 check Layer 6 在报告检索失败，8 个澄清是授权集
为空。**治理层在充当"我不知道"的检测器。**

要让它成为结论而不是观察，需要一根对照臂：同一批题关掉治理层，看那些被拒绝的题在无治理
时是否真的会编出错答案。见 [open-work §4.1](open-work.md)。

---

## 九、`r_ambiguous_fold`：一个 8pp 的缺陷，靠一个字段才看见〔run1→v3-fold〕

`attempts[].reason_code` 这个字段 2026-08-09 才进产物。它一进去，最大的一项立刻显形：

```
v3-pinned:  PARSE/r_ambiguous_fold   568 次尝试 / 119 轮（8.8%）
              其中 112 轮以 capped 收场，那批的 EX = 0.025（未受影响 0.668）
              吃掉全部输入 token 的 24%
```

**机制**：`spellings_for` 把一轮全部被授权表（约 26 张、跨约 8 个 schema）的名字压成
**一个平面命名空间**，任意两个只差大小写的名字（`Name` / `name`）就让**任何**对它们的
引用被拒 —— 包括写全了限定的 `T1."Name"`。规则的意图（大小写折叠可能落到诱饵上）是对的，
作用域错了一级。

**修法**：限定引用按自己那张表解析；别名表只登记在别名下（Postgres 用别名遮住表名）；
一个句柄在全树里指向两张表就丢弃、回落到原行为。

**结果**（v3-pinned → v3-fold，同 prompt）：

```
r_ambiguous_fold   568 次 / 119 轮  →  109 次 / 35 轮
attempt_cap        150  →  57            capped 率 11.1% → 4.2%
EX                 0.611 → 0.664         net +71  χ²=25.9  p=2.4e-07
输入 token         87.2M → 74.7M         −14.4%
```

**这条的教训不是"有个 bug"，是"它躺了不知道多久，因为记录它的字段停在 `stamp`"。**
加那个字段的回报比同期任何 prompt 干预都高。

---

## 十、上下文预算：一个我测错过的量〔v3-fold 首测〕

`context_budget_chars = 80000`，块约 22,285 token，**每次模型调用重发一遍**。

我曾用离线重建估出「中位 79,866/80,000，64% 的轮次触发驱逐」，并据此反对削减预算。
**那个估计是错的。** 重建时我用「所有被授权表 + 它们的所有列」构造 `retrieved`，忽略了
pass-two 实际施加的按类型预算（列 30、join 5……），所以造出的上下文远大于真实的。

`context_evicted` 落地后的真实测量：

```
触发驱逐  19/1351 = 1.4%      而且只有 bodies_dropped —— 一张整表都没丢过
```

**预算不是约束。** 要问的是"这些内容值不值得"，不是"装不装得下"。

配套的精确量（`model_calls` 首次落地）：

```
agent_core 真实调用 3,308 次 / 1,345 轮 = 每轮 2.5 次
重复前缀 = (3308 − 1345) × 22,185 = 58.3% 的全部输入   ← 实测，不再是上界
```

代理是 OpenAI 兼容的，长前缀自动缓存且不报缓存计数，所以这 58.3% 是靠 `model_calls`
算出来的，不依赖 provider。

---

## 附录：外部参考

- [Pervasive Annotation Errors Break Text-to-SQL Benchmarks and Leaderboards](https://arxiv.org/abs/2601.08778)
  — BIRD 标注错误率 52.8%
- [The Death of Schema Linking?](https://arxiv.org/html/2408.07702) — 激进裁剪丢失 22.6%
  必需元素；本次 0.925 的覆盖率说明当前配置不在那个陷阱里
- [CHASE-SQL](https://arxiv.org/html/2410.01943v1) / [DPC](https://aclanthology.org/2026.acl-long.313/)
  — 候选生成 + 选择，BIRD 73.01%。成本高且与本项目的治理论点无关
