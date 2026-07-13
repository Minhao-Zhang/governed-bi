# Three-Arm Experiment: Results (v1)

_Recorded 2026-07-13. Companion to [three-arm-experiment-plan.md](three-arm-experiment-plan.md).
The raw run artifacts under `runs/` are **git-ignored and ephemeral** — this doc is
the durable record of the numbers and, more importantly, the mechanism._

## TL;DR

- **Curation's value scales with how much the schema hides meaning.** On a DB with
  FK-columns disguised as values (`ice_hockey_draft`) curation ~4×'d accuracy; on a
  self-describing schema (`cs_semester`) it added nothing and a thin corpus slightly
  hurt. This is the clearest evidence for the thesis so far — visible in the SQL, not
  just the point estimate.
- **Not caused by obfuscation.** Both DBs got the same class of obfuscation
  (English → identity rename, decoy/trap injection); intensity is symmetric.
- **n is tiny (17 / 23) and the baseline is nondeterministic.** Treat the
  percentages loosely; trust the mechanism. Scaling N is the next blocker.

## Setup

- Arms: **A1** no-layer (raw schema names+types to the LLM, no corpus) · **A2**
  deep-agent curator over all train `(question, gold SQL)` pairs · **A3** A2 + a
  Simulated SME answering the curator's clarification questions.
- DBs: `cs_semester` (train 90 / test 23) and `ice_hockey_draft` (train 67 / test 17),
  on the **`pg_rename_decoy`** Postgres instance (schema per DB).
- Model `gpt-5.6-sol`; curator ran the real deep agent (`skip_agent=false`).
- Grading: self-contained hash compare (`eval/hash_grade.py`), normalization verified
  **byte-for-byte** against the reference `BIRD-Data-Obfuscation/pipeline/_db.py`, plus
  a per-prediction `execution_match` crosscheck (agreed 1.0) and a 5/5 gold self-check.
- `grade_semantic_failures=true` (deliver-and-grade §6): L3–L5 / execution / coverage
  exhaustion returns the best-effort SQL as `unverified`/`fenced_raw`; L2 policy +
  refuse-gate stay hard refusals.

## Numbers (execution accuracy, lenient; graded re-run)

| DB | A1 | A2 | A3 | A2 refusal |
|---|---|---|---|---|
| cs_semester | 0.348 | 0.261 | 0.435 | 0.0 |
| ice_hockey_draft | 0.118 | 0.471 | 0.471 | 0.0 |

(Earlier pre-§6 run, for context: cs_semester A2 0.391 with a 0.30 refusal rate;
§6 removed the refusals but the EX gap vs A1 remained.)

## Findings

### 1. Schema deceptiveness is the dominant driver (the ice_hockey lift)

`ice_hockey_draft.PlayerInfo.height` and `.weight` are integer **foreign keys** into
`height_info`/`weight_info` lookup tables — they *look* like literal values. The A1
prompt dumps only physical names + types (no descriptions), so given `height: integer`
the model can't tell it's an FK; it doesn't error, it **hallucinates a flat table**.
Example — *"weight in kg of Tony Martensson"*:

- **A1:** `SELECT weight_kg FROM player_biographical_profile WHERE full_name='Tony Martensson'`
  — a table + columns that exist nowhere (not even in the decoy manifest).
- **A2:** `... FROM PlayerInfo JOIN weight_info ON PlayerInfo.weight = weight_info.weight_id ...`
  — correct.

**All 6** of ice_hockey's A1-wrong→A2-right flips are this pattern; the curated corpus
holds exactly the two non-obvious joins (`PlayerInfo↔height_info`, `↔weight_info`) at
confidence 1.0. That join-discovery is the entire lift.

`cs_semester`'s schema is transparent (`student_id`, `course_id`, …); A1 infers those
joins from the `_id` suffix on the first try, so curation adds nothing it didn't have.

### 2. The cs_semester regression is a coverage gap, not a bad join

The 4 A1-right→A2-wrong losses were **not** wrong joins (the correct joins are in the
corpus at 0.99). The generator **declined** (couldn't map the question to a covered
asset) and deliver-and-grade forced a best-effort answer — which degenerated into no-op
stubs (`SELECT NULL::text WHERE FALSE`) or wrong-column substitutions
(`grade` instead of the evidence-specified `sat`). The corpus was **metrics-heavy with
no asset for ad-hoc "list names filtered by a literal" queries**. The SME pass (A3)
patched exactly these coverage gaps, recovering all four → cs_semester's best arm
(0.435). So the regression is a fixable curator **asset-mix** problem.

### 3. Obfuscation intensity is ruled out

Decoys are symmetric (2 decoy tables + 4 decoy columns each); traps are if anything
higher for cs_semester (15 trap cols / 2 tables) than ice_hockey (12 / 2). A1
decoy-touch is negligible on both (~1–2 / test set). ice_hockey's A1 failures are
schema **hallucination**, not decoy contamination.

### 4. §6 deliver-and-grade worked as intended, honestly

It erased the refusal artifact (cs_semester A2 refusal 0.30 → 0) but did **not** erase
the EX gap: the forced best-effort answers on cs_semester were 7/7 wrong. Under an
honest grade the residual A2 < A1 is genuine generation quality under a thin corpus, not
scoring unfairness. (This corrected an earlier hypothesis that §6 would close the gap.)

## Threats to validity

- **N is tiny** (17 / 23). A couple of flipped questions shifts a percentage
  materially. The ice_hockey lift (6 identical join-discovery flips) is a repeatable
  *mechanism*, which is why it's trusted more than the raw deltas.
- **Baseline nondeterminism is loud** — `ice_hockey` A1 moved 0.235 → 0.118 across
  runs (the no-corpus control drifting by a full question). Single seed.
- **Grader is the self-contained hash compare**, not the reference `grade_offline_eval.py`
  (validated equivalent on a sample, but never run head-to-head at full scale).
- **DB choice is confounded with the finding** — these two DBs happen to sit at
  opposite ends of the schema-deceptiveness axis; that's the point, but it means the
  result is about *these schemas*, not curation in general, until more DBs are added.

## Methodological lessons

1. **DB selection is a first-class experimental variable.** Curation's measurable value
   scales with schema opacity (cryptic names, FK-lookups, no textual affordance) — which
   is what real enterprise schemas look like. Transparent academic schemas systematically
   understate the moat. (Same lesson as "don't run on the un-obfuscated DB," one level
   deeper.)
2. **Grade honestly, then read the SQL.** The point estimates at this N are noisy; the
   per-prediction SQL diffs are where the real signal lives.

## Bugs found & fixed during the experiment (for the record)

- Seed join extraction returned the SQL **alias** (`T1`) instead of the physical table,
  so alias-heavy gold SQL produced zero usable seed joins — fixed in `curator/seed.py`.
- `grade_semantic_failures` was **not loadable from TOML** (`load_settings` didn't read
  it) — it had only ever been on in the eval harness, never in serving. Added to the
  `[runtime]` override keys in `config.py`.
- Coverage declines produced `no_coverage` refusals with no SQL, so repair-exhaustion
  delivery never fired; a decline now forces one best-effort generate
  (`allow_decline=False`, stamped `coverage_best_effort`).
- Postgres connector: `search_path` pinning + `SET statement_timeout` literal
  interpolation (bind params are invalid in `SET`) — live-only bugs the offline fakes
  missed.

## Next steps (in priority order)

1. **Scale N** — full test split and/or ≥3 seeds with mean±spread — before treating any
   A2−A1 / A3−A2 delta as real. Plumbing, grading, and §6 honesty are in place; power is
   the blocker.
2. Add **more opaque-schema DBs** (and ideally a non-English/renamed DB, with a
   leakage-safe SME brief source) to test the moat where it should appear.
3. Fix the curator **asset-mix coverage gap** (ad-hoc list/filter queries, not just
   metrics) that drove the cs_semester regression.
