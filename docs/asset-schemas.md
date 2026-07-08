# Agentic BI Asset Schemas

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

Why both: you can't CI-check or graph-project a prose blob, and you can't cleanly express *"IF the question is about eligibility rate, start from the derived table, DO NOT use raw counts"* in a per-column field, since that's cross-entity procedure. Anthropic and the book draw the same split.

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
governance:
  excluded: true
  reason: "PII / deprecated / known-bad, never surface"
  by: minhaoz
  at: "2026-07-07"
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

## ID conventions (CI regex-checked)

| Asset | ID format | Example |
|---|---|---|
| table | `tbl_<db>_<name>` | `tbl_california_schools_frpm` |
| column *(inline; id derived by loader)* | `col_<db>_<table>_<physical>` | `col_california_schools_frpm_lie_2` |
| join | `join_<left>_<right>` | `join_frpm_schools` |
| few_shot | `fs_<db>_<n>` | `fs_california_schools_003` |
| term | `term_<name>` | `term_eligibility_rate` |
| metric | `metric_<name>` | `metric_frpm_rate` |
| rule | `rule_<name>` | `rule_academic_year_format` |
| negative_example | `neg_<db>_<n>` | `neg_california_schools_002` |

The **physical ↔ meaning bridge** runs through every table/column: `physical_name` is the identifier as it exists in the live DB (obfuscated for BIRD, cryptic in enterprise data). SQL emits this; the Inference tier carries the *meaning*. The curator's whole job is filling meaning for cryptic physical names, and this is identical in BIRD and enterprise deployments.

---

## Asset: `table` (with inline columns)

```yaml
# tables/tbl_california_schools_frpm.yaml
asset_type: table
id: tbl_california_schools_frpm

# ── Facts (catalog/data) ──
db: california_schools                 # scoping namespace = the connection/database this belongs to
physical_name: biao_3                  # identifier in the live DB
row_count: 17686

# ── Inference (curator writes / gold fills; server-consumed) ──
description: "Free/reduced-price meal eligibility counts per school-year"
grain: "one row = one school × academic year"
confidence: 0.85

columns:
  - # Facts
    physical_name: lie_2
    physical_type: "varchar(20)"       # verbatim from catalog, dialect-specific
    logical_type: string               # normalized, portable (string/integer/decimal/date/datetime/boolean)
    nullable: false
    is_unique: true
    sample_values: ["01100170109835", "01100170112607"]
    # Inference
    description: "school+district identifier (CDS code)"
    role: primary_key                  # primary_key | foreign_key | key | measure | dimension
    references: null                   # col id if FK
    reliability: { status: ok, note: null }   # status: ok | suspect ; note: prose (server-visible)
    confidence: 0.9
    # Audit
    audit:
      description_evidence: "unique across all rows; joins to schools in 12 seed queries"
      provenance: { source: curator, model: claude-opus-4-8, status: draft, source_refs: [q1032] }

  - # Facts
    physical_name: lie_12
    physical_type: "numeric(10,2)"
    logical_type: decimal
    nullable: false
    is_unique: false
    sample_values: [512.00, 431.00, 1043.00]
    # Inference
    description: "enrollment - values appear tampered"
    role: measure
    references: null
    reliability:
      status: suspect
      note: "UNRELIABLE - DO NOT USE."
    confidence: 0.6
    # Governance (human-authored override; not curator, not gold)
    governance: { excluded: false }    # human sets true → asset removed everywhere the server sees
    # Audit
    audit:
      reliability_evidence: "corr(values, join-key order)=0.02; near-synonym of lie_7"
      description_evidence: "inferred from value range + 3 seed queries"
      provenance: { source: curator, model: claude-opus-4-8, status: draft, source_refs: [q1032, q1077] }

# ── Audit (table-level) ──
audit:
  description_evidence: "table name obfuscated; inferred from column set + seed-query usage"
  provenance: { source: curator, model: claude-opus-4-8, status: draft, built_at: "2026-07-07" }
```

## Asset: `join` (FK is inferred; BIRD withholds it)

```yaml
# joins/join_frpm_schools.yaml
asset_type: join
id: join_frpm_schools

# ── Facts (the referenced physical columns exist in the catalog) ──
left_table: tbl_california_schools_frpm
right_table: tbl_california_schools_schools
on: "biao_3.lie_2 = biao_1.lie_0"      # physical names

# ── Inference (the EXISTENCE of the edge is inferred) ──
cardinality: many_to_one               # inferred from uniqueness of the right key
cost: 1.0                              # Steiner-planner input (derivable from cardinality × row_counts)
confidence: 0.82

# ── Audit ──
audit:
  evidence: "value-overlap 0.97 (frpm.lie_2 ⊆ schools.lie_0); right key unique; used in 12 seed queries"
  provenance: { source: curator, model: claude-opus-4-8, status: draft, source_refs: [q1032, q1044] }
```

## Asset: `few_shot`

```yaml
# few-shots/fs_california_schools_003.yaml
asset_type: few_shot
id: fs_california_schools_003

# ── Facts ──
db: california_schools

# ── Inference (curator selects/distills; server-consumed as a prompt exemplar) ──
question: "Which schools have the highest free-meal eligibility rate?"
sql: |
  SELECT ... FROM biao_3 ...           # gold SQL in the live (obfuscated) identifiers
bound_terms: [free-meal, eligibility rate]
complexity: medium                     # simple | medium | complex → controls injection count
confidence: 0.9

# ── Audit ──
audit:
  provenance: { source: curator, status: draft, source_refs: [q1032] }
  # NB: the BIRD eval harness's CI additionally checks source_refs ⊆ train split (leakage guard).
  # That is a harness rule, not a schema rule (P2).
```

## Asset: `term` (synonyms + relationships inline)

```yaml
# terms/term_eligibility_rate.yaml
asset_type: term
id: term_eligibility_rate

# ── Inference (curator maps business language → assets) ──
name: "free-meal eligibility rate"
synonyms: ["FRPM rate", "free/reduced-price meal rate", "eligibility %"]
binding: { asset_type: metric, asset_id: metric_frpm_rate }
related_terms:                         # projects into the graph
  - { id: term_enrollment, relation: uses }   # relation: synonym_of | broader_than | uses
confidence: 0.7

# ── Audit ──
audit:
  evidence: "phrase varies across seed questions (paraphrase dimension); all map to one computation"
  provenance: { source: curator, status: draft, source_refs: [q1032, q1101] }
```

## Asset: `metric` (inline rules; no per-asset gold, D4)

```yaml
# metrics/metric_frpm_rate.yaml
asset_type: metric
id: metric_frpm_rate

# ── Inference (curator derives from evidence + seed queries) ──
name: "free-meal eligibility rate"
base_table: tbl_california_schools_frpm
expression: "SUM(free_count) / NULLIF(SUM(enrollment), 0)"   # in meaning; SQL-gen maps to physical
dimensions: [school, academic_year]
rules:
  - { kind: filter, note: "exclude rows where enrollment = 0" }
confidence: 0.6

# ── Audit ──
audit:
  evidence: "BIRD evidence field: 'eligible free rate = free_count / total'; seen in 5 seed queries"
  provenance: { source: curator, status: draft, version: "0.1.0", source_refs: [q1050] }
```

## Asset: `rule` / `context` (standalone)

```yaml
# rules/rule_academic_year_format.yaml
asset_type: rule
id: rule_academic_year_format

# ── Inference ──
kind: business_rule                    # business_rule | context | constraint
scope: [tbl_california_schools_frpm]   # assets it constrains; empty = global
statement: "academic_year is the starting calendar year (2014 = the 2014-15 school year)"
confidence: 0.7

# ── Audit ──
audit:
  evidence: "sample values 2014, 2015; BIRD evidence note"
  provenance: { source: curator, status: draft, source_refs: [q1060] }
```

## Asset: `negative_example`

Marks a question class as **unanswerable from this data** → fires the refuse-gate's canned escalation (D5). Curator-proposed from self-eval coverage gaps (dev) or owner-curated (prod); adversary-checked; matched at serve time by semantic similarity.

```yaml
# negatives/neg_california_schools_002.yaml
asset_type: negative_example
id: neg_california_schools_002

# ── Inference (curator proposes; human certifies) ──
pattern: "questions about teacher salaries / compensation"
example_questions: ["What is the average teacher salary per district?"]
reason: "no table in this DB covers compensation"
escalation: "not answerable from this data - contact <owner>"
confidence: 0.8

# ── Audit ──
audit:
  evidence: "3 self-eval questions on compensation found no covering table"
  provenance: { source: curator, status: draft, source_refs: [q1180] }
```

## Asset: `skill` (Markdown, not YAML)

Prose procedural knowledge per domain. Frontmatter carries the same provenance as YAML assets (auditable, but no gold). The body is retrieved and injected into the server prompt.

```markdown
---
# skills/routing_frpm.md
skill_id: skill_california_schools_routing
db: california_schools
kind: routing              # routing | gotchas | pattern | domain_overview
provenance: { source: curator, model: claude-opus-4-8, status: draft, source_refs: [q1032, q1044] }
---

# California Schools: routing & gotchas

## Scope
Answers about schools, districts, and free/reduced-price-meal eligibility.
Hub table: `tbl_california_schools_schools`. Join everything via the CDS code.

## Routing triggers
- IF the question is about eligibility rate → use `metric_frpm_rate`; join
  `tbl_california_schools_frpm` → `tbl_california_schools_schools` via `join_frpm_schools`.
- DO NOT use `col_california_schools_frpm_lie_12` for counts, it is flagged unreliable (see its reliability caveat).

## Gotchas
- `academic_year` is the *starting* calendar year (see `rule_academic_year_format`).
- Several near-synonym columns are decoys; prefer the primary column named in each table asset.

## Query patterns
- Eligibility rate = free_count / enrollment, excluding enrollment = 0.
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
- **Gold (Arm 3)** fills the *same* Inference fields deterministically from the manifests: real names (rename map), FK graph (original schema), and `reliability.status=suspect` on every manifest decoy, with `provenance.source: gold`, `confidence: 1.0`.
- Facts are identical across arms (read from the catalog). The gold-diff compares the Inference tier only.
- **Skills (Markdown) are curator-only**: no manifest derives them, so Arm 3 has none. This is the mechanism by which Arm 2 can *exceed* the gold ceiling on skill-sensitive questions.
