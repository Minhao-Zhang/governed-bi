# Curator — agentic sequence

The offline path that turns a raw (possibly obfuscated) database into a governed,
auditable corpus — the source of truth the Analyst later reads. It runs as an
**eval ladder**: a deterministic `baseline`, then an agentic `curated` (Phase A),
then a human-enriched `curated_sme` (Phase B). Source:
[`curator/pipeline.py`](../src/governed_bi/curator/pipeline.py) and the deep-agent
harness [`curator/deep_agent.py`](../src/governed_bi/curator/deep_agent.py).

**Participants → code**

| Lifeline | Where |
|---|---|
| Curator pipeline | `curator/pipeline.py` · `build_baseline_corpus` / `build_curated_corpus` / `build_curated_corpus_with_sme` |
| Profiler | `curator/profile.py` · `profile_database` |
| Seeder | `curator/seed.py` · `seed_from_train_sql` |
| AssetBag | `curator/asset_bag.py` · in-memory typed assets + validated writes |
| Deep agent | `curator/deep_agent.py` · `create_deep_agent` (max-autonomy LLM) |
| Read-only gateway | `gateway/…` · `Gateway` (probes run read-only) |
| clarifications.jsonl | `deepagents` `FilesystemBackend` · the open-question ledger |
| Validator | `corpus/validate.py` · `validate_corpus` (deterministic gate) |
| Adversary | `curator/adversary.py` · structural `review` (refute-first LLM adversary deferred) |
| SME | `curator/clarifications.py` · `Responder` (human / stand-in) |
| Corpus (on disk) | `corpus/<schema>/…` typed YAML + run manifest |

```mermaid
sequenceDiagram
    autonumber
    actor OP as Operator / eval
    participant P as Curator pipeline
    participant PR as Profiler
    participant SD as Seeder (train SQL)
    participant BAG as AssetBag
    participant AG as Deep agent (LLM)
    participant DB as Read-only gateway
    participant FS as clarifications.jsonl
    participant VAL as Validator
    participant ADV as Adversary
    participant SME as SME
    participant DISK as Corpus (on disk)

    Note over OP,DISK: Baseline — deterministic, no LLM (the eval floor)
    OP->>P: build_baseline_corpus(schema)
    P->>PR: profile_database
    PR->>DB: introspect (names, types, samples, PK)
    DB-->>PR: catalog facts
    PR-->>P: TableAssets
    P->>BAG: seed facts + derive FK candidates by naming convention
    BAG-->>P: baseline assets
    P->>DISK: write baseline corpus

    Note over OP,DISK: Phase A — curated (profile → seed → explore → validate → write)
    OP->>P: build_curated_corpus(schema, train, model)
    activate P
    P->>PR: profile_database
    PR-->>P: facts
    P->>BAG: seed facts (tables + columns)
    P->>SD: seed_from_train_sql(train pairs)
    SD-->>P: SeedBundle (join / metric / term / few-shot candidates)
    P->>BAG: apply seed candidates
    P->>BAG: mark decoy / suspect columns absent from gold SQL

    P->>AG: build agent (grounded tools + PHASE_A_PROMPT + train batch)
    P->>AG: invoke — explore every pair
    activate AG
    loop reasoning steps (plan → act)
        AG->>BAG: read_corpus(table?, kind?)
        BAG-->>AG: facts + inference written so far
        AG->>DB: run_probe_query(SELECT) — confirm / falsify a claim
        DB-->>AG: rows (read-only, truncated)
        alt confident from the data
            AG->>BAG: upsert_join / upsert_metric / upsert_term / upsert_few_shot / annotate_table / annotate_column
            BAG->>BAG: validate binding resolves to a real asset
            alt binding invalid
                BAG-->>AG: error → fix and retry
            else accepted
                BAG-->>AG: written
            end
        else gap needs domain knowledge
            AG->>FS: append clarification (open question)
            FS-->>AG: recorded
        end
    end
    deactivate AG

    P->>BAG: repair_references() (deterministic danglers pre-pass)
    P->>VAL: validate_corpus(assets)
    alt findings (dangling ref / bad binding / ambiguity)
        VAL-->>P: findings
        P->>AG: one fix pass — re-invoke to repair
        AG->>BAG: corrective upserts / annotations
        P->>VAL: re-validate
        VAL-->>P: remaining findings → validate_findings.jsonl
    end
    P->>ADV: structural review signal (validate_corpus wrapper)
    Note right of ADV: refute-first LLM adversary is deferred (a stub) — structural<br/>review plus the deterministic validate run are the signal, not a hard gate
    ADV-->>P: signal
    P->>DISK: write curated corpus + manifest (findings recorded, not blocking)
    deactivate P

    Note over OP,DISK: Phase B — +SME (answer ledger → ingest → validate → write)
    OP->>P: build_curated_corpus_with_sme(schema, model)
    activate P
    P->>FS: load clarifications (open from Phase A)
    FS-->>P: open questions
    P->>SME: answer(open questions)
    SME-->>P: answers (domain truth profiling can't infer)
    P->>FS: write answered ledger
    P->>AG: fresh agent (PHASE_B_PROMPT — ingest answers)
    activate AG
    loop each answered clarification
        AG->>BAG: upsert / annotate (certified, answered_by = SME)
        BAG-->>AG: written
    end
    deactivate AG
    P->>VAL: validate_corpus (fix pass + record findings)
    VAL-->>P: findings recorded
    P->>DISK: write curated_sme corpus (regardless of findings)
    deactivate P
```

## Notes

- **Three arms, increasing trust.** `baseline` is DB-derivable facts only (no LLM);
  `curated` adds the agent's inference grounded in train SQL + live probes;
  `curated_sme` folds in human answers to the questions the agent couldn't resolve.
- **The agent is grounded, not free.** It reads the live corpus, probes the
  database **read-only**, and every write goes through a validated `upsert_*` /
  `annotate_*` tool that rejects a binding which does not resolve — the agent
  cannot author a dangling reference.
- **Uncertainty becomes a question, not a guess.** A gap the data can't settle is
  appended to `clarifications.jsonl` rather than invented, and is what Phase B's SME
  answers.
- **Validation is a fix pass + recorded signal, not a hard gate.** `validate_corpus`
  (references, bindings, `(schema, physical_name)` ambiguity) runs, triggers one
  agent fix pass, and writes any remaining findings to `validate_findings.jsonl`;
  `bag.repair_references()` also deterministically fixes danglers first. The corpus
  is then written **regardless** of remaining findings — in this greenfield harness
  they are a signal to act on, surfaced in the run manifest, not a write-blocking gate.

Companion: [analyst-sequence.md](analyst-sequence.md) — how this corpus is read at
serve time.
