# 第三批工作单 · M3 删双轨（N9 / N10 / N10a）

> **2026-08-02：Langfuse 已整体移除，LangSmith 是唯一 tracer**（[design-decisions.md](../design-decisions.md) D20）。
> 文末提到「N12 要写 Langfuse」的那句按历史读。

2026-07-31 立。分支从 `impl/rebuild-first-batch` 起。上游是 [near-term-plan.md](near-term-plan.md) 的 M3 一节 —— **那一节只给了目标，这一份给做法**。同 [batch-m2.md](batch-m2.md) 的体例。

> **语言：简体中文，无英文孪生。**`.zh` 后缀宣称的是「某份英文文档的中文孪生」，这份没有英文源头。

## 这一批是什么

三项。**依据是决定 12：跑实验的人是有智慧的，不给操作员建拦手滑的闸门。**注意这条**管不到**数据读取的向后兼容 —— 把「不给操作员建护栏」扩张成「不许有任何向后兼容」是范畴错误。

| 项 | 一句话 | 估工 | 花钱？ |
|---|---|---|---|
| **N9** | `run_experiment.py` 退役（1118 行） | 1 人日 | 否 |
| **N10** | 删 `--skip-agent` / `--allow-git-sha-drift` / resume 双轨判断 | **1.5 人日** | 否 |
| **N10a** | rvgd ↔ `Corpus.table_by_name` 歧义一致性测试（M2 遗留） | 0.5 人日 | 否 |

**N9 → N10 是硬顺序。**`skip_agent` 在 `src/` 里 **75 处 / 8 个文件**，其中 **11 处在 `run_experiment.py`**。先删 driver，N10 就少改 11 处；反过来做那 11 处白改。

**N10a 与前两者零文件冲突**，可以并行开。

near-term-plan 给 M3 估 2.5 人日。**那个数偏低** —— 它把 N10 当成「删一个 flag」。实际上 `skip_agent` 是一个 **manifest 字段 + 一个 `Metric` 注册项 + 一个 `RESUME_DRIFT_KEYS` 成员**，删它要动 schema 版本。下面第二节说清楚。

PR 规约、review 标准沿用 [near-term-plan.md 的「交付规约」](near-term-plan.md)。基线：**1703 passed / 10 skipped / 1 xfailed**。

---

## 开工前：两件必须先知道的事

### 一 · `skip_agent` 不是一个 flag，是测量契约的一部分

near-term-plan 写的是「删掉 `--skip-agent`、`--allow-git-sha-drift`，以及 `_check_resume_manifest` 里那套双轨判断」。**照字面做会漏掉一半。**逐处核过的实际分布：

| 位置 | 它是什么 | 删掉的后果 |
|---|---|---|
| `eval/metrics.py:133` | `Metric("skip_agent", "no model was called at all")` —— **指标注册表的一项** | `docs/eval-metrics.md` 要重新生成（`scripts/gen_eval_metrics_doc.py`，由 `tests/test_eval_metrics.py` 守） |
| `eval/metrics.py:427` | **manifest 字段** | **`MANIFEST_SCHEMA_VERSION` 必须从 1 bump 到 2**（`metrics.py:102`） |
| `eval/metrics.py:175-187` | `manifest_model(model_name, *, skip_agent)` —— skip 时把 `model` 强制成 `None` | `model` 字段的语义变了，函数签名要跟着改 |
| `eval/index.py:170` | **`RESUME_DRIFT_KEYS` 成员** | resume 一致性检查少一个键 |
| `eval/index.py:106` | 「为什么它不在 `COMPARABILITY_KEYS` 里」的理由字典 | 理由随键一起删 |
| `eval/index.py:462` | 写进 `runs/index.jsonl` 的 record | 老 record 有这个键，新的没有 |
| `eval/index.py:646` | `quotable()` 的一条拒绝理由 | 这条判据消失（本身没问题 —— 不会再有 skip-agent 跑） |

**`tests/test_eval_index.py:516` 是硬闸门**，逐字是：

```python
assert drift - comparability == {"git_sha", "skip_agent"}
```

删的那一刻就 `AssertionError`。这不是形式检查 —— 它是这一项唯一会当场告诉你「你动的是契约不是 flag」的东西。

**顺带把 X.4 的守卫在这里立起来。**`comparable()`（`eval/index.py:938-951`）只在 `manifest_schema_version` **为 `None`** 时拒绝，**版本号不同不拒绝**。所以 v1 的 record（有 `skip_agent`）和 v2 的（没有）比对时，缺的那个键会被读成「两边一致」—— 正是 X.4 描述的那个洞。审计的 M6 已经写明正确处方，**这一项是第一次真的删掉一个 manifest knob，所以在这里落它最便宜**：

> 加一个测试：`MANIFEST_KNOBS` 的名字集合变了而 `MANIFEST_SCHEMA_VERSION` 没有 bump，则失败。

约 15 行。不做的话，下一次删 knob 的人不会收到任何提醒。

### 二 · `--skip-agent` 有一个正当用途，删之前必须先决定它去哪

`--skip-agent --arms baseline --oracle oracle_sql` 是 **grader 上限自检**，**零模型调用、零成本**。`docs/plans/experiment-runbook.md` 的 **step 0 和 step 1 整个建在它上面**（`:54`、`:91`、`:133`，以及 `:464`、`:621` 两处语义说明）。runbook 自己在 `:24` 已经写了一句「every `--skip-agent` command here is killed by checklist 0.2」—— 但没说替代品是什么。

**三个选项，PR 里必须写清楚选了哪个、为什么：**

**A（推荐）· 保留成一个独立开关，不是全局 flag。**比如 `--oracle-only`：语义是「只跑 oracle 档，不跑任何 fair 臂，因此不调模型」。它把「不花钱」变成 oracle 路径的**推论**，而不是一个能和任何配置组合的全局旁路 —— 后者正是双轨的来源。选 A 的话 `skip_agent` 这个 **manifest 字段仍然可以删**：`arms: []` 加 `oracle: oracle_sql` 已经把这次跑是什么说清楚了。

**B · 接受自检从此花钱。**最简单，但会让「改完 grader 想验一下」这件事从零成本变成有成本，而 grader 的正确性是所有数字的地基。

**C · 保留内部 `skip_agent`，只删 CLI flag。**看着温和，实际最差 —— 双轨的代码路径全留着，只是没人能从命令行打开它，于是它变成一条没人测的死路。

**决定必须在动手之前做出来**，因为它决定了 `skip_agent` 这个 manifest 字段是删还是留。

---

## N9 · `run_experiment.py` 退役

### 目标

删掉单 db driver（1118 行）。它唯一独有的能力是「只跑一个 db」，而 `run_datalake --dbs <db> --limit N` 完全覆盖，还多给 `stage_events.jsonl`、serve 断点续跑和真实退出码 —— **`run_experiment` 自己永远返回 0，哪怕台账判定不合格**。

### 碰哪些文件（逐处核过）

**`src/`（5 个）**：`eval/run_experiment.py`（删）、`eval/harness.py`、`eval/index.py`、`eval/metrics.py`、`eval/run_datalake.py`。

**另外两处容易漏 —— 它们在 `gateway/` 里，是文档字符串在陈述「哪些 connector 被真跑覆盖」**：

- `gateway/connectors/base.py:12` ——「**live** by the eval harness (``eval/run_experiment.py`` runs the eval-ladder rungs against …)」
- `gateway/__init__.py:23` ——「(``eval/run_experiment.py``) and unit-tested offline; Redshift is implemented but …」

这两句是**关于测试覆盖的事实主张**。改指向 `run_datalake` 的同时**要确认那句话在新 driver 下仍然成立**（pooled 跑是 Postgres，所以大概率成立 —— 但要确认，不要机械替换字符串）。

**`tests/`（8 个文件）**：`test_run_experiment_parity.py`(4 处) · `test_eval_metrics.py`(14) · `test_eval_index.py`(8) · `test_prompt_attribution.py`(4) · `test_eval_concurrency.py`(2) · `test_eval_usage.py`(1) · `test_hash_grade.py`(1) · `test_notes_c5_withholding.py`(1)

**`docs/`（约 20 个文件）** —— 见下面「文档怎么处理」。

### `tests/test_run_experiment_parity.py`：**不要 `rm`，要分诊**

这个文件有 **15 个 test 函数，但只有 4 处提到 `run_experiment`**。它的 docstring 说得很清楚它在钉两样东西：

> These tests pin the overlap (result shape per row, cost/attempt aggregates) **and the None-vs-zero discipline the aggregates depend on**.

**「两个 driver 的 parity」随 N9 死掉；「`run_datalake` 自己的 None-vs-zero 纪律」不死。**后者正是这个仓库反复栽过的那类洞（「we never looked」被记成「nothing happened」）。

所以：把仍然只关于 `run_datalake` 的断言**搬到**一个新文件（比如 `tests/test_datalake_row_discipline.py`）或并进 `test_datalake_stage_attribution.py`，只删真正的 parity 断言。

**PR 描述里必须写明最终测试数，以及减少的每一个是哪一条、为什么。**「一项一个 PR、测试数只许增不许减」这条规约在这里**被明确豁免** —— 但豁免要一条条报账，不是一句「删了个 driver」。

### 文档怎么处理

三类，处置不同：

| 类 | 文件 | 怎么办 |
|---|---|---|
| **不许改** | `docs/adr/0002-*.md`、`docs/adr/0004-*.md` | **ADR 是历史记录。**它们描述的是当时为真的事。改 ADR 等于改历史 |
| **英文源，中文孪生本次不动** | `docs/usage.md`（+ `usage.zh.md`）、`docs/glossary.md`、`docs/design-decisions.md` | usage / glossary / design-decisions 都在 AGENTS.md 的九文件孪生清单里 —— **只改英文**，让孪生漂移 |
| **正常改** | `docs/measurement.md`、`docs/open-work.md`、`docs/oracle-ladder.md`、`docs/prompt-experiments.md`、`docs/eval-metrics.md`（生成物）、`docs/plans/*` | 命令引用换成 `run_datalake` 等价物 |

`docs/open-work.md` 的 **E4**（`--resume-curated` 无条件重建 baseline）随 N9 关闭 —— 按 C13 的删除线格式写。

### 验收

- `grep -rn "run_experiment" src/ tests/ docs/ README*.md` 只剩 ADR 里的历史记录。
- `run_datalake --dbs beer_factory --limit 5` 跑通并产出 `stage_events.jsonl`。
- `pytest tests/` 全绿，且 PR 描述里逐条报了测试数的账。

### 禁止

- 不许在这一项里碰 `skip_agent`（那是 N10）—— 除了 `run_experiment.py` 自身那 11 处随文件一起消失。
- 不许改 ADR。
- 不许把 `gateway/` 那两句 docstring 机械替换成 `run_datalake` 而不验证主张是否仍成立。

---

## N10 · 删 `--skip-agent` 与 drift 双轨

**前置：上面「开工前 · 二」的决定必须已经做出。**

### 改什么

三样：

1. **`--skip-agent`** 及其全部下游（见「开工前 · 一」那张表）。
2. **`--allow-git-sha-drift`** —— `eval/metrics.py:196/375/446`、`eval/run_datalake.py:1055/1096/1149/1243-1256`。它也是一个 `Metric` 和一个 manifest 字段，和 `skip_agent` 一样要走 schema bump。
3. **`_check_resume_manifest` 里「付费跑致命 / smoke 跑只警告」那套双轨判断**（`run_datalake.py:1243-1256`，`if smoke or allow_git_sha_drift or prior.get("allow_git_sha_drift")`）。

**resume 的一致性检查保留。**它防的是**两套配置混进一份 artifact**，不是防手滑 —— 决定 12 管不到它。这一条要在代码注释里写死，否则下一个读到「M3 删了双轨」的人会把它一起删掉。

### 顺带落 X.4 的守卫

见「开工前 · 一」末尾。一个测试：`MANIFEST_KNOBS` 名字集合变了而 `MANIFEST_SCHEMA_VERSION` 未 bump 则失败。**这一项自己就是它的第一个用户** —— 先写测试（红），再删 knob 并 bump（绿）。

### `MANIFEST_SCHEMA_VERSION` 1 → 2 的连带

- `comparable()`（`index.py:938-951`）只在版本为 `None` 时拒绝，**版本不同不拒绝**。所以 20260730 那份 v1 数据和将来的 v2 跑仍然可比 —— 这是**好事**（M5/N15 要拿 20260730 开发分析工具），但要在 PR 里明说这是有意为之，不是漏判。
- 缺键读成「两边一致」这个洞**不会**因为这次 bump 被修好，只是被守卫住了「下次删 knob 忘了 bump」。X.4 本体仍在 M2/M3 之外。

### 验收

- `grep -rn "skip_agent\|skip-agent\|git_sha_drift" src/` 无结果。
- `tests/test_eval_index.py:516` 的集合等式改对了并且绿 —— **改法是删成员，不是把断言改宽**。
- 新增的 schema-bump 守卫测试：先红后绿，PR 里贴失败输出。
- `docs/eval-metrics.md` 重新生成，`tests/test_eval_metrics.py` 绿。
- `pytest tests/` 全绿。

### 禁止

- **不许把 `test_eval_index.py:516` 的断言改成「包含」或注释掉。**它变红是设计。
- 不许删 resume 一致性检查本体。
- 不许在这一项里改 runbook 的 step 0/1 内容 —— 只删命令里的 flag，runbook 重写是 D1。**但**「开工前 · 二」那个决定的结论要写进 runbook 一句话，否则 step 0 变成一条跑不了的命令。

---

## N10a · rvgd ↔ `Corpus.table_by_name` 歧义一致性（M2 遗留）

### 为什么单独开

N7 把 tools / middleware / agent 三处收到了 `Corpus.table_by_name`，但 `retrieval/rvgd.py:530-538` 的 `phys_to_table` 内联策略**原样还在**，那个文件里 `table_by_name` 引用数为 **0**。所以**歧义策略现在有两份实现**。

**不能硬合**：rvgd 是一趟 O(n) 建**全量映射**，`table_by_name` 是单次 O(n) **查一个**；逐名去调它就是 O(n²)。batch-m2 明确允许了这一点，缺的只是「两份会漂移」的钉。

### 改什么

一致性测试，约 20 行：同一个 corpus，断言

```
table_by_name(bare) is None   ⟺   rvgd 的 phys_to_table[bare] is None
```

可以为可测性把 rvgd 建映射的那段抽成一个 helper，**也可以**在测试里复刻同一个循环。两种都行 —— 抽 helper 更好，但**不许为了「让 rvgd 调 table_by_name」把热路径改成 O(n²)**。

### 验收

- 合成歧义 corpus（照 `tests/test_corpus_table_by_name.py` 的形状，按**歧义名字**构造不是按表数）+ 有 `../BIRD-corpus` 时的跳过式真语料用例。
- 两边对同一批裸名的 `None` / 非 `None` **完全一致**。
- 热路径语义零变化：`pytest tests/` 全绿，且 rvgd 的检索相关测试一条不改。

---

## 交付顺序

```
N9  ──►  N10        （硬顺序：先删 driver，少改 11 处）
N10a                （并行，零文件冲突）
```

三个 PR，一项一个。N10 内部建议再拆两笔：**先**「schema-bump 守卫测试（红）」，**后**「删 knob + bump（绿）」—— 这样红的证据是行为层的，不是签名错误。（M1 的 N1 就在这上面栽过一次：`TypeError: unexpected keyword argument` 那种红什么都证明不了。）

## review 会挂在哪里

按会退回的概率排：

1. **N10 把 `test_eval_index.py:516` 的断言改宽了**而不是删成员。那条断言变红是它在干活。
2. **N10 只删了 CLI flag，manifest 字段留着**（「开工前 · 二」的选项 C）。留下一条没人能走、也没人测的代码路径。
3. **N9 直接 `rm tests/test_run_experiment_parity.py`。**里面 15 个 test 有一多半和 `run_experiment` 无关。
4. **N9 改了 ADR。**ADR 是历史。
5. **N9 机械替换 `gateway/` 那两句 docstring** 而没验证「哪些 connector 被真跑覆盖」这个主张在新 driver 下是否仍成立。
6. **没 bump `MANIFEST_SCHEMA_VERSION`。**删了 manifest 字段却不 bump，正是新加的那个守卫要拦的事 —— 如果守卫也没写，两个都算没做。
7. **N10a 为了「调用 `table_by_name`」把 rvgd 改成 O(n²)。**
8. **PR 描述没逐条报测试数的账。**这一批是全案唯一一次允许测试数下降的，所以报账要求更严，不是更松。

## 这一批做完之后

M3 的出口判据（[near-term-plan.md](near-term-plan.md) 那张表）：`grep -rn "run_experiment\|skip_agent\|git_sha_drift" src/` 零命中、`pytest` 全绿、rvgd ↔ `table_by_name` 一致性测试绿。

下一批是 **M4（N11–N14）**，四项里 **N12 要花钱**（Langfuse 侧要真跑，5 题小跑，跑之前先确认）。M1 遗留的那条也在 N12 一并验：**`generations.*.jsonl` 里逐层判决的端到端验收**，不单独为它付一次费。

M4 之前会碰到的一件事：**N11（实时可观测）和 N10 都改 `run_datalake.py` 的 argparse 与日志区。**M3 必须先落，否则 N11 写的进度输出要在 N10 删 flag 时再改一遍。
