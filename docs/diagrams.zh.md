# 架构与流程图表

_[English](diagrams.md) · [简体中文](diagrams.zh.md)_

本目录按细节粒度拆分，以便单独审阅和修正每一张 Mermaid 图。这些图特意区分了已实现的代码与设计层脚手架。

> **实现说明：**从提问到应答的整条流水线均已实现并通过测试（corpus、gateway 及五层护栏、graph 与 Steiner 规划器、retrieval、上下文组装、带自修复循环和 SQL 缓存的 serve 流程、memory、eval、viz），且两套 agent harness（LangGraph serve DAG、deepagents curator）都已实现，置于 `agents` extra 之后。这些图展示的是契约；少数标注为“future”的节点是预留接口(seam)（例如 Neo4j、实时模型编目）。

## 按复杂度分级的推荐阅读顺序

### L0：定位

1. [系统总览图](diagrams/overview.zh.md)
   - 当前代码状态
   - 目标架构
   - 两套 harness 的拆分
   - 环境开关

### L1：子系统流程

2. [Corpus 图表](diagrams/corpus.zh.md)
   - Corpus 消费契约
   - 加载器内部机制
   - 校验内部机制
   - Pydantic 资产模型
   - Graph 投影的边分类体系
3. [Server 图表](diagrams/server.zh.md)
   - 回答流水线
   - 提问序列图
   - SQL 语义缓存序列图
   - 拒答闸(Refuse-gate)序列图
   - 可靠性/治理执行
4. [Curator 图表](diagrams/curator.zh.md)
   - 构建循环数据流
   - 资产生命周期状态机
   - Proposer/adversary 序列图
5. [Viz 图表](diagrams/viz.zh.md)
   - 驾驶舱子系统
   - 审计/认证序列图
6. [Eval 图表](diagrams/eval.zh.md)
   - 三臂评测
   - 拒答闸(Refuse-gate)评测

### L2/L3：实例演练与深入解析

7. [啤酒厂示例图表](diagrams/beer-factory.zh.md)
   - 示例语义微图
   - 示例评分最高品牌问题序列图
   - 拒答路径示例

## 来源对照表

| 图表文件 | 主要来源 |
|---|---|
| [overview](diagrams/overview.zh.md) | `docs/architecture.md`、`docs/system-overview.md`、`src/governed_bi/config.py` |
| [corpus](diagrams/corpus.zh.md) | `src/governed_bi/corpus/`、`docs/asset-schemas.md`、`src/governed_bi/graph/projection.py` |
| [server](diagrams/server.zh.md) | `docs/server.md`、`src/governed_bi/server/`、`gateway/`、`retrieval/`、`graph/` |
| [curator](diagrams/curator.zh.md) | `docs/curator.md`、`src/governed_bi/curator/` |
| [viz](diagrams/viz.zh.md) | `docs/viz.md`、`src/governed_bi/viz/` |
| [eval](diagrams/eval.zh.md) | `docs/architecture.md` §8、`src/governed_bi/eval/` |
| [beer-factory](diagrams/beer-factory.zh.md) | `corpus/beer_factory/` |
