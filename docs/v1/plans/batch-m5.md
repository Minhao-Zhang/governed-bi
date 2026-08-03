# 第五批工作单 · M5 工具与跑（N15–N17）

2026-07-31 立。分支从 `impl/rebuild-first-batch` 起。上游是 [near-term-plan.md](near-term-plan.md) 的 M5 一节。体例同 [batch-m2.md](batch-m2.md) / [batch-m3.md](batch-m3.md) / [batch-m4.md](batch-m4.md)。

> **语言：简体中文，无英文孪生。**

## 这一批是什么

近期计划的最后一批。终点是那三句话里的第三句:**拿到第一份 quotable 的跑,而且报告里每一个数字都能用仓库里的工具重现出来 —— 不是临时脚本。**

| 项 | 一句话 | 估工 | 花钱？ |
|---|---|---|---|
| **N15** | 分析工具 CLI 化 + 把题面和 gold SQL join 进 artifact | 4 人日 | 否 |
| **N16** | 验证「臂的内容对得上」+ 修一句误导性文案 | 0.5 人日 | 一次构建 |
| **N17** | 交付完整命令、零题守卫、MDE 预登记 | 1 人日 | **不含那次跑** |

### 两条硬约束,先说

**一 · `runs/` 在 N15 做完之前不许删。**服务器上有备份,但 N15 要靠 `runs/datalake/20260730T034522Z-test-ladder-fixed2` 那份 1351×4 的数据开发,并重现 `docs/experiments/20260730T034522Z-curated-sme-error-analysis.md` 里的每一个数字。删了就得从服务器拉回来。

**二 · N17 交付的是命令,不是「跑完了」。**那次全量跑(4 臂 + `--replicate` 第 5 遍 serve)按 20260730 实测基线推算约 **2 小时 10 分、2 亿 token 量级**。花不花这笔钱是你的决定,不是这一批的验收。N17 的验收是「命令能跑通一次 5 题小跑,且三件交付物都在」。

PR 规约沿用 [near-term-plan.md 的「交付规约」](near-term-plan.md)。基线:**1701 passed / 10 skipped / 1 xfailed**(M4 收尾后)。

---

## 开工前:上游 spec 的五处更正

near-term-plan 的 M5 摘自 rebuild-checklist X.3 / 6.2 / 6.3。**2026-07-31 逐处核过:**

| # | 原文说 | 实际 |
|---|---|---|
| 1 | 「`error_taxonomy.py` 和 `sql_diff.py` 都是库,没有 CLI、没有 `__main__`,**`analysis.py` 也是**」 | 前两个对(各 **0** 处 `__main__`)。**`analysis.py` 有完整的 argparse CLI 加 `__main__`**(`:812-833`),还能写 `analysis.json`(默认 `run_dir/analysis.json`)。N15 要补的是**前两个**的入口,不是三个 |
| 2 | 「`analysis.py` 也不调用 `attribute_rows`」 | 字面为真但会误导。**`run_datalake.py:110` import 了它,`:2851` 在调它。**错误分类不是没接线的孤儿,它已经在 driver 里跑 |
| 3 | 「`analysis.json` 从来没有被任何一次跑产出过」 | **成立,而且原因具体**:`run_datalake.py` 里 `analysis.json` 只出现在 `:1887` 一句注释里,driver 从不调用 `analyse_run`。手工跑 `python -m governed_bi.eval.analysis <run_dir>` 是能产出的 —— 所以缺的是**自动化**,不是能力 |
| 4 | `sme_noop_dbs` 在 `index.py:452-456` 与 `:843-851`;误导性文案在 `:832-838` | 行号全漂了(M3 删了 knob)。现在:`sme_noop_dbs` 在 **`:436`** 与 **`:815`**;那句硬编码文案在 **`:809`** —— 是**一行**,不是七行 |
| 5 | N17 的完整命令含 `--model` | **`--model` 不存在**,`run_datalake.py` 里零命中。它是 checklist 2.3,不在近期计划任何一批里。现有的是 `--split` / `--dbs` / `--limit` / `--workers` / `--replicate`（**不写行号** —— 本文档写于 N19 之前，那时引的 `:5048`–`:5199` 在 N19 从 `run_datalake.py` 搬走 1567 行之后全部作废。跨批次的 `file:line` 会烂，用 `--help` 或 `grep add_argument` 现查） |

另外一条**已经被 M4 修掉、不用再做**:generations 行现在是 **73 字段**(M1 的 `governance_ledger` 投影加进去的),`docs/eval-metrics.md` 已同步。

---

## N15 · 分析工具 CLI 化

**这一项对你手上那份 1351×4 的数据立刻生效,不用再跑一次。**

### 现状(核过)

`docs/experiments/20260730T034522Z-curated-sme-error-analysis.md` 里那份错误分析 —— 五阶段漏斗、近似孪生混淆矩阵、「44 次误路由覆盖了更好的检索排名」、多余 `DISTINCT` 计数 —— **全部是用不在仓库里的临时脚本算的**。那些脚本没了,所以那份报告现在**没有任何人能重现**,包括写它的人。

这是这一项存在的唯一理由:**一个不能被重现的数字,和一个没测过的数字,在可引用性上是同一档。**

### 五件

1. **给 `error_taxonomy` 和 `sql_diff` 各一个入口**,能对单行、单 db、单臂运行。照 `analysis.py:812-833` 的 `main(argv)` 形状抄 —— 那里已经有一份可用的模板,别自创第二套。
2. **把报告里的五阶段漏斗、孪生混淆矩阵实现进 `analysis.py`。**
3. **跨臂单题 diff**:给一个 `question_id`,并排显示四个臂各自的 SQL、结果、失败阶段。现在 `comparisons[]` 只给不一致的**数量**,不给列表。
4. **把题面原文和 gold SQL join 进 artifact。**核过:generations 行 73 个字段里只有 `question_id`,**没有题面,没有 gold SQL**(`gold_frozen` / `gold_nrows` / `gold_twin_in_train` 那些是**关于** gold 的元数据,不是 gold 本身)。所以每次 debug 第一步都是回 sibling 仓库手工 join。
5. **让 `analysis.json` 在跑结束时自动产出。**能力已经有了(`analyse_run` + CLI),缺的是 driver 收尾时调它一次。

### 验收

**用 `runs/datalake/20260730T034522Z-test-ladder-fixed2` 那份数据重现出报告里的每一个数字。**

重现不出来的,说明**工具和报告有一个是错的 —— 在 PR 描述里写明是哪一个**。这条不许含糊过去:那份报告已经被引用过,如果它错了,现在是发现的最后机会。

### 禁止

- **不许改落盘字段名。**`GenerationRow`(checklist 4.1)不在近期计划,所以字段名不变,这一项直接读现有 artifact —— **不需要迁移脚本,也不需要旧名映射表。这是把 4.1 排除在外换来的最大一笔简化,不要自己把它花掉。**
- 第 4 件加字段时,注意它会让 artifact 变大(题面 + gold SQL × 5404 行)。**先量一下增量**再决定是内联还是旁挂一个 `questions.jsonl`。M1 那次就是因为把整份查询结果内联进 ledger 才险些炸掉 artifact。
- 不许给 `error_taxonomy` / `sql_diff` 自创一套和 `analysis.py` 不同的 CLI 约定。

---

## N16 · 验证「臂的内容对得上」

20260730 那次跑被判不可引用,两条原因,**其中一条已经在 `main` 上修好了**。

### 三件,难度差很多

**1 · `always-note-budget` 假阳性 —— 已修(`0012dbe`)。这是「验证」,不是「修复」。**

`summary.json` → `corpus_validation.curated_sme.findings` 逐字是
`always-note-budget []: always-note summaries total 5178 characters; maximum is 2000`,其余三臂 `finding_count: 0`。那是一个 **per-turn 预算被拿去对 57 个 schema 的 pooled corpus 求和** —— 最差的单 schema 是 1591/2000,build log 记 0 dropped。`0012dbe` 对 `corpus/validate.py` 是 +164/−27,现在按 turn scope 分组判定。

**动作:跑一次构建,确认这个 finding 不再出现。**这是这一批唯一要动真格跑的一步,但只构建不 serve,便宜。

**2 · 唯一还没修的是一句文案。**`eval/index.py:809` 对**任何** corpus-validation finding 都硬编码:

> "nothing cannot reach a prompt, so the arm did not serve what it holds"

正是这句话把我误导成「有悬空引用」,并写进了计划、又被审计当成事实引用了一轮。**按 `finding.code` 分流文案。**一行的改动,但它污染过一份计划文档和一次审计。

**3 · `sme_noop_dbs` 是抽奖式判据,本次接受。**`eval/index.py:436` 收集列表,`:815` 判定列表非空即写进 `not_quotable_because`。也就是 **57 个 schema 里任意一个的 Phase A 提零个问题,整次付费跑就不可引用**。

**不要给它灌一个下限** —— 那是往 hygiene gate 里注水。在 PR 里明写「每次跑都是抽奖,本次接受」。

### 验收

跑完后 `runs/index.jsonl` 里 `not_quotable_because` 为空。**注意这一条要等 N17 那次真跑才能验** —— N16 本身只能验到「构建不再产生那个 finding」加「文案按 code 分流」。

---

## N17 · 交付命令、守卫、MDE 预登记

### 三件交付物

**1 · 完整命令行。**含 `--replicate` / `--workers` / `--dbs` / `--split`。**不含 `--model`** —— 它不存在(更正 5),模型仍然从 `governed_bi.toml` 的 `[models].llm_model` 读,现在是 `gpt-5.6-luna`。

**runbook 里必须写明这个坑**:切模型靠改 TOML,忘了改回来不会报错,只会在事后的 manifest 里留下一个你没注意的字段。**跑之前先确认:**

```bash
uv run python -c "from governed_bi.config import Settings, Environment; print(Settings.for_env(Environment.dev).models.llm_model)"
```

跑完再从 `manifest.json` 的 `model` 复核。M4 的 N12b 已经把这套前置检查跑通过一次,照抄。

**2 · 零题 schema 守卫**(从 `eval-rebuild.md` §4 搬来):重筛之后某个 schema 落到 0 题,不能算 built-but-unscored,也不能弄坏 pool census。

**3 · MDE 预登记,而且把结论写硬。**

实测噪声底线:31 对 `context_hash` 逐字节相同的问题里有 **4 个 `correct` 翻转(12.9%)**,全量不一致 **122/1351 = 9.03%**,成对 SE = 0.0082 → 80% power 的 **MDE ≈ 2.3pp**。而争论中的 SME 步长是 **−0.15pp**。

> **在这个噪声底线下,没有任何负担得起的 N 能分辨 0.2pp。**`--replicate` 只让你有资格说「**未检出**」,而不是「无效果」—— 它不会让 SME 变成可检出。
>
> **这句话必须进 runbook**,否则两亿 token 花完还会有人以为答了 SME 那个问题。

### 那次跑的成本(你决定,不是验收)

20260730 实测基线:57 db / 1351 题 / 4 臂 = 5404 turn,20 并发,构建 23'49",四臂 serve 26'52" / 21'15" / 16'35" / 16'52",**总 1h45'32",1.62 亿 token,0 crash**。

**带 `--replicate` 要在此之上再加一个臂的 serve 时间** —— `run_datalake.py` 的 help 自己写着「Costs one extra serve pass」。推算约 **2h10m / 2 亿 token 量级**。

### 验收

命令能跑通一次 **5 题小跑**(照 M4 N12b 的形状),且三件交付物都在。**不含「跑完了」。**

---

## 交付顺序

```
N15 ──────────────►  （最大的一项,先开）
N16 ──►              （并行,零文件冲突）
        N17 ──►      （依赖 N16 第 1 件的构建验证结论)
```

三个 PR。N15 内部建议按那五件再拆 —— 尤其第 4 件(join 题面和 gold)会动 artifact 形状,单独一笔便于回退。

## review 会挂在哪里

按会退回的概率排:

1. **N15 顺手改了落盘字段名。**那会让这一项自己读不了 20260730 的数据,而那份数据是它唯一的开发依据。
2. **N15 重现不出报告里的数字,却没说是哪一边错了。**「大致对得上」不算 —— 那份报告已经被引用过。
3. **N15 给 `error_taxonomy` / `sql_diff` 自创了第二套 CLI 约定**,而 `analysis.py:812` 已经有一份。
4. **N16 给 `sme_noop_dbs` 灌下限。**往 hygiene gate 里注水。
5. **N17 的 MDE 那段被写成「建议关注」而不是结论。**它的作用就是提前否掉一个花完钱之后必然会有人提的问题。
6. **第 4 件把题面和 gold 内联进 generations 行却没量增量。**M1 就是这么险些炸掉 artifact 的。

## 近期计划做完之后,还剩什么

M5 收尾时,[near-term-plan.md](near-term-plan.md) 的三条终点:**第 1 条(数字不再是错的)M1 已结,第 2 条(看得见)M4 已结,第 3 条(第一份 quotable 的跑)等你按 N17 的命令去跑。**

**但要说清楚一件事:你最看重的那条(B,结构与可维护性)基本没动。**

`src/` 里超过 1000 行的文件从 7 个变成 6 个,唯一减少的是 N9 删掉的 `run_experiment.py`;**剩下六个净增长了**(`run_datalake` 5371→5474,`agent` 1500→1534)。扣掉那个被删的文件,`src/` 净增约 700 行。

原因是排序的直接后果:**4.2(拆 `build_serve_rails` —— 1034 行单函数、14 个嵌套 def)和 4.3(把约 1300 行统计从 `run_datalake` 提出去)刻意不在近期计划里**,而它们是全案唯一真正回应「几千行的大文件都多的要死」的两条。

**我的建议是它们排在 M5 之前,不是之后:**

- **4.2 有硬时序** —— 它必须在任何往 `agent.py` 加东西的工作之前,而 M4 已经往里加过了。再拖,那 1034 行只会更长。
- **4.3 和 N15 同域** —— 两者都要把 `run_datalake` 里的统计代码从头过一遍。分开做等于读两遍。

这个顺序调整需要你拍板,不是我能替你决定的。
