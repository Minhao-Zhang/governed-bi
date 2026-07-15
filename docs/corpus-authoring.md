# Corpus authoring

_[English](corpus-authoring.md) · [简体中文](corpus-authoring.zh.md)_

How to write and validate corpus assets by hand. The [asset schemas](asset-schemas.md)
page is the field-by-field reference; this page is the task-oriented walkthrough.

In the finished system the curator agent generates these assets and an
adversary checks them, then a human audits the result (D9, D10). A deterministic
proposer/adversary scaffold runs today (Facts profiling, heuristic + LLM
proposers, the structural adversary `review`, and the `curate` promote loop); the
autonomous self-eval repair loop and per-asset LLM `refute` are still seams. You
still author assets by hand: to seed a corpus, to build test fixtures, or to
correct what the curator produced. Either way the rule is
the same. The Git-tracked YAML and Markdown files **are** the source of truth;
editing them is editing the semantic layer. The graph, vector, and BM25 stores
are rebuildable projections and are never edited directly.

Work against the bundled example as you read: [`corpus/beer_factory/`](../corpus/beer_factory).

## 1. Create the directory layout

Pick a schema namespace (the schema the assets describe) and create the
per-type folders under it:

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

One YAML file per asset (columns are the exception: they live inline in their
table). One `<schema>` folder per schema; a single database may hold many
schemas (D15).

> **D15**: The folder is a _schema_ namespace, not a database — one database can
> hold many schemas, joined across via qualified `schema.table` SQL. On-disk YAML
> and load/write APIs use the field/param name `schema` (hard cut from `db`).
> Asset IDs such as `tbl_<schema>_<table>` are unchanged.

## 2. The three tiers (what you fill in)

Every asset splits into three tiers plus a human-only override. Knowing which
tier a field belongs to tells you whether you should be writing it:

- **Facts**: read from the catalog and data (`physical_name`, `physical_type`, `nullable`, `is_unique`, `sample_values`, `row_count`). In the real loop these are generated programmatically and never edited. When authoring by hand, fill them from the actual database.
- **Inference**: the semantic layer, the part that carries meaning (`description`, `role`, `references`, `cardinality`, `expression`, `confidence`, and so on). This is the work.
- **Audit**: why the inference was made (`audit.provenance` plus free-text `*_evidence`). Never shown to the server; keep it as verbose as you like.
- **Governance**: `governance.excluded`, set only by a human owner. See step 7.

## 3. Add a table (with columns)

`physical_name` is the identifier as it exists in the live DB (cryptic or
obfuscated). `description` is what it means. The corpus maps one to the other.

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

## 4. Add a second table and a join

A `join` records an inferred foreign-key edge (the DB may not declare it). The
`on` clause uses **physical** names; cardinality and confidence are inferred.

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

## 5. Reference wiring (the part the validator checks)

References must resolve to an existing asset. The IDs follow fixed conventions
(the validator regex-checks them):

| Field | Points at | Example target |
|---|---|---|
| `column.references` | a column id | `col_demo_customers_c_0` |
| `join.left_table` / `right_table` | a table id | `tbl_demo_customers` |
| `metric.base_table` | a table id | `tbl_demo_orders` |
| `term.binding.asset_id` | a metric / table / column id | `metric_demo_order_total` |
| `term.related_terms[].id` | a term id | `term_customer` |
| `rule.scope[]` | any asset id | `tbl_demo_orders` |

Columns have no `id` field of their own; the loader derives one as
`col_<schema>_<table>_<physical>`. So the primary key of `tbl_demo_customers` with
physical name `c_0` is `col_demo_customers_c_0`, which is what `references` above
points to.

A metric and a term, wired to the assets above:

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

## 6. Reliability caveats

If a column looks untrustworthy, mark it `suspect` and say why in prose. This is
a caveat, not a typed flag, so the same mechanism works everywhere.

```yaml
    reliability:
      status: suspect            # ok | suspect
      note: "UNRELIABLE - DO NOT USE. values look tampered."
```

At serve time a `suspect` column is hard-blocked in dev and soft-warned in an
enterprise deployment. See [server.md](server.md).

## 7. Governance exclusion (human only)

Distinct from a reliability caveat: a human owner can remove an asset from
everything the server sees, permanently, in all environments.

```yaml
    governance:
      excluded: true
      reason: "PII, never surface"
      by: your-handle
      at: "2026-07-08"
```

`Corpus.for_server()` drops these, so an excluded column never reaches
retrieval, the presented schema, or SQL generation.

## 8. Skills (Markdown, not YAML)

Prose routing and gotchas. The frontmatter carries provenance; the body
references assets by ID and never restates their data.

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

## 9. Validate

```bash
uv run python -m governed_bi.corpus.cli corpus/demo
```

Green means the IDs are well-formed and every reference resolves. It prints a
summary line with your asset and skill counts, for example:

```
CI green: 6 assets, 1 skills, 0 findings.
```

If something is off, each finding names the asset and the problem. The common ones:

- `bad-id` : an `id` does not match its convention (for example a table id not starting with `tbl_`).
- `duplicate-id` : two assets share an id.
- `dangling-ref` : a reference does not resolve, for example `metric.base_table -> 'tbl_demo_order' does not resolve` when the table is actually `tbl_demo_orders`. Fix the typo (or add the missing asset) and re-run.

That green run is the machine-checkable "done-enough" signal (D9). Two checks are
deliberately **not** run here: that each `physical_name` exists in the live
catalog (needs a DB connection) and that few-shot `source_refs` stay within the
train split (needs the eval split). Both belong to the eval harness.

## Next

- [Asset schemas](asset-schemas.md) for the full field spec and every asset type.
- [Usage](usage.md) for the programmatic loader/validator API.
- [Design decisions](design-decisions.md) D9/D10 for why the corpus is authored this way.
