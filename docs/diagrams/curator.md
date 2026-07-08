# Curator Diagrams

The curator package is currently a design scaffold. It is the offline build-side
harness that writes the corpus.

## Curator build-loop data flow

```mermaid
flowchart TD
    Inputs["Per-DB inputs<br/>live catalog/data + train seed queries"] --> Profile["Profile facts<br/>programmatic table/column facts"]
    Profile --> Propose["Proposer<br/>descriptions, joins, terms,<br/>metrics, rules, skills, caveats"]
    Propose --> Adversary{"Adversary refutes<br/>model-authored claims"}
    Adversary -->|reject| Propose
    Adversary -->|revise| Propose
    Adversary -->|accept| Draft["Draft corpus<br/>proposed to draft"]
    Draft --> SelfEval["Self-eval on train questions<br/>run server pipeline; measure EX"]
    SelfEval --> Plateau{"Train EX plateau<br/>or cap hit?"}
    Plateau -->|no| Diagnose["Diagnose failures<br/>patch assets/skills"]
    Diagnose --> Propose
    Plateau -->|yes| Validate["validate_corpus()<br/>CI reference integrity"]
    Validate --> Green{"CI green?"}
    Green -->|no| Diagnose
    Green -->|yes| Emit["Emit corpus/&lt;db&gt;/"]
    Emit --> Mode{"Environment"}
    Mode -->|dev / BIRD| AutoAccept["Auto-accept draft"]
    Mode -->|prod / enterprise| PullRequest["Open PR for human certification"]
```

## Asset lifecycle state machine

The three boxes are the persisted `provenance.status` values. The adversary's
verdict (accept / revise / reject) drives the transitions out of `proposed`; it
is not itself a stored status.

```mermaid
stateDiagram-v2
    state "proposed" as Proposed
    state "draft" as Draft
    state "certified" as Certified

    [*] --> Proposed: proposer emits asset
    Proposed --> Draft: adversary verdict = accept
    Proposed --> Proposed: adversary verdict = revise (proposer patches, re-proposes)
    Proposed --> [*]: adversary verdict = reject (dropped, never stored)
    Draft --> Certified: human certifies (prod only, D6)
    Draft --> [*]: dev / BIRD — draft is the accepted terminal (no cert)
    Certified --> Proposed: drift repair proposes an update
```

## Proposer and adversary sequence

```mermaid
sequenceDiagram
    autonumber
    participant Gateway as Gateway catalog/data
    participant Proposer
    participant Adversary
    participant Corpus as Draft corpus
    participant Server as Server self-eval
    participant CI as Corpus CI

    Gateway->>Proposer: catalog, samples, train seed queries
    Proposer->>Proposer: infer descriptions, joins, terms, metrics, skills
    Proposer->>Adversary: proposed inference or skill asset
    Adversary->>Gateway: run falsifying probes
    alt accept
        Adversary-->>Corpus: mark draft with audit verdict
    else revise
        Adversary-->>Proposer: reasons and required changes
        Proposer->>Adversary: revised asset
    else reject
        Adversary-->>Proposer: reject with reasons
    end
    Corpus->>Server: self-eval on train questions
    Server-->>Proposer: failures and diagnostics
    Proposer->>Corpus: patches after adversary pass
    Corpus->>CI: validate references and IDs
    CI-->>Corpus: green or findings
```

## Drift-repair feedback loop

```mermaid
flowchart LR
    Server["Serve-side answer runs"] --> Signals["Audit logs, corrections,<br/>failures, low-stamp answers"]
    Signals --> Harvest["Harvest signals<br/>correction memory"]
    Harvest --> CuratorInput["Curator input queue"]
    CuratorInput --> Proposer["Proposer patches assets/skills"]
    Proposer --> Adversary["Adversary re-checks"]
    Adversary --> Corpus["Updated corpus proposal"]
    Corpus --> CI["CI + PR / auto-accept"]
    CI --> Server
```

