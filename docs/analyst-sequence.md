# Analyst — agentic sequence

The serve-time path a natural-language question travels to become a grounded,
governed, auditable answer. Every branch fails **closed**; the curated corpus is
the only source of truth. Broken into an overview plus one small diagram per
stage. Source: [`analyst/agent.py`](../src/governed_bi/analyst/agent.py)
(`build_serve_rails`) and [`gateway/guardrails.py`](../src/governed_bi/gateway/guardrails.py)
(`check`).

**Participants → code**

| Lifeline | Where |
|---|---|
| Serve rails | `analyst/agent.py` · `build_serve_rails` StateGraph |
| Working memory | `memory/store.py` · `WorkingMemory` |
| Refuse-gate | `analyst/agent.py` · `refuse_gate` (curated `NegativeExampleAsset`s) |
| SQL cache | `analyst/governance.py` · `_try_cache_hit` |
| RVGD retrieval | `retrieval/rvgd.py` · `retrieve`; `retrieval/schema_router.py` |
| Graph planner | `graph/planner.py` · `detect_missing_join_path`, `plan_joins` |
| Corpus | `corpus/loader.py` · `Corpus.for_analyst()` view |
| Agent core | `analyst/agent.py` · `build_agent_core` (LLM + tools) |
| Governance middleware | `analyst/middleware.py` · `GovernanceMiddleware` → `check` (L1–L5) |
| Read-only gateway | `gateway/…` · `Gateway` + connector (read-only) |
| Narrator | `analyst/narrate.py` · `AnswerNarrator` (assurance enum in `answer.py`) |

---

## Overview

The outer rails as a map — nodes and the branch each can take.

```mermaid
flowchart TD
    Q([question]) --> ING[ingest<br/>route + bind terms]
    ING --> RG{refuse-gate}
    RG -->|matches negative| XO([REFUSE · out of scope])
    RG -->|in scope| CA{semantic cache}
    CA -->|hit, re-verified| NAR[narrate + stamp]
    CA -->|miss| ASM{assemble<br/>RVGD + license}
    ASM -->|no curated join| XE([REFUSE · missing join])
    ASM -->|licensed| AG{governed agent core}
    AG -->|guardrail hard-stop| XG([REFUSE · guardrail])
    AG -->|exhausted / no coverage| XN([REFUSE · no coverage])
    AG -->|a query passes| NAR
    NAR --> ANS([governed answer + ledger])
```

Each stage below zooms into one of these nodes.

---

## 1 · Gating — ingest → refuse-gate → cache

Two ways to answer (or refuse) before any retrieval or generation.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant R as Serve rails
    participant RG as Refuse-gate
    participant SC as SQL cache
    participant DB as Read-only gateway

    U->>R: ask(question, identity)
    Note over R: ingest — route_intent + bind_terms (no memory read yet)
    R->>RG: match(question, negative examples)
    alt matches a curated negative example
        RG-->>R: refuse
        R-->>U: REFUSE — out of scope (0 tokens, + hint)
    else in scope
        RG-->>R: ok
        R->>SC: lookup(question)
        alt cache hit
            SC-->>R: cached SQL + licensed tables
            R->>R: re-guardrail via check() L1–L5 (freshness)
            R->>DB: execute(sql)
            DB-->>R: rows
            R-->>U: ANSWER — cached, re-verified
        else cache miss
            SC-->>R: miss → go to Assemble
        end
    end
```

---

## 2 · Assemble — retrieve + license

Entered only on a cache miss. Pulls the governed assets and computes the scope
the query is allowed to touch.

```mermaid
sequenceDiagram
    autonumber
    participant R as Serve rails
    participant WM as Working memory
    participant RV as RVGD retrieval
    participant GP as Graph planner
    participant C as Corpus (for_analyst)

    R->>WM: history(session_id)
    WM-->>R: prior turns (follow-up resolution)
    opt corpus spans more than one schema
        R->>RV: shortlist_schemas(question)
        RV-->>R: candidate schemas
        R->>RV: expand via curated cross-schema joins
        RV-->>R: routed schemas (+ bridges)
    end
    R->>RV: retrieve(question)
    Note right of RV: BM25 (+ optional vector) → RRF fuse<br/>per-type budgets → ground expansion
    RV->>C: read assets (for_analyst view)
    C-->>RV: tables / columns / terms / metrics / few-shots
    RV-->>R: RetrievalResult
    R->>GP: detect_missing_join_path(tables)
    alt tables span schemas with no curated join
        GP-->>R: missing edge
        R-->>R: REFUSE — missing join (never invents one)
    else joinable
        GP-->>R: none
        R->>GP: plan_joins(tables) — Steiner tree over FK graph
        GP-->>R: join plan + licensed scope (L4 allow-set)
        R->>C: assemble_context(retrieval, licensed)
        C-->>R: grounded prompt → hand to Agent core
    end
```

---

## 3 · Governed agent core — the guarded tool loop

The non-linear heart: the LLM proposes tool calls; the middleware re-checks every
data-touching one (L1–L5); a **soft** block is repaired-and-retried, a **hard**
block stops the turn.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant R as Serve rails
    participant AG as Agent core (LLM)
    participant MW as Governance middleware
    participant RV as RVGD retrieval
    participant C as Corpus
    participant DB as Read-only gateway

    R->>AG: run(question, grounded context, seed licensed)
    Note over AG,MW: run_query capped at RUN_QUERY_CAP (3) —<br/>40-step recursion budget over the turn
    loop until final answer or budget
        AG->>AG: reason (propose a tool call)
        alt tool = search_corpus
            AG->>RV: retrieve(query)
            RV-->>AG: more governed assets
        else tool = inspect_schema
            AG->>C: columns(table_id)
            C-->>AG: licensed columns (+ licenses the table)
        else tool = run_query / sample_rows
            AG->>MW: tool_call(sql)
            MW->>MW: L1 syntax · L2 read-only · L3 columns · L4 tables · L5 cartesian
            alt hard stop (write / forbidden statement)
                MW-->>R: GovernanceHardStop
                R-->>U: REFUSE — guardrail (fail closed)
            else soft block (off-scope col / table / cartesian)
                MW-->>AG: BLOCKED(reason) → repair and retry
            else layers pass
                MW->>DB: execute(sql)
                alt execution error
                    DB-->>MW: error
                    MW-->>AG: error → repair and retry
                else rows returned
                    DB-->>MW: rows (capped, read-only)
                    MW-->>AG: rows (+ ledger: pass)
                end
            end
        else tool = ask_user (HITL, optional)
            AG-->>U: interrupt(clarification)
            U-->>AG: response (resume)
        end
    end
    alt a run_query passed
        AG-->>R: final SQL + tables_used
    else no query passed
        AG-->>R: exhausted / no coverage → REFUSE
    end
```

---

## 4 · Finalize — narrate + reliability stamp

Only on a passing query. The answer is grounded in the rows that query returned.

```mermaid
sequenceDiagram
    autonumber
    participant R as Serve rails
    participant NR as Narrator
    participant GP as Graph planner
    actor U as User

    R->>R: extract_final_sql → sql, tables_used
    R->>NR: narrate(verified rows)
    NR-->>R: answer text (grounded in returned rows)
    R->>GP: plan_joins(tables_used) → reliability
    GP-->>R: grounded | heuristic | unverified
    R-->>U: ANSWER + stamp + safety clearance + governance ledger
```

---

## Refuse terminals (all fail-closed)

| Terminal | Raised where | Trigger |
|---|---|---|
| out of scope | gating | question matches a curated negative example (before any LLM) |
| missing join | assemble | retrieved tables span schemas with no curated join |
| guardrail | agent core | a hard stop — a write / forbidden statement (`GovernanceHardStop`) |
| exhausted / no coverage | agent core | step/attempt budget hit, or no `run_query` ever passed |

A **soft** guardrail block (off-scope column/table, accidental cartesian) and a
gateway **execution error** are *not* terminals — they return to the agent as a
tool message to repair and retry. (`refused_by="execution"` is only a defensive
guard for an already-passing query that can no longer be replayed.)

Companion: [curator-sequence.md](curator-sequence.md) — how the corpus this path
reads is built.
