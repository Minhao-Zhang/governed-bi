# 执行清单

2026-07-30 grill 之后定下的工作队列。按依赖链排序，不按重要性。

每项只写三件事：**改什么**、**碰哪些文件**、**怎么验证**。理由不在这里，在 [rebuild-decisions.zh.md](rebuild-decisions.zh.md)。

**这份取代 [build-sequence.md](build-sequence.md)。**那份的四阶段结构（Phase 0-4，41 项）被推翻了：清单改成按横切概念组织，`run_experiment` 退役，3.17 降级，新增七项那份里没有的活。build-sequence 只保留作为证据索引，指向五份分析。

> 中文文档，你要求的。AGENTS.md 规定 `docs/plans/` 只写英文，这里偏离了，记一笔。

## ⚠ 2026-07-31 对抗审计的未修复项

六个敌意人格审计了这两份文档，**104 条指控，全部经过独立复核**：21 条整条杀掉（证据造假、范畴错误、或把计划自己写下来的告警当罪状），约 45 条降级，**38 条通过。「致命」一条都没存活** —— 复核标准是「按计划执行会产生错误结论或不可逆损失」。

复核还纠正了审计员自己：`ledger 525` 那个数被指「四种数法都重现不出」，实际大小写不敏感一个开关就复现了；四个被标为「核不动」的数字里三个可核且计划是对的（BM25 76% 实测 73.0%、两个 embed 耗时落在实测区间、截断实验 0.538/0.554/0.600 逐位复现），**只有 `corpus.by_id` 占热态 36% 是 4 倍高估（实测 8.9%）** —— 而那恰好是审计员标为「端点都对上了」的那一条。推论：任何审计员说「这里不必再核」都不成立。

已修复：**0.3**（回溯查询完成 + 攻击面限定是错的 + 两条漏掉的阻塞项）、**6.1**（S1，三个字段里两个已存在）、**6.2**（M2/M4，第一条阻塞已在 main 修好，从「修复」改成「验证」）、**6.3**（M3，三项全缺 + MDE 预登记）、**1.4 计数口径**（M5）、**阶段 1 表头**（四项→五项，删「互相独立」）。

下面是已核实且**尚未修复**的：

| # | 已核实的缺陷 | 状态 |
|---|---|---|
| ~~**A-1**~~ | **已处置（决定 22，2026-07-31）**：ARCH-2（拆 `build_serve_rails`）与 ARCH-7（把统计从 `run_datalake` 提出去）作为 **4.2 / 4.3** 加回清单，4.2 排在 5.3 那批 `agent.py` 改动之前；5.3 整块移出 A 的关键路径。原文如下 —— |
| **A-1** | **委托人的原始抱怨没有被回应。**`src/` 里有 **7 个**超过 1000 行的文件（`run_datalake` 5371 / `pipeline` 1658 / `agent` 1500 / `index` 1437 / `asset_bag` 1262 / `run_experiment` 1118 / `run_log` 1065）。清单只删掉 1 个（0.1）、改名 1 个（1.4.3），其余五个原地保留；而 5.3 与 X.5 还会往 `agent.py` 和 `run_datalake.py` 里**继续加东西**。原话是「几千行的大文件都多的要死」。**待决**：是否把 build-sequence 的 3.2（`ServeDeployment` + 模块级 rails 节点）与 3.8（把约 1300 行统计从 `run_datalake` 提出去）加回清单，并排在 5.3 那批 `agent.py` 改动**之前** |
| **A-2** | **build-sequence 的 41 项里约 28 项静默消失**，既没进清单也没进非目标。逐词 grep 命中数为 0 的：`RetryPolicy`、`ServeDeployment`、`get_stream_writer`、`on_event`、`_generated`、`verified_at`、`durability`、`circuit`、`EXPLAIN`、`sanitize_note_text`、`Connector.explain`、summariser。**所以决定记录里「没有未决项」这句话不成立。****待做**：对 41 项做一张 carried / dropped / retired 三态表，dropped 的每条写理由 |
| **A-3** | **1.4「四项互相独立」是错的。**1.4 的词表命中 90/92 个 `src/*.py`、95/97 个 `tests/*.py`、48/48 个 `docs/*.md` —— 阶段 2 到 5 的每一个条目改的文件都在它的命中集内。决定 7 否决 worktree 并行的理由是「十一项里只有四项真正互不相干」，实测是**零项**。**待决**：1.4 改成一条规则（「凡因别的条目动到某文件，顺手改掉该文件里的词」）写进 AGENTS.md，把 1.4.5（glossary 补 45 词，唯一真正独立的部分）拆成独立条目 |
| **A-4** | **1.4 永远不会「做完」。**它把落盘字段改名推给 4.1、跨 wire 改名推给 5.3，于是从阶段 1 结束到阶段 5 结束的**整个中间期，仓库按设计维持「同一概念两个名字」**—— 正是 1.4 存在的理由。`agent.py` 有六处 `"governance_ledger": ledger`，左边归 5.3、右边归 1.4。`tests/` 里 1591 处字符串字面量携带这些词，每处都要人工判定属于内部名/落盘名/wire 名三桶中的哪一桶，**没有机器检查能验证判定对不对**。**待决**：三批合成一个原子改名，或承认 1.4 只做 glossary 与 1.4.3 的文件重命名 |
| **A-5** | **golden 回归其实几乎免费，决定 3 的成本论证不成立。**X.5.4 的基线命令实跑 `--schema hockey` 是 **2.3 秒**，9 个数的全套不到一分钟，离线无模型无 Postgres；而 `tests/` 里已有 18 个文件用 `FakeToolModel` 驱动完整 governed turn。**待决**：在阶段 0 加一条，用 `FakeToolModel` 对 N 个固定问题录 provenance + `stage_events` 快照当 golden。成本一天，之后 55 个条目每一个都白拿归因 |
| **A-6** | **决定 16 两头占。**它用「网关规则不是 topology」给 P5 破例，同时对 P1/P3 采用了它刚否定的那套理由。而被删的 `/corpus/edit` 密钥门守的是 **corpus 写入** —— corpus 正是 L3 allowlist 与 refuse-gate 负例的策略源，且 dev 环境 `allow_edit` 默认 `True`。**待决**：要么 P1/P3 也做成应用层控制，要么在 ADR 0002 与 `docs/architecture.md` 里把主张改成「governance = topology **within the graph**；HTTP 边界由部署环境承担」。**现在两份文档一个字都没改，那就是文档在撒谎** |
| **A-7** | **总成本 235–435 工时（6–11 周单人全职）**，服务的终点是一次 1 小时 45 分的机器跑。55 个标题展开约 180 个离散工作单元，触及约 157 个文件。极简主义者提了一份 10 条的竞争方案，**声称达成同一终点**。**待决**：是否把顺序倒过来 —— 先做 6.1 + 6.2 + 6.3（两天加一次跑）拿到第一份可引用数字，再决定 B 里哪些还值得做 |

四个悬空编号（`1.7` / `1.8` / `3.12` / `3.17`）仍未修，它们的唯一定义在 `build-sequence.md` 与 `corpus-drift.md` 里（那两份文档的头注写明了要搬什么）。

### 审计的中等项（M 系列），尚未逐条并入正文

复核通过 38 条，上表的 A 系列是其中最重的。剩下约 21 条中等项还在
`scratchpad/audit-final.md`，逐条并入是独立一笔工作。已经处置的两条：

- **M1（`runs/` 是唯一副本，72 MB）—— 已解除。**服务器上有备份，本地可以直接删。**但留一条依赖**：X.3（分析工具）要靠 20260730 那份数据开发并重现报告里的数字，而决定 9 把它排在第一个 —— 所以要么先做 X.3 再删，要么删了之后从服务器拉回来。4.1 的一次性迁移脚本同理。
- **M18（九份 plan 文档全部 untracked）—— 仍然成立**，包括本文件。这个仓库四个 commit 前刚发生过一次误删加恢复。

值得单独点出的三条尚未并入的：

- **M6**：X.4 现在的处方（「缺失键一律判不可比」）会打坏一条刻意设计并写明理由的规则，而且**按字面无法实现** —— 通过 `dict.get()`，缺键与 `None` 值在结构上不可区分。`eval/index.py:941-951` 已经在任一侧 `manifest_schema_version is None` 时判 REFUSAL。应改成「加一个测试：`MANIFEST_KNOBS` 名字集合变了而 `MANIFEST_SCHEMA_VERSION` 未 bump 则失败」。
- **M7**：0.2 的文件清单不全 —— `skip_agent` 在 `src/` 里 **42 处、6 个文件**（含 `eval/index.py` 16 处、`eval/metrics.py` 6 处），不是只有 argparse 加 resume 检查。而 `tests/test_eval_index.py:516` 是集合等式，删的那一刻就 AssertionError —— 所以 0.2 的「现有测试仍绿」是一道真闸门。
- **M8**：2.3 第 1 点**整条已过期**。TOML 键**不是死的**（`config.py:659-668` 把 `[routing]` 三个键读进 `knob_overrides`，`governed_bi.local.toml:88` 正在用 `top_k = 10`，而 `config.py:652-658` 的注释记录这问题**已被修好**）；不同取值只有两个（3 / 10）不是三个。第 2、3 点（`Gateway(max_rows, timeout_s)` 无 Settings 字段、缺 `--model`）三路复核无反例，保留。

已修复的：**0.3**（回溯查询已完成，攻击面限定是错的）、**6.1**（S1）、**6.2**（M2/M4）、**6.3**（M3）、**1.4 计数口径**（M5）、**阶段 1 表头**、**A-1**（决定 22：4.2/4.3 加回，5.3 移出关键路径）。

---

## 阶段 0 · 先删

先删是因为后面十一项横切修改，每一项都会碰到这些代码。删掉就少改十一遍。

### 0.1 · `run_experiment.py` 退役

**改什么**　删掉单 db driver。它唯一独有的能力是「只跑一个 db」，而 `run_datalake --dbs <db> --limit N` 完全覆盖，还多给 `stage_events.jsonl`、serve 断点续跑和真实退出码（它自己永远返回 0，哪怕台账判定不合格）。

**碰哪些文件**　`src/governed_bi/eval/run_experiment.py`（1118 行）；`tests/` 里针对它的测试；`docs/plans/experiment-runbook.md`、`docs/plans/eval-rebuild.md`、`README.md` 里的命令引用；`docs/open-work.md` 的 E4（`--resume-curated` 无条件重建 baseline）随之关闭。

**怎么验证**　`grep -rn "run_experiment" src/ tests/ docs/ README*.md` 只剩历史记录；`run_datalake --dbs beer_factory --limit 5` 跑通并产出 `stage_events.jsonl`。

### 0.2 · 删掉 `--skip-agent` 与 drift 双轨

**改什么**　真跑一定用 agent、一定花钱；测试用便宜模型，不用「不用模型」。所以 `--skip-agent`、`--allow-git-sha-drift`，以及 `_check_resume_manifest` 里「付费跑致命 / smoke 跑只警告」那套双轨判断，全部删掉。resume 的一致性检查保留（它防的是两套配置混进一份 artifact，不是防手滑）。

**碰哪些文件**　`eval/run_datalake.py` 的 argparse（约 4942 行起）与 `_check_resume_manifest`（约 1145-1274 行）；`docs/plans/experiment-runbook.md` 里所有 `--skip-agent` 的示例命令。

**怎么验证**　`grep -rn "skip_agent\|skip-agent\|git_sha_drift" src/` 无结果；resume 一致性检查的现有测试仍绿。

**注意**　`--skip-agent --oracle oracle_sql` 现在被用作「grader 上限自检」，它确实不花钱。删之前确认这条自检有替代路径，或把它单独保留成一个独立命令而不是一个全局 flag。

### 0.3 · graded delivery 收紧（五份分析里严重度最高的一条）

不属于「先删」，但和 0.1 / 0.2 一样必须最先做 —— 它的第一步不改任何代码，而且可能揭示我们已经执行过越权 SQL。

> **2026-07-31 更正：第 1 步已完成，结论是「发生过」，而且比原先的预判严重。**四个独立审计人格各自跑了这个查询并给出同一答案，我自己复核确认。原先写的第 1 步**按字面执行不了**（两个字段都不是我说的那样），而原先给出的攻击面限定**是错的**。

**已知事实（20260730 那次跑，5404 行 generation，已核）**

`graded_delivery=True` 恰好 **1 行**：`curated` 臂 / `train_5163`。同一行 `routing_escaped=True`。

```
routed_schemas = ['regional_sales']
tables_used    = ['tbl_address_country', 'tbl_address_zip_data']   ← 完全不同的 schema
by_guardrail_layer = {syntax:0, policy_blacklist:0,
                      ast_column_allowlist:0,      ← L3 一次都没拦
                      term_semantics:1,            ← 只有 L4 拦了，然后被豁免
                      cost_estimate:0}
generated_sql  = SELECT (CAST(SUM("T1"."population_2020") AS REAL) - ...) FROM "address"."zip_data" ...
correct        = True                              ← 而且它计入了 EX
```

**三条更正：**

1. **越权是 100% 的，不是部分的。**授权的是 `regional_sales`，实际读的是 `address` schema 的两张表 —— 一张都不在授权集里。
2. **「攻击面限于不含列引用的 SQL」这句是错的，删掉。**那条 SQL 满是列引用（`T1.population_2020` 等），L3 **一次都没触发**。原因是 `gateway/guardrails.py:82-106` 的 `column_allowlist(corpus)` 遍历**整个** corpus 建 allowlist，而 `analyst/agent.py:434` 传的是池化后的 corpus —— **pooled 配置下 L3 是一张 57-schema 的通行证**。所以表级唯一的守门人就是 L4，而 graded delivery 把它整层跳过。攻击面是**任意可执行 SQL**。
3. **原先写的 join 做不了，但另一个 join 可以。**`base_provenance` 不是 generation row 上的键（它是 `governance.py:789` 的形参名）；`governance_ledger` 也不落盘（`eval/arms.py:476-480` 把它折成 `ledger_len`，清单 5.1 自己写着这件事）。可跑的判据是 `routing_escaped`，driver 在跑时就算好并落盘了（`eval/run_datalake.py:3765-3780` 用 `_schema_of_assets(tables_used)` 对 `routed_schemas` 比）。**复现命令：对 `generations.*.jsonl` 过滤 `routing_escaped=True`。**

**改什么**　第 1 步已完成，剩下三步：

1. **写 `tests/test_graded_delivery_scope.py`。**用 `FakeToolModel` 把一轮驱动到 `coverage_best_effort`，最终 SQL 触及未授权 schema 的表（**带列引用**，照 `train_5163` 的形状），断言结果是拒答而不是交付。
2. **改：**`governance.py:700` 的复检传 `allowed_tables`，并把 `governance.py:115-119` 里 `term_semantics` 的豁免限定为「不含未授权基表」。
3. **新增：评估 L3 的 allowlist 是否也该收窄到 routed schema。**收紧 L4 之后，L3 仍然是一张 57-schema 的通行证 —— 这条是上面第 2 点暴露出来的独立缺陷，不在原计划里。

**另外两条阻塞项（原计划漏了）**

- **`governance_ledger` 不落盘**，所以收紧之后**无法事后验证 L4 有没有生效**。这条要从清单 5.1 的一条待办升级成 0.3 的前置。
- `guardrails.py:775` 那句无条件的 fail-closed 承诺、以及 `column_allowlist` 的 docstring，都要写死这条不对称。

加紧迫性的一条仍然成立：**pooled eval 把 `grade_semantic_failures` 打开了**（`config.py:253` 默认 `False`，`run_datalake.py:4166` 设 `True`），我们跑得最多的那个臂正是走这条路 —— 而那唯一一次越权交付，`correct=True`，静默计入了 EX。

**碰哪些文件**　`analyst/governance.py:115-119/695-716`、`gateway/guardrails.py:775/918`（docstring 同步）、新增 `tests/test_graded_delivery_scope.py`。

**怎么验证**　回溯查询有明确结论（有或没有，都要写下来）；测试在修复前红、修复后绿；`guardrails.py:775` 那句无条件的 fail-closed 承诺与代码一致。

---

## 阶段 1 · 横切收敛，第一梯队

**五项，不是四项；而且「互相独立」是错的。**审计实测：1.4 的词表命中 **89/92 个 `src/*.py`、93/97 个 `tests/*.py`、48/48 个 `docs/*.md`**，阶段 2 到 5 每一个条目改的文件都在它的命中集内 —— **互相独立的实测数是零项**。决定 7 当初否决 worktree 并行的理由写的是「十一项里只有四项真正互不相干」，那个数也是错的。

按顶部 A-3 的处置：**1.4 不作为一个可排期的条目存在**，降级成一条写进 `AGENTS.md` 的规则（「凡因别的条目动到某个文件，顺手把该文件里 1.4 表格中的词改掉」），只把 **1.4.5（glossary 补词，全表唯一真正不碰代码的部分）** 和 **1.4.3（七处文件与类重命名）** 拆成独立条目。1.1 / 1.2 / 1.3 / 1.5 四项仍可随便挑一个开始。

### 1.1 · b5 小函数三处重复

**改什么**　三个名字各在三个文件里定义了三遍：`_FROZEN_GOLD_RE`、`_slug`、`_render`。各收敛成一份。

**碰哪些文件**
- `_FROZEN_GOLD_RE`：`eval/analysis.py:50`、`eval/run_datalake.py:196`、`eval/sql_diff.py:195`
- `_slug`：`curator/asset_bag.py:38`、`curator/profile.py:31`、`curator/seed.py:21`
- `_render`：`analyst/context.py:363`、`analyst/governance.py:259`、`eval/sql_diff.py:446`（这三个未必是同一件事，先读再决定合不合）

**怎么验证**　每个名字在 `src/` 里只有一处定义。

### 1.2 · b4 裸表名查找三份拷贝

**改什么**　「先按 id 查，查不到按物理名查」这段逻辑有三份拷贝，都取 `corpus.assets` 里的第一个匹配。`retrieval/rvgd.py:530-538` 已经有正确实现（歧义时返回 `None`，注释里写明了理由），另外三处没用它。收敛成一个 `Corpus.table_by_name`，接受限定名（`schema.table`），裸名歧义时返回 `None`。

这不只是重复：BIRD-corpus 上实测 **731 个表资产里有 67 个裸名歧义（9.2%）**，`pais` 五个、`kunden` 四个。命中时 agent 会收到「`tbl_beer_factory_kunden`: not licensed this turn」，泄露一个它从没提过、且在其路由范围之外的表名，还可能死循环到步数上限，最后记成 agent 失败。

**碰哪些文件**　`analyst/tools.py:38`、`analyst/middleware.py:118`、`analyst/agent.py:465`、`retrieval/rvgd.py:530-538`。同时需要一个 `Corpus.concat` 构造器，否则 pooled 路径上索引会过期。

**怎么验证**　构造一个含歧义裸名的 corpus，四个调用点都返回 `None` 而不是第一个匹配；限定名 `schema.table` 四处都能解析。关掉 `docs/open-work.md` 的 C13。

### 1.3 · b6 CLI 骨架

**改什么**　六个 `main()`，零个共享骨架，`pyproject.toml` 里没有 `[project.scripts]`，全靠 `python -m governed_bi.eval.run_datalake ...` 这种长命令。加 `[project.scripts]`，给每个入口一个短名字，共享一份 argparse 骨架（`--verbose`、`--out` 这类通用参数）。

服务器上跑的时候，命令的长度和一致性比在本地重要得多。

**碰哪些文件**　`pyproject.toml`；`corpus/cli.py`、`eval/run_datalake.py`、`eval/index.py`、`eval/analysis.py`、`eval/retrieval_eval.py`；`scripts/` 三个脚本。

**怎么验证**　`uv run gbi-eval --help` 之类的短命令可用；文档里的长命令全部替换。

### 1.4 · b7 术语审计（不落盘、不跨 wire 的部分）

一次完整的术语审计，起点是 `ledger`，最后发现的远不止它。**落盘字段名的改名并进 4.1**（定义 `GenerationRow` 时本来就要把 72 个字段过一遍，分两次做等于改两遍）；**跨 wire 的字段改名并进 5.3**（那时候正在定义契约）。这里只留模块名、类名、函数名、内部变量、glossary。

> **2026-07-31 审计更正（M5）：下面的计数原先不在同一个基准上，三个数是错的。**
>
> **口径写死为这一条命令**，全表按它重跑（裸词、词界、大小写不敏感、src + tests + tracked docs）：
>
> ```bash
> rg -o -w -i '<word>' $(git ls-files 'src/**' 'tests/**' 'docs/**') | wc -l
> ```
>
> 数**裸词**是术语审计的正确口径（`governance_ledger` 与 `ledger` 不是同一个词，下划线是 word char 所以复合形式被正确排除）。在这个口径下 `ledger 527`（写 525）、`rung 319`、`verdict 378`（写 376）、`db_id 351`（写 347）、`licensed` / `solver` / `quotable` / `headline` 逐位相同。
>
> **必须重算的三个：`pooled 405 → 205`（差 2 倍）、`tier 349 → 262`、`harness 151 → 180`。**七个带 `~` 的词全部偏 1.5–4.6 倍，最差是 `run ~330 → 1488`。`kind 147` / `layer ~115` / `stage ~310` 是 src-only 基准，混用了。
>
> 连带影响：1.4.5 那句「按出现次数排前几个」的有序清单要重排（`pooled` 从第 1 名掉到第 9 名左右）；顶部 A-7 的「235–435 工时」估价建在这批数上，重算后要重估。**但这不影响执行顺序** —— 清单开头就写着「按依赖链排序，不按重要性」，决定 7 还专门否决了「影响面从大到小」。

`ledger` 的实际规模：**527 次**（src 233 / tests 193 / docs 101），**四个**互不相关的意思，`docs/glossary.md` 里 **0 次**。

> 一条更正：**`KMB` 在两个仓库里都是 0 命中。**那是描述 Steiner 树算法时从外部带进来的词，不是仓库术语。`docs/plans/grill-agenda.zh.md` 的 T8.Q4 用了它，读到时按「`graph/planner.py` 里那个 Steiner planner」理解。

#### 1.4.1 · 一词多义，拆开

| 词 | 几个意思 | 出现 | 拆法 |
|---|---|---|---|
| `ledger` | 4 | 525 | (a) 每轮 guardrail 判决列表 → `guardrail_log`；(b) curator 的 clarification 来源（`ledger_source`）→ `clarification_origin`；(c) `ledger_ok` → **直接删**，`eval/index.py:591` 已经有个值完全相同的 `hygiene_ok`；(d) `eval/index.py` 模块自称 "A ledger of runs" → 见 1.4.3 |
| `stamp` | 4 | 210 | 只保留「可靠性 stamp」（glossary 里唯一定义过的那个）。`_ledger_stamp` → `_entry_timing`（它返回 `{duration_ms, ts}`）；eval 的 `*stamped*` 系列 → `*recorded*` / `*labelled*` |
| `verdict` | 3 | 376 | `check()` 的结果对象保留 `verdict`；ledger 条目里的字符串（`pass`/`block`/`error`/`cap`/`deny`）→ `status`；curator 的每列可靠性判断 → `reliability_call` |
| `run` | 3 | ~330 | `run_id` 是**每轮**一个，而 `runs/` 是**每次实验**一个，两者并列存在。给 per-turn 那个改名或至少在 glossary 里写死区别；`analyst/run_log.py` 记的是 turn，键是 `turn_id`，名字要跟上 |
| `index` | 3 | ~90 | 只留「检索索引」。`eval/index.py` → 见 1.4.3；序数用 `*_position` |
| `scope` | 5 | ~180 | note 附着范围 / L4 授权表集 / 图视窗 / 工具可调用范围 / 一次跑评了哪些题。至少拆前三个 |
| `pin` | 3 | ~93 | `corpus_pin`（服务哪个 schema 子树）/ `pin_triggers`（把 note 顶进 prompt）/ `pinned_schemas`（路由必留）。第二个的 `PIN` 全大写却从未展开 |
| `fold` | 3 | 124 | SME 折叠 / token 归一化 / 已合并的臂。在 eval 语境里第一直觉是交叉验证的 fold —— 而**没有一处是那个意思** |
| `layer` | 2 | ~115 | guardrail 层 L1-L5 / 语义层（corpus） |
| `harness` | 5 | 151 | serve 运行时 / build 运行时 / eval 打分器 / 共享 helper 模块 / retrieval-eval / 假模型测试架。见 1.4.3 |
| `kind` | 8 | 147 | note 类别 / trigger 类型 / metric 类型 / 图节点类型 / 事件类型 / 日志后端 / checkpointer 后端 / connector 方言 |
| `promote` | 2 | 23 | glossary 里是「把发现的模式提炼成认证数据集」，代码里是「把构建产物从 staging 目录搬出来」。正面冲突 |
| `stage` | 2 | ~310 | 流水线位置枚举 / 临时构建目录（`_stage_roots`、`staging_root`）。后者改成 `staging_*` |
| `tier` | 3 | 349 | **归 5.3**（跨 wire） |
| `step` | 4 | ~290 | **归 5.3 与 b2**（跨 wire） |

#### 1.4.2 · 多词一义，收敛

| 概念 | 现在几个词 | 合计出现 | 收敛到 |
|---|---|---|---|
| 实验里被打分的语料变体 | `arm` / `rung` / `treatment` / `condition` | **1793** | `arm`。注意 `eval/arms.py:12-45` **一段 docstring 里四个都用了** |
| SQL 命名空间 | `schema` / `db_id` / `corpus_pin` / `namespace` / `schema_name` | ~2400 | `schema`。**落盘部分归 4.1，wire 部分归 5.3** |
| lake 身份（和上一行**不是**一回事） | `db` / `db_name` / `lake` | ~290 | `lake_id`。`config.py:126-132` 现在不得不在行内写 `(≠ corpus_pin)` 来自救 |
| 拒答 / 拦截 | `refuse` / `block` / `reject` / `deny` / `veto` / `cap` | ~430 | `refuse`（轮级）+ `block`（guardrail 级）。`middleware.py:315` 和 `:393` 对同一种形状分别用了 `deny` 和 `block`，而 `_LEDGER_STATUS` 又把两者都映射成 `blocked` |
| 不可信的列 | `decoy` / `trap` / `suspect` / `caveat` / `unreliable` / `flagged` | ~430 | `decoy`（数据集埋的）+ `caveat`（curator 推断的）；`suspect` 只留作枚举值；**`trap` 全删**（`decoy` 的纯同义词，glossary 已经用 `decoy`） |
| 收窄候选 | `shortlist` / `pick` / `route` / `select` / `candidate` | ~290 | `shortlist`（多→少）+ `pick`（少→一）；`route` / `select` 不再当动词 |
| 跑实验的东西 | `driver` / `harness` / `solver` / `grader` / `scorer` | ~320 | `driver` + `solver` + `grader`；**删 `scorer`** |
| 内容标识 | `*_hash` / `digest` / `sha` / `fingerprint` | ~200 | `*_hash`；`git_sha` → `git_commit`，`fingerprint_arm` → `arm_hash` |

#### 1.4.3 · 名不副实（这些是代码自己承认的）

| 现在 | 改成 | 它自己的文档说 |
|---|---|---|
| `eval/index.py` | `eval/run_registry.py`（`DEFAULT_INDEX` → `RUN_REGISTRY_PATH`，`load_index` → `load_registry`，`index_run` → `register_run`） | 第一行：「A **ledger** of runs, and a rule for which two of them may be compared.」 |
| `eval/harness.py` | `eval/driver_support.py` | 第一行：「Shared **helpers** for the eval drivers.」 |
| `RetrievalIndexCache` | `RetrievalIndexMemo` | docstring 自称 "memo"、"Unbounded on purpose" —— 无淘汰、无 TTL、不持久化 |
| `Stage.route` / 节点 `ingest` | `Stage.turn_start` / 节点 `turn_start` | 函数体是 `pass  # term binding is the agent's job now`（`agent.py:565-566`），而真正的路由叫 `schema_route` |
| `AssetBag.read_corpus()` | `render_bag()` | 从不读 `corpus/`，是把内存里的 bag 渲染成 ≤20k 字符给模型（`asset_bag.py:358-366`） |
| `retrieval/rvgd.py` | `retrieval/corpus_search.py` | `RVGD` 从未在任何一份文档里展开，而且**两个文件对同一个字母的定义相互矛盾**：`retrieval/rvgd.py:5` 说 BM25 是「'V'/lexical 通道」，`retrieval/embedding.py:1` 说「V (vector / semantic) 通道」 |
| `graded_delivery` | `degraded_delivery` | `grade`/`grader`/`gradeable`/`hash_grade` 在 src 里 219 次全指「对着 gold 打分」，而这里的 graded 是 de**graded**。全仓库最糟的同音词。**落盘字段部分归 4.1** |

另外三个不改名但要在 glossary 里写清楚的陷阱：`safety_clearance=False` **不等于**「不安全」（只过了 L1-L3、栽在语义层的 SQL 也是 `False`，`answer.py:236`）；`semantic_assurance=unflagged` **不等于**「已验证正确」（glossary 自己已经在否定它）；`UncertaintySignals.corrective_rag` 是 `reserved: unused; no writer in src/`。

#### 1.4.4 · 命名风格统一

| 角色 | 现状 | 统一到 |
|---|---|---|
| 计数 | `n_*` 954 次 / 182 个名字，`*_count` 96 次 / 10 个名字，`num_*` **0** | `n_*` |
| 行数 | `row_count` 67 / `n_rows` 12 / `nrows` 9 / `gold_nrows` 11 / `pred_nrows` 9 —— **四种拼法**，而行集比对正是 headline 指标 | `n_rows`。**落盘部分归 4.1** |
| 构造函数 | `build_*` 19 / `make_*` 8 / `create_*` 1 | `build_*` |
| 布尔 | `is_*` / `has_*` / `can_*` / `allow_*` / `*_enabled` / `need_*` / `require_*` / `must_*` / `skip_*` / **`*_ok` 12 个** | `is_*` 内部、`can_*` 上 wire；`*_ok` 那一族是项目自创的，收掉 |
| 时长 | `_ms` / `_s` / `_sec` | `_ms` 表实测、`_s` 表配置超时；**删 `_sec`** |
| 分类后缀 | `kind` 147 / `*_type`（`asset_type` 82 等）/ `mode` 46 / `role` 70 | `kind`。`asset_type` → `kind` **归 5.3**（跨 wire，且 `presenter.py:591` 已经在做 `kind=asset.asset_type` 的桥接） |
| 英式 / 美式混写 | `normalised`/`normalized`、`recognised`/`recognized`、`must_honour`/`finalize`/`tokenize` | 美式（跟 Python 标准库一致）。`must_honour` 是唯一进了公开 API 的（12 src + 20 docs），但它是 **corpus YAML 字段**，归 4.1 一起处理 |

#### 1.4.5 · glossary 补全

`docs/glossary.md` 现在 40 个词条。**45 个高频承重术语一个都不在里面**，按出现次数排前几个：`pooled`(405)、`verdict`(376)、`db_id`(347)、`resume`(311)、`suspect`(298)、`outcome`(275)、`budget`(272，而且是四种不同的 cap)、`driver`(198)、`licensed`(195)、`solver`(187)、`quotable`(179)、`twin`(160)、`headline`(144)、`crashed`(133)。

而 `arm`(1278) 和 `rung`(319) 在 glossary 里只出现在**「已退役词汇」那一段**，作为整个实验设计的核心单位却没有独立定义。

改完名之后补 glossary，一次补齐。英文为准，中文孪生按现有规矩同步（glossary 在那九份需要孪生的清单里）。

**碰哪些文件**　`src/` 全域；`tests/`（193 处 `ledger`）；`docs/` 23 个文件；`docs/glossary.md` 与 `docs/glossary.zh.md`。

**怎么验证**　每个被拆的词，`grep -rn` 之后只剩一个含义；`docs/glossary.md` 里 45 个新词条都在；`pytest` 全绿。

### 1.5 · 锁死线协议依赖的版本范围

**改什么**　主通道的线协议**由传递依赖拥有**。`uv.lock` 锁的是 `langgraph-api 0.11.0` / `langgraph-sdk 0.4.2` / `langgraph 1.2.8`，而 `pyproject.toml` 的约束是 `langgraph>=1.0`、`langgraph-cli[inmem]>=0.2` —— 一次 `uv sync -U` 就能换掉 `/threads`、`/runs/stream`、`stream_subgraphs` 的行为，而本仓 diff 里什么都看不到。给这三个包加上界，并在契约文档里发布「本仓声明兼容的 SDK 版本范围」。任何只管 `openapi info.version` 的版本化策略都管不到这条。

**碰哪些文件**　`pyproject.toml`、`uv.lock`、契约文档一节。

**怎么验证**　`uv sync -U` 之后 `uv.lock` 里三个包仍在声明范围内；一个测试解析 `pyproject.toml` 与契约文档里的范围并比对。

---

## 阶段 2 · 横切收敛，第二梯队

这三项决定 `GenerationRow` 的字段长什么样，所以必须排在 a1 前面。

### 2.1 · b2 一个步骤三套名字

**改什么**　graph 节点名、实时 wire 名、持久 `Stage` 是三套字符串空间。`"schema_route"` 和 `Stage.schema_pick` 是同一步的两个名字 —— 而这一步正是我们在测的那一步。收敛成一个 `Step` 值，同时携带节点名、wire 名、`Stage` 和工具绑定。

**碰哪些文件**　`src/governed_bi/stages.py`（261 行）、`analyst/agent.py`（emit 点）、`analyst/governance.py`（`GovEventStream`）、`eval/run_datalake.py`（写 `stage_events.jsonl`）、前端 `lib/steps.ts`。

**怎么验证**　一个测试断言：每个 graph 节点都能映射到唯一的 `Stage` 和唯一的 wire 名，反向亦然。`stage_events.jsonl` 里的 `stage` 值集合与 `Stage` 枚举完全一致。

### 2.2 · b3 `governance.excluded` 与层级严重度

**改什么**　两件事合并处理。一是 `governance.excluded` 这个判断在九个文件里有五种写法，收敛成一个谓词。二是层级严重度分居两个消费模块、用了两种类型：`middleware.py:44` 的 `_HARD = {GuardrailLayer.policy_blacklist}`（枚举成员）和 `governance.py:115` 的 `_GRADED_DELIVERY_LAYERS`（字符串值）。五层里有两层（`syntax`、`ast_column_allowlist`）在两个集合里都不出现，处置方式只存在于控制流里。把严重度放进 verdict 本身，由产出判决的模块决定。

**碰哪些文件**　`gateway/guardrails.py`（`GuardrailLayer` 与 `check()`）、`analyst/middleware.py:44`、`analyst/governance.py:115-119`、九个含 excluded 写法的文件。

**怎么验证**　新增第六层时，不给它声明处置方式会导致测试失败（而不是静默继承 fall-through）。

### 2.3 · b1 config knob 收敛

**改什么**　三件事：

1. 同一个 knob 多处声明。`schema_route_top_k` 在四个地方声明了三个不同的默认值；两个 TOML 键是死的，因为 argparse 的默认值永远赢。改成一处声明。
2. `Gateway(max_rows, timeout_s)` 根本没有 `Settings` 字段。serve 是 1000 行 / 30 秒，pooled eval 是 200000 行 / 60 秒 —— **200 倍的差异，不在任何 manifest 里**。加进 `Settings`，让它进 manifest。
3. **加 `--model` 参数。**现在两个 driver 都没有，模型只能从 `governed_bi.toml` 的 `[models].llm_model` 读。而你的工作方式是「测试用便宜的、正式用好的」，所以现在每次切换都要改 TOML —— 忘了改回来不会报错，只会在事后的 manifest 里留下一个你没注意的字段。

**碰哪些文件**　`src/governed_bi/config.py`（794 行）、`governed_bi.toml`、`eval/run_datalake.py` 的 argparse、`gateway/factory.py`。

**怎么验证**　一个测试遍历所有 knob，断言每个只有一处声明；`grep` 确认没有 TOML 键被 argparse 默认值遮蔽；manifest 里出现 `max_rows`、`timeout_s`、`model`。

### 2.4 · 「改语义不改形状」的开关清单

排在 2.3 之后，它消费 2.3 的 knob 收敛结果。

**改什么**　`grade_semantic_failures`（`config.py:253`，默认 `False`，可被 `[runtime]` 覆盖）一旦打开，原本 `sql=null` 的硬拒答会变成带 SQL、带结果、`tier=fenced_raw` 的交付答案 —— **wire 形状零变化**，所以 openapi 的 `--check`、字节漂移、版本 bump 全都不会红。这一整类开关现在没人清点。逐个列出来，标注「打开后 wire 上什么语义变了」，并全部纳入 `serve_config_hash`（`provenance.py:84-102`，`grade_semantic_failures` 已在里面）。这份清单是 5.3.9 第 3 条要文档化的对象。

**碰哪些文件**　`config.py`、`provenance.py:67-107`、契约文档一节。

**怎么验证**　一个测试遍历这份清单，断言每个开关都在 `serve_config_hash` 的输入里；翻转任一开关，`serve_config_hash` 变。

---

## 阶段 3 · 身份

### 3.1 · a3 `RunContext`

**改什么**　`run_id` / `turn_id` / `corpus_pin` 三个字段已经存在，但**没有一个进入 Langfuse 或 LangSmith 的 trace**。十三个 sink 没有共享 key，所以服务器上跑完之后，trace 和 `stage_events.jsonl` 拼不回去。

做两件事：一个 `RunContext` 记录承载这三个字段（外加 `arm`、`schema`、`prompt_set_hash`、`identity`），一个 `tracing_config(ctx)` 函数产出同时喂给两个 tracer 的 metadata（LangSmith 读 `metadata` 与 `tags`，Langfuse 读 `langfuse_session_id` / `langfuse_user_id` / `langfuse_tags`）。

同时补 `configure_logging()`：`src/` 里没有任何 `logging.basicConfig`，入口也没有，所以 **30 个 `logger.` 调用全是死的**，而 105 个必须被看见的诊断都写成了 `print()`。加一个 ContextVar filter，把 `run_id` / `turn_id` 注入每条日志记录，不用改任何函数签名。

**碰哪些文件**　新增 `src/governed_bi/logging_setup.py`；`src/governed_bi/obs.py`（`CallbackHandler()` 现在不带任何参数）；八个调用点：`analyst/agent.py:1478`、`api/graph_app.py:174`、`eval/arms.py:436`、`eval/oracle.py:362`、`eval/refuse_gate.py:71`、`curator/pipeline.py`、`curator/sme.py`、`scripts/live_smoke.py`。

**怎么验证**　跑一次 5 题的小跑，用 `run_id` 能同时在 Langfuse trace、`stage_events.jsonl`、日志文件里查到同一批记录。

---

## 阶段 4 · 记录类型与大文件拆分

4.2 与 4.3 是 2026-07-31 加回来的（决定 22）。它们原是 `build-sequence.md` 的 ARCH-2 与 ARCH-7，在重构清单时丢了 —— 而它们是**全案唯一真正回应「几千行的大文件都多的要死」的两条**。**4.2 必须排在 5.3 那批 `agent.py` 改动之前。**

### 4.1 · a1 `GenerationRow`

**改什么**　那个 70 键（实际落盘 72 字段）的无类型 dict：两个生产者，12 个模块里 205 处 `.get()` 读取。任何一处键名拼错都是静默 `None`。改成一个真的记录类型，`eval/metrics.py` 的 `ROW_*` 注册表溶解进去。

必须排在阶段 2 和 3 之后：它的 `stage` 字段等 b2、`failed_layer` 字段等 b3、`corpus_pin` 与 `prompt_set_hash` 等 b1、`run_id` 与 `turn_id` 等 a3。

**顺带做完落盘字段的改名（从 1.4 转过来）。**定义记录类型的时候本来就要把 72 个字段过一遍，这时候改名是同一次工作；分两次做等于改两遍。要改的：

| 现在 | 改成 | 在哪个落盘物 |
|---|---|---|
| `graded_delivery` | `degraded_delivery` | generations 行 |
| `ledger_len` | `guardrail_log_len` | generations 行 |
| `ledger_ok` / `not_ledger_ok_because` | **删掉** —— `eval/index.py:591` 已有值完全相同的 `hygiene_ok` | `runs/index.jsonl` |
| `db_id` | `schema` | generations 行 + manifest |
| `rung` | `arm` | generations 行 + summary |
| `nrows` / `gold_nrows` / `pred_nrows` / `row_count` | `n_rows` / `n_rows_gold` / `n_rows_pred` | generations 行 |
| `n_twin_unstamped` | `n_twin_unlabelled` | `summary.json` |
| `must_honour` | `must_honor` | **corpus YAML** —— 影响 `BIRD-corpus` 姊妹仓库和 run dir 里约 48 MB 语料 |
| `*_sec` | `*_s` | generations 行 |

`tier` 与 `asset_type` 不在这里，它们跨 wire，归 5.3。

**同时写旧字段兼容读取层。**一个旧名 → 新名的映射表，读取 artifact 时自动翻译。理由是 X.3（分析工具）要靠 20260730 那份数据开发并重现报告里的数字，改了字段名就读不了。映射表里每一条都写上「什么时候可以删」（比如「20260730 那批数据退役后」），否则它会永远留着。

`must_honour` 那条要单独处理：corpus YAML 的读取层要同时接受两种拼法，`BIRD-corpus` 仓库另行迁移。

**碰哪些文件**　`eval/metrics.py`（814 行，`ROW_*` 注册表）、`eval/run_datalake.py`、`eval/arms.py`、`eval/oracle.py`、`eval/analysis.py`、`eval/error_taxonomy.py`、`eval/index.py`，加上另外五个读它的模块；`corpus/schemas.py` 与 `corpus/loader.py`（`must_honour`）。

**怎么验证**　`grep -c "\.get(" ` 在这十二个模块里显著下降；拼错字段名变成类型错误而不是 `None`；**用 20260730 那份 `generations.*.jsonl` 反序列化成新类型，字段一个不丢，且 X.3 的分析工具能跑出和报告一致的数字**。

> **改用一次性迁移脚本，不做常驻映射表（审计计划层面第 8 点）。**需要兼容的旧数据总共**一个** run dir（4 个 `generations.*.jsonl` 约 27 MB + `summary.json` 1.6 MB + `stage_events.jsonl` 7.9 MB），一次性转换是分钟级。决定 14 原先选的是常驻映射表加「每条注明什么时候可以删」—— 而它自己就写着「否则它会永远留着」。
>
> 写一个 `migrate_generations.py`，对那一个 run dir 跑一次，然后**把脚本删掉**。必须与 X.3 一起做：单独执行会让 X.3 失去开发用的数据。
>
> 「不做兜底」这条原则管不到这里：决定 12 删的三样都是**拦操作员手滑的闸门**，而一层旧字段名到新字段名的数据读取翻译不是同一类东西。把「不给操作员建护栏」扩张成「不许有任何向后兼容」是范畴错误。

### 4.2 · 拆 `build_serve_rails`（原 ARCH-2，2026-07-31 加回）

**必须排在 5.3 那批 `agent.py` 改动之前。**

**改什么**　`build_serve_rails` 是 `analyst/agent.py:391-1424` 的**单个 1034 行函数，内含 14 个嵌套 def**。`build-sequence.md:206` 把它评为 **Strong** 并逐字写「1,032 lines with 13 unaddressable closures」。它的具体症状：

- **13 个闭包无法被寻址** —— 外部代码引用不到任何一个，所以测不了、也换不了。
- **两个测试用 `inspect.getsource` 加手写括号匹配解析它的源码文本**。这是全仓最刺眼的一处。
- **17 个 kwarg 反复穿层**，加 6 个手写的构造点。

改成一个 `ServeDeployment` 加模块级的 rails 节点。三样症状一次消除：kwarg 穿层没了、构造点收成一处、两个解析源码的测试可以改成真正的断言。

**为什么必须在 5.3 之前**　5.3.2 / 5.3.3 / 5.3.6 要往这个函数里再塞**六组 emit 代码**。先做 5.3 等于让 1034 行继续长，然后再拆一遍。

**碰哪些文件**　`analyst/agent.py:391-1424`；6 个构造点；那两个 `inspect.getsource` 测试。注意 X.5.5 的记忆层参数名 `index_cache=` 本次不动，理由同 X.5.5（`tests/test_retrieval_index_cache.py:333/534` 按字符串盯着它）。

**怎么验证**　`grep -rn "inspect.getsource" tests/` 无命中；`agent.py` 最大函数行数降到三位数以内；`pytest tests/` 全绿。

### 4.3 · 把统计从 `run_datalake` 提出去（原 ARCH-7，2026-07-31 加回）

**改什么**　约 1300 行统计代码私有于 5371 行的 driver，而 **6 个测试文件通过下划线名 import 它们**。提成一个独立模块。

`grill-agenda.zh.md` 的 K2 曾建议「降级或直接删」这一条，至今无决定记录 —— 现在的决定是**做**，理由是 4.1（`GenerationRow`）本来就要把 `run_datalake` 里的行构造过一遍，两件事同域。

**明确不是** eval driver 合并（那件事已推迟）。这是把不属于 driver 的东西搬出去。

**碰哪些文件**　`eval/run_datalake.py`；6 个 import 下划线名的测试文件。

**怎么验证**　那 6 个测试文件不再 import 下划线名；`run_datalake.py` 行数显著下降；X.5.4 的 9 个基线数不变（这条不碰检索）。

---

## 随时可插

不依赖任何前置，也不被任何后续依赖。哪天卡住了就做这几项。

### X.1 · a2 `AssetBag` 六个开放 dict

**改什么**　`curator/asset_bag.py`（1262 行）暴露六个 dict，外部有 18 处直接伸手进去改。`tables` 按物理名索引，其余按 id 索引，所以每个调用方都得分支处理。改成 `AssetBag.from_corpus` / `install`，dict 转私有，删掉四个死别名。

同时修一个真 bug：`curator/pipeline.py:1528` 的 `if/elif` 链没有 `else`，所以 `curated_sme` 会**静默丢掉 note 和 negative example 两类资产**；而 `:1654` 的验收门检查的是两份 corpus 是否**不同**，不是是否**变大** —— 丢光所有 note 也能过。

**怎么验证**　外部不再能直接改 dict；一个测试断言 Phase B 之后每类资产的数量都不减少。

### X.2 · a4 presenter 与 api/schemas 对齐

**改什么**　`viz/presenter.py`（870 行）里 20 个记录，`api/schemas.py`（330 行）里约 25 个 pydantic 模型镜像它们，`from_attributes=True`，**没有一致性测试**。加 parity 测试；脱敏从私有 helper 变成视图接口上的一个参数（这项与阶段 8 的脱敏开关是同一件事，一起做）。

### X.3 · L1 分析工具 CLI 化

**改什么**　这一项对**你手上已有的那份 1351×4 数据立刻生效**，不用再跑一次。

现状：`eval/error_taxonomy.py`（547 行）和 `eval/sql_diff.py`（579 行）都是库，没有 CLI、没有 `__main__`，`analysis.py` 也不调用 `attribute_rows`。`analysis.json` 从来没有被任何一次跑产出过。最关键的一条：`docs/experiments/` 里那份错误分析报告 —— 五阶段漏斗、近似孪生混淆矩阵、「44 次误路由覆盖了更好的检索排名」、多余 `DISTINCT` 计数 —— **全部是用不在仓库里的临时脚本算的**。

要做的：

1. 给 `error_taxonomy` 和 `sql_diff` 各一个入口，能对单行、单 db、单臂运行。
2. 把那份报告里的五阶段漏斗、孪生混淆矩阵实现进 `analysis.py`。
3. 跨臂单题 diff：给一个 `question_id`，并排显示四个臂各自的 SQL、结果、失败阶段。现在 `comparisons[]` 只给不一致的**数量**，不给列表。
4. **把题目原文和 gold SQL join 进 artifact。**现在 72 个字段里两者都没有，每次 debug 的第一步都是回 sibling 仓库手工 join。
5. 让 `analysis.json` 在跑结束时自动产出，不需要单独一条命令。

**怎么验证**　用 20260730 那份数据重现出报告里的每一个数字。重现不出来的，说明工具和报告有一个是错的。

### X.4 · `runs/index.jsonl` 的缺失键被读成「两边一致」

**改什么**　`eval/index.py:376-430` 的 record 由字面量拼装（`MANIFEST_KNOBS` 展开 + 约 25 个手写键 + headline 嵌套块），没有自己的版本，只透传 `manifest_schema_version` 与 `headline_rate`。`index.py` 自己的注释写明：**缺失的 key 会被 `comparable()` 读成两边一致** —— 删一个键会静默让「哪两次跑可比」的规则失效，而这是唯一决定「这个数字能不能引用」的机器可读账本。加 `record_schema_version`；把 `comparable()` 对缺失键的处理从「一致」改成「不可比并给出原因」。这是 eval 正确性问题，不是对外契约，不要给它建字段注册表。

**碰哪些文件**　`eval/index.py:376-430` 与 `comparable()`；`tests/`。

**怎么验证**　构造两份各缺一个不同 knob 的 record，`comparable()` 返回 `False` 并给出缺失键名。

### X.5 · `retrieval/` 重组（`rvgd.py` 拆分）

本条**取代 1.4.3 里「`retrieval/rvgd.py` → `retrieval/corpus_search.py`」那一行**：不是改名成一个文件，是拆成七个，`corpus_search.py` 承接编排器角色。1.4.3 的 `RetrievalIndexCache` → `RetrievalIndexMemo` 在 X.5.5 落地。

X.5.1 到 X.5.4 各自可独立发；X.5.5 依赖 X.5.3 与 X.5.4；X.5.6 到 X.5.8 跟着 X.5.5；X.5.9 每一笔都是独立 commit。

#### X.5.1 · 先发 docstring 更正（零行为）

**改什么**　四处互相打架的 docstring，约 20 行，与拆分完全解耦。

1. `rvgd.py:4` 一行内制造两个冲突：`(the "V"/lexical channel)` 把 BM25 判给 V，与 `embedding.py:1` 冲突；`plus a small Ground expansion` 与 `__init__.py:12`「**G** graph」冲突，且与 `__init__.py:16`「**G is not built**」互相否定。
2. `__init__.py:6-8`「`RVGD` names four retrieval methods, not four asset classes」整句删掉 —— 它防的是一个没人在犯的误读，同段两个真错误没被防住。
3. `__init__.py:10`「**R** exact (id / physical-name lookup...)」是假的：`retrieve()` 的匹配路径里没有任何 exact-lookup 步骤，`synonyms` 只是进了 `asset_document` 被 BM25 打分，`phys_to_table` 只服务 grounding。唯一的子串精确匹配在 `triggers.py:56`。
4. `__init__.py:11` 把 BM25 塞进 V —— 四字母里没有别的地方能放它，**这是命名妥协不是分类**。

**`RVGD` 的处置：从代码层彻底退出，不造替代缩写。文件名即分类法。**`RVGD` 只作为对照参考书的**比较术语**留在 `book-fidelity-assessment.md` 与 `docs/architecture.md` §5。

新 `__init__.py` docstring 只列机制、只列做没做：`bm25.py`（词法打分器）/ `embedding.py`（dense vector 打分器 + RRF）/ `triggers.py`（keyword PIN 准入，**只覆盖 `NoteAsset`，regex 未做**）/ `grounding.py`（corpus 内四条硬编码关系的不定点闭包，**不是 graph traversal**）/ `schema_router.py`（跑在 `retrieve` 之前的预路由）/ `index_memo.py` / `corpus_search.py`（编排 + 契约）。

明写没做的四项：graph traversal；动态 few-shot 的自积累回路（无 `record_success`/`approve`/`review_status`/`fail_count`）；四阶段 rerank（`grep -rn "rerank" src/` 只命中文档提及）；token 预算与 QU 前置节点（`route_intent`/`bind_terms` 已于 2026-07-28 删除）。**第三种检索模式住在包外**：ADR 0003 的 agent-fetch `read_notes`/`grep_notes` 在 `analyst/tools.py:420/445`，docstring 必须点名。

**碰哪些文件**　`retrieval/rvgd.py:1-15`、`retrieval/__init__.py:1-43`、`retrieval/embedding.py:1-6`、`docs/analyst.md:28`（「dictionary」在书里从未对应任何机制，改成 dynamic few-shot 并注明我们无自积累回路）。

**怎么验证**　`grep -rn "RVGD\|rvgd" src/` 拆分后归零；`grep -rn '"V"' src/governed_bi/retrieval/` 无命中。

#### X.5.2 · 修 `open-work.md` E6 的机理，并给索引记忆补 schema-vector 槽

**改什么**　E6 现在写的「`schema_router.py:224-231` 在 cache 分支之前短路」**是错的**：行号已漂到 `:232`，且 `else` 分支确实用了 `index_cache.schema_docs`。真实缺陷两条：`RetrievalIndexCache.__slots__`（`rvgd.py:333`）**没有 schema 向量槽**；`embed_schema_documents`（`schema_router.py:165`）签名只有两个参数，直接调 `schema_documents(corpus)`，把 R6 修的那次 `for_analyst()` deep-copy 也绕过了。

实测 57 schema / 3070 asset：`schema_documents` 0.499 s，`embed_schema_documents` 0.814 s（含前者），**每轮都付**（`graph_app.py:163` → `agent.py:1454` → `:469`）。eval 路径不受影响（图只建一次）。

**碰哪些文件**　`retrieval/rvgd.py:316-394`、`schema_router.py:165-177/232-239`、`analyst/agent.py:469`、`docs/open-work.md:54`。

**怎么验证**　同进程连跑两轮，第二轮 `misses` 不增；`for_analyst()` 的 deep-copy 计数不再随轮数线性增长。

#### X.5.3 · `retrieval_config_hash` 进 manifest（X.5.5 的硬前置）

**改什么**　`eval/metrics.py:116-165` 的 `MANIFEST_KNOBS` 里**没有任何 asset 级检索项**，`provenance.py:84-102` 的 `serve_config_hash` 同样不含。而 `eval/index.py:100-105` 把 `git_sha` **显式排除**在 comparability 之外。

注意这些不是「可配 knob」—— `few_shot_k`/`term_k`/`metric_k`/`note_k`/`vector_weight` 在 `src/` 里除 `rvgd.py` 自己的签名外零命中，根本没有 CLI 能设。所以洞不在「有人调了」，而在**跨 commit 的代码层默认值变更对账本完全不可见**。

加 `retrieval_config_hash`，取值来源是模块常量与函数默认参数（融合开关 + `top_k` + 四个 per-type 预算 + `vector_weight` + `_SEMANTIC_BOOST` + `k1`/`b` + RRF `k`）。

**碰哪些文件**　`eval/metrics.py:116-165`、`eval/index.py:100-105`、`provenance.py:84-102`、`tests/test_eval_metrics.py:636-650`。

**怎么验证**　改任一默认值，哈希变，两次跑被判 not comparable；不改时拆分前后逐字节相同。

#### X.5.4 · 钉死回归基线（X.5.5 的硬前置）

**改什么**　`retrieval_eval` 的默认调用是 **n=4 且 `retrieved=1.000`/`licensed=1.000`**（`eval/dataset.py` 只有 4 个 `EvalItem`），`--embedder none` 与 `hashing` 完全一致 —— **它对任何改动都返回 1.000，不构成回归网**。

固定判据：对 `works_cycles`(73 表)/`mondial_geo`(42)/`hockey`(29) 三个 schema，`top_k ∈ {4, 8, 15}`、`--embedder none` 各跑一次，9 个数记进固定位置当基线。

```bash
uv run python -m governed_bi.eval.retrieval_eval --corpus-root corpora/curated_sme_20260730 --schema works_cycles --dataset-dir ../BIRD-Data-Obfuscation/eval_dataset --split test --gold-sql-field sql_rename --top-k 8 --embedder none
```

已实测锚点：`works_cycles` n=65 skipped=12，`none` → 0.723/0.969，`hashing` → 0.600/0.923。顺带给 `evaluate_retrieval` 加 `index_cache` 参数（现在 65 题触发 65 次重复建索引）。

**怎么验证**　基线命令在拆分前后输出完全相同的 9 个数。

#### X.5.5 · 拆分（主刀，行为逐字节不变）

**改什么**　`rvgd.py` 同时是最底层和最顶层，这是它 623 行的成因，也是**两条循环依赖**的成因：`schema_router.py:30` ↔ `rvgd.py:361`（函数体内）；`embedding.py:23` ↔ `rvgd.py:388/472`。第一刀必须切在这条倒置依赖上。

组织原则：**corpus 派生（可缓存、随 `corpus_index_key` 失效）在下层，question 派生在上层**。记忆层不是可选的优化模块，是下层的身份。

| 新文件 | 职责 | 搬自 | 包内依赖 |
|---|---|---|---|
| `documents.py` | asset 的文本表面，唯一定义。`asset_document`/`bm25_tokens`/`tokenize`/`_stem`/`_SEMANTIC_BOOST` | `rvgd.py:71-79/125-150/262-295` | 无 |
| `bm25.py` | `BM25Index`/`build_index` | `rvgd.py:153-227/298-300` | `documents` |
| `embedding.py` | dense vector + RRF，**不改名不改接口** | 原地 | `documents` |
| `triggers.py` | keyword PIN 准入，**零代码改动** | 原地 | 无 |
| `grounding.py` | `AssetIndex` + `ground` | `rvgd.py:397-426/526-541/544-570` | `documents` |
| `index_memo.py` | `corpus_index_key` + `RetrievalIndexMemo` | `rvgd.py:303-394` | `bm25`/`embedding`/`schema_router` |
| `corpus_search.py` | 编排 + `RetrievalResult` 契约 | `rvgd.py:230-259/95-122/429-623` | 以上全部 |
| `schema_router.py` | **本次不动**，只改两行 import | 原地 | `documents`/`bm25` |

共享工具归属：`tokenize`+`_stem` → `documents.py`（**不单开 `text.py`**，剩下那两个函数只有一个消费者，作者自认任意的边界不是 seam）；`asset_document`+`bm25_tokens`+`_SEMANTIC_BOOST` → `documents.py`，三者必须同处（提出它同时解掉两条模块级反向依赖）；`content_terms`+`lexical_coverage` → `corpus_search.py`。

**记忆保持单一对象，不按通道拆。**五个传递点共用一个实例，按通道拆会让它们变成传 N 个对象。参数名 `index_cache=` 与局部变量 `_index_cache` **本次不动** —— `tests/test_retrieval_index_cache.py:333/534` 两处 `inspect.getsource` 结构测试按字符串盯着它们。

`grounding.py` 是全案 seam 最实的一刀：接口纯集合到集合（`ground(index, seeds) -> set[str]`），内部深（sqlglot 容错、qualified 优先、裸名歧义 → `None`、不定点循环）。`AssetIndex.table_by_physical_name` 承接 `rvgd.py:530-538` —— **那是全仓库唯一正确的实现**（见 1.2 / C13）。

**`documents.py` 的 docstring 必须写死一条现在没人知道的不对称**：`build_index` 索引 `bm25_tokens`，`build_embedding_index` 索引 `asset_document`。`_SEMANTIC_BOOST` 只在 `bm25_tokens` 里做 token 重复，`asset_document` 是一个字符串、没有加权概念。**把 boost 从 1 调到 >1 只给 BM25 一路加权，向量那一路保持平的**，随后 RRF 把两条口径已经不同的排序融在一起。而 `rvgd.py:268-273` 的 TUNING 注释正在指挥下一个人往这个坑里走，唯一为该 knob 写的规格 `tests/test_retrieval.py:253-272` 不带 embedder，永远看不到失配。

**怎么验证**　X.5.4 的 9 个基线数逐位相同；`pytest tests/` 全绿且 `test_retrieval.py:253-272` 仍是 xfail（**不是 XPASS**）；无函数体内 import；无循环导入；commit message 逐条列出保持原样的六组默认值。

#### X.5.6 · `RetrievalResult` 拆完之后的形状

**改什么**　现有 10 个字段的名字、类型、顺序**全部不动**。约束三条，都是运行时才炸：`tests/` 里 19 处直接构造（8 个文件）；`tools.py:311-317` 用 `dataclasses.replace`（字段改名 import 期不报错）；`tools.py:49` 的 `render_retrieval` **无类型标注**。

**不做嵌套槽模型。**`search_corpus` 的治理过滤只覆盖 tables 一个槽，改成每引擎一槽之后 `replace(table_ids=)` 不成立，同一条治理策略要在 N 个槽上各放一次。

**加一个带默认值的字段**落地 5.3.6 第 7 条：`ranking: tuple[tuple[str, float], ...] = ()`，装融合后的完整排序（含被预算切掉的候选）。今天 `scores` 只含 selected，落选项在 `retrieve` 内部就被丢弃。必须带默认值，否则 19 处构造全断。

docstring 钉死三条今天没写在任何地方的事实：**grounded additions 不在 `scores` 里**（取 0.0，排在所有正分资产之后）；**`top_k=8` 是种子上限不是输出上限**（grounding 跑在预算之后，实测返回 9-12 张，pooled 19 张，`context.py:239-241` 之后还会追加 licensed join 表，**全仓对最终表数无任何上限**）；`column_ids` 在 `src/` 里**无生产消费者**。

#### X.5.7 · 测试迁移与 `eval/retrieval_eval.py` 不失效

**改什么**　`retrieval_eval` 对 `retrieve` 的耦合面实测只有三样：包 facade 上的 `retrieve` 名字、`top_k`/`embedder` 两个 kwarg、`RetrievalResult.table_ids`。保住这三样就不会坏。

**不留兼容别名。**五个文件八处 monkeypatch `governed_bi.analyst.agent.retrieve`；留别名 = patch 打在没人用的名字上、测试全绿却什么都没测。

只有三个文件要动：`tests/test_retrieval.py`（BM25 部分搬到新 `test_bm25.py`，`test_schema_documents_exclude_note_text` 搬到 `test_schema_router.py`，**其余 `:88-296` 原地保留**，那是全套里最有价值的一批）；`test_embedding_retrieval.py:22` 改一行 import；`test_curator_caveat_notes.py:35` 改一行。

新增 `tests/test_grounding.py`：`ground` 是纯集合函数，不搭 corpus fixture 直接测四条闭包规则 + 不定点收敛。**必补今天缺失的一条**：两个 schema 各有同名裸表时，few-shot 里的裸名引用应 ground 到**零**张表而不是先加载的那张。

新增一条 repo contract：`src/` 下 `retrieval/` 之外不得 `from ..retrieval.<submodule> import`。生产代码今天 100% 合规，零成本。

`test_retrieval.py:253-272` 的 `xfail(strict=True)` 一旦 XPASS 就是设计好的报警。**不要翻 strict。**

#### X.5.8 · 文档指针

**改什么**　`docs/` 下 `rvgd.py:NNN` 引用 39 处（11 个文件），`schema_router.py:NNN` 14 处，**其中已有数处是错的**（`adr/0003:49/78/238` 三处行号全部指向了别的东西）。政策：**行号引用降级为函数名引用**（`rvgd.py:429` → `corpus_search.py::retrieve`）。

`docs/adr/0003` **逐条重定位，不能用顶注打发** —— 它是 Accepted 状态。`docs/plans/` 下七份加顶注即可。

**同批修 `book-fidelity-assessment.md` 的四处** —— 它是本次决策的依据，它错了等于依据错了：(a) §3.4 把 Corrective-RAG 三档列在 Book 列而未标「书自承未实现」；(b) §3.2 写「G runs serially over the **union**」—— 书的代码是 `collect_asset_ids(vector_results)`，**只收 V 的结果，不是并集**；(c) §3.2 写「Each engine owns a slot; nothing competes across slots」—— **书没有这个陈述**，实情是 V 拥有整个容器、D 寄生在 V 的 `few_shots` 里、R 的产出是个下划线前缀 view；(d) §3.1 采用 §9.2 的「8 张 embedding 表」，未提 §4.1 的「7 张」冲突。

#### X.5.9 · 拆分之后的独立小 commit（每笔各自自报）

1. **`corpus.by_id` 线性扫描（E11）。**`rvgd.py:597` 在循环内调线性扫描，而 `:488` 建的 `by_id` dict 就在作用域里。实测 3.54 ms/问 + 3.12 ms 重建 = 6.7 ms，占热态 `retrieve`（18.6 ms）的 **36%**。输出逐字节相同。
2. **`_ordered(column_ids)` 是 no-op 排序。**column id 永不进 `score_map`，排序键恒为 `(0.0, -0.5, id)`。化简成 `sorted()`，去掉那行不再误导人以为列按相关度排过序。
3. **`triggered_note_ids` 入参的哨兵语义。**`rvgd.py:546-547` 双双吃掉空列表转而重算 —— 这个 seam 结构上无法表达「我算过了，结果是空」。改成 `is None` 判断。
4. **`fire_triggers` 每问跑两遍，且跑在两个不同的 corpus 上**（`schema_router.py:270-274` 全量 vs `rvgd.py:547-550` routed）。两次结果可以不同，所以不是纯浪费 —— 但今天没有任何地方记录这个差异。**先只加注释把约定写下来**，真改是设计决策。
5. **`route_schemas` 删除，不是改名。**`schema_router.py:328-339` 在 `src/` 里无人调用，而 1.4.2 已定「`route`/`select` 不再当动词」。
6. **`eval/oracle.py:301` 的 `table_budget: int = 8` 是 `top_k=8` 的手抄副本**，且绑定它的那句注释**是假的**（`top_k` 是种子上限，grounding 跑在预算之后，实测返回 9-12 张）—— 所以 **`oracle_tables_padded` 控制臂系统性欠 pad 12%-50%**，`beer_factory`（9 表）pad 到 8 直接退化回 `oracle_schema`。这是 eval 有效性问题，不是重构附带项。
7. **给 `BM25Index` 加 term → doc_ids 的 postings map。**实测 3070 个文档里只有 448-791 个非零，**76% 的打分花在零词重叠的文档上**。行为逐字节等价。

#### X.5.10 · A0–A7：参考书的七项结构性选择，各自开项

**这一轮都不做**（决定 15）。列在这里，因为它们是真选项，不是遗漏。每一项都是独立的行为变更加独立基线 —— 没有一条能在拆分 commit 里顺手做。

| # | 条目 | 状态与解锁条件 |
|---|---|---|
| A0 | **RRF 融合存废。**书的引擎从不融合排序，各写各的槽 | **Gate**：先给 `retrieval_eval` 加真 embedder（`--embedder openai\|bedrock`）。`HashingEmbedder` 是比 BM25 更弱的**词法**通道，用它做的 A/B 说明不了问题。另外要修订 ADR 0003（Accepted，决定条款逐字写着「BLEND into RRF normally」），并记两个 `scores` 下游：`tools.py:60-62` 把 `rank_score` 直接打进模型可见的工具输出，`note_inject.py:246` 参与 always-note 预算排序 —— **入选的 note 集合会变**，而 `always_note_*` 是 comparability key |
| A1 | **精确命中是准入池还是排序通道。**书的 R 命中即全量返回；我们只有 `triggers.py:18` 是真准入池，且七种资产只覆盖 `NoteAsset`、regex 未做。`TermAsset` 的精确命中今天在 `term_k=5` 里和 BM25 分数一起排队 | **开项待评估**。动 `RetrievalResult` 的下游（X.5.6 的三条约束）；准入不占槽意味着最终表数进一步无界 |
| A2 | **索引单元粒度**（列作为一等文档） | **Gate**：X1 长度匹配 placebo 臂 + `retrieval_eval` 扩出列级召回与 prompt 尺寸 + 一份 assurance 分布基线（改索引单元会改词表，词表经 `lexical_coverage` 独立地移动每题的 `semantic_assurance`） |
| A3 | **ranking query 用 `tokenize` 还是 `content_terms`。**`rvgd.py:221` 用前者，停用词表只喂 `lexical_coverage` | **七项里最便宜的一项**：一行改动加一次 X.5.4 基线对照。已定不与拆分同批做（会在账本上留 delta，「逐字节不变」的担保就没了）。拆分落地后第一个做它 |
| A4 | **预算的位置。**书在链路末端（prompt 组装前），我们在首端 —— 中间隔着两次无界扩张。差别不是单位，是位置 | **开项待评估**。会移动 `oracle_tables_padded` 控制臂，也会移动 `eval/analysis.py:369-380` 的 `retrieval_miss` / `selection_miss` 切分，而 `runs/index.jsonl` 无字段报告后者 |
| A5 | **term binding 作为结构化产出向下游传播**（书传五环；我们只拉一个 asset 进 selected + 渲染一句 `binds_to`） | **Gate**：等 A0 有结论（两者都动 `RetrievalResult` 下游）。方向上还与今天相反：书把绑定目标 boost 到最前，我们的 grounded 目标取 0.0、排在所有正分资产之后 |
| A6 | **D 的自积累回路**（书是 `record_success` → `approve` → `search_similar` 三步审批） | **Gate**：等交互信号落地。三步全缺，也没有 `review_status` / `fail_count` / `sql_cache` |
| A7 | **G 是不是 graph traversal**（书是 AGE 上 `depth=3` 的 Cypher、13 种边、三个职责） | **非目标**。需要新的图存储依赖；书 G 的另两个职责在我们这里无对应物，要从零建 |

书的调参性数字（B1–B7）一概不采纳。其中两条值得单独记：**B3（V 的 `threshold=0.65` / `top_k=20`）已被我们自己的 harness 证伪** —— `works_cycles`/`top_k=8` 下截断到 20 → 0.538、截断到 8 → 0.554、不截断（现状）→ **0.600**；**B7（Corrective-RAG 三档阈值）不构成可采纳项** —— 书 §4.4 逐字承认「不评估检索质量，无 retrieval confidence score」。

---

## 阶段 5 · 记录、可观测与契约

> **2026-07-31 重排（决定 22）：这一阶段里只有 5.1、5.2、5.4 在 A 的关键路径上。5.3 移出了。**
>
> 审计给出了一个可机器证明的事实：**5.3 物理上不可能影响 A。**`eval/arms.py:429-443` 调 `build_serve_rails(...)` **完全不传 `on_event`**，而 `analyst/governance.py:531-532` 是 `if self._on_event is None: return` —— 所以 5.3 的全部事件契约改动在那 5404 个 turn 上一个字节都不会生效。eval 侧也不 import `api/`、不 import `presenter`（两个 grep 均零命中）。
>
> 但**不要**据此说 5.3 贡献为零：决定 13 明写它的目的是「让将来那次前端重写是照着文档抄，而不是考古」，用 A 的验收标准去量一个不以 A 为目标的条目是错的尺子。
>
> | 条目 | 在 A 的关键路径上？ |
> |---|---|
> | 5.1 事后可复原的记录 | 是 |
> | 5.2 实时可观测 | 是 |
> | **5.4 从 5.3 抽出来的三条真 serve bug** | **是** |
> | 5.3 后端契约发布（11 个子条目，178 行） | **否 —— 依赖 4.2，在它之后做** |

### 5.1 · L2 事后可复原的记录

**改什么**　现在一道错题能查到「哪一步错了」，查不到「为什么」。缺的是：

| 缺什么 | 现在有的 |
|---|---|
| prompt 原文 | 只有 `context_chars` 和 `context_hash` |
| guardrail 判决列表 | 只有 `ledger_len: 1` —— 列表本身在 `eval/arms.py:480` 被丢掉 |
| serve 的工具调用记录 | 只有 `{tool: 次数}`。curator 有 `curator_trace.jsonl`，analyst 没有对应物 |
| 单题重跑入口 | 两个 driver 都没有 `--question-id` |

前三项落盘，第四项加入口。注意：LLM 有随机性，重跑并不真的复现，所以**记录比重放重要**。

**怎么验证**　随便挑一道错题，不重跑，仅凭 artifact 就能说清模型看到了什么、guardrail 说了什么、它试了几次。

### 5.2 · L3 实时可观测

**改什么**　服务器上跑一个多小时的任务，现在你看不见任何东西：

- serve 阶段**每题零输出**，每个臂静默 16 到 27 分钟，连续四次。`eval/parallel.py:180-183` 有个 `on_result` 钩子，driver 只用它写盘（`run_datalake.py:3966-3974`），一个字不打。
- stdout 上**没有任何一行带时间戳**。
- 构建阶段 20 个线程的日志交错、无 db 标签、会串行断行。
- 结束时终端一次性吐 **50,716 行 JSON**，绝大部分是 `question_ids` 数组。
- `run.console.log` 不是代码写的，靠操作员记得重定向。

对应改：每行加时间戳；serve 每 N 题打一行进度和 ETA（钩子已经在）；构建日志加 db 前缀；结构化日志自己写文件；把巨型 JSON 从终端挪进文件。

**怎么验证**　跑一次 5 题的小跑，全程 stdout 不超过 50 行，且每一行都能看出「现在在干什么、到哪了」。

### 5.3 · C 后端契约发布

后端说了算，前端适配（决定 13）。现有 `../governed-bi-ui` 只当**消费者样本**读，不当维护对象 —— 原先「后端发了前端扔掉」那两条按此销案（新前端接就是了），只保留后端侧确实是缺陷或多余物的部分。

十条按顺序做。5.3.7 依赖 2.1（`Step` 收敛）与 5.3.1–5.3.6 的行为定型；其余按编号顺序。

#### 5.3.1 · 离线 wire 装置（先建，后面每条都往里加断言）

**改什么**　`tests/test_chat_graph.py:1-49` 声称「Answering a turn now requires a live model」，**这个前提是假的**：把 `governed_bi.llm.fake.FakeToolModel` 装进 `ServeStack(chat_model=...)`，`build_chat_graph(stack).stream(...)` 能离线跑完一轮 governed 答案。建一个 hermetic conformance 测试：起图 → 开 thread → 跑一轮 → 断言四个通道的**基数、顺序、载荷**。

实测基线：一轮成功 turn 上 `values` 恰好 2 帧、`updates` 1 帧、`messages` 1 帧、`custom` 10 帧；`rail` 的 `route` / `refuse_gate` / `assemble` 全部排在任何 `tool` 事件之前（`assemble` 在 `seq=2`，第一个 tool start 在 `seq=3`）；`route.detail` 与 `refuse_gate.detail` 恒为 `{}`。

序列化断言必须用真实传输层 `langgraph_api.serde.json_dumpb`，不要用 `json.dumps`。`json_dumpb` **从不抛错**：`{"a": object()}` → `{"a":null}`，`memoryview` → `null`，`set` → JSON 数组。坏值的表现是**字段静默变 null**，不是事件消失。断言写成「往返后无 null 塌陷」。

同时给 `ServeStack` 加 replay/fixture 模型开关。现在别人起不了这个后端：`POST /chat` 在 `stack.chat_model is None` 时 503（`api/app.py:488-490`），LangGraph 那条无条件走 `answer_question_agent`；`scripts/` 只有三个脚本，无 Dockerfile、无 compose、无 replay。把这一轮的四通道帧录成 `docs/samples/stream-turn.jsonl` 发布。

**碰哪些文件**　新增 `tests/test_wire_conformance.py`、`docs/samples/stream-turn.jsonl`；`src/governed_bi/llm/fake.py`；`api/stack.py`；`tests/test_chat_graph.py:43/137/152/163`。

**怎么验证**　无 `OPENAI_API_KEY` 的环境里 `uv run pytest tests/test_wire_conformance.py -q` 绿；用 replay 开关起 `uv run langgraph dev`，不给 key、不给 Postgres 也能收到完整事件流。

#### 5.3.2 · 删死字段与谎报的能力位

**改什么**　四个都是「契约面上存在、实现恒为空或恒被忽略」：

1. `label` —— `analyst/governance.py:536-538` 会写进信封，`rail()`（:548）与 `tool()`（:552-562）都有形参，`agent.py` 全部 14 个发射点无一传（`grep -c "label=" src/governed_bi/analyst/agent.py` = 0）。删形参、删信封字段。
2. `ChatRequest.identity`（`api/schemas.py:295`）—— 进了 `docs/openapi.json`，而 `api/app.py:510` 传的是 `stack.identity`，请求里的值从不读。删字段并重新导出。
3. `can_search`（`api/stack.py:254` 硬编码 `False`，全仓无路径能置 `True`）—— 删字段，或加 `Field(description="always false in this build")`。
4. `analyst/agent.py:1037` 的 `step = info.get("step") or "tool"` —— `pending` 在 `:1005` 每次 `_stream_agent` 新建，resume 后 pop 不到，`ask_user` 的收尾事件永远发成 `step="tool"`。改成取 `msg.name`，删掉 `or "tool"` 这个静默默认。

**碰哪些文件**　`analyst/governance.py:536-562`、`analyst/agent.py:1005/1036-1037`、`api/schemas.py:295`、`api/stack.py:254`、`docs/openapi.json`。

**怎么验证**　`grep -n "label" src/governed_bi/analyst/governance.py` 只剩无关命中；导出后 `ChatRequest` 无 `identity`；clarification resume 测试断言收尾事件 `step == "ask_user"`。

#### 5.3.3 · 修 wire 上的真缺陷

**改什么**　七条，都是消费者按直觉写代码就会错的：

1. **第 N 轮（N>1）的首个 `values` 帧携带第 N−1 轮的答案，且 `tier="governed"`。** `ChatState.answer` 是 `dict | None`（`api/graph_app.py:40-48`），无 reducer，last-write-wins；`answer` 节点只在结束时写，从不在开头清空。实测同 thread 连发两问，turn 2 首帧是上一轮的答案，整轮 10~140 秒都是这个状态，末帧才换。唯一能区分的是 `answer.provenance.turn_id`。修：`answer` 之前加一个只返回 `{"answer": None}` 的节点。
2. **同一批 DB 值在两条传输上序列化不同。** `Decimal("18496.55")` → REST 给字符串 `"18496.55"`，流式给数字；tz-aware `datetime` → `"...Z"` vs `"...+00:00"`；`bytes` → REST 给带控制字符的乱码，流式给 base64；`NaN` 两边都静默变 `null`。psycopg 对 Postgres `NUMERIC` 默认返回 `Decimal`，**金额列每轮都踩**。修：在 presenter 里归一化，复用 `corpus/serialize.py:41` 已有做法。
3. **两条路的答案对象不共用序列化器。** `api/graph_app.py:192` 走裸 `asdict`，`api/app.py:531` 走 `AnswerResponse.model_validate`（`_View` 未设 `extra`，默认 ignore）。给 `AnswerView` 加字段，流上立刻出现、REST 上静默丢。修：`graph_app.py:192` 改成 `AnswerResponse.model_validate(...).model_dump(by_alias=True)`。**这条吸收 X.2 的对齐部分。**
4. **`seq` 跨 clarification 归零、rails 重放三遍。** `api/graph_app.py:154-189` 的 `while True` 第一次以 `clarify_resume=None` 调用 → 内层已暂停 → `interrupt()` → 第三次调用才带上答案，所以 `ingest→refuse_gate→assemble` 跑三遍（`agent.py:553` 每次 `events.reset()`）：`seq` 归零三次、完整检索与上下文装配做三次。而 `docs/analyst.md:106` 承诺 "seq is monotonic per turn"。修：信封加一个跨 resume 单调的 `pass` 序号；`assemble` 的重复执行按成本单独评估。
5. **流式侧 working memory 无上限。** `api/graph_app.py:94-103` 的 `InMemoryWorkingMemory()` 不传 `max_turns`，整段 thread 转录无界注入 prompt；REST 侧有 100 轮 / 每条 8000 字符（`api/schemas.py:284/294`）。两条传输取同一个值。
6. **一个进程两份 `ServeStack`，且 `POST /corpus/edit` 写盘后本进程读不到。** `api/routes.py:28` 在 import 时 `build_stack()` 建一份，`graph_app._build_graph` 又独立调 `build_stack()` 建第二份（无 `lru_cache`），一个进程两份 corpus、两份 index_cache、两套 clarify checkpointer。`api/app.py:459-468` 写成功后直接返回，从不刷新 corpus。策展客户端会看到「200 写成功 → 列表还是旧的 → 答案还是旧的」。
7. **narrator 的 token 归属互抢。** `analyst/agent.py:1397-1407` 从 stack 级共享的 `narrator._chat` 读并清空 `last_usage_metadata`，而 `narrator` 由 `build_stack()` 建一次。LangGraph Server 默认并发跑 run，不需要开多标签页就能触发。修：usage 从调用返回值取。

**碰哪些文件**　`api/graph_app.py:40-48/94-103/152-192`、`api/app.py:459-468/531`、`api/routes.py:28`、`api/stack.py`、`viz/presenter.py:855-860`、`gateway/connectors/postgres.py:128-131/237-251`、`analyst/agent.py:553/1397-1407`、`memory/store.py:52`。

**怎么验证**　conformance 测试加：同 thread 连发两问，turn 2 的所有 `values` 帧要么 `answer is None`、要么 `provenance.turn_id` 等于本轮；同一行 `Decimal`/`datetime`/`bytes` 在两条传输上 JSON 相等；一次 clarification 之后 `pass` 严格递增；`build_stack()` 两次调用返回同一对象；`POST /corpus/edit` 之后 `GET /schema` 立刻可见。

#### 5.3.4 · 平台自带路由的暴露面

**改什么**　`langgraph.json` 同时挂 `graphs.serve` 与 `http.app`，**无 `auth` 段、无 `http.cors` 段**。平台自带 49 条路径与我们的 12 条并存，至少四处是活的暴露面：

1. **`POST /threads/{id}/state` 可直接伪造答案。** 实测 `graph.update_state(cfg, {"answer": {"tier":"governed","text":"FORGED ANSWER", ...}})` 后 `get_state` 原样返回；`answer` 无 reducer，是纯覆盖。HTTP 路由是它的薄封装且无鉴权。
2. **`stream_subgraphs=true` 绕过全部脱敏。** 由客户端自选（`langgraph_api/models/run.py:115` 字段、`:341` 映射成 `subgraphs=True`）。打开后 `messages` 通道依次吐出原始模型 prose、tool_call args 里的原始 SQL、完整 `ToolMessage` 正文（含结果行与 corpus 笔记全文），绕过 presenter 的全部脱敏与两轴戳；`subgraphs=False` 时只有 1 条最终 `AIMessage`。**无鉴权、无开关、不在任何文档里。**
3. `/store/items`、`/store/items/search`、`/store/namespaces` 是开放 KV。
4. **`require_mutating_auth` 管不到真正执行 SQL 的那条路。** 它只挂在 `api/app.py:347 /corpus/edit` 与 `:478 /chat`；`useStream` 走的 `/threads`、`/runs/stream` 完全不经过它。

**CORS 同理：实际生效的是外层通配。** `.venv/.../langgraph_api/config/__init__.py:283` 的 `CORS_ALLOW_ORIGINS` 默认 `"*"`，`langgraph_api/server.py:80-82` 在 `CORS_CONFIG is None` 时用它 + `allow_credentials=True` 挂全局中间件；内层 `api/app.py:130-139` 只覆盖被 mount 的自定义路由，预检在外层就被短路。

具体做法取决于 P1 与 P6。无论哪种都要落地：`langgraph.json` 加 `http.cors` 与 `[serve].cors_origins` 同源（或删掉内层那份让配置只有一处）；契约文档列出必须由网关拦截或鉴权覆盖的完整路径清单，`stream_subgraphs` 明确列为「必须拒绝的请求参数」。

**碰哪些文件**　`langgraph.json`、`api/app.py:130-139/141-169`、`config.py:301`、契约文档一节。

**怎么验证**　起 `langgraph_api` 后断言：无凭据的 `POST /threads` 被拒（或该路径已在文档清单里）；`stream_subgraphs=true` 时 `messages` 通道不出现原始 SQL 与 `ToolMessage` 正文；`POST /threads/{id}/state` 不能把 `answer.tier` 写成 `governed`。

#### 5.3.5 · 脱敏拆成两件事，只有 `reason` 走共用开关

**改什么**　`viz/presenter.py:812-842` 的 `_redact_provenance_for_client` 现在做的是两件无关的事，不能一起开关：

- `:826-833` 清空 ledger entry 的 `result.rows` 并加 `rows_redacted` —— D7 批量数据策略，**无条件保留**。开关若包住整个函数且默认关，一个匿名可达的面会开始下发完整执行结果行。
- `:834-840` 把 `reason` 置 `None` 并加 `reason_redacted` —— S7 PII 策略（`verdict="error"` 时是裸 `str(err)`，libpq 内嵌 `LINE 1: SELECT ...`，回显问题字面量）。**这一件走开关，默认关。**

实时流侧 `analyst/agent.py:949`、`:962`、`:1054` 原样发 `reason`，与最终答案硬编码脱敏正好相反 —— **共用开关不存在共用点，必须先合并这个分叉**。抽 `redact_ledger_entry(entry, *, redact_reason: bool)` 放 `analyst/governance.py`，两条通道共用。默认关意味着 presenter 现有的硬编码脱敏要被显式改掉，这是行为变更不是新增。

`rows_redacted` / `reason_redacted` 两个标记字段同时写进 5.3.7 的注册表。**这条吸收 X.2 里「脱敏从私有 helper 变成视图接口上的一个参数」。**

**碰哪些文件**　`viz/presenter.py:812-842/868`、`analyst/governance.py`、`analyst/agent.py:949/962/1054`、`config.py`、`api/stack.py`。

**怎么验证**　同一轮同时取 `custom` 通道的 `run_query.detail.reason` 与 `values.answer.provenance.governance_ledger[].reason`：开关关时两边都有值，开关开时两边都是 `None` 且都带 `reason_redacted`；两种状态下 `result.rows` 都恒为空且带 `rows_redacted`。

#### 5.3.6 · 先全发：补上内存里已有、wire 上丢掉的信号

**改什么**

1. **`read_notes` / `grep_notes` 是信息丢失，且治理动作以 `ok` 上报。** `agent.py:915-926` 的 `_tool_start_detail` 无这两个分支，返回 `{}`；resolve 落到 `:989-990` 的 `else` 兜底。参数在手（`:1029-1032` 已取到 `note_id`/`pattern`）。工具本身会返回 `"withheld (names excluded identifiers)"`（`tools.py:436`）与 `"error: ..."`（`:456`），两者都上报成 `status="ok"` —— **一次因排除标识符而被拒绝的笔记读取，在流上与正常读取完全不可区分**。补 detail，`withheld` 时 `status` 用 `blocked`。
2. **`narrate` 不发事件。** `agent.py:1370-1407` 是真节点、会调 LLM、会整体 `replace` answer 并重新 stamp `stage_events`，一个事件都不发。补 `rail("narrate", "start"/"ok")`。`docs/open-work.md:41` 的 C19 同源，一并修（该条行号已漂）。
3. **`final` 只发 6 键**，而 `docs/analyst.md:100` 写「the two-axis stamp plus the whole `provenance` dict」。按「先全发」改代码不改文档。`final.detail` 必须走 5.3.5 的同一个共用函数，否则 `custom` 与 `values` 会同时携带同一个 dict 的两个脱敏程度不同的副本。
4. **`search_corpus` 事件的 `items[].name` 无界。** `tools.py:275-289` 对 note 资产一路 fallback 到 `summary`，实测是约 400 字符的完整笔记正文，出现在匿名可达的流上；同一轮 `assemble` 事件里的笔记项却只有 `{id, normative_force}`。两处统一形状，`name` 加长度上限。
5. **`ask_user` 收尾事件补关联键**：`clarification_id`（`clarify.py:39-49` 已生成，从不上流）与 `answered_by`；`declined` 时 `status` 发 `"declined"`。
6. **跨 wire 的改名，从 1.4 转过来**：`detail.rows` 现在是**行数**（`agent.py:951/962` 取 `result.get("row_count")`）而 `answer.result.rows` 是**二维网格** —— 同一个词在同一份契约里指两个东西，改 `detail.rows` → `n_rows`；`asset_type` → `kind`；wire 上 SQL 命名空间统一到 `schema`；`tier` 的三义（answer 的 `ReliabilityTier` / corpus 的 Facts-Inference-Audit 字段层 / clarification 载荷的 `"tier": "audit"`，`clarify.py:45`）在 wire 上只保留第一个。
7. **其余「先全发」项**：guardrail 逐层判决、retrieval 分数与落选项、prompt 组成、token 与延迟。emit 签名带 `level`（默认 `INFO`）。

**碰哪些文件**　`analyst/agent.py:96-100/915-926/949-990/1029-1040/1367-1407`、`analyst/governance.py:521-599`、`analyst/tools.py:275-289/436/456`、`analyst/clarify.py:39-49`、`docs/analyst.md:67/89-101`、`docs/open-work.md:41`。

**怎么验证**　跑一轮命中 `withheld` 的 `read_notes`，断言 `status == "blocked"` 且 detail 非空；跑一轮成功 turn，断言 `custom` 上出现 `narrate` 的 start/ok；`final.detail` 与 `values.answer.provenance` 逐键相等；clarification 的 `ask_user` resolve 事件带 `clarification_id`。

#### 5.3.7 · 事件契约注册表 + strict 模式 + 生成物 + CI

**改什么**　照抄 `eval/metrics.py` 那套（register + 生成物 + `--check` + **双向断言**），不另起炉灶。关键是双向断言而不是生成器：`tests/test_eval_metrics.py:636-650` 同时断言 emitted-but-undeclared 与 declared-but-absent；只抄 `gen_eval_metrics_doc.py` 等于把 markdown 换个后缀。

1. 新增 `src/governed_bi/analyst/event_contract.py`：`EventSpec(kind, step, statuses, fields)` + `ENVELOPE` + `CONTRACT_VERSION`（一个整数）+ `CHANNELS`。每个 `Field` 带 `level`（出现所需的最低详细度）与 `redaction: None | "drop" | "mask" | "hash"`。`required` 只放信封键 —— `governance.py:539` 的 None-strip 让 detail 里任何字段都可能缺席，声明 required 是撒谎。另出一张 `STAGE_FOR_STEP` 映射，把缺口显式列出来：`stages.py` 的成员里没有 `schema_route`/`run_query`/`ask_user`，反向 `Stage.agent_core` 与 `Stage.narrate` 现在无任何事件。
2. `GovEventStream._emit_event` 加 `strict` 标志：`(kind, step)` 未声明 / `status` 不在 `spec.statuses` / detail 键不在 `spec.fields` → raise。默认 `False`（生产不能因为契约问题拒答），`tests/conftest.py` 对全测试会话置 `True`。**raise 写在 `governance.py:543` 的 `try` 之前**，否则被 `:545-546` 的 `except Exception: pass` 吞掉。**不做兜底 spec** —— `agent.py:990` 的 `else` 分支让「注册一个新工具、`agent.py` 一行不改」就能上线一个未声明的 step，strict raise 就是用来堵它的。
3. `CONTRACT_VERSION` 随 `serve_path` 一起发（`governance.py:540-542` 那个每 turn 只写一次的分支）。bump 规则写死在模块顶部：删除或重命名任何 `(kind, step, status)` 或字段 → bump；新增字段 → 不 bump。
4. `governance.py:543-546` 的 `except Exception: pass` 加一行服务端日志。
5. 新增 `scripts/gen_event_contract.py`：抄 `gen_eval_metrics_doc.py:29-66` 的子进程 DUMP 与 unified_diff、抄 `export_openapi.py:96-118` 的 JSON pointer 级 drift 报告。生成 `docs/stream-contract.md` 与 `docs/stream-events.schema.json`，支持 `--check`。JSON Schema 用 `oneOf` + `step` 作 discriminator，每个 property 挂 `x-level` 与 `x-redaction`。**本仓不生成 `.d.ts`**，在文档里写一条 `npx json-schema-to-typescript docs/stream-events.schema.json`（`.github/workflows/ci.yml:16-29` 无 node）。
6. `interrupt` 通道模型化：`clarify.py:29-50` 的手拼 dict 与 `:53-72` 的 `parse_response` 改成 `api/schemas.py` 里的 pydantic 模型。FastAPI 只导出被路由引用的模型，所以要么挂到某个已有响应上，要么在 `export_openapi.py:76-78` 的 `render()` 里后处理注入 components。
7. `docs/analyst.md:85-100` 的手写表整段删掉换成链接，不留摘要版。顺手修 `:67` 的「four tools」（实际 6 + `ask_user` 共 7）与 `stages.py:44` 的「seven」（rails 块只有六个成员）。
8. CI 在 openapi `--check` 之后加 `gen_event_contract.py --check`。

**碰哪些文件**　新增 `analyst/event_contract.py`、`scripts/gen_event_contract.py`、`tests/test_event_contract.py`、`docs/stream-contract.md`、`docs/stream-events.schema.json`；改 `analyst/governance.py:521-562`、`analyst/agent.py`、`analyst/clarify.py:29-72`、`api/schemas.py`、`scripts/export_openapi.py:76-78`、`tests/conftest.py`、`.github/workflows/ci.yml`、`docs/analyst.md:67/85-100`、`stages.py:44`。

**怎么验证**　`gen_event_contract.py --check` 绿；往 `analyst/tools.py` 注册一个新工具、不改 `agent.py`、不改注册表，`pytest` 变红；从注册表删一个仍在发的字段，测试变红；每个 spec 的样例载荷过 `json_dumpb` 往返无 null 塌陷。

#### 5.3.8 · openapi 补全

**改什么**　`docs/openapi.json` 现在每条路由的 `responses` 只有 `['200']` 或 `['200','422']`，`components` 无 `securitySchemes`、无任何 `security` 块。

1. 补错误响应：`api/app.py:157/165`（401）、`:251/:269`（404）、`:375`（403）、`:427`（422）、`:464/:528`（500）、`:490/:505`（503）。`detail` 有两种形状（`HTTPException` 是字符串，422 是对象数组），写成 union。
2. 用 `APIKeyHeader` 声明 security scheme 并挂到 `/corpus/edit` 与 `/chat`，密钥配置方式（`[serve].api_key_env` 存的是**环境变量名**）写进描述。
3. 枚举全部 `Literal` 化或复用已有 `StrEnum`（`api/schemas.py:30-31/64/68/176/190/306-308`；源头枚举都已存在）。
4. `refused_by` 提升为 `AnswerResponse` 一等字段并用 `Literal` 枚举九个取值。现在系统故障（`model_error`/`no_model`）与治理拒答**一样走 HTTP 200 + `tier=refused`**，唯一区分依据埋在无类型的 `provenance` 里。
5. `CapabilitiesResponse` 每个字段加 description —— `export_openapi.py` 从合成 stack 生成，spec 里只有形状没有取值，description 是表达「`can_search` 恒 false」「`can_stream` 随部署变」的唯一路径。同时删掉 TOML 里的 `can_stream` 键让它无法被手写。
6. `/chat` 的 description 写明：无状态回退路径、历史由调用方重发并卡在 100 轮 / 每条 8000 字符、主路径是 LangGraph thread、**`history` 是不受信输入**（`api/app.py:493-495` 无任何校验，`n_human` 由客户端 history 算出并用于 `:498` 的审计行 upsert）。顶层写明读路由无鉴权。

**碰哪些文件**　`api/app.py`、`api/schemas.py`、`api/stack.py`、`governed_bi.toml`、`governed_bi.local.toml`、`docs/openapi.json`。

**怎么验证**　`export_openapi.py --check` 绿；一个测试遍历路由断言 `responses` 键集合与代码实际抛出的状态码一致；`components.securitySchemes` 非空。

#### 5.3.9 · provenance 可依赖子集与版本声明

**改什么**　实测一条 happy-path turn 的 wire provenance 有 **49 个键**，`analyst/run_log.py:57-80` 的 `METADATA_PROVENANCE_KEYS` 只声明 22 个，`tests/test_run_log.py:122` 只做 `issubset`，wire 上是 `additionalProperties: true`。**版本信号本身不缺**（`corpus_release_hash`、`corpus_pin`、`serve_config_hash`、`prompt_set_hash`、`model`、`data_split` 都已在 wire 上），缺的是声明。

1. 契约文档点名列出**可依赖的约 10 个键**，其余明确标为「内部，随时可变」。给 provenance 加 `provenance_schema_version`。**不做 49 个键的 pydantic 建模。**
2. 逐键决定哪些不该发给匿名客户端。实测 wire 上带 `data_split`、`export_allow`、`producer`、`prompt_variants`（提示词变体名）、`model`、`cost_est_usd`、`token_usage`、`latency_ms`、`user`、`corpus_release_hash`（**= 后端源码精确 commit sha**）。`export_allow` 本身是「这条记录能否离开运营边界」的治理元数据，**正在每轮离开运营边界**。
3. `serve_config_hash` 的输入清单（`provenance.py:84-102`）文档化 —— 它是唯一能表达 `grade_semantic_failures` 这类「改语义不改形状」开关的信号（见 2.4）。
4. 版本策略：`pyproject.toml` 与 `__init__.py:14` 的 `0.1.0` 自初始提交 `7627eb2` 未动，无 CHANGELOG。定下：契约变更必须 bump `__version__`；CI 加「`docs/openapi.json` 变了但 `__version__` 没变则失败」；新建 `docs/api-changelog.md`；`GET /capabilities` 增加 `api_version` 与 `git_commit`。
5. `runs.sqlite` 加 `record_schema_version`（`run_log.py:546-551` 是单列 JSON blob，实测 757 行混三种 producer、键并集 31）。**只加版本字段，不建字段注册表。**

**碰哪些文件**　`analyst/run_log.py:57-80/546-551/959-968`、`provenance.py:67-107`、`viz/presenter.py:812-842`、`api/schemas.py`、`__init__.py:14`、`pyproject.toml`、新增 `docs/api-changelog.md`、`.github/workflows/ci.yml`。

**怎么验证**　`GET /capabilities` 返回 `api_version` 与 `git_commit`；改一次 `api/schemas.py` 而不 bump `__version__`，CI 变红；一个测试断言 wire provenance 里所有键要么在「可依赖」清单里、要么在「内部」清单里。

#### 5.3.10 · 对外契约文档

**改什么**

1. **抓运行时的合并 spec 当 artifact。** 部署后 `GET /openapi.json` 返回的**不是** `docs/openapi.json`：`langgraph_api/api/__init__.py:100-107` 把它列为 `unshadowable_meta_routes`，`api/openapi.py:100-102` 再 `merge_openapi_specs(平台 spec, 我们的 spec)` —— 平台 49 条 + 我们 12 条 = 61 条路径，`info` 以我们的为准，所以整份 spec 被贴上 `title="governed-bi API"` / `version="0.1.0"`。新增 `scripts/export_runtime_openapi.py` 落盘 `docs/openapi.runtime.json` 并加 `--check`。
2. **新建 `docs/stream-contract.md`**，四节（`custom`/`values`/`messages`/`interrupt`），主体由 5.3.7 的生成器产出。另加六节手写：
   - **终止形态三种**：`final/ok`、`final/refused`、流异常结束且无 `final`（`graph_app.py:159-160` 的 `RuntimeError("database unavailable")` 是唯一逃逸到流上的路径，同一故障在 REST 是 `app.py:505` 的 503）。
   - **没有 token 流式**：`messages` 通道整轮只有 1 帧。配实测延迟 —— 20260730 那次跑（n=1351）**p50 10.8s / p90 21.2s / p95 29.2s / p99 69.3s / max 141.5s，21 次超 60s**；预算是步数不是墙钟（`middleware.py:43` `AGENT_RECURSION_LIMIT=40`）。REST 纯阻塞无心跳，流式有 SSE comment 心跳（`onmessage` 看不到）。给出网关超时下限建议（≥180s）。
   - **两条传输对照表**：命令、端口、有无 `/threads`、有无 HITL、历史归谁、鉴权、错误形状。明说主路径是 LangGraph thread、REST `/chat` 是无状态回退。
   - **turn 身份归客户端**：`turn_id = f"{thread_id}:{n_human}"`（`provenance.py:43-49`），`thread_id` 客户端可任选（`graph_app.py:79` 缺省 `"default"`），`run_log` 以 `turn_id` 为主键 upsert —— **同 `thread_id` 的两个客户端共享 working memory 并互相覆盖持久 turn 记录**；且每轮新生成 `run_id`，中断恢复后落盘记录的 `run_id` 与中断前流上的对不上。
   - **HITL 的剩余缺口**：clarify checkpointer 是进程内内存（`api/stack.py:205-212`），重启即丢；无服务端超时；无取消/过期 API。
   - **事件日志不可恢复**：`ChatState` 只有 `{messages, answer}`，没有任何通道持有事件日志，`custom` 流是 fire-and-forget。刷新页面 / 断线重连 / 中途加入，rail 时间轴在客户端侧无法恢复。
3. **`docs/ui-frontend-handoff.md` 整份重写**（590 行）。现在的体裁是给内部实现者的 build brief（§8 built-vs-planned、四处 build order、§13.3「what the backend still owes you」）。重写成接入指南：怎么起、走哪条传输、读哪份 schema、错误怎么处理、延迟预期、鉴权由谁负责。已知硬错随重写消失 —— `:409-410` 说 `No schema_route stream event exists`（`agent.py:758` 确实在发）、说 rail 发 `cache`（全仓无此 step），且五行后自相矛盾。
4. `docs/README.md` 的「Read in this order」加一条指向接入指南（现在 `grep -iE 'frontend|openapi|ui-' docs/README.md` 零命中）。

**碰哪些文件**　新增 `scripts/export_runtime_openapi.py`、`docs/openapi.runtime.json`、`docs/stream-contract.md`；重写 `docs/ui-frontend-handoff.md`；`docs/README.md`、`.github/workflows/ci.yml`。

**怎么验证**　`export_runtime_openapi.py --check` 绿；`grep -nE "cache|No .schema_route" docs/ui-frontend-handoff.md` 无命中；只读接入指南 + 两份 schema，用 5.3.1 的 replay 模式能跑通一轮。

#### 5.3.11 · 七个 P 项的落地（已定，见决定 16-17）

| # | 定了什么 |
|---|---|
| P1 | **宣告本仓不提供鉴权**，由部署环境承担；**同时删掉** `/chat` 与 `/corpus/edit` 上的共享密钥门（它只挡两条路，真正执行 SQL 的 `/threads`、`/runs/stream` 不经过它，留着是「已鉴权」的错觉）。契约文档列出必须由网关覆盖的完整路径清单。 |
| P2 | **`events.final` 移到 `narrate` 之后**，`final` = 回合结束。同时关掉 `open-work.md` 的 C19。`finalize_and_log` 的落盘时机一起挪，要确认异常路径不漏记。 |
| P3 | **读路由不加鉴权**，在契约顶层明写「读路由匿名可读，部署方自行在网关层加」。 |
| P4 | **不加 `/v1` 前缀**。只能给 61 条里的 12 条加版本，平台那 49 条（主路径）动不了；半个前缀传达错误印象。版本走 `api_version` + `git_commit` + `api-changelog.md`。 |
| P5 | **在图层封堵**（内层 runnable 打 tag 并过滤），不靠网关规则。理由：它绕过的是治理本身，而 ADR 0002 说治理靠拓扑不靠信任 —— 写在网关配置里、将来可能忘记同步的规则不是拓扑。 |
| P6 | **`corpus_release_hash` 保留原样**，可用作部署指纹。 |
| P7 | **`route` 事件保留并改名 `turn_start`**，与 1.4.3 的 `Stage.route` → `Stage.turn_start` 一致。契约写死 detail 恒为空对象。 |

CORS 一并落地：`langgraph.json` 加 `http.cors` 与 `[serve].cors_origins` 同源，或删掉内层那份让配置只有一处 —— 现在实际生效的是外层 `CORS_ALLOW_ORIGINS = "*"` 加 `allow_credentials=True`。

### 5.4 · 从 5.3 抽出来的三条真 serve bug（在 A 的关键路径上）

5.3 整块移出关键路径之后，这三条留下 —— 它们不是契约工作，是真缺陷（决定 22）。

**改什么**

1. **一个进程两份 `ServeStack`。**`api/routes.py:28` 在 import 时调一次 `build_stack()`，`api/graph_app.py:233` 又独立调一次，而 `api/stack.py:173` 没有 `lru_cache` —— 所以一个进程里两份 corpus、两份 `index_cache`、两套 clarify checkpointer。连带后果：`POST /corpus/edit` 写盘成功后本进程读不到（`api/app.py:459-468` 写完直接返回，从不刷新 corpus），策展客户端会看到「200 写成功 → 列表还是旧的 → 答案还是旧的」。
2. **narrator 的 token 归属互抢。**`analyst/agent.py:1396-1407` 从 stack 级**共享**的 `narrator._chat.last_usage_metadata` 读并清空，而 `narrator` 由 `build_stack()` 建一次。LangGraph Server 默认并发跑 run，**不需要开多标签页就能触发**。改成从调用返回值取 usage。
3. **guardrail 逐层判决落盘。**`eval/arms.py:480` 把 `governance_ledger` 折成 `ledger_len` 就丢掉了列表本身。这条同时是 0.3 收紧之后的**事后验证手段** —— 没有它，收紧了也无法验证 L4 到底有没有生效。

**为什么这三条在关键路径上**　前两条影响 serve 的正确性与 token 计量（后者直接影响成本核算，而成本是你自己算的）；第三条是 0.3 的验证前提。其余 5.3 子条目影响的是**契约面**，而 eval 不经过契约面。

**碰哪些文件**　`api/stack.py:173`、`api/routes.py:28`、`api/graph_app.py:233`、`api/app.py:459-468`、`analyst/agent.py:1396-1407`、`eval/arms.py:476-480`。

**怎么验证**　`build_stack()` 两次调用返回同一对象；`POST /corpus/edit` 之后 `GET /schema` 立刻可见；并发两个 run，两边的 `token_usage` 之和等于实际消耗；跑一题后 `generations.*.jsonl` 里能读到逐层判决列表。

---

## 阶段 6 · A

### 6.1 · 可追溯

> **2026-07-31 审计更正（S1）：原先写的三个字段里有两个已经存在，而加完第三个也 checkout 不出来。**这是全份计划里唯一一条「按自己的验收标准执行会当场失败」的条目。

**改什么**　上次跑不可复现：manifest 记的 `git_sha 3f599b6` 在本地找不到（`git cat-file -t` fatal），而且工作树当时是脏的（header 带 `+C11`）。**原因不是缺字段，是分支没推加工作树脏。**

已经存在、不要重复加：`created_at_utc` = `20260730T034553Z`、`completed_at_utc` = `20260730T053125Z`（相减正是 6.3 引用的 1h45m32s）；`git_sha` 由 `eval/metrics.py:422` 的 `corpus_release_hash()` 产出，而 `provenance.py:169-190` 读的就是 `.git/HEAD` —— **它已经是服务器分支的 HEAD**。

真正要做的三件：

1. **跑之前 `git push` 服务器分支。**零代码，是唯一真正让 hash 可 checkout 的动作。落点是 D1 的 runbook，不是这里。
2. **manifest 记 `dirty: bool` 与 `diff_sha256`**（或把 `git diff` 整份落进 run dir）。注意 `provenance.py:169-176` 的 docstring 明写「without `subprocess`」—— 这条约束要么破，要么自己重实现 index 比对。**它不是四小时的活**，定价时别按小改动算。
3. **只新增两个字段**：分支名、对应的 main hash。服务器上 internal proxy 代码在另一个分支，HEAD 永远不等于 main，两个都记是零成本的痕迹。

「不加闸门」保留，但接受推论：**不推分支的那次跑不进 quotable 台账。**

**怎么验证**　`dirty=false` 的那次跑，拿分支名加 hash 能 checkout 出可运行的代码；`dirty=true` 的那次，`diff_sha256` 能对上落盘的 diff。

### 6.2 · 修「臂的内容对得上」

> **2026-07-31 更正：第 1 条阻塞的描述是错的。**我写的「有 1 个 asset 指向不存在的东西」抄的是 `eval/index.py:836` 对**任何** corpus-validation finding 的硬编码通用措辞。实际 finding 已从 `summary.json` 逐字核出。

**改什么**　20260730 那次跑被判不可引用，两条原因：

1. **`always-note-budget` 假阳性 —— 而它已经在 `main` 上修好了（`0012dbe`，HEAD~4）。这一条从「修复」改成「验证」。**

   `summary.json` → `corpus_validation.curated_sme.findings` 逐字是
   `always-note-budget []: always-note summaries total 5178 characters; maximum is 2000`，其余三臂 `finding_count: 0`。那是一个 **per-turn 预算被拿去对 57 个 schema 的 pooled corpus 求和**，而最差的单 schema 是 1591/2000，build log 记 0 dropped。

   `0012dbe` 对 `corpus/validate.py` 是 +164/−27，现在按 turn scope 分组、空 scope 计入每组、逐组判定，docstring 逐字写着「The finding was false, and it cost a 1351-question run its quotable status」。

   > **原先写的「修法二选一」已删除。**第二个选项（抬高 `always_note_char_max`）会在一个已修好的地方白换掉一个 comparability key。
   >
   > 剩下唯一未修的是 **`eval/index.py:832-838` 的措辞** —— 它对任何 corpus-validation finding 都硬编码「assets that resolve to nothing cannot reach a prompt」，正是这句话把我误导成「悬空引用」。按 `finding.code` 分流文案。

2. `curated_sme` 在 `professional_basketball`、`synthea`、`works_cycles` 三个 db 上**一条都没折叠**，语料和 `curated` 逐字节相同（`fold_mode='none'`、`ledger_source='missing'`、`clarifications_applied=0`）。这三个 db 上的 SME 提升不是测出来的，是零。

3. **`sme_noop_dbs` 是抽奖式判据，本次接受。**`eval/index.py:452-456`：57 个 schema 里**任何一个**的 `sme_fold.identical_to_curated` 为真就进列表；`:843-851`：列表非空即写进 `not_quotable_because`。也就是**任意一个 schema 的 Phase A 提零个问题，整次付费跑就不可引用**。明写「每次跑都是抽奖，本次接受」—— **不要给它灌一个下限**，那是往 hygiene gate 里注水。

后果仍然成立：「SME 到底有没有用」这个结论用这次数据答不了 —— 而 6.3 的 MDE 预登记说明，**下一次跑也答不了**。

**新增：`claim_ready` 不是靠这两条解决的。**`eval/index.py:608` 是 `record["claim_ready"] = False` **无条件执行**，`CLAIM_READY_REQUIRES`（`:538`）七条里第二条是「serve-replicate noise floor measured and not drifted」。20260730 的 manifest 里 `replicate_of: None`，而 `--replicate` 默认就是 `None`。**清单里没有任何一条会让 `claim_ready` 变 true** —— 所以 6.3 必须带 `--replicate`，见那一条。

**怎么验证**　跑完后 `runs/index.jsonl` 里 `registry_ok`（原 `ledger_ok`）为 `true`、`not_quotable_because` 为空；`claim_ready_requires` 逐条对照，明确标出哪几条本次满足、哪几条（如 X7 的 `curated_sme` 双机制捆绑）永久不可满足因而只能走「bundles disclosed」路径。

### 6.3 · 跑

> **2026-07-31 审计更正（M3）：这一条原来只有耗时回顾，「改什么 / 碰哪些文件 / 怎么验证」三项全缺 —— 而它是整份计划的终点交付物。而且 6.2 写着「所以 6.3 必须带 `--replicate`，见那一条」，指过来却什么都没有。**

**改什么**

1. **完整命令行**，含 `--replicate`、`--workers`、`--model`、`--dbs`、`--split`。多一次 serve pass 的成本是明码的：`run_datalake.py:5109-5116` 的 help 自己写着「Costs one extra serve pass」。完整命令的归宿可以是 D1 的 runbook，但 6.2 ↔ 6.3 那句互指必须闭合。

2. **预登记 MDE，并且把结论写硬。**实测噪声底线：31 对 `context_hash` 逐字节相同的问题里有 **4 个 `correct` 翻转（12.9%）**，全量不一致 **122/1351 = 9.03%**，成对 SE = 0.0082 → 80% power 的 **MDE ≈ 2.3pp**。而争论中的 SME 步长是 **−0.15pp**。

   > **在这个噪声底线下，没有任何负担得起的 N 能分辨 0.2pp。**`--replicate` 只让你有资格说「**未检出**」而不是「无效果」—— 它不会让 SME 变成可检出。这句话必须写在这里，否则 1.62 亿 token 花完还会有人以为答了 SME 那个问题。

3. **零题 schema 守卫**（从 `eval-rebuild.md` §4 搬来）：重筛后某个 schema 落到 0 题，不能算 built-but-unscored，也不能弄坏 pool census。

**碰哪些文件**　`docs/experiment-runbook.md`（命令）、`eval/run_datalake.py`（零题守卫）、本条（MDE 预登记）。

**怎么验证**　把 `CLAIM_READY_REQUIRES`（`eval/index.py:538`）七条逐条勾选，明确标出哪几条本次满足、哪几条不满足。注意 **`record["claim_ready"] = False` 在 `:608` 是无条件的**（上一行注释：`Never auto-computed: claim readiness needs the runbook checklist`），所以跑完它仍然是 `False` —— 这是设计，不是缺陷。第六条自带 `or bundles disclosed and not quoted as one mechanism` 逃逸分支，X7 的 `curated_sme` 双机制捆绑走这条路。

**规划基线**（20260730 实测）：57 db / 1351 题 / 4 臂 = 5404 turn，20 并发，构建 23 分 49 秒，四个臂 serve 分别 26'52" / 21'15" / 16'35" / 16'52"，总 1 小时 45 分 32 秒，1.62 亿 token，0 crash。**带 `--replicate` 要在此之上再加一个臂的 serve 时间。**

---

## 阶段 7 · 四个测量臂

A 之后。这四个是原 build-sequence Phase 2 的内容，重构清单时漏了，补回来。它们互不依赖，可以按需挑。

### 7.1 · 多轮臂

**改什么**　这个仓库产出过的**每一个数字都是 turn 1**（`arms.py:417`：每题各自 mint 一个 `run_id`）。而多轮路径有一个治理形状的结构缺陷：`refuse_gate`、schema routing、`retrieve` 三步都跑在**未解析的原问题**上，history 只在 `assemble_context` 才进来；AUDIT S4 又把 `inspect_schema` 限定在 `routed_schemas` 内，所以误路由之后 agent 没有自救路径 —— **一个正确的追问会被拒答，而导致它的控制正在正确工作**。

数据来源用**合成代词 follow-up**：对每个 BIRD 题目产出两轮会话，turn 1 是基于 gold 表的通用开场，turn 2 把实体名**机械替换**成「那些 / 它」。gold SQL 不变，所以评分与 leakage 处理完全不动。诚实边界要划死：**替换必须是机械的**；一旦让 LLM 自由改写，测的就是我们自己的生成器。

前置：`retrieval_eval.py` 要能对一个 session 打分（现在 `:194` 只吃 `item.question`）；`InMemoryWorkingMemory` 两个构造点都要传 `max_turns`；comparability gate 加 `turn_depth` 键，否则两轮跑会和单轮跑比作同一配置。

**这一项是 feature 扩张的 gate**（见非目标一节）。

### 7.2 · pooled-valid 的 out-of-scope 负例集

**改什么**　`eval/refuse_gate.py` 的打分器已经建好并单测过，**没有任何 driver 调它**，原因写在 `eval/__init__.py:37`：它原来接的跨库负例集在 pooled 之后失效（X6 —— pooled 跑里其他 schema 都在池子里，那些题变成**可答**的，指标会把每个正确答案判成拒答失败）。

所以诚实的说法不是「拒答门未测试」，而是：**这个系统的拒答召回从未在 pooled 规模上量过，瓶颈是负例集不是机器**。而拒答是这个产品的一半。

需要的是对**整个池子**都 out-of-scope 的题：每个 schema 都没有的实体；需要 lake 不携带的数据（实时、外部）；需要一个没有任何 schema 声明的 join —— 最后一类最划算，因为 `detect_missing_join_path` / `missing_edge_refusal` 已经实现了那种拒答，而且同样没被测过。

### 7.3 · red-team 臂

**改什么**　四个家族：(i) 对整个池子 out-of-scope；(ii) 作用域逃逸（直接点名未授权的 schema / 表）；(iii) prompt injection（指令覆盖、系统提示词提取、上下文转储）；(iv) 会话式规避负例（需要 7.1 的多轮装置 —— `_match_negative_example` 只匹配**当轮原文**，所以把禁止的问题拆成追问就绕过了，这不需要漏洞，这是人正常说话的方式）。

**关键断言不是一个比率，是一个不变量**：这个臂里**没有任何一条 ledger 记录执行了触及该轮授权集之外的表的 SQL**。一次违反就是一个发现，与百分比无关。

leakage 注意：对抗题集必须像 gold 一样对策展保密，否则 curator 会照着它写负例，臂测的就是记忆。

依赖 0.3（先知道 graded delivery 那条路修没修）与 7.2。

### 7.4 · metadata-说谎臂（原 drift 臂）

**改什么**　动机是「**corpus 说的和数据库有的不一致时，系统怎么表现**」—— 拒答、答错，还是耗尽步数走 graded delivery。与 decoy 那条线并轨，不是「防漂移」（我们没有 production，那是为假想部署做工）。

数据免费：`BIRD-Data-Obfuscation` 已经有 rename map 和重命名过的 Postgres，**一份 rename map 就是一次受控的 metadata 说谎**。把按 names-A 建的 corpus 指向 database-B，每个资产都以完全已知的方式说谎。

**`cs_semester` 与 `ice_hockey_draft` 是 identity-rename（名字没变），自动成为无漂移对照组** —— 这一点要写进设计，不要留给实现者发现。再加一档部分说谎（重命名 20% 的列）测真实情况：大部分 corpus 是对的，少数几个资产在说谎。

两个产出：检查本身的召回（`validate_corpus(connector=...)` 能不能找全）、以及说谎的代价（绕过检查时 serve 怎么表现）。

依赖 1.7（`corpus doctor`）与 1.8（`drift` 进 `error_taxonomy`，否则丢列和幻觉列是同一类错误，说谎会被读成模型退化）。

---

## 非目标与 gate

记下来，避免被当成遗漏重新提出。

**非目标 —— 已决定不做**

- **跨轮 licensing**（把上一轮已授权的表并进本轮）。它等于把 agent 上一轮的自授权结果延长一轮，而 AUDIT S4 收窄的正是这个。多轮恢复靠 3.12 / 3.13，那两项不扩大作用域。见决定 19。
- **两个 eval driver 合并**。已推迟；0.1 是退役 `run_experiment`，不是合并。
- **拆分 `guardrails.py` / `validate_corpus` / `middleware.py` / `llm/` 协议**。四个都已经是深模块，`test_guardrails.py` 与 `test_presenter.py` 零 monkeypatch 就是回报。
- **重建语义 SQL 缓存**。删掉是对的，在有真实查询分布之前重建是过早。
- **图数据库（AGE / Neo4j）**。X.5 的 A7 记录了它的完整代价；唯一的残留（term 关系层级）应该挂在已有的 `graph/planner.py` Steiner planner 上，不新起一张图。
- **四条观测通道（Kafka / Prometheus / facade）**。两个 tracer 加 ledger 回答同样的问题，少三个服务。
- **RLS / CLS / PII 分级**。本仓范围外，已记录。
- **重开 ADR 0002（agentic core 是唯一 serve 路径）**。注意 ADR 0003 不在此列 —— X.5 的 A0 若要动 RRF，就必须修订它。
- **持久化 retrieval 索引**。只有测出冷启动成本才值得，而做了就引入我们现在没有的失效面。
- **DeepAgents `skills=` 给 curator 指令**。要测才知道，而测一次就要一次 rebuild。

**Gate —— 不是不做，是等一个信号**

- **feature 扩张** → 等 7.1（多轮臂）出数字。在一条未测量的多轮路径上设计 feature 是猜。
- **列级检索单元（A2 / 原 3.17）** → 等三件事：X1（长度匹配的 placebo 臂）、`retrieval_eval` 扩出列级召回与 prompt 尺寸、以及一份 assurance 分布基线（改索引单元会改词表，而词表经 `lexical_coverage` 独立地移动每一道题的 `semantic_assurance`）。
- **A0 RRF 存废** → 等 `retrieval_eval` 加上真 embedder。`HashingEmbedder` 不是弱语义通道，是比 BM25 更弱的**词法**通道（实测 `cosine('revenue','earnings')=0.0`），用它做的 A/B 说明不了问题。
- **A5 term binding 向下游传播** → 等 A0 有结论（两者都动 `RetrievalResult` 的下游）。
- **A6 D 的自积累回路** → 等交互信号落地（现在没有 `record_success` / `approve` / `review_status` / `fail_count`）。

---

## 文档，跟着走

**D1 · 重写 runbook。**`docs/plans/experiment-runbook.md` 有六处数字是错的：写 69 db / 2030 题（实际 57 / 1351，差 35%）；写 `--limit-dbs 3` 选中 `address, airline, app_store`（实际是 `address, airline, authors`，`app_store` 已经不在 split 里）；写试点 166 题（实际 135）；写孪生率 182/1627（实际 115/1200）；写「这个仓库从未用模型跑过完整 split」（20260730 跑过了）；`--pg-dsn` 默认值写的是带密码的，`run_datalake` 实际读 `GOVERNED_BI_PG_DSN` 且不带密码。能从代码生成的部分就生成，别手写。

**D2 · 关掉 `open-work.md` 的 C10。**它描述的问题已经修好了：`curator_trace.jsonl` 和 `curator_sme_trace.jsonl` 现在都在 `run_datalake.py:212-224` 的 `_SIDECARS` 里，实际产物里也都在。

**D3 · glossary 补 45 个承重术语**（随 1.4 一起做）。

**D4 · 修 `docs/open-work.md` 的失准指针。**`:41` 的 C19 指向 `analyst/agent.py:1265-1268`，而成功路径的 `events.final` 实际在 `agent.py:1367`。按 debt-audit 的结论 `open-work.md` 是「剩余工作」的 canonical 来源，全文的 `file:line` 指针过一遍。验证：每个指针都能在当前 HEAD 上找到它描述的东西。
