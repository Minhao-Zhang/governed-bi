# 数据湖运行：操作手册 + 状态

_[English](datalake-run.md) · [简体中文](datalake-run.zh.md)_

_实现 [D15](../design-decisions.zh.md#d15多-schema-服务一个数据库多个-schema)（一个数据库，多个 schema）。配套文档：[eval-ladder-results.md](eval-ladder-results.md)（单库的 arm、方法、术语，英文）以及 [experiment-runbook.zh.md](experiment-runbook.zh.md) 里的操作清单。付费的池化运行之前先读操作手册。Arm 名称：`baseline` / `seeded` / `curated` / `curated_sme`（`curated_sme_blind` 可选）。_

## 这是什么

单库 harness（`governed_bi.eval.run_experiment`）每次运行只钉住**一个 schema**。数据湖运行反过来，把 **69 个 BIRD `db_id` 全部作为 69 个 schema 装进同一个 Postgres 数据库**（`pg_rename_decoy`，端口 5435），再加一个**schema 路由器**在服务时按问题挑 schema：这就是 D15 的拓扑放到评测规模上。

驱动：`src/governed_bi/eval/run_datalake.py`，以 `python -m governed_bi.eval.run_datalake` 调用。

默认的公平 arm 是 `baseline`、`seeded`、`curated`、`curated_sme`（与实验操作手册同一条阶梯）。`curated_sme_blind` 需要显式开启。各 arm 之间**只差喂进去的语料库**；路由、护栏、评分都是共享的。

## 怎么跑起来的

三个阶段，由同一次驱动调用按顺序执行。

### 1. 构建（Build）

对每个请求的 `db_id`，把请求的 arm 构建到共享根目录 `corpus_<arm>/`。每个库写自己的 `<root>/<db_id>/` 子树，所以 69 个库在每个 arm 下共享一个根目录，而不像单库 harness 那样每次运行一个根目录。

- **可续跑。** 只有当 `<db>/_build/` 下存在持久的 `BUILD_COMPLETE.json`（且 YAML 在位）时，才会跳过一个库。只有部分 YAML 而没有这个标记的，会重建，不会将就采用。`--no-resume` 从干净状态开始：它会重建每个语料库，*并且*把运行目录里已经打过分的问题全部重新服务一遍。
- **并行构建。** `--build-workers > 1` 时，每次构建用自己私有的暂存根目录，再通过同一文件系统内的交换晋升进共享 arm 根目录（incoming → live；只有新树落地之后才移除旧树）。
- **旁挂文件重定位。** 每个库的 curator 旁挂文件（`run_manifest.json`、`validate_findings.jsonl` 等）会移到 `<root>/<db_id>/_build/`，避免 69 个库在共享根目录里互相覆盖同名旁挂文件。
- **容忍部分失败。** 构建失败的库会从池子里剔除并记进 `build_errors`；除非构建覆盖率跌破中止阈值（见实验操作手册），单个坏库不会让整次运行中止。

### 2. 池化（Pool）

测试问题按库加载，并打上各自的 `db_id` 标签（`EvalItem` 类型本身没有 `db_id` 字段，所以这个标签在池化步骤里与它并列存放）。Gold 哈希跨所有库合并，以 `question_id` 为键。这样做是安全的，因为 `question_id` 全局唯一：在 2030 个测试问题 / 2030 个不同 `question_id` 上验证过，所以池化不会碰撞。

可疑列 / 诱饵列（suspect/decoy）**不按同样方式池化**。它们保持为**按库的集合**，因为把 69 个 schema 的 suspect 集合并起来，会让一个库的诱饵名字在另一个库的问题上误报。每个库的问题只对着这个库自己的 suspect 集合打分。

在一个库内部，decoy-touch 会先把每个列引用解析到它自己的查询作用域再做匹配（`arms._touches_suspect`），这样复用的别名不会把某一列归到错误的表上。真正没有限定、有歧义的引用仍然算一次 touch——失败即拒，与护栏 L3 读取同一种歧义的方式一致——所以这个指标可能高估，但绝不会静默低估。

### 3. 服务（Serve）

整次运行使用一个**未钉住**的 `PostgresConnector(schema=None)` 横跨所有 schema。引擎产出完全 schema 限定的 `schema.table` SQL，裸引用或凭空编造的引用会失败即拒（D15 的护栏契约）。

每个 arm 的语料库用 `_load_built_corpus(root, built)` 加载——范围限定在**实际参与打分的那些库**，而不是磁盘上碰巧存在的东西。arm 根目录是共享且累积的，所以按整个目录去加载会把此前任何一次尝试写下的子树全都端上来：一个从 `built` 里掉出去的库（一次短暂的 Postgres 抖动就够了）会继续作为路由候选去和其他所有库的问题竞争，悄悄改变了两次运行之间路由问题的难度，同时 `corpus_census` 和 `corpus_validation` 描述的语料库与真正被服务的那个并不是同一个。

评分：对着池化后的 gold 哈希算 EX，另外单独报一个实时的 `routing_recall` 指标（真实 schema 活过路由短名单的比例），单独报是为了让路由错误不会藏在一个偏低的 EX 数字里面。

## 路由配置（关键所在）

驱动 schema 路由器的两个 `Settings` 开关，都是这次运行新加的：

| 开关 | 含义 | 默认值（产品 / 单库） | 数据湖驱动默认值 |
|---|---|---|---|
| `schema_route_top_k` | 候选 schema 短名单长度 | 3 | 10 |
| `schema_route_llm_pick` | 由 LLM 从短名单里挑出恰好一个 schema | `False` | `True` |
| `schema_pick_max_columns` | 展示给挑选器的每表列名数量（0 = 只给表名） | 12 | 12 |

当 `schema_route_llm_pick=True` 时，LLM 从短名单里挑出恰好一个 schema（pipeline-design §5.1），并且**跨 schema 的 join 扩展会被跳过**。这是「单 schema 作答」的模式，对 BIRD 是正确的（每个测试问题都只针对一个 `db_id`）。默认值（`False`）是通用的跨 schema 模式，单库 / 产品服务路径保持不变。

数据湖驱动还默认打开 embedder，叠在 `top_k=10` 与 `llm_pick=True` 之上。Schema 文档向量在护轨构建期一次性嵌入（`embed_schema_documents`），不会每个问题重新嵌入。

CLI 开关：`--route-top-k N`、`--schema-pick-max-columns N`、`--no-llm-pick`、`--no-embedder`。四者都会记进 `manifest.json`，在 `--resume` 时受守卫，并被运行 ledger 的可比性规则读取，所以两次运行只要其中任一项不同，就会被报告为不可比，而不是被悄悄拿来对比。

### 关键风险（以及路由的设计）

这次运行的约束瓶颈是 schema 路由，不是策展：一个路由错的问题，无论语料库多好，EX 都是 0。一次覆盖全部 2030 个测试问题、对着只有表的 `../BIRD-corpus` 做的探针，用三种策略量了 schema 路由召回：

| 策略 | recall@1 | recall@3 | recall@5 | recall@10 |
|---|---|---|---|---|
| BM25（词法） | 0.234 | 0.351 | 0.435 | 0.572 |
| **仅 embedding** | **0.517** | **0.700** | **0.785** | **0.860** |
| BM25 + embedding RRF | 0.346 | 0.535 | 0.626 | 0.746 |

两个发现决定了路由器的设计：

- 在这里 BM25 单独用是弱的。BIRD 的问题很少与 schema / 表名共享标识符，所以有十几个 schema（`olympics`、`retails`、`european_football_2` 等）在词法上 recall@3 是 0.00。
- 把 BM25 与 embedding 信号做 RRF 融合，在每一个 k 上都**比只用 embedding 更差**：偏弱的词法排名把偏强的 embedding 排名拖了下来。

于是 `shortlist_schemas` 现在在有 embedder 时按 embedding 相似度排序，没有 embedder 才退回 BM25。在短名单之上，`pick_schema`（LLM 单选）再收窄到一个 schema。

一次完整路径的线上运行（gpt-5.6-luna，embedder 短名单 `top_k=8` + LLM 挑选，跨全部 69 个 schema 的 138 问题抽样，只有表的语料库）量到下面的结果。注意那个 `top_k=8`：这次运行早于驱动默认值改成 10，所以它与今天跑出来的运行不能直接对比——当前默认值见上面的开关表。

**已于 2026-07-25 作废——不得引用。** 产出于测量修正之前，留在这里只是作为
「当时跑过什么」的记录。

| 指标 | 值（已作废） |
|---|---|
| 短名单 recall@8 | 0.848（117/138） |
| `pick_schema` 挑选准确率（端到端） | 0.732（101/138） |
| 真实 schema 在短名单内时的挑选准确率 | 0.863（101/117） |

有效的单 schema 路由约 0.73，相对 BM25 那个约 0.35 的天花板是一次抬升，而且这还是在很薄的「只有表」的语料库上，所以策展过的 arm（schema 文档更丰富）应该至少不会更差。剩下的漏失大多是这个混淆过的数据湖里真正有歧义的兄弟 schema（`food_inspection_2` vs `food_inspection`、`movielens` vs `movies_4`、`computer_student` vs `cs_semester`），任何单选路由器都无法完全解决。

## 前置条件

- `pg_rename_decoy` Postgres 跑在 5435 端口，schema 已装载（目前一共有 171 个 schema；69 个 BIRD 目标全部在内）。装载发生在兄弟仓库 `../BIRD-Data-Obfuscation`（docker-compose + 编号的流水线脚本）里，**不在**本仓库。
- Gold 哈希 + trap manifest 位于 `../BIRD-Data-Obfuscation/eval_dataset` 与 `/artifacts`，覆盖全部 69 个 `db_id`。
- `.env` 里要有 `OPENAI_API_KEY`。这个 CLI 的 Postgres 由 `--pg-dsn` 指定（默认 `host=127.0.0.1 port=5435 dbname=bird user=bird password=bird`）。驱动**不会**自动读 `PG_RENAME_DECOY_DSN`；如果你用的是那个 DSN，显式传进来（`--pg-dsn "$PG_RENAME_DECOY_DSN"`）。产品的 `[datasource]` 覆盖层里可能仍然写着那个环境变量名。
- 模型：`gpt-5.6-luna`（`governed_bi.toml [models].llm_model`）。
- 可选：在 TOML 里用一段注释掉的 `[routing]` 配置把评测路由钉住，与驱动默认值一致（`top_k = 10`、`llm_pick = true`）；产品的 dataclass 默认值仍然是短名单@3、不做挑选。见 `governed_bi.toml` 与实验操作手册。

## 怎么运行

**离线管路冒烟测试**（不调模型；对着线上 Postgres 走一遍 build → pool → serve → grade）：

```bash
uv run python -m governed_bi.eval.run_datalake --skip-agent --limit 2 --dbs beer_factory,address --out runs/datalake/
```

**子集试跑**（推荐的第一个真实步骤，用来端到端验证，并在投入整次运行之前拿到按库的成本 / 延迟估计）：

```bash
uv run python -m governed_bi.eval.run_datalake --limit-dbs 5 --out runs/datalake/
```

**完整运行。** 默认值：所有测试数据库，arm 为 `baseline,seeded,curated,curated_sme`。也就是 69 个库的策展（一趟 LLM curator + 一轮 SME；`baseline` / `seeded` 构建是免费的），后面接 2030 × 4 次 agentic 服务调用——如果加 `--replicate curated` 就是五次（10,150 个打分轮次）。规模与成本纪律见 [experiment-runbook.zh.md](experiment-runbook.zh.md)。

```bash
uv run python -m governed_bi.eval.run_datalake --build-workers 6 --workers 8 --replicate curated --out runs/datalake/
```

其他参数：`--dbs a,b,c`（显式库列表，替代「所有测试库」）、`--arms baseline,seeded,curated,curated_sme`（`baseline,seeded,curated,curated_sme_blind,curated_sme` 的子集；只跑 baseline 可以跳过昂贵的策展）、`--limit N`（每库测试问题上限）、`--limit-dbs N`、`--pg-dsn`、`--bird-dir`、`--max-agent-steps`、`--allow-git-sha-drift`（改过代码之后的付费续跑；ledger 仍然会把这次运行标为不可引用）。

## 产出物

都在带时间戳的 `--out` 目录下。逐字段的完整说明，以及怎么定位某一类具体失败，在 [`measurement.zh.md`](../measurement.zh.md)；本节只是产出物清单。

- `generations.<arm>.jsonl`：按问题的行，包含 `db_id`、`routed_schemas`、`routed_hit`、`schema_pick`，以及来自 outcome/stage 分类体系（`governed_bi.stages`）的 `outcome`（`answered` / `refused` / `clarification` / `capped` / `crashed`）、`failed_stage`、`refused_by`、`n_tool_calls`、`by_guardrail_layer`。`outcome` / `failed_stage` 正是用来区分「真拒答」与「服务路径退化成拒答的崩溃」的；在早于这个字段的运行上，信任 `refusal_rate` 之前先读 `measurement.zh.md`。
- `stage_events.jsonl`：*本次尝试中*每个被服务的问题、每个阶段一条记录（`stage`、`status`、`ms`、`detail`，并打上 `question_id` / `arm` / `db_id`）。`--resume` 时被回放的行不会往这里贡献任何东西——它没有新的计时——所以在续跑过的运行上，这个文件是 `generations.<arm>.jsonl` 的子集，可按 `(question_id, arm)` 关联。
- `summary.json`：按 arm 的 EX（lenient / strict / gradeable）、`routing_recall`、`schema_pick_accuracy`、按库拆分、各项差值、`build_errors`、`gold_hash_self_check`，以及 outcome 的划分：`by_outcome`、`by_failed_stage`（来自 `classify_row` 的实时 Outcome/Stage——与离线分类体系 `arms.<arm>.errors.by_error_stage` 不是一回事）、`crash_rate`（与 `refusal_rate` 分开，后者现在只含真拒答）、`n_unmapped_refused_by`、`n_with_difficulty`、`tool_calls` 与 `by_guardrail_layer`（跨行求和）。
- `manifest.json`：那些会改变「一个打过分的行意味着什么」的开关（`split`、`model`、`route_top_k`、`route_llm_pick`、`schema_pick_max_columns`、`use_embedder`、`prompt_variants` / `prompt_set_hash`、`git_sha`，加上范围类的 `arms` / `db_ids` / `oracles` / `replicate_of` / `limit` / `limit_dbs`），会被 `--resume` 和运行 ledger 的可比性检查读回。
- 构建出来的语料库根目录（`corpus_baseline/`、`corpus_seeded/`、`corpus_curated/`、`corpus_curated_sme/`，以及当 `curated_sme_blind` 参与打分时的对应目录）。
- 往 `runs/index.jsonl`（运行 ledger，`governed_bi.eval.index`）追加一条记录，计算这次运行的产出物卫生状况是否 `ledger_ok` / `hygiene_ok` / `quotable`（这些是别名，**不等于**「结论已经站得住」），以及它与此前哪些运行 `comparable`。在 `run_datalake()` 结束时自动追加；要给已有运行重新建索引用 `uv run python -m governed_bi.eval.index --add runs/datalake/<ts>`。

## 已知限制 / 注记

- **没有交叉核对的 EX。** Gold 自检是对着按 schema 钉住的 gateway、按抽到的库逐个跑的（gold 的 `sql_rename` 没有 schema 限定，所以它需要一个 `search_path`）。因此在数据湖模式下，那个把 gold SQL 拿到「横跨所有 schema」的连接器上重跑一遍的交叉核对 EX 被跳过了。
- **只有 schema 内部的 join。** curator 只从该库自己的训练 SQL 里构建 join，从不构建跨 schema 的 join。对 BIRD 是正确的（每个测试问题都是单库），但一个真正跨 schema 的问题会以缺边拒答的形式失败即拒（D15 只认已声明 join 的契约）。
- **既有的种子质量问题，与数据湖无关。** 有些库由种子推导出的 join 带有引用完整性问题（例如 `address` 从 `seed_from_train_sql` 产出了 2 条 `join-on-unresolved`）。CI 绿灯这道关口会在 `summary.json.corpus_validation` 里把它们大声报出来并告警，但不会中止——这是有意设计成非致命的。
- 两个阶段都可以续跑，机制各自独立：策展只在 `BUILD_COMPLETE.json` 存在时跳过一个库，而服务阶段回放 `generations.<arm>.jsonl` 里已有的行（见下面「切分、续跑与离线分析」）。

## 状态

> **这一页上没有任何数字可以引用。** 2026-07-25 那次作废覆盖的是「这些 EX
> 值」；而路由数字来自 2026-07-19，落在同一个作废窗口里，只是因为那条警告
> 当时只点了 EX 的名字才幸免。它们是被同一套仪器、在同一套「崩溃算拒答」的
> 错误定义下产出的，所以也一并作废。把下面每一个数字都当成「当初尝试过什么」
> 的记录去读，而不是结果。

驱动可以端到端跑通，评测阶梯也能在多库规模上复现。已在机制层面确认（是形状，不是量值）：

- 离线（`--skip-agent`）在 1 库和 2 库池上走通 build → pool → serve → grade。
- 69 schema 规模上的线上 schema 路由跑通：embedder 短名单 + `pick_schema` 量出约
  0.73 的有效单 schema 路由（见上面的路由表），相对 BM25 约 0.35 的天花板。
  **已作废——不得引用。** 方向（schema 路由上 embedding 优于词法）是设计
  前提本身，在 `schema_router` 自己的 docstring 里也独立可见；但数值大小需要
  重新测量。
- **5 库、3 arm 的线上试跑**（72 个池化问题，每库 15 个，`address`、`airline`、`app_store`、`authors`、`beer_factory`）——**这些数字已于 2026-07-25 作废，见下面的警告**：

  | arm | EX | 相比上一档 |
  |---|---|---|
  | baseline | 0.208 | |
  | curated | 0.333 | +0.125 |
  | curated_sme | 0.417 | +0.083 |

  这次运行想找的形状（一条策展护城河，再叠一层 SME 抬升）就是这张表所显示
  的。但这不能证明它真的存在：作废这些数字的同一套测量缺陷，也在移动这些
  数字——而且每个 arm 被移动的幅度还不一样，所以*排序*和数值本身一样不可信。
  这次运行真正确立的是：这套 harness 能端到端跑通。Decoy-touch 从 0.35 → 0.0
  → 0.01（策展的可靠性标注起了作用）；所有 arm CI 绿灯；gold 自检 5/5；没有
  构建失败。这里的路由召回读出来约 0.97，只是因为池子里只有 5 个 schema——
  真正 69 schema 的路由数字是上面那个约 0.73，不是这个。

> **这些 EX 值已作废，不得作为结果引用**——从这些数字里得出的那个结论，也不
> 能在数字被撤下之后继续沿用，而这正是这条警告的第一版所放行的事。
> 它们是在此后被发现有误的指标定义下产出的——最重要的一点是，一次 solver 崩溃被算成了拒答，于是 `refusal_rate` 被抬高、EX 被压低，而且每个 arm 被影响的幅度还不一样。它们的运行产出物是被删掉的，没有重新分析。这张表留在这里，只是作为「这次运行当初想找什么」的记录。在修正后的定义下跑的下一次运行才是第一次可引用的运行，而且要做的对比是配对对比（`governed_bi.eval.analysis`），不是跨运行的点估计相减。

完整的 69 schema 运行**尚未**执行。为它记两条运维注记：

- **速率限制。** 线上策展撞上了组织在 `gpt-5.6-luna` 上 200K TPM 的上限。deep-agent curator 是优雅降级的（它把那个库的策展提前结束，而不是崩掉），但随后有一个库（`app_store`）就没拿到策展带来的抬升。完整运行需要给策展加限流 / 退避，否则它会静默地把一部分库策展得不够。
- 这里的 5 个库都很小。更大的库（训练问题更多）策展更贵，所以完整运行的预算要从一个更大的库去外推，不要从这几个。

## 切分、续跑与离线分析

`--split test`（默认）对留出集打分。`--split train` 对更大的训练集打分，但那些问题恰恰就是 curator 读过、用来构建 `curated` / `curated_sme` 的问题，所以它是**一个用更高统计功效来对比路由或 prompt 改动的诊断手段，永远不是留出集结果**。驱动会打印警告，并把 split 记进 `manifest.json`、`summary.json` 和每一个 generation 行。

服务阶段的续跑与上面的构建续跑是分开的。行在打分过程中就流式写入 `generations.<arm>.jsonl`，所以被打断的运行不会丢掉已做的工作。**要把原来的范围参数原样重复一遍**——漏掉一个意味着取 CLI 默认值，而不是「保持原样」，而这种偏移会在花钱之前被拒绝：

```bash
uv run python -m governed_bi.eval.run_datalake --resume-from runs/datalake/<timestamp> \
  --arms <原来的 arms> --dbs <原来的 dbs> --oracle <原来的 oracles> \
  --replicate <原来的 replicate> \
  --limit <原来的 limit，如果有> --limit-dbs <原来的 limit-dbs，如果有> \
  --build-workers 6 --workers 8
```

文件里已有的问题会被回放而不是重新服务，汇总是把回放的行和新打分的行放在一起算的，所以一次续跑过的运行与一次没被打断的运行打出来的分是一样的。守卫：跨不同 `--split` 续跑是致命错误（两个问题池不相交）；范围（`arms` / `dbs` / `oracle` / `replicate` / `limit` / `limit-dbs`）发生变化是致命错误；付费运行的 `git_sha` 偏移是致命错误，除非加 `--allow-git-sha-drift`（ledger 仍会把这次运行标为不可引用）；`manifest.json` 里记录的其他开关（模型、`route_top_k`、`llm_pick`、`schema_pick_max_columns`、embedder、`prompt_set_hash`）发生变化会按续跑契约告警或拒绝，因为已经打过分的行仍然带着旧配置。对 `generations.<arm>.jsonl` 的崩溃行改写是原子的（临时文件 + 替换）。

`stage_events.jsonl` 的续跑行为与行文件不同：被回放的问题不写任何阶段计时记录（它没有可报的），所以这个计时文件只会在当次尝试真正服务过的问题上增长，并且在任何续跑过至少一次的运行上，它都是 `generations.<arm>.jsonl` 的子集。

一次运行存在之后，`governed_bi.eval.analysis` 能报出这次运行本身报不了的东西——不花模型、数据库或 API 的钱：

```bash
uv run python -m governed_bi.eval.analysis runs/datalake/<timestamp>
```

- **表选择。** 把「schema 对了但失败」拆成「表选错」与「表对了但 SQL 错」，并用 `retrieved_tables` 这份 provenance，把选错的表归因到*检索*（从来没被提供）还是*选择*（提供了却没用）。这两者需要相反的修法。
- **配对显著性。** arm 之间的精确 McNemar 检验。服务侧的解码没有钉住，所以同一个 arm 的两次运行会在相当一部分问题上给出不同结果；跨未配对的运行去比点估计并不能替代它。
- **可评分 EX。** 把冻结的 `VALUES(...)` gold 以及对顺序敏感、被数据集排除的行（`order_sensitive_qids.json`）从分母里去掉之后的 EX——与实时汇总里同一条 `ex_gradeable` 规则。
- **Gold 排名分桶。** 按真实 schema 在短名单里的排名来分桶看 EX 和挑选准确率，把「短名单漏了」与「挑选器错了」分开。
