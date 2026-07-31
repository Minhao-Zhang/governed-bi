# M5 review findings（N15–N17）

2026-07-31。对 `impl/rebuild-first-batch` tip `9d851f6` 的 review。

> **2026-07-31 复核（`499a3c0`）：15 条全部处置，M5 收。**
> A1–A4、B1–B5、C1–C5 逐条复核通过，A4 我重新证伪过（删调用点现在会红）。
> D 是我自己的文档错，已在 [batch-m5.md](batch-m5.md) 改掉。
> 两条新的、小的，见文末「复核后的残留」。

**原结论：M5 不收。**工具本身是好的 —— `bird_basis.py` 的 cascade 比原报告规定得更清楚，§1 waterfall 四个臂逐位重现，N17 的守卫函数和 MDE 都过硬。**要撤的是裁定和几处接线，不是代码。**

全套 **1734 passed / 10 skipped / 1 xfailed**，工作区干净 —— 下面每一条都不是靠测试红发现的。

---

## A · 必须修（会让结论错或让守卫失效）

### A1 · N15 的九处裁定，七处是反的

evidence 里把每一处和报告的差异都判成「**Report high / tool pick-stage**」。**报告没有高。**

用报告自己的 population —— `routed_hit=False` **且** gold schema 在 `shortlisted_schemas` 里，全部 1351 行 `curated_sme`，**不排除 BIRD exclusion，不过滤 `correct`** —— 复现命令：

```bash
python -c "
import json,pathlib
from collections import Counter
d=pathlib.Path('runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z')
rows=[json.loads(l) for l in (d/'generations.curated_sme.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
pop=[r for r in rows if r.get('routed_hit') is False and r.get('db_id') in (r.get('shortlisted_schemas') or [])]
idx=lambda l,v: (list(l).index(v) if v in (l or []) else None)
ov=sum(1 for r in pop if (lambda g,p: g is not None and p is not None and p>g)(idx(r.get('shortlisted_schemas'),r.get('db_id')), idx(r.get('shortlisted_schemas'),(r.get('routed_schemas') or [None])[0])))
print('n =',len(pop),' rank_overrides =',ov)
print(Counter((r.get('routed_schemas') or [None])[0] for r in pop).most_common(6))
"
```

实际输出：

```
n = 107  rank_overrides = 44
[('superstore', 12), ('world', 12), ('ice_hockey_draft', 9),
 ('law_episode', 8), ('food_inspection_2', 7), ('movies_4', 7)]
```

**报告那六个 attractor 和那个 44，全部逐位命中。**

另有一次 96 个候选 population 的网格搜索（4 臂 × 3 basis × wrong/short/answered 过滤），**只有这一个能同时满足 14 个 cell**；它同时还产出 44 和那 1 个 parse-failure fallback，共 16+ 条约束落在同一定义上。所以 evidence 里那句「**even that set does not fully reproduce every attractor**」**是没验证过的断言**。

而且报告的 population 在这件事上本身更合理：**误路由到 `world` 就是一次路由混淆，跟答案碰巧对不对无关。**工具滤掉了 `correct=True`，两行「误路由但答对」的 `mondial_geo→world` 正好就是 `world` 12→10 那个缺口的全部。

**要做的**：把 evidence 表里那七条从「report high」改成「**不同 population；报告在它自己的口径上正确**」。工具保留 pick-stage 口径没问题 —— 但那是**另一个指标**，不是对同一个指标的更正。

### A2 · 六个测试钉把错误裁定固化了

`tests/test_bird_basis_report.py:242-265` 断言 `rank_overrides == 41`、`_attractor_n(pick,"world") == 10`、`over_join == 110`、`table == 138` …… **每一条都是工具当前的输出**，报告的值只写在尾注释里，注释文案是 `# report cell wrong` 之类的裁定。

这是 characterization test：能抓代码漂移，**抓不了统计本身错**。而且现在会反噬 —— 谁把 population 改回报告用的那个，会看到**六条红**，读起来像回归。

**要做的**：解掉这六个钉，或改成断言报告口径下的值；**删掉注释里的裁定文案**。未经验证的判断不该以事实的形态待在测试套里。

### A3 · `over_join` 有约三分之二是噪声，而且它撑着一条建议

stage-4 里 **69 行的 gold 是 frozen `VALUES(...)` 常量，解析出零张表**。`over_join` 判 `pred_tables - gold_tables` 非空 —— **对不引用任何表的 gold，这个差集恒非空**，所以这 69 行全被记成「多连了表」。

```bash
python -c "
import json,pathlib
d=pathlib.Path('runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z')
rows=[json.loads(l) for l in (d/'generations.curated_sme.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
s4=[r for r in rows if r.get('outcome')=='answered' and r.get('correct') is False and r.get('routed_hit') is True]
print(len(s4),'rows,', sum(1 for r in s4 if r.get('gold_frozen')),'frozen-gold')
"
# -> 421 rows, 69 frozen-gold  (16.4%)
```

报告的 113 和工具的 110 都建在这上面；去掉之后约 **39**。而 §5 那条「over-joining (113) 主导 stage 4」的建议**直接靠这个数**。

**这个仓库已经有排除函数** —— `is_frozen_constant` / `gold_frozen` / 整个 `ex_gradeable` 机制就是为它建的，M2 的 N6 刚把那条 regex 收敛成一份。**这个统计没用上。**

**要做的**：`over_join` 加 frozen-gold 排除，用 `is_frozen_constant`；§5 那条建议标为待重算。顺带检查 stage-3 覆盖门 —— 那 71 行因为 `if gold_tables and ...` **整个跳过了 stage-3**，把 stage-4 相对 stage-3 抬高了。

### A4 · 零题守卫没有接线测试

`_quarantine_zero_question_schemas` 本身有 7 个测试，**但没有一个走 driver**。把调用点 `run_datalake.py:2962` 删掉：

```
1734 passed, 10 skipped, 1 xfailed
```

**全绿。**（我自己复现过，不是二手结论。）废掉函数本体会红 3 条 —— 所以函数是被守住的，**接线不是**。将来谁重构掉那一行，CI 全绿，**只有在那次 2 小时 2 亿 token 的付费跑上才会暴露**。

**要做的**：一个测试用真 driver 跑一个含空 schema 的小池，断言 `dbs_zero_questions` 非空。

---

## B · 该修（正确但没验证 / 措辞不实）

### B1 · N16 的「验证」没有真的发生

batch-m5 写的动作是「**跑一次构建，确认这个 finding 不再出现**」。交付的是一个单元测试加那次 5 题 smoke，而 smoke 的 `corpus_validation` 是 `{"baseline": {"finding_count": 0, "findings": []}}` —— **一个臂、一个 schema**。

那个假阳性的成因是「per-turn 预算被拿去对 **57 个 schema 的 pooled corpus** 求和」。**单 schema 的构建在结构上不可能重现它，也就不可能证伪它。**

**要做的**：`--dbs` 挑三四个 schema 跑一次**只构建不 serve**，零模型调用。

### B2 · runbook 里 MDE 的出处写错了

runbook 说那个 MDE「derived from **serve** noise (re-serving one corpus)」。**不是。**122/1351 是 `curated`↔`curated_sme` 的**臂间**不一致，两份不同的语料；fixed2 那次跑**没有 replicate 臂**。只有 4/31 那个子数是真正的同上下文重跑噪声。

方向上是**低估**（臂间不一致 ≥ 纯解码噪声，真实 MDE 只会更大），**所以结论完全站得住** —— 但出处那句是错的，而这份文档存在的意义就是被引用。

**要做的**：改成「臂间不一致，是解码噪声的上界估计」。

### B3 · census 那半边只有静态确认

`built` 在 `:2962` 重新赋值后，一路到 `:3480` 没有被再放宽（`twin_report` / `gold_hashes` / `_load_built_corpus` / `corpus_census` / `sme_fold` 都吃隔离后的值）—— **这是读代码确认的，没有测试覆盖**。

`eval-rebuild.md` §4 的要求有两半：不算 built-but-unscored（已测）、不弄坏 pool census（未测）。

### B4 · `_assert_train_test_disjoint` 跑在隔离之前

`:2950` 早于 `:2962`。一个零测试题的 schema 仍可能有 train 行，所以 `manifest["leakage"]["n_train_ids"]` 会数进一个**不在 `built_dbs` 里**的 schema。`n_test_ids` 不受影响（按定义为零）。**census 不一致，而这正是守卫要修的东西之一。**

### B5 · evidence 的措辞让人以为跳过了构建

零题 schema 的 corpus **照样被构建了**（curator 的模型调用照花），隔离发生在构建之后，只剔除了 load / census / route / serve。evidence 写的「leave `built` before corpora」读起来像跳过了构建。**只是成本不是正确性**，但措辞要改。

---

## C · 小的

| # | 问题 |
|---|---|
| **C1** | `Extra DISTINCT` 报告 75 / 工具 76 —— **这是九处里唯一工具真对的一处**（独立重算得 76，找不到任何合理变体能得 75）。而 evidence 用中性措辞写它（"Tool +1 vs report"），没判成报告缺陷。**错的地方写得斩钉截铁，对的地方反而含糊** —— 这条该升级成明确的报告缺陷 |
| **C2** | `Seeded §1 table / wrong_shape` 139/155 vs 138/156：独立重算按 AST parser 得 138/156、按朴素解析得 140/155，**判不了**。evidence 写的「Report cell wrong」**没有依据**，应改成「口径未定，无法判定」 |
| **C3** | 不可引用理由字符串说「arm count unknown」，而记录里 `arms: ['baseline']` —— `holm_family_size(1) = 0` 把一个**已知**的单臂跑路由进了 unknown 分支。阈值 8 是对的，文案错了 |
| **C4** | runbook `:533` 说可分辨带宽「~+1.6-point」，`:540` 说 MDE ≈ 2.3pp —— **一个概念两个数**。两者都 ≫ 0.2pp 所以结论不变，但读者会绊一下 |
| **C5** | runbook 写 SME 步长「on the order of 0.2pp」，**实测是 −0.15pp**，而实测值在 runbook 里一次都没出现 |

---

## D · 这条是我的错，不是实现的

`batch-m5.md` 的「更正 5」里我引了 `--workers` 在 `run_datalake.py:5144`、`--replicate` 在 `:5199`。**N19 从那个文件搬走 1567 行之后，它现在是 4021 行，那些行号全废了** —— 而我是在 N19 之前写的 M5 工作单。

跨批次的 `file:line` 引用会烂。这是这几批里我第四次栽在「一次不完整或过期的检索」上（前三次：`| head` 截断的 grep、`grep -l` 当依赖证据、`getsource` 站点列表）。

---

## 复核这份 review 的方式

A1 / A3 / A4 三条的复现命令都在上面，**都不需要模型、不需要 Postgres、不需要付费**。A1 和 A3 读 `runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z/`（**在 N15 收尾前不许删**），A4 是删一行跑一次全套。

**不同意任何一条就回来说，带上你自己的复现。**A1 我最有把握 —— 报告那六个 attractor 加那个 44，在我这边和独立 review 那边各算了一次，两次都逐位落在报告上。


---

## 复核后的残留（2026-07-31，`499a3c0` 之后）

修得干净，两处小的：

### R1 · `over_join` 的排除谓词比需要的窄 2 行

A3 用 `is_frozen_constant(gsql)` 排除，这是仓库的正典函数，选得对。但真正让 `over_join`
失去意义的条件是「**gold 一张表都没命名**」—— frozen 是它的一个子集：

```
stage4 n = 355
  frozen gold（现已排除）:            69
  非 frozen 但 gold 解析出 0 张表:     2   ← 仍被计成 over-join
```

这正是我这边 41 与独立复核 39 的全部差距。一行的事：`if is_frozen_constant(gsql) or not gold_tables: continue`。
不影响结论（2/41），但谓词该对准它真正要挡的东西。

### R2 · 这一批给 ruff 又添了 2 个错误

`ruff check .` 从 M4b 之后的 **7** 涨到 **9**，新增两处都是 `I001`，在这一批新建的两个测试文件里：
`tests/test_bird_basis_report.py:10`、`tests/test_zero_question_guard.py:10`。

CI 有 lint 门（`.github/workflows`，注释写着 "keep it green"）而它一直是红的 —— 这是既有问题，
不是这一批造成的。但**在一个已经红的门上继续加**会让将来那次清理更难归因。九处全部 `--fix` 可自动修。
