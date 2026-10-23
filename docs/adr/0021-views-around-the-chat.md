# 0021: Three views around the chat

- **Status:** Accepted (2026-10-23).
- **Deciders:** project owner.
- **Scope:** the web UI beyond the chat column. Amends [0019](0019-one-plain-chat-page.md), whose
  style rules and answer layout stay, and the part of
  [0017](0017-a-served-chat-app-over-a-layer-the-user-brings.md) that kept nothing around the
  chat. The plan is [Track W](../reviews/2026-10-workspace-and-ossie-storage.md#track-w-the-pages).

## Context

0019 made the app one chat page with a sidebar of conversations. Using it, the owner could not see
how each past conversation ended without opening it, could not see the attempts that failed before
an answer, and had no way to look at the semantic layer from the app. The records needed for the
first two already exist in the conversation store; the third needs the layer in Ossie form
([0022](0022-ossie-is-the-storage-format.md)).

## Decision

1. **Chat**, as 0019 describes it.
2. **The conversation list.** The sidebar shows, under each conversation, how its last turn ended
   ("Answered", "Couldn't answer", "Waiting for you", "Stopped", "Failed"). A Conversations page
   lists them all with a filter by ending. `GET /conversations` serves both from the checkpointer;
   no new store.
3. **The semantic layer.** The `ossie-viewer` build, embedded in the app and opened on a schema's
   Ossie file, served read-only by `GET /layer` and `GET /layer/{schema}`. The answer's Details
   links to the first table the answer used. No rows from the database are shown.

There is no SQL log page. Each answer's Details shows the SQL that answered it and, above it, each
failed attempt with its SQL and its error. Each attempt therefore records `submitted_sql` and
`error`, which it did not before.

0019's style applies to every view: white page, one grey, amber only for guesses and the waiting
dot, no badges or icons beyond send, no dark mode.

## Rejected

- **A SQL log across conversations.** The owner needs the SQL behind one answer, not a log.
- **A tables page built in this app.** It would repeat the viewer. Embedding the viewer keeps one
  drawing of the layer.
- **Sample rows from the warehouse.** A second path from a page to the database, for a need the
  owner does not have.
- **Bringing back the old pages.** They read the browse API that [0009](0009-browsing-and-filtering-api.md)
  reverses.
