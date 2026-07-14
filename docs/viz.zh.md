# Agentic BI 可视化

_[English](viz.md) · [简体中文](viz.zh.md)_

面向 [Agentic BI System](system-overview.zh.md) corpus 的**只读审计面（audit surface）**。
本仓库**不提供任何内置 UI**；它提供的是 UI 所读取的部件——与 UI 无关的 `presenter`
视图模型，以及 `governed_bi.api` HTTP/JSON API——因此你可以浏览并审计 AI 构建的层，并
提出问题以查看治理后的答案及其可靠性标记。交互式 UI 是一个独立项目（见
[ui-frontend-design.md](ui-frontend-design.zh.md)）。

**本仓库有意不在这里实现 corpus 编辑与发起 PR 的功能。** 由于 git 是唯一的事实来源
（D9），一次修正就是「编辑一个文件 + 发起 PR」，这件事可以由通用的 git/PR 工具配合
CI 来完成（开发环境），也可以由企业应用来完成（生产环境）。本仓库拥有编辑器会复用的
写入*原语*，但不拥有交互式编辑器本身，也不拥有 PR 编排。

> 实现位置：[`src/governed_bi/viz/`](../src/governed_bi/viz/)（只读）。
> `presenter.py` 保存与 UI 无关的视图模型；[`governed_bi.api`](../src/governed_bi/api/)
>（可选的 `api` extra）通过 HTTP/JSON 提供这些视图模型。运行方式：
> `uv run uvicorn --factory governed_bi.api:create_app`（交互式文档在
> http://localhost:8000/docs）。

## 范围：引擎 vs 产品

审计面的*读取*侧贴近引擎：是架设在 corpus 之上的开发/审计/演示工具。其*编辑*侧
属于产品层面：一个交互式表单，加上把该引擎嵌入其中的 git/PR 工作流。将编辑排除在外，
避免把 UI 框架和 git/PR 编排固化进库里，也符合两产品拆分的格局（一个通用的公开引擎；
一个私有的企业 fork，owner + PR + CI 评审真正落地在其中）。

| 关注点 | 归属位置 |
|---|---|
| 资产 schema（用于 schema 驱动的表单） | 本仓库（`corpus/schemas`） |
| 将编辑结果序列化回 YAML | 本仓库（`corpus/serialize.write_corpus`） |
| 在 PR 上做校验（CI 关卡） | 本仓库（`corpus/validate` + CLI） |
| 只读审计面（health / tables / assets / skills / ask） | 本仓库（`viz/presenter` + `governed_bi.api`） |
| 交互式编辑表单 + git/PR 编排 | 下游工具 / 企业应用 |

## 写入路径（下游）

编辑功能被有意设计为只是「编辑一个文件 + 发起 PR」，复用现有的 git 机制，而不是另起
一套定制的存储方案：

1. 人工在任意编辑器中编辑 corpus 的 YAML/MD，或使用下游某个基于 schema 生成表单
   （form-over-schema）、并通过 `write_corpus` 完成序列化的工具。
2. 提交并发起 PR（git / GitHub / `gh`）。
   - **Dev/BIRD：** 开发者本人就是评审者，因此可以直接提交（commit）。
   - **Prod/企业：** 真正的 owner + PR + CI（D6）。adversary 已经预先做过筛选，人工
     只需要认证 draft 质量的资产。
3. CI 运行引用完整性检查（`governed-bi validate ...`）。

这与修正循环（correction loop，D8；此时 memory 与 corpus 的界限归于统一）属于同一套
机制。一次 serve 端的修正可以预先填充一份草稿编辑，供人工确认，同样由拥有编辑功能的
那个工具完成。如果下游编辑器需要，本仓库也可以合理地在此提供一个小型的 corpus 级辅助
函数（一个会追加 `source: human` 溯源（provenance）条目、并使状态发生
`draft -> certified` 翻转的 `certify`）；目前尚未实现。

## 可编辑性模型 = 档位模型（编辑器必须遵守的契约）

无论谁构建这个编辑器，都要遵守本仓库定义并校验的档位契约：

| 档位 | 编辑规则 |
|---|---|
| **Facts** | **只读**：catalog 层面的事实；人工永远不会编辑 dtypes/samples |
| **Inference** | **可编辑**：由人工纠正 curator 的产出（description、role、references、reliability、confidence） |
| **Audit** | 系统写入；人工编辑只会追加一条溯源条目（`source: human`、who、when、reason） |
| **Governance** | **仅限人工**：`governance.excluded` 就是在这里设置的 |

编辑一项资产会使其状态发生 `draft -> certified` 的翻转（即认证行为，D6），审计轨迹也
随之变为**三方：proposer -> adversary -> human**。

## 视图

本仓库内置（只读）、基于 corpus 计算得到的 `presenter` 视图模型，并由 `governed_bi.api`
通过 HTTP/JSON 提供，供独立的 UI 渲染：

- **Chat**。对受治理 server 流程的多轮对话（在 `POST /chat` 提供）；每个答案都展示
  双轴标记、SQL 与溯源轨迹，追问会通过工作记忆（D8）回灌。
- **corpus 健康度**。资产数量、CI 状态，以及评审者最先要梳理的标记：疑似
  列数、被排除资产数、低置信度连接数。
- **表视图**。Facts 与 Inference 并排展示；标记为 `suspect` 和 `excluded` 的列会
  附带原因；并显示逐列的溯源状态。
- **资产**。非表类资产（连接、指标、术语、规则、few-shot 示例、反例），可按类型
  筛选，并显示溯源状态。
- **技能**。以渲染后的 markdown 呈现。

设计愿景，尚未在本仓库实现（更完整的审计面，或下游产品）：

- **FK 图**（连接投影，边按置信度着色）。
- **Gold-diff**（BIRD：按资产对比 curator 与 gold）。
- **搜索**（BM25 加可选的语义搜索）。
- **可编辑**表单，以及**save -> PR** 按钮（参见前文的写入路径）。

## 简单是设计使然

这是一个基于 corpus 计算得到的只读面：与 UI 无关的视图模型（`presenter`）由
`governed_bi.api` 通过 HTTP/JSON 提供，因此前端可以替换，自身不携带任何业务逻辑。没有
SaaS、没有多租户，也没有仓库内的编辑或 PR 编排。

延伸阅读：[设计决策](design-decisions.zh.md)（D6 归属、D9 corpus 契约、D10
curator）、[资产 schema](asset-schemas.zh.md)、[Curator](curator.zh.md)、
[Server](server.zh.md)。
