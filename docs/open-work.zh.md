# 待办工作

_[English](open-work.md) · [简体中文](open-work.zh.md)_

这是唯一一份记录**还没做完**的清单。它取代了四份带日期的跟踪表
（`engineering-gaps-2026-07-16`、`eval-audit-backlog-2026-07-22`、
`clarification-sme-benchmark-build-plan`、`implementation-plan-notes-and-run-logging`），
它们已关闭的条目现在只留在 git 历史里。这里不记录任何设计决策，决策属于
[设计决策](design-decisions.zh.md)与 [adr/](adr/)。

## 正确性

| # | 是什么 | 在哪里 |
| --- | --- | --- |
| C3 | `ex_strict` 没有守卫：`validate_gold_hashes_live` 只用宽松（lenient）归一化器算哈希，再和 `gold.hash_lenient` 比。`hash_normalised_result_strict` 从未与 `gold.hash_strict` 对过账，运行却已经在信任 `ex_strict`。 | `eval/hash_grade.py` |
| C9 | 池化的 `_validate_corpora(corpora)` 不带连接器，所以在规模化运行下没有任何东西拿实际目录（catalog）校验过资产引用。 | `eval/run_datalake.py` |
| G8 | 评分器自检只在 5 行样本上验证过。真正的正面比对需要连上实际数据库。 | `eval/hash_grade.py` |

## 效率

| # | 是什么 | 在哪里 |
| --- | --- | --- |
| E1 | 交叉核对对每个问题、每个 arm 都重跑一次 gold **和** 预测，可 gold 与 arm 无关。按 `question_id` 把 gold 哈希记忆化即可。 | `eval/run_experiment.py` → `eval/ex.py` |
| E2 | 每个 corpus 从磁盘加载两次——一次给 solver，一次给 `_suspect_from_corpus`。 | `eval/run_experiment.py` |
| E3 | `profile_database` 每库跑两遍（baseline 与 curated 各自独立画像）。 | `curator/pipeline.py` |
| E4 | `--resume-curated` 时 baseline 仍无条件重建；`run_datalake` 已经用 `_has_yaml` 挡住了。 | `eval/run_experiment.py` |
| E5 | gold 自检为每个抽样库单开一个按 schema 固定的连接器，与随后共享的非固定 serve 连接器分离。 | `eval/run_datalake.py` |

## 实验设计

仪器是可靠的；设计还没有把要证明的东西隔离出来。按优先级：

| # | 是什么 | 为什么它挡住了结论 |
| --- | --- | --- |
| X1 | **没有等长的安慰剂 arm。** 每一级都是上一级内容的严格超集，所以"越靠后的级 token 越多"是构造出来的必然。应把 schema *Y* 的 corpus 拿去服务 schema *X* 的问题，并按 `context_chars` 对齐字节数。 | 没有它，任何 curated arm 的结果都与提示词长度混淆在一起。 |
| X2 | **`mask_only` 消融实验。** `_mark_columns_absent_from_gold` 会标记训练集 gold 从未碰过的每一列，而这层掩码覆盖了约 86% 测试问题的 gold 列。要把它与 join/metric/few-shot 分开。 | 头条的 decoy-touch 结果可能是机械产物，而不是关于元数据的证据。 |
| X3 | **`refute()` 直接抛 `NotImplementedError`**（`curator/adversary.py`），所以 `curated` 这一级的 adversary 只是结构校验器加两条置信度惩罚。要么把它实现，要么把这一级改成它实际的名字。 | 一个以某机制命名、而该机制并不运行的 arm。 |
| X4 | **到处都是单一随机种子。** 需要 ≥3 次 curator 抽样加一次 serve 复跑，才能把构建方差与服务方差分开。 | 最大的一次实跑只有 n=52，而 `curated`/`sme` 的正负号在连续两次运行之间翻转。 |
| X5 | **69 schema 的规模化运行**（8,134 训练 / 2,030 测试）至今只用 `--skip-agent` 跑过。 | 在设计所针对的规模上，还没有任何结果。 |
| X6 | **拒答闸门从未被触发。** BIRD 的问题全都可答，而 curator 从来不生成 `NegativeExampleAsset`（所有已生成的 corpus 里是 0 个文件），所以拒答两边都没有数据。 | `false_refusal_rate` 没有被测量；拒答与失败无法区分。 |

## Corpus 覆盖度

资产 schema 远比 curator 实际产出的东西丰富。要么把这些字段生成出来，要么从
`corpus/schemas.py` 删掉：

- `TermRelation` / `relation`——所有已生成的 corpus 里出现 **0** 次。
- `ColumnRole`——约 4,245 个已生成的表资产里只有 76 个设了它。
- `normative_force`——只出现过 `advisory`，`must_honour` 从未产出。
- `activation`——只出现过 `always`，`on_match` 从未产出，所以 ADR 0003 里那条
  触发词钉住（PIN）的检索通路完全没有数据在跑。
- `NegativeExampleAsset`——从未生成（见 X6）。

## 治理缺口

- 模拟 SME 的回答默认盖上 `status=certified`（`corpus/clarify.py`），而
  `pin_require_certified` 又拿这个状态当门槛。最高信任层级是模型自己盖上去的。
- `AssetBag.repair_references` / `repair_term_bindings` 在结构 adversary 闸门跑
  **之前**就把悬空引用自动修好了，所以那道闸门天然是绿的。
- 有 8 个 `Settings` 开关被盖章写进 `provenance.py`，却不约束任何东西。于是一次
  运行会报告一个它从未施加过的阈值。

## 已交付（不要重新规划）

ADR 0003 的 M1–M4 与 ADR 0004 的 M1–M2、M5 均已落地（`b157834`、`3ae4eec`、
`061b00b`）。`workers` 并发开关在 `99f517d` 落地。澄清协议与模拟 SME 随 D12–D14
落地。2026-07-25 的度量完整性大修（`stages.py`、`stage_events.jsonl`、
`runs/index.jsonl`）已完成，且 2026-07-26 之前产出的每一个数字都已作废。
