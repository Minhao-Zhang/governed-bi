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

- **P1：三个字段层。** 每个资产的字段分为三层：**Facts**（从 catalog/数据中读取，绝不推断）、**Inference**（由 curator 撰写；这正是语义层）、以及 **Audit**（记录做出该推断的理由，仅供参考）。不同层遵循不同的规则（见下文）。
- **P2：字段通用，只有取值才是项目特定的。** 没有任何字段名是 BIRD 专属的。BIRD、企业级部署，以及未来的任何项目，共享*完全相同的 schema*；只有取值（用哪个 DB、哪种 SQL 方言、哪些 `source_refs`）会不同。BIRD 评测专属的规则（例如 leakage guard）存在于 eval harness 中，绝不存在于 schema 里。

## 单一表示：一切都是类型化 YAML 资产

这份规范早些版本把 corpus 拆成两部分：YAML 类型化资产承载结构化、按实体拆分的内容；Markdown "skill" 承载行文式、跨实体、程序性的内容（路由触发条件、注意事项、查询模式、领域概览）。这么拆分的理由是：行文式内容没法做 CI 检查，也没法投影进图，而且没法把"如果问题是关于收入的，就从交易事实表出发，不要用品牌零售价"这样的跨实体流程，干净地表达成某个逐列字段。

**D17 把这个拆分合并掉了。**`skill` 资产类型已被删除，`RuleAsset` 被泛化为 `NoteAsset`：用一个受治理的类型化资产，承载原来那部分跨实体的程序性内容，但它现在是经 CI 检查、被索引、可纳入治理的 corpus 资产，不再是一个没有任何东西校验、也没有任何东西以程序方式创建它的、不受治理的 Markdown 旁路。笔记仍然可以跨实体（它的 `scope` 可以横跨多个表、整个 schema，或整条 DB），并且仍然靠 id 引用其他资产，而不是重复它们的数据；渐进式展开也保留了下来，只是变成了一个字段拆成两半（`summary` 始终可见，`body` 按需获取），而不是两种文件格式。参见下文的**资产：`note`**与 **D17**。

> **笔记是价值最高的产出，且只有 curator 才能产出**
>
> Anthropic 在这类程序性知识上的结果是：同一个模型，没有它时**低于 21%**，用上它之后
> 能到**95% 以上**。这是单一的最大杠杆。笔记**没有 oracle 对应物**（评测阶梯里任何
> 一档已构建的分支都无法推导出它），所以即便是 `ceiling` 这一档也没有笔记。这正是
> 为什么 `curated` 分支能在对笔记敏感的问题上*超越*可恢复上限（recoverable
> ceiling，D4）的原因。

## 消费契约（谁读取哪一层）

| 消费方 | Facts | Inference | Audit |
|---|---|---|---|
| **Analyst**（SQL 生成） | ✅ | ✅ | ❌ **绝不注入** |
| **Viz / 审计界面** | ✅ | ✅ | ✅ |
| **检索索引**（R/V/G/D） | ✅ | ✅ | ❌ |

loader 强制执行这一契约：Analyst 的上下文仅由 Facts 加 Inference 构建。因此 Audit 层的文本（证据、溯源）可以按人类需要写得再详尽，也不会消耗 Analyst 的 token，也不会引入噪声。如果 Audit 层把文件撑得过大，退路是拆分成 sidecar 文件（在 BIRD 的规模下尚不需要）。

## 治理覆盖（由人类撰写，位于三层字段之外）

有一个字段既不是由 catalog 撰写、也不是由 curator 撰写，而是由**人类所有者**撰写（D6）：`governance.excluded`。在某一列或某个表上，当人类审核后将其设为 `true`，该资产就会从 Analyst 所能看到的一切（检索、呈现给它的 schema、图）中**被彻底移除**，并且是在**所有环境中、没有开关、永久生效**。它仍会显示在 viz / 审计界面中（带有标记与原因），从而使这一排除动作可被审计；护栏 L3 也会将其硬性拦截，作为纵深防御的一层。

```yaml
# on tbl_beer_factory_transaction, column CreditCardNumber
governance:
  excluded: true
  reason: "PII (payment card number); never surface to the Analyst"
  by: minhaoz
  at: "2026-07-08"
```

这与 curator 的 `reliability.status: suspect` 不同：

| | `reliability: suspect` | `governance.excluded` |
|---|---|---|
| 撰写者 | curator（AI），经 adversary 检查 | 人类所有者（经认证） |
| 含义 | "看起来不可靠" | "已决定：永不使用" |
| 服务时的效果 | 软性警告或硬性拦截（可按环境切换） | **彻底移除**，所有环境，无开关 |

升级路径：curator 标记为 `suspect` → 人类审核（D6） → 维持原状，或升级为 `excluded`。这一机制**排除在自主评测阶梯之外**（以使 `curated` 分支保持纯 curator）；它是面向企业级部署的人机协同治理能力。

## 澄清（一个 Audit 层字段块，D12）

当 curator 无法仅凭 Facts 和批次信息解决某个问题时，它会**记录该问题而非臆测**，把它写成资产**Audit 层**上的一个 `clarification` 块。因为它属于 Audit 层，所以**绝不会注入 Analyst 的上下文**（见上文的消费契约），未决问题也就不会泄漏进 SQL 生成或检索环节。问题悬而未决期间，资产仍会从其 Inference 层给出尽力而为的答案（低 `confidence` 加一条 `suspect` 警示）；一个 **Responder**（生产环境中是人类 **SME**，评测环境中是 **Simulated SME**）对其作答后，`accept_answer` 会把状态翻转为 `answered`，并重新盖上 provenance 戳。参见 [D12](design-decisions.zh.md#d12澄清协议)。

```yaml
# 在任意资产的 audit 块下
audit:
  provenance: { source: curator, status: draft }
  clarification:
    question: "`kunde_id` 是客户 id，还是内部账户 id？"
    status: open            # open | answered
    asked_by: curator
    answer: null            # Responder 给出的自由文本回复
    answered_by: null       # SME / responder 的 id
    at: null                # 作答时的 ISO 时间戳
```

> 注意：这个逐资产的 `Clarification`（内嵌在 schema 中，`corpus/schemas.py`）与 curator 运行时的 `clarifications.jsonl` **账本**（`curator/clarifications.py`，SME 往返实际迭代的对象）不同。账本驱动批次往返；此字段块是挂在相关资产上、可持久、带 ID 追踪的记录。

## 目录结构

```
corpus/
  <schema>/
    tables/      tbl_<schema>_<name>.yaml      # columns inline
    joins/       join_<left>_<right>.yaml
    few-shots/   fs_<schema>_<n>.yaml
    terms/       term_<name>.yaml
    metrics/     metric_<name>.yaml
    notes/       note_<name>.yaml
    negatives/   neg_<schema>_<n>.yaml
  _generated/    # search index, embeddings, compiled graph (derived, gitignored, rebuildable)
```

> **D15**：corpus 命名空间 `<schema>`（上方目录、下方 ID 格式）建模的是一个
> **schema**，而非数据库（一个数据库可以容纳多个 schema，跨 schema 的 join 以带
> 限定名的 `schema.table` SQL 执行）。字段/目录名为 `schema`（`db` → `schema`
> 改名**已落地**，D15 增量 7）；ID 为 `tbl_<schema>_<name>`。

## ID 约定（CI 用正则表达式检查）

| 资产 | ID 格式 | 示例 |
|---|---|---|
| table | `tbl_<schema>_<name>` | `tbl_beer_factory_customers` |
| column *（内联；ID 由 loader 推导）* | `col_<schema>_<table>_<physical>` | `col_beer_factory_customers_CustomerID` |
| join | `join_<left>_<right>` | `join_transaction_customers` |
| few_shot | `fs_<schema>_<n>` | `fs_beer_factory_001` |
| term | `term_<name>` | `term_revenue` |
| metric | `metric_<name>` | `metric_revenue` |
| note | `note_<name>` | `note_boolean_flags` |
| negative_example | `neg_<schema>_<n>` | `neg_beer_factory_001` |

**物理 ↔ 含义桥梁**贯穿每一个表/列：`physical_name` 是该字段在实际数据库中存在的标识符（在 BIRD 中经过混淆处理，在企业数据中则本身就晦涩难懂）。SQL 输出的正是这个标识符；而 Inference 层承载的是它的*含义*。curator 的全部工作，就是为晦涩难懂的物理名称填充含义，这一点在 BIRD 与企业级部署中完全相同。

---

## 资产：`table`（带内联列）

```yaml
# tables/tbl_beer_factory_customers.yaml
asset_type: table
id: tbl_beer_factory_customers

# ── Facts (catalog/data) ──
schema: beer_factory                   # scoping namespace = Postgres/Redshift schema / corpus subtree
physical_name: customers               # identifier in the live DB
row_count: 554

# ── Inference (curator writes; Analyst-consumed) ──
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
    reliability: { status: ok, note: null }   # status: ok | suspect ; note: prose (Analyst-visible)
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
    # Governance (human-authored override; not curator-authored)
    governance: { excluded: false }    # human sets true → asset removed everywhere the Analyst sees
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
schema: beer_factory

# ── Inference (curator selects/distills; Analyst-consumed as a prompt exemplar) ──
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

## 资产：`note`（受治理的笔记；D17）

取代了原来独立的 `rule` 资产，以及未经类型约束的 `skills/*.md` 页面。"规则"其实就是
一条 `activation=always` 且 `normative_force=must_honour` 的笔记。路由 / 注意事项
（gotchas）/ 模式（pattern）现在也都是笔记（以前是 Markdown skill）。

| `kind` | 默认 `activation` | 默认 `normative_force` |
|---|---|---|
| `business_rule`、`constraint` | `always` | `must_honour` |
| `context`、`domain_overview` | `always` | `advisory` |
| `routing`、`gotchas`、`pattern` | `on_match` | `advisory` |

这两个默认值都可以覆盖（比如一条由关键词触发的 `business_rule`，可以设成
`activation=on_match` 加 `normative_force=must_honour`）。第一阶段只注入
`activation=always` 的笔记，并且只注入它们的 `summary`（绝不注入 `body`）。
`on_match` 的 PIN 机制、body 的按需获取，以及 `read_notes` / `grep_notes`，都
留给第二阶段。

`Trigger` 的结构是 `{ kind: keyword | regex, value: <string> }`。一次触发命中，
会把对应的笔记直接**钉入（pin）**shortlist，而不是给 RRF 贡献一个词法分数（把偏弱的
词法信号混入 RRF，实测会明显拉低召回率，见 ADR 0003）。`regex` 目前已经建模，但
现在只有 `keyword` 真正会触发；对问题文本做 regex 匹配这件事被特意推迟了（第二
阶段的 `grep_notes`，能在不依赖这一机制的前提下，覆盖对*文本*做 regex 匹配的场景）。

`scope` 里的条目可以是资产 id，也可以是命名空间哨兵值 `schema:<name>` /
`db:<name>`；`[]` 表示全局，资产 id 中永远不会出现 `:`。`publication_status` 在
serve 时可见（会在 `for_analyst` 之后保留下来）；当资产存在 Audit 层时，CI 会检查
它是否偏离了 `audit.provenance.status`。

```yaml
# notes/note_boolean_flags.yaml
asset_type: note
id: note_boolean_flags

# ── Inference ──
kind: business_rule
scope: [tbl_beer_factory_rootbeerbrand]   # [] = global; also schema:… / db:…
summary: >
  The ingredient and availability flags on rootbeerbrand (CaneSugar, CornSyrup,
  Honey, ArtificialSweetener, Caffeinated, Alcoholic, AvailableInCans,
  AvailableInBottles, AvailableInKegs) are stored as the TEXT strings 'TRUE' and
  'FALSE', not as integers or booleans; filter with = 'TRUE', never = 1.
# body: |                                 # optional long form; on-demand only (Phase 2)
# triggers: [{ kind: keyword, value: "TRUE" }]   # authored now; PIN wired in Phase 2
activation: always                        # default from kind; overridable
# normative_force defaults to must_honour for business_rule
confidence: 0.85
publication_status: draft                 # proposed | draft | certified
# related_notes: [note_other]
# governance: { excluded: false }

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

---

## CI 引用完整性检查（"足够完成"信号）

CI 会校验这个 corpus，而校验通过同时也充当 curator 的、可由机器检查的停止信号（D9）：

- **ID 正则**：每个 `id` 都匹配其约定格式。
- **物理存在性**：每个 `physical_name` / `on` 中出现的列都存在于实际的 catalog 中。
- **引用解析**：`references`、`binding.asset_id`、`related_terms[].id`、`metric.base_table`、`note.scope[]` 都能解析到已存在的资产（包括 `schema:` / `db:` 这类哨兵值）。
- **笔记发布状态漂移**：当一条笔记存在 Audit 层时，`publication_status` 必须与 `audit.provenance.status` 一致。
- **常驻笔记预算**：全局 `activation=always` 的笔记最多 8 条，且这些笔记的 `summary` 文本总长度不超过 2000 字符。
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
| `SCOPES` | Note → 被限定范围的资产 id | `note.scope[]` |

BIRD 使用内存中的图（networkx）做 Steiner 规划；Neo4j 是可选的企业级投影。

## curator 对比已退役的 gold 填充器

- **Curator（`curated` 分支）** 通过*推断*来填充 Inference 层：描述（description）、角色（role）、`references`、`reliability`、`confidence`，并用 `audit.*_evidence` 记录原因。
- 现在已经不存在基于 manifest 的 oracle 填充器来填充 Inference 层了。曾经有一个已退役的去混淆 "gold" 分支，会从 manifest 里以确定性方式填充*相同的* Inference 字段（通过 rename map 还原出的真实名称、来自原始 schema 的 FK 图，以及 manifest 记录的任何 `reliability.status=suspect` 标记），并带上 `provenance.source: gold`、`confidence: 1.0`；它已被**移除**：它从来都不是真正的上限（ceiling），因为 curator 撰写的笔记可以在对笔记敏感的问题上超越它。（`ProvenanceSource.gold` 现在只作为遗留的枚举值保留在 schema 中。）
- Facts 在评测阶梯的每一档中都完全相同（均从 catalog 中读取）；只有 Inference（包括笔记）会变化。
- **笔记由 curator 撰写**：评测阶梯里没有任何其他环节能推导出它们，包括那个已设计但尚未构建的 `ceiling` 档所用的、具备测试感知能力的 Simulated SME。这正是 `curated` 分支能在对笔记敏感的问题上*超越*可恢复上限（recoverable ceiling）的机制所在。
