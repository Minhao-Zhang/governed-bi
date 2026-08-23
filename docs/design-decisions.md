# Design decisions

Binding decisions live as ADRs under [`adr/`](adr/). This page is only an index.
Do not re-author ADR content here.

| Topic | ADR |
|---|---|
| LangGraph Server + streaming chat | [0001](adr/0001-langgraph-server-chat-runtime.md) (superseded; transport holds) |
| Governed agentic serve runtime | [0002](adr/0002-governed-agentic-serve-runtime.md) (superseded; topology thesis holds) |
| Notes and tri-modal retrieval | [0003](adr/0003-governed-notes-tri-modal-retrieval.md) (reversed by 0005) |
| Local-first turn / run logging | [0004](adr/0004-local-first-conversation-run-logging.md) (superseded in part by 0014) |
| Memory layer and faceted retrieval | [0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) |
| Execution-time governance (the layer stack) | [0006](adr/0006-execution-time-governance.md) |
| HTTP surface and UI contract | [0007](adr/0007-http-surface-and-the-ui-contract.md) |
| Identifiers end to end | [0008](adr/0008-identifiers-end-to-end.md) |
| Browse / filter / relationship API | [0009](adr/0009-browsing-and-filtering-api.md) |
| Live stage events | [0010](adr/0010-live-stage-events.md) |
| Two-model split and facet rewriting | [0011](adr/0011-two-model-split-and-facet-query-rewriting.md) |
| The access seam: principal, authorization, Layer 6 split | [0012](adr/0012-access-seam-principal-and-authorization.md) |
| The declared abstention policy | [0013](adr/0013-the-declared-abstention-policy.md) |
| One conversation store | [0014](adr/0014-one-conversation-store.md) |
| The return path: reader feedback into the corpus | [0015](adr/0015-the-return-path.md) (accepted; steps 0-6 built, the pipeline and T4/T5 are not) |

Non-ADR judgements that still matter in code:

- **Knobs vs env.** Comparability and serve knobs are declared in
  `register/knobs.py`. Deployment-facing knobs that operators can set today are
  primarily `GOVERNED_BI_*` environment variables (models, paths, timeouts,
  retries, embedder, and the access policy). The full table is
  [usage](usage.md#environment).
- **No trust score, and no two-axis stamp either.** A single collapsed reliability
  number is refused (ADR 0002 / 0006), and no per-axis stamp replaces it. What a turn
  carries is `outcome`, `guardrail_errors`, `terminal_reason` and the per-attempt
  ledger, each derived from something observed. `safety_clearance` and
  `semantic_assurance` are barred from the abstention policy by a test, not from `src/` at large:
  `tests/serve/test_the_abstention_policy_is_declared.py::test_the_verdict_carries_no_trust_signal`
  AST-walks `serve/nodes/abstain.py` for eight forbidden names and nothing scans the rest of the
  tree. Corrected 2026-08-22.
- **UI is in tree, out of the import graph.** The interactive chat UI is `ui/`. It shares the
  repository and nothing else: no module either way, and every payload it reads crosses the HTTP
  surface of ADR 0007.
