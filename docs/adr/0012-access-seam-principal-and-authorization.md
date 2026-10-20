# 0012: The access seam — principal, authorization, and the Layer 6 split

- **Status:** **Reversed** (P2 of the pivot, [ADR 0017](0017-a-served-chat-app-over-a-layer-the-user-brings.md)).
  Nothing below is built. The ports, the two adapters, the three authorization rules, the
  browse-surface narrowing and the resume identity binding are deleted.
- **Deciders:** project owner (2026-08-12; reversed 2026-10).
- **Scope:** who a turn is executed *for*, and what that subject may read.

## What was decided

On 2026-08-12 the repository took on a seam for per-user authorization, so that a fork could
carry a permission model without editing the layer stack:

- a `Principal`, an `AccessPolicy` port returning a `Grant` per turn (reachable tables, denied
  columns, declared row predicates), and a default adapter that authorized everything;
- three new rules in the layer stack, so "you may not read this" stopped sharing a reason code with
  "retrieval did not find this": `r_table_not_authorized`, `r_column_not_authorized` and
  `r_row_predicate_unenforced`, asked after the licence so the pair could not be used to learn
  which tables exist;
- withholding rather than refusing alone: a denied asset left the model's prompt, the tools that
  can name an asset and every browse route;
- the grant's digest as a comparability knob, and an identity stored on each paused clarification
  that the resuming caller had to match.

The reasoning was that `licensed` served two masters, the retrieval budget and the governance
allowlist, and that the abstention story ("19 of 20 refusals are retrieval misses") rested on
there being no second meaning for `r_table_not_licensed`.

## What is true instead

The engine serves one local operator. Nothing authenticates, every request carries one constant
identity that nothing reads, and the thread id is the only handle on a paused clarification. The
TABLES layer asks one question, whether the turn licensed the table, and COLUMNS asks whether the
corpus allows the column and has not excluded it or marked it suspect. Every verdict, rule id and
rendered context for every shipped question is the same as it was under the open grant; only the
cases and probes that existed to test a grant are gone.

`api/auth.py` stays, for a different reason: LangGraph Server will not start an `auth` block
without an authenticate handler, and the two handlers that refuse a client-supplied state-writing
payload (`threads.update`, and `create_run` carrying `command.update` or `command.goto`) are about
payloads, not callers.

## What was learned

- **A seam that ships open is never exercised by a user.** Every authorization branch was a
  constant under the default grant, so for two months the only evidence they worked was a
  fictional world in the adversarial suite. The code was carried through every change to the
  tools, the renderer and the browse routes without a caller who would have noticed it break.
- **Refusing a statement is not withholding.** An independent review found three surfaces that
  disclosed what the layer stack refused: a tool that listed a denied column of an allowed table,
  a join whose ON clause named a withheld table, and a browse route. A permission model has to be
  asked at every place that can name an asset, which is a larger commitment than a rule in the
  layer stack, and the commitment was made for a deployment that never existed.
- **Keys must fold the same way on both sides.** A short key made the two halves of the seam
  disagree: the statement check accepted a spelling that the tool bound refused, and a denial that
  matched nothing denied nothing without a warning. If a permission model ever comes back, one
  function must own the folding.
- **The identity gate guarded nothing.** With one principal the comparison was always equal. It
  cost a checkpointed field, a reserved config key and a test file, and it was the part of the
  design most often cited as a reason to keep the rest.
- **`licensed` serving two masters is still open.** Deleting the second set does not decide
  whether the retrieval budget and the governance allowlist should be different sets; it only
  stops pretending a third set answered that.
