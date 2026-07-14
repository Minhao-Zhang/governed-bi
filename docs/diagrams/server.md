# Server Diagrams

_[English](server.md) · [简体中文](server.zh.md)_

The serve-time StateGraph is implemented. These diagrams reflect `docs/server.md`
and the built modules in `src/governed_bi/server/` (the agentic rails +
`GovernanceMiddleware` in `server/agent.py` and `server/middleware.py`),
`gateway/`, `retrieval/`, and `graph/`.

## Answer pipeline

```mermaid
flowchart TD
    Ask["Question + identity + session_id"] --> Ingest["Ingest<br/>attach working memory + RLS scope"]
    Ingest --> Bind["Query understanding<br/>bind terms to canonical assets"]
    Bind --> Route{"Intent route<br/>nl2sql / kpi_lookup / knowledge_qa / deep_analysis<br/>shared pipeline; per-route retrieval + memory budgets"}
    Route --> Cache{"SQL semantic cache<br/>cosine gate 0.92?"}

    Cache -->|hit| Reexecute["Re-execute cached SQL<br/>as current identity"]
    Cache -->|miss| Assemble["Assemble<br/>RVGD retrieval, Steiner-tree join planning,<br/>seed Governed context + licensed table scope"]
    Assemble --> AgentCore{"agent_core: create_agent tool loop<br/>GovernanceMiddleware.wrap_tool_call gates every call"}
    Bind --> RefuseGate{"Refuse-gate<br/>negative example match?"}

    RefuseGate -->|match| Refuse["Refuse / clarify<br/>fail closed"]
    RefuseGate -->|no match| AgentCore
    AgentCore -->|search_corpus / inspect_schema / sample_rows| AgentCore
    AgentCore -->|run_query blocked, attempts remain| AgentCore
    AgentCore -->|run_query blocked: attempt cap or recursion_limit exhausted| Refuse
    AgentCore -->|run_query passes five guardrails| Execute["Gateway.execute()<br/>read-only, forced LIMIT/timeout, audit"]
    Reexecute --> Execute
    Execute --> Result["Rows + provenance"]
    Result --> Stamp["Finalize<br/>two-axis reliability stamp"]
    Stamp --> User["Answer to user"]
    Execute --> Audit["Audit/replay log"]
    Stamp --> CacheWrite["Cache successful SQL text<br/>TTL 15 minutes"]
```

## Ask-question sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Rails as Server rails (StateGraph)
    participant Corpus as Server-visible Corpus
    participant Agent as create_agent (agent_core)
    participant MW as GovernanceMiddleware
    participant Gateway as Governed Gateway
    participant DB as Relational DB

    User->>Rails: Ask question with identity
    Rails->>Corpus: Load Facts + Inference context; match negative examples
    alt refuse-gate match
        Rails-->>User: Refusal or clarifying question
    else no match
        Rails->>Rails: Assemble: RVGD retrieval, Steiner join plan,<br/>seed Governed context + licensed table scope
        Rails->>Agent: agent_core(Governed context, licensed scope)
        loop bounded by recursion_limit
            Agent->>MW: call a governed tool
            MW->>MW: normalize call, run L1-L5 guardrails<br/>over current licensed set, write ledger entry
            alt search_corpus / inspect_schema / sample_rows
                MW-->>Agent: expand licensed set + result
            else run_query blocked (guardrail veto or attempt cap)
                MW-->>Agent: ToolMessage: blocked, retry or stop
            else run_query passes guardrails
                MW->>Gateway: execute SQL as user
                Gateway->>DB: Read-only query under RLS
                DB-->>Gateway: Rows
                Gateway-->>MW: QueryResult + audit metadata
                MW-->>Agent: rows + ledger entry
            end
        end
        Agent-->>Rails: final rows + governance ledger, or budget exhausted
        Rails-->>User: Answer + provenance + reliability stamp
    end
```

## SQL semantic-cache sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Server
    participant Cache as SQL cache
    participant Pipeline as Agent core (assemble + tool loop)
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
        Server->>Pipeline: assemble Governed context, run agent tool loop
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
    SQL["Agent-written SQL<br/>(run_query tool call)"] --> Syntax{"1. syntax<br/>valid SQL parse?"}
    Syntax -->|fail| Veto["veto / fail closed<br/>(wrap_tool_call blocks the call)"]
    Syntax -->|pass| Policy{"2. policy blacklist<br/>read-only only?"}
    Policy -->|fail| Veto
    Policy -->|pass| AST{"3. AST column allowlist<br/>known, non-excluded,<br/>suspect policy respected?"}
    AST -->|fail| Veto
    AST -->|pass| Semantics{"4. term semantics<br/>columns match bound terms?"}
    Semantics -->|fail| Veto
    Semantics -->|pass| Cost{"5. cost / EXPLAIN<br/>under budget?"}
    Cost -->|fail| Veto
    Cost -->|pass| Pass["pass; run_query executes via Gateway.execute()"]
```

