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
| Browse / filter / relationship API (reversed) | [0009](adr/0009-browsing-and-filtering-api.md) |
| Live stage events | [0010](adr/0010-live-stage-events.md) |
| Two-model split and retrieval channel rewriting | [0011](adr/0011-two-model-split-and-facet-query-rewriting.md) |
| The access boundary: principal, authorization, Layer 6 split — **reversed** | [0012](adr/0012-access-seam-principal-and-authorization.md) |
| The declared abstention policy | [0013](adr/0013-the-declared-abstention-policy.md) |
| One conversation store | [0014](adr/0014-one-conversation-store.md) |
| The return path: reader feedback into the corpus | [0015](adr/0015-the-return-path.md) (reversed; the loop is deleted) |

Non-ADR judgements that still matter in code:

- **settings vs env.** Settings that affect results, and serve settings, are declared in
  `registry/settings.py`. Deployment-facing settings that operators can set today are
  primarily `GOVERNED_BI_*` environment variables (models, paths, timeouts,
  retries, embedder, and the access policy). The full table is
  [usage](usage.md#environment).
- **No trust score, and no two-axis finish either.** A single collapsed reliability
  number is refused (ADR 0002 / 0006), and no per-axis finish replaces it. What a turn
  carries is `outcome`, `guardrail_errors`, `terminal_reason` and the per-attempt
  ledger, each derived from something observed. `safety_clearance` and `semantic_assurance` are
  barred from **all** of `src/`, by two tests at different scopes:
  `tests/api/test_http_contract_answer_and_stream.py::test_the_api_never_synthesizes_a_reliability_field`
  walks every `.py` under `src/` for both names — with a `MIN_SOURCE_FILES = 50` floor, so a scan
  that reached the wrong root fails instead of passing vacuously — and
  `tests/serve/test_the_abstention_policy_is_declared.py::test_the_result_carries_no_trust_signal`
  AST-walks the abstention policy for eight forbidden names, the two above included. A line here
  claimed until 2026-08-25 that only the second existed and that "nothing scans the rest of the
  tree"; that was wrong, and it made `architecture.md` and `glossary.md` read as the overclaims.
  **That second scan names no file.** It used to walk `serve/nodes/abstain.py`, which held both the
  policy and the graph adapter at 470 lines; the policy is `serve/abstention.py` now and the
  adapter stayed. A scan hardcoded to one path would have gone on passing over whichever half a
  `confidence` field landed in, so `_sources_of_the_policy` asks `inspect` where the policy's own
  symbols are defined and walks every file that answers. Proven by planting a forbidden name in
  each half in turn and watching the test fail for both.
- **A node's read set is declared where a decision depends on it, and nowhere else.** Every
  serve node is `(state, config) -> dict` over a 47-channel `ServeState`, which names none of the
  channels it reads, writes or clears. Two nodes now project the state dict into a typed read-view
  first: `serve/abstention.py`'s `AbstentionInputs` (8 channels) and `serve/outcome.py`'s
  `OutcomeInputs` (7). The rules and the derivation take the view, so a test states two or three
  facts instead of assembling a turn, and a second reader of a channel is a structural failure
  rather than a thing nobody notices — which is what `measure/checks.py` reading
  `Outcome.clarification` as a evidence of "reached `finish`" cost.
  **The limit, because it is not a general rule.** `finish` reads 32 channels effectively (measured
  by deleting one key at a time from 30 turn shapes); only 7 are declared. The other 25 are the
  register's — `project` walks `RECORD_REGISTER` and asks for each field by name — so the
  projection's read set already exists in `registry/record.py`, and a dataclass restating it would
  be a second copy of a read set that already exists. A view is worth writing where a
  *decision* can have two readers who disagree, not wherever a channel is copied.
- **Source-text mutation anchors are gone.** They pinned a path plus a literal fragment, so a
  refactor had to move the anchor with the code. That is why `finish`'s derivation stayed beside
  the record projection. The boundary did not need the file boundary.
- **UI is in tree, out of the import graph.** The interactive chat UI is `ui/`. It shares the
  repository and nothing else: no module either way, and every payload it reads crosses the HTTP
  surface of ADR 0007.
