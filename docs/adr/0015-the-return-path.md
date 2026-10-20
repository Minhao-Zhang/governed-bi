# 0015: The return path — reader feedback into the corpus

- **Status:** **Reversed** (P3 of the pivot, [ADR 0017](0017-a-served-chat-app-over-a-layer-the-user-brings.md)).
  Nothing below is built. The store, the review page, the note control and the tools that
  drafted a corpus change from a complaint are deleted. Clarifications waiting for an answer
  remain, and they are read from interrupt state alone.
- **Deciders:** project owner (2026-08-23; reversed 2026-10).
- **Scope:** how a reader telling the engine it is wrong becomes a change to the corpus.

## What was decided

On 2026-08-23 the repository took on a loop from a complaint back into the corpus, so that a
wrong answer would not stop at a note nobody acted on:

- a store of observations, each attributed to one turn, with a closed vocabulary for what was
  wrong and a lifecycle a steward moved by hand;
- a patch as a candidate corpus edit, checked in memory and handed to a person as a diff,
  because this process must not commit to the corpus repository;
- a waiting list that showed unanswered clarifications and open notes together, oldest first;
- a review page and a small note control on the answer card. The richer capture surface, the
  agentic triage pipeline and the paid verification tiers were designed and not built.

The reasoning was that an analyst knows the business and an engineer has commit rights, and
that the engine should manufacture the evidence for a change without being the thing that lands it.

## What is true instead

There is no store of complaints and no review page. A reader cannot file a note on an answer.
`/clarifications/pending` lists questions the engine asked and nobody has answered, read from
the platform's interrupt state, one page at a time. Answering one is still the resume of that
thread, which is unchanged.

The corpus changes the way it always could: a person edits the repository they brought and
commits it there.

## What was learned

- **A queue that unions two stores cannot be paged by asking either store for a page.** The
  merged order is the only order a caller can walk, and pushing the offset into one half
  repeats rows and hides the middle. Once the note half was gone, the interrupt half pages
  by itself.
- **A complaint that never changes an answer is a second product.** The loop was built,
  walked once, and most of the imported rows no longer reproduced, because the corpus had
  moved under them. The engine's job on this deployment is to answer, not to curate.
- **Filing on a paused turn and resuming that turn are different writes.** The earlier design
  wrote the note into graph state and could consume the live interrupt. Moving the note out
  of graph state fixed that collision, and then removed the reason to keep a second store.
- **Derived landing state was the right shape and still had no user.** Recomputing "did this
  land" from the corpus, rather than storing a flag, is how two answers to one question are
  avoided. Nobody was asking the question.
