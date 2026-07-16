# 外部设计来源

_[English](references.md) · [简体中文](references.zh.md)_

设计文档引用了以下这些来源。它们存在于本仓库之外（设计库 / 上游项目），此处列出是为了让文中的引用可以解析。

| 来源 | 接地的内容 |
|---|---|
| **[BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation)** | 近期的评测数据集：4 个数据库版本、约 10k 条经过验证的问答、诱饵清单、重命名映射。这是一个*独立的上游仓库*，负责产出经过验证的数据和清单。它明确将"利用这些陷阱的下游代理"排除在范围之外：那个下游代理正是*本系统*。 |
| **BIRD Bench Obfuscation Methodology** | 混淆维度（诱饵 / 重命名 / 外键隐藏 / 重写）是如何构造的。 |
| **Data Agent Memory Design Overview**（2026-07-05） | 记忆策略、可复用的数值（TTL、阈值）、"curation beats accumulation"（策展胜于堆积）法则，以及 SQL 语义缓存设计。 |
| **How Anthropic enables self-service data analytics with Claude** | corpus 腐化（不加维护时约 95%→65%/月）；skills 作为最高价值的杠杆点（<21% → 95%+）；对原始 corpus 直接 grep 检索得到的零效应结果（null result）。 |
| **《从数据到智能》**（*From Data to Intelligence*） | 第 3 章中 9 种资产类型的语义层，经过改编（将创作主体反转）后，转化为 corpus contract（D9）。 |
| **私有企业分叉版本** | 一个私有的并行分叉版本（第二阶段），在企业规模上复用本引擎；面临同样的无人负责、缺乏人力的处境。不在本仓库范围内。 |

## 仓库边界

BIRD-Obfuscation（上游）负责产出数据和清单。**本仓库是下游代理**，负责消费这些数据：构建语义层（curator）、回答问题（analyst），并依据执行准确率（execution accuracy）评分。
