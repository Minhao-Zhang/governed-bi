# Agentic BI Asset Schemas

_[English](asset-schemas.md) · [简体中文](asset-schemas.zh.md)_

The per-asset YAML field spec for the [Agentic BI System](system-overview.md)
corpus. Concretizes **D9** in [Design decisions](design-decisions.md) (Git+YAML
typed assets, curator-authored / human-audited); storage rationale in
[Architecture](architecture.md) §5; term definitions in [Glossary](glossary.md).
Adapted from *《从数据到智能》* Ch.3, with the authoring model inverted.

> This is the canonical field spec. The Pydantic implementation lives in
> [`src/governed_bi/corpus/schemas.py`](../src/governed_bi/corpus/schemas.py);
> ID conventions in [`ids.py`](../src/governed_bi/corpus/ids.py); the CI
> reference-integrity checker in
> [`validate.py`](../src/governed_bi/corpus/validate.py).

## Two principles (the backbone)

- **P1: Three field tiers.** Every asset's fields split into **Facts** (read from the catalog/data, never inferred), **Inference** (curator writes, or gold fills; this is the semantic layer), and **Audit** (why the inference was made, for reference only). Different tiers follow different rules (below).
- **P2: Universal fields, project-specific values only.** No field name is ever BIRD-specific. BIRD, enterprise deployments, and any future project share the *exact same schema*; only the values (which DB, which SQL dialect, which `source_refs`) differ. BIRD-eval-specific rules (e.g. leakage guards) live in the eval harness, never in the schema.

## Two representations: YAML for structure, Markdown for procedure

The corpus is **not YAML-only.** Two representations, split by access pattern:

- **YAML typed assets** carry *structured, atomic, per-entity* content: Facts + definitions (`table`/`column`/`join`/`metric`/`term`/`rule`/`few_shot`/`negative_example`). Machine-parsed, CI-checked, graph-projected, retrieved as discrete units.
- **Markdown skills / reference docs** carry *prose, cross-entity, procedural* content: routing triggers, gotchas, query patterns, domain overview. Retrieved (vector + BM25) and injected as narrative. They **reference YAML assets by ID and never duplicate their data.**

Why both: you can't CI-check or graph-project a prose blob, and you can't cleanly express *"IF the question is about revenue, start from the transaction fact table, DO NOT use the brand list price"* in a per-column field, since that's cross-entity procedure. Anthropic and the book draw the same split.

> **Skills are the highest-value output, and curator-only**
>
> Anthropic's result: same model, **<21% without skills, 95%+ with them**.
> That's the single biggest lever. Skills have **no gold counterpart** (nothing
> in the manifests derives them), so **Arm 3 has no skills**. That is exactly
> why the curator (Arm 2) can *beat* the gold ceiling on skill-sensitive
> questions (D4).

## The consumption contract (who reads which tier)

| Consumer | Facts | Inference | Audit |
|---|---|---|---|
| **Server** (SQL generation) | ✅ | ✅ | ❌ **never injected** |
| **Viz / audit surface** | ✅ | ✅ | ✅ |
| **Gold-diff** (Arm 2 vs Arm 3) | n/a (identical in all arms) | ✅ the diff target | n/a |
| **Retrieval index** (R/V/G/D) | ✅ | ✅ | ❌ |

The loader enforces the contract: the server's context is built from Facts + Inference only. Audit-tier prose (evidence, provenance) can therefore be as verbose as humans need without costing the server tokens or adding noise. If Audit ever bloats the files, the escape hatch is a sidecar (not needed at BIRD scale).

## Governance overrides (human-authored, outside the three tiers)

One field is authored by neither the catalog nor the curator, but by a **human owner** (D6): `governance.excluded`. On a column or table, when a human sets it `true` after review, the asset is **removed entirely** from everything the server sees (retrieval, the presented schema, the graph) in **all environments, no toggle, permanently**. It is still shown in the viz/audit surface (marked, with reason) so the exclusion is auditable, and guardrail L3 hard-blocks it as defense-in-depth.

```yaml
# on tbl_beer_factory_transaction, column CreditCardNumber
governance:
  excluded: true
  reason: "PII (payment card number); never surface to the server"
  by: minhaoz
  at: "2026-07-08"
```

Distinct from the curator's `reliability.status: suspect`:

| | `reliability: suspect` | `governance.excluded` |
|---|---|---|
| Author | curator (AI), adversary-checked | human owner (certified) |
| Means | "looks unreliable" | "decided: never use" |
| Serve effect | soft-warn or hard-block (env-toggle) | **removed entirely**, all envs, no toggle |

Escalation path: curator flags `suspect` → human reviews (D6) → leaves it, or escalates to `excluded`. Kept **out of the autonomous eval arms** (so Arm 2 stays pure-curator); it's the human-in-the-loop governance capability for enterprise deployments.

## Directory layout

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

> **D15**: the corpus namespace `<db>` (directory above, ID formats below) models a **schema**, not a database — one database holds many schemas, and cross-schema joins run as qualified `schema.table` SQL. Renaming the field/dir `db` → `schema` is decided, not yet built; **ID values are unchanged** (`tbl_<schema>_<name>`), so the `<db>` placeholders stand as-is.

## ID conventions (CI regex-checked)

| Asset | ID format | Example |
|---|---|---|
| table | `tbl_<db>_<name>` | `tbl_beer_factory_customers` |
| column *(inline; id derived by loader)* | `col_<db>_<table>_<physical>` | `col_beer_factory_customers_CustomerID` |
| join | `join_<left>_<right>` | `join_transaction_customers` |
| few_shot | `fs_<db>_<n>` | `fs_beer_factory_001` |
| term | `term_<name>` | `term_revenue` |
| metric | `metric_<name>` | `metric_revenue` |
| rule | `rule_<name>` | `rule_boolean_flags` |
| negative_example | `neg_<db>_<n>` | `neg_beer_factory_001` |

The **physical ↔ meaning bridge** runs through every table/column: `physical_name` is the identifier as it exists in the live DB (obfuscated for BIRD, cryptic in enterprise data). SQL emits this; the Inference tier carries the *meaning*. The curator's whole job is filling meaning for cryptic physical names, and this is identical in BIRD and enterprise deployments.

---

## Asset: `table` (with inline columns)

```yaml
# tables/tbl_beer_factory_customers.yaml
asset_type: table
id: tbl_beer_factory_customers

# ── Facts (catalog/data) ──
schema: beer_factory                   # scoping namespace = Postgres/Redshift schema / corpus subtree
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

## Asset: `join` (FK is inferred; BIRD withholds it)

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

## Asset: `few_shot`

```yaml
# few-shots/fs_beer_factory_001.yaml
asset_type: few_shot
id: fs_beer_factory_001

# ── Facts ──
schema: beer_factory

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

## Asset: `term` (synonyms + relationships inline)

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

## Asset: `metric` (inline rules; no per-asset gold, D4)

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

## Asset: `rule` / `context` (standalone)

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

## Asset: `negative_example`

Marks a question class as **unanswerable from this data** → fires the refuse-gate's canned escalation (D5). Curator-proposed from self-eval coverage gaps (dev) or owner-curated (prod); adversary-checked; matched at serve time by semantic similarity.

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

## Asset: `skill` (Markdown, not YAML)

Prose procedural knowledge per domain. Frontmatter carries the same provenance as YAML assets (auditable, but no gold). The body is retrieved and injected into the server prompt.

```markdown
---
# skills/routing.md
skill_id: skill_beer_factory_routing
schema: beer_factory
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

Skills reference typed assets by ID; they do **not** restate the assets' data. A skill is pure curator value-add: there is no gold skill to diff against.

---

## CI reference-integrity (the "done-enough" signal)

CI validates the corpus and its pass doubles as the curator's machine-checkable stop signal (D9):

- **ID regex**: every `id` matches its convention.
- **Physical existence**: every `physical_name` / `on` column exists in the live catalog.
- **Reference resolution**: `references`, `binding.asset_id`, `related_terms[].id`, `metric.base_table`, `rule.scope[]` all resolve to existing assets.
- **Enum validity**: `role`, `reliability.status`, `logical_type`, `complexity`, `cardinality`, `relation`, `kind` ∈ their allowed sets.
- *(Eval-harness layer, not schema)*: few-shot `source_refs ⊆ train split` (leakage guard).

## Graph projection (all derived from YAML; Neo4j never authored)

| Edge | From → To | Sourced from |
|---|---|---|
| `HAS_COLUMN` | Table → Column | inline `columns[]` |
| `JOINS_TO` | Table → Table (props: on, cardinality, cost) | `join` |
| `REFERENCES` | Column → Column | `column.references` |
| `BINDS_TO` | Term → Metric/Table/Column | `term.binding` |
| `SYNONYM_OF` / `BROADER_THAN` / `USES` | Term → Term | `term.related_terms[]` |
| `DERIVED_FROM` | Metric → Table/Column | `metric.base_table` / expression |

BIRD uses an in-memory graph (networkx) for Steiner planning; Neo4j is the optional enterprise-scale projection.

## Gold vs curator (the same schema, two fillers)

- **Curator (Arm 2)** fills the Inference tier by *inference*: descriptions, roles, `references`, `reliability`, `confidence`, with `audit.*_evidence` recording why.
- **Gold (Arm 3)** fills the *same* Inference fields deterministically from the manifests: real names (rename map), FK graph (original schema), and any `reliability.status=suspect` flags the manifest records, with `provenance.source: gold`, `confidence: 1.0`.
- Facts are identical across arms (read from the catalog). The gold-diff compares the Inference tier only.
- **Skills (Markdown) are curator-only**: no manifest derives them, so Arm 3 has none. This is the mechanism by which Arm 2 can *exceed* the gold ceiling on skill-sensitive questions.
