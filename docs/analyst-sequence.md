# Analyst — agentic sequence

The serve-time path a natural-language question travels to become a grounded,
governed, auditable answer. Every branch fails **closed**; the curated corpus is
the only source of truth. Source: [`analyst/agent.py`](../src/governed_bi/analyst/agent.py)
(the `build_serve_rails` outer graph) and [`gateway/guardrails.py`](../src/governed_bi/gateway/guardrails.py)
(the `check` stack).

**Participants → code**

| Lifeline | Where |
|---|---|
| Serve rails | `analyst/agent.py` · `build_serve_rails` StateGraph (`ingest → refuse_gate → prepare → cache → assemble → agent_core → narrate`) |
| Working memory | `memory/store.py` · `WorkingMemory` |
| Refuse-gate | `analyst/agent.py` · `refuse_gate` (curated `NegativeExampleAsset`s) |
| SQL cache | `analyst/governance.py` · `_try_cache_hit` |
| RVGD retrieval | `retrieval/rvgd.py` · `retrieve`; `retrieval/schema_router.py` |
| Graph planner | `graph/planner.py` · `detect_missing_join_path`, `plan_joins` |
| Corpus | `corpus/…` · `Corpus.for_analyst()` view |
| Agent core | `analyst/agent.py` · `build_agent_core` (LLM + tools) |
| Governance middleware | `analyst/middleware.py` · `GovernanceMiddleware` → `check` (L1–L5) |
| Read-only gateway | `gateway/…` · `Gateway` + connector (`PRAGMA query_only` / read-only role) |
| Narrator | `analyst/narrate.py` · `AnswerNarrator` (assurance enum in `analyst/answer.py`) |

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant R as Serve rails
    participant WM as Working memory
    participant RG as Refuse-gate
    participant SC as SQL cache
    participant RV as RVGD retrieval
    participant GP as Graph planner
    participant C as Corpus (for_analyst)
    participant AG as Agent core (LLM)
    participant MW as Governance middleware
    participant DB as Read-only gateway
    participant NR as Narrator

    U->>R: ask(question, identity)
    activate R

    Note over R: ingest
    R->>R: route_intent + bind_terms (no working-memory read yet)

    Note over R,RG: refuse-gate — before any LLM call
    R->>RG: match(question, negative_examples)
    alt matches a curated negative example
        RG-->>R: refuse
        R-->>U: REFUSE — out of scope (0 tokens, + clarification hint)
    else in scope
        RG-->>R: ok

        Note over R,SC: semantic cache
        R->>SC: lookup(question)
        alt cache hit
            SC-->>R: cached SQL + licensed tables
            R->>R: re-guardrail via check() — L1..L5 (freshness)
            R->>DB: execute(sql)
            DB-->>R: rows
            R->>NR: narrate(rows)
            NR-->>R: answer text
            R-->>U: ANSWER — cached, re-verified
        else cache miss
            Note over R,C: assemble — retrieve + license
            R->>WM: history(session_id)
            WM-->>R: prior turns (for follow-up resolution)
            opt corpus spans more than one schema
                R->>RV: shortlist_schemas(question)
                RV-->>R: candidate schemas
                R->>RV: expand via curated cross-schema joins
                RV-->>R: routed schemas (+ bridge tables)
            end
            R->>RV: retrieve(question)
            Note right of RV: BM25 (+ optional vector) → RRF fuse<br/>per-type budgets → ground expansion<br/>(over the for_analyst view only)
            RV->>C: read assets
            C-->>RV: tables / columns / terms / metrics / few-shots
            RV-->>R: RetrievalResult (typed ids)

            R->>GP: detect_missing_join_path(retrieved tables)
            alt tables span schemas with no curated join
                GP-->>R: missing edge
                R-->>U: REFUSE — missing join (never invents one)
            else joinable
                GP-->>R: none
                R->>GP: plan_joins(tables) — approx. Steiner tree over FK graph
                GP-->>R: join plan + licensed scope (the L4 allow-set)
                R->>C: assemble_context(retrieval, licensed)
                C-->>R: grounded prompt (tables, cols, terms, metrics, few-shots, rules, caveats)

                Note over R,DB: governed agent core
                R->>AG: run(question, context, seed licensed)
                activate AG
                Note over AG,MW: run_query capped at RUN_QUERY_CAP (3) attempts —<br/>40-step recursion budget over the whole turn
                loop until final answer or step budget
                    AG->>AG: reason (LLM proposes a tool call)
                    alt tool = search_corpus
                        AG->>RV: retrieve(query)
                        RV-->>AG: more governed assets
                    else tool = inspect_schema
                        AG->>C: columns(table_id)
                        C-->>AG: licensed columns (+ licenses the table)
                    else tool = run_query / sample_rows
                        AG->>MW: tool_call(sql)
                        activate MW
                        MW->>MW: L1 syntax · L2 read-only · L3 columns · L4 tables (licensed) · L5 cartesian
                        alt hard stop — write / forbidden statement
                            MW-->>R: GovernanceHardStop(entry, ledger)
                            R-->>U: REFUSE — guardrail (fail closed)
                        else soft block — off-scope column / table / cartesian
                            MW-->>AG: BLOCKED(layer, reason) → repair and retry
                        else all layers pass
                            MW->>DB: execute(sql)
                            alt execution error
                                DB-->>MW: error
                                MW-->>AG: error(verdict) → repair and retry
                            else rows returned
                                DB-->>MW: rows (capped, read-only)
                                MW-->>AG: rows (+ ledger: pass)
                            end
                        end
                        deactivate MW
                    else tool = ask_user (HITL, optional)
                        AG-->>R: interrupt(clarification request)
                        R-->>U: clarification needed
                        U->>R: response
                        R->>AG: resume(response)
                    end
                end
                deactivate AG

                alt step budget exhausted
                    R-->>U: REFUSE — exhausted (ledger + attempts preserved)
                else no run_query ever passed
                    R-->>U: REFUSE — no coverage / guardrail
                else a run_query passed
                    R->>R: extract_final_sql → sql, tables_used
                    Note over R,NR: narrate + reliability stamp
                    R->>NR: narrate(verified rows)
                    NR-->>R: answer text (grounded in returned rows)
                    R->>GP: plan_joins(tables_used) → reliability
                    GP-->>R: grounded | heuristic | unverified
                    R-->>U: ANSWER + stamp + safety clearance + governance ledger
                end
            end
        end
    end
    deactivate R
```

## Notes

- **Fail-closed everywhere.** Refuse-gate, missing-join, a guardrail hard-stop,
  budget/attempt exhaustion, and no-coverage all end in a `REFUSE` — never a
  best-guess answer. A gateway execution error is returned to the agent as a tool
  message to repair (not a terminal); `refused_by="execution"` is only a defensive
  guard for an already-passing query that can no longer be replayed.
- **The repair loop is the non-linear core.** A *soft* guardrail block (an
  off-scope column/table or an accidental cartesian) is returned to the agent as
  a tool message so it can re-plan and retry within the step budget; a *hard*
  block (a write or forbidden statement) stops the turn immediately.
- **The middleware is the boundary the agent cannot self-authorize past.** Every
  data-touching tool call is re-parsed and re-checked (L1–L5) against the licensed
  scope and the read-only gateway — the LLM never talks to the database directly.
- **Auditability.** Each governed query appends one ledger entry (pass or block);
  the final answer carries the full ledger, a reliability stamp, and an explicit
  safety clearance.

Companion: [curator-sequence.md](curator-sequence.md) — how the corpus this path
reads is built.
