# 0009: The browsing, filtering and relationship API

- **Status:** **Reversed** (P4 of the pivot, [ADR 0017](0017-a-served-chat-app-over-a-layer-the-user-brings.md)).
  Nothing below is built. The browse routes, the graph routes, the audit routes and every page
  that read them are deleted.
- **Deciders:** project owner + design session (2026-08-04; reversed 2026-10).
- **Scope:** the read surface the UI browsed the semantic layer with.

## What was decided

On 2026-08-04 the UI browsed about 14 000 assets over 57 schemas through three flat dumps:
every asset (2.25 MB), every table with its columns inline (937 KB), and an unbounded join graph
that the browser could not lay out. The decision replaced them with a scoped read surface:

- **one filter contract**, `/corpus/fields` and `/corpus/rows`, whose columns were derived from
  the asset dataclasses and the asset register so a new field became filterable with no API or
  UI change, and which returned a filter it could not apply rather than ignoring it;
- **bounded graphs**, `/graph` (tables and joins) and `/knowledge-graph` (every asset kind), over
  one scope contract (`schema`, `focus`, `radius`, `kinds`, `node_budget`) that always said what
  it cut, grew the budget from the best-connected seed, and drew a join leaving the scope as a
  destination rather than a silence;
- **a lean catalog**, `/schema/summary`, with one table's detail fetched on demand from
  `/schema/{table_id}`, and `/columns/{column_id}/related` for the column panel;
- **capability flags as observations**: `can_scope` true because the scoped routes existed,
  `can_search` false because no search was built;
- **filtering over the loaded corpus**, not the database, so the browser and retrieval saw the
  same assets.

An amendment the same day added the audit routes (`/audit/turns`, the per-turn trace and
`/audit/corpus`) to "the sufficient set", and `npm run check:api` was written to validate every
route against the client's schemas on a live engine.

## What is true instead

The served app has one page, the chat page ([ADR 0019](0019-one-plain-chat-page.md)), and the
semantic layer is shown by a separate viewer ([ADR 0020](0020-ossie-export-and-a-separate-viewer.md)).
The custom app mounts four reads: `/livez`, `/readyz`, `/capabilities` and
`/clarifications/pending`. Everything else the page needs is LangGraph's own thread API.
`/capabilities` no longer reports `can_scope` or `can_search`. `docs/openapi.json` declares
exactly those four routes, and `tests/api/test_the_spec_matches_the_server.py` checks every
body against it.

The chat page used to read `/corpus/assets` too, only to pick four few-shot questions as
starters on an empty conversation. The starters are deleted with the route; a later milestone
brings them back from a route built for them.

## What was learned

- **A surface sized for browsing is a second product.** The browse API grew its own contract,
  its own capability flags, its own spec, a live-engine contract check and three UI pages, and
  the one question the app exists to answer never used any of it. The work went into the
  semantic layer's presentation, which the user brings and can view elsewhere.
- **"Says what it cut" was the right rule and still was not enough.** The graph budget reported
  truncation honestly, and the client then re-scoped the payload and overwrote the report until
  the echo was made complete. An honesty property of a payload holds only if every reader keeps
  it, so a bounded view needs a test on the client as well as the server.
- **A flag described as an observation drifted into a literal.** `can_scope` was hardcoded
  `True`; it would have kept reporting the scoped routes after they were deleted. A capability
  flag has to be computed from the thing it reports, or it is a comment on the wire.
- **The audit routes belonged to a reader who did not exist.** They returned every thread's SQL
  to any caller on the port, and the only readers were the developer's own pages. Thread state
  remains readable through the platform's API, which is the one store.
