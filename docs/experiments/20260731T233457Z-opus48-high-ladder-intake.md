# Intake note · `20260731T233457Z-opus48-high-ladder`

2026-07-31。服务器跑的完整四臂梯子,收进仓库时的核查记录。**不是分析,是收货单** —— 分析在 bundle 自带的 `…-results.md` 里,重算在后面。

## 放在哪

| 内容 | 落点 |
|---|---|
| 完整 run 目录 | `runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z/` |
| 两个 console log | 同上一级目录 |
| bundle 的 README | 同上,改名 `BUNDLE_README.md` |
| progress / results / EXPERIMENT_LOG | `docs/experiments/` |
| `runs.sqlite`(run_log) | `data/logs/runs-20260731T233457Z-opus48-high.sqlite` |

`runs/` 与 `data/logs/` 都被 gitignore —— **和 20260730 那份一样,这份 110 MB 的东西不进版本控制**。`docs/experiments/` 那三份 md 会进。

已 `--add` 进 `runs/index.jsonl`。

## 收货时发现的四件事

### 1 · 台账判定与 bundle README 相反

README 写「**run is quotable**」。仓库自己的台账:

```
quotable: False
not_quotable_because:
  - curated_sme folded nothing on codebase_community, disney, donor,
    formula_1, legislator, mondial_geo — its corpus is identical to
    curated there, so the SME delta on those dbs is not a measurement
```

**57 个 schema 里有 6 个的 SME Phase A 提了零个问题。**这是 M5 N16 第 3 件明确「本次接受」的抽奖式判据 —— **不是新缺陷**,但 README 的表述和台账不一致,而**台账是文档指定的仲裁者**(`docs/architecture.md:204`)。

`summary.json` 自己的 `not_quotable_because` 是 `null`,台账算出来是非空 —— 两处判定在不同时点算,结论不同。

### 2 · resume 期间工作树变了,没有任何东西发现

manifest 记录了一次 resume(README:curated_sme 跑到 1025/1351 被打断)。两段的指纹:

```
顶层    dirty=True  diff_sha256=dde6190b242bd2e2…
resume  dirty=True  diff_sha256=ba526fa44940f441…   ← 不同
```

**`git_sha` 两段相同(`e8a2633`),所以那道「致命」的 resume 守卫没有触发** —— 而 `diff_sha256` 和 `dirty` **都不在 `RESUME_DRIFT_KEYS` 里**(实测)。

也就是说 **`curated_sme` 的前 1025 行和后 326 行是在两份不同的工作树下跑出来的**,manifest 两个哈希都记了,**没有任何门去比它们**。这正是 `run_datalake.py:1394` 那道守卫自己描述的「two harness versions' rows silently averaged into one arm's score」。

这是对抗审计 [findings #9](../plans/adversarial-review-2026-07-31.md) 在一次真实付费跑上的实例。

### 3 · resume 记录里 `corpus_content_hash` 是 `None`

```
resumes[0].corpus_content_hash = None
```

按构造如此(`build_manifest` 总写 `None`,由 `stamp_corpus_hashes` 事后填,而 resume 记录不经过那一步),所以 **`_resume_drift` 的 `if now is not None` 永远为假,跨 resume 的语料漂移在结构上不可见**。对抗审计 F1 的预测,在这份 manifest 上逐字成立。

本次顶层 `corpus_content_hash == corpus_content_hash_observed == 88178026c1e4483c`,**语料确实没漂** —— 但那是运气好,不是守卫起了作用。

### 4 · 台账不收 N13 的四个出处字段

```
manifest: dirty=True   git_branch='mars'   main_git_sha='a5023ef…'   diff_sha256='dde619…'
ledger  : dirty=None   (git_branch / main_git_sha / diff_sha256 根本不在 record 里)
```

`record_for_run` 不 lift 这四个中的任何一个。所以**在台账层面,一次脏工作树的跑和一次干净的跑长得一模一样** —— M4 N13 加这些字段就是为了可追溯,而可引用性的仲裁者看不到它们。

## 出处能不能复原

| | |
|---|---|
| `git_sha` `e8a2633` | **本仓库里不存在** —— 它在 `governed-bi-mars` fork 的 `mars` 分支上 |
| `main_git_sha` `a5023ef` | **在本仓库里,是 `main` 的祖先** —— `feat(eval): pooled data-lake eval harness + embedder-first schema routing` |
| `diff_sha256` | 记了哈希,**diff 本身不在 bundle 里** |

所以:基点可解析,fork 的提交不可解析,而且 `dirty=True` 的那份改动**只有哈希没有内容**。N13 的规格当初写的是「记 `dirty` 与 `diff_sha256`,**或把 `git diff` 整份落进 run dir**」—— 实现选了前者,对一次 `dirty=True` 的跑而言,**代码状态不可重建**。

## 关于 SME 那一步:同一份文件里三个数

| 口径 | curated → curated_sme |
|---|---|
| `ex_lenient`(README 引的) | 0.5625 → 0.5611 = **−0.15pp** |
| `ex_no_twin`(headline,n=1085) | 0.5705 → 0.5714 = **+0.09pp** |
| `no_twin` 比较块(带 p 值,n=1236) | **−0.16pp** |

**headline 说正,带 p 值的那个说负。**成因是对抗审计 [findings #8](../plans/adversarial-review-2026-07-31.md):headline 过 `is_gradeable_eval_row`,比较块不过,两者差 151 行(125 frozen-constant + 26 order-sensitive)。

**三个数都比 MDE(2.3pp)小一到两个数量级**,所以诚实的结论仍然是**未检出**,和 MDE 预登记说的一致 —— **预登记在这里起到了它的作用**。但「同一个预登记量在一份 artifact 里有两个互相矛盾的值」这件事本身要修,否则下一份 artifact 还是这样。

## 可以直接做的分析

数据齐全,而且 **N15 的工具现在能吃它**(`analysis.json` / `questions.jsonl` / `arms_summary.json` 都在 bundle 里,是同一套工具产的):

```bash
uv run python -m governed_bi.eval.analysis runs/datalake/20260731T233457Z-opus48-high-ladder/20260731T233545Z
```

跨 run 对比也可以 —— 它和 20260730 那份是同一个 split、同一个 `question_pool_hash` 口径,但**模型不同(Opus-4.8/high vs 之前的)**,所以那是模型对比不是语料对比。用 `comparable()` 先问一句能不能并排引用,别直接比。
