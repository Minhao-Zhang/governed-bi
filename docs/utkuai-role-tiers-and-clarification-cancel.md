# Role tiers, a Settings surface, and cancelling a clarification

**Status:** approved 2026-08-15, implementing. This fork's design, not upstream's.
**Branch:** `ryan/merge-upstream-0814`.
**Why this file is here and not in `docs/adr/`:** the ADR sequence is numbered and upstream owns
it. A fork-local `0014` would collide the first time upstream adds one — the same defect the
2026-08-14 merge found in `register/prompts.py`, where `v3`, `v4` and `v5` had each come to name
two different prompts. Fork-specific docs live flat in `docs/`, beside
`questions-for-minhao-2026-08-14.md`.

---

## The problem, in the owner's words

> 現在的 User experience 跟 UI 並不是太好，太多文字，太不直覺，給工程師的模式，跟給商業使用者
> 的模式分流的不夠明確，都混在一起了

Three concrete instances, all observed rather than inferred:

1. **A business user is shown engineer surfaces.** `/corpus`, `/audit` and `/schema` are in the
   sidebar for everyone. `/audit` exposes every thread's SQL, the full turn records and an
   absolute path to the log directory.
2. **A clarification reads like a bug report.** A live `data_definition` question rendered as:
   *"The Play Store table has two fields that both seem to hold the app's category, and they
   disagree on about 1,028 of the 10,840 rows (though each has 33 distinct category values
   overall). Which one should I treat as authoritative for counting distinct categories?"* —
   followed by the filler line *"The question is ambiguous and the answer depends on which
   reading is meant."*, and two choices phrased *"The capitalized "Category" field is
   authoritative"*. Row counts, distinct-value counts and column casing, on the surface a
   non-technical domain owner is supposed to answer.
3. **A pending clarification has no exit.** `conversation.tsx:79` sets
   `onStop={pendingClarification ? undefined : stop}` and locks the composer. Upstream's escape
   hatch was a **Decline** button; this fork replaced it with **Defer** and hides Defer for
   `ranking_ambiguity`. So the one basis with no admin-answerable question is also the one with
   no way out but answering. **This fork introduced that trap.**

## Scope

In: role tiers, a Settings surface, cancelling a clarification, and dropping the filler `why`.

**Out, deliberately:** the UChicago palette. The owner declined it this round —
*"配色我不知道你抓的對不對，能先不處理配色"* — and they were right that it was being guessed at.
Recorded below under *Deferred* with the one fact that makes it cheap later.

**Out:** the `allow_user_clarification` toggle. See *Three client-only halves*.

---

## Three client-only halves (read this before adding a fourth)

The port surfaced a pattern worth naming, because it is how a feature comes to look finished
while never having run:

| Field / control | Client | Server |
|---|---|---|
| `POST /settings/allow-user-clarification` | full client: schema, `api-client` method, `ClarificationToggle` component | **route does not exist**, on either branch. `api-client.ts` cites `api/runtime_toggles.py`, which does not exist either. |
| `capabilities.can_edit` | `ClarificationToggle` renders only when true | **hardcoded `False`** (`routes.py:319`) — so the control above can never appear |
| `capabilities.ui_display_mode` | declared in `schemas.ts`, read by `isSimpleUiMode` | **never populated.** `grep -r ui_display_mode src/` is empty; the live response has no such key |

The third is why this design's tier is **client-side only**. The wire field stays declared, the
server keeps not filling it, and that is stated rather than papered over: a future multi-tenant
server sets it and every screen below already honours it, with no interface change. The seam is
the deliverable; the server half is not in this round.

`allow_user_clarification` is worse than unfilled — it is **not in the knob register at all**
(`governed_bi.local.toml`'s `[serve]` section is read by nothing). Wiring it needs a writable
runtime knob, a route, and a decision about where an override persists. That is backend work on
a register upstream also owns, and mixing it into a UI round would slow the two changes the
owner actually asked for.

---

## Design

### 1. Three tiers

`ui_display_mode` widens from `"audit" | "simple"` to `"business" | "analyst" | "engineer"`.
Old values map forward — `simple → business`, `audit → engineer` — so a stored override or a
server that still sends the old spelling keeps working.

The field is **this fork's**: upstream's client discards it (`npm run check:api` reports it
among `/capabilities`'s dropped fields), so widening it cannot conflict with upstream.

| Surface | Business | Analyst | Engineer |
|---|---|---|---|
| Chat | ✅ | ✅ | ✅ |
| Schema | | ✅ | ✅ |
| History | | ✅ | ✅ |
| Corpus | | | ✅ |
| Audit | | | ✅ |
| Settings | ✅ | ✅ | ✅ |

Each exclusion has a reason that is not taste:

- **History is not in Business** because `lib/threads.ts` lists the *server's* threads, not the
  caller's. Until threads are per-principal, a business user would read other people's
  questions. That is a privacy fact, not a preference.
- **Corpus is not in Analyst** because curating the semantic layer changes what the engine
  answers for *everyone*. It stays additionally gated on `can_curate_corpus`, and two
  independent gates is correct: one asks "may this person curate", the other "can this
  deployment curate at all".
- **Audit is Engineer-only** because it returns every thread's SQL, the complete turn records
  and `TURN_LOG_DIR` as an absolute path.

The answer card follows the same three tiers rather than a boolean:

| | Business | Analyst | Engineer |
|---|---|---|---|
| Answer text + reliability stamp | ✅ | ✅ | ✅ |
| SQL block, "schemas considered" | | ✅ | ✅ |
| Provenance drawer, corpus pin, reasoning timeline | | | ✅ |

`ServeProgress` collapses to one line below Engineer: a stage list is the most audit-shaped
surface in the app, and a view with no progress indicator at all reads as a hung page.

### 2. Settings

A sidebar entry, last in the list, present at every tier. Its only content this round is the
role switcher: three cards, each naming what that tier can see, so the choice is legible
without trying it.

Mechanism already exists and is not being rebuilt. `lib/capabilities.ts`'s
`effectiveSimpleMode(caps, override)` already implements *server default, local override wins*;
this widens it from two values to three and renames it for what it now returns.

Why a page and not the header icon it replaces: the eye icon was a two-state control with no
label, sitting next to the theme toggle, changing what a whole application shows. A role is not
a display preference, and the icon gave no way to say what each state means.

### 3. Cancelling a clarification

The prompt gains a third action. What it does to the ledger **depends on `basis`**, and that is
the whole point of the field:

| `basis` | Screen | Ledger row |
|---|---|---|
| `ranking_ambiguity` | prompt closes, composer unlocks | **`cancelled`** — dropped from the admin queue |
| `data_definition` | same | stays **`open`** — still the admin's homework |

*"Which metric does 'best' mean"* is a per-user judgment call; an abandoned one is noise in an
admin's queue. *"How do you count an active app"* is a fact with one answer for everyone, and it
is worth answering whether or not this particular user waited for it.

**The wire contract does not move.** Cancel is a ledger operation, not a kind of resume:
`ask_user`'s `interrupt()` payload is unchanged, and so is the resume shape
(`answer | choice_id | declined | defer`). New surface is one enum member and one route:

- `ClarificationRecordStatus.cancelled`
- `POST /clarifications/{id}/cancel`

The basis rule lives in **one** place — the ledger function reads the record's own `basis`
rather than trusting a caller to pass it — so a second caller cannot implement a second rule.

### 4. The filler `why`

`serve/tools.py:606` substitutes *"The question is ambiguous and the answer depends on which
reading is meant."* whenever the model passes no `why`. It restates the situation the user is
already looking at. When the model gives no reason, show no reason line.

This does not attempt to fix the *question's* own wording — that is prompt work, measured, and
belongs with the ANALYST v10 arm rather than here. What this round removes is the sentence the
code adds on its own.

---

## Why this survives an upstream merge

The 2026-08-14 merge cost 24 conflicts. This design is shaped to add close to none:

**Files that are entirely ours.** Upstream has no counterpart, so a merge cannot conflict:
`app/settings/page.tsx`, `components/settings/*`, `lib/display-mode.ts`,
`components/common/clarification-answer-form.tsx`, `components/corpus/*`.

**Upstream files touched, and how the edit is shaped to be small:**

| File | Edit | Why it is merge-cheap |
|---|---|---|
| `components/layout/nav.tsx` | filter one `NAV` array; drop the eye button | the array is data, and a filter is one expression over it |
| `components/answer/answer-card.tsx` | `showAudit?: boolean` → `tier?: Tier`, three guards | already a prop this fork added; the guards sit on existing JSX |
| `components/chat/message-list.tsx` | read the tier, pass it down | the read already exists from 2026-08-14 |
| `components/chat/serve-progress.tsx` | one early return | already a prop this fork added |
| `components/chat/conversation.tsx` | pass `onCancel` | one prop on an existing call |
| `lib/schemas.ts`, `lib/types.ts`, `lib/api-client.ts` | additive | upstream drops every field involved, so its schema never disagrees |
| `src/governed_bi/curator/clarifications.py` | one enum member, one function | this fork's module |
| `src/governed_bi/api/curation_routes.py` | one route | this fork's module |
| `src/governed_bi/serve/tools.py` | delete a fallback string | one line inside a block this fork wrote |

**Everything visual stays in one file.** All colour lives in `app/globals.css` as CSS variables
that components reference by semantic name (`bg-primary`, `text-tier-lineage`). Recolouring is
~40 variable *values* in that one file and zero component edits — which is why the palette can
be deferred at no cost, and why doing it later will not conflict either.

**The rule this design follows:** put fork-specific behaviour behind a field upstream's client
already discards, and behind files upstream does not have. Both are already true of
`ui_display_mode` and of `components/corpus/*`, and were what made the 8/14 port land as 8 added
files and 14 edits instead of 98 files of unattributable difference.

---

## Verification

Not "tests pass" — every claim below is checked by running it:

1. Backend, test-first: `cancelled` is reachable, a `ranking_ambiguity` cancel leaves the admin
   queue shorter by one, a `data_definition` cancel leaves it the same length, and a cancel is
   not a resume (the paused turn produces no answer).
2. `npm run check:api` against a live engine — must stay 16/16 with no new dropped field.
3. Browser, all three tiers: the sidebar contains exactly the rows tabled above, and the answer
   card shows exactly the rows tabled above.
4. Browser, cancel on both bases: prompt closes, composer unlocks, and the Clarifications tab's
   count changes for `ranking_ambiguity` and does not for `data_definition`.
5. Full backend suite and every gate in `tools/` green.

## Deferred, with what each needs

| | Needs |
|---|---|
| **UChicago palette** | a decision on how far it goes (accent only vs full re-skin) and whether the four semantic tier colours move onto the secondary palette. ~40 values in `app/globals.css`, no component edits. |
| **`allow_user_clarification` toggle** | a writable runtime knob in a register upstream owns, a route, and a persistence decision. Or: delete the client half. |
| **Server-driven tier** | populate `capabilities.ui_display_mode`. Needs the same writable-knob mechanism as the row above, or a per-principal source once multi-tenancy exists. |
| **Defer's reliability caveat on the answer card** | the field is declared and no longer stripped (2026-08-14); nothing renders it. |
| **The clarification question's own wording** | prompt work with a measured arm, alongside ANALYST v10. |
