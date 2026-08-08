# 2026-08-08 全面排查：语料、脚手架、实验缺口与设计缺陷

六路 agent 分头审计，加上我在 2026-08-07 运行产物上做的独立复算。**没有跑任何实验**，
所有数字要么来自已有产物，要么来自静态解析。

产物来源：`_eval_bundle_20260807.zip` 里的三个 JSONL（两臂各 1351 行 + 对比系统的评分行），
解包在会话临时目录，**没有进仓库**。引擎 `7ce3a9d`，数据集 `22fe2a6`，对比系统 `06e4c42`。

标注约定：**〔实测〕**= 我在本次会话里从产物或源码直接算出/读出；**〔审计〕**= subagent 报告、
我抽样复核过；**〔存疑〕**= 无法在本机验证。

---

## 0. 先说三条最要紧的

**一、语料把基准的答案键交给了 agent。** 数据集植入了 1486 个"诱饵列"——仿冒真实列的
permute/remap 副本，是这套混淆基准要测的核心能力。语料里 **422 个诱饵列全部被当作正常列资产
收录，一个不落**，而且 625 个表文件的 `body` 字段里逐字写着：

> `DECOY column: not a real business field. Fabricated to mimic 'zip_code'. Corruption
> operator 'permute'... Do NOT use it to answer questions.`

`body` 会被渲染进 prompt（`serve/context.py:130-141`，`struct_with_body`）。所以
"受治理的语义层能避开陷阱"在这个基准上是**构造出来的，不是策展出来的**。〔实测〕

**二、"检索占差距 62%"是我算错了，正确值约 47%，而且补满检索也补不上差距。** 那个反事实
把"检索失败集"按"检索成功集"的正确率 0.675 来补，等于假设两个子集一样难。对比系统在这两个
子集上的表现直接给出了难度比：0.5603/0.7265 = 0.771。按此调整，检索只解释 **47.3%**。
更要紧的是：在检索**已经成功**的 1108 行上，我们仍以 0.6751 对 0.7265 落后
5.14pp（配对 McNemar p=1.6e-04）。〔实测〕

**三、对比臂的配置无法核实。** 跑出 WrenAI 那 0.679 的 harness 在服务器上是未跟踪目录
（`?? bird_project/`），**没有传过来**。我在它那个 commit `06e4c42` 上确认：树里既没有
`prediction.py` 也没有 `get_contexts_from_sql`。所以"它拿到了 gold 推导的 DDL"这个说法
**不成立于该 commit**〔存疑〕。能确证的是结构性事实：`schema_indexer.py:36`
的 `SCHEMA_DESCRIBE_THRESHOLD = 30_000`，低于此阈值就把**整个 MDL schema 原文塞进 prompt**；
一个 manifest 对应一个数据库。它每题 10,954 input tokens，和"整库 schema 一次性塞入"完全吻合。
**它很可能从来不需要在 57 个 schema 里做路由——那是我们独付的税。**〔实测 + 推断〕

---

## 1. 语料本身的问题

### 1.1 15 个资产占了语料一半的字节〔实测〕

语料 8034 个 YAML、18.9 MB。**15 个资产超过 80,000 字节（= 上下文预算），合计占全部字节的
50.5%**。最大的 `video_games/few-shots/fs_video_games_0050.yaml` 是 **5.1 MB**，
是上下文预算的 64 倍。

内容全是从无表 gold SQL 收割来的常量块：`SELECT "v"."c0", "v"."c1" FROM (VALUES
(CAST(58 AS DOUBLE PRECISION), 'North America'), ... )` 一路铺 5 MB。
这类 few-shot 没有任何 SQL 结构可学，纯粹是噪声，而且一旦被检中就会撑爆上下文。

| 类别 | 个数 | 字节 | 均值 |
|---|---:|---:|---:|
| few-shots | 5000 | 14.63 MB | 3,068 |
| tables | 656 | 3.20 MB | 5,118 |
| joins | 928 | 0.43 MB | 485 |
| terms | 994 | 0.40 MB | 425 |
| metrics | 399 | 0.21 MB | 549 |
| schema 根 | 57 | 0.06 MB | 1,018 |

### 1.2 诱饵列被完整收录并带标注〔实测〕

见 §0 第一条。补充两点：

- 诱饵标注同时写在 `body` 和 `reliability.note` 两处。`reliability` **只有 browse API 读**
  （`api/browse_routes.py`），serve 路径不读；`body` **会进 prompt**。
- 我按"生成 SQL 引用了诱饵列名"这条规则量了一下，全 1351 行只命中 **1 行**。
  但这**不能**和对比系统的 66/1351 比——它的判定规则在没传过来的 harness 里。〔存疑〕

### 1.3 few-shot 不是泄漏，这点先澄清〔实测〕

5000 条 few-shot 全部收割自 `train_final.jsonl`（`source_refs` 一致，
`evidence: gold train Q train_5211`）。评测用 `test_final.jsonl`：

- few-shot 引用的题号与评测集**交集为 0**。
- gold SQL 字符串完全相同的只有 **2 条（0.15%）**，且这 2 条我们都答错了。
- 和数据集自己那条 commit 对得上：`22fe2a6 Dedupe before splitting: leakage 3.60% -> 0.22%`。

**所以"WrenAI 有 train few-shot 是单方面优势"这个说法是错的**——我们语料里有同样的 5000 条。

### 1.4 索引读的和 prompt 读的是两个不相交的字段〔实测〕

这是整份排查里最关键的结构事实，值得单独列一张表：

| 字段 | 进检索索引？ | 进 prompt？ |
|---|---|---|
| `summary` | **是——而且是唯一进索引的**（`IndexEntry` docstring: *"the only text that enters either channel"*） | **否**（`serve/context.py::_structural_line` 只发标识符/类型/`grain`/`cardinality`/`on`；few-shot 缺 `body` 时有个后备分支，但 5000/5000 都有 `body`，是死代码） |
| `body` | 否 | **是**——命中时渲染成 `struct_with_body`，且 `body_droppable` |
| `rules` | 否 | 是（schema 级，`## Must honour`） |
| `reliability.note` | 否 | 否（**只有 browse API 读**） |

以 `address` 的 schema 资产为例：`summary` 是 `'address: 9 tables — CBSA, alias, ...'`
一串表名（进索引、不进 prompt）；`body` 是 `'A US ZIP-code geographic and demographic
reference database...'`（进 prompt、不进索引）。

**推论一：A/B 的处理变量（改的全是 `summary`）对生成器完全不可见，只能通过排序起作用。**
所以那个"+2.8pp 条件 EX"不可能是文本质量效应，只能是"选中了哪些资产"的效应。

**推论二：诱饵警告写在 `body` 里，所以它不帮检索、只帮生成**——正好是把答案键递到最需要的地方。

### 1.4b schema facet 几乎没有词面锚点〔审计〕

物理（混淆后）标识符在 100% 的表摘要和列摘要里出现，但一条 schema 摘要平均只点到它 11.5 张表
里的 **0.95 张（A）/ 1.89 张（B）**，57 个里只有 20/21 个点到哪怕一张。更糟的是
**45/57（B）的 schema 摘要点的是混淆前的英文名**（`restaurant: 3 tables — generalinfo,
geographic, location`，而物理表叫 `allgemeine_informationen / geografisch / standort`）。

叠加语义通道整轮失效（§2.1），**schema 路由是在 57 条大半含着"已被改掉的词汇"的字符串上跑 BM25**。
这 57 条字符串是整个语料里最小、杠杆最高的一块。

### 1.5 A/B 的处理变量是什么，说清楚〔实测〕

`git diff main corpus-a-20260807`：表面看 **2152 / 8034 个文件变化**，
按目录分 joins 928、tables 656、metrics 399、terms 109、schema 根 57。**few-shot 一个没动。**

**但 2152 这个数字虚高。** 加上 `--ignore-cr-at-eol` 之后：**399 个 metric 的差异全部消失
——纯 CRLF→LF**；928 个 join 每个只有 2 行是语义变化，其余是换行符。合计
**1327 个文件的差异不携带任何信息**，却全都进了 `corpus_content_hash`。
（`.gitattributes` 的 `* -text` 加进来正是为了稳住哈希，结果这批文件本身就带着不一致的换行符。）

真正的变化维度：

1. **摘要致密化**（本意的处理变量）：把 `body` 的领域语言搬进被索引的 `summary`。
   `Address (Address): AddressID, AddressLine1, ...` → `Address stores individual street
   addresses (line 1/2, city, postal code, state-province link) used by customers,
   employees, vendors and orders.` 摘要均长：schema 110.9→220.8，table 81.1→154.4，
   column 39.4→99.8。模板化比例：B 的列摘要 98% 是 `W — W.W` 一个模子，A 降到 4%。

   **但这一项里裹着两个独立干预：**
   - **1a 真实样本值被搬进了被索引的字段。** A 有 2047/5942（34.4%）的列摘要和 78/655 个
     表摘要引用了字面样本值（`e.g.` / `such as`），**B 是 0/5947 和 0/656**（B 把它们留在
     `body`）。里面有形似 PII 的东西：`synthea` 的社保号列摘要写着
     *"for example '999-94-3751'"*，`retails.cliente.telefono` 是 `'627-220-3983'`，
     `chicago_crime` 的邮箱列是真实的市政地址 `'ward13@cityofchicago.org'`。
     这些现在进了 BM25 倒排、嵌入缓存键和向量库。
   - **1b 诱饵披露在两臂之间是不对称的，而且不对称正好落在驱动检索的字段上。**
     **A 把 2282/5942 个列摘要标成诱饵，B 是 0/5947**（B 只写在 `body` 和 `reliability.note`）。
     表级：B 有 141 个表摘要以 `[DECOY clone of <original>]` 开头，A 改写成散文。
     所以 **A 的"更丰富"里，有一部分就是"陷阱披露得更靠前"**。
2. **928 条 join 的 `on` 子句全部加了 schema 限定**：`CBSA.CBSA = zip_data.CBSA` →
   `address.CBSA.CBSA = address.zip_data.CBSA`，资产 id 随摘要摘要重算而改名。
   **但 join 的 `summary` 逐字节不变** —— 所以这一项**不走检索**，走的是 `connect` 的
   Steiner 求解，而 `connect` 决定哪些表被授权。
3. 109 条 term 的绑定目标从 metric 改到 table。
4. 删了 1 张表 4 个列、5 个列，`airline` / `soccer_2016` 手工加了 2 条规则。
5. 1331 个文件 CRLF→LF，根 README 重写（两者都进 `corpus_content_hash`）。
6. **corpus A 少了一整张表**：`airline/tables/tbl_airline_Air_Carriers_66c534.yaml` 被删了
   （655 vs 656 张表，5942 vs 5947 个列）。

**结论：+4.5pp 的授权率提升至少有四个候选活性成分（1、1a、1b、2），一个都没被单独测过。**
"丰富摘要有用"这句话现在没有证据支撑——它只是四个同时发生的改动的标签。

**一处需要注意的张力：** 另一路 agent 量到，语料 join 图连不上 gold 表的题只有
**12/948 = 1.27%**（两臂相同）。所以第 2 项那个 join 限定，**不是通过"补上缺失的边"起作用的**
——如果它有用，作用点是 `connect` 的解析，不是覆盖率。这一点也还没测。
（另有 38 张真实表一条声明的边都没有，含 8 张 hockey 表。）

### 1.5b 第 6 项那张缺失的表，顺带给出了臂标注的铁证〔实测〕

`airline` 有 13 道测试题，**其中 5 道要用 `Air Carriers`**。我在产物里查了两臂的授权集：

- arm B（`clone`）里 `airline.Air_Carriers_66c534` 出现 **19 次**；
- arm A（`inrepo`）里 **一次都没有**，而且 arm A 的授权集里**不含任何带 slug 的 id**。

这同时确认三件事：(1) arm A 确实服务的是 `corpus-a-20260807`；(2) arm B 服务的是 `main`；
(3) **赢的那个臂缺了一张 5 道题需要的表** —— 混淆的方向是**不利于 A** 的。

副作用：我在 §0/§1 那个漏斗里用 `airline.Air Carriers` 去比对授权集，
对 arm B 是**误判为失败**（它以 slug 形式存在）。所以 B 的表级失败数 55 应该约为 50，
授权率略高于 0.8595。方向和结论不变，但这个盲点要记在账上。

### 1.6 `works_cycles` 一个 schema 就是 22% 的差距〔实测〕

| | 值 |
|---|---|
| 题量 | 77（全集最大，5.7%） |
| EX（我们 / 对比） | **0.169 / 0.532** |
| 占 9.4pp 总差距 | **2.07pp** |
| 路由召回 | 0.909（**不是检索问题**） |
| 撞 attempt cap | **26 / 77 = 33.8%** |
| 表数 | 73（次多的 mondial_geo 是 42） |
| 中位授权表数 | 18 |
| 标识符含大写比例 | **96%**（对照：hockey 5%、address 18%） |
| 撞 cap 那些回合的平均输入 token | **113,471**（全局 38,637） |

撞 cap 的最后一条 SQL 长这样：`SELECT 1 FROM "works_cycles"."WorkOrderRouting" LIMIT 1`
—— 那是探查，不是答案。agent 在 73 张表里摸索，把 attempt 花光了。
`authors` 是第二严重的：21 题里 38.1% 撞 cap，EX 0.429 对 0.714。

全局撞 cap 68/1351（5.03%），其中对比系统答对了 38 题。

### 1.7 `main` 上那份 manifest 描述的是另一棵树〔实测〕

`GOLD_LAYER_MANIFEST.json`（在 `main` 上，且**进哈希**）声明：

```
Table 655   Column 5942   total_assets 13975
```

`main` 的树里实际是：

```
table 656   column 5947   total 13981
```

**它描述的是 corpus A** —— 那个没推上去的分支，数字分毫不差（13975）。
`main` 的 `README.md` 把这个差额解释成 *"13 975 declared / 13 981 loaded"*，
但那不是"声明 vs 加载"的差异，**那是两棵不同的树**。

manifest 的注记还写着那两个不可拼写的标识符"已记录在 `_build/skipped_identifiers.json`"，
而 `main` 的树里两个都在，`_build/` 也不存在。

**代价：任何人 clone `BIRD-corpus @ 05fb31a`、读 manifest，会以为自己拿到了 gold 层，
实际拿到的是较弱的那一臂。** 这就是 08-07 那份文档里说的"commit message 描述了一次它
没做的替换"，现在有了凭据。

顺带：**`corpus-a-20260807` 分支根目录的 `README.md` 是引擎仓库的包文档**——开头是
`# corpus/`，写着 *"This directory holds no assets"*，链接 `../docs/adr/0005-…`（在语料仓库里
解析不了），还描述了 0/57 个 schema 里存在的 `notes/` 和 `negatives/` 目录。
它也在哈希里，所以**给 0.585 那一臂命名的摘要，被钉在一个装错文件的 commit 上**。

`GOLD_LAYER_MANIFEST.json` 本身没有任何代码读（`src/` 和 `scripts/` 里零命中）。

---

## 2. 语料周边脚手架的不匹配

### 2.1 `facet_schema.semantic` 在 2673 个回合上 100% 失败，真正的原因和我之前写的不一样

我在 08-07 那份文档里写的是"schema 类资产没有向量"。**这是错的。**〔审计，已复核代码〕

真实链路：`eval/arms.py:65` 调 `session.configurable()` **不传 question** → config 上没有
`query_vector`；eval 图不含 `accept` 节点，state 里也没人写；`facet_schema` 是唯一
不做查询改写的 facet，于是旧的 `vector_for_query` 把它当成"fallback 命中缓存"跳过了 embed，
而 fallback 是 `None`；`semantic_search` 收到 `None` 向量就返回不可用。

`73f5312` 的修复（`if query and embedder is not None and (rewritten or fallback is None)`）
是对的，堵的就是这个洞。

**顺带纠正一个我可能给人的错误印象**：路由不是只靠 schema facet。`route` 是
`score(schema) = Σ_facets max(该 facet 命中里带此 schema 标签的最高分)`，五个 facet 都投票。
schema facet 丢了语义一半，另外四个的语义通道是好的——这就是为什么观测到的 0.938 高于
"纯词面"的引用值 0.7018@3。

另外两个 `not_configured` 是**声明式的、有理由的**，不是 bug：`facet_example.lexical`
（NL 问句之间做词频匹配会奖励虚词）和 `facet_schema.extraction`（改写在此处实测无效，
p≥0.45）。

### 2.2 通道融合的权重是导入期常量，运行期改不动〔审计〕

`max(lexical, semantic)` 那个老缺陷已经修了，换成"每通道 min-max 归一 + 加权平均"。
但新规则有新问题，而且**最要命的是三个最该调的旋钮没有运行期读取者**：

| 旋钮 | 默认 | 运行期可改？ |
|---|---|---|
| `w_lexical` / `w_semantic` | 0.5 / 0.5 | **否**——`FUSE_WEIGHTS` 是模块级常量，导入时定死 |
| `lexical_saturation_k` | 1.2 | **否**——建索引时读一次 |
| `asset_budgets`（table 8 / column 30） | — | **否**——直接读 `ASSET_REGISTER` |
| `expand_hops` | 0 | **全树零读取者，根本没实现** |

后果两层。第一层：**把 `w_semantic` 写进 `knobs_resolved` 会改配置哈希、不改行为**，
而这样一次运行能通过全部可比性门。第二层：引用记录里语义单通道是 **0.9064@1 / 0.9825@3**，
词面单通道是 0.5468@1 / 0.7018@3 —— 50/50 混合明显不是最优，而现在**改不了**。

### 2.3 诊断信息算出来就扔〔审计〕

`apply_budgets` 会返回 `dropped` 和 `best_dropped_score`，`pass_two` 也写进了
`retrieved["budget_dropped"]`，注释里写明了为什么要它。但 `stamp` 不提取，`record.py` 不声明，
**没有任何人读**。于是那 40 个表级失败里，"gold 表排第 9 被预算切了"和"gold 表压根没被打分"
在产物里**分不出来**——而这正好是"该抬预算"还是"该修索引"的分水岭。

### 2.4 agent 没有任何补救路径〔审计，已复核〕

五个工具全部被 `ToolBounds` 框死在本回合的 `licensed` / `readable_assets` 里，
且"Closed at `connect`, never widened"。**没有 `search_corpus`、没有 `list_schemas`。**
图是严格 DAG，`agent_core` 之后没有回到 `route` 的边。

**所以 agent 无法察觉自己缺表，连"缺了什么"都枚举不出来。**从回合内部看，
"没被路由到的 schema"和"不存在的 schema"完全一样。EX 0.043 不是"失败"的算术，是"猜"的算术。
观测吻合：201 个对方独赢里，只有 2 个 clarification、5 个 refused，而 142 个是 `result_mismatch`。

### 2.5 `--replay-routing` 不存在〔审计，已复核〕

全仓搜不到任何路由固定机制。记忆里那个"LLM router 有 14% 重掷"属于 v1 的 LLM picker，
**v2 已经没有这个东西**——`route` 是纯算术，平局按名字/id 排序，完全确定。

v2 唯一的随机源是**四个 utility-model 查询改写器**（`facet_schema` 不在其中）。
它们按 provider 默认温度每次重掷，而改写结果驱动 pass-two 的候选选择、进而决定表授权。
**所以配对设计其实没配对**，而这个比例在 v2 上**从未测过**。
`api/graph_app.py::_utility_model` 加一行 `temperature=0` 就能去掉绝大部分。

### 2.6 语料"有版本但不可重建"，而且比 AGENTS.md 写的更彻底〔审计 + 实测〕

`corpus/seed.py` 是 `src/` 里**唯一**的资产生产者，只产 4 种类型（schema/table/column/join，
join 还只从外键来）。**metric、term、few_shot、negative_example 四类在 `src/` 里没有任何生产者**
——那是 13,981 个资产里的 6,393 个，**45.7%**。没有 curator 模块。

`_build/` 那三个生成器脚本没在传输包里。`tools/` 下有 `densify_summaries.py`、
`graft_corpus_fields.py`、`_set_asset_fields.py` 等，是一套**部分**的构建工具。

### 2.7 仓库唯一的资产写入器会毁掉 42.5% 的语料〔审计，已执行验证〕

`ports.py:124` 指名 `corpus/store.py` 是 `CorpusStore` 适配器，`serve/session.py` 也 import 了
它的 `write`。但一次真实往返（在临时目录做的）：

`load_file(tbl_beer_factory_kunden.yaml)` → **16 个资产**（1 表 + 15 列）
→ `write()` → 路径变成 `beer_factory/beer_factory.kunden.yaml`
→ 再 `load_file` → **1 个资产**，`columns` 塌成一串 id 字符串。

**全量往返会删掉 5,947 / 13,981 个资产（42.5%），并搬动全部 8,034 个文件。没有测试覆盖。**
任何将来基于 `CorpusStore.write` 建的 curator 都会静默销毁整个列层。

### 2.8 同一棵树有两个 `corpus_content_hash`〔实测〕

`hash.py:44` 调 `corpus_files` **不传 `suffixes=`**，而 `store.load` 传 `(".yaml", ".yml")`。
在 `BIRD-corpus@main` 上实测：

```
hash(schemas=None)     = 3e8e50d20ee45ca5...d84b88f      # 哈希 8038 个文件
hash(schemas=[全 57])  = cfdf0bacffaa612b...5814219e      # 哈希 8034 个
EQUAL: False
```

差的 4 个是 `.gitignore`、`.gitattributes`、`GOLD_LAYER_MANIFEST.json`、`README.md` ——
根目录文件的 `namespace_of` 返回 `""`，不在 allowed 里。**池化驱动传的是 `None`，
所以它把 README 也哈希进去了：改一下 README 就给每个臂换了身份。**
反方向也成立：往 schema 目录里扔一个非 YAML 文件会改哈希，而加载器看不见它。

**`corpus_content_hash` digest 的不是被服务的资产集合，是一个目录。**

### 2.9 线上应用会服务一个坏掉的语料〔审计〕

`tools/run_datalake_eval.py` 和 `serve/__main__.py` 都检查 `fatal_problems` 并 `return 3`。
**`api/graph_app.py::session_from_environment` 不检查。** `api/routes.py:539` 把
`"servable": not session.fatal_problems` 算出来当作 `GET /audit/corpus` 的一个信息字段，
然后没人用它。于是 58 个 schema 目录里有 3 个解析失败时，线上照常服务缩水的资产集，
而 `corpus_content_hash` 盖的是**完整树的字节**——和一个干净语料给出的摘要一模一样。

`eval/harness.py` 里完全没有 `problems`/`fatal` 字样。那道拒绝是**一个脚本的属性，不是 harness 的**。

### 2.10 修完语义通道后，每回合的 embed 从 1 次变成 5–6 次〔审计〕

ADR 0005 §2.2 要求"问题每回合只嵌入一次，向量往下传，不由每个消费者各自推导"。
`73f5312` 修对了正确性，但 harness 路径上 config 和 state 里仍然都没有 `query_vector`，
于是五个 facet 各自调一次 `embedder.embed([query])`，`pass_two` 再来一次。
**每回合的嵌入成本和限流暴露是 ADR 预算的约 5–6 倍**，而 §2.2 引用的 v1 事故正是被限流打死的。

### 2.11 花钱写进去、没人读的字段〔审计〕

| 字段 | 语料里的实例数 | 读者 |
|---|---:|---|
| `FewShotAsset.complexity` | **5,000 / 5,000** | `src/` 里无 |
| `Binding.target_type` | **967 / 967** | 无（只解引用 `target_id`） |
| `Provenance.version` / `source_refs` / `source` | 约 13,977 块 | 无 |

反方向——serve 路径会读、但语料里一个都没设（渲染分支从不触发）：
`TableAsset.grain` 0/656、`TableAsset.rules` 0/656、`ColumnAsset.role` 0/5947、
`JoinAsset.body` 0/928、`ColumnAsset.sample_values` 0/5947。

以及：**`Governance.excluded` 在两个语料的全部 13,981 个资产上都是 `False`**
——D6 那个排除控制，从来没有被这个项目测过的任何语料行使过。

---

## 3. 实验缺口

### 3.1 一行里没有处理身份〔审计，实测印证〕

`project_turn` 是手写的字面 dict，和 `RECORD_REGISTER` 之间**没有闭合测试**。被丢掉的字段里
最要命的三个：

- **`corpus_content_hash` —— 每一行都没有。** 而它的声明是 `Absence.never`、Tier=treatment、
  注释写着"语料就是处理变量"。**这和上次让整轮数字作废的 `knobs_resolved` 是同一类缺陷，
  只是换了一个字段，而且还开着。**
- **`prompt_set_hash` —— 每一行都没有。**
- **引擎 commit —— 全树没有任何写入者。** `git_sha` / `working_tree_dirty` / `diff_sha256`
  是声明过的旋钮，默认 `None`，`src/` 和 `tools/` 里零写入。而 `knobs_resolved` 门读的
  `resume_drift_keys()` **包含** `git_sha` —— 于是"全是 null"就是一个一致的签名，
  **这道门会在一个引擎版本未知的臂上判通过**。
- 数据集 commit 也没记。没有任何 run manifest。唯一的来源标记是文件名。

我在产物上直接确认了这些字段确实一个都不在。〔实测〕

### 3.2 一行里没有 attempt 历史、没有延迟、没有进入 prompt 的资产〔审计 + 实测〕

- `execution`（逐 attempt 的判定与终止原因）—— `stamp` 造好了，不投影。所以
  "5 次尝试各自为什么失败"无解。
- `latency_sec` —— 不投影。**行里完全没有时间信息**，所以"这个臂跑一轮要多久"事后也算不出来。
- `facet_hits` / `pulled_in` / `delivery_hash` —— 都不投影。`context_hash` 是摘要不是清单。
  **"我们那 5000 条 few-shot 到底有没有进 prompt"从产物里回答不了。**〔实测：`licensed`
  里只有 `schema.table`，但 licensed 本来就只管表授权，证明不了别的〕
- `terminal_reason` 在全部 1351 行上都是 null。〔实测〕
- 29 行 `outcome=answered` 但 `generated_sql` 是 `None`。〔实测〕

### 3.3 成本无法估算〔审计〕

`observed_tokens` 只加 `input_tokens` / `output_tokens`，**完全忽略 `cache_read_tokens`、
`cache_write_tokens`、`reasoning_tokens`**。而缓存写按 1.25x 计费、缓存读约 0.1x
（record.py 自己写着）。更糟的是未计量的调用会静默变成 0 token：`Measured.unmeasured`
序列化成字符串，`_count` 返回 0，但 `calls` 照加，**且没有计数器记录发生了多少次**。
我实测到 3 条 usage 的 `input_tokens` 不是 int。

按政策没有价目表。所以"这个臂要花多少钱"**只能事后按 token 数说，永远无法在启动前估**。

### 3.4 跨臂门从来没在产出数字的路径上跑过〔审计〕

七道单臂门都接上了，但**只打印不阻断**。真正致命的是
`context_hashes_distinct`（要求 ≥95% 的共享问题上下文哈希不同）——**这是唯一能证明
A 和 B 确实是两个处理而不是一个的检查**——只能经由 SQLite-only 的 `eval/__main__.py` 到达。
**2026-08-07 的 A/B 比较从未评估过它。**

### 3.5 数据集发了一堆文件我们没有读者〔实测〕

| 文件 | 规模 | 状况 |
|---|---|---|
| `gold_quality_flags.jsonl` | 10,164 行 | 无读者（我查了：覆盖我们 1351 行，全部 `clean: true`） |
| `trap_manifest.json` | 1,486 行 | 无读者 —— **这是 decoy_touch 的输入，离线就能算** |
| `trap_table_manifest.json` | 162 行 | 无读者 |
| `gold_star_expanded.jsonl` | 3 行 | 无读者 —— 2 行落在测试集，导致 `SELECT *` gold 判错 |
| `question_paraphrases.jsonl` | 6,743 行 | 无读者 |
| `db_language_map.json` | — | 无读者 —— 14 个英文库是**重命名轴的噪声底** |

### 3.6 recall@k 只能在免费臂上测〔审计〕

`schema_ranking`（截断前的全部打分 schema）进了 record、没进行。行上只有 `schemas`
（选中的 top-N），于是"gold 排第 4"和"gold 从未被打分"是同一个观测。
`datalake.routing_recall` 造的是第三种行格式，**在付费产物上跑不了**。

---

## 4. 实验设计缺陷

### 4.1 头条比较不是同一个任务〔实测 + 存疑〕

见 §0 第三条。可确证的：WrenAI 的 MDL 是**按数据库**组织的，
`describe_schema` 在 30,000 字符以下把整库 schema 原文塞进 prompt，
每题 10,954 tokens 与之吻合。**它没有在 57 个 schema 里路由。**

我们付 0.938 的路由税 + 0.905 的授权税，它结构上不付。

另外几项不对称，方向都得说清楚：

| 项 | 偏向 | 量级 |
|---|---|---|
| schema 是递给的还是要路由的 | → 对方 | 约 4.4–6.0pp |
| few-shot | **两边都有**（各 5000 条 train 派生），此前的说法是错的 | ~0 |
| BIRD `evidence` | 我们拿的是 `evidence_rename`（提示里的标识符已按混淆替换，**更强**）；对方拿的疑似原版 | 可能 → 我们，≤2pp〔存疑〕 |
| 语料含诱饵答案键 | → 我们 | ≤2pp |
| 对方产不出的结局（capped/refused/crashed）在我们这边计为错 | → 对方 | 约 5pp 的分母 |
| 排序敏感的 23 题我们自设更严规则 | → 对方 | ≤1.7pp |
| **对比臂的模型未申报** | **未知** | **这一条单独就能否掉头条** |

### 4.2 语料是在测试集上调出来的〔审计，已复核默认值〕

`tools/routing_recall.py` 和 `tools/query_summary_alignment.py` 的默认数据集都是
**`test_final.jsonl`**。`tools/densify_summaries.py` 的 docstring 直接写着验收线：

> "…all gold tables licensed top_n=3: 0.632 → 0.693, **+6.1 pp**。+6.1pp 是模型改写的验收门槛。"

`corpora/` 下还留着六个变体加一个隔离区。**corpus A 是这轮在留出集上打分的筛选的幸存者。**
`check_train_only.py` 抓不到这个——它查的是逐字 n-gram 包含，自己也承认"改写泄漏无法检测"。

雪上加霜：那 +6.1pp 此前已被判定为词面通道的假象，而**这次运行 schema 路由恰好就是
BM25-only**（语义通道死了）——也就是说，A 优于 B 的检索增益，是在假象所在的那个通道上、
且在能证伪它的通道关着的情况下测出来的。

### 4.3 127 道题是转译产物，应该出分母〔审计，我独立复算了 127 这个数〕

9.40% 的 gold SQL 不读表，答案是常量。**112/127 的原始 `sql_sqlite` 读真实的表**——
常量是 SQLite→Postgres 转译过程引入的。数据集自己的文档写着：

> "PostgreSQL query **embeds SQLite result rows** rather than recomputing them...
> but is **not** a durable dialect translation."

也就是说，这**恰好是"没有任何自然的 Postgres 查询能复现 gold 结果"的子集**。
三个臂在上面都是 0.23–0.35，符合预期。31 道题的常量带 ≥6 位小数——复现它意味着
逐位复现 SQLite 的浮点运算。

数据集自己的 `limitations.md §5` 说这个比例是 "~0.5%（约 46 行）"，**实测是 9.40%，差 19 倍**。

### 4.4 功效：3pp 以下的干预全在死区〔实测复算〕

配对 McNemar，α=.05，双侧，80% 功效，按观测到的不一致率 0.172：

| 比较 | n | 结果 |
|---|---|---|
| A vs 对比（全 1351） | 1351 | b=92, c=219, **p=4.3e-13** —— 决定性 |
| A vs B（全 1351） | 1351 | b=152, c=80, **p=2.6e-06** —— 决定性 |
| A vs 对比（检索已成功的 1108） | 1108 | b=83, c=140, **p=1.6e-04** —— 决定性 |
| 检出 3pp 差异 | 需 **~1,484** 题 | 全集 1351 **刚好不够** |
| 检出 2pp 差异 | 需 **~3,356** 题 | 做不到 |

而且这还乐观了两处：**题目嵌套在 57 个 schema 里**（works_cycles 一个占 77 题），
而语料干预作用在 schema 层——按 ICC=0.05、簇均 23.7 算，有效 n 掉到约 633，
MDE 升到约 5.2pp，**A vs B 就不再是决定性的了**。`Population` 和 `mcnemar` 都没有簇的概念。
另外单种子、无重复，run-to-run 方差完全未测。

### 4.5 治理轴根本没测〔审计〕

- `run_datalake_eval.py` 构造的是 `GovernancePolicy(guard_rules_enabled={})` ——
  **所有输入护栏全关**，包括线上应用开着的 BI-scope 门。**基准测的不是发布出去的系统。**
- `serve/nodes/negative.py` 是桩，每回合都返回 `outcome: "disabled"`。
- `cost_budget` 是 `UNSET`，第 7 层从不运行。
- 2702 个回合 0 个护栏错误 —— 那是个上界（≤3/1351），不是测量。
- 七道可引用性门**全部是仪器健康检查**，register 里**没有任何治理结局指标**。

**这个系统正在被完全按它最弱的那根轴打分，而它命名所依据的那根轴一点没测。**

---

## 5. 计划

分四阶段。**阶段 A 全部免费、无模型调用**，先做完再谈花钱。

### 阶段 A：不花钱就能拿到的东西（建议全做，可并行）

| # | 做什么 | 为什么 | 成本 |
|---|---|---|---|
| A1 | **把 `corpus_content_hash`、`prompt_set_hash`、`git_sha`、数据集摘要写进行**，并加一条 record→row 的闭合测试（照 `test_register_closure.py` 的样子） | 这是上次让数字作废的同一类缺陷，还开着。**不做这条，下一轮又是不可引用的** | 半天 |
| A2 | **离线算 decoy_touch**：`trap_manifest.json` + 已有产物的 `generated_sql`。同时算"语料里带诱饵标注的列有多少进了 prompt" | 唯一的治理信号，且**不需要重跑**。也量化 §1.2 那个答案键有多大 | 半天 |
| A3 | **跑 `tools/routing_recall.py`**，全 1351 题，`--corpus-dir ../BIRD-corpus`，`--baseline` 指向另一个分支，`--no-rewrite`，`--top-n 3/5/10` | 一次同时结掉：recall@k、A/B 的检索差、top_n 的代价、以及修好语义通道后到底值多少。**$0，约 12 分钟一轮**，向量缓存 365MB 已在本地 | 1 天 |
| A4 | **把 `budget_dropped` 投影到 record/row** | 把那 40 个表级失败拆成"被预算切了"vs"没被打分"——这决定 A3 之后该动哪个旋钮 | 2 小时 |
| A5 | **给 `w_lexical`/`w_semantic`/`asset_budgets` 加运行期读取者** | 现在写进 `knobs_resolved` 会改哈希不改行为——这是 register 自己定义的失效模式 | 半天 |
| A6 | **删掉那 15 个超过 80,000 字节的 few-shot 资产**，并给 curator 加一条尺寸上限 | 语料一半的字节，零信息量，且撑爆上下文 | 1 小时 |
| A7 | **`temperature=0`** on `_utility_model` | v2 唯一的随机源。不做这条，此后每个 A/B 都不是配对的 | 1 行 |
| A8 | **在产物层面明确排除那 127 题**，并在报告里按 §4.3 的理由写清楚 | 它们对任何查活库的系统都不可赢 | 2 小时 |
| A9 | **修 `GOLD_LAYER_MANIFEST.json`（`main` 上描述的是另一棵树）和 A 分支根目录那个装错的 README**；把 manifest 的生成做成可校验的 | 现在 clone 的人会以为自己拿到了 gold 层。而且这两个文件都在哈希里 | 1 小时 |
| A10 | **修 `corpus_content_hash` 的双身份**：`hash.py` 补上 `suffixes=`，或者把根文件的 namespace 规则和 `store.load` 对齐 | 同一棵树现在有两个摘要，改一下 README 就给每个臂换身份 | 1 小时 |
| A11 | **把语料的行尾统一成 LF 并单独提交一次**（1327 个文件纯换行符差异） | 让 A/B 的哈希差异只反映语义差异 | 30 分钟 |
| A12 | **给 `store.write` 的往返加一条测试**，或者在 `ports.py` 里明说它不是语料的写回路径 | 一次全量往返会删掉 42.5% 的资产，且无测试覆盖 | 1 小时 |

### 阶段 B：把已经拿到的产物榨干（免费，A 之后）

- **B1（最重要）** 用 A3 的 recall@k，把 §1.5 那**四个**候选活性成分分开测。
  需要四个语料变体，全部只跑 `routing_recall`，$0：

  | 变体 | = corpus B 加上 |
  |---|---|
  | V1 | 只做摘要致密化（不含样本值、不含诱饵标记） |
  | V2 | 只把样本值搬进 summary |
  | V3 | 只把诱饵标记搬进 summary |
  | V4 | 只做 join 的 schema 限定 |

  **不做这条，"丰富摘要有用"就一直只是四个同时发生的改动的标签。**
  我的先验：V1 和 V3 是主要贡献者，V4 通过 `connect` 起作用而不是通过检索。

- **B2** 把 `works_cycles` 单独拎出来做个案（73 张表、96% 大写、33.8% 撞 cap、
  113k token）。它一个占 22% 的差距，且**不是检索问题**——路由召回 0.909。
  重点看：18/73 的授权率是不是全局 8 表预算在作祟（A4 会给出答案）。
- **B3** 按 schema 聚类重算功效，把簇效应写进 `measure/stats.py`。
  现在的 MDE 约 4pp 没算簇；算上 ICC=0.05 是约 5.2pp，**A vs B 就不再是决定性的**。

### 阶段 C：必须回答的两个前置问题（挡住一切付费运行）

- **C1 对比臂到底是什么配置。** 需要从服务器取回 `bird_project/` （模型名、pipeline 模式、
  每题喂进去的 schema、评分脚本）。**在这之前，0.679 这个数不能写进任何对外材料。**
- **C2 语料含诱饵答案键这件事怎么处理。** 三个选项，需要你定：
  (a) 保留，但把结论限定为"在 SME 可读取陷阱清单的前提下"；
  (b) 剥掉 `body` 里的 DECOY 文本，重跑，测真实的陷阱规避能力；
  (c) 两个臂都做，把差值当作"答案键值多少"的测量。
  我倾向 **(c)**——它把一个污点变成一个结果。

### 阶段 D：付费重跑（只有 A、C 完成后）

前提全部满足才启动：A1（身份字段）、A3（已知 top_n 的正确取值）、A5（权重可调）、
A7（温度固定）、C1（对比臂已知）。

- 臂：corpus A / corpus B / 最优权重 / 最优 top_n，视 A3 结果收敛到 2–3 个。
- **预期不要设成"赢过对比系统"**。§0 第二条已经说明：检索补满也只到 0.6605，
  仍落后。真实的、在同题同答条件下的残差是 **2.73pp**（n=1063），**低于 4pp 的 MDE**。
- 所以头条该换：不是"我们 EX 更高"，而是**"在等价的检索条件下差距是 2.7pp 且不可分辨，
  而我们提供了对方没有的治理面"**——前提是 §4.5 那根轴真的被测起来。

---

## 6. 需要你拍板的

1. **C2 那三个选项选哪个（诱饵答案键）。** 我推荐 (c)：两个臂都做，把差值当作
   "答案键值多少"的测量——它把一个污点变成一个结果。
2. **`corpus-a-20260807` 要不要成为 `main`。** 现在 `main` 是较弱的 B，而它的
   manifest 描述的却是 A（§1.7）。所以**现状是两边都错**：树是 B，说明书是 A。
   无论换不换，A9 都得做。另外 A 分支自己还带着一张删掉的表（§1.5b）和一个装错的
   README（§1.7），所以**不能直接把现在的 A 推成 main**。
3. **`_build/` 生成器要不要一并取回。** 不取，语料就一直不可重建——而且现在知道了，
   `src/` 里 45.7% 的资产类型根本没有生产者（§2.6）。
4. **阶段 A 是否全做。** 我的判断是 **A1、A3、A7 不可省**——少任何一个，下一轮又不可引用。
   A9/A10 很便宜但直接关系到"哪棵树被测了"，建议一并做。
5. **头条要不要改。** 现在的框架是"我们 EX 更高"，而证据不支持（§0 第二条、§5 阶段 D）。
   我建议改成"在等价检索条件下差距 2.7pp 且不可分辨，而我们提供了对方没有的治理面"
   ——**但这需要 §4.5 那根轴真的被测起来，现在它一点没测。**

---

*本文档不是 ADR。它记录的是一次排查和一份待批计划，会被之后的干净运行取代。
所有引用的产物在会话临时目录，未进仓库；文件名带内部部署代号，故不复现。*
