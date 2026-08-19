# DetentAI-on-v2 Manual Test Checklist

Regression checklist for the DetentAI features ported onto `governed-bi`'s `v2`
branch (`ryan/dev-v2`) — see `detent-ai-v2-porting-spec.md` (Obsidian) for why
this exists as a fork addition rather than an upstream feature. Run this
after any change touching `src/governed_bi/curator/`,
`src/governed_bi/corpus/drafts.py`, `src/governed_bi/serve/structured_check.py`,
or the two knobs below.

**How to use this file:** work top to bottom, check each box, note the commit
hash tested next to the date. If a box fails, root-cause it for real, add an
automated test that would have caught it, then restart from the top. Do not
commit a round with any box still failing.

**Config note:** v2 has no `governed_bi.toml`-equivalent config surface —
every knob here is read via `register/knobs.py`'s register (env var override
where a session's construction path threads one, otherwise the register's
declared default). There is no `governed_bi.local.toml` on this branch.

---

## 1. Round H structured percentage check (Phase 1)

- [ ] `enable_structured_percentage_check` off (register default): ask a
  percentage-style question whose SQL computes a 0-1 ratio — the tool reply
  the model sees carries no `[structured check]` suffix.
- [ ] Same question with the knob on: the tool reply carries the
  `[structured check] ... PERCENTAGE ...` suffix when the executed SQL has no
  `*100`/`/100` factor, and none when it does.
- [ ] `GET /capabilities` reports `enable_structured_percentage_check`
  matching the session's actual knob value, not a hard-coded literal.
- [ ] `/audit/turns/{turn_id}/trace` shows the check's effect on a flagged
  turn without any UI-side change (register/record.py's field-per-stage
  contract — confirm no frontend patch was needed to see it).

## 2. Corpus draft-write foundation (Phase 2)

- [ ] `submit_draft()` on a fresh `FewShotAsset`/`TermAsset` writes a file
  whose `audit.provenance.status` is `proposed`, never `certified`, even if
  the caller tries to hand it a forged `governance.excluded=False` /
  certified `audit` — `restamp_model_authored()` strips both.
- [ ] The same asset is **absent** from `for_analyst()`'s view (and therefore
  from live retrieval) until approved.
- [ ] `POST /corpus/drafts/{id}/approve` flips it to `certified`; a repeat
  call on the same id returns 409, not a silent no-op.
- [ ] An unknown id returns 404.
- [ ] `GET /corpus/assets` (admin browser) shows the draft with
  `provenance_status: "proposed"` **before** approval — this is the "free"
  visibility the audit surface already provides; confirm it did not regress.

## 3. Mistake-memory mining (Phase 3)

- [ ] A turn whose first `run_query` attempt fails a governance layer and a
  later attempt in the same turn passes gets logged (`api/trace_store`) with
  both attempts in `execution.attempts`.
- [ ] `scripts/mine_mistakes_v2.py --corpus-dir <dir> --schema <s>` mines
  exactly one `few_shot` draft from that turn, with the corrected SQL and the
  failed layer named in `body`.
- [ ] A turn whose first attempt already passed mines nothing.
- [ ] Re-running the miner on the same logged turn produces the same
  deterministic id (no duplicate files pile up on disk from repeat runs).

## 4. Enhancer dedup/conflict (Phase 4)

- [ ] `scripts/mine_mistakes_v2.py ... --enhancer-model <model>` against a
  corpus that already has a certified `few_shot` restating the same fact:
  the candidate is **skipped**, not written — confirm no new file appears.
- [ ] Same setup but the candidate genuinely contradicts an existing
  certified fact: the candidate **is** written, with
  `audit.extra.conflict_with` set to the existing asset's id.
- [ ] A genuinely novel candidate writes plain, with no `conflict_with` key
  in `audit.extra`.
- [ ] The model is never trusted to invent an id it wasn't offered — this is
  covered by `tests/curator/test_enhancer.py`, but re-confirm manually if the
  system prompt changes: hand-craft a response naming a nonexistent id and
  confirm `EnhancerError`, not a silently-written draft.

## 5. Live clarification → draft (Phase 5)

- [ ] `enable_clarification_to_draft` off (register default): answer a live
  `ask_user` clarification via `POST /chat/resume` — no new corpus file
  appears under `session.corpus_root`.
- [ ] Same flow with the knob on: a `TermAsset` draft appears, `proposed`,
  named/summarized from the clarification's question and the answer text.
- [ ] Declining the clarification (`{"declined": true}`) mines nothing, knob
  on or off.
- [ ] The turn's own answer is delivered normally regardless of whether
  mining succeeded — break the corpus root (point it at a read-only path) and
  confirm the resumed turn still completes and answers.
- [ ] `GET /capabilities` reports `enable_clarification_to_draft` matching
  the session's actual value.

## 7. Outcome breakdown reporting (2026-08-07 Power Kiosk audit, Gap 3)

- [ ] `eval/harness.py`'s per-row output carries `clarified`/`refused` booleans
  that agree with the row's actual `outcome` (`Outcome.clarification.value`/
  `Outcome.refused.value`) — spot-check a few rows from a real eval run, not
  just the unit tests.
- [ ] `report.outcome_rates(population)` returns `correct`/`clarified`/
  `refused` rates that sum to the population (allowing for `Measured.unmeasured`
  on rows missing a field) — this is offline-report-only, no UI surface to
  check.

## 8. Schema-term leak guard on `ask_user` (2026-08-07 Power Kiosk audit, Gap 2)

- [ ] `find_schema_leak()` unit tests still pass (dotted path, snake_case,
  camelCase, and the documented false-positive case: a camelCase *proper noun*
  like a customer name is indistinguishable from a real leak by design).
- [ ] **Not yet observed live**: get a real model to draft a clarification
  question that references a raw column/table name, and confirm the backend
  log shows `ask_user rejected: ...` before the turn pauses, and the *next*
  question the user actually sees is the rewritten, plain-language version.
  Forcing this naturally is hard — if you hit it while testing something
  else, that's the first live confirmation; don't go out of your way.

## 9. Assumption self-report (2026-08-07 Power Kiosk audit, Gap 1)

- [ ] Backend: `state_assumption` is bound (6 tools total — confirm against
  ADR 0005 §3.5, not just a hard-coded count) and a turn that calls it lands
  the text, verbatim, in `answer["assumptions"]`.
- [ ] **UI, not yet observed live**: an `answered` outcome whose `assumptions`
  array is non-empty renders the "Assumptions" block in `answer-card.tsx`
  (`ListChecks` icon + bullet list) — ask a question with a plausible-but-
  unstated interpretation gap (e.g. two tables that could both be "the"
  answer) and see whether the model states an assumption instead of asking.

## 10. Chat UI retarget to v2's answer/interrupt contract (2026-08-07)

- [ ] `refused` and `capped` outcomes render the right badge + explanation
  text (`reliability-stamp.tsx`/`answer-card.tsx`).
- [ ] A live `ask_user` interrupt renders the `ClarificationPrompt` (question,
  why, freeform input, defer button) — **this exact path was silently broken**
  until 2026-08-07 (`clarificationRequestSchema` required a `tier` field v2
  never sends; `safeParse` failed silently, interrupt rendered as nothing).
  If a clarification ever shows a blank screen again, suspect a schema/wire
  mismatch first — check the raw `GET /threads/{id}/state` JSON against the
  zod schema by hand before assuming it's a new bug.
- [ ] Submitting a clarification answer resumes the turn to a real final
  answer (`stream.submit(undefined, { command: { resume } })`).

## ⚠️ Known out of scope: v1-only admin/corpus surface, not ported to v2

The `/corpus` page (`AssumptionsLog`, `ConflictsPanel`, `ClarificationToggle`,
the elicitation wizard) and its backing routes —
`/corpus/conflicts`, `/corpus/assumptions`, `/clarifications`,
`/elicitation/candidates`, `/settings/allow-user-clarification` — are v1-only.
v2's `routes.py` never implemented them; `/capabilities` hard-codes
`can_edit: False`, which is *why* `ClarificationToggle` renders nothing (it
bails out on `!canEdit(caps)`) rather than a bug in the toggle itself. Testing
these against the v2 backend will reliably 404/blank — that's expected until
someone does the (separate, sizable) work of porting v1's admin API surface
to v2. Don't file these as regressions from the 2026-08-07 loop; they predate
it and are untouched by it. Skip section testing on `/corpus` entirely until
that porting work is scoped and started.

## 11. Cross-cutting

- [ ] Full suite green (`uv run pytest`) and all five conformance lints clean
  (`uv run python tools/check_imports.py`,
  `check_citations.py`, `check_file_length.py`,
  `check_one_implementation.py`, `check_measurement_locality.py`).
- [ ] No DetentAI feature above writes to the corpus except through
  `corpus/drafts.py`'s `submit_draft`/`approve_draft` — grep for any direct
  `corpus.store.write` call outside that module before merging a change to
  any of the four phases.
- [ ] **Local dev env**: `.env` sets `GOVERNED_BI_EMBEDDING_MODEL`. Confirm it
  by asking one question and reading `record.facet_hits[*].channels.semantic`
  on the turn — `"ran"` is configured, **`"not_configured"` means the semantic
  channel is off**. Without it, retrieval is lexical-only: whether a question
  routes at all depends on whether the model happens to emit a query string
  that literally matches a column name, so the same question answers once and
  refuses the next time, and the symptom is a wall of `no_schema_matched`
  refusals that reads as a serving bug.

  **This item used to say `.claude/launch.json`'s backend config sets it, and
  that was wrong in a way that cost half a day on 2026-08-18.** Two reasons,
  either of which is fatal: the entry actually used for verification
  (`merge-api`) sets no env at all, and even the entry that does export them
  cannot deliver them — `langgraph.json` declares `"env": ".env"`, which
  overrides the process environment, a fact `launch.json`'s own
  `_why_merge_entries` note records. So the setting has to live in `.env`.

  Consequence for the record, not just for setup: this engine had **never**
  had the variable set, so anything measured on this configuration measured a
  degraded system. See `docs/handoff-claim-audit-2026-08-18.md` finding 3.
