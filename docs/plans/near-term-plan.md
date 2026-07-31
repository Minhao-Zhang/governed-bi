# 近期计划：第一批实现

2026-07-31 立。分支 `impl/rebuild-first-batch`。

> **语言：简体中文，无英文孪生。**文件名不带 `.zh` 是刻意的 —— `.zh.md` 那个后缀宣称「我是某份英文文档的中文孪生」，而这份没有英文源头。同目录的 [rebuild-checklist.md](rebuild-checklist.md)、[rebuild-decisions.md](rebuild-decisions.md)、[grill-agenda.md](grill-agenda.md) 同为中文原生。

## 这份文档是什么

[rebuild-checklist.md](rebuild-checklist.md) 有 55 个标题、约 180 个离散工作单元、触及约 157 个文件。那是**全案**。这份是从里面切出来的 **17 项**，切的判据只有两条：

1. **能外派** —— 改什么、碰哪些文件、怎么算做完，三件事都能写死到不需要回来问设计意图。
2. **有机器可查的验收** —— 一条命令或一个测试能判定「做完了」，不靠人读代码下结论。

切掉的不是不做，是**不在这一批**。见文末「近期明确不做」。

**分工**：实现全部外派给 junior engineer。**我只做 code review**，不动键盘写实现。**跑实验不外派** —— 那要花钱，由你在服务器上跑（N17 只交付命令与守卫，不交付「跑完了」）。

## 终点

三句话，缺一条这一批就没完成：

1. **已发表的数字不再是错的。**graded delivery 那条越权路径关上，且关上之后**能事后验证它确实关上了**。
2. **服务器上那一小时四十五分钟看得见。**每题有进度、每行有时间戳、`run_id` 能把 Langfuse trace / `stage_events.jsonl` / 日志文件拼回同一批记录。
3. **拿到第一份 quotable 的跑。**`runs/index.jsonl` 里 `not_quotable_because` 为空，并且报告里的每一个数字都能用仓库里的工具重现出来 —— 不是临时脚本。

`claim_ready` **不在**终点里。`eval/index.py:608` 是 `record["claim_ready"] = False` 无条件执行（上一行注释：`Never auto-computed`），这是设计。终点是 quotable，不是 claim_ready。

---

## 里程碑

五个里程碑，按依赖链排。**M1 与 M2 可以并行**（两批文件不重叠）；M3 必须在 M4 之前（两者都改 `run_datalake.py` 的 argparse 与日志区）；M5 最后。

| # | 出口判据（机器可查） | 条目 | 估工 |
|---|---|---|---|
| ~~**M1**~~ **已完成 2026-07-31** | 未授权基表的用例修复前红、修复后绿；`generations.*.jsonl` 里读得到逐层判决列表 | N1–N4（`e94a133` / `af7fd37` / `6c4e709` / `db21779`） | 3.5 人日 |
| ~~**M2**~~ **已完成 2026-07-31** | 三个重复名字各只剩一处定义（`_render` 是例外 —— 三者无关，改名不合并）；歧义裸名返回 `None`；约束真的会绑（把 `langgraph-api` 改成 `>=99` 后 `uv lock` 判定 unsatisfiable）；glossary 补出 ops/eval 半区 | N5–N8（`14d8172` / `8be261f` / `71aabfb` / `db6704d`） | 4 人日 |
| **M3** 删双轨 | `grep -rn "run_experiment\|skip_agent\|git_sha_drift" src/` 零命中，`pytest` 全绿；rvgd ↔ `table_by_name` 歧义一致性测试绿 | N9–N10a | 2.5 人日 |
| ~~**M4**~~ **已完成 2026-07-31** | 付费 5 题跑 `runs/datalake/20260731T195022Z`（`gpt-5.6-luna`，工作树干净，分支已推）：stdout 23 行；**5 个 `run_id` 在 generations / `stage_events.jsonl` / `run.log` 三个 sink 里集合完全相同**；manifest 四个字段齐（`git_branch` / `main_git_sha` / `dirty` / `diff_sha256`）；M1 遗留的投影 ledger 端到端结清（无 `result`） | N11–N14（`526f21a` / `477b453` / `afe7776` / `099833a` / `ec7be1c`）+ 四条修复（`d8e67c6` / `c9b1c19` / `c6c74b1` / `d29fb16`） | 7.5 人日 |
| **M5** 工具与跑 | 用 20260730 那份数据重现出 `docs/experiments/` 报告里的**每一个**数字；带 `--replicate` 的完整命令行成立 | N15–N17 | 5.5 人日 |

估工是我的一阶估计，不是从 checklist 顶部 A-7 那个「235–435 工时」拆出来的 —— 那个数建在一批已被审计打回重算的计数上（M5：`pooled 405→205`、`run ~330→1488`）。接手的人第一天就该自己重估，估完不一致以他的为准。

---

## M1 · 让已发表的数字不再是错的

对应 checklist [0.3](rebuild-checklist.md) 的第 2–4 步与 [5.4](rebuild-checklist.md) 第 3 条。第 1 步（回溯查询）已完成，结论是**越权发生过**：20260730 那次跑，5404 行里 `graded_delivery=True` 恰好 1 行，`routed_schemas=['regional_sales']` 而 `tables_used=['tbl_address_country','tbl_address_zip_data']` —— 一张都不在授权集里，`correct=True`，静默计入了 EX。

### N1 · 未授权基表的用例（先红）

**改什么**　往**已有的** `tests/test_graded_delivery_cap.py` 加一个用例。不要新建文件 —— 那个文件已经有 `FakeToolModel` + 真 corpus + sqlite 的完整装置，抄它的 `EXCLUDED_SQL` 那个形状就行。

用例把一轮驱动到 `coverage_best_effort`，最终 SQL 触及**未授权 schema 的表**，**且带列引用**（照 `train_5163` 的形状）。断言结果是拒答，不是交付。

**为什么必须带列引用**　原计划写着「攻击面限于不含列引用的 SQL」，**那句是错的**。那条真实 SQL 满是 `T1.population_2020` 这样的列引用，L3 一次都没触发 —— 因为 `gateway/guardrails.py:82-106` 的 `column_allowlist(corpus)` 遍历**整个** corpus 建 allowlist，而 `analyst/agent.py:434` 传的是池化后的 corpus。**pooled 配置下 L3 是一张 57-schema 的通行证。**不带列引用的用例会因为错误的原因通过。

**验收**　用例在 N2 之前必须**红**。提交时附上失败输出。

**review 会挂在哪里**　用例通过了但 N2 还没做 —— 说明它测的不是这件事。

### N2 · 收紧复检（后绿）

**改什么**　`analyst/governance.py:700` 的复检传 `allowed_tables`；`analyst/governance.py:115-119` 里 `term_semantics` 的豁免限定为「不含未授权基表」。

**碰哪些文件**　`analyst/governance.py:115-119` 与 `:695-716`；`gateway/guardrails.py:775` 那句无条件的 fail-closed 承诺、以及 `column_allowlist` 的 docstring —— 两处都要把「L3 在 pooled 下不按 routed schema 收窄」这条不对称**写死在注释里**，不许留成口头知识。

**验收**　N1 转绿；`pytest tests/` 全绿；`test_graded_delivery_l3_hard.py` 与 `test_graded_delivery_cap.py` 原有断言一条不改。

**禁止**　不要顺手改 `grade_semantic_failures` 的默认值。它 `config.py:253` 默认 `False`、`run_datalake.py:4166` 设 `True`，这个不对称是 2.4 的对象，不是这一项的。

### N3 · guardrail 逐层判决落盘

**改什么**　`eval/arms.py:476-480` 把 `governance_ledger` 折成 `ledger_len` 就把列表本身丢了。让列表落盘。

**为什么在 M1 里**　**这是 N2 的验证前提**。不落盘，收紧了也无法事后证明 L4 到底有没有生效 —— 而这一批的终点第 1 条要求的正是「能验证它确实关上了」。

**验收**　跑一题（`run_datalake --dbs beer_factory --limit 1`），`generations.*.jsonl` 里读得到逐层判决列表。

> **2026-07-31 处置：落盘要投影，不要原样。**ledger 的 `pass` 条目带着 `"result": serialize_result(result)` —— **整份查询结果行**，而 pooled eval 是 `Gateway(connector, max_rows=200_000)`。原样落盘有两个后果：artifact 体积失控，以及 `run_datalake.py:941` 的 `json.dumps(row, ensure_ascii=False)`（**没有 `default=`**）会在第一个 `Decimal` / `bytes` 上抛 `TypeError` —— Postgres 的 `numeric` / `date` / `bytea` 和 sqlite 的 BLOB 都是。N3 之前不可能发生，因为 ledger 被折成了一个 int。
>
> 落盘的是 `_ledger_for_artifact` 的投影：只留 `action` / `verdict` / `layer` / `sql` / `allowed` / `row_count`。
>
> **这条真跑的验收没做**（要花钱，且 `--skip-agent` 不产 ledger）。单测 + 一个用真 `Gateway` 打 sqlite 再注入 `Decimal` / `bytes` 的序列化测试已覆盖生产数据形状，**把端到端那次挪进 N12 的 5 题小跑一并验**，不单独为它付一次费。

**注意**　落盘的键名先叫什么无所谓，**不要**在这里做 `ledger` → `guardrail_log` 的改名。落盘字段改名归 checklist 4.1，那一项不在这一批里；现在改会让 N15 读不了旧数据。

### N4 · L3 收窄的判断（spike，交一页结论不交代码）

结论页：[l3-allowlist-narrowing.md](l3-allowlist-narrowing.md)。

**改什么**　评估 L3 的 allowlist 是否也该收窄到 routed schema。这条是 N1 暴露出来的独立缺陷，不在原计划里。

**交付物**　一页判断，写清三件事：收窄之后哪些现有测试会红、pooled eval 的 EX 会不会因此掉、以及「不收窄」这个选择要写进哪份文档。**不要直接改。**

**review 会挂在哪里**　交上来一个 diff 而不是一页结论。

---

## M2 · 零风险收敛

> **详细工作单：[batch-m2.md](batch-m2.md)。**下面四节只给目标，那一份给做法 —— 并且更正了本节的**六处事实错误**（`agent.py:465` 是错的行号、`_render` 三处不是同一件事、要判断的是 `_slug` 不是 `_render`、`Corpus.concat` 不需要、glossary 缺的是「运维词」不是「45 个词」、`langgraph-api`/`langgraph-sdk` 根本不是直接依赖）。**照本节原文做会踩到其中至少三处，以 batch-m2.md 为准。**

四项互不依赖，可以四个人同时开。**N5 必须在任何术语类改动之前** —— 否则后面的人在一个自己都没定义清楚的术语表上改名。

### N5 · glossary：六条同音词警告 + 45 个承重词

**改什么**　`docs/glossary.md` 是一张 Markdown 表（77 行，40 个词条）。加两批行。

第一批是**六条陷阱**，一条都不能少：

| 词 | 要写清的事 |
|---|---|
| `graded_delivery` | 是 de**graded**（降级交付），不是「已打分」。`grade`/`grader`/`gradeable`/`hash_grade` 在 src 里 219 次全指「对着 gold 打分」。全仓库最糟的同音词 |
| `safety_clearance=False` | **不等于**不安全。只过了 L1–L3、栽在语义层的 SQL 也是 `False`（`answer.py:236`） |
| `semantic_assurance=unflagged` | **不等于**已验证正确（glossary 现有条目已经在否定它，但没有独立词条） |
| `ledger` | 四个互不相关的意思，527 次，glossary 里 **0 次** |
| `stamp` | 四个意思，210 次 |
| `scope` | 五个意思：note 附着范围 / L4 授权表集 / 图视窗 / 工具可调用范围 / 一次跑评了哪些题 |
| `tier` | 三个意思 |

第二批是 **45 个高频承重词**，一个都不在现有表里：`pooled`、`verdict`、`db_id`、`resume`、`suspect`、`outcome`、`budget`（四种不同的 cap，要分开写）、`driver`、`licensed`、`solver`、`quotable`、`twin`、`headline`、`crashed` 等。另外 `arm`(1278) 与 `rung`(319) 现在只出现在**「已退役词汇」那一段**里，作为整个实验设计的核心单位却没有独立定义 —— 补上。

**只写现状，不做改名。**这一项是描述性的：每个词条写「代码里它现在是什么意思」。改名归 checklist 1.4，不在这一批。

**碰哪些文件**　`docs/glossary.md`。**`docs/glossary.zh.md` 本次不动** —— AGENTS.md 写着工作进行中只改英文，让孪生漂移。

**验收**　45 个词每个在表里有一行；六条陷阱每条能被 `grep` 到；`pytest tests/test_repo_contracts.py` 绿。

### N6 · 三个小函数各收敛成一份（checklist 1.1）

**碰哪些文件**
- `_FROZEN_GOLD_RE`：`eval/analysis.py:50`、`eval/run_datalake.py:196`、`eval/sql_diff.py:195`
- `_slug`：`curator/asset_bag.py:38`、`curator/profile.py:31`、`curator/seed.py:21`
- `_render`：`analyst/context.py:363`、`analyst/governance.py:259`、`eval/sql_diff.py:446` —— **这三个未必是同一件事**，先读再决定合不合。合不了就在 PR 描述里写「不合，因为……」，那也是合格交付。

**验收**　每个名字在 `src/` 里只有一处定义；`pytest` 全绿。

### N7 · `Corpus.table_by_name`（checklist 1.2）

**改什么**　「先按 id 查，查不到按物理名查」这段逻辑有三份拷贝，都取 `corpus.assets` 里的第一个匹配。`retrieval/rvgd.py:530-538` 已经有正确实现（歧义时返回 `None`，注释写明了理由），另外三处没用它。收敛成一个 `Corpus.table_by_name`，接受限定名（`schema.table`），裸名歧义时返回 `None`。

**为什么不只是重复**　BIRD-corpus 上实测 731 个表资产里有 **67 张表卷进裸名歧义**，涉及 **27 个歧义名字**（`pais` 五个、`kunden` 四个）。命中时 agent 会收到「`tbl_beer_factory_kunden`: not licensed this turn」—— 泄露一个它从没提过、且在其路由范围之外的表名，还可能死循环到步数上限，最后记成 agent 失败。

**碰哪些文件**　`analyst/tools.py:38`、`analyst/middleware.py:118`、`analyst/agent.py:465`、`retrieval/rvgd.py:530-538`。同时需要一个 `Corpus.concat` 构造器，否则 pooled 路径上索引会过期。

**验收**　测试按**那 27 个歧义名字**构造（不是 67 —— 67 是被影响的表数，按表数构造会重复测同一条路径）；四个调用点都返回 `None` 而不是第一个匹配；限定名 `schema.table` 四处都能解析。关掉 `docs/open-work.md` 的 C13。

### N8 · 锁死线协议依赖的版本范围（checklist 1.5）

**改什么**　主通道的线协议**由传递依赖拥有**。`uv.lock` 锁的是 `langgraph-api 0.11.0` / `langgraph-sdk 0.4.2` / `langgraph 1.2.8`，而 `pyproject.toml` 的约束是 `langgraph>=1.0`、`langgraph-cli[inmem]>=0.2` —— 一次 `uv sync -U` 就能换掉 `/threads`、`/runs/stream`、`stream_subgraphs` 的行为，而本仓 diff 里什么都看不到。给这三个包加上界。

**验收**　`uv sync -U` 之后 `uv.lock` 里三个包仍在声明范围内；一个测试解析 `pyproject.toml` 里的范围并断言。**声明范围写进 `docs/` 的哪一节留给做的人定**，但必须有一处文档写着它 —— 只改 `pyproject.toml` 不算做完。

---

## M3 · 删双轨

> **详细工作单：[batch-m3.md](batch-m3.md)。**下面两节只给目标。那一份加了本节没说的两件事：**`skip_agent` 不是一个 flag** —— 它同时是 `Metric` 注册项、manifest 字段和 `RESUME_DRIFT_KEYS` 成员，删它要 bump `MANIFEST_SCHEMA_VERSION`；以及 **`--skip-agent --oracle oracle_sql` 那个零成本 grader 自检去哪，必须在动手之前决定**（runbook 的 step 0 和 step 1 整个建在它上面）。**估工 2.5 → 3 人日。**

依据决定 12：**跑实验的人是有智慧的**，不给操作员建拦手滑的闸门。注意这条**管不到**数据读取的向后兼容（见 N15 的说明），把它扩张成「不许有任何向后兼容」是范畴错误。

### N9 · `run_experiment.py` 退役（checklist 0.1）

**改什么**　删掉单 db driver（1118 行）。它唯一独有的能力是「只跑一个 db」，而 `run_datalake --dbs <db> --limit N` 完全覆盖，还多给 `stage_events.jsonl`、serve 断点续跑和真实退出码 —— `run_experiment` 自己永远返回 0，哪怕台账判定不合格。

**碰哪些文件**　`src/governed_bi/eval/run_experiment.py`；`tests/` 里针对它的测试；`docs/plans/experiment-runbook.md`、`docs/plans/eval-rebuild.md`、`README.md` 里的命令引用。`docs/open-work.md` 的 E4 随之关闭。

**验收**　`grep -rn "run_experiment" src/ tests/ docs/ README*.md` 只剩历史记录；`run_datalake --dbs beer_factory --limit 5` 跑通并产出 `stage_events.jsonl`。

### N10 · 删 `--skip-agent` 与 drift 双轨（checklist 0.2）

**改什么**　`--skip-agent`、`--allow-git-sha-drift`，以及 `_check_resume_manifest` 里「付费跑致命 / smoke 跑只警告」那套双轨判断，全删。**resume 的一致性检查保留** —— 它防的是两套配置混进一份 artifact，不是防手滑。

**文件清单（2026-07-31 实测，取代 checklist 里 M7 给的「42 处 / 6 个文件」）**

```bash
grep -rn "skip_agent\|skip-agent" src/ --include=*.py -c | sort -t: -k2 -rn
```

命中 **75 处 / 8 个文件**：`eval/run_datalake.py` 35、`eval/index.py` 16、`eval/run_experiment.py` 11、`eval/metrics.py` 6、`eval/harness.py` 2、`curator/pipeline.py` 2、`curator/clarifications.py` 2、`stages.py` 1。**做完 N9 之后剩 64 处 / 7 个文件。**所以这一项**必须排在 N9 之后**，否则那 11 处白改。

**验收**　`grep -rn "skip_agent\|skip-agent\|git_sha_drift" src/` 无结果。`tests/test_eval_index.py:516` 是一个**集合等式**，删的那一刻就 `AssertionError` —— 修好它、并且 `pytest` 全绿，是这一项的真闸门，不是形式检查。

**动手前先确认一件事**　`--skip-agent --oracle oracle_sql` 现在被用作「grader 上限自检」，它确实不花钱。删之前确认这条自检有替代路径，或把它保留成一个**独立命令**而不是一个全局 flag。这个判断写进 PR 描述。

### N10a · rvgd ↔ `Corpus.table_by_name` 歧义一致性（M2 遗留）

**为什么单独开**　N7 把 tools / middleware / agent 收到 `Corpus.table_by_name`，但 `retrieval/rvgd.py` 里 `phys_to_table` 的内联策略原样还在（一趟 O(n) 建全量映射）。batch-m2 允许不调用 `table_by_name`（逐名去调会变 O(n²)），所以热路径不能硬合 —— 缺的是「两份会漂移」的钉。

**改什么**　一致性测试，约 20 行：同一个 corpus，断言 `table_by_name(bare) is None` **当且仅当** rvgd 的 `phys_to_table[bare] is None`（可抽 rvgd 建映射的那段为可测 helper，或在测试里复刻同一循环）。**不改**热路径语义。

**碰哪些文件**　`tests/` 新增（或扩 `test_corpus_table_by_name.py`）；必要时 `retrieval/rvgd.py` 只为可测性抽 helper —— 不许为了「调用 table_by_name」改成 O(n²)。

**验收**　合成歧义 corpus +（有则）BIRD 跳过式：两边对同一批裸名的 `None`/非 `None` 完全一致。

**排期**　与 N9/N10 同批交付即可；不碰它们要删的那些文件，无改动面冲突。

---

## M4 · 看得见与对得上

> **详细工作单：[batch-m4.md](batch-m4.md)。**下面四节只给目标。那一份更正了本节**七处事实**（tracing 是六个调用点不是八个，其中三个原文列的根本不调；narrator 那条行号漂到 `agent.py:1412-1422`；**`agent.py:700` 有同一缺陷的第二个实例，本节完全没提**；N13 的分支名其实已经被解析出来又丢掉了，近乎免费），并把顺序改成 **N12a → N11 → N13 → N14 → N12b**：**只花一次钱，一次 5 题真跑同时验四条**（含 M1 遗留的逐层判决端到端）。模型是 `gpt-5.6-luna`，配置已经对，但花钱前要先确认 —— `--model` 参数归 checklist 2.3，不在这一批。

### N11 · 实时可观测（checklist 5.2）

**改什么**　服务器上跑一个多小时，现在看不见任何东西：serve 阶段**每题零输出**，每个臂静默 16 到 27 分钟，连续四次；stdout 上**没有任何一行带时间戳**；构建阶段 20 个线程的日志交错、无 db 标签、会串行断行；结束时终端一次性吐 **50,716 行 JSON**（绝大部分是 `question_ids` 数组）；`run.console.log` 不是代码写的，靠操作员记得重定向。

对应改：每行加时间戳；serve 每 N 题打一行进度和 ETA（钩子已经在 —— `eval/parallel.py:180-183` 的 `on_result`，driver 只用它写盘，一个字不打）；构建日志加 db 前缀；结构化日志自己写文件；巨型 JSON 从终端挪进文件。

**验收**　跑一次 5 题的小跑，**全程 stdout 不超过 50 行**，且每一行都能看出「现在在干什么、到哪了」。

### N12 · `RunContext` 与 `configure_logging()`（checklist 3.1）

**改什么**　`run_id` / `turn_id` / `corpus_pin` 三个字段已经存在，但**没有一个进入 Langfuse 或 LangSmith 的 trace**。十三个 sink 没有共享 key，所以服务器上跑完之后，trace 和 `stage_events.jsonl` 拼不回去。

两件事：一个 `RunContext` 记录承载这三个字段（外加 `arm`、`schema`、`prompt_set_hash`、`identity`）；一个 `tracing_config(ctx)` 产出同时喂给两个 tracer 的 metadata（LangSmith 读 `metadata` 与 `tags`，Langfuse 读 `langfuse_session_id` / `langfuse_user_id` / `langfuse_tags`）。

外加 `configure_logging()`：`src/` 里没有任何 `logging.basicConfig`，入口也没有，所以 **30 个 `logger.` 调用全是死的**，而 105 个必须被看见的诊断都写成了 `print()`。加一个 ContextVar filter 把 `run_id` / `turn_id` 注入每条日志记录 —— **不要改函数签名**。

**碰哪些文件**　新增 `src/governed_bi/logging_setup.py`；`src/governed_bi/obs.py`（`CallbackHandler()` 现在不带任何参数）；八个调用点：`analyst/agent.py:1478`、`api/graph_app.py:174`、`eval/arms.py:436`、`eval/oracle.py:362`、`eval/refuse_gate.py:71`、`curator/pipeline.py`、`curator/sme.py`、`scripts/live_smoke.py`。

**验收**　跑一次 5 题小跑，**用一个 `run_id` 能同时在 Langfuse trace、`stage_events.jsonl`、日志文件里查到同一批记录**。这一项是三个 sink 的联合验收，缺一个不算做完。

**这一项要花钱**（Langfuse 侧要真跑）。跑之前跟我确认。

### N13 · 可追溯（checklist 6.1）

**先读这段，原计划错过一次**　上次跑不可复现，manifest 记的 `git_sha 3f599b6` 在本地 `git cat-file -t` fatal。**原因不是缺字段，是分支没推加工作树脏。**已经存在、**不要重复加**：`created_at_utc`、`completed_at_utc`（相减正是 1h45m32s）、`git_sha`（`eval/metrics.py:422` 的 `corpus_release_hash()` 产出，`provenance.py:169-190` 读 `.git/HEAD` —— **它已经是服务器分支的 HEAD**）。

**改什么**　两件：

1. **manifest 记 `dirty: bool` 与 `diff_sha256`**（或把 `git diff` 整份落进 run dir）。注意 `provenance.py:169-176` 的 docstring 明写「without `subprocess`」—— 这条约束要么破、要么自己重实现 index 比对。**它不是四小时的活**，估工别按小改动算。
2. **新增两个字段**：分支名、对应的 main hash。服务器上 internal proxy 代码在另一个分支，HEAD 永远不等于 main。

第三件是**零代码**的：跑之前 `git push` 服务器分支。落点是 runbook，不是这一项。接受推论：**不推分支的那次跑不进 quotable 台账。**

**验收**　`dirty=false` 的那次跑，拿分支名加 hash 能 checkout 出可运行的代码；`dirty=true` 的那次，`diff_sha256` 能对上落盘的 diff。

### N14 · 两条 serve 真缺陷（checklist 5.4 第 1、2 条）

**改什么**

1. **一个进程两份 `ServeStack`。**`api/routes.py:28` 在 import 时调一次 `build_stack()`，`api/graph_app.py:233` 又独立调一次，而 `api/stack.py:173` 没有 `lru_cache` —— 一个进程里两份 corpus、两份 `index_cache`、两套 clarify checkpointer。连带后果：`POST /corpus/edit` 写盘成功后本进程读不到（`api/app.py:459-468` 写完直接返回，从不刷新 corpus），策展客户端看到「200 写成功 → 列表还是旧的 → 答案还是旧的」。
2. **narrator 的 token 归属互抢。**`analyst/agent.py:1396-1407` 从 stack 级**共享**的 `narrator._chat.last_usage_metadata` 读并清空，而 `narrator` 由 `build_stack()` 建一次。LangGraph Server 默认并发跑 run，**不需要开多标签页就能触发**。改成从调用返回值取 usage。

**为什么在这一批里**　第 2 条直接影响 token 计量，而**成本是你自己按 token usage 算的** —— 归属错了，成本数字就是错的。

**碰哪些文件**　`api/stack.py:173`、`api/routes.py:28`、`api/graph_app.py:233`、`api/app.py:459-468`、`analyst/agent.py:1396-1407`。

**验收**　`build_stack()` 两次调用返回同一对象；`POST /corpus/edit` 之后 `GET /schema` 立刻可见；并发两个 run，两边的 `token_usage` 之和等于实际消耗。

---

## M5 · 工具与跑

### N15 · 分析工具 CLI 化（checklist X.3）

**这一项对你手上已有的那份 1351×4 数据立刻生效，不用再跑一次。**

**现状**　`eval/error_taxonomy.py`（547 行）和 `eval/sql_diff.py`（579 行）都是库，没有 CLI、没有 `__main__`，`analysis.py` 也不调用 `attribute_rows`。`analysis.json` 从来没有被任何一次跑产出过。最关键的一条：`docs/experiments/` 里那份错误分析报告 —— 五阶段漏斗、近似孪生混淆矩阵、「44 次误路由覆盖了更好的检索排名」、多余 `DISTINCT` 计数 —— **全部是用不在仓库里的临时脚本算的**。

**改什么**　五件：

1. 给 `error_taxonomy` 和 `sql_diff` 各一个入口，能对单行、单 db、单臂运行。
2. 把那份报告里的五阶段漏斗、孪生混淆矩阵实现进 `analysis.py`。
3. 跨臂单题 diff：给一个 `question_id`，并排显示四个臂各自的 SQL、结果、失败阶段。现在 `comparisons[]` 只给不一致的**数量**，不给列表。
4. **把题目原文和 gold SQL join 进 artifact。**现在 72 个字段里两者都没有，每次 debug 的第一步都是回 sibling 仓库手工 join。
5. 让 `analysis.json` 在跑结束时自动产出，不需要单独一条命令。

**验收**　**用 20260730 那份数据重现出报告里的每一个数字。重现不出来的，说明工具和报告有一个是错的 —— 哪一个错，写进 PR 描述。**

**硬约束：`runs/` 在这一项做完之前不许删。**服务器上有备份（决定：本地可删），但这一项要靠那份数据开发并重现报告数字。要么先做完这一项，要么删了之后从服务器拉回来。

**不做字段改名兼容层。**`GenerationRow`（checklist 4.1）不在这一批，所以落盘字段名不变，这一项直接读现有 artifact —— 不需要迁移脚本，也不需要常驻旧名映射表。**这是把 4.1 排除在这一批之外换来的最大一笔简化，不要自己把它花掉。**

### N16 · 修「臂的内容对得上」（checklist 6.2）

20260730 那次跑被判不可引用，两条原因，**其中一条已经在 `main` 上修好了**。

1. **`always-note-budget` 假阳性 —— 已修（`0012dbe`）。这一条是「验证」，不是「修复」。**`summary.json` → `corpus_validation.curated_sme.findings` 逐字是 `always-note-budget []: always-note summaries total 5178 characters; maximum is 2000`，其余三臂 `finding_count: 0`。那是一个 **per-turn 预算被拿去对 57 个 schema 的 pooled corpus 求和**，最差的单 schema 是 1591/2000，build log 记 0 dropped。`0012dbe` 对 `corpus/validate.py` 是 +164/−27，现在按 turn scope 分组判定。**动作是跑一次构建，确认这个 finding 不再出现。**
2. **剩下唯一未修的是措辞。**`eval/index.py:832-838` 对**任何** corpus-validation finding 都硬编码「assets that resolve to nothing cannot reach a prompt」—— 正是这句话把我误导成「悬空引用」，并写进了计划。按 `finding.code` 分流文案。
3. **`sme_noop_dbs` 是抽奖式判据，本次接受。**`eval/index.py:452-456`：57 个 schema 里**任何一个**的 `sme_fold.identical_to_curated` 为真就进列表；`:843-851`：列表非空即写进 `not_quotable_because`。也就是**任意一个 schema 的 Phase A 提零个问题，整次付费跑就不可引用**。**不要给它灌一个下限** —— 那是往 hygiene gate 里注水。明写「每次跑都是抽奖，本次接受」。

**验收**　跑完后 `runs/index.jsonl` 里 `not_quotable_because` 为空。

### N17 · 跑（交付命令与守卫，不交付「跑完了」）

**交付三件**

1. **完整命令行**，含 `--replicate`、`--workers`、`--dbs`、`--split`。多一次 serve pass 的成本是明码的：`run_datalake.py:5109-5116` 的 help 自己写着「Costs one extra serve pass」。归宿是重写后的 runbook。（`--model` 参数属于 checklist 2.3，不在这一批 —— 这一批仍从 `governed_bi.toml` 的 `[models].llm_model` 读，**并且要在 runbook 里写明这个坑：忘了改回来不会报错，只会在事后的 manifest 里留下一个你没注意的字段**。）
2. **零题 schema 守卫**：重筛后某个 schema 落到 0 题，不能算 built-but-unscored，也不能弄坏 pool census。
3. **MDE 预登记，写硬。**实测噪声底线：31 对 `context_hash` 逐字节相同的问题里有 **4 个 `correct` 翻转（12.9%）**，全量不一致 **122/1351 = 9.03%**，成对 SE = 0.0082 → 80% power 的 **MDE ≈ 2.3pp**。而争论中的 SME 步长是 **−0.15pp**。

   > **在这个噪声底线下，没有任何负担得起的 N 能分辨 0.2pp。**`--replicate` 只让你有资格说「**未检出**」而不是「无效果」—— 它不会让 SME 变成可检出。**这句话必须进 runbook**，否则 1.62 亿 token 花完还会有人以为答了 SME 那个问题。

**规划基线**（20260730 实测）：57 db / 1351 题 / 4 臂 = 5404 turn，20 并发，构建 23'49"，四个臂 serve 分别 26'52" / 21'15" / 16'35" / 16'52"，总 1h45'32"，1.62 亿 token，0 crash。**带 `--replicate` 要在此之上再加一个臂的 serve 时间。**

**跑不外派。**这一项的验收是「命令能跑通一次 5 题小跑，且三件交付物都在」，不是「跑完了」。

---

## 交付规约（每个 PR 都按这个收）

1. **一个 N 项一个 PR。**N1 与 N2 是例外，它们必须分成两个 PR（先红后绿），且第一个 PR 的描述里贴失败输出。
2. **不许顺手改。**PR 只碰它那一项「碰哪些文件」里列的文件。看见别的坑就记进 `docs/open-work.md`，不要在这个 PR 里修 —— 一次改两件事的 PR 我会退回。
3. **`pytest tests/` 全绿**（基线：1686 passed / 8 skipped / 1 xfailed）。**测试数只许增不许减**；删任何一个现有测试都要在 PR 描述里单独说明理由。
4. **只改英文文档。**AGENTS.md：工作进行中让中文孪生漂移。`tests/test_repo_contracts.py` 在守孪生清单 —— **不要新建任何 `.zh.md`**，它会当场红。
5. **动 LangGraph / LangChain / DeepAgents 之前先读对应的 skill**（AGENTS.md 的硬规定）。N11、N12、N14 三项都会碰到。
6. **PR 描述四段**：改了什么、验收命令与它的输出、动手时发现和这份文档不一致的地方、以及「我没做但应该有人做」的清单。第三段最重要 —— 这份文档已经被审计打回过一次（104 条指控里 38 条通过），它还会有错。

## 我在 review 里看什么

按会退回的概率排序：

1. **验收是不是真的机器可查。**「手工确认过」不算。N12 尤其 —— 它的验收是三个 sink 的联合查询，只截一张 Langfuse 图不算。
2. **测试测的是不是那件事。**N1 的用例不带列引用就会因为错误的原因通过；N7 的测试按 67 张表构造而不是 27 个名字，就是重复测同一条路径。
3. **有没有借着这一项做别的项。**最容易发生在 N15（想顺手改字段名）和 N2（想顺手改 `grade_semantic_failures` 默认值）。
4. **新增文件的大小。**这个仓库现在有 7 个超过 1000 行的文件（`run_datalake` 5371 / `pipeline` 1658 / `agent` 1500 / `index` 1437 / `asset_bag` 1262 / `run_experiment` 1118 / `run_log` 1065）。**这一批不许再添第八个** —— N9 会消掉一个，那是净减。
5. **注释里有没有把发现的不对称写死。**N2 那句 pooled 下 L3 不收窄、N17 那句 MDE 分辨不了 0.2pp，都属于「不写下来下个人一定会重新踩」的。

## 近期明确不做

记下来，避免被当成遗漏重新提出。**都在 [rebuild-checklist.md](rebuild-checklist.md) 里，只是不在这一批。**

| 不做 | checklist 编号 | 为什么不在这一批 |
|---|---|---|
| `GenerationRow` 类型化 + 落盘字段改名 | 4.1 | 依赖 2.1 / 2.2 / 2.3 / 3.1 全部落地，且会让 N15 需要一层迁移。排除它换来 N15 的最大简化 |
| `Step` 收敛、层级严重度、config knob 收敛 | 2.1 / 2.2 / 2.3 | 它们是 4.1 的前置，4.1 不做就没有紧迫性。**注意 2.3 第 1 点已过期** —— TOML 键不是死的（`config.py:659-668`），别照着做 |
| 后端契约发布（11 个子条目，178 行） | 5.3 | 审计给出机器可证的事实：`eval/arms.py:429-443` 调 `build_serve_rails` **完全不传 `on_event`**，而 `governance.py:531-532` 是 `if self._on_event is None: return` —— 5.3 在那 5404 个 turn 上一个字节都不生效。它的目的是让将来那次前端重写照文档抄，不是服务这一批的终点 |
| 拆 `build_serve_rails`（1034 行 / 14 个嵌套 def）、把统计从 `run_datalake` 提出去 | 4.2 / 4.3 | 这两条是**唯一真正回应「几千行的大文件都多的要死」**的，不做只是因为它们必须紧跟 5.3 的排序。**下一批的第一顺位就是它们** |
| `retrieval/` 重组、A0–A7 参考书对齐 | X.5 | 依赖两个硬前置（`retrieval_config_hash` 进 manifest、9 个数的回归基线） |
| 四个测量臂（多轮 / 负例集 / red-team / metadata-说谎） | 7.1–7.4 | 全在 A 之后。7.3 还依赖 N2 的结论 |
| `AssetBag` 六个开放 dict、presenter parity、`index.jsonl` 缺失键 | X.1 / X.2 / X.4 | 随时可插，不阻塞终点。**X.4 现在的处方按字面无法实现** —— 通过 `dict.get()`，缺键与 `None` 值结构上不可区分，动手前先改处方 |
| 术语改名（模块名、类名、函数名、变量名） | 1.4 | 降级成一条写进 `AGENTS.md` 的规则：凡因别的条目动到某个文件，顺手把该文件里 1.4 表格中的词改掉。**只有 1.4.5（glossary）作为 N5 独立做** |
| 四个悬空编号 | `1.7` / `1.8` / `3.12` / `3.17` | 它们的唯一定义在 `build-sequence.md` 与 `corpus-drift.md` 里，还没搬过来。**谁在这一批里读到这四个编号，当它不存在** |

## 已知的坑

1. **这份文档的上游被审计过，还有约 21 条中等项没并进正文**，在 `scratchpad/audit-final.md`。这一批已经吸收了其中最相关的四条（M3 的 `--skip-agent` 计数、M6 的 X.4 不可实现、M7、M8 的 2.3 过期）。剩下的在做别的项时可能撞上。
2. **A-2 未处置**：`build-sequence.md` 的 41 项里约 28 项静默消失，`RetryPolicy`、`ServeDeployment`、`get_stream_writer`、`on_event`、`durability`、`EXPLAIN`、summariser 等逐词 grep 命中数为 0。**这一批不处理它**，但也别以为「没提到就是决定不做」。
3. **A-6 未处置**：ADR 0002 与 `docs/architecture.md` 里「governance = topology-not-trust」的主张与删掉 `/corpus/edit` 密钥门的决定不自洽，**两份文档一个字都没改**。这一批不动它，但它是文档在撒谎，不是无害的悬案。
4. **golden 回归几乎免费，而这一批没用上。**`tests/` 里已有 18 个文件用 `FakeToolModel` 驱动完整 governed turn，X.5.4 的基线命令实跑 `--schema hockey` 是 2.3 秒。审计的 A-5 建议花一天录一批 provenance + `stage_events` 快照当 golden，之后每个条目都白拿归因。**这一批没排它是我的判断，不是它不值得** —— 如果 M1 或 M4 里出现「不知道是谁改坏的」，第一件事就是回来做它。
