# 路由重设计 · R1 / 融合 / 置信门控

2026-08-01。承接 [E1](../experiments/e1-shortlist-ablation.md)。同一份 curated 语料
(`runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z/corpus_curated`,57 schema),
同一批 test split 1351 题,同一次 Opus-4.8/high 跑出来的 `generations.curated.jsonl`。

本文里的每个数都来自两个新的**离线**探针,**都不调用 chat 模型**:

| 脚本 | 产物 | 花了什么 |
|---|---|---|
| `scripts/pick_evidence_probe.py` | `runs/ablation/e2-pick-evidence.json` | 纯 CPU;加 `--embedder` 时 656 表 + 1351 题 embedding |
| `scripts/routing_fusion.py` | `runs/ablation/e3-fusion.json` + `e3-rankings.json`(排名缓存) | 约 33 万 embedding token,总计 **$0.05 以下**,7 分钟 |

对照:**一条完整 curated 臂是 50.3M token / 1.7 小时**,其中 router 一个人占 25.0%(12.4M 入 / 0.16M 出)。

---

## 0 · 三个问题,三个答案

| | 结论 | 证据强度 |
|---|---|---|
| **R1**(picker 看哪 15 张表) | 按题目相关性排,用**表级 embedding**。gold 表「全部可见」率 **0.840 → 0.970**;宽 schema 上 **0.400 → 0.886** | 可见性:**实测**。可见性 → pick 准确率:**未建立**(schema 内符号检验 p=0.50)。需要干预实验 |
| **融合**(BM25 + embedding) | **不要**融进 k=10 的 shortlist —— 但 docstring 给的理由和数都是错的。真正该换的是**表级 max-pool 通道**:@1 0.694→0.730,@3 0.850→**0.893**,@10 0.952→**0.973**,token 反而少 26% | **实测,直接命中该通道的目标指标(召回)**,不需要 LLM |
| **置信门控** | **离线被证伪。**margin 门控在每个阈值上净收益 ≤ 0。picker 比 rank-1 强 17.9pp:它纠正了 264 个错的 rank-1,只覆盖错 21 个 | **实测。**剩下唯一站得住的用途是**省钱**,不是提准 |

一句话:**能凭现有证据直接上的是通道换 `tblmax`;R1 值得上但必须带一个便宜的干预实验;门控不要做。**

---

## 1 · R1:排序 picker 看到的那 15 张表

### 1.1 机制复核

`retrieval/schema_router.py:46` `SCHEMA_PICK_MAX_TABLES = 15`,`_schema_pick_summary` 按
`physical_name` 排序后截断。57 个 schema 里 **9 个**超过 15 张表(中位数 8,均值 11.5),
它们是 325 道题的 gold。`works_cycles` 有 73 张,picker 永远只看 A–D 开头那 15 张。

### 1.2 gold 表到底可不可见(E2,1222 题)

「可见」= 该题 gold SQL 读到的**每一张**表都出现在 picker 摘要的 15 行里。
(1351 题里 129 题的 gold SQL 是 `SELECT ... FROM (VALUES ...)` 的冻结常量,不读任何表,剔除。)

```
population                       alpha        rel  rel_guard rel_descon    rel_emb
全部 1222 题                     0.840      0.948      0.875      0.951      0.970
gold schema > 15 张表 (n=325)    0.400      0.806      0.529      0.815      0.886
被误选的 (n=88)                  0.682      0.875      0.739      0.875      0.909
  其中 gold 排第 1 的 (n=18)     0.722      0.889      0.778      0.889      0.944
```

变体定义:

- `alpha` —— 今天。
- `rel` —— schema 内 BM25 over `asset_document(table)`(标识符 + 描述),有分的排前面,其余按字母序补齐。
- `rel_guard` —— `rel`,但只对「表描述覆盖率 ≥ 50%」的 schema 生效。
- `rel_descon` —— BM25 **只 over 策展散文**(表描述 + grain + 列描述),不含任何物理标识符。
- `rel_emb` —— 表级 embedding 余弦(`text-embedding-3-large`,和 §2 的 `tblmax` 通道**是同一批向量**)。

分 schema(只有这 9 个 schema 会被截断影响,其余 48 个所有变体完全一致):

```
schema                    表数   题数  desc%      alpha        rel  rel_descon    rel_emb
works_cycles                73     65   0.11      0.077      0.831      0.831      0.969
public_review_platform      18     44   0.33      0.591      0.977      0.977      1.000
mondial_geo                 42     39   0.00      0.179      0.154      0.179      0.231
movie_3                     21     39   1.00      0.718      0.923      0.974      1.000
books                       19     34   1.00      0.676      0.941      0.941      1.000
hockey                      29     29   0.28      0.034      0.897      0.897      0.897
soccer_2016                 27     27   1.00      0.333      0.889      0.889      0.963
formula_1                   17     24   0.00      0.875      0.875      0.875      1.000
movies_4                    21     24   1.00      0.417      0.833      0.833      0.958
```

`works_cycles` 0.077、`hockey` 0.034 —— **今天 picker 在这两个 schema 上,96% 的题看不到答案要用的表。**

### 1.3 「相关性打平的时候会怎样」—— 有一个变体真的会把事情弄坏

`mondial_geo`:42 张表,**0 张有表描述,275 个列 0 个有列描述**,表名全是拼音(`guo_jia` / `min_zu_zu`)。
`rel` 在这里把它**弄糟了**(0.179 → 0.154)。原因可查:

```
Q: Provide the country with its full name which has the most ethnic group?
gold: guo_jia, min_zu_zu
rel top-8: shan_zai_dao_yu 1.856  di_li_he 1.805  di_li_shan 1.805  di_li_hu 1.756 ...
           全部命中的词只有一个:name(某些表的列标识符里漏出来的英文)
```

一个纯噪声的标识符匹配,把两张 gold 表从「字母序恰好排得进」的位置挤了出去。
**这就是「相关性打平」的真实形态:不是所有表都得 0 分,而是少数表因为无意义的匹配得了分。**

两个候选补丁,一个有效一个无效:

- `rel_guard`(按表描述覆盖率设阈值)**是坏补丁**。它确实保住了 `mondial_geo`,但同时把
  `works_cycles`(0.11)和 `hockey`(0.28)整个关掉 —— 这两个恰好是收益最大的。
  表描述覆盖率**不是**「有没有策展语言」的正确代理:这两个 schema 的语言在**列描述**里。
- `rel_descon`(只用散文,不用标识符)**是好补丁**,而且**自带守卫**:
  `mondial_geo` 的散文索引是空的,BM25 一个都排不出来,自动退化回字母序,和今天逐字节相同。
  不需要任何阈值,不需要任何 schema 白名单。

`rel_emb` 比它们都好,并且**在 9 个宽 schema 上一个都没输给 `alpha`**(`mondial_geo` 0.179→0.231,
`formula_1` 0.875→1.000)。

### 1.4 `works_cycles` 的三道真题:改之前 / 改之后

今天(和题目无关,73 张表永远是这 15 张):

```
Address, AddressType, BillOfMaterials, BusinessEntity, BusinessEntityAddress,
BusinessEntityContact, ContactType, CountryRegion, CountryRegionCurrency, CreditCard,
Culture, Currency, CurrencyRate, Customer, Department
… (58 more tables)
```

改之后(`rel_descon`,纯 CPU,不需要 embedding):

| 题 | 15 张表 | 结果 |
|---|---|---|
| `train_7005`<br>*"Provide all the transactions whereby the quantiy is more than 10,000 pieces. State the product name and the selling price."*<br>gold = `Product`, `TransactionHistory`<br>实际被误选到 `sales`(6 张表,完整展示) | ProductListPriceHistory, StateProvince, **TransactionHistory**, SalesOrderDetail, TransactionHistoryArchive, **Product**, Person, ProductVendor, ProductDocument, ProductCostHistory, SalesReason, PurchaseOrderDetail, Address, ProductInventory, SalesTaxRate | 两张 gold 表都进来了 |
| `train_7041`<br>*"Which job title has the lowest pay?"*<br>gold = `Employee`, `EmployeePayHistory`<br>实际被误选到 `food_inspection_2` | **EmployeePayHistory**, JobCandidate, **Employee**, StateProvince, Document, Product, Person, WorkOrderRouting, ShoppingCartItem, ProductListPriceHistory, ProductCostHistory, ProductInventory, ProductDocument, TransactionHistory, CurrencyRate | gold 排 1、3 |
| `train_7001`<br>*"List the products whereby the standard cost is $80 more than previous standard cost in history."*<br>gold = `Product`, `ProductCostHistory`<br>选对了 schema,但 SQL 错 | **ProductCostHistory**, **Product**, ProductListPriceHistory, ProductVendor, Location, WorkOrderRouting, ProductDocument, TransactionHistory, ShoppingCartItem, Document, ProductInventory, TransactionHistoryArchive, SalesPerson, EmployeeDepartmentHistory, SalesTerritory | gold 排 1、2 |

同一份 prompt 要求模型「flag any part no table can supply」。在改之前,对前两道题,
**诚实执行这条指令的唯一正确答案就是「`works_cycles` 供不了」** —— 模型没有做错,它被喂了假证据。

### 1.5 但是:**「可见」导致「选对」这件事没有被建立**

观察性对比看起来很漂亮:

```
gold 表全部可见   pick_hit 919/979 = 0.939    EX 0.636
gold 表不全可见   pick_hit 158/186 = 0.849    EX 0.548
```

按 gold 排名分层后就缩水到 +4pp 左右(rank1:0.983 vs 0.948;非 rank1:0.786 vs 0.742)。
再做 **schema 内对照**(把 schema 难度固定住,只比同一个 schema 里「字母序恰好抽中 gold 表」和
「恰好没抽中」的两组),就什么也不剩了:

```
schema                  可见 n  hit    不可见 n  hit     delta
books                      22  0.773       11  0.818   -0.045
formula_1                  20  1.000        3  1.000    0.000
hockey                      1  1.000       28  0.929   +0.071
mondial_geo                 7  0.714       26  0.692   +0.022
movie_3                    26  0.962       11  1.000   -0.038
movies_4                   10  0.700       13  1.000   -0.300
public_review_platform     25  1.000       18  1.000    0.000
soccer_2016                 9  1.000       18  0.778   +0.222
works_cycles                4  1.000       58  0.793   +0.207
```

**符号检验 4 正 / 3 负,单边 p = 0.50。**

顺便,「小 schema 因为展示完整所以更容易被误选进去」这个说法也**没通过检验**。
106 次误选里 gold 比被选中的 schema 宽的有 72 次(0.679),而在候选里均匀乱选的期望值就是 0.615
—— 单边二项 p = 0.104;「被选中的 schema 是完整展示的」实测 0.849 vs 期望 0.822,p = 0.278。
**这个不对称主要是「宽 schema 本来就常出现在候选里」,不是 picker 偏爱完整证据。**

这和 `analyst_max_table_columns` 那次是同一个形状:**机制清清楚楚,剂量反应不存在。**
所以 R1 **不能**当成「已经证明的收益」上线,它需要一次干预(§4 的 E4)。

上限也要说清楚:88 道可分析的误选里,**68.2% 在今天就已经看得到全部 gold 表**,R1 碰不到它们。
`rel_emb` 新增看得见的只有 20 道。**即使这 20 道全部翻盘,也就是 +1.5pp pick / 约 +0.9pp EX。**

### 1.6 还有一个方向相反的风险,离线测不出来

R1 **对称地**改写每一个候选的摘要,不只是 gold 的。一个宽的干扰项(`works_cycles`、`movies_4`)
今天摆出一堆字母序的无关表,改完之后摆出的是**和问题最像的 15 张表**。
**它也会变得更有说服力。**§4 的分层设计就是为了让这一项单独可测。

### 1.7 落地形态

```
_schema_pick_summary(corpus, schema, *, order: Sequence[str] | None = None, ...)
```

排序由**调用方注入**,不在这个函数里算 —— 它今天是纯函数且被单测覆盖,不应该长出一个索引依赖。
`order=None` 时保持字母序,**逐字节等于今天**。`pick_schema` 负责算 order 并传下去。

- 无 embedder / embedding 通道降级时:`rel_descon`(纯 CPU,BM25 over 散文)。
- 有 embedder 时:`rel_emb`,复用 §2 的表向量,**零额外网络调用**。
- 两种情况下,没排出名次的表一律按字母序补齐;摘要末尾的 `… (N more tables)` 保留。
- `SCHEMA_PICK_MAX_TABLES` 的 docstring 要改:它现在说「提高它等于 papering over R1」;R1 修完之后,
  这句话的前提变了,但结论不变(15 是预算,不是排序策略)。

**成本:** 纯 CPU 分支 —— 9 个宽 schema 各建一个 BM25 索引,一次;每题排 ≤73 篇文档,亚毫秒。
embedding 分支 —— 见 §2.5,和通道共用向量,增量为零。

---

## 2 · 融合:重测之后的答案

### 2.1 先确认重建是忠实的

`routing_fusion.py` 重算的 `emb_large` 排名对上了那次跑记录下来的 `shortlisted_schemas`:

```
gold 排名一致  0.970    rank-1 身份一致  0.990    top-10 集合一致  0.882    top-10 顺序一致  0.554
```

顺序在第 6–10 位上有零星差异(那里的余弦间距在 1e-4 量级,批量 embed 和逐条 `embed_one` 在浮点上会分手)。
门控分析只用到 rank-1 / rank-2 的身份和 gold 的名次,这两项一致率 0.99 / 0.97。

### 2.2 通道表(1351 题,test split)

```
channel                                           @1      @3      @5     @10     @20
bm25                                           0.736   0.844   0.879   0.906   0.920
bm25_tbl_max                                   0.617   0.770   0.823   0.870   0.897
emb_large            ← 今天在跑的              0.694   0.850   0.906   0.952   0.979
emb_small                                      0.668   0.845   0.891   0.930   0.959
tblmax_large                                   0.730   0.893   0.939   0.973   0.991
tblmax_small                                   0.680   0.867   0.911   0.943   0.967
rrf(bm25,emb_large) w_lex=0.5                  0.713   0.856   0.898   0.933   0.947
rrf(bm25,emb_large) w_lex=1.0                  0.733   0.871   0.898   0.922   0.943
rrf(bm25,emb_large) w_lex=2.0                  0.743   0.865   0.900   0.918   0.928
rrf(bm25,emb_large) k=10                       0.733   0.873   0.908   0.942   0.966
rrf(bm25,emb_large) k=20                       0.734   0.871   0.903   0.931   0.950
rrf(bm25,tblmax_large)                         0.744   0.879   0.908   0.923   0.944
rrf(emb_large,tblmax_large)                    0.710   0.887   0.931   0.976   0.991
rrf(bm25,emb_large,tblmax_large)               0.750   0.879   0.916   0.941   0.950
rrf(bm25,bm25_tbl_max,emb_large,tblmax_large)  0.756   0.870   0.899   0.909   0.919
```

`tblmax_*` = **每张表各自 embedding,schema 得分取它所有表的最大余弦**(max-pool),
而不是把整个 schema 拼成一篇文档再 embedding。

> `emb_large` 这一行和 E1 的表差在小数点后第三位(@3 0.850 vs 0.852,@10 0.952 vs 0.953):
> E1 逐题 `embed_one`,本文按 256 一批 `embed`,同一个模型同一个端点在浮点上不完全一致。
> 两张表内部各自可比,**跨表不要做减法**。

### 2.3 「要不要融 BM25」

**不要 —— 但 docstring 里的理由和数字都得删掉。**

`shortlist_schemas` 说 BM25 recall@3 = 0.35、RRF 0.535、「弱词法信号把强向量排名拖下水」。
实测 BM25@3 = 0.844,RRF(bm25, emb_large)@3 = 0.871 —— **RRF 在 @3 上是赢 emb_large(0.850)的**,
在 @1 上赢得更多(0.733 vs 0.694)。那条注释描述的是**另一个语料**(无描述的 baseline/seeded)的性质。

真正的理由是另一个,而且只在**当前配置下**成立:

**融合用 top-1 精度换深处召回。** 管线跑的是 `route_top_k=10`,**@10 才是决定 pick 有没有机会的那一格**,
而 BM25 自己 @20 的天花板只有 0.920 —— RRF 是纯位次融合,它把向量通道那条又长又准的尾巴拉向了这个天花板:

```
                    @1              @10
emb_large        0.694           0.952
+bm25 (RRF)      0.733 (+3.9)    0.922 (−3.0)
```

**所以:在 k=10 的 shortlist 上不融;如果哪天 `route_top_k` 降到 1–3(比如为了省 picker 的 token),
这个结论要重测,因为那时 @1/@3 才是目标格。** 这个重测是免费的 —— 排名缓存
`runs/ablation/e3-rankings.json` 已经落盘,换个 k 重新读表就行。

### 2.4 真正该换的东西:表级 max-pool

`tblmax_large` **在五个 k 上全部胜过 `emb_large`**:

```
              @1      @3      @5     @10     @20
emb_large  0.694   0.850   0.906   0.952   0.979
tblmax     0.730   0.893   0.939   0.973   0.991
delta      +3.6    +4.3    +3.3    +2.1    +1.2
```

机制和 R1 是同一个:`works_cycles` 的 schema 文档是 73 张表拼起来的,一道关于销售订单的题,
是在跟一个把工资、采购、地理平均在一起的向量比余弦。**max-pool 换掉的是「拼接=求平均」这个隐含假设。**

两个反例,划定这个想法的边界:

- **`bm25_tbl_max`(词法通道也 max-pool)是负结果**:0.870@10,比 `bm25` 的 0.906 还差。
  max-pool 只对向量通道有效 —— BM25 的分数带长度归一化,把它 max-pool 等于奖励短文档。
- **`assetmax`(把 metric / few-shot / term 也一起 pool,2810 篇文档)也是负结果**:
  @1 0.686、@10 0.942,比只 pool 表还差。一个 term 的文档就是 `name synonym1 synonym2`,
  短文档跟短问题的余弦会虚高。**表是正确的粒度**:它长到不被长度伪影支配,又是答案真正的单位。

补充上界,说明还剩多少:

```
tblmax@10                        0.973
tblmax@10 ∪ emb_large@10         0.988    ← 两个向量视角确实互补
tblmax@10 ∪ bm25@10              0.980
三通道 @10 并集                  0.990
rrf(emb_large, tblmax_large)@10  0.976    ← RRF 只吃到互补性的 1/5
```

按预算切分(`tblmax@8 ∪ emb@2` = 0.970、`tblmax@9 ∪ bm25@1` = 0.973)全都**打不过 `tblmax@10` 自己**。
所以:**主选 `tblmax_large` 单通道;`rrf(emb_large, tblmax_large)` 作为可选项,
@10 多 0.3pp、@3 少 0.6pp,成本相同 —— 不值得为它增加一条代码路径。**

`tblmax@10` 剩下的 36 个漏网:`mondial_geo` 12、`donor` 9、`codebase_community` 3。
**E1 点过同样的名字。这三个是策展缺口(0 描述),不是检索算法问题。**

### 2.5 成本与热路径

```
schema 文档索引(今天):  57 篇, 130,243 token,  $0.017 @ -3-large
表文档索引(提议):     656 篇,  95,750 token,  $0.012 @ -3-large   ← 便宜 26%
```

**比今天还便宜**,因为 schema 文档额外含了 metric / few-shot / term 的文本(而 §2.4 已证明它们该被排除)。

每题的 CPU:

```
今天    57 个向量,纯 Python cosine       26 ms
提议   656 个向量,纯 Python cosine      348 ms   ← 不可接受
提议   656 个向量,numpy matvec          1.17 ms
```

**这条设计需要 numpy 显式进 `dependencies`** —— 它现在只是被依赖树顺带装上的,
`pyproject.toml` 一个字没写,直接依赖里也没有一个无条件要求它,
并且向量要以矩阵而不是 `dict[str, list[float]]` 的形式缓存。8.1 MB / 656×3072 float32。
这不是优化,是可行性:docstring 说这个模块要撑「thousands of tables」,10k 张表时纯 Python 是 5 秒/题,numpy 是 18 ms。

落地形态,和现有结构同构:

- `table_documents(corpus)` / `embed_table_documents(corpus, embedder)`,
  照抄 `_SCHEMA_VECTOR_MEMO` 的**内容哈希 + 锁 + 锁外发请求**那套(那套是为了 2026-08-01 那次
  24 worker 同时启动打爆 1M TPM 限额写的,表级索引会原样继承这个风险)。
- `_embedding_ranking` 增加 max-pool 分支;`channel_out` 的取值增加 `"embedding_table_max"`,
  这样一行记录能说清自己是哪个通道排的。
- 通道选择做成 `Settings` 旋钮并进 `MANIFEST_KNOBS` —— 和
  `MANIFEST_SCHEMA_VERSION` 3 把 `embedding_model` 变成 gate key 是同一个理由:
  **上一次跑到底用了哪个通道,artifact 必须自己说得出来。**
- PIN 前置、fail-open、BM25 降级三条路径**都不动**:max-pool 只换了 `ranked` 是怎么算出来的。

---

## 3 · 给 picker 一个置信信号

### 3.1 假设与它的证伪

假设:「rank-1 的余弦领先足够大时,直接采信 rank-1、不问 LLM」。
证伪方式:在那次跑的 1351 行上直接查表 —— `saved` = LLM 推翻了正确的 rank-1 而门控会拦住它;
`broken` = LLM 修正了错误的 rank-1 而门控会阻止这次修正。用**相对 margin** `(s1−s2)/s1`,
因为余弦的绝对值在题与题之间不可比。

```
emb_large(那次跑真正用的通道)
     t   coverage   saved  broken   net   net pp  gate prec
  0.00      1.000      21     264  -243   -17.99      0.694
  0.05      0.781      13     132  -119    -8.81      0.796
  0.10      0.640       7      67   -60    -4.44      0.867
  0.20      0.420       2      14   -12    -0.89      0.956
  0.30      0.265       0       4    -4    -0.30      0.969

tblmax_large(提议的通道)
  0.00      1.000      50     244  -194   -14.36      0.730
  0.10      0.645      27      56   -29    -2.15      0.902
  0.20      0.432      12      12     0     0.00      0.971
  0.30      0.267       4       1    +3    +0.22      0.994
```

**每一个阈值上净收益都 ≤ 0**(`tblmax` 在 t=0.30 处 +3 题,n=1351,纯噪声)。
`bm25`、`rrf(emb,tblmax)` 两条通道结论相同,已一并落在 `e3-fusion.json` 里。

### 3.2 为什么 —— 「21 个 rank-1 gold 被推翻」是个会误导人的取景

同一批题上把另一半也数出来:

```
rank-1 准确率   0.694
pick 准确率     0.873      ← LLM picker 净赚 +17.9pp
其中:LLM 推翻了正确的 rank-1     21 次
      LLM 修正了错误的 rank-1    264 次
```

**picker 每犯 1 次这种错,就纠正 12.6 次检索的错。**给它加一个「检索很自信」的门,
等于在一个 12.6:1 的对赌上站到少数派那边。21 这个数字单独看很扎眼,但它不是可回收损失。

### 3.3 对冲(hedge 到多个 schema)

```
gold 在 rank-1        0.694
gold 在 rank ≤ 2      0.790
gold 在 rank ≤ 3      0.850
LLM pick 准确率       0.873      ← 比 top-3 全要还高
```

**「不选了,把前 2 个(甚至前 3 个)schema 全给下游」的天花板,低于 picker 今天的水平。**
对冲不能替代 pick。

作为**补充**(pick 之后再补一个 rank-1)也不划算:`pick_hit=False` 且 gold 在 top-2 的只有 56 题(4.1pp),
代价是 D15 的单 schema 不变量 —— `agent.py:830` 的 `routed = frozenset([picked])` 一旦变成两元素,
`filter_corpus_for_retrieval` 就会同时放行两个 schema 的表,L4 许可集翻倍,
而**跨 schema join 只在有策展 `JoinAsset` 时才被允许** —— 两个不相关的 schema 之间没有这种边,
模型拿到的是一个「看得见但连不起来」的表集。这正是 D15 当初收敛到单 schema 的原因。
**为 4.1pp 的上限去动这条不变量,不划算。**

### 3.4 唯一还站得住的用法:省钱,不是提准

`tblmax_large`、t=0.20:**覆盖 43.2%,saved 12 / broken 12,净 0。**
router 占整臂 25.0% 的 token(12.4M 入 / 题均 9185 入 + 117 出)。
**43% 的 picker 调用换 0 的准确率变化,≈ 整臂 token −11%。**

要不要做,取决于是不是在跟 token 预算搏斗。**如果做,它必须被记成成本旋钮,
manifest 里带上阈值,并且在 arm summary 里报 `gate_fired_rate`** —— 否则下一个人会把它读成一个提准手段,
然后拿它去解释一个它不负责的 EX 变化。**默认关。**

---

## 4 · 落地顺序,和唯一需要花钱的那个实验

### 步骤 1 —— 通道换成 `tblmax`(可以凭现有证据上)

它的目标指标是召回,召回已经在 1351 题上直接测过了,不需要 LLM 来确认。
**验收:** shortlist@10 从 0.952 升到 0.973 ± 0.005(用 `routing_fusion.py` 复跑,几分钟)。
**如果 EX 没动,不要惊讶也不要回滚** —— @10 只是让 pick 有机会,+2.1pp 的召回最多值 +1.2pp EX
(2.1 × pick 0.873 × EX|pick 0.641),
淹没在单 seed 的方差里。要看的是 shortlist 召回本身。

### 步骤 2 —— R1,并且必须带 E4

**E4 = pick-only 台架。**只跑 `shortlist_schemas` + `pick_schema`,不生成 SQL、不执行、不评分;
用 `db_id` 直接判 pick 对错。

- 成本:1351 次调用 × 9.2k 入 / 117 出 = **12.6M token,约整臂的 25%、整条梯子的 6%**,没有 1.7 小时的 serve 循环。
- 便宜模型上先筛一遍(deepseek/luna),再用 Opus 复核结论方向。
- **预注册的分层**,因为 §1.6 那个反向风险只有分层才看得见:

| 层 | n | 今天 pick_acc | R1 的作用 | 预期 |
|---|---|---|---|---|
| **A** 候选里没有宽 schema | 144 | 0.938 | **prompt 逐字节不变** | A/A 对照。动了就是模型噪声,给整个实验定噪声底 |
| **B** 只有宽**干扰项**,gold 是窄的 | 838 | 0.885 | 只让干扰项更像 | **风险层。跌了就说明 §1.6 的风险是真的** |
| **C** gold 是宽 schema | 369 | 0.821 | gold 证据可见率 0.400 → 0.886 | 收益层 |

- **证伪条件(先写下来):** C 层 pick_acc 不升,或 B 层的跌幅吃掉 C 层的涨幅 → **R1 不上,回字母序。**
- 上限提醒:§1.5 算过,R1 全局最多 +1.5pp pick。**C 层要能看出信号,靠的是它自己 0.400→0.886 的幅度,
  而不是全局那个被稀释过的数。**

### 步骤 3 —— 门控:不做(除非在为 token 预算搏斗)

### 顺带,几处必须改的文字(它们现在正在管着架构决策)

- `shortlist_schemas` docstring 里那段「0.70 / 0.35 / RRF 0.535,所以我们不融」—— 数是错的,
  结论(在 k=10 上)碰巧对。换成 §2.3 的真实理由,并写清它对 `route_top_k` 敏感。
- `_log_embed_failure` 的告警文案里写着「recall@3 drops 0.70 -> 0.35」。实测是 **0.850 → 0.844**。
  **降级远没有它宣称的那么严重**,这条告警现在会把一次无关紧要的降级报成灾难。
- `SCHEMA_PICK_MAX_TABLES` 的 docstring:R1 修完之后要说明它现在只是预算。

---

## 5 · 我没测的,以及什么会推翻本文

- **R1 对干扰项的影响没有离线测量方式。**§1.6 是一个论证,不是一个数。E4 的 B 层是唯一的检验。
- **门控分析是反事实的。**它假设换了排序之后 LLM 的选择不变,而候选集本身会变。
  结论(净收益 ≤ 0)在四条通道上一致,方向应该稳,但绝对数不要引用。
- **`tblmax` 的收益只在这一份 curated 语料、这一个 embedder 上测过。**
  它的机制(反拼接稀释)预测:**schema 越宽收益越大**。这是个可证伪的预言,
  在一个表数分布不同的 lake 上应该重测。
- **全部数字来自单个 seed 的那次 Opus 跑。**`pick_hit` / `correct` 是那一次的;
  召回和可见性是确定性的(不依赖那次跑)。前者会随 seed 变,后者不会。
- **1351 题里 129 题的 gold SQL 不读任何表**(冻结常量),E2 的分母是 1222,不是 1351。
  §2 的召回分母是 1351。两张表不要交叉相除。
