# 0019: One plain chat page

- **Status:** Accepted (2026-10-19).
- **Deciders:** project owner.
- **Scope:** the whole web UI. What an answer contains is
  [0018](0018-every-answer-states-its-assumptions.md); what the project is, is
  [0017](0017-a-served-chat-app-over-a-layer-the-user-brings.md).
- **Reference mockup:** `variant-11-simple.html`, kept outside the repository with the ten
  rejected variants.

## Context

The UI has eight pages and 21,500 lines: chat, audit, history, clarifications, corpus, schema,
settings and review, with display levels that show engineers more than analysts. Ten richer
redesigns were drafted on 2026-10-19, with coloured assumption cards, inspector panels, grounding
meters and lineage graphs. The owner rejected all of them. Each one made the interface the
subject, when the person came to ask a question and read an answer.

## Decision

The page looks and behaves like a plain chat assistant, in the manner of ChatGPT: a quiet sidebar,
one centred column of conversation, and a composer at the bottom. Nothing competes with the
question and the answer.

### 1. One page

- **Sidebar.** "New conversation" at the top, then the conversations grouped by date. A
  conversation where the engine is waiting on a clarifying question appears first, under "Waiting
  for you", with a small dot. That replaces the history and clarifications pages.
- **Conversation.** A single column about 720 pixels wide. The question is a grey bubble on the
  right, and the answer is plain text on the left, with no card around it.
- **Composer.** One rounded input. Below it, one line of small print: "Read-only. Every query is
  checked before it runs."
- Each conversation has its own URL.

### 2. An answer, top to bottom

1. The answer in one or two plain sentences.
2. The result rows as a small plain table: the first 20 rows, the ones the model saw, with
   "Showing 20 of 1,204 rows" when there are more. The turn entry keeps them, outside the record,
   so an old conversation shows its numbers without rerunning the query.
3. "To answer this, I assumed:" and a short bulleted list. Each assumption that the semantic layer
   backs ends with its source in grey ("Your definition of state."). A guess ends with "This is a
   guess." in amber and a "Change" link. Anything the server found in the query reads "The query
   does this, though you didn't ask for it."
4. A row of two quiet text actions, "Copy" and "Details".

"Details" opens, in place: where each part of the question came from, with the physical names in
monospace; the SQL; the six checks it passed; and the model and time taken. These are built from
the parsed SQL and the layer, the same parse 0018 uses, so they describe only what the query did.
Physical names appear only here, because on an obfuscated schema they mean nothing to a reader.

### 3. The other states

- **Running.** One grey status line under the question, naming the current stage ("Finding your
  definitions…", "Checking the query…"), fed by the stage events of
  [0010](0010-live-stage-events.md). No stage tree.
- **Clarifying question.** The engine asks in plain text, says why in one sentence, and offers the
  choices as plain outlined buttons. The person can also type an answer.
- **Refusal.** "I can't answer this from your data.", the reason in one sentence, and two or three
  nearby questions the layer can answer, as links that fill the composer.
- **New conversation.** "What do you want to know?" centred, with three example questions in grey
  below the composer, drawn from the layer's verified examples.

### 4. Style

White page, black text, one grey for secondary text, amber only for guesses and the waiting dot.
No badges, no coloured cards, no icons beyond the send button, no dark mode in the first version.
One view for everyone: the engineer and analyst display levels and the settings page go.

## Consequences

- The UI is rebuilt rather than adapted. It reuses the existing stream handling and the turn
  projection from the API, and little of today's component tree.
- The UI test suite is rewritten with it. The tests that check the deleted pages go.
- The answer card's current "why" lines, which nothing writes, and the "Schemas considered" field,
  which reads a key the record does not have, go with the old card.

## Rejected

- **The ten mockups of 2026-10-19.** Too much interface for a person who came to ask a question.
- **An inspector panel beside the conversation.** Useful to an engineer, but it splits attention
  for everyone else. "Details" in place covers the engineer.
- **Keeping display levels.** Two audiences seeing different answers makes support harder, and
  "Details" serves the curious without a setting.
