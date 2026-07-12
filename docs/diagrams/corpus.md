# Corpus Diagrams

_[English](corpus.md) · [简体中文](corpus.zh.md)_

The corpus package is the implemented center of the repo. It loads Git-tracked
YAML assets and Markdown skills, validates them, and exposes different views for
server runtime and human audit.

D15 renames the corpus namespace field/dir historically named `db` to `schema`
(`corpus/<schema>/`, `col_<schema>_<table>_<column>`). **API/presenter wire and
on-disk YAML** both use `schema` (`TableAsset.schema`, skill frontmatter). Asset
IDs are unchanged. Diagram folder labels below use `<schema>/`.

## Corpus consumption contract

```mermaid
flowchart LR
    Git["Git<br/>single source of truth"]
    Files["corpus/&lt;schema&gt;/<br/>tables, joins, terms, metrics,<br/>rules, few-shots, negatives, skills"]
    Loader["load_corpus()<br/>parse YAML + skill frontmatter"]
    FullCorpus["Corpus<br/>Facts + Inference + Audit + Governance"]
    Validator["validate_corpus()<br/>IDs, duplicates, references"]
    VizView["Viz/audit view<br/>full corpus"]
    ServerView["Corpus.for_server()<br/>strip Audit; drop excluded assets/columns"]
    Runtime["Server retrieval + SQL generation context"]
    Generated["_generated/<br/>derived projections only"]

    Git --> Files
    Files --> Loader
    Loader --> FullCorpus
    FullCorpus --> Validator
    FullCorpus --> VizView
    FullCorpus --> ServerView
    ServerView --> Runtime
    ServerView -. rebuilds .-> Generated
```

## Loader internals

```mermaid
flowchart TD
    Start["load_corpus(root, schema?)"] --> Dirs{"schema argument?"}
    Dirs -->|yes| OneDb["load root / schema"]
    Dirs -->|no| AllDbs["scan child dirs<br/>skip _generated"]
    OneDb --> AssetDirs["scan asset directories"]
    AllDbs --> AssetDirs

    AssetDirs --> YamlLoader["_CorpusYamlLoader<br/>YAML 1.2 bool semantics"]
    YamlLoader --> LoadYaml["_load_yaml()"]
    LoadYaml --> ParseAsset["parse_asset()<br/>Pydantic discriminated union"]
    ParseAsset --> Assets["Corpus.assets"]

    AssetDirs --> SkillFiles["scan skills/*.md"]
    SkillFiles --> SplitFront["_split_frontmatter()"]
    SplitFront --> ParseSkill["parse_skill_frontmatter()"]
    ParseSkill --> Skills["Corpus.skills"]

    Assets --> Corpus["Corpus"]
    Skills --> Corpus
    Corpus --> ForServer["for_server()<br/>copy assets, remove Audit,<br/>drop governance.excluded"]
```

## Validation internals

```mermaid
flowchart TD
    Assets["list[Asset]"] --> IdRegex["ID regex per asset type<br/>ids.is_valid_id()"]
    IdRegex --> Duplicates["duplicate ID check"]
    Duplicates --> Pools["build resolvable pools<br/>tables, metrics, terms, columns"]
    Pools --> ColumnIds["derive column IDs<br/>col_schema_table_column"]
    ColumnIds --> RefChecks["reference checks"]

    RefChecks --> TableRefs["column.references -> column pool"]
    RefChecks --> JoinRefs["join.left_table/right_table -> table pool"]
    RefChecks --> TermRefs["term.binding -> typed pool<br/>term.related_terms -> term pool"]
    RefChecks --> MetricRefs["metric.base_table -> table pool"]
    RefChecks --> RuleRefs["rule.scope -> asset or column pool"]
    RefChecks --> FewShotLeak["optional few-shot leakage guard<br/>source_refs subset train_refs"]
    RefChecks --> CatalogHook["optional physical-existence hook<br/>via the gateway catalog reader"]

    TableRefs --> Findings["list[Finding]"]
    JoinRefs --> Findings
    TermRefs --> Findings
    MetricRefs --> Findings
    RuleRefs --> Findings
    FewShotLeak --> Findings
    CatalogHook --> Findings
    Findings --> Green{"is_green(findings)?"}
```

## Pydantic asset model

```mermaid
classDiagram
    class Asset {
        discriminated union by asset_type
    }
    class TableAsset {
        asset_type
        id
        schema
        physical_name
        row_count
        description
        grain
        confidence
        columns
        governance
        audit
    }
    class Column {
        physical_name
        physical_type
        logical_type
        nullable
        is_unique
        sample_values
        description
        role
        references
        reliability
        confidence
        governance
        audit
    }
    class JoinAsset {
        left_table
        right_table
        on
        cardinality
        cost
        confidence
        audit
    }
    class FewShotAsset {
        schema
        question
        sql
        bound_terms
        complexity
        confidence
        audit
    }
    class TermAsset {
        name
        synonyms
        binding
        related_terms
        confidence
        audit
    }
    class MetricAsset {
        name
        base_table
        expression
        dimensions
        rules
        confidence
        audit
    }
    class RuleAsset {
        kind
        scope
        statement
        confidence
        audit
    }
    class NegativeExampleAsset {
        pattern
        example_questions
        reason
        escalation
        confidence
        audit
    }
    class SkillFrontmatter {
        skill_id
        schema
        kind
        provenance
    }
    class Governance {
        excluded
        reason
        by
        at
    }
    class Reliability {
        status
        note
    }
    class Audit {
        provenance
        extra evidence fields
    }
    class Provenance {
        source
        status
        model
        version
        source_refs
        built_at
    }

    Asset <|.. TableAsset
    Asset <|.. JoinAsset
    Asset <|.. FewShotAsset
    Asset <|.. TermAsset
    Asset <|.. MetricAsset
    Asset <|.. RuleAsset
    Asset <|.. NegativeExampleAsset
    TableAsset "1" *-- "*" Column
    TableAsset --> Governance
    Column --> Governance
    Column --> Reliability
    TableAsset --> Audit
    Column --> Audit
    JoinAsset --> Audit
    FewShotAsset --> Audit
    TermAsset --> Audit
    MetricAsset --> Audit
    RuleAsset --> Audit
    NegativeExampleAsset --> Audit
    Audit --> Provenance
    SkillFrontmatter --> Provenance
```

## Graph projection edge taxonomy

```mermaid
flowchart LR
    Table["Table asset"] -->|HAS_COLUMN| Column["Inline column"]
    Table -->|JOINS_TO<br/>from join asset| OtherTable["Table asset"]
    Column -->|REFERENCES<br/>column.references| OtherColumn["Column"]
    Term["Term asset"] -->|BINDS_TO| Metric["Metric asset"]
    Term -->|BINDS_TO| Table
    Term -->|BINDS_TO| Column
    Term -->|SYNONYM_OF / BROADER_THAN / USES| OtherTerm["Term asset"]
    Metric -->|DERIVED_FROM<br/>base_table + expression| Table
    Metric -->|DERIVED_FROM<br/>expression columns| Column

    Corpus["Corpus.for_server()"] -. input .-> Projection["build_graph()<br/>networkx.MultiDiGraph"]
    Projection -. emits .-> Table
    Projection -. emits .-> Term
```

## Corpus CLI sequence

```mermaid
sequenceDiagram
    autonumber
    actor Developer
    participant CLI as corpus.cli
    participant Loader as load_corpus
    participant Validator as validate_corpus

    Developer->>CLI: python -m governed_bi.corpus.cli corpus/beer_factory
    CLI->>CLI: Detect single DB dir or corpus root
    CLI->>Loader: load corpus assets and skills
    Loader-->>CLI: Corpus
    CLI->>Validator: validate_corpus(corpus.assets)
    Validator-->>CLI: findings
    alt no findings
        CLI-->>Developer: CI green and exit 0
    else findings exist
        CLI-->>Developer: print findings and exit 1
    end
```

