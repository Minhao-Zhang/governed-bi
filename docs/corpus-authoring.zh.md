# Corpus 编写

_[English](corpus-authoring.md) · [简体中文](corpus-authoring.zh.md)_

如何手动编写并校验 corpus 资产。[资产模式](asset-schemas.zh.md)页是逐字段的参考文档；本页则是面向任务的操作指南。

在建成后的系统中，curator 代理会生成这些资产，adversary 对其进行检查，再由人工审核结果(D9、D10)。今天已有一套确定性的 proposer/adversary 脚手架在运行（Facts 画像、启发式 + LLM proposer、结构化 adversary `review`、`curate` 晋升循环）；自主自评修复循环与逐资产 LLM `refute` 仍是接缝。你仍会手动编写资产：用于给 corpus 播种、构建测试夹具(fixtures)，或者修正 curator 产出的结果。无论出于哪种目的，规则都是一样的。Git 跟踪的 YAML 和 Markdown 文件**才是**事实来源(source of truth)；编辑它们就是在编辑语义层。图(graph)、向量(vector)和 BM25 存储都是可重建的投影，绝不能直接编辑。

阅读本文时，可以对照内置的示例进行操作：[`corpus/beer_factory/`](../corpus/beer_factory)。

## 1. 创建目录结构

选择一个 schema 命名空间(即这些资产所描述的 schema)，并在其下创建按类型划分的文件夹：

```
corpus/
  <schema>/
    tables/      tbl_<schema>_<name>.yaml      # columns are inline
    joins/       join_<left>_<right>.yaml
    metrics/     metric_<name>.yaml
    terms/       term_<name>.yaml
    rules/       rule_<name>.yaml
    few-shots/   fs_<schema>_<n>.yaml
    negatives/   neg_<schema>_<n>.yaml
    skills/      *.md
```

每个资产对应一个 YAML 文件(列是例外：它们内联在所属的表中)。每个 schema 对应一个 `<schema>` 文件夹；一个数据库可以容纳多个 schema(D15)。

> **D15**：这里的文件夹是一个 schema 命名空间，而非数据库——一个数据库可以容纳多个 schema，跨 schema 之间通过带限定名的 `schema.table` SQL 连接。磁盘 YAML 与加载/写入 API 使用字段/参数名 `schema`（由 `db` 硬切断更名）。资产 ID（如 `tbl_<schema>_<table>`）保持不变。

## 2. 三个层级(你需要填写的内容)

每个资产分为三个层级，外加一个仅限人工的覆盖(override)层。了解某个字段属于哪个层级，就能知道自己是否应该去填写它：

- **Facts**：从目录(catalog)和数据中读取(`physical_name`、`physical_type`、`nullable`、`is_unique`、`sample_values`、`row_count`)。在真实的流程中，这些字段由程序自动生成，永远不会手动编辑。手动编写时，请根据实际数据库来填写它们。
- **Inference**：语义层，也就是承载含义的部分(`description`、`role`、`references`、`cardinality`、`expression`、`confidence` 等)。这才是真正要做的工作。
- **Audit**：说明为什么会做出该推断(`audit.provenance`，以及自由文本形式的 `*_evidence`)。永远不会展示给 Analyst；可以尽可能详细地填写。
- **Governance**：`governance.excluded`，只能由人工负责人设置。参见第 7 步。

## 3. 添加一张表(含列)

`physical_name` 是它在真实数据库中实际存在的标识符(可能隐晦或经过混淆)。`description` 则是它的含义。corpus 把两者对应起来。

```yaml
# corpus/demo/tables/tbl_demo_orders.yaml
asset_type: table
id: tbl_demo_orders

# Facts
schema: demo
physical_name: t_1
row_count: 50000

# Inference
description: "one row per customer order"
grain: "one row = one order"
confidence: 0.8

columns:
  - # Facts
    physical_name: c_0
    physical_type: "integer"
    logical_type: integer        # string | integer | decimal | date | datetime | boolean
    nullable: false
    is_unique: true
    sample_values: [1001, 1002]
    # Inference
    description: "order id"
    role: primary_key            # primary_key | foreign_key | key | measure | dimension
    confidence: 0.95
  - # Facts
    physical_name: c_3
    physical_type: "integer"
    logical_type: integer
    nullable: false
    is_unique: false
    sample_values: [42, 42, 77]
    # Inference
    description: "customer id (joins to the customers table)"
    role: foreign_key
    references: col_demo_customers_c_0   # a column id, see step 5
    confidence: 0.85
```

## 4. 添加第二张表和一个连接(join)

`join` 记录的是一条被推断出的外键关系(DB 本身可能并未声明这条关系)。`on` 子句使用的是**物理**名称；基数(cardinality)和置信度(confidence)都是推断得出的。

```yaml
# corpus/demo/joins/join_orders_customers.yaml
asset_type: join
id: join_orders_customers

# Facts (the referenced physical columns exist)
left_table: tbl_demo_orders
right_table: tbl_demo_customers
on: "t_1.c_3 = t_2.c_0"

# Inference (the existence of the edge is inferred)
cardinality: many_to_one         # one_to_one | one_to_many | many_to_one | many_to_many
cost: 1.0
confidence: 0.8
```

## 5. 引用关联(校验器检查的部分)

引用必须能解析到一个已存在的资产。ID 遵循固定的命名约定(校验器会用正则表达式对其进行检查)：

| 字段 | 指向 | 示例目标 |
|---|---|---|
| `column.references` | 某个列的 id | `col_demo_customers_c_0` |
| `join.left_table` / `right_table` | 某个表的 id | `tbl_demo_customers` |
| `metric.base_table` | 某个表的 id | `tbl_demo_orders` |
| `term.binding.asset_id` | 某个指标、表或列的 id | `metric_demo_order_total` |
| `term.related_terms[].id` | 某个术语的 id | `term_customer` |
| `rule.scope[]` | 任意资产的 id | `tbl_demo_orders` |

列本身没有自己的 `id` 字段；loader(加载器)会按照 `col_<schema>_<table>_<physical>` 的格式自动派生一个。因此，`tbl_demo_customers` 中物理名为 `c_0` 的主键，其 id 就是 `col_demo_customers_c_0`，也就是上面 `references` 所指向的目标。

一个指标和一个术语，与上面的资产相互关联：

```yaml
# corpus/demo/metrics/metric_demo_order_total.yaml
asset_type: metric
id: metric_demo_order_total
name: "total order value"
base_table: tbl_demo_orders          # must resolve to a table
expression: "SUM(amount)"            # in meaning; SQL-gen maps to physical
dimensions: [customer]
confidence: 0.6
```

```yaml
# corpus/demo/terms/term_order_value.yaml
asset_type: term
id: term_order_value
name: "order value"
synonyms: ["order total", "revenue per order"]
binding: { asset_type: metric, asset_id: metric_demo_order_total }
related_terms:
  - { id: term_customer, relation: uses }   # synonym_of | broader_than | uses
confidence: 0.7
```

## 6. 可靠性警示(reliability caveat)

如果某一列看起来不可信，就把它标记为 `suspect`，并用文字说明原因。这是一种警示，而不是一个带类型的标志(flag)，因此同一套机制在任何地方都适用。

```yaml
    reliability:
      status: suspect            # ok | suspect
      note: "UNRELIABLE - DO NOT USE. values look tampered."
```

在 serve 阶段，被标记为 `suspect` 的列在 dev 环境中会被硬性阻止(hard-blocked)，在企业级部署中则只给出软性警告(soft-warned)。参见[Analyst](analyst.zh.md)。

## 7. Governance 排除(仅限人工)

这与可靠性警示不同：人工负责人可以将某个资产从 Analyst 所能看到的一切内容中永久移除，且在所有环境中都生效。

```yaml
    governance:
      excluded: true
      reason: "PII, never surface"
      by: your-handle
      at: "2026-07-08"
```

`Corpus.for_analyst()` 会丢弃这些资产，因此被排除的列永远不会进入检索(retrieval)、呈现的模式(schema)，或 SQL 生成环节。

## 8. Skills(Markdown，而不是 YAML)

用于路由说明和注意事项(gotchas)的自由文本。frontmatter 携带溯源(provenance)信息；正文通过 ID 引用资产，并不会重复列出它们的数据。

```markdown
---
skill_id: skill_demo_routing
schema: demo
kind: routing              # routing | gotchas | pattern | domain_overview
provenance: { source: curator, status: draft, source_refs: [q1] }
---

# Demo: routing & gotchas

- For order value, use `metric_demo_order_total`; join
  `tbl_demo_orders` to `tbl_demo_customers` via `join_orders_customers`.
```

## 9. 校验

```bash
uv run python -m governed_bi.corpus.cli corpus/demo
```

校验通过(green)，意味着所有 id 格式都正确，且每一个引用都能成功解析。命令会打印一行摘要，列出你的资产和 skill 数量，例如：

```
CI green: 6 assets, 1 skills, 0 findings.
```

如果哪里有问题，每条 finding 都会指出具体的资产和存在的问题。常见的几种如下：

- `bad-id`：某个 `id` 不符合其命名约定(比如某个表 id 没有以 `tbl_` 开头)。
- `duplicate-id`：两个资产共用同一个 id。
- `dangling-ref`：某个引用无法解析，例如当表实际是 `tbl_demo_orders` 时出现 `metric.base_table -> 'tbl_demo_order' does not resolve`。修正拼写错误(或补上缺失的资产)后重新运行即可。

这次校验通过(green)的运行，就是机器可验证的「足够完成」信号(D9)。这里特意**不**运行另外两项检查：确认每个 `physical_name` 都存在于真实目录(catalog)中(这需要数据库连接)，以及确认 few-shot 的 `source_refs` 都落在训练集(train split)范围内(这需要用到评测集划分)。这两项检查都属于 eval harness 的职责。

## 下一步

- [资产模式](asset-schemas.zh.md)：完整的字段规范以及全部资产类型。
- [使用指南](usage.zh.md)：可编程调用的 loader / validator API。
- [设计决策](design-decisions.zh.md) D9/D10：为什么 corpus 要以这种方式编写。
