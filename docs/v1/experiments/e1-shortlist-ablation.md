# E1 · 离线 shortlist 消融

2026-08-01。`scripts/routing_ablation.py`,curated 语料(20260731 那次跑的 `corpus_curated`,57 schema),test split 全量 1351 题,**不调用任何 chat 模型**。

```
channel                     @1      @3      @5     @10     @20   never@20    sec
bm25_only                0.736   0.844   0.879   0.906   0.920       108    1722
text-embedding-3-small   0.668   0.845   0.891   0.930   0.959        55     360
text-embedding-3-large   0.694   0.852   0.906   0.953   0.979        29     463
```

成本:几十万 embedding token。**一条完整 curated 臂是 50.3M token / 1.7 小时。**

---

## 1 · 它先确认了一件出处上的事

`-3-large @10 = 0.953`,而我从 20260731 那次跑的原始行里重算出的 shortlist 召回是 **0.952**。`-3-small` 只有 0.930。

对上了,而且这解释了一个此前的困惑:**那次 Opus 跑用的就是 `text-embedding-3-large`** —— results 文档第 52 行写着「embeddings 3072-dim」、第 58 行写着「`text-embedding-3-large` 不在价格表里」,而**仓库默认是 `-3-small`,manifest 一个字都没记**。

所以把默认改成 `-3-large` **不是引入一个新变量,是对齐**。这也正是 `MANIFEST_SCHEMA_VERSION` 3 把 `embedding_model` 变成 gate key 的理由:上一次跑的服务端把它换掉了,artifact 无从知晓。

## 2 · `schema_router` 里那条决定性注释,数据已经不支持了

`shortlist_schemas` 的 docstring:

> A probe over the 2030-question pool measured embedding-only recall@3 = **0.70** vs BM25 **0.35** vs BM25+embedder RRF 0.535 — fusing the weak lexical signal measurably *drags the strong embedding ranking down*, so we do not fuse.

在 curated 语料上实测:

| | 注释声称 | 本次实测 |
|---|---|---|
| embedding @3 | 0.70 | **0.852** |
| BM25 @3 | 0.35 | **0.844** |
| 差距 | 2 倍 | **0.8pp** |

**BM25 不是弱信号,它和 embedding 在 @3 上基本打平。**而且:

**在 @1 上 BM25(0.736)反过来赢 embedding(0.694 / 0.668)。**

这不难解释 —— 那条 probe 大概率跑在**没有描述的语料**上(`baseline` / `seeded` 臂的表描述覆盖率是 0),而 curated 语料给了 BM25 大量可匹配的自然语言。**注释描述的是另一个语料的性质,却在管着当前的架构决策。**

「不融合」这个决定因此**建立在一个已经失效的测量上**。两个通道的形状还是互补的:词法通道 top-1 精度更好,向量通道深处召回更好 —— 这正是 RRF 该赢的形状。**值得重测,而且成本是这张表的量级,不是一条梯子的量级。**

## 3 · 但它同时说明:检索不是瓶颈

`-3-large @10` 已经 **0.953**。剩下 4.7% 从没进过 shortlist。

而 pick 准确率是 **0.873**。两者之间那 **7.8%(106 题)** 是「检索找到了,pick 扔掉了」,其中只有 3 题最后还答对了。

```
pick 完美(shortlist 不变)  0.952 x 0.641 = 0.610   (+4.7pp)
路由完美                    1.000 x 0.641 = 0.641   (+7.8pp)
```

**所以 embedder 升级最多值 2.3pp 的召回(且已经在用),而 pick 值 4.7pp 的 EX。下一个实验的靶子是 pick,不是检索。**

## 4 · pick 为什么输:它看到的是按字母序的前 15 张表

`schema_router.py:44` `SCHEMA_PICK_MAX_TABLES = 15`,`:362` 按 `physical_name` 排序后截断。**没有任何按问题相关性的排序。**实际渲染出来:

```
works_cycles: 73 张表,picker 看 15 张
SHOWN : Address, AddressType, BillOfMaterials, BusinessEntity, BusinessEntityAddress,
        BusinessEntityContact, ContactType, CountryRegion, CountryRegionCurrency,
        CreditCard, Culture, Currency, CurrencyRate, Customer, Department
HIDDEN: Employee, Person, Product, SalesOrderHeader, SalesOrderDetail, SalesPerson,
        PurchaseOrderHeader, Store, Vendor, WorkOrder, ...(共 58 张)
```

一道关于销售订单或员工的题,`works_cycles` 的摘要里**一张相关的表都没有**,只有一行 `… (58 more tables)`。而同一份 prompt 要求模型「flag any part no table can supply」。

对照:`world` 只有 5 张表,picker 看到它的**全部内容** —— 而 `world` 是最大的误选吸引子之一(10 次)。**小 schema 拿完整证据,大 schema 拿截断证据,两者在同一个排序里竞争。**

这个常量的 docstring 自己写着:提高它等于「papering over R1(排序该显示哪 15 张)而不是修它」。**R1 至今没修,而它现在是全管线上最大的一笔可回收损失。**

## 5 · 检索确实失败的地方(`-3-large` @10)

```
codebase_community   0.321  n=28
donor                0.412  n=17
car_retails          0.789  n=19
book_publishing_...  0.846  n=13
mondial_geo          0.857  n=42
```

`codebase_community` 有 68% 的题从没进过 shortlist。这不是 pick 的问题,是它的 schema 文档匹配不上。**换 `-3-small` 时 `donor` 只有 0.059、`mondial_geo` 只有 0.167 —— `-3-large` 把这两个救回来了很多,这就是那 2.3pp 的来源,而且它高度集中在少数 schema 上,不是均匀的。**

---

## 结论

| 问题 | 答案 |
|---|---|
| 换 `-3-large` 值不值 | **已经在用了**(上次跑就是),留着;它对 `donor` / `mondial_geo` 是救命的 |
| 检索是瓶颈吗 | **不是。**@10 已 0.953 |
| 瓶颈在哪 | **LLM pick**,值 +4.7pp EX,机制是按字母序截断到 15 张表 |
| 「不融合 BM25」这个决定 | **建立在一个已失效的测量上** —— 重测,便宜 |

E1 花了不到一小时和几毛钱,把下一个实验从「换 embedder」改成了「修 picker 的表排序」。
