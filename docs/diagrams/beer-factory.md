# Beer Factory Example Diagrams

These diagrams ground the architecture in the worked example under
`corpus/beer_factory/`, which is authored over the real BIRD `beer_factory`
database in `data/bird/beer_factory.sqlite`.

## Semantic mini-graph

```mermaid
flowchart LR
    TermRevenue["term_revenue<br/>revenue / sales"]
    TermBrand["term_brand<br/>root beer brand"]
    MetricRevenue["metric_revenue<br/>SUM(PurchasePrice)"]
    MetricRating["metric_avg_rating<br/>AVG(StarRating)"]

    TableTxn["tbl_beer_factory_transaction<br/>transaction"]
    TableCust["tbl_beer_factory_customers<br/>customers"]
    TableRB["tbl_beer_factory_rootbeer<br/>rootbeer"]
    TableBrand["tbl_beer_factory_rootbeerbrand<br/>rootbeerbrand"]
    TableReview["tbl_beer_factory_rootbeerreview<br/>rootbeerreview"]

    ColTxnPrice["PurchasePrice<br/>measure"]
    ColTxnCust["CustomerID (FK)"]
    ColTxnRB["RootBeerID (FK)"]
    ColTxnCC["CreditCardNumber<br/>excluded (PII)"]
    ColCustPK["CustomerID (PK)"]
    ColZip["ZipCode<br/>suspect: INTEGER, loses leading zeros"]
    ColRating["StarRating<br/>measure"]

    Rule["rule_boolean_flags<br/>flags are 'TRUE'/'FALSE' text"]
    Skill["skill_beer_factory_routing"]
    Negative["neg_beer_factory_001<br/>employees / staffing: out of scope"]

    TermRevenue -->|BINDS_TO| MetricRevenue
    TermRevenue -->|USES| TermBrand
    TermBrand -->|BINDS_TO| TableBrand
    MetricRevenue -->|DERIVED_FROM| TableTxn
    MetricRevenue -->|DERIVED_FROM| ColTxnPrice
    MetricRating -->|DERIVED_FROM| TableReview
    MetricRating -->|DERIVED_FROM| ColRating

    TableTxn -->|HAS_COLUMN| ColTxnPrice
    TableTxn -->|HAS_COLUMN| ColTxnCust
    TableTxn -->|HAS_COLUMN| ColTxnRB
    TableTxn -->|HAS_COLUMN| ColTxnCC
    TableCust -->|HAS_COLUMN| ColCustPK
    TableCust -->|HAS_COLUMN| ColZip
    TableReview -->|HAS_COLUMN| ColRating

    ColTxnCust -->|REFERENCES| ColCustPK
    TableTxn --> JoinTC["join_transaction_customers"] --> TableCust
    TableTxn --> JoinTR["join_transaction_rootbeer"] --> TableRB
    TableRB --> JoinRB["join_rootbeer_rootbeerbrand"] --> TableBrand
    TableReview --> JoinRvB["join_review_rootbeerbrand"] --> TableBrand

    Rule -->|SCOPE| TableBrand
    Skill -->|mentions| MetricRevenue
    Skill -->|mentions| MetricRating
    Skill -->|warns against| ColZip
    Skill -->|warns against| ColTxnCC
```

## Top-rated-brand question sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Server
    participant Corpus as Beer factory corpus
    participant Retrieval as RVGD / skills
    participant Graph as Join planner
    participant Guardrails
    participant Gateway

    User->>Server: Which root beer brand has the highest average review rating?
    Server->>Corpus: Bind term_brand; recognize "average rating"
    Corpus-->>Server: metric_avg_rating + tbl_beer_factory_rootbeerbrand
    Server->>Retrieval: Retrieve routing skill and few-shot fs_beer_factory_001
    Retrieval-->>Server: Use rootbeerreview + rootbeerbrand; exclude null ratings
    Server->>Graph: Need reviews joined to brands
    Graph-->>Server: join_review_rootbeerbrand on rootbeerreview.BrandID = rootbeerbrand.BrandID
    Server->>Server: Generate SQL using physical identifiers
    Server->>Guardrails: syntax, read-only policy, column allowlist, semantics, cost
    alt SQL touches an excluded/suspect column (CreditCardNumber, ZipCode)
        Guardrails-->>Server: veto in dev or lower stamp in prod
        Server-->>User: Refuse, clarify, or low-stamp result depending on environment
    else governed columns only
        Guardrails-->>Server: pass
        Server->>Gateway: Execute guarded SQL as user
        Gateway-->>Server: QueryResult
        Server-->>User: Ranked brands + reliability stamp
    end
```

## Example refusal path

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Server
    participant Corpus as negative_example asset
    participant RefuseGate

    User->>Server: How many employees work at the factory?
    Server->>Corpus: Retrieve neg_beer_factory_001
    Corpus-->>Server: pattern employees / staffing / headcount
    Server->>RefuseGate: Compare question to negative pattern
    RefuseGate-->>Server: match; no table covers employees or payroll
    Server-->>User: not answerable from this data - contact owner
```

## Few-shot to SQL mapping

```mermaid
flowchart TD
    Question["Which root beer brand has the highest average review rating?"]
    FewShot["fs_beer_factory_001<br/>medium complexity exemplar"]
    Metric["metric_avg_rating<br/>AVG(StarRating)"]
    PhysicalSQL["Physical SQL<br/>AVG(r.StarRating) AS avg_rating"]
    Join["JOIN rootbeerbrand AS b<br/>ON r.BrandID = b.BrandID"]
    Filter["WHERE r.StarRating IS NOT NULL"]
    Display["GROUP BY b.BrandName<br/>ORDER BY avg_rating DESC"]

    Question --> FewShot
    FewShot --> Metric
    Metric --> PhysicalSQL
    PhysicalSQL --> Join
    Join --> Filter
    Filter --> Display
```
