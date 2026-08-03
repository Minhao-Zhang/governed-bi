# governed-bi 设计

> **本文档描述的是 v1,已在 commit `2347ae3` 中删除。** 保留在原路径是因为它是仓库的入口之一,
> 目前正依据 [ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) 与
> [ADR 0006](adr/0006-execution-time-governance.md) 重写。在重写完成之前,
> 请把本文中所有具体的说法 —— 模块名、文件路径、工具名、实测数字 —— 都当作历史记录,
> 而不是对当前系统的描述。v1 的其余文档在 [`docs/v1/`](v1/),
> 哪些实测结论经复核后仍然成立、哪些已作废,记在 [`lessons-from-v1.md`](lessons-from-v1.md)。

_[English](README.md) · [简体中文](README.zh.md)_

面向 agentic BI / Generative-BI 系统的设计：自然语言问题 → 基于企业关系型数据的接地（grounded）、受治理（governed）、可审计（auditable）的答案。

它从一批已知良好的种子查询出发、逐步扩展出一个可审阅的语义层——这是*种子辅助的生长*，而非零先验的冷启动。**Postgres** 是真正跑过的路径；SQLite 只作为离线测试 / CI 的底座。企业级抽象已经以预留接口(seam)的方式接入，但默认处于关闭状态。评估基于自建的 [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) 数据集（执行准确率；记录成本）。

## 按此顺序阅读

1. [架构](architecture.zh.md)：完整设计（主干(spine)、内核(kernel)、服务、存储、流程、评测、环境）。
2. [设计决策](design-decisions.zh.md)：以 ADR 形式呈现的 D1–D19（+ 2026-07-15 审计处置），包含备选方案与权衡。
3. [资产模式](v1/asset-schemas.md)：每个资产的 YAML 字段规范（Facts 层 / Inference 层 / Audit 层）。
4. [Curator](v1/curator.md)：构建侧的 proposer + adversary 循环。如需查看逐字提示词，见 [Curator LLM 调用全流程](v1/curator-llm-call.md)。
5. [Analyst](v1/analyst.md)：服务侧受治理的 agentic 内核 + 护栏(guardrails)。如需查看逐字提示词，见 [Analyst LLM 调用全流程](v1/analyst-llm-call.md)。
6. [Viz](v1/viz.md)：审计面(surface)——presenter 视图模型加上 `governed_bi.api` HTTP API，用于浏览语义层并与受治理 Analyst 对话（corpus 写操作由 `allow_edit` 门控；交互式 UI 是一个独立项目）。
7. [度量](v1/measurement.md)：eval harness 记录了什么、失败会定位到哪里——数字看着不对时先读这篇。
8. [提示词变体实验](v1/prompt-experiments.md)：提示词注册表、一次运行怎么选变体、什么被盖章记到哪里，以及怎么判断一个测出来的失败到底该换哪个变体。
9. [术语表](glossary.zh.md)：规范术语。

[待办工作](v1/open-work.md)是唯一一份记录"还没做完"的清单。

支撑本设计的[外部设计资料来源](v1/references.md)。

## 使用本仓库

上述设计文档描述的是预期中的系统。至于当前实际运行的部分（corpus 层与开发工作流）：

- [使用指南](usage.zh.md)：安装、校验示例 corpus、提出第一个问题。**从这里开始。**
- [Corpus 编写](v1/corpus-authoring.md)：逐步编写并校验 corpus 资产。

读代码时，另有两份逐步调用轨迹可以对照：[Analyst 时序](v1/analyst-sequence.md)与
[Curator 时序](v1/curator-sequence.md)（英文）。

## 决策记录（ADR）

ADR 记录的是某个时点的决策。它不会为了跟上后来的现实而改写。要推翻，就另写一份
取代它的 ADR。所以 ADR 里出现看起来过时的说法，那是有意留下的历史。

| ADR | 状态 |
|---|---|
| [0001 LangGraph Server 聊天运行时](adr/0001-langgraph-server-chat-runtime.md) | 2026-07-10 接受；部分被 0002 取代 |
| [0002 受治理的 agentic 服务运行时](adr/0002-governed-agentic-serve-runtime.md) | 已接受并实现（`d2fdd6a`），是唯一的服务路径 |
| [0003 受治理的 note 与三模态检索](adr/0003-governed-notes-tri-modal-retrieval.md) | 2026-07-22 接受（D17）；已构建——`NoteAsset`、`note_inject.py`、`retrieval/triggers.py`、`read_notes` / `grep_notes`、`[notes]` 配置 |
| [0004 本地优先的会话与运行日志](adr/0004-local-first-conversation-run-logging.md) | 2026-07-22 接受（D18）；已构建——`run_log.py`、`[logging]` 配置、`prune_full_content` 保留策略 |

> **证伪条件（falsifier）。** 能让我们判定"corpus 没有用"的那一个结果——arm 配对、指标、
> 分层、效应量、curator 抽样次数——写在
> [`plans/experiment-runbook.md`](v1/plans/experiment-runbook.md#the-result-that-would-make-us-abandon-the-corpus-thesis)里。
> 它是在跑之前就定好的，至今还没被评估过：需要在 69 个 schema 上独立跑三次 curator，而目前
> 还没有这样的运行。

## 工作文档（`plans/`）与评审

这些是带日期的工作文档，不是规范设计。凡与上面的设计文档冲突，以上面为准。
**本仓库现在没有任何可引用的评测数字：2026-07-26 之前产出的数字全部作废。**

*仍然有效：*

- [实验操作手册](v1/plans/experiment-runbook.md)：跑什么、按什么顺序跑，以及一个数字要满足哪些条件才值得引用。**做评测就从这里开始。**
- [Data-lake 运行](v1/plans/datalake-run.md)：多 schema 池化运行（D15）的操作手册与状态。
- [服务透明度](v1/plans/serve-transparency.md)及其[交接文档](v1/plans/serve-transparency-handoff.md)（英文）：把 agent 看到的**输入**显示到界面上。四项改动已落地两项。
- [SME 通道修复](v1/plans/sme-channel-repair.md)（英文）：`curated_sme` 这一臂为什么没带来变化，以及该按什么顺序修。
- [评测重建](v1/plans/eval-rebuild.md)（英文）：2026-07-26 之前的数字为什么全部作废，以及跟着要做的四项修复。

另有两份计划已经做完，文件也删了。它们定下的接口契约挪进了[分析师](v1/analyst.md)（英文）：[治理事件流](v1/analyst.md#the-event-contract-per-step)和[服务时澄清](v1/analyst.md#serve-time-clarification-hitl)。删的是计划，不是能力。模块加深计划一项未做就删掉了，其中还要紧的条目留在[待办工作](v1/open-work.md)（英文），剩下的翻 git 历史。

已关闭的跟踪表和被取代的计划不再以文件形式保留——git 历史就是归档。它们里面还没做完的条目
都收进了[待办工作](v1/open-work.md)。

## 主干（不可妥协项）

- **两个平面(planes)。** 语义/控制平面（版本化配置 + markdown，通过 PR/CI 发布）与数据平面相互分离，后者只执行通过护栏检查的 SQL。语义只定义一次，由人类掌控。
- **权限是确定性的；推理可以是 agentic 的。** 问题可以很宽泛、模型在一个有界的 agentic 循环里推理，但*什么能执行、什么被信任、什么被记录*由中间件固定，而非模型自行裁量（ADR 0002 反转了此前"绝不自主循环"的规则）。但 SQL 必须收窄。
- **在 serve 默认配置下失败即拒。** 在 `grade_semantic_failures=False`（serve 的默认值）下，超出范围(out-of-scope)/覆盖缺失(missing-coverage)/触发护栏(tripped-guardrail)会返回拒答或澄清性问题——而不是一个自信却错误的数字。分级投递（graded delivery，目前在 eval driver 里是开着的）可以把部分 L4/L5 失败重新以 `unverified` 行的形式送出；这不是 serve 的默认行为。

## 文档与代码的对应关系

| 文档 | 对应的包区域 |
|---|---|
| [资产模式](v1/asset-schemas.md)、[设计决策](design-decisions.zh.md) D9 | `src/governed_bi/corpus/` |
| [Curator](v1/curator.md) | `src/governed_bi/curator/` |
| [Analyst](v1/analyst.md)、[架构](architecture.zh.md) §6 | `src/governed_bi/analyst/`、`gateway/`、`graph/`、`retrieval/`、`memory/` |
| [架构](architecture.zh.md) §8、[度量](v1/measurement.md) | `src/governed_bi/eval/`、`src/governed_bi/stages.py` |
| [提示词变体实验](v1/prompt-experiments.md) | `src/governed_bi/prompts/` |
| [Viz](v1/viz.md) | `src/governed_bi/viz/` |
| [架构](architecture.zh.md) §9（环境开关(environment toggles)） | `src/governed_bi/config.py` |
