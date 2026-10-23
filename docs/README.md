# Documentation

What is in `docs/`.

## Using the app

- [Usage](usage.md): install, configure, and run the server, the page, the evaluation, and the exporter.
- [Corpus format](corpus-format.md): the YAML layout of the semantic layer, and the Ossie export.
- [OpenAPI](openapi.json): the custom HTTP routes the page calls besides chat.

## How it works

- [Architecture](architecture.md): how a question becomes an answer.
- [Failure modes](failure-modes.md): how answers go wrong.
- [Glossary](glossary.md): the words this repository uses.
- [Open work](open-work.md): what is unfinished.

## Measurement

- [Measurement](measurement.md): how to run an evaluation and how to read the result.

## Decisions

The records in [adr/](adr/) are point-in-time decisions. The decision an ADR records is not edited to match later reality. A later ADR, or the code, wins, and the reasoning stays even when it led to the wrong answer.

What is edited is everything around the decision. A superseded ADR is rewritten as a reversal record: what was decided, what is true instead, and what was learned. It does not keep coordinates into files that no longer exist, and its Status line does not describe a build in the present tense after that build was deleted.

- [0001](adr/0001-langgraph-server-chat-runtime.md): chat goes through LangGraph Server. That transport still holds. The graph this record describes does not.
- [0002](adr/0002-governed-agentic-serve-runtime.md): the model proposes SQL, and the checks run in tool code.
- [0003](adr/0003-governed-notes-tri-modal-retrieval.md): reversed. There is no note asset.
- [0004](adr/0004-local-first-conversation-run-logging.md): superseded in part by 0014. A conversation is the checkpointer.
- [0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md): the semantic layer, and how a question finds the assets it needs.
- [0006](adr/0006-execution-time-governance.md): the six checks that run before a query.
- [0007](adr/0007-http-surface-and-the-ui-contract.md): the HTTP routes, and what the page may show. There is no trust score.
- [0008](adr/0008-identifiers-end-to-end.md): identifiers from the corpus through to the SQL.
- [0009](adr/0009-browsing-and-filtering-api.md): reversed. The only page is chat.
- [0010](adr/0010-live-stage-events.md): the status line while a turn runs.
- [0011](adr/0011-two-model-split-and-facet-query-rewriting.md): a chat model and a utility model, and a rewritten query for each retrieval channel.
- [0012](adr/0012-access-seam-principal-and-authorization.md): reversed. Who may read which rows is a database role.
- [0013](adr/0013-the-declared-abstention-policy.md): a named policy that can decline before the model spends a query. It ships off.
- [0014](adr/0014-one-conversation-store.md): one conversation store, a SQLite checkpointer.
- [0015](adr/0015-the-return-path.md): reversed. The app does not collect notes on answers.
- [0016](adr/0016-gating-the-corpus-repository.md): reversed. The corpus belongs to whoever brings it.
- [0017](adr/0017-a-served-chat-app-over-a-layer-the-user-brings.md): what the project is. Accepted.
- [0018](adr/0018-every-answer-states-its-assumptions.md): every answer states its assumptions, and the server checks them. Accepted.
- [0019](adr/0019-one-plain-chat-page.md): the one chat page. Accepted.
- [0020](adr/0020-ossie-export-and-a-separate-viewer.md): Ossie as an export, and a viewer in another repository. Accepted.
- [Design decisions](design-decisions.md): a short pointer into these records.

## Dated reviews

The current work follows the pivot milestones. The earlier plans stay as the record of what they decided.

- [2026-10 pivot](reviews/2026-10-pivot.md): why the project is a chat app over a semantic layer the user brings.
- [2026-10 pivot milestones](reviews/2026-10-pivot-milestones.md): the work that carries out that decision.
- [2026-10 trim plan](reviews/2026-10-trim-plan.md): an earlier cut. The pivot supersedes it.
- [2026-10 trim milestones](reviews/2026-10-trim-milestones.md): the steps of that cut.
- [2026-10 Ossie plan](reviews/2026-10-ossie-plan.md): an earlier plan to store the corpus as Ossie. ADR 0020 supersedes it.
- [2026-09 improvement plan](reviews/2026-09-improvement-plan.md): the September plan.
- [2026-09 improvement milestones](reviews/2026-09-improvement-milestones.md): the steps of that plan.
- [2026-09 outside review](reviews/2026-09-outside-review.md): an outside read of the code with the comments and the docs removed.
