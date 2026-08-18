# Role tiers, a Settings surface, and cancelling a clarification

**Status:** implemented and verified live, 2026-08-15. This fork's design, not upstream's.
**Branch:** `main` (was `ryan/merge-upstream-0814`, force-pushed onto `main` and deleted 2026-08-15).
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

**Out of this round, done in the next one the same day:** the engine switches. See *Three
client-only halves* for why they were deferred and the 2026-08-15 update at the end for what
closing them corrected — including that the name in this sentence turned out not to be a knob.

---

## Three client-only halves (read this before adding a fourth)

The port surfaced a pattern worth naming, because it is how a feature comes to look finished
while never having run:

| Field / control | Client | Server |
|---|---|---|
| `POST /settings/allow-user-clarification` | full client: schema, `api-client` method, `ClarificationToggle` component | **route does not exist**, on either branch. `api-client.ts` cites `api/runtime_toggles.py`, which does not exist either. **Closed 2026-08-15 by deleting the client half** — there is no such knob to wire it to; the switches that exist are at `/settings/toggles`. |
| `capabilities.can_edit` | `ClarificationToggle` renders only when true | **hardcoded `False`** (`routes.py:319`) — so the control above can never appear. **Closed with the component**; nothing this fork adds gates on it. |
| `capabilities.ui_display_mode` | declared in `schemas.ts`, read by `isSimpleUiMode` | **never populated.** `grep -r ui_display_mode src/` is empty; the live response has no such key. **Still open**, and deliberately: a tier is per-principal and waits on multi-tenancy. |

The third is why this design's tier is **client-side only**. The wire field stays declared, the
server keeps not filling it, and that is stated rather than papered over: a future multi-tenant
server sets it and every screen below already honours it, with no interface change. The seam is
the deliverable; the server half is not in this round.

`allow_user_clarification` is worse than unfilled — it is **not in the knob register at all**
(`governed_bi.local.toml`'s `[serve]` section is read by nothing), which is why a clarification
fires regardless of what that file says. Wiring it needs a writable runtime knob, a route, and a
decision about where an override persists; that was deferred out of this round and built the same
day. The answer turned out to be that the name was wrong rather than the plumbing missing — see
the 2026-08-15 update at the end.

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
| ~~**`allow_user_clarification` toggle**~~ | **Done 2026-08-15, and the answer was that the name was wrong.** There is no such knob; the two that exist (`enable_clarification_to_draft`, `enable_mistake_memory_mining`) are now writable through `serve/runtime_overrides.py` and `POST /settings/toggles/{name}`. The client half was deleted rather than wired. See the section below. |
| **Server-driven tier** | populate `capabilities.ui_display_mode`. The writable-knob mechanism now exists, but a tier is per-*principal* and a knob is per-*deployment*, so this waits on multi-tenancy rather than on plumbing. |
| **Defer's reliability caveat on the answer card** | the field is declared and no longer stripped (2026-08-14); nothing renders it. |
| **The clarification question's own wording** | prompt work with a measured arm, alongside ANALYST v10. |


---

## Update 2026-08-15 — the switches, and two defects only clicking them found

The deferred row above is closed, and closing it corrected the plan twice.

### It is two knobs, not three, and "operational" was the wrong gate

The plan named three toggles. `enable_structured_percentage_check` is declared
`Role.comparability` — its own note says "a run with it on is not comparable to one without" — so
changing it from a switch would make two runs incomparable with nothing recording that a human did
it. That belongs in `arms.toml`, which exists to name such a change and reconcile it against an
artifact. Two knobs are exposed: `enable_clarification_to_draft` and `enable_mistake_memory_mining`.

And the first design gated on the `operational` role, which is wrong for a sharper reason: that
role also carries `git_sha`, `git_main_sha`, `working_tree_dirty` and `diff_sha256` — the fields by
which a measurement says *which code produced it*. A UI able to write any operational knob could
**forge a run's provenance**. Toggleability is a second, explicit decision per knob
(`serve/runtime_overrides.py::TOGGLEABLE`), and a test asserts the provenance four are absent.

### Every row says where its value came from

`describe()` returns a `source` of `default` / `override` / `environment` per knob, and the UI
renders it. Without that field a client cannot tell an operator that a switch is pinned by an
exported variable and would render a control that silently does nothing — the exact class this
round exists to end. An environment-pinned knob is listed as not editable, names the variable, and
a write to it is **409** rather than accepted-and-ignored: accepting it would leave the interface
showing a value the engine does not use, which is the same lie in a new place.

Precedence is default → policy → resolvers → **override** → environment. An exported variable is
how an eval arm pins a run.

### An override is recorded, never hidden

It is layered by the two readers that mint a claim — `Session.turn` and `capabilities_for` — so it
lands in every turn's `knobs_resolved`. That means `measure/gates.py::_knobs_resolved_gate` sees a
mid-run flip as configuration drift and **fails that arm**, which is correct rather than a
limitation: `enable_clarification_to_draft`'s own declaration says it "changes the corpus on disk
between two turns of the SAME run".

### Two defects, both found by clicking, both mine

1. **Setting a switch changed nothing a node reads.** `_resolved_knobs` runs once at session
   construction and `Session.turn` copies its output, so an override written after boot never
   reached the turn `mine_corpus` reads its knob off. The feature reported success and did nothing
   — a control with no server behind it, built in reverse.
2. **Then clearing one changed nothing either.** The fix above layered the override in *two* places,
   including inside `_resolved_knobs`, so a session built while a switch was on baked `True` into
   its base and layering `{}` over that still resolved `True`. A switch that turns on and will not
   turn off is worse than one that does neither, because the operator cannot tell which state the
   engine is in. The base is clean now; the two readers own the layering.

A third, smaller: nothing isolated the test suite from the real override file, so a switch flipped
in a browser changed what the suite asserted. It surfaced as one API test failing on one machine.
Now an autouse fixture in `tests/conftest.py`, repository-wide, because the failure mode is a test
that never thought about the file.

### The loop ran end to end for the first time

With the switch on from the UI, a `data_definition` clarification answered **in chat** wrote
`clarification.app_store.01e4b2e6842db898` as `proposed` — the corpus went from 5
clarification-derived assets to 6. That path has existed since the 8/11 port and had never been
reachable, because there was no way to turn the knob on. Clearing the switch from the route returned
`/capabilities` to `false` in the same session.

Worth recording alongside: the question the model asked on that turn was markedly better copy than
the one that started this round — *"two columns that look like they should both hold the app's name,
but on the same row they actually show different, unrelated app names"* — no row counts, no column
casing. Part of that is the filler `why` being gone; the rest is the model, and it is not measured.

### The scope gate blocked this verification three times

Recorded because it is now the most reproducible instance of a defect nothing tracks. While trying
to reach a `data_definition` pause: *"How many active apps are in the playstore?"* produced a
`data_definition` clarification twice earlier in the session and then `refused by guard`; *"Which
are the best apps in the play store?"* clarified once and refused twice; *"very best"* refused. Two
further phrasings clarified as `ranking_ambiguity`, which `mine_corpus` skips by design. It took
five live turns to get one usable pause.
