# California Schools Example Diagrams

These diagrams ground the architecture in the worked example under
`corpus/california_schools/`.

## Semantic mini-graph

```mermaid
flowchart LR
    TermRate["term_eligibility_rate<br/>free-meal eligibility rate"]
    TermEnrollment["term_enrollment<br/>enrollment"]
    MetricRate["metric_frpm_rate<br/>SUM(free_count) / NULLIF(SUM(enrollment), 0)"]
    TableFRPM["tbl_california_schools_frpm<br/>biao_3"]
    TableSchools["tbl_california_schools_schools<br/>biao_1"]
    Join["join_frpm_schools<br/>biao_3.lie_2 = biao_1.lie_0<br/>confidence 0.82"]
    ColCDSFrpm["col_california_schools_frpm_lie_2<br/>CDS code"]
    ColFree["col_california_schools_frpm_lie_5<br/>free meal count"]
    ColEnroll["col_california_schools_frpm_lie_7<br/>enrollment denominator"]
    ColSuspect["col_california_schools_frpm_lie_12<br/>suspect decoy<br/>UNRELIABLE - DO NOT USE"]
    ColCDSSchools["col_california_schools_schools_lie_0<br/>canonical CDS code"]
    ColName["col_california_schools_schools_lie_3<br/>school name"]
    RuleYear["rule_academic_year_format<br/>year is start calendar year"]
    Skill["skill_california_schools_routing<br/>routing + gotchas"]
    Negative["neg_california_schools_002<br/>refuse-gate pattern:<br/>teacher salary (no covering table)"]

    TermRate -->|BINDS_TO| MetricRate
    TermRate -->|USES| TermEnrollment
    TermEnrollment -->|BINDS_TO| ColEnroll
    MetricRate -->|DERIVED_FROM| TableFRPM
    MetricRate -->|DERIVED_FROM| ColFree
    MetricRate -->|DERIVED_FROM| ColEnroll
    TableFRPM -->|HAS_COLUMN| ColCDSFrpm
    TableFRPM -->|HAS_COLUMN| ColFree
    TableFRPM -->|HAS_COLUMN| ColEnroll
    TableFRPM -->|HAS_COLUMN| ColSuspect
    TableSchools -->|HAS_COLUMN| ColCDSSchools
    TableSchools -->|HAS_COLUMN| ColName
    ColCDSFrpm -->|REFERENCES| ColCDSSchools
    TableFRPM --> Join
    Join --> TableSchools
    RuleYear -->|SCOPE| TableFRPM
    Skill -->|mentions| MetricRate
    Skill -->|mentions| Join
    Skill -->|warns against| ColSuspect
```

## Eligibility-rate question sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Server
    participant Corpus as California Schools corpus
    participant Retrieval as RVGD / skills
    participant Graph as Join planner
    participant Guardrails
    participant Gateway

    User->>Server: Which schools have the highest free-meal eligibility rate?
    Server->>Corpus: Bind term_eligibility_rate
    Corpus-->>Server: metric_frpm_rate + term_enrollment
    Server->>Retrieval: Retrieve routing_frpm skill and few-shot fs_003
    Retrieval-->>Server: Use biao_3, biao_1, lie_5, lie_7 and avoid lie_12
    Server->>Graph: Need FRPM table joined to schools table
    Graph-->>Server: join_frpm_schools on biao_3.lie_2 = biao_1.lie_0
    Server->>Server: Generate SQL using physical identifiers
    Server->>Guardrails: Check syntax, read-only policy, column allowlist, semantics, cost
    alt suspect decoy lie_12 used
        Guardrails-->>Server: veto in dev or lower stamp in prod
        Server-->>User: Refuse, clarify, or low-stamp result depending environment
    else governed columns used
        Guardrails-->>Server: pass
        Server->>Gateway: Execute guarded SQL as user
        Gateway-->>Server: QueryResult
        Server-->>User: Ranked schools + reliability stamp
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

    User->>Server: What is the average teacher salary per district?
    Server->>Corpus: Retrieve neg_california_schools_002
    Corpus-->>Server: pattern teacher salaries / compensation
    Server->>RefuseGate: Compare question to negative pattern
    RefuseGate-->>Server: match with no compensation table coverage
    Server-->>User: not answerable from this data - contact owner
```

## Few-shot to SQL mapping

```mermaid
flowchart TD
    Question["Which schools have the highest free-meal eligibility rate?"]
    FewShot["fs_california_schools_003<br/>medium complexity exemplar"]
    Metric["metric_frpm_rate<br/>free_count / enrollment"]
    PhysicalSQL["Physical SQL<br/>SUM(f.lie_5) / NULLIF(SUM(f.lie_7), 0)"]
    Join["JOIN biao_1 AS s<br/>ON f.lie_2 = s.lie_0"]
    Display["GROUP BY s.lie_3<br/>ORDER BY rate DESC"]
    Caveat["Avoid lie_12<br/>suspect enrollment decoy"]

    Question --> FewShot
    FewShot --> Metric
    Metric --> PhysicalSQL
    PhysicalSQL --> Join
    Join --> Display
    Caveat -. guardrail / prompt shaping .-> PhysicalSQL
```

