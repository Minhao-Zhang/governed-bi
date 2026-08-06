# UtkuAI-on-v2 Manual Test Checklist

Regression checklist for the UtkuAI features ported onto `governed-bi`'s `v2`
branch (`ryan/dev-v2`) — see `utku-ai-v2-porting-spec.md` (Obsidian) for why
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

## 6. Cross-cutting

- [ ] Full suite green (`uv run pytest`) and all five conformance lints clean
  (`uv run python tools/check_imports.py`,
  `check_citations.py`, `check_file_length.py`,
  `check_one_implementation.py`, `check_measurement_locality.py`).
- [ ] No UtkuAI feature above writes to the corpus except through
  `corpus/drafts.py`'s `submit_draft`/`approve_draft` — grep for any direct
  `corpus.store.write` call outside that module before merging a change to
  any of the four phases.
