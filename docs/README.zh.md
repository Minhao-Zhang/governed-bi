# governed-bi 设计

_[English](README.md) · [简体中文](README.zh.md)_

面向 agentic BI / Generative-BI 系统的设计：自然语言问题 → 基于企业关系型数据的接地（grounded）、受治理（governed）、可审计（auditable）的答案。

近期目标是打造一个**在 SQLite 上得到验证的展示系统**（对其他引擎留有方言可插拔接口），它从一批已知良好的种子查询出发、逐步扩展出一个可审阅的语义层——这是*种子辅助的生长*，而非零先验的冷启动。企业级抽象已经以预留接口(seam)的方式接入，但默认处于关闭状态。评估基于自建的 [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) 数据集（执行准确率；记录成本）。

## 按此顺序阅读

1. [系统总览](system-overview.zh.md)：这是什么、两个 harness、当前状态。
2. [架构](architecture.zh.md)：完整设计（主干(spine)、内核(kernel)、服务、存储、流程、评测、环境）。
3. [图表](diagrams.zh.md)：Mermaid 架构图、数据流图与用户序列图。
4. [设计决策](design-decisions.zh.md)：以 ADR 形式呈现的 D1-D10，包含备选方案与权衡。
5. [资产模式](asset-schemas.zh.md)：每个资产的 YAML 字段规范（Facts 层 / Inference 层 / Audit 层）。
6. [Curator](curator.zh.md)：构建侧的 proposer + adversary 循环。
7. [Server](server.zh.md)：服务侧的 LangGraph 流程 + 护栏(guardrails)。
8. [Viz](viz.zh.md)：只读审计驾驶舱——浏览语义层并与受治理 server 对话。
9. [术语表](glossary.zh.md)：规范术语。

支撑本设计的[外部设计资料来源](references.zh.md)。

## 使用本仓库

上述设计文档描述的是预期中的系统。至于当前实际运行的部分（corpus 层与开发工作流）：

- [演练](walkthrough.zh.md)：克隆 → 校验 → 提出第一个问题。**从这里开始。**
- [使用指南](usage.zh.md)：安装、validate CLI，以及可编程调用的 corpus API。
- [Corpus 编写](corpus-authoring.zh.md)：逐步编写并校验 corpus 资产。

## 主干（不可妥协项）

- **两个平面(planes)。** 语义/控制平面（版本化配置 + markdown，通过 PR/CI 发布）与数据平面相互分离，后者只执行通过护栏检查的 SQL。语义只定义一次，由人类掌控。
- **确定性 DAG + 条件路由，而非自主式 ReAct。** 问题可以很宽泛，但 SQL 必须收窄。
- **失败即拒（fail-closed）。** 超出范围(out-of-scope)/覆盖缺失(missing-coverage)/触发护栏(tripped-guardrail)，任何一种情况都只会返回拒答或澄清性问题，绝不会给出一个自信却错误的数字。

## 文档与代码的对应关系

| 文档 | 对应的包区域 |
|---|---|
| [资产模式](asset-schemas.zh.md)、[设计决策](design-decisions.zh.md) D9 | `src/governed_bi/corpus/` |
| [图表](diagrams.zh.md) | 横跨 `src/governed_bi/` 与 `corpus/` 的端到端映射 |
| [Curator](curator.zh.md) | `src/governed_bi/curator/` |
| [Server](server.zh.md)、[架构](architecture.zh.md) §6 | `src/governed_bi/server/`、`gateway/`、`graph/`、`retrieval/`、`memory/` |
| [架构](architecture.zh.md) §8 | `src/governed_bi/eval/` |
| [Viz](viz.zh.md) | `src/governed_bi/viz/` |
| [架构](architecture.zh.md) §9（环境开关(environment toggles)） | `src/governed_bi/config.py` |
