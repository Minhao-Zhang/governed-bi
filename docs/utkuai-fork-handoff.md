# UtkuAI fork — design rationale, spec, and how it attaches

**What this is.** A handoff, not a request. It records why this fork exists, what it was built
against, exactly which parts of the engine it touches and why, and the one piece of debt it owes
you. Nothing in this document needs an answer from you — where this fork had to choose between
two legitimate designs, the choice made and its evidence are stated below rather than asked about.

**Why this file lives flat in `docs/`, not `docs/adr/`.** Same reason as
[`docs/utkuai-role-tiers-and-clarification-cancel.md`](utkuai-role-tiers-and-clarification-cancel.md):
the ADR sequence (`0001`–`0013`) is numbered and you own it. A fork-local `0014` would collide the
first time you add one — the same defect this fork's own `register/prompts.py` merge found, where
`v3`, `v4` and `v5` had each come to name two different prompts across the two histories. Fork docs
sit beside `docs/questions-for-minhao-2026-08-14.md`.

**Companion doc.** [`docs/handoff-claim-audit-2026-08-18.md`](handoff-claim-audit-2026-08-18.md) is
the evidence-based account of what this fork's product claims actually do, verified by running the
live engine rather than by reading code. This document is consistent with it and defers to it on
every claim of "does it work" — this document's job is "why does it exist and how is it built."

---

## Why this fork exists

The problem this fork was built to close is not answer accuracy. It is that the person the product
is for — a non-technical owner of a small business, with no data engineer and no SQL — cannot tell
whether an answer in front of them is trustworthy, and has no way to say that it is wrong. An
engine that is well-governed on the backend and illegible on the front end fails that person just
as completely as one that is wrong: a refusal that reads as a bare token, an answer recited from a
stored definition that looks identical on screen to one that just queried the database, and no
control anywhere for "this is wrong, tell someone" are all the same failure from that person's
seat. Everything this fork adds — the curation layer, the clarification loop, the tier split, the
answer-card work — is in service of making an answer something that person can evaluate and
contest, not in service of making the SQL more correct.

---

## The spec

Two users, two modes, one asymmetry that decides what has to persist.

| | Admin | Business reader |
|---|---|---|
| **Runs** | Once per corpus, or when the schema changes meaningfully | Every time they have a question |
| **Does** | Validates the semantic layer: reviews AI-proposed column meanings, answers clarifications, approves or rejects drafts | Asks a question in plain language, reads the answer, can report one as wrong |
| **Never sees** | — | Generated SQL, the governance pipeline, raw table/column names (at `business` tier) |
| **Gate** | Nothing is queryable by the corpus's own tables until this step has run at least once | Cannot change the semantic layer directly — every correction routes through the admin queue |

**What must persist, and why the split matters:**

| State | Owner | Lifetime | Cost of losing it |
|---|---|---|---|
| Semantic layer (column definitions, business-term mapping, certified rules) | Admin | Long-lived, survives every session | Wrong answers — this is the layer every SQL statement is checked and written against |
| Session context (conversation history, active scope, mid-conversation filters) | Business reader | Medium-lived, one conversation | Re-asking the last question — annoying, not incorrect |

The asymmetry is the design: a bug that loses session context costs a reader thirty seconds; a bug
that loses or silently corrupts the semantic layer costs every future answer's correctness. That is
why the write path into the semantic layer (below) is gated by an explicit human approval action
and the read path out of it is not gated by anything a reader can touch.

---

## Who owns what: the fork is almost purely additive

**Measured today** (`upstream/main` at `7142ab9`, 2026-08-13, the actual merge base — 0 commits
behind): **154 files changed, +29,070 insertions, −250 deletions, zero files deleted, zero
renames.** The ratio matters more than the size: nothing this fork does required removing a line of
Minhao's code, which is the property that keeps a merge a merge rather than a judgment call (§6).

**Commands used, so this is re-derivable against a newer commit rather than trusted:**

```bash
git diff --shortstat upstream/main...HEAD
git diff --name-status upstream/main...HEAD -- src/governed_bi/ | grep "^A"
git diff --name-status upstream/main...HEAD | grep -E "^(D|R)"
git diff --numstat upstream/main...HEAD -- src/governed_bi/ \
  | while read a d f; do git cat-file -e upstream/main:"$f" 2>/dev/null \
  && echo "$((a+d)) $f"; done | sort -rn
grep -n "LAYERS\|UNLAYERED" tools/check_imports.py
grep -rIin "wren\|genbi" pyproject.toml uv.lock ui/package.json
```

### Backend: 28 modules exist only in this fork

```
api/curation_routes.py            api/drafts_routes.py
api/feedback_routes.py            api/trust_loop_routes.py
corpus/drafts.py                  corpus/provenance.py
corpus/snapshot.py
curator/__init__.py               curator/candidate_rules.py
curator/clarification.py          curator/clarifications.py
curator/elicitation.py            curator/elicitation_answers.py
curator/elicitation_terms.py      curator/enhancer.py
curator/feedback.py               curator/gap_joins.py
curator/gap_signals.py            curator/gaps.py
curator/mistake_memory.py         curator/scan_report.py
eval/attribution.py               eval/power.py
serve/nodes/mine_corpus.py        serve/nodes/mine_mistakes.py
serve/runtime_overrides.py        serve/schema_term_guard.py
serve/structured_check.py
```

`curator/` — 14 files, the second-largest package in the tree — is the clearest single statement
of what this fork added: an entire admin-facing curation subsystem Minhao's engine has no
equivalent of.

### Every upstream file this fork touched, and how much

```
381  register/prompts.py       45  datasource/sqlite.py
337  serve/tools.py            32  serve/graph.py
326  serve/fetch.py            31  serve/session.py
 66  api/routes.py             31  serve/resume.py
 48  serve/nodes/stamp.py      22  register/knobs.py
 45  eval/harness.py           20  corpus/analyst.py
                               17  register/record.py
                               16  serve/state.py / eval/report.py
                                9  serve/agent_state.py
                                3  serve/nodes/agent_core.py
```

The top three total 1,044 lines; every other touched file in `src/governed_bi/` totals 401. **Why
each was necessary**, for the top three (the rest is wiring — a new state key threaded through, a
new edge in the graph):

- `register/prompts.py` — the fork's clarification guidance, `basis`, and language-matching rules
  had to live beside the prompt they modify, not beside it in a separate file, because
  `prompt_set_hash` (same file) hashes every registered prompt's text, and a prompt that is not
  physically the same string as its registered variant is not the variant it claims to be.
- `serve/tools.py` — `ask_user`'s `basis`/`choices`/defer/ledger wiring. This is `ask_user`'s own
  file; there is nowhere else a tool's argument surface can live.
- `serve/fetch.py` — `compare_column_pair` (line 301) and `count_distinct_values`, the governed
  reads `curator/gaps.py`'s near-duplicate detector needs. They live here, not beside their caller
  in `curator/`, because offline curation has to use the identical `prepare()`-checked, ledgered
  read path a live turn uses — otherwise the Setup Wizard could see data a query never could. This
  decision is the direct cause of §5.

### UI: 18 components exist only in this fork

```
app/settings/page.tsx
components/answer/refusal-clarification-prompt.tsx  components/answer/wrong-answer-report.tsx
components/chat/raised-history.tsx
components/common/clarification-answer-form.tsx
components/corpus/assumptions-log.tsx        components/corpus/clarifications-panel.tsx
components/corpus/conflicts-panel.tsx        components/corpus/drafts-panel.tsx
components/corpus/elicitation-checklist-form.tsx
components/corpus/elicitation-wizard.tsx     components/corpus/feedback-panel.tsx
components/corpus/trust-loop-metrics.tsx
components/settings/corpus-tab-toggles.tsx   components/settings/engine-toggles.tsx
components/settings/role-switcher.tsx
lib/corpus-tab-groups.ts                     lib/display-mode.ts
```

Heaviest touch on upstream's own UI files: `lib/schemas.ts` (419), `lib/api-client.ts` (338),
`lib/mock/fixtures.ts` (337 — mock mode has to demonstrate this fork's features without a
database), `lib/capabilities.ts` (203), `components/answer/answer-card.tsx` (198),
`components/chat/clarification-prompt.tsx` (189), `lib/answer-delivery.ts` (174),
`app/corpus/page.tsx` (158).

**Note on how this was measured.** `ui/` and the standalone `governed-bi-ui` repo share no git
history — the copy into the monorepo was a fresh tree — so a plain file-list diff cannot tell
"this fork wrote it" from "upstream deleted it." Four files this fork's own working notes once
called "ours to re-port" turned out to be exactly that: upstream had deleted them
(`components/chat/stage-stepper.tsx`, `lib/stages.ts`, `lib/columns.ts`,
`components/health/health-overview.tsx`). The number above comes from doing the comparison inside
`governed-bi-ui`, where the history *is* shared, not from the file-list diff.

---

## The seams — how it attaches

Five points of contact, named by symbol.

**1. The graph node that mines a clarification.** `serve/graph.py` wires two nodes into the edge
chain: `agent_core → reflect → mine_corpus → mine_mistakes → narrate → stamp`. `mine_corpus_node`
(`serve/nodes/mine_corpus.py`) and `mine_mistakes_node` (`serve/nodes/mine_mistakes.py`) run on
**every turn**, not behind an HTTP route — the module docstring's own argument is that
`graph.invoke()`/`graph.astream()` is the one thing every transport actually calls (LangGraph
Server's native resume included), so putting the mining logic in a node is what makes it
unskippable by a transport nobody has written yet.

**2. The fold path.** `mine_corpus_node` calls `curator/clarification.py::fold_answered_clarification`,
shared with the offline `POST /clarifications/{id}/answer` route via
`curator/clarification.py::fold_ledger_answer_into_corpus`. The reader-facing wrong-answer report
(§8) folds through the sibling `curator/feedback.py::fold_report_into_corpus`. Both paths run the
candidate through `curator/enhancer.py::decide_fold` / `apply` — a model call, not a heuristic,
that decides `duplicate_of` (reinforce), `conflict_with` (hold, exclude from the model's context),
or genuinely new.

**3. The corpus write.** A new or generalized fact is written by `corpus/drafts.py::submit_draft`
at `ProvenanceStatus.proposed`. **Read the next sentence carefully, because it is the one thing in
this document you should not take on trust.** `proposed` gates *certification*, not *retrieval*:
`corpus/analyst.py`'s certified-only filter stops a `proposed` term licensing a column in
`govern/check.py`, but `serve/session.py::_visible` filters on `governance.excluded` alone, with no
provenance check — so a `proposed` asset **is** rendered into the model's context on the next
session over that corpus root. Verified by construction on 2026-08-16, not inferred. The two halves
therefore disagree about what `proposed` means, and closing that gap changes behaviour for every
corpus, which is why this fork recorded it rather than fixed it
(`api/curation_routes.py::clarification_from_refusal_route`'s docstring is the fullest account).
`corpus/provenance.py` records where it
came from (`audit.source`: `live_chat` / `refusal` / `feedback`), which is what lets
`GET /trust-loop/metrics` (§8) count entrances by origin at all.

**4. The HTTP routes.** All additive; nothing upstream's client reads is removed.

| Route | File | What it does |
|---|---|---|
| `POST /corpus/drafts/{asset_id}/approve` | `curation_routes.py` | promotes `proposed` → `certified` |
| `GET /corpus/drafts` | `drafts_routes.py` | the approval queue, read fresh off disk (not the frozen session mapping) |
| `GET /clarifications`, `POST /clarifications/{id}/answer`, `POST /clarifications/{id}/cancel`, `POST /clarifications/from-refusal` | `curation_routes.py` | the offline clarification ledger and the refusal-becomes-a-clarification entrance |
| `GET /corpus/assumptions`, `GET /corpus/conflicts`, `POST /corpus/conflicts/{id}/resolve` | `curation_routes.py` | the Enhancer's dedup/conflict surfaces |
| `GET/POST /settings/toggles` | `curation_routes.py` | the operational-knob overrides, incl. `enable_clarification_to_draft` |
| `POST /elicitation/generate`, `GET /elicitation/candidates` | `curation_routes.py` | the Setup Wizard's gap detectors |
| `POST /feedback`, `GET /feedback`, `POST /feedback/{id}/answer`, `POST /feedback/{id}/dismiss` | `feedback_routes.py` | a reader saying an answer is wrong |
| `GET /threads/{id}/raised` | `trust_loop_routes.py` | what a given reader has raised, and whether it was resolved |
| `GET /trust-loop/metrics` | `trust_loop_routes.py` | refusals → entrances → approved rules → retrieved-again, counted |

**5. The UI.** `role-switcher.tsx` / `lib/display-mode.ts` implement the `business` / `analyst` /
`engineer` tier — **today a client-only override** (`localStorage`), stated so in the module's own
comment, because the server's `ui_display_mode` capability field is declared and never populated.
`clarification-answer-form.tsx`, `elicitation-wizard.tsx`, `clarifications-panel.tsx`,
`conflicts-panel.tsx`, `drafts-panel.tsx`, `feedback-panel.tsx` and `assumptions-log.tsx` are the
admin-side surfaces that drive the routes above; `refusal-clarification-prompt.tsx` and
`wrong-answer-report.tsx` are the two reader-side entrances into the queue.

---

## The one architectural debt: `curator/` ↔ `serve/`

`tools/check_imports.py` declares 13 ordered layers
(`paths → credentials → ports → register → measure → corpus → retrieve → govern → datasource →
model → serve → eval → api`) and enforces the order by walking the AST — a function-level import
is not an escape hatch. `curator/` is the one package in `UNLAYERED`, and the reason is recorded in
the file itself, not just asserted here: it is in a genuine **mutual** dependency with `serve/`.

- `curator/mistake_memory.py:24` imports `serve.ledger` at module level.
- `curator/gaps.py:311` imports `serve.fetch.compare_column_pair` (function-scoped — the AST walk
  catches it anyway).
- `serve/nodes/mine_corpus.py:72` and `serve/nodes/mine_mistakes.py:81,88` import `curator` back.

No single position in the 13-layer ordering satisfies both directions, and the cycle is structural
rather than incidental: offline curation needs the exact same governed, ledgered read the serve
path uses (§4, seam 2's reasoning for `serve/fetch.py`), and the serve path needs the curator to
fold what a turn just learned. Those two requirements point through the stack in opposite
directions.

**The recorded fix is to lift the governed-read helpers into a layer below both** — a real
refactor, not a repositioning, and it was deliberately not improvised inside the 2026-08-14 merge.
**This is the thing that would cost something if this were ever taken upstream:** the fix touches
`serve/fetch.py`, this fork's single largest seam into your code (326 lines, §3), which is exactly
the file where a future conflict would hurt most. Leaving it as-is costs the opposite thing — one
package's imports are unconstrained by the layering gate, so a future edit could add a dependency
nobody notices until the AST sweep is re-read by hand.

---

## Why merges stay cheap

The 2026-08-14 merge of 102 upstream commits (8/07–8/13, 388 files, +77,777/−9,310) is the
datapoint. Two separate merge operations, both landed clean:

| Merge | Conflicts | Outcome |
|---|---|---|
| Backend (`governed-bi-analysis`, this repo) | 17 → 12 after dropping a since-superseded patch → **0** | 1,232 → 1,839 tests passing, all green; `ruff check` zero net-new findings; every conformance gate passed, none waived |
| Frontend (`governed-bi-ui`, shared history) | **24** on the main merge, then **1** on flattening the monorepo's `ui/` tree back onto it | Result copied into `ui/` as 8 added files + 14 edits — small enough for a reviewer to read |

Three properties are why, and worth preserving deliberately going forward:

1. **New behaviour goes in new files.** The 28 backend modules and 18 UI components in §3 cannot
   conflict with anything upstream does, because upstream does not have them.
2. **Changed behaviour hides behind fields upstream's client discards.** An extra key on a stream
   message or a `/capabilities` payload is inert to code that never reads it.
3. **Nothing is deleted.** Zero files removed, 250 lines removed across 154 touched files. A
   deletion is what turns a clean three-way merge into a judgment call — the two Bedrock-related
   conflicts that used to recur on every sync went away specifically because this fork's own
   Bedrock patch was deleted (upstream's later, better version superseded it) rather than merged
   line-by-line.

---

## The current state, honestly

Full account: [`docs/handoff-claim-audit-2026-08-18.md`](handoff-claim-audit-2026-08-18.md), which
ran real questions against the live engine and read the durable log rather than inferring from
code. Summary, so it does not need restating here:

**Verified live, today.** Refusal (names what the corpus can see instead), clarification
(`ask_user` asks when a term is genuinely ambiguous), report (a reader can say an answer is wrong),
approve (an admin promotes a draft without a restart), and count (`GET /trust-loop/metrics`
currently reports **50 refusals → 2 reader entrances → 2 approved rules → 2 retrieved again**,
against 240 real turns) are all real, run today, not read from code.

**Wired and never populated.** The `assumptions` field is declared, sent, parsed, and rendered —
and nothing in the prompt or tool layer ever fills it. It sits in the goal sentence of this fork's
own product pitch, and today the pitch does not hold.

**A certified rule is recited, not recomputed.** A correction taught the corpus a count ("8,512
active apps, excluding delisted") once; re-asking now returns that literal number with
`terminal: no_sql` and an empty attempt ledger — it never re-queries. That is honest at the
business-tier stamp ("answered without consulting your data at all") but wrong in the answer's own
prose if it's read on its own, and it does not generalise: the same exclusion rule was not applied
to a related aggregate question.

**One correction to this fork's own earlier design intent, made here rather than left stale.**
Earlier working notes described the write-back path as togglable between "auto-certify" and
"draft," mirroring your fail-closed default only when the toggle was off. That is not what
shipped. `enable_clarification_to_draft` (`register/knobs.py`) gates only **whether mining runs at
all** — every fold this fork's engine produces, toggle on or off, is written `proposed`
(`corpus/drafts.py::submit_draft`) and needs one explicit admin approval before it can affect an
answer. The shipped behavior is stricter than the original design, and matches your own default
more closely than planned, not less.

---

## Upstream decisions, stated not asked

Two decisions this fork made where the boundary between the two repos was genuinely in question.
Both are recorded with their evidence in `docs/questions-for-minhao-2026-08-14.md` (questions 2 and
3, drafted as questions before it was clear no answer was needed to proceed) — restated here as
what was decided, why, and what it costs to leave as-is.

### Decision: keep your `analyst`-prompt numbering; add this fork's rules as new variants

Both prompt lineages branch from `v2` and share no text after it. Your `v3`/`v4` add the
result-shape/DISTINCT rule and the star rule (McNemar-significant: over-projection 107 → 18,
p = 0.0008; `r_star_projection` 35/29 → 2/2). This fork's line adds a ranking clarification,
`basis`, and language-matching rules. Independently, `v3`, `v4` and `v5` had each come to name two
different prompts across the two histories at merge time — the exact defect a variant's name-hash
exists to prevent.

**Decision:** your numbering is kept untouched because your McNemar figures are published against
it; this fork's variants were renumbered `v3–v6` → `v6–v9`, with `v9` the default and carrying only
this fork's three rules. `v10` is also composed — `v9` plus exactly the suffix your `v4` adds to
`v2`, pinned byte-for-byte by a conformance test — but is **not** the default, because your numbers
were measured against your `v2` base and `v9` is a different base (this fork's rules change when
the agent stops to ask instead of answering, so your result-shape rule may fire less often or
interact differently). Promoting `v10` needs its own measurement arm; none has been run.

**Cost of delay:** none today — the renumbering already shipped and both lineages' own measured
claims still hold against their own numbers. The only thing waiting is whether `v10` is worth an
arm before this fork spends one measuring it.

### Decision: `curator/` stays a fork-local layer, not a contribution, until the layering fix ships first

`curator/` is the largest thing this fork carries that you do not: the clarifications ledger, the
Setup Wizard's gap detectors, the Enhancer's dedup/conflict handling, and the UI tabs that drive
them. It reads your corpus through your own asset schema and writes drafts through your
`corpus/store.py`; none of its logic is UtkuAI-specific business logic.

**Decision:** not offered upstream as-is. The honest version of that offer requires lifting the
governed-read helpers below both `curator/` and `serve/` first (§5) — handing over `curator/`
without also handing over its one exempted import cycle would be handing you this fork's debt along
with its feature.

**Cost of delay:** every future upstream merge continues to carry `curator/`'s 4,000+ lines as a
package only this fork tests and maintains, though §6 shows that cost is currently small (zero
conflicts on the last sync, because nothing here overlaps a file you touch except the three named
seams). If left indefinitely, your own users get no admin-curation path — the gap this fork's whole
premise ("an SMB has no data engineer to author a semantic layer up front") was built to close.
