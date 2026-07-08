# Evaluation Diagrams

The eval package is currently a scaffold. The intended harness proves whether
the curator-built semantic layer improves execution accuracy and safety signals.

## Three-arm evaluation

```mermaid
flowchart LR
    HeldOut["Held-out test questions"] --> Arm1
    HeldOut --> Arm2
    HeldOut --> Arm3
    Train["Train split<br/>curator only"] --> Curator["Curator-built corpus"]

    subgraph Arms["Evaluation arms"]
        Arm1["Arm 1<br/>no semantic layer"]
        Arm2["Arm 2<br/>curator corpus"]
        Arm3["Arm 3<br/>gold corpus"]
    end

    Curator --> Arm2
    Gold["Gold oracle corpus"] --> Arm3
    Arm1 --> ServerRuns["Run server pipeline"]
    Arm2 --> ServerRuns
    Arm3 --> ServerRuns
    ServerRuns --> Gateway["Execute generated SQL"]
    Gateway --> EX["Execution accuracy"]
    ServerRuns --> Refuse["Refuse-gate metrics"]
    ServerRuns --> Decoy["Decoy-touch / governed-path metrics"]
    EX --> Scoreboard["Eval scoreboard + telemetry"]
    Refuse --> Scoreboard
    Decoy --> Scoreboard
    Scoreboard --> CuratorFeedback["Failure analysis<br/>train-only repair input"]
    CuratorFeedback --> Curator
```

## Refuse-gate evaluation

```mermaid
flowchart TD
    Answerable["Answerable held-out set"] --> ServerA["Server with refuse-gate"]
    Unanswerable["Unanswerable held-out set<br/>cross-DB + removed coverage + hand-built"] --> ServerU["Server with refuse-gate"]
    ServerA --> FalseRefusal["false_refusal_rate<br/>answerable questions refused"]
    ServerU --> RefusalAccuracy["refusal_accuracy<br/>unanswerable questions refused"]
    FalseRefusal --> RefuseResult["RefuseGateResult"]
    RefusalAccuracy --> RefuseResult
```

## Metrics and feedback

```mermaid
flowchart LR
    Runs["Server runs"] --> Logs["SQL, retrieved assets,<br/>guardrail/refusal outcomes"]
    Logs --> EX["EX score"]
    Logs --> Decoy["decoy_touch_rate"]
    Logs --> Governed["governed_path_adherence"]
    Logs --> Cost["cost logging"]
    Logs --> Refusal["refusal metrics"]

    EX --> Scoreboard["Scoreboard"]
    Decoy --> Scoreboard
    Governed --> Scoreboard
    Cost --> Scoreboard
    Refusal --> Scoreboard
    Scoreboard --> Diagnosis["Failure diagnosis"]
    Diagnosis --> Curator["Curator repair loop<br/>train-only"]
```

