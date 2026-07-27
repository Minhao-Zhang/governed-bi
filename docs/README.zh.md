# governed-bi 设计

_[English](README.md) · [简体中文](README.zh.md)_

面向 agentic BI / Generative-BI 系统的设计：自然语言问题 → 基于企业关系型数据的接地（grounded）、受治理（governed）、可审计（auditable）的答案。

近期目标是打造一个**在 SQLite 上得到验证的展示系统**（对其他引擎留有方言可插拔接口），它从一批已知良好的种子查询出发、逐步扩展出一个可审阅的语义层——这是*种子辅助的生长*，而非零先验的冷启动。企业级抽象已经以预留接口(seam)的方式接入，但默认处于关闭状态。评估基于自建的 [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) 数据集（执行准确率；记录成本）。

## 按此顺序阅读

1. [系统总览](system-overview.zh.md)：这是什么、两个 harness、当前状态。
2. [架构](architecture.zh.md)：完整设计（主干(spine)、内核(kernel)、服务、存储、流程、评测、环境）。
3. [设计决策](design-decisions.zh.md)：以 ADR 形式呈现的 D1-D18（+ 2026-07-15 审计处置），包含备选方案与权衡。
4. [资产模式](asset-schemas.zh.md)：每个资产的 YAML 字段规范（Facts 层 / Inference 层 / Audit 层）。
5. [Curator](curator.zh.md)：构建侧的 proposer + adversary 循环。如需查看逐字提示词，见 [Curator LLM 调用全流程](curator-llm-call.zh.md)。
6. [Analyst](analyst.zh.md)：服务侧受治理的 agentic 内核 + 护栏(guardrails)。如需查看逐字提示词，见 [Analyst LLM 调用全流程](analyst-llm-call.zh.md)。
7. [Viz](viz.zh.md)：只读审计面(surface)——presenter 视图模型加上 `governed_bi.api` HTTP API，用于浏览语义层并与受治理 Analyst 对话（交互式 UI 是一个独立项目）。
8. [度量](measurement.zh.md)：eval harness 记录了什么、失败会定位到哪里——数字看着不对时先读这篇。
9. [提示词变体实验](prompt-experiments.zh.md)：提示词注册表、一次运行怎么选变体、什么被盖章记到哪里，以及怎么判断一个测出来的失败到底该换哪个变体。
10. [术语表](glossary.zh.md)：规范术语。

支撑本设计的[外部设计资料来源](references.zh.md)。

## 使用本仓库

上述设计文档描述的是预期中的系统。至于当前实际运行的部分（corpus 层与开发工作流）：

- [演练](walkthrough.zh.md)：克隆 → 校验 → 提出第一个问题。**从这里开始。**
- [使用指南](usage.zh.md)：安装、validate CLI，以及可编程调用的 corpus API。
- [Corpus 编写](corpus-authoring.zh.md)：逐步编写并校验 corpus 资产。

读代码时，另有两份逐步调用轨迹可以对照：[Analyst 时序](analyst-sequence.md)与
[Curator 时序](curator-sequence.md)（英文）。

## 决策记录（ADR）

ADR 记录的是某个时点的决策。它不会为了跟上后来的现实而改写。要推翻，就另写一份
取代它的 ADR。所以 ADR 里出现看起来过时的说法，那是有意留下的历史。

| ADR | 状态 |
|---|---|
| [0001 LangGraph Server 聊天运行时](adr/0001-langgraph-server-chat-runtime.zh.md) | 2026-07-10 接受；部分被 0002 取代 |
| [0002 受治理的 agentic 服务运行时](adr/0002-governed-agentic-serve-runtime.zh.md) | 已接受并实现（`d2fdd6a`），是唯一的服务路径 |
| [0003 受治理的 note 与三模态检索](adr/0003-governed-notes-tri-modal-retrieval.zh.md) | 2026-07-22 接受（只到设计，D17）；尚未实现 |
| [0004 本地优先的会话与运行日志](adr/0004-local-first-conversation-run-logging.zh.md) | 2026-07-22 接受（D18）；尚未开工 |

## 工作文档（`plans/`）与评审

这些是带日期的工作文档，不是规范设计。凡与上面的设计文档冲突，以上面为准。
**本仓库现在没有任何可引用的评测数字：2026-07-26 之前产出的数字全部作废。**

*仍然有效：*

- [实验操作手册](plans/experiment-runbook.zh.md)：跑什么、按什么顺序跑，以及一个数字要满足哪些条件才值得引用。**做评测就从这里开始。**
- [Data-lake 运行](plans/datalake-run.zh.md)：多 schema 池化运行（D15）的操作手册与状态。
- [评测审计待办](plans/eval-audit-backlog-2026-07-22.md)（英文）：eval harness 上仍未关闭的正确性与效率条目。
- [note 与运行日志的实施计划](plans/implementation-plan-notes-and-run-logging.md)（英文）：ADR 0003 + 0004 的建议构建顺序。
- [澄清协议 + SME 基准构建计划](plans/clarification-sme-benchmark-build-plan.md)（英文）：D12–D14；增量 1–2 已交付，规模化运行仍未开始。
- [HITL 澄清契约](plans/hitl-clarification-contract.zh.md)：服务时向人追问的服务端 ↔ 前端契约，服务端已实现。
- [Agent 步骤可视化](plans/agent-step-visualization.md)（英文）：前端怎么展示受治理服务流的每一步。

*已归档记录，留作历史，不作为指导：*

- [评测阶梯结果](plans/eval-ladder-results.md)（英文）：v5 那次运行。**数字已作废**；留下的是当时的 arm 定义与术语。
- [评测并发设计](plans/eval-concurrency-design.md)（英文）：`workers` 开关，2026-07-23 交付。
- [工程缺口 2026-07-16](plans/engineering-gaps-2026-07-16.md)（英文）：审计跟踪表，少数条目仍搁置。
- [Schema 限定的规模化风险](plans/schema-qualification-scale-risk.md)（英文）：2026-07-17 通过移除 `multi_schema` 模式解决。
- [术语重构](plans/terminology-refactor.md)（英文）：2026-07-16 的执行记录，**在阶梯/arm 口径上已被取代**——请改用[术语表](glossary.zh.md)与操作手册。
- [流水线设计](pipeline-design.md)（英文）：curator/构建侧流水线；服务侧最终以另一种方式落地，相关章节已删。

## 主干（不可妥协项）

- **两个平面(planes)。** 语义/控制平面（版本化配置 + markdown，通过 PR/CI 发布）与数据平面相互分离，后者只执行通过护栏检查的 SQL。语义只定义一次，由人类掌控。
- **权限是确定性的；推理可以是 agentic 的。** 问题可以很宽泛、模型在一个有界的 agentic 循环里推理，但*什么能执行、什么被信任、什么被记录*由中间件固定，而非模型自行裁量（ADR 0002 反转了此前"绝不自主循环"的规则）。但 SQL 必须收窄。
- **失败即拒（fail-closed）。** 超出范围(out-of-scope)/覆盖缺失(missing-coverage)/触发护栏(tripped-guardrail)，任何一种情况都只会返回拒答或澄清性问题，绝不会给出一个自信却错误的数字。

## 文档与代码的对应关系

| 文档 | 对应的包区域 |
|---|---|
| [资产模式](asset-schemas.zh.md)、[设计决策](design-decisions.zh.md) D9 | `src/governed_bi/corpus/` |
| [Curator](curator.zh.md) | `src/governed_bi/curator/` |
| [Analyst](analyst.zh.md)、[架构](architecture.zh.md) §6 | `src/governed_bi/analyst/`、`gateway/`、`graph/`、`retrieval/`、`memory/` |
| [架构](architecture.zh.md) §8、[度量](measurement.zh.md) | `src/governed_bi/eval/`、`src/governed_bi/stages.py` |
| [提示词变体实验](prompt-experiments.zh.md) | `src/governed_bi/prompts/` |
| [Viz](viz.zh.md) | `src/governed_bi/viz/` |
| [架构](architecture.zh.md) §9（环境开关(environment toggles)） | `src/governed_bi/config.py` |
