# 0020: Ossie as the export format, and a viewer as its own project

- **Status:** Accepted (2026-10-19).
- **Deciders:** project owner.
- **Scope:** how the semantic layer leaves this repository and how people look at it. Replaces the
  [Ossie plan](../reviews/2026-10-ossie-plan.md), which made Ossie the storage format.
- **Related:** [0017](0017-a-served-chat-app-over-a-layer-the-user-brings.md) is the scope;
  [0019](0019-one-plain-chat-page.md) is the chat page, which does not show the layer.

## Context

Apache Ossie (incubating, formerly Open Semantic Interchange) is a vendor-neutral format for
semantic models, backed by Snowflake, dbt Labs, Salesforce and others. At commit `53418ba` of
`apache/ossie` (2026-10-18) the spec is `0.2.0.dev0`, which calls itself a draft and says not to
depend on it in production. The last release is 0.1.1, `apache-ossie` is not on PyPI, and dbt
reads only 0.1.x documents.

The people who write and review a semantic layer need to see it whole: its datasets, how they
relate, its metrics, and the text the model reads. No tool does that for an Ossie file today. The
Ossie repository ships only `convert` and `validate` commands, and the sites that track Ossie name
no viewer.

## Decision

### 1. Storage stays, export is added

The corpus stays in its per-asset YAML layout, which keeps per-definition diffs and puts no draft
spec under the engine. A new exporter writes one Ossie model per database, 57 for BIRD-corpus. A
test checks that every exported model passes `../ossie/validation/validate.py` and that a round
trip through the exporter's own reader loses nothing it does not list.

The mapping:

| Asset | Ossie |
|---|---|
| Schema | the model; `summary` and `body` become `description`; `rules` go into `ai_context.instructions` |
| Table | a dataset whose `source` is `db.schema.table` |
| Column | a field whose expression is the column name; `physical_type` becomes `datatype` |
| Join | a relationship from the many side to the one side, with `from_columns` and `to_columns` |
| Metric | a metric whose expression references `dataset.field` |
| Term | synonyms on the field or metric it binds to, plus a `terms` key in `ai_context` |
| Few-shot example | a model-level `ai_context.verified_queries` list in the shape Snowflake uses, so it lines up with upstream discussion #82 if that lands |
| Negative example | a model-level `ai_context.unanswerable` list |
| Provenance, reliability, governance | a `custom_extensions` entry with `vendor_name: GOVERNED_BI` |

Ossie relationships carry no cardinality and are always many to one. The exporter therefore
writes the engine's cardinality into the `GOVERNED_BI` extension on every relationship, and puts
the 112 many-to-many joins in a model-level extension list. Two `talkingdata` joins declare no
cardinality, and the exporter reports them instead of guessing. Metric expressions are exported as
written, under `ANSI_SQL`; any that sqlglot cannot parse are counted in the export report.

Reading Ossie as an input, so a user can bring a layer written elsewhere, waits for the 0.2.0
release.

### 2. The viewer is a separate project

The viewer is `ossie-viewer`, a public repository under the owner's GitHub account
(`Minhao-Zhang/ossie-viewer`), to be offered to the Ossie project once it is useful. It has no
backend. It opens an Ossie file from a picker,
a URL or a drag and drop. It shows the datasets and their relationships as a graph, then each
dataset's fields, the metrics and what they reference, `ai_context` at every level, and each
`custom_extensions` entry by vendor name with its `data` parsed. It reads 0.1.x and `0.2.0.dev0`
documents and says which version it opened. A link can name a dataset, field or metric, and the
viewer opens on it.

It follows the same style rule as 0019: plain, quiet and focused on the content.

The chat page's "Details" gains one link, "Open in the layer viewer", which opens the exported
model on the first dataset the answer used. It is for whoever maintains the layer; the person
asking does not need it.

## Consequences

- The exporter is the only code in this repository that knows Ossie, so a spec change touches one
  module.
- The viewer is useful to anyone with an Ossie file, including models converted from dbt or
  Snowflake, not only to users of this engine.
- Two repositories have to agree on a spec version. The exporter records the Ossie commit it
  wrote against, and the viewer reports what it can read.

## Rejected

- **Ossie as the storage format.** It would put a draft spec under the engine and lose the
  per-definition diffs.
- **The viewer inside the chat app.** Different readers, a different release cycle, and it could
  not be opened without a running server. It would also repeat the mistake 0017 undoes: one
  repository trying to be several products.
- **Showing the layer to the person asking.** They need the layer only as far as it explains their
  answer, which "Details" already does.
