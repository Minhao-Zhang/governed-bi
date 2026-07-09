# Server Diagrams

The serve-time DAG is implemented. These diagrams reflect `docs/server.md` and
the built modules in `src/governed_bi/server/` (including the LangGraph harness
in `server/graph.py`), `gateway/`, `retrieval/`, and `graph/`.

## Answer pipeline

```mermaid
flowchart TD
    Ask["Question + identity + session_id"] --> Ingest["Ingest<br/>attach working memory + RLS scope"]
    Ingest --> Bind["Query understanding<br/>bind terms to canonical assets"]
    Bind --> Route{"Intent route<br/>nl2sql / kpi_lookup / knowledge_qa / deep_analysis<br/>shared pipeline; per-route retrieval + memory budgets"}
    Route --> Cache{"SQL semantic cache<br/>cosine gate 0.92?"}

    Cache -->|hit| Reexecute["Re-execute cached SQL<br/>as current identity"]
    Cache -->|miss| Retrieve["RVGD retrieval<br/>R exact, V semantic, G graph, D dictionary"]
    Retrieve --> JoinPlan["Steiner-tree join planning<br/>penalize low-confidence joins"]
    JoinPlan --> Generate["SQL generation<br/>physical identifiers only"]
    Generate --> Guardrails{"Five guardrails pass?"}
    Bind --> RefuseGate{"Refuse-gate<br/>negative example match?"}

    RefuseGate -->|match| Refuse["Refuse / clarify<br/>fail closed"]
    RefuseGate -->|no match| Guardrails
    Guardrails -->|no| Refuse
    Guardrails -->|yes| Execute["Gateway.execute()<br/>read-only, forced LIMIT/timeout, audit"]
    Reexecute --> Execute
    Execute --> Result["Rows + provenance"]
    Result --> Stamp["Answer composition<br/>reliability stamp"]
    Stamp --> User["Answer to user"]
    Execute --> Audit["Audit/replay log"]
    Stamp --> CacheWrite["Cache successful SQL text<br/>TTL 15 minutes"]
```

## Ask-question sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Server as Server DAG
    participant Corpus as Server-visible Corpus
    participant Retrieval as RVGD / Graph
    participant Guardrails as Guardrails + Refuse-gate
    participant Gateway as Governed Gateway
    participant DB as Relational DB

    User->>Server: Ask question with identity
    Server->>Corpus: Load Facts + Inference context
    Server->>Server: Bind terms and choose intent route
    Server->>Retrieval: Retrieve assets, skills, and join paths
    Retrieval-->>Server: Context + join plan + uncertainty signals
    Server->>Server: Generate SQL
    par Hard SQL checks
        Server->>Guardrails: syntax, policy, AST, semantics, cost
    and Curated refusal check
        Server->>Guardrails: negative-example semantic match
    end
    alt any check fails
        Guardrails-->>Server: veto
        Server-->>User: Refusal or clarifying question
    else checks pass
        Guardrails-->>Server: pass
        Server->>Gateway: Execute SQL as user
        Gateway->>DB: Read-only query under RLS
        DB-->>Gateway: Rows
        Gateway-->>Server: QueryResult + audit metadata
        Server-->>User: Answer + provenance + reliability stamp
    end
```

## SQL semantic-cache sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Server
    participant Cache as SQL cache
    participant Pipeline as Full pipeline
    participant Gateway

    User->>Server: Ask repeated or similar question
    Server->>Cache: lookup(question, identity)
    Cache->>Cache: Embed question and compare identity-scoped SQL entries
    alt cosine >= 0.92 and TTL valid
        Cache-->>Server: SQL text only
        Server->>Gateway: Re-execute cached SQL as current identity
        Gateway-->>Server: fresh rows
        Server-->>User: Answer from fresh execution
    else miss
        Cache-->>Server: None
        Server->>Pipeline: retrieval, planning, generation, guardrails
        Pipeline-->>Server: guarded SQL + answer
        Server->>Cache: write_back(question, sql, identity)
        Server-->>User: Answer
    end
```

## Refuse-gate sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Server
    participant Corpus as Negative examples
    participant RefuseGate
    participant Guardrails

    User->>Server: Ask potentially out-of-scope question
    Server->>Corpus: Retrieve negative_example assets
    par Curated refusal
        Server->>RefuseGate: semantic match against negative patterns
    and SQL safety
        Server->>Guardrails: syntax, policy, AST, semantics, cost
    end
    alt negative match
        RefuseGate-->>Server: refusal reason + escalation
        Server-->>User: Refuse with canned escalation
    else hard guardrail veto
        Guardrails-->>Server: veto
        Server-->>User: Refuse or clarify
    else no refusal signal
        RefuseGate-->>Server: continue
        Server->>Server: execute normal answer path
    end
```

## Reliability and governance enforcement

```mermaid
stateDiagram-v2
    state "Server-visible column" as Visible
    state "Reliability ok" as Ok
    state "Reliability suspect" as Suspect
    state "Dev hard block" as DevBlock
    state "Prod soft warn" as ProdWarn
    state "Governance excluded" as Excluded
    state "Removed from server view" as Removed

    [*] --> Visible
    Visible --> Ok: reliability.status = ok
    Visible --> Suspect: curator flags suspect
    Suspect --> DevBlock: Environment.dev
    Suspect --> ProdWarn: Environment.prod
    DevBlock --> [*]: guardrail veto
    ProdWarn --> [*]: lower reliability stamp
    Visible --> Excluded: human sets governance.excluded = true
    Suspect --> Excluded: human escalates caveat
    Excluded --> Removed: Corpus.for_server()
    Removed --> [*]: hidden from retrieval and SQL context
```

## Guardrail stack

```mermaid
flowchart TD
    SQL["Generated SQL"] --> Syntax{"1. syntax<br/>valid SQL parse?"}
    Syntax -->|fail| Veto["veto / fail closed"]
    Syntax -->|pass| Policy{"2. policy blacklist<br/>read-only only?"}
    Policy -->|fail| Veto
    Policy -->|pass| AST{"3. AST column allowlist<br/>known, non-excluded,<br/>suspect policy respected?"}
    AST -->|fail| Veto
    AST -->|pass| Semantics{"4. term semantics<br/>columns match bound terms?"}
    Semantics -->|fail| Veto
    Semantics -->|pass| Cost{"5. cost / EXPLAIN<br/>under budget?"}
    Cost -->|fail| Veto
    Cost -->|pass| Pass["pass to Gateway.execute()"]
```

