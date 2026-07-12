# Agentic BI 资产 Schema

_[English](asset-schemas.md) · [简体中文](asset-schemas.zh.md)_

这是 [Agentic BI 系统](system-overview.zh.md) corpus 的逐资产 YAML 字段规范。具体化了
[设计决策](design-decisions.zh.md)中的 **D9**（Git+YAML 类型化资产，由 curator 撰写、
人工审核）；存储方案的理由见[架构](architecture.zh.md)第 5 节；术语定义见
[术语表](glossary.zh.md)。改编自 *《从数据到智能》* 第 3 章，但撰写模式方向相反。

> 这是权威的字段规范。Pydantic 实现见
> [`src/governed_bi/corpus/schemas.py`](../src/governed_bi/corpus/schemas.py)；
> ID 约定见 [`ids.py`](../src/governed_bi/corpus/ids.py)；CI
> 引用完整性检查器见
> [`validate.py`](../src/governed_bi/corpus/validate.py)。

## 两大原则（基石）

- **P1：三个字段层。** 每个资产的字段分为三层：**Facts**（从 catalog/数据中读取，绝不推断）、**Inference**（由 curator 撰写，或由 gold 填充；这正是语义层）、以及 **Audit**（记录做出该推断的理由，仅供参考）。不同层遵循不同的规则（见下文）。
- **P2：字段通用，只有取值才是项目特定的。** 没有任何字段名是 BIRD 专属的。BIRD、企业级部署，以及未来的任何项目，共享*完全相同的 schema*；只有取值（用哪个 DB、哪种 SQL 方言、哪些 `source_refs`）会不同。BIRD 评测专属的规则（例如 leakage guard）存在于 eval harness 中，绝不存在于 schema 里。

## 两种表示形式：YAML 表示结构，Markdown 表示流程

该 corpus **并非只有 YAML。** 存在两种表示形式，按访问模式划分：

- **YAML 类型化资产**承载*结构化、原子化、按实体划分*的内容：Facts 加各类定义（`table`/`column`/`join`/`metric`/`term`/`rule`/`few_shot`/`negative_example`）。由机器解析、经 CI 检查、投影到图中，并作为离散单元被检索。
- **Markdown skill / 参考文档**承载*行文式、跨实体、程序性*的内容：路由触发条件、注意事项（gotcha）、查询模式、领域概览。通过检索（向量 + BM25）获取，并以叙述形式注入。它们**通过 ID 引用 YAML 资产，绝不重复其数据。**

为什么两者都需要：你没法对一段行文式内容做 CI 检查或投影进图，也没法把"如果问题是关于收入的，就从交易事实表出发，不要用品牌零售价"这样跨实体的流程干净地表达成某个逐列字段，因为这本身就是跨实体的流程。Anthropic 和这本书采用的是同样的划分方式。

> **Skill 是价值最高的产出，且只有 curator 才能产出**
>
> Anthropic 的结果：同一模型，没有 skill 时**低于 21%**，有 skill 时**95%以上**。
> 这是单一的最大杠杆。Skill **没有 gold 对应物**（manifest 中没有任何东西能推导出
> 它），因此 **Arm 3 没有 skill**。这正是为什么 curator（Arm 2）能在对 skill 敏感的
> 问题上*超越* gold 上限（D4）的原因。

## 消费契约（谁读取哪一层）

| 消费方 | Facts | Inference | Audit |
|---|---|---|---|
| **Server**（SQL 生成） | ✅ | ✅ | ❌ **绝不注入** |
| **Viz / 审计界面** | ✅ | ✅ | ✅ |
| **Gold-diff**（Arm 2 对比 Arm 3） | n/a（在所有 arm 中相同） | ✅ 该 diff 的目标 | n/a |
| **检索索引**（R/V/G/D） | ✅ | ✅ | ❌ |

loader 强制执行这一契约：server 的上下文仅由 Facts 加 Inference 构建。因此 Audit 层的文本（证据、溯源）可以按人类需要写得再详尽，也不会消耗 server 的 token，也不会引入噪声。如果 Audit 层把文件撑得过大，退路是拆分成 sidecar 文件（在 BIRD 的规模下尚不需要）。

## 治理覆盖（由人类撰写，位于三层字段之外）

有一个字段既不是由 catalog 撰写、也不是由 curator 撰写，而是由**人类所有者**撰写（D6）：`governance.excluded`。在某一列或某个表上，当人类审核后将其设为 `true`，该资产就会从 server 所能看到的一切（检索、呈现给它的 schema、图）中**被彻底移除**，并且是在**所有环境中、没有开关、永久生效**。它仍会显示在 viz / 审计界面中（带有标记与原因），从而使这一排除动作可被审计；护栏 L3 也会将其硬性拦截，作为纵深防御的一层。

```yaml
# on tbl_beer_factory_transaction, column CreditCardNumber
governance:
  excluded: true
  reason: "PII (payment card number); never surface to the server"
  by: minhaoz
  at: "2026-07-08"
```

这与 curator 的 `reliability.status: suspect` 不同：

| | `reliability: suspect` | `governance.excluded` |
|---|---|---|
| 撰写者 | curator（AI），经 adversary 检查 | 人类所有者（经认证） |
| 含义 | “看起来不可靠” | “已决定：永不使用” |
| 服务时的效果 | 软性警告或硬性拦截（可按环境切换） | **彻底移除**，所有环境，无开关 |

升级路径：curator 标记为 `suspect` → 人类审核（D6） → 维持原状，或升级为 `excluded`。这一机制**排除在自主评测 arm 之外**（以使 Arm 2 保持纯 curator）；它是面向企业级部署的人机协同治理能力。

## 目录结构

```
corpus/
  <db>/
    tables/      tbl_<db>_<name>.yaml      # columns inline
    joins/       join_<left>_<right>.yaml
    few-shots/   fs_<db>_<n>.yaml
    terms/       term_<name>.yaml
    metrics/     metric_<name>.yaml
    rules/       rule_<name>.yaml
    negatives/   neg_<db>_<n>.yaml
    skills/      *.md                        # prose gotchas / query-patterns (not typed assets)
  _generated/    # search index, embeddings, compiled graph (derived, gitignored, rebuildable)
```

> **D15**：corpus 命名空间 `<db>`（上方目录、下方 ID 格式）建模的是一个 **schema**，而非一个数据库——一个数据库容纳多个 schema，跨 schema 的 join 以带限定名的 `schema.table` SQL 执行。将字段/目录名 `db` → `schema` 已决定，但尚未落地；**ID 取值保持不变**（`tbl_<schema>_<name>`），因此下文的 `<db>` 占位符维持原样。

## ID 约定（CI 用正则表达式检查）

| 资产 | ID 格式 | 示例 |
|---|---|---|
| table | `tbl_<db>_<name>` | `tbl_beer_factory_customers` |
| column *（内联；ID 由 loader 推导）* | `col_<db>_<table>_<physical>` | `col_beer_factory_customers_CustomerID` |
| join | `join_<left>_<right>` | `join_transaction_customers` |
| few_shot | `fs_<db>_<n>` | `fs_beer_factory_001` |
| term | `term_<name>` | `term_revenue` |
| metric | `metric_<name>` | `metric_revenue` |
| rule | `rule_<name>` | `rule_boolean_flags` |
| negative_example | `neg_<db>_<n>` | `neg_beer_factory_001` |

**物理 ↔ 含义桥梁**贯穿每一个表/列：`physical_name` 是该字段在实际数据库中存在的标识符（在 BIRD 中经过混淆处理，在企业数据中则本身就晦涩难懂）。SQL 输出的正是这个标识符；而 Inference 层承载的是它的*含义*。curator 的全部工作，就是为晦涩难懂的物理名称填充含义，这一点在 BIRD 与企业级部署中完全相同。

---

## 资产：`table`（带内联列）

```yaml
# tables/tbl_beer_factory_customers.yaml
asset_type: table
id: tbl_beer_factory_customers

# ── Facts (catalog/data) ──
db: beer_factory                       # scoping namespace = the schema this belongs to (code field still `db`; D15 renames it `schema`)
physical_name: customers               # identifier in the live DB
row_count: 554

# ── Inference (curator writes / gold fills; server-consumed) ──
description: "One row per customer of the root beer factory."
grain: "one row = one customer"
confidence: 0.9

columns:
  - # Facts
    physical_name: CustomerID
    physical_type: INTEGER             # verbatim from catalog, dialect-specific
    logical_type: integer              # normalized, portable (string/integer/decimal/date/datetime/boolean)
    nullable: true
    is_unique: true
    sample_values: [101811, 864896]
    # Inference
    description: "unique customer identifier"
    role: primary_key                  # primary_key | foreign_key | key | measure | dimension
    references: null                   # col id if FK
    reliability: { status: ok, note: null }   # status: ok | suspect ; note: prose (server-visible)
    confidence: 0.95

  - # Facts
    physical_name: ZipCode
    physical_type: INTEGER
    logical_type: integer
    nullable: true
    is_unique: false
    sample_values: [94256]
    # Inference
    description: "postal code, stored as an integer"
    role: dimension
    references: null
    reliability:
      status: suspect
      note: "Stored as INTEGER, so leading zeros are lost. Unreliable as a postal key or for display; cast/pad before use."
    confidence: 0.6
    # Governance (human-authored override; not curator, not gold)
    governance: { excluded: false }    # human sets true → asset removed everywhere the server sees
    # Audit
    audit:
      reliability_evidence: "declared INTEGER; east-coast ZIPs with leading zeros cannot round-trip"
      provenance: { source: curator, status: draft }

# ── Audit (table-level) ──
audit:
  provenance: { source: curator, status: draft }
```

## 资产：`join`（FK 由推断得出；BIRD 会隐去它）

```yaml
# joins/join_transaction_customers.yaml
asset_type: join
id: join_transaction_customers

# ── Facts (the referenced physical columns exist in the catalog) ──
left_table: tbl_beer_factory_transaction
right_table: tbl_beer_factory_customers
on: "transaction.CustomerID = customers.CustomerID"   # physical names

# ── Inference (the EXISTENCE of the edge is inferred) ──
cardinality: many_to_one               # inferred from uniqueness of the right key
cost: 1.0                              # Steiner-planner input (derivable from cardinality × row_counts)
confidence: 0.95

# ── Audit ──
audit:
  evidence: "declared foreign key; every sale has one buyer"
  provenance: { source: curator, status: draft }
```

## 资产：`few_shot`

```yaml
# few-shots/fs_beer_factory_001.yaml
asset_type: few_shot
id: fs_beer_factory_001

# ── Facts ──
db: beer_factory

# ── Inference (curator selects/distills; server-consumed as a prompt exemplar) ──
question: "Which root beer brand has the highest average review rating?"
sql: |
  SELECT b.BrandName, AVG(r.StarRating) AS avg_rating
  FROM rootbeerreview AS r
  JOIN rootbeerbrand AS b ON r.BrandID = b.BrandID
  WHERE r.StarRating IS NOT NULL
  GROUP BY b.BrandName
  ORDER BY avg_rating DESC
bound_terms: [brand, rating]
complexity: medium                     # simple | medium | complex → controls injection count
confidence: 0.9

# ── Audit ──
audit:
  provenance: { source: curator, status: draft }
  # NB: the BIRD eval harness's CI additionally checks source_refs ⊆ train split (leakage guard).
  # That is a harness rule, not a schema rule (P2).
```

## 资产：`term`（内联同义词与关系）

```yaml
# terms/term_revenue.yaml
asset_type: term
id: term_revenue

# ── Inference (curator maps business language → assets) ──
name: "revenue"
synonyms: ["sales", "total sales", "gross revenue"]
binding: { asset_type: metric, asset_id: metric_revenue }
related_terms:                         # projects into the graph
  - { id: term_brand, relation: uses }   # relation: synonym_of | broader_than | uses
confidence: 0.75

# ── Audit ──
audit:
  evidence: "'revenue'/'sales' used interchangeably across seed questions; all map to SUM(PurchasePrice)"
  provenance: { source: curator, status: draft }
```

## 资产：`metric`（内联规则；没有逐资产的 gold，D4）

```yaml
# metrics/metric_revenue.yaml
asset_type: metric
id: metric_revenue

# ── Inference (curator derives from evidence + seed queries) ──
name: "total revenue"
base_table: tbl_beer_factory_transaction
expression: "SUM(PurchasePrice)"       # in meaning; SQL-gen maps to physical
dimensions: [customer, brand, transaction_date]
rules:
  - { kind: filter, note: "count only completed sales (all rows in transaction)" }
confidence: 0.75

# ── Audit ──
audit:
  evidence: "PurchasePrice is the per-sale amount; recurring SUM over sales in seed queries"
  provenance: { source: curator, status: draft, version: "0.1.0" }
```

## 资产：`rule` / `context`（独立）

```yaml
# rules/rule_boolean_flags.yaml
asset_type: rule
id: rule_boolean_flags

# ── Inference ──
kind: business_rule                    # business_rule | context | constraint
scope: [tbl_beer_factory_rootbeerbrand]   # assets it constrains; empty = global
statement: >
  The ingredient and availability flags on rootbeerbrand (CaneSugar, CornSyrup,
  Honey, ArtificialSweetener, Caffeinated, Alcoholic, AvailableInCans,
  AvailableInBottles, AvailableInKegs) are stored as the TEXT strings 'TRUE' and
  'FALSE', not as integers or booleans. Filter with = 'TRUE', never = 1.
confidence: 0.85

# ── Audit ──
audit:
  evidence: "sampled values are the literal strings 'TRUE'/'FALSE' in TEXT columns"
  provenance: { source: curator, status: draft }
```

## 资产：`negative_example`

把某一类问题标记为**无法从这份数据中回答** → 触发 refuse-gate 预设的升级处理（D5）。由 curator 基于 self-eval 覆盖缺口提出（dev 环境），或由所有者整理（prod 环境）；经 adversary 检查；在 serve 时按语义相似度匹配。

```yaml
# negatives/neg_beer_factory_001.yaml
asset_type: negative_example
id: neg_beer_factory_001

# ── Inference (curator proposes; human certifies) ──
pattern: "questions about employees, staffing, or headcount"
example_questions:
  - "How many employees work at the factory?"
  - "What is the average salary of factory staff?"
reason: "no table in this database covers employees, staffing, or payroll"
escalation: "not answerable from this data - contact <owner>"
confidence: 0.8

# ── Audit ──
audit:
  evidence: "self-eval questions about staffing found no covering table"
  provenance: { source: curator, status: draft }
```

## 资产：`skill`（Markdown，非 YAML）

按领域组织的行文式程序性知识。Frontmatter 携带与 YAML 资产相同的溯源信息（可审计，但没有 gold）。正文会被检索并注入到 server 的 prompt 中。

```markdown
---
# skills/routing.md
skill_id: skill_beer_factory_routing
db: beer_factory
kind: routing              # routing | gotchas | pattern | domain_overview
provenance: { source: curator, status: draft }
---

# Beer factory: routing & gotchas

## Scope
Sales, customers, root beer brands, and reviews for a root beer factory.
`transaction` is the sales fact table; `rootbeer` is the unit dimension, which
rolls up to `rootbeerbrand`.

## Routing triggers
- Revenue / sales questions use `metric_revenue` (`SUM(PurchasePrice)` on
  `tbl_beer_factory_transaction`). To break revenue down by brand, join
  transaction to rootbeer (`join_transaction_rootbeer`) then rootbeer to
  rootbeerbrand (`join_rootbeer_rootbeerbrand`).
- Rating / review-quality questions use `metric_avg_rating`
  (`AVG(StarRating)` on `tbl_beer_factory_rootbeerreview`); join to
  `tbl_beer_factory_rootbeerbrand` via `join_review_rootbeerbrand`.

## Gotchas
- Ingredient and availability flags on `rootbeerbrand` are the strings
  `'TRUE'`/`'FALSE'`, not integers (see `rule_boolean_flags`). Filter with
  `= 'TRUE'`.
- `customers.ZipCode` is an INTEGER, so leading zeros are lost; do not use it as
  a postal key (see its reliability caveat).
- `transaction.CreditCardNumber` is PII and is excluded; never select it.
```

Skill 通过 ID 引用类型化资产，**不会**重述这些资产的数据。skill 完全是 curator 带来的增量价值：没有 gold skill 可以比对（diff）。

---

## CI 引用完整性检查（“足够完成”信号）

CI 会校验这个 corpus，而校验通过同时也充当 curator 的、可由机器检查的停止信号（D9）：

- **ID 正则**：每个 `id` 都匹配其约定格式。
- **物理存在性**：每个 `physical_name` / `on` 中出现的列都存在于实际的 catalog 中。
- **引用解析**：`references`、`binding.asset_id`、`related_terms[].id`、`metric.base_table`、`rule.scope[]` 都能解析到已存在的资产。
- **枚举合法性**：`role`、`reliability.status`、`logical_type`、`complexity`、`cardinality`、`relation`、`kind` 均 ∈ 各自允许的取值集合。
- *（属于 eval harness 层，不属于 schema）*：few-shot 的 `source_refs ⊆ train split`（leakage guard）。

## 图投影（全部从 YAML 派生；Neo4j 从不被直接撰写）

| 边 | 从 → 到 | 来源 |
|---|---|---|
| `HAS_COLUMN` | Table → Column | 内联 `columns[]` |
| `JOINS_TO` | Table → Table（属性：on、cardinality、cost） | `join` |
| `REFERENCES` | Column → Column | `column.references` |
| `BINDS_TO` | Term → Metric/Table/Column | `term.binding` |
| `SYNONYM_OF` / `BROADER_THAN` / `USES` | Term → Term | `term.related_terms[]` |
| `DERIVED_FROM` | Metric → Table/Column | `metric.base_table` / expression |

BIRD 使用内存中的图（networkx）做 Steiner 规划；Neo4j 是可选的企业级投影。

## Gold 对比 curator（同一个 schema，两个填充者）

- **Curator（Arm 2）** 通过*推断*来填充 Inference 层：描述（description）、角色（role）、`references`、`reliability`、`confidence`，并用 `audit.*_evidence` 记录原因。
- **Gold（Arm 3）** 从 manifest 中以确定性的方式填充*相同的* Inference 字段：真实名称（通过 rename map）、FK 图（来自原始 schema），以及 manifest 中记录的任何 `reliability.status=suspect` 标记，并附带 `provenance.source: gold`、`confidence: 1.0`。
- Facts 在所有 arm 中都是相同的（均从 catalog 中读取）。gold-diff 只比较 Inference 层。
- **Skill（Markdown）只由 curator 产出**：没有任何 manifest 能推导出它们，所以 Arm 3 没有 skill。这正是 Arm 2 能在对 skill 敏感的问题上*超越* gold 上限的机制所在。
