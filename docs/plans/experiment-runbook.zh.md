# 实验运行手册

该跑什么、按什么顺序跑、以及在一个数字值得被引用之前必须先满足哪些条件。写给
一个手上有机器和数据、但没有参与构建这套 harness 的人看。

2026-07-26 之前产出的每一个数字都已作废。下文的任何一步都不依赖此前的任何结果。

## 你需要什么

- 混淆版 BIRD 的检出目录（`../BIRD-Data-Obfuscation`），带
  `eval_dataset/{train,test}_final.jsonl` 和
  `data/train/train_databases/<db_id>/database_description/*.csv`。
- 一个装着混淆 schema 的 Postgres（本地是 `pg_rename_decoy`，端口 5435）。
  `run_datalake` 的 DSN 来自 `--pg-dsn`（默认
  `host=127.0.0.1 port=5435 dbname=bird user=bird password=bird`）。它**不会**
  自动读 `PG_RENAME_DECOY_DSN`；那个环境变量给产品 `[datasource]` 覆盖和活
  Postgres 集成测试用，除非你显式写成 `--pg-dsn "$PG_RENAME_DECOY_DSN"`。
- 仓库根目录 `.env` 里的 `OPENAI_API_KEY`。
- `uv sync`。

## 第 0 步——在往模型上花任何钱之前，先证明打分器本身是对的

```bash
uv run python -m governed_bi.eval.run_datalake --skip-agent --arms baseline --oracle oracle_sql
```

`oracle_sql` 把 gold SQL 直接提交给打分器。没有模型调用，没有检索，没有 agent
循环——它只和一个真实的臂共享最后一步，这正是为什么它的数字回答的是一个不同
的问题：**打分器给 gold 打出来的分是多少？** 只要低于 1.0，就是一个打分缺口
（一个冻结的 `VALUES` 常量、一个过期的 hash、一处归一化上的怪癖），之后每一个
数字都该拿这个上限去对照阅读，而不是想当然地认为上限是 1.0。

`--skip-agent` 是它免费的原因：一次模型调用都不花，所以直接跑完整个切分，不用
只取样。旁边那个 `baseline` 臂会全部拒答、拿 0 分——这是预期之内的，也不是你
要读的东西。读 `arms.oracle_sql.ex_gradeable`，再把它答错的那些问题列出来看看：
那些是任何一个臂都赢不了的。

不要跳过这一步。它是唯一一个能给它之后所有东西定标的步骤，而且不花钱。

**跑批本身也会做一次 gold 预检，而且是在构建阶段之前。** 它按 schema 抽取 gold，
在 Postgres 上执行，再和记录下来的哈希做比对——每个 schema 每行大约 40 ms，整个
切分下来也就几秒钟。它跑在任何模型调用之前，所以 DSN 写错、某个 schema 没加载、
或者 gold 读的是未混淆的 `sql_sqlite`，代价是几秒钟，而不是先在 69 个 schema 上
白跑一整轮 curator。

当超过四分之一的 schema 跑不动自己的 gold 时，它会中止——那属于配置错误。低于这个
比例，它会告警，并把这些 schema 记到 `gold_hash_self_check.exec_error_dbs` 里，
而这会挡住可引用性：某一条查询撞上 60 秒的网关超时，不应该让整个切分变得不可跑；
但一个 schema 的 gold 从来没被确认过，它的分数也就不是一个可以拿出来引用的数字。
如果碰到这种情况，请把抽样数提上去，而不是无视它：

```bash
uv run python -m governed_bi.eval.run_datalake --skip-agent --arms baseline --oracle oracle_sql --gold-per-db 3
```

只要*任意*一条抽到的行能执行并且对得上，这个 schema 就算通过验证。所以把这个数调大，
买到的是“一行不顺就换一行”的冗余，而不是更多失败的方式。

把 `--skip-agent --arms baseline` 留在这条命令上。这一步的每一部分都不该花一分钱，
而光秃秃的命令会继承完整的默认阶梯——四个臂，再加上一次 `--replicate` 就是第五轮
serve。那正是整个第 2 步的全部预算，而这条命令就摆在一个承诺不花任何预算的小节里。

**如果构建阶段大面积失败，跑批也会拒绝继续 serve。** 当成功构建的 schema 不到请求
数量的一半时，它会在构建阶段之后停下来，而不是把剩下的那些拿去打分——池化路由器
会去给一个根本没被构建出来的 corpus 排序，而 `quotable()` 本来就会因为
`build_errors` 拒掉这次运行，所以继续 serve 只是把 serve 预算花在一个没人能引用的
数字上。少数几个 schema 失败是可以接受的，不会让跑批停下来；它们会被记在
`summary.json` 的 `build_errors` 里。

这个阈值和 gold 那个阈值是故意分开的。gold 的比例回答的是“这是不是一个覆盖了我们
所请求范围的系统性配置错误”，所以它是拿这次跑批打算构建的那批 schema 来做分母；而
构建流失是另一种失败，它有自己的检查，而不是从 gold 的分母里反推出来。

**三种流失，三个独立的信号。** 它们的修法不一样，所以产物里是分开记的，而不是给出
一个含义混在一起的“覆盖率”数字：

| 丢掉了什么 | 记在哪里 | 会做什么 |
|---|---|---|
| 请求了，但这个 schema 没加载到 Postgres 上 | `dbs_absent_from_postgres`、`n_dbs_requested` | 告警；挡住可引用性 |
| 加载了，但 corpus 构建失败 | `build_errors` | 挡住可引用性；低于 50% 直接中止 |
| 构建出来了，但它的 gold 跑不动 | `gold_hash_self_check.exec_error_dbs` | 挡住可引用性；超过 25% 直接中止 |

第一种是大规模跑批之前最该确认的一种：另外两道门都看不见它，因为它们的分母都是“实际
存在的那些 schema”。一次默认配置的跑批，在一个只加载了部分 schema 的 Postgres 上，
会心平气和地给 69 个里的 40 个打分，并且报告说自己尝试过的部分覆盖率是满的——所以在
花掉模型预算之前，请先确认你的 Postgres 上有你打算测的每一个 schema。

## 第 1 步——离线冒烟测试，不碰模型

```bash
uv run python -m governed_bi.eval.run_datalake --skip-agent --limit-dbs 3 --limit 5
```

跑通模型之外的这套 harness——构建、池化、打分、汇总、入台账——用一个全拒答的
求解器。每个臂都会打 0 分，本来就该这样。你要检查的是它能跑完全程、写出
`summary.json`、并往 `runs/index.jsonl` 追加一行。

**它不会跑到路由这一步。** 全拒答的求解器在图被构建出来*之前*就返回了，所以
不会构造 embedder（哪怕开着那个旋钮，manifest 里记的也是 `use_embedder: false`），
不会检索出任何 schema shortlist，LLM pick 也从来不会跑。一份被引用的结果里
每一个和路由有关的数字——`routing_recall`、`schema_pick_accuracy`、
`by_gold_rank`——测的都是一套这一步完全没碰过的配置。那条路径第一次真正跑
起来，就是那次要花钱的运行，所以那里出现的早期路由异常，该当成接线还没测过，
而不是当成一个结论。

## 第 1b 步——跑批前的试跑（要在第 2 步之前做，过了这一步就补不回来了）

有两个问题只能在完整跑批*之前*回答，因为两者都需要对某个第 2 步只抽一次样
的东西，再多抽一次。

**corpus 本身稳不稳？** `--replicate` 服务的是*同一份* corpus 两遍，所以它测
出来的噪声下限只是 serve 端的采样噪声（`noise_floor.source:
"serve_replicate"`）。但 `curated` 的 corpus 是一个随机 agent 的一次抽样，
每一个 `curated` 或更靠后档位的差值，测的时候用的下限都把这份方差排除在外
了。先从免费的做起：把 `curated` 在分层试跑上构建两遍，分别放进两个全新
目录，再去比对这两份 corpus——asset 数量、连接集合、few-shot id、笔记文本。
这只是在 6 个 schema 上多跑一遍 curator，不涉及 serve。

如果两次构建几乎一样，这份担心就可以放下，你可以老老实实引用那个只测
serve 端的下限。如果它们有实质性差异，就花上这笔要花钱的版本：把这个试跑
用 `--replicate curated` 再跑一遍，这样同一组 166 个问题上就有了三个数
字——一次运行内部的 serve-only 不一致度、两次运行之间的 build+serve 不一致
度，以及两者之差，也就是可以归因于构建的那一项。你不是要精确测出构建方差，
只是要弄清楚它比 serve 方差小得多、差不多，还是大得多。这个答案会给每一个
全切分区间定下一个诚实的乘数——用一次试跑的代价把它先弄清楚，好过等写报告
的时候才发现。

**模型供应商是不是在悄悄漂移？** 各个臂是按小时数量级依次串行 serve 的，所
以供应商那边的漂移会单调地叠加到这条阶梯上，看起来和阶梯本身的效应一模一
样。在某一次试跑里改用 `--replicate baseline`：这会把对照组放在 serve 的第
1 个位置，把它的 replicate 放到最后一个，是这次运行里力臂最长的一对。
`noise_floor` 已经带着一个有符号的 `net`，以及一个在 `|net| > 2√d` 时触发的
`suspect` 标记；隔着这么长的跨度，一个数值很大的有符号 `net` 就是漂移，不是
采样噪声。如果这次试跑的 `net` 接近零，一个单调的全切分结果引用起来就安全
得多。全切分本身仍然用 `--replicate curated`。

## 第 2 步——真正的跑批

```bash
uv run python -m governed_bi.eval.run_datalake --build-workers 6 --workers 8 --replicate curated
```

默认值：测试切分里的全部数据库，臂为 `baseline,seeded,curated,curated_sme`。

**开跑之前先搞清楚它有多大。** 测试切分是 **69 个数据库 / 2030 个问题**。加上
默认的四个臂再加 `--replicate curated`，一共是五轮 serve，也就是 **10150 次
被打分的对话**——每一次都是一整个 agent 循环，不是一次简单的补全。除此之外，
构建阶段还要在 69 个数据库上跑两遍 LLM curator：一遍给 `curated`，一遍给 SME
那一轮。`baseline` 和 `seeded` 的构建不花任何模型调用。如果再加上
`curated_sme_blind`（见下文），就变成三遍 curator、六轮 serve。

成本和延迟本来就是按每次对话记录下来的——`summary.json` 里的
`arms.<arm>.cost`（`total_tokens`、`total_cost_est_usd`、`n_rows_priced`），
Langfuse 里也是同样这些字段——所以如果你想在投入整个切分之前先看个数，先跑
`--limit-dbs 3`，然后按比例放大。按阶段拆开的 token 数记在
`generations.<arm>.jsonl` 里每一行的 `token_usage` 下，并没有汇总进 summary，
所以要把开销归因到某个阶段，就得回去重新读那份行文件。宁可自己这样量一遍，
也别信这份文档里给的估计：这个仓库从来没有带模型跑完过完整的切分，所以这里
写下的任何数字都只能是猜的。

**给两个旋钮定尺寸。** 它们各自耗尽不同的资源，被刻意分开。`--build-workers`
让整个 curator 构建并发进行——每个构建都同时占着一个 Postgres 连接*和*一段
deep-agent 对话，所以它该按你的模型供应商的速率限制来定。
`--workers` 把按问题的 serve 循环并行开来；按 Postgres 的 `max_connections`
减去余量来定。先保守起步。

提高 `--workers` 还有第二重、小一些的代价：检索索引缓存是按 worker 分开的
（线程安全就靠这个），所以每个 worker 都要为每一份被路由到的 corpus 各自
建一份 embedding 索引。在 69 个 schema 上开 `--workers 8`，建索引的次数
就从 69 次变成最多约 550 次。每一次都比一次模型调用便宜得多，而且彼此重叠，
所以不会改变上面那条加速比曲线的形状——但一次开得很宽的运行，最初几个问题
比进入稳定状态之后慢，原因就在这里。

如果 `summary.json` 里的 `arms.<arm>.by_error_type`
满是 `RateLimitError`，就调低 `--build-workers` 再重跑——这些行会被算成崩溃，
正确地阻断可引用性，而不是悄悄拉低一个分数。

**如果你打算引用一个差值，`--replicate curated` 不是可选项。** 它会把某个臂
再服务一遍，让这次运行能测出自己的噪声。没有它，每一次比较都只报一个 p 值，
不报任何分辨率——这个项目已经发布过一次淹没在噪声里的零结果了。它的代价
只是多跑一趟 serve。

**但要说清楚它测的是哪一种噪声。** 它把*同一份* corpus 服务两遍，所以这个下限
测的是 serve 端的采样噪声：解码、LLM 的 schema 挑选、工具调用顺序。这正是它被
记成 `noise_floor.source: "serve_replicate"` 的原因。它**不**测量 corpus 本身的
方差，而在这条阶梯上，corpus *就是*这次实验的干预本身——每一份 `(arm, db)`
corpus 都只是一个随机 curator agent 的单次抽样，n=1。所以一个越过了
`detectable.mde_questions` 的差值，只是越过了 serve 噪声，对“同一个 schema
再跑一次 curator，会不会产出同一份 corpus”这件事，什么都没说。

harness 目前测不了这一点。一个已经被采信的结果，日后最可能就是从这个缺口上被
撤回的。要补上它，需要用一个全新的 agent，对一部分 schema
重新构建某个臂，把两份 corpus 都服务一遍，再从这一对里取出下限——大致是在 20
个 schema 上多跑一遍 curator，再加一趟 serve。在这之前，请把任何 `curated`
或更靠后档位的差值，都当成只被 serve 噪声下限约束住了，并在引用时说明这一点。

## 第 3 步——反事实档位（单独一次运行）

```bash
uv run python -m governed_bi.eval.run_datalake --arms baseline --workers 8 --oracle oracle_schema,oracle_tables,oracle_tables_padded
```

把这些放在公平阶梯*之后*跑，并把它们读成可提升空间的上界，绝不要读成系统的
表现——每一个都是拿答案key构建出来的。`oracle_tables_padded` 是 `oracle_tables`
的对照组：同样的 gold 表，用非 gold 表填充回一个可比较的数量，这样表的*身份*
在变，而规模大致不变。任何一行 `oracle_padding_degenerate` 为真的都要跳过。

`--workers` 对这些档位同样起作用。每个 worker 都拥有自己的求解器、连接器和
图缓存——和公平臂用的是同一套隔离——按 worker 数量拆分之后，单个 worker 的
图缓存上限也随之缩小，总量到 `--workers 8` 为止是平的。再往上，每个 worker
4 份图的下限会顶上来，总量就开始涨了（16 个 worker → 64 份，32 个 → 128 份）；
这个下限是故意留的，上限压到 1 就把真正管用的那点复用也一起压没了。

**动手之前先知道它有多大。** 这些档位是**在 `--arms` 之外另加**上去 serve 的，
不是替换掉它——`--arms baseline --oracle X,Y,Z` 是**四**轮 serve，也就是
**8120 轮打分**，大约是第 2 步 serve 预算的八成，而且每一轮都是一整个 agent
循环。像这一步以前那样串行 serve 它们，是这本手册里最长的一段冤枉等待。

`oracle_base` 取的是 `--arms` 里的**最后**一个臂，档位收窄的就是那个臂的
corpus。上面这条命令传的是 `--arms baseline`，所以它量出来的是 baseline 的
headroom。

**想给阶梯顶端定界——`--arms curated_sme`——得把一整轮构建的账也算进去。**
corpus 是放在运行目录里面的，所以另起一次运行就是从零重建：写上 `curated_sme`
会同时把 curator 和 SME 那一轮都打开，也就是在池子里每个 schema 上各跑一遍
LLM curator、再各跑一遍 SME，这些都叠在那四轮 serve 之上。它不是上面那条命令的
便宜版本，更接近于第二次第 2 步。

## 挂钟时间都花在哪儿

两个旋钮，卡住的是两件不同的事。`--build-workers` 让整个 curator 构建并发
运行——每个构建都同时占着一个 Postgres 连接*和*一段 deep-agent 对话，所以
它该按你的模型供应商的速率限制来定。`--workers` 把按问题的 serve 循环铺开。

构建过程并没有做重复的工作：`seeded` 走的就是 `curated` 的代码路径，只是把
agent 关掉了（不产生模型调用）；两个 SME 臂都是在已经构建好的 `curated`
corpus *之上*接着构建，而不是从零开始，所以默认这条阶梯每个 schema 只花一
次 curator、一轮 SME——不是每个臂各花一次。

**各个臂依次串行 serve，这一点不花任何额外代价。** 每个臂都有 2030 个问
题，对面最多不过几十个 worker，所以每个臂自己就能把这个池子跑满；把它们
叠在一起 serve 并不会提高利用率，只会在同一个速率限制之下把峰值请求速率
推得更高。

**还剩一个结构性的优化没有做。** `baseline` 和 `seeded` 构建时都不需要模
型，但它们仍然要等整个 curator 阶段跑完才开始 serve，因为构建和 serve 是
严格串行的。把它们交错起来，本可以把 curator 阶段藏在这两个臂的 serve 时
间背后。这是故意没做的：这么改要重新划分构建、serve 之间的这道阶段边界，
而恢复运行、stage-event 事件流、按构建分开的暂存根目录全都靠这道边界来定
位，这不是一次要花钱的跑批之前该顺手改的事。如果跑批时长是你的瓶颈，这是
之后第一个该去做的事。

所以老实的建议是：把 `--build-workers` 往上调，一直调到你在
`arms.<arm>.errors.by_error_type` 里看到 `RateLimitError` 为止，再退回一
档。真正卡住你的是你的模型供应商，不是这份代码。

## 第 4 步——按这个顺序读产物

1. `runs/index.jsonl` 最后一行。优先看 `ledger_ok` / `hygiene_ok`（`quotable`
   的别名）。若为 `false`，读 `not_ledger_ok_because` / `not_quotable_because`
   然后就此打住。里面列出的每一条理由，都是一件会让这些数字的含义和它们表面
   看起来的样子不一样的事。**`ledger_ok: true` 只表示卫生**——不要把它当成
   `claim_ready`。台账里的 `claim_ready` 恒为 `false`，并列着
   `claim_ready_requires`；声明闸门是上面那份清单。
   `arithmetic_floor_questions` 是*这一次*臂数量下的 Holm 家族下限（四臂 → 8，
   五臂 → 9），不是“够不够发表”的充分条件。

   如果这次运行最后抛的是写台账时的 `PermissionError`，台账里没能留下那一行，
   那什么都没丢——`summary.json` 和 `manifest.json` 早就落盘了，而追加操作本身
   是幂等的。把它重新入账就行：

   ```bash
   uv run python -m governed_bi.eval.index --add runs/datalake/<timestamp> --quiet
   ```

   除非你想看那张完整的表，否则记得带上 `--quiet`：渲染出来的比较区块按记录数
   的平方增长，到大约 120 条记录就已经是几千行。

   台账会把这台机器上任何人跑过的运行都攒起来，冒烟测试也算。想把它收回到只剩
   你真能去查的那些运行：

   ```bash
   uv run python -m governed_bi.eval.index --prune --prune-outside-repo --reindex --quiet
   ```

   `--prune` 丢掉目录已经没了的记录，`--prune-outside-repo` 丢掉写在仓库外面的
   临时运行，`--reindex` 则把每一条留下来的记录按当前这套闸门重新判一遍——某条
   记录写下来的时候要是那道闸门还不存在，它就从来没被判过。
2. `summary.json` → `treatment_divergence`。这些臂到底有没有真的交付出不同的
   上下文？一对交付了相同上下文的臂，其实是同一个实验跑了两遍。
3. `summary.json` → `comparisons[]`。先读 `reading`，再读 `net_questions`。然后
   看 `p_value_holm`（不是原始的 `p_value`——四个臂就是六次检验），以及
   `cluster` 块，它把每个数据库当成一个观测，而不是每个问题。每一条记录都会
   说清楚自己是什么：`single_variable` 为 `false` 且带着一个 `bundles` 列表，
   意味着这个差值覆盖了不止一项改动；`ladder_descending` 为真，则意味着这
   一对是按字母顺序排的，而不是按阶梯顺序排的，所以它的正负号读起来是反的。
   这两者都写在这条记录本身上；不需要再去对照 `deltas` 块。
4. `summary.json` → `arms.<arm>.errors`，看错误答案都去了哪里；再看
   `arms.<arm>.errors.by_result_shape`，看一个错误答案是返回了空结果，还是
   返回了和 gold 行数一样、但内容不同的结果。
5. `summary.json` → `arms.<arm>.by_db.<db>`，当某个全局数字需要解释的时候。每个数据库携带的诊断块和这个臂本身**一模一样**——EX、routing recall、`cond_ex_given_routing`、outcome 与崩溃分布、错误分类、成本——因为它就是同一个函数跑在那个数据库的行上。所以一个池化后的数字永远可以拆开看：如果全局 `routing_recall` 是 0.71，per-db 这一块会告诉你那是少数几个 schema 路由器完全看不见，还是所有地方都差一点点——这两件事的修法不一样。配合 `comparisons[].cluster.dbs_worse` 一起读，后者只点出哪些数据库退步了，不说为什么。

6. `summary.json` → `deltas.*_usd_per_added_correct`。每一档多答对的题，代价是
   多少钱。这条阶梯的机制是：越往后的档位塞进的上下文越多，而上下文是要计费
   的，所以一档如果是靠买来的准确率，买它用的就是 token。把它和
   `deltas.*_usd`（这一步总共花了多少钱）、`deltas.*_correct_answers`（这一步
   买到了什么）放在一起读。计价被拒绝时，`deltas.*_not_priced_because` 会
   用文字说明原因——去读那句话，而不要来这里对照一份列表：真正的枚举是
   `eval/run_datalake.py` 里的 `price_verdict`，它的结果会以
   `PRICE_VERDICT_TAGS` 的形式发布，并且有一个测试断言每一种结果都是可达的。
   此前两次想把这份列表原样搬进这份文档，都在一次提交之内就过时了。

   **`*_correct_answers` 只做配对。** 计价和这个规范增益字段都要求两个臂的
   **问题 id 集合相同**（不能只是 N 相等）。题池不同时，`*_correct_answers` 为
   `null`，原因写在 `*_correct_answers_unmeasured_because`；原始的 `n_correct`
   相减若还出现，只会落在 `*_unpaired_n_correct_delta` 这个不会被误认的名字下，
   不能当成“多答对了几题”来引用。关心“哪些题动了”时，优先看 `comparisons[]`
   里的配对不一致增益。

   **一档如果丢答案，会被计入 `deltas.*_usd_per_lost_correct`，** 而不是
   `_usd_per_added_correct`，而且只有当成本的统计覆盖了每一行时才会计价。之所
   以要拆成两个键，是因为一旦分母变成负数，一个“每多答对一题花多少钱”式的
   数字，符号就没法解读了：一档丢了 10 个答案*同时*又变便宜了（一个过度谨慎、
   拒答更多的层就会这样：拒答既便宜又错），以前会标价成 **+0.05**，读起来像“每多
   答对一题多花 5 分钱”，可它其实是一次退步。如果一次退步的成本统计只覆盖了
   部分行，这个键会直接*缺失*，而不是 `null`，所以拿它去 grep 什么都搜不到；
   `_not_priced_because` 才是能告诉你原因的地方。

   现在两个键分开了，符号的含义如下：

   | 键 | 什么时候出现 | 负数的含义 | 正数的含义 |
   |---|---|---|---|
   | `_usd_per_added_correct` | 这一步答对的题变多了 | 答对的题变多了**并且**变便宜了——最好的情况 | 答对的题变多了，但花了钱 |
   | `_usd_per_lost_correct` | 这一步答对的题变少了 | 答对的题变少了**但**变便宜了 | 答对的题变少了**并且**花了更多钱——最差的情况 |

   所以现在每个键的符号，单独看都是可以解读的；不能做的是拿两个键的符号互相
   比较，因为一个负数在其中一个键下是最好的情况，在另一个键下未必是。先读
   `_correct_answers`，弄清楚自己现在看的是哪一个键。

7. `summary.json` → `arms.<arm>.by_tier`、`by_semantic_assurance`、
   `graded_delivery_rate`、`safety_clearance_rate`。**光凭 EX 撑不起这个论断。**
   这个基准测试存在的意义，就是要证明受治理的元数据能让答案变好，而可靠性是按
   `semantic_assurance` 打分的——所以一个臂如果一边把 EX 抬高，一边又把质量的
   重心从 `grounded` 推向 `unverified` 或 `none`，或者靠交付更多低于可靠性门槛
   的答案来抬高 EX（`graded_delivery_rate` 上升），那它换来的是分数，赔掉的是
   治理，而不是让产品变好。这几项也都会按阶梯报出差值，所以这笔交易在每一档上
   都看得见，不只是按臂才看得见。

   把这些比率和它们旁边的 `n_*_observed` 分母放在一起读。一个比率是 `null`，
   代表根本没有任何东西记录过这个字段，这和比率为 `0.0` 是两回事。这三个布尔
   比率都是以**已交付**为条件算出来的（也就是那些交回了 SQL 的行）：因为一次
   拒答会把 `safety_clearance` 标成 `False`，而且什么都没交付，把拒答也算进
   平均值里，会让一个疯狂拒答的臂看起来治理得最好。拒答本身的行为交给
   `refusal_rate` 去衡量。

   **这三项是整份汇总里唯一一块没有任何离线运行会跑到的部分。** 一次
   `--skip-agent` 运行会拒答一切，三项一个都不会被打上标记；`oracle_sql` 会
   打上 `tier` 和 `semantic_assurance`，但不会打上那两个布尔值。所以第一次真正
   的运行，才是它们第一次真的带上数值——读这些比率之前先确认 `n_*_observed`
   不是零，把那里的 `null` 当成“这条路径的插桩根本没跑到”，而不是当成一个结果
   来读。

8. `summary.json` → `arms.<arm>.serve_index` / `serve_started_utc` /
   `serve_seconds`。**各个臂是依次串行 serve 的，不是交替进行的。** 在一次大
   规模跑批里，第一个臂和最后一个臂之间可能隔了好几个小时，而对面是一个托管
   的模型供应商，所以供应商那边任何一点行为漂移，都会单调地叠加到这条阶梯上，
   和某一档本身的效果分不清楚。

   harness 没有去消除这一点——按问题交替 serve 各个臂，会重新改造 serve 循环、
   每个臂各自的 generations 文件，以及恢复运行的契约——所以它选择把位置记录
   下来，而不是消除它。在相信一个单调的结果之前，先检查一下 EX 是不是跟着
   `serve_index` 走，而不是跟着阶梯走。有两件事能约束住这个风险：replicate
   是**最后**才 serve 的，离它所复制的那个臂距离最远，所以噪声下限本身就已经
   吸收了跨越至少一个臂的 serve 过程的漂移，而不是一个“同一时刻”的数字；另外，
   cluster 检验把每个数据库当成一个观测，一次全局性的漂移会均匀地影响到它们
   所有人。

## 这条阶梯能告诉你什么，不能告诉你什么

每相邻一步只改变一件事：

| 步骤 | 新增了什么 | 它的差值意味着什么 |
|---|---|---|
| `baseline → seeded` | 训练集 SQL 推出的连接与指标，诱饵 / 负空间标记；同时*去掉* baseline 按命名规范猜的外键。**没有 LLM，没有 few-shot。** | 多机制：训练集 gold SQL 的连接/指标**以及**它的负空间（外加去掉 FK 名猜测）。**不是**“单靠解析”或 few-shot 的估计。 |
| `seeded → curated` | LLM curator agent（含 few-shot），作用在同一份种子之上。 | curator LLM 在那趟免费的确定性处理之上又多贡献了什么。 |
| `curated → curated_sme` | Simulated-SME 澄清轮次。 | **有混淆——见下文。** |

`baseline → curated` *不会*作为一个步骤被报告，因为它把前两步捆在了一起，说不清
是哪一步在起作用。如果你用 `--arms` 跳过了某一档，产生的复合步骤仍然会被
报告，但会标注清楚它到底捆绑了什么——写在这一对本身上，即
`comparisons[].bundles`，也写在 `deltas.*_bundles` 里，以及标准输出上。三者
出自同一个函数，所以它们不会互相矛盾。

**SME 混淆。** SME 的任务简报是基于 BIRD 人工撰写的 `database_description/*.csv`
构建的，而 curator 从来看不到这个目录。所以一个为正的 `curated → curated_sme`
差值，既可以解释成“我们第一次给流水线塞进了一个新的知识来源”，也可以解释成
“澄清协议本身生效了”，两种说法一样自洽。要把它拆开，加上这个可选档位：

```bash
uv run python -m governed_bi.eval.run_datalake --arms baseline,seeded,curated,curated_sme_blind,curated_sme --build-workers 6 --workers 8 --replicate curated
```

`curated_sme_blind` 跑的是同一轮，但 SME 看不到那些 CSV，所以
`curated → curated_sme_blind` 就是协议本身，`curated_sme_blind → curated_sme`
就是那些人工文档值多少。它的代价是每个数据库都要多跑一整轮 SME，这也是为什么
它不是默认项——但不拆开的版本撑不起这个基准测试本来要证明的那个说法。

## 在引用任何数字之前

- [ ] 跑过 `oracle_sql`，它的 EX 是已知的。之后的每一个数字都要拿它去对照阅读。
- [ ] `runs/index.jsonl` 最后一行写着 `ledger_ok` / `hygiene_ok` / `quotable: true`
      （同一套产物卫生闸门的别名）。这**不是**声明就绪：台账里的
      `claim_ready` 恒为 `false`，并列着 `claim_ready_requires`。先清卫生，再走本清单。
- [ ] 这次运行带了一个 `--replicate` 臂，所以 `comparisons[].reading` 能说出
      它到底能分辨到什么程度，而不是“没测出噪声下限”。
- [ ] 你想引用的差值越过了 `comparisons[].detectable.mde_questions`（这是
      按每次比较记的，不是一个顶层字段；它要对照的那个下限，读的是
      `comparisons[].noise_floor`）。如果 `from_zero_discordance` 为真，这个
      下限是一个三法则给出的界，不是一次测量——把它当成报告里最弱的一个论断。
      而且要记住这个下限是
      `serve_replicate`：它约束的是 serve 端的采样噪声，不是 corpus 构建的
      方差，所以在 `curated` 或更靠后的档位上，它约束的是错误的那个量。引用
      这个差值时要把这一点说清楚。
- [ ] `p_value_holm` 低于 0.05，而不只是 `p_value`。
- [ ] `cluster` 块结论一致。一次数据库级别检验看不见的问题级别胜出，是靠少数
      几个 schema 撑起来的；把它们点名出来，而不是把它们平均掉。
- [ ] `treatment_divergence` 显示这次比较里的两个臂确实产生了差异。
- [ ] 你要引用的这一步，在它自己的 `comparisons[]` 记录上写着
      `single_variable: true`。如果它带着一个 `bundles` 列表，这个差值就
      不能归因到其中任何一件事上。如果它写着 `ladder_descending: true`，
      说明这一对是按字母顺序排的，而不是按阶梯顺序排的，`net_questions`
      相对“这一档是否有帮助”这件事来说，符号是反的。

      **`single_variable` 的意思是“相邻档位”，不是“只有一个机制”。** 它单
      纯是从阶梯的相邻关系算出来的（`arms.skipped_rungs`），所以它在每一
      对相邻档位上都是 `true`，包括 `baseline → seeded`——而这一步其实
      *新增*了训练集推出的连接和指标，*去掉*了 baseline 那套按命名规范猜
      的外键，还*新增*了一个以训练集为条件的列掩码，把训练集 gold 从未碰
      过的每一列都标成可疑。这个掩码覆盖了大约 86% 测试问题的 gold 列。
      所以 `baseline → seeded` 测的是“训练集 gold SQL 值多少钱，包括它的
      负空间”，而不是“解析训练集 SQL 值多少钱”。真正只隔离出一件事的是
      `seeded → curated`：同一条代码路径，只是把 curator agent——也就是
      模型这个开关——打开了。

## 恢复运行

```bash
uv run python -m governed_bi.eval.run_datalake --resume-from runs/<dir> \
  --arms <原来的臂> --dbs <原来的 dbs> --oracle <原来的档位> \
  --replicate <原来的 replicate> \
  --limit <原来的 limit，若有> --limit-dbs <原来的 limit-dbs，若有> \
  --build-workers 6 --workers 8
```

**每一个决定范围的 flag 都要重写一遍。** `--arms`、`--dbs`、`--oracle`、
`--replicate`、`--limit`、`--limit-dbs` 是从命令行读的，不是从目录读的，所以漏掉
一个不等于“沿用原来那个”，而是“用默认值”。在一次 `--arms baseline` 的运行上漏掉
`--arms`，就会把四个默认臂全捡起来；在分层小规模试跑上漏掉 `--dbs`，池子会从
166 个问题涨到全部 2030 个。范围和 manifest 不一致时，运行会**直接拒绝**，而不是
闷头照办，所以一条写错的恢复命令代价是一条报错，而不是在 69 个 schema 上白跑
一遍 curator。这几个 flag 记在 `manifest.json` 的 `arms` / `db_ids` / `oracles` /
`replicate_of` / `limit` / `limit_dbs` 下面——原来的命令找不着了就从那里读。

已经出现在 `generations.<arm>.jsonl` 里的问题会被重放，而不是重新服务。在原始
运行和一次恢复之间换一个提示词变体是致命错误，会被拒绝；换掉 `--skip-agent`
同样是致命错误——**一个第 1 步的冒烟测试目录，是没法恢复进第 2 步里去的。**
它里面的行全是构造阶段的拒答，打的是 0 分，把它们重放进去，就是把它们混进了
一个要花钱的臂的分母里。第 2 步请另开一个新目录。并发相关的旋钮可以随便改
（它们会被记进 manifest，但不算恢复用的旋钮，因为按构建隔离让并发宽度和一行
记录的含义无关）。

**构建完整性。** 恢复、暂存播种、跳过、提升，都以 `<db>/_build/` 下的
`BUILD_COMPLETE.json` 为准，而不是“有任意一个 `*.yaml`”。没有该标记的半成品
YAML 会当作残骸重建。`--build-workers > 1` 时，每次构建开始会清暂存，只信任
已提升且完整的构建。

**提升与 generations 改写。** 提升用同文件系统换名（incoming → live，旧树挪开，
新树落地后再删旧树），中途被杀时留下旧的或新的 corpus，不会留下空洞。崩溃行
恢复时对 `generations.<arm>.jsonl` 做临时文件 + replace，中途被杀仍保留旧文件。

**付费恢复上的代码 SHA。** 改过代码之后，付费恢复若 `git_sha` 漂移会直接拒绝，
除非传 `--allow-git-sha-drift`（或先前 manifest 已记下该覆盖）。行跨多个 SHA 时，
台账仍会标为不可引用。冒烟（`--skip-agent`）只告警并继续。

## 引用不含孪生题的那一层

**1627 个可打分的测试问题里，有 182 个（11.2%）的 gold 语句，把字面量抹掉
之后，和它所在 schema 训练集里的某条 gold 语句一模一样。** 引用时用这个分母。
`seeded` 的种子就是从训练集 gold SQL 里推出来的，`curated` 又是在训练集上跑
agent，所以在这些问题上，EX 的提升既可能来自泛化，也可能只是来自背下来——EX
分不清这两者。这个比例并不均匀——`student_loan`、`university` 和 `video_games`
最严重——所以某个 schema 的结果很可能大半都是孪生题。

分母用的是**可打分**的那些行，不是全部 2030 行。冻结的 `VALUES(...)`
gold，一旦把常量抹掉，全都会塌缩成同一个规范形状，这些行彼此之间是那种毫
无意义的孪生；它们本来就已经在 `ex_gradeable` 之外，两层里哪一层都到不
了。25 道顺序敏感题也因同一理由剔除，所以这个分母正好就是 `ex_gradeable`
的分母。更早一次在全测试切分上、未过滤的计数是 **246/2030**；那是历史数字，
不要和可打分口径混用。

id 层面的不相交检查（`leakage.train_test_disjoint`）看不见这件事。它证明
的是没有任何一个问题 *id* 同时出现在两个切分里，这话是真的，但说的是另一
件事。

**这条通路是直接的，不是假设出来的。** `curated` 的 few-shot asset 就是字
面意义上的（训练集问题、gold SQL）对——`curator/pipeline.py` 把 `id / Q /
evidence / sql` 这样的三元组交给 agent，`asset_bag.upsert_few_shot` 把它们
持久化下来。（`seeded` 没有这些。）到了 serve 阶段，`retrieval/rvgd.py` 按和
*测试问题文本*的相关度去挑 few-shot，然后把检索范围扩展到被选中那条范例的
SQL 里点到的那些表。所以一个孪生题给出的不只是一个可以模仿的形状：把它检索
出来，同时也定死了表的选择。这就是一条从测试问题到训练集孪生答案、实打实能
跑通的最近邻查找。

还要注意，这个严格意义上的孪生比例，只是暴露程度的一个**下限**，不是全
部。把定义放松一点，这个比例会陡然上升——同时共享一套表和一个查询形状的
问题，占了这个切分的大多数；只是共享一套表的问题，几乎是全部。这意味着诚
实的读法是比较性的，而不是绝对性的：如果这个差值在孪生层和无孪生层上一样
平，这就不支持“靠背下来”这个解释；如果它集中在孪生层里，那就是靠背下来
的。数字显示的是哪一种，就照实说哪一种。

每次运行现在都会给每一行标上 `gold_twin_in_train`，并且分两层报告。孪生率
和 `comparisons[].no_twin` 要求打分行上**戳记全覆盖**：只要还有未打戳的行
（`n_twin_unstamped > 0`），这两层就读成 `null`，而不会只在已打戳子集上出
一个率、同时让池化指标仍把未打戳的行算进去。

| 字段 | 读法 |
|---|---|
| `arms.<arm>.ex_no_twin` | **站得住脚的那个头条数字**——curator 在这些题上无背可背 |
| `arms.<arm>.ex_twin` | 带着“可能靠背诵”味道的那一层，值得报告，但不能拿它当结论 |
| `comparisons[].no_twin` | 同一个配对检验，限定在无孪生的那一层上 |
| `leakage.structural_gold_twins` | 比例、最严重的那些 schema，以及每个 schema 的具体计数 |

要单独去看 `comparisons[].no_twin`，不要想当然地认为它会跟着池化结果走。
丢掉这 11% 的切分会让区间变宽，一个差值完全可能在这一层上就分辨不出来
了——真要是这样，诚实的说法就是：在那些流水线根本无法靠背诵拿分的问题
上，这个效应还没有被确立。

这个区块上有两个标签，都是真的，也都很容易被一眼扫过去而误读：
`p_value_is_raw`（Holm 校正只在顶层那个检验家族里做，所以**不要**拿这个
p 值去和池化的 `p_value_holm` 比——那样比出来的结论会偏向“这个效应挺得
住”），以及 `floor_from_full_split`（它的噪声下限和 MDE 都来自全切分的
replicate，描述的是一个仍然含着孪生题的总体；保守，但不是这一层自己的分
辨率）。`n_twin_unstamped` 在跨过打戳边界的半截恢复、或任何戳记不全的集合
上都会非零——那些行在这两层里读到的是 `null`，而不会悄悄地被当成池化数字。

这不是一道闸门。孪生题是这个基准测试本身的属性，拒绝给它们打分，就是白白
扔掉这个切分的八分之一，还会改掉每一个已发布的 BIRD 数字所用的那个分母。

## 应该和任何结果一起说明的已知局限

- **这套评测不会经过产品真正的入口。** 打分用的运行是直接驱动
  `build_serve_rails` 的。HTTP 的 `/chat` 路由和 LangGraph server 图都从未被
  跑到，叙述器（narrator）的 LLM 调用、SQL 缓存（在每条路径上都从未被构造出来）、
  工作记忆、HITL 澄清、运行日志，同样都没有被跑到。EX 测的是 analyst 核心，
  不是那个已部署的系统。
- **这套评测的路由配置生产环境用得到，但它不是默认值。**
  `schema_route_top_k`、`schema_route_llm_pick` 和 `schema_pick_max_columns`
  现在会从 `[routing]` 表里读取，所以一次部署*可以*跑评测所测的那套配置——
  但前提是它被配置成那样。dataclass 的默认值仍然是 shortlist@3、不带 LLM
  pick，而池化评测默认是 shortlist@10、*带* pick。如果你要引用某个数据湖
  的数字，就要说明它是在哪套路由配置下测出来的，并且把对应的 `[routing]`
  块写进部署的 TOML 里：

  ```toml
  [routing]
  top_k = 10
  llm_pick = true
  pick_max_columns = 12
  ```
- **`curated` 里不含任何笔记。** 系统里唯一的 `NoteAsset` 生产者是
  `AssetBag.record_caveats`，只有 SME 构建流程才会走到它。curator agent 没有
  写笔记的工具。所以笔记这整套机制，只有 SME 臂才真正跑到过。
- **few-shot 泄漏防护形同虚设。** 没有任何东西会填充 `source_refs`，也没有
  任何调用点会传入 `train_refs`。真正在保护这条切分的，是调用点本身和不相交性
  断言，两者都是可靠的，但也都只是“靠结构强制执行”，而不是靠这道防护本身。
