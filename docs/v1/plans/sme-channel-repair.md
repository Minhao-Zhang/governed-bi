# Curator intake and the SME clarification channel

> **STATUS 2026-07-31 — LOAD-BEARING. This file falsified a claim in the current plan.**
>
> F7 here is why [rebuild-checklist.md](rebuild-checklist.md) §6.2's first blocker was
> **wrong**. The ledger's "corpus reference-integrity findings" is `eval/index.py:836`'s
> hardcoded generic wording for *any* corpus-validation finding; the actual finding, verbatim from
> `summary.json`, is `always-note-budget []: always-note summaries total 5178 characters; maximum
> is 2000` — a **per-turn** budget summed over a 57-schema pooled corpus, whose worst single
> schema is 1591/2000. §6.2 has been corrected.
>
> Still to migrate: F6's noise floor → §6.3. 31 rows got byte-identical prompts in both arms
> (equal `context_hash`); of the 26 gradeable ones, EX was 0.4231 vs 0.5000 with 4 flips —
> **sampling randomness alone gives a 15% disagreement rate**. Without `--replicate`, a new run
> reads SME as "not detected", not "no effect" · §5 item 4 + F4: `decoy_touch` is saturated at
> 1/1351, so SME can only show up in the 78 schema-pick and 179 SQL-value errors → an SME
> success criterion in §6.2/6.3 · optionally F2's elimination evidence (Phase-A budget vs
> questions-raised correlates **−0.353**; `works_cycles` is the largest schema, budget 339, 1583
> tool calls, **zero** questions raised) → `docs/prompt-experiments.md`, which took the
> conclusion but not these two counter-examples.
>
> Absorbed already: three of its four fixes shipped; the three-db zero-fold finding is §6.2's
> remaining blocker.

The `curated_sme` arm moved EX by −0.2pp on a 1351-question test run. This plan
records why, from evidence in that run's artifacts, and lists the fixes in the order
worth doing them. The general tracker is [open-work.md](../open-work.md); the run
itself is [20260730T034522Z](../experiments/20260730T034522Z-test-ladder-fixed2-results.md).

Every number below was recomputed from
`runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z/` rather than
copied from the shipped analysis. Section 7 lists where the two disagree.

## 1. What the run showed

Test split, 57 schemas, 1351 questions per arm, Claude-Opus-4.8, `curator_phase_a=v2`.

| arm | EX | EX_gradeable | decoy | refuse | crash |
|---|---|---|---|---|---|
| baseline | 0.392 | 0.418 | 0.1150 | 0.019 | 0.000 |
| seeded | 0.470 | 0.499 | 0.0477 | 0.019 | 0.000 |
| curated | 0.585 | 0.618 | 0.0007 | 0.006 | 0.000 |
| curated_sme | 0.583 | 0.618 | 0.0007 | 0.006 | 0.000 |

The two ascending steps are large and survive both a Holm correction and a
per-database sign test. The SME step is net −2 questions out of 1351, p=0.928, with 22
databases better against 21 worse (p=1.0). That is a null result rather than a small
win.

Twin-free EX (1085 questions, complete stamp coverage) is 0.404, 0.484, 0.591, 0.594
across the four arms, so the ladder is +18.7pp against the +19.3pp headline. The SME
null holds in both strata.

## 2. The channel, as built

Verified against code, because the fix depends on it:

- The SME cannot write. `build_sme_agent` "holds no write tools: it cannot touch the
  corpus" (`curator/sme.py:345`). It gets a read-only `run_probe_query`.
- Its brief is BIRD's column-description CSVs for the un-obfuscated dataset, all
  deduplicated evidence hints, and train question text (`curator/sme.py:200-266`). The
  curator's corpus is not in it.
- The curator raises `/clarifications.jsonl` during its own build
  (`curator/pipeline.py:947`).
- A second curator pass folds the answers with write tools, `fold_mode: agent` on 54
  of 57 schemas.

So the SME never revises the curator's work and never sees it. The curator asks, the
SME answers, the curator revises itself. That is the intended design and it is what
runs.

## 3. Why the channel produced nothing

### F1. The curator only ever sees the first 40 train pairs

`_render_train_batch` truncates to `items[:40]` and appends a count of what it dropped
(`curator/pipeline.py:78-88`). It is called once, with no batching loop, and the step
budget uses `min(len(train_items), MAX_RENDERED_PAIRS)` under the comment "the agent
cannot work a pair it was never shown".

Every one of the 57 schemas has more than 40 train questions: 49 at the smallest, 86
median, 306 largest. Counting unique `evidence` hints:

| | |
|---|---|
| unique evidence hints across train | 4900 |
| reaching the curator's prompt | 2094 |
| never reaching it | **2806 (57.3%)** |

Per schema the curator sees a median 47.1% of the available hints. The SME brief takes
all of them uncapped, and `curator/sme.py:252` explains that the cap was removed there
deliberately, because "dropping any starves the SME of exactly what it needs". The
same cap is still live on the curator, which is the arm that produces +11.5pp.

This is independent of the SME layer and is the largest single lever in this plan.

**Fixed 2026-07-30, and the fix found something this section had wrong.** The 40-pair
slice was not pure loss. It was also, accidentally, the only bound on **render size**,
and at that job it was already failing:

| | |
|---|---|
| train pairs whose `sql_rename` exceeds 2000 chars | 48 of 5392 |
| largest single pair (`video_games/train_3491`) | **2,527,929 chars**, roughly 630k tokens |
| worst first-40 render today (`language_corpus`) | **323,403 chars**, re-sent every turn of the loop |
| schemas whose full render would exceed 60k chars | 19 of 57 |

BIRD-Obfuscation rewrites some gold as a literal `VALUES` list, which is where the
multi-megabyte pairs come from. Delivering every pair without a size guard would have
traded a coverage bug for a context overflow on at least one schema. So the fix adds a
per-pair `MAX_RENDERED_SQL_CHARS` clip at 2000 with an announced marker; those clipped
golds are materialised constants naming no table or column, so the curator loses
nothing by not seeing them in full.

Result: intake goes from 42.7% to 100% of unique evidence hints while the widest single
batch render falls to 43,848 chars, 7.4 times below today's worst case. The cost is
**147 Phase A invocations across the 57 schemas against 57 today** (24 schemas at two
batches, 33 at three), bounded by `MAX_PAIR_BATCHES = 3`. `plan_pair_batches` always
partitions the whole split: when the batch count and the per-batch target conflict, the
count wins and batches widen, so coverage is never what gets traded.

### F2. The curator barely asks, and neither a cap nor a budget explains it

186 clarifications across 57 schemas: median 3, mean 3.3, max 7, and three schemas
raised none. Against roughly 104 columns per schema.

No cap exists. `seed_gap_clarifications` carries `limit=20` but runs only when
`seed_ledger_if_empty=True`, an opt-in for `--skip-agent`
(`curator/pipeline.py:1083`); this run was `ledger_source: agent` throughout. Nothing
truncates the ledger on read.

The budget does not explain it either. Phase A budgets ran 65 to 339, median 83, with
zero build errors and no schema hitting the recursion cap. The correlation between
Phase A budget and questions raised is **−0.353**, slightly negative. The clearest
case is `works_cycles`: the largest schema in the pool, the widest budget at 339, 1583
tool calls spent, and zero questions raised.

What remains is the prompt. `curator/pipeline.py:325-337` states an explicit priority
order under "If you cannot do everything, this is the order that matters, most first":
marking suspect columns is 1, describing tables and columns is 2, raising
clarifications is 3. The agent spends its budget on 1 and 2 and complies with the
instruction to drop 3. The phrase "genuine unknowns" invites further conservatism.

So the channel is narrow because the prompt ranks it as the first thing to skip, not
because anything in the design bounds it.

### F3. The questions are aimed where the SME cannot answer

Of the 186 questions, 83 (44.6%) describe a duplicate-or-decoy-shaped anomaly: a table
with the same row count as another, columns shuffled out of alignment, or a table no
gold query references. 85 answers (45.7%) disclaim knowledge of the object being asked
about:

> **Q:** `country.region_state` is non-null for ~50k rows but matches `country.state`
> in only 1489 of 51001. What does it represent?
>
> **A:** I don't recognise `country.region_state`. The `country` table I have
> documented has only three columns [...]

Each half of the exchange is behaving correctly and the pairing still fails. The
curator asks about what it cannot explain; what it cannot explain is largely the
decoys BIRD-Obfuscation injected; the SME's only knowledge source documents the
un-obfuscated dataset, where those decoys do not exist. Tuning cannot fix that,
because it follows from where each side gets its information.

The remaining 101 answers are substantive and good. They confirm that
`congress.first_name` holds surnames, or that `Air Carriers` is the authoritative
lookup while `air_carrier_id` is a shuffled decoy. Answer quality is not the problem.

### F4. What the channel does establish has no headroom left

"I don't recognise this table" is real information, since it implies a decoy. The fold
acted on it, making 61 `ok` to `suspect` flips. But `decoy_touch` across the ladder
runs 143, 62, **1**, 1. The curator alone already drove decoy contact down to one
question in 1351. The metric this channel most reliably improves was saturated before
the SME round started.

### F5. Every caveat note was truncated and the discarded text was thrown away

| fold output | count | carrier capped? |
|---|---|---|
| new column descriptions | +33 | no |
| new notes | +31 | summary capped at 400 chars |
| `ok` to `suspect` flips | +61 | no |
| new terms | +1 | no |
| new few-shots | +14 | no |

`record_caveats` (`curator/asset_bag.py:995-1072`) clips each SME answer with
`_clip_words(rec.answer, _CAVEAT_NOTE_MAX_CHARS)` at a 400-character ceiling
(`asset_bag.py:274`). The build log totals across the run are `31 caveat notes
recorded, 31 clipped to 400 chars`. A **100% truncation rate**, and the surviving
summaries cluster at 398 to 401 characters.

The loss is avoidable. `NoteAsset` already carries a `body` field for exactly this,
documented at `corpus/schemas.py:405-406` as long form, on-demand only, never embedded
and never always-injected. `record_caveats` does not set it, so the tail of every
answer is discarded rather than parked somewhere free. What falls off the end is the
reasoning that justifies the conclusion.

An earlier draft of this section claimed that roughly 8 of 13 always-notes were being
dropped by the character budget. That was wrong; see F7 for what the evidence actually
shows.

Measured delivery, averaged over all 1351 questions:

| injected per question | curated | curated_sme |
|---|---|---|
| notes | 0.00 | 0.42 (435 rows had one) |
| caveats | 33.83 | 34.65 |
| terms / few-shots / metrics | 3.93 / 2.85 / 5.00 | unchanged |
| context characters | 19537 | 21298 |

The prompt grew 9% and every other carrier stayed where it was.

A register problem points at the same cause. 11 of the 31 notes open conversationally
with "Yes", "Confirmed", or "You're right that", 18 mention the gold SQL, and 7 assert
the gold is wrong or contaminated. Summaries cluster at 398 to 401 characters. That
reads as the fold pasting the SME's reply text into note summaries near a
400-character clip, instead of distilling it into a column description.

### F6. The measured churn is within sampler noise

31 of 1351 rows received a byte-identical prompt in both arms (equal `context_hash`),
26 of them gradeable. On those 26, EX was 0.4231 against 0.5000 with four outcomes
flipping, a **15% discordance rate from nondeterminism alone**. Overall discordance is
115 of 1200, or 9.6%.

The treatment perturbed 97.7% of prompts and produced less disagreement than an
unchanged prompt does. Stratifying agrees:

| stratum | n | curated | curated_sme | delta |
|---|---|---|---|---|
| a note was injected | 398 | 0.6432 | 0.6482 | +0.0050 |
| no note injected | 802 | 0.6047 | 0.6035 | −0.0012 |
| schema pick flipped | 52 | 0.2115 | 0.2500 | +0.0385 |

So "SME rewrote half the queries for net +1" should not be read as the SME behaving
like a coin flip. Most of that rewrite volume is the sampler.

### F7. The run was disqualified by a validator measuring the wrong population

`ALWAYS_NOTE_TOTAL_CHARS_MAX = 2000` (`corpus/validate.py:78`) is a **per-turn prompt
budget**: `apply_always_budget` admits always-notes into one analyst turn up to 8 notes
and 2000 characters (`analyst/note_inject.py:191-227`). But `_validate_corpora`
(`eval/harness.py:125-140`) calls `validate_corpus` once per arm on that arm's entire
pooled corpus, which in the data-lake driver is 57 schemas at once
(`eval/run_datalake.py:4505`). A per-turn budget is therefore summed across a whole
data lake.

The arithmetic confirms that is exactly what happened:

| | |
|---|---|
| reported finding | `always-note summaries total 5178 characters; maximum is 2000` |
| pooled across all 57 schemas | 5178 characters in 13 always-notes |
| worst single schema (`sales`) | **1591 characters in 4 notes** |
| schemas over budget | **none** |
| dropped over budget, per build log | **0** |

These notes are scoped `schema:<name>` by `record_caveats`, so only one schema's notes
are ever licensed for a turn. Per-schema is the only scope at which the budget means
anything, and no schema breaches it.

That single finding propagated into `corpus_validation.curated_sme.finding_count: 1`
and became one of the two `not_quotable_because` reasons for the whole run. So a paid
1351-question experiment was marked unquotable on a measurement artifact.

A second inconsistency sits next to it. `validate.py:239` counts only empty-scope notes
against the 8-note cap, while serve-time `apply_always_budget` counts every always-note,
and `note_inject.py:204-215` documents that the empty-scope-only reading was already
established as a bug. Validator and serve still disagree about the same cap.

## 4. What is not broken

Worth stating so no one fixes it:

- SME isolation from the corpus is correct. Showing the SME what the curator wrote
  would turn an independent source into an endorsement of the curator's guesses.
- Answer quality is fine. 101 of 186 answers are substantive and correct.
- Structural gold twins are measured rather than removed, by decision
  (`eval/leakage.py:21-23`). 115 of 1200 scored questions have one, and the ladder
  holds twin-free. This is a property of the benchmark, not a fault in the run.

## 5. Fixes, in order

0. **Fix the pooled always-note validation (F7).** Evaluate the budget at the scope
   where it binds instead of summing a 57-schema pool against a per-turn ceiling, and
   align the note-count check with serve-time behaviour. Listed first because it is the
   cheapest item here and it is what disqualified a paid run.
1. **Batch the curator's train pairs.** Remove the effective 40-pair ceiling in
   `_render_train_batch` so all train pairs reach the curator across batches, with the
   step budget scaled to the pair count actually shown. At present 57.3% of available
   evidence never reaches the arm that produces the +11.5pp step. This needs no
   protocol or prompt change and carries the largest expected lift.
2. **`curator_phase_a=v3`: change what gets asked and how willingly.** Promote
   clarifications above "describe tables and columns" in the priority list, or give an
   explicit quota. State which questions are worth asking, namely business meaning and
   measure definition, which BIRD's column docs can answer. State that statistical
   anomaly detection on row counts is not worth an SME question. Register it as a
   falsifiable variant against v2.
3. **Keep the truncated tail (F5).** `record_caveats` clipped 31 of 31 answers to 400
   characters and set no `body`. Park the full answer in `body` and leave the
   400-character summary cap alone; it protects a shared per-turn budget and is
   defensible. Routing more of the fold into column descriptions and terms is still
   worth doing, since those have no ceiling, but it is a preference rather than a
   repair now that F7 shows nothing was actually dropped.

   One correction to the framing above, found while implementing it: `body` is free
   only for the `always` half. `note_inject.py:271` charges `len(summary) + len(body)`
   against the shared note budget for an `on_match` note, `:283` passes the body
   through, and `format_note_lines:407-408` renders it. That is deliberate progressive
   disclosure rather than a leak, but on this run 18 of the 31 caveats are `on_match`
   and 13 are `always`, so 13 get the tail for free and 18 get it charged on the turns
   their trigger fires. Making the `on_match` half free too would be a change in
   `note_inject.py`, not in the fold.
4. **Stop aiming at decoy avoidance.** It is saturated at 1 of 1351. If the SME layer
   has value it will show up where `curated` still fails: 78 schema-pick errors and
   179 SQL-value errors in the gradeable pool.

Items 1 and 2 are independent and can be measured separately. Item 2 without item 1
still leaves the curator reasoning from under half the evidence. Item 0 is independent
of all of them.

## 6. Prerequisites before any of this is quotable

- **A noise floor.** All six comparisons in this run report significance without
  knowing what the run could resolve. F6 gives an informal 15% discordance estimate on
  26 rows; a replicate arm would turn that into a real floor. Until then the SME result
  is "no effect detected" rather than "no effect".
- ~~**Pre-register the headline.**~~ **Done 2026-07-30.** `metrics.HEADLINE_RATE` now
  names `ex_no_twin`, `ex_lenient` is explicitly demoted, and a test enforces that
  exactly one rate claims the word. X11 is closed. The hazard was real rather than
  theoretical: on `ex_no_twin` the SME arm slightly exceeds `curated` (0.594 against
  0.591) while on `ex_lenient` it sits slightly below, so the choice of metric flips
  the sign of a reported delta.
- ~~**Resolve the commit.**~~ **Recorded 2026-07-30, and it cannot be resolved here.**
  `3f599b6` is in none of the 248 commits reachable from any local ref, and
  `git ls-remote origin` publishes only `main` at `49536ac`. Worse than a missing
  commit: the run's own label is `HEAD=3f599b6 +C11`, so the working tree carried an
  uncommitted change on top of that commit and recovering the commit still would not
  recover the code that ran. The caveat is now on the results doc. Quote those numbers
  as measured, not as reproducible.
- **Know which disqualification survives.** The ledger gave two
  `not_quotable_because` reasons. F7 shows the first, the corpus reference-integrity
  finding, is a false positive. The second stands on its own: `curated_sme` folded
  nothing on `professional_basketball`, `synthea` and `works_cycles`, so the SME delta
  on those three schemas is not a measurement. Fixing F7 does not make this run
  quotable, it removes one of two blockers.

## 7. Corrections to the shipped analysis

`docs/experiments/20260730T034522Z-curated-sme-error-analysis.md` §11 states that of
the columns the SME round touched, "only 9 had an actual verdict flip". Diffing all 656
shared tables and 5947 shared columns gives **61** `ok` to `suspect` flips, and zero in
the reverse direction. The 61 is corroborated by `summary.json`'s
`n_columns_suspect: +61`. That section's argument still holds in direction, since prose
edits outnumber decisions about 5:1 rather than 72:1, but planning against "9" would
understate how much the fold actually changed.

The prose count depends on where you draw the line, so both figures belong on the
record: **326** columns changed only their description or confidence, and **404** if
reliability-note text counts as prose too (77 columns changed only
`reliability.note`, one only `role`). 465 columns differ in some field. The shipped
doc's "656" was the shared *table* count, which is also why its own category table
summed to 88 rather than 656.

Three errors made while producing this plan, recorded so they are not repeated:

- F1 called the 40-pair render cap pure loss. It was also the only bound on render size,
  and it was already failing at that: the worst first-40 render is 323,403 characters and
  the largest single train pair is 2.53 MB. Removing it without adding a per-pair size
  clip would have replaced a coverage bug with a context overflow. Reading a limit as
  serving only its stated purpose is the mistake; a cap that happens to bound two things
  needs both replaced.
- The first draft of F5 said roughly 8 of 13 always-notes failed to reach the prompt on
  every turn, reading the 5178-character validator finding as a real per-schema breach.
  It is not. The build log reports `0 dropped over the always-note budget`, and the
  worst schema sits at 1591 of 2000 characters. The mistake was accepting a pooled
  aggregate as a per-turn quantity, which is the same confusion the validator itself
  makes (F7). The real defect on that path is truncation, not dropping.

- Phase B's budget is `30 + 3 * len(answered)` (`curator/pipeline.py:1207`), derived
  from the clarification count. Correlating it against that count returns r=1.000 and
  means nothing. The Phase A correlation, which is the one that bears on F2, is −0.353.
- The notes and suspect flips are written by the curator's fold pass, not by the SME.
  Attributing them to the SME misplaces where the fix belongs.

## 8. Tracker entries to add

- Curator intake ceiling: 40 rendered train pairs against a 49 to 306 question pool,
  with 57.3% of evidence unseen (F1).
- Clarification volume governed by prompt priority rather than schema need (F2).
- Clarification targeting structurally mismatched with the SME's knowledge source (F3).
- Caveat notes truncated at 400 characters with the remainder discarded, 31 of 31 on
  this run, while `NoteAsset.body` sits unused (F5).
- Per-turn always-note budget validated against a pooled 57-schema corpus, producing a
  false positive that disqualified a paid run; validator and serve disagree on the
  note-count cap (F7).
- `eval/refuse_gate.py:71-80` `agent_refuser` passes `settings` to
  `answer_question_agent` with no `run_log_kind` guard, the same defect class as C11 at
  the call site C12 already flags. Fold into C12 rather than opening a new item.
