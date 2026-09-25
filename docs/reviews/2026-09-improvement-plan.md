# Improvement plan: from a correct engine to a useful one

Status: revision 2, 2026-09-25, after an independent review of revision 1
Audience: engineers joining or reviewing governed-bi
Scope: the eight weeks to 2026-11-20
Companion: [2026-09-improvement-milestones.md](2026-09-improvement-milestones.md) is the
delegated work, milestone by milestone. This file is the reasoning.

This repository is public. This document covers the project and how the team works on it.
Individual development goals are handled in one-to-ones and are not recorded here.

## 1. Why this plan exists

An outside read of the code, git history and eval data found an engine whose hard parts are
well built: execution-time SQL governance, read-only database sessions, eval provenance on every
record, and a paired-statistics habit most projects never reach. It also found a project that
writes down far more than it acts on, measures a configuration it no longer ships, and has no
user.

The plan has three aims:

1. **Make the served configuration the measured one**, and make the headline describe it.
2. **Spend the next accuracy effort where the failures are**, with a protocol that cannot fool
   us.
3. **Close the cheap, high-value items the backlog already names** before adding anything new.

Nothing here undoes the governance work. It is the product's differentiator.

## 2. Where we are

Measured on `main` at `482b9d6`. Every figure has a command in milestone M0.

| Area | Measurement |
|---|---|
| Size | ~154k tracked lines: `src` 44k, `tests` 51k, `tools` 11k, `ui` 22k (excluding the lockfile), `docs` 25k |
| History | 569 commits in 11 weeks, one human author; 91% carry an AI co-author trailer (478 Claude, 38 Cursor) |
| Tests | 2,242 passed, 31 skipped, 10 xfailed, 0 failed, 3m48s locally; CI about 2.5 min |
| Types | `mypy` over `src/governed_bi`: 45 errors in 16 files; CI checks a named subset |
| Prose | ~36% of `src` lines are comments or docstrings |
| Config | 31 distinct `GOVERNED_BI_*` environment variables read in `src` |
| Gates | 12 `tools/check_*.py`: 6 per push, 1 nightly, 5 manual; plus `govern_bench` per push and 84 mutations nightly |
| Branch protection | none on `main` (`docs/open-work.md` §6.6), so every gate reports and none blocks |
| Accuracy | 57.0% to 67.6% execution accuracy (EX) over 7 full arms of a 1,351-question test split drawn from BIRD train, 57 databases |

## 3. Findings

### A. The headline describes a configuration that no longer ships

Every quoted number comes from the v4 arm: Claude Opus 4.8 through a proxy, 118 commits before
`482b9d6`. The shipped configuration and the eval driver's default are now `gpt-5.6-luna`.
`register/arms.toml` itself says no pairing between the old arms and the new engine is sound,
and the new baseline arm (`luna_v4_embed`) is registered but has not been run. There is no
current accuracy figure for what a user would get.

### B. The served path is not the measured path, and the difference is large

The deterministic guard rules ran on no served turn until 2026-09-20. The scope gate runs on
every served turn and on no measured one. Measured by the repository's own shadow replay, it
refuses 180 of 1,351 benign benchmark questions (13.3%) and would turn EX 0.676 into 0.581
(`docs/open-work.md` §6.10). A user of the served app gets roughly 9.5 points less than the
headline, before the model change in finding A is counted.

This is one instance of the repository's recurring defect, which its own backlog names in §3.10:
**declared but not wired**. A property is asserted where it is configured and nowhere it
executes. More tests of the declared side do not catch it; tests on the served path do.

### C. The failure breakdown exists; the next level down does not

`docs/failure-modes.md` already partitions v4's 438 failures:

| Bucket | n |
|---|---:|
| answered wrong, full table coverage | 259 |
| answered, frozen-literal gold (unwinnable) | 75 |
| capped at five attempts | 49 |
| answered, incomplete table coverage | 31 |
| refused | 20 |
| clarification | 4 |

Retrieval accounts for 71 failures by coverage (16%), including 19 of the 20 refusals. The
largest bucket, 259 genuine semantic errors, has no finer taxonomy, so nobody can say which
kind of SQL mistake to attack first. That is where labelling effort pays.

### D. The instrument has known gaps that bias every comparison

- The grader compares floats exactly and coerces types (`open-work` §6.4): `1/3` against
  `0.33333333333333337` is wrong, `'00123'` against `123` is right. Aggregation form is exactly
  what prompt changes move, so this biases prompt experiments.
- Cost per arm is not in the artifact (§3.5). A full v4 arm used about 74M input tokens.
- Run-to-run disagreement is 12.7% of questions (§3.12). Two runs of one configuration differ
  on about 172 questions, so a single-run comparison is close to noise.
- ~4 points of any score are output *shape*, which `README.md` already states, and v5 was a
  deliberate ablation that priced it.

### E. The backlog is honest, long, and acted on too slowly

`docs/open-work.md` runs to 1,517 lines. It is accurate and specific, and several of its items
are cheap: branch protection is one setting, recorded "written down rather than done". There is
no logging module, and an audit row can be dropped silently (§6.1). 234 tests are one
near-tautological assertion (§6.7). The pattern is careful diagnosis followed by more diagnosis.
This plan works mostly *from* that backlog rather than beside it.

### F. Size and prose are high for a project with no user

Tests outweigh source, 12 gates police the repository itself, and a third of `src` is prose.
The prose carries real decisions: the 2026-09-18 outside review, run on a comment-stripped
mirror, re-filed four settled decisions as defects. What bloats it is history (dates, audit
IDs, earlier wrong versions) that belongs in git. `AGENTS.md` now states that rule. Some comments
also cite `AGENTS.md` rules that no longer exist in it (`tools/mutate.py:3`,
`eval/provenance.py:12`).

## 4. Principles for the eight weeks

1. **Served is measured.** A number we quote describes a configuration a user can run. A check we
   rely on is tested on the path that serves users.
2. **Work the backlog before growing it.** Prefer closing an `open-work.md` item to opening a new
   one.
3. **Freeze the meta-layer.** No new `tools/check_*` gate, conformance test, ratchet, mutation,
   knob or ADR unless a milestone asks for it or it pins a defect we actually hit.
4. **Delete is a feature.** A PR that removes something with no measured effect gets reviewed as
   fast as a feature.
5. **Pre-register, split, replicate.** An accuracy change states its expected effect and keep
   rule before running, is developed on one half of the databases and confirmed on the other, and
   is run at least twice.
6. **Comments record decisions; git records history** (`AGENTS.md`).

## 5. Workstreams

The milestones document breaks these into delegable units with acceptance criteria.

| Workstream | What | Milestones |
|---|---|---|
| W1 Land safely | Branch protection, named reviewers, budget, baseline | M0 |
| W2 Served is measured | Fix the scope gate's false refusals; test governance on the served path | M1 |
| W3 Instrument and baseline | Grader tolerance, cost in the artifact, the new baseline run twice, an honest headline | M2 |
| W4 Error taxonomy | Label the semantic failures of the new baseline, development half only | M3 |
| W5 Accuracy round 1 | Two pre-registered hypotheses from W4, confirmed on the held-out half | M4 |
| W6 Operability | No silent audit drops; a readiness probe | M5 |
| W7 Rigor gaps | Whole-package types in CI; UI component tests | M6, M7 |
| W8 First user | One pilot user, safely, for four weeks | M8 |
| W9 Cleanup | Retire the tautological tests, fix stale citations, audit config and gates, park unused subsystems | M9 |
| W10 Retrospective | Decide the next eight weeks | M10 |

## 6. What stays untouched

- The SQL governance stack in `src/governed_bi/govern/` and its adversarial suite. Changes there
  need the governance reviewer.
- Read-only sessions and statement timeouts in `src/governed_bi/datasource/`.
- The `threads.update` denial in `src/governed_bi/api/auth.py`.
- Eval provenance, the arm register, the power gate, and the paired-comparison tooling. M4 uses
  them as they are.
- The test suite, except for the specific retirements in M9.

## 7. How the team works on this

- **Named people.** Every milestone has an assignee and a reviewer who is a different person, and
  a governance reviewer for anything touching `govern/`. Named at M0; nothing starts without them.
- **Weekly prioritisation review**, 30 minutes: for each planned item, which finding, user
  behaviour or security property does it move? Items without an answer wait.
- **Paired walkthroughs** every two weeks: the assignee and one engineer walk through a core
  module together (`govern/check.py`, `govern/pipeline.py`, `serve/nodes/agent_core.py`,
  `serve/graph.py`). The goal is that at least two people can change each one.
- **PR shape** for everyone: one intent, under ~400 changed lines of non-test code, a short commit
  message. The investigation goes in the PR description.
- **AI assistance** for everyone: welcome. Trim generated prose before review, and be ready to say
  what you changed in the draft and why.

## 8. Scoreboard

| Metric | Today | Target at 2026-11-20 |
|---|---|---|
| EX of the **shipped** configuration, mean of ≥ 2 runs | not measured | measured, with its run-to-run spread |
| EX of the shipped configuration **with the served guard** (shadow replay) | not measured (v4: 0.581) | within 2 points of the permissive figure |
| Scope-gate false refusals on the 1,351 | 13.3% | < 3% |
| Semantic failures with a taxonomy code (development half) | 0 | all sampled rows, Cohen's κ ≥ 0.6 |
| Accuracy hypotheses decided on the held-out half | 0 | 2 (kept or rejected) |
| `main` branch protection | off | on, with required checks |
| Silent audit-row drops | possible | recorded |
| `mypy` errors, whole package | 45 | 0, enforced in CI |
| UI component tests | 0 | chat, answer card, clarification |
| Weekly active pilot users | 0 | 1 for 4 consecutive weeks |

## 9. Open questions for the review

1. Is the multi-database setting (57 schemas per question) the product, or a benchmark artefact?
   The answer decides how much retrieval work is justified after this round.
2. Is BIRD dev worth a corpus build, for a figure comparable with published work? M2 records the
   decision; it does not run it.
3. What is the model-spend budget for eight weeks? M2 and M4 together need 6 to 8 full arms,
   roughly 450M to 600M input tokens at v4's rate.
4. Which parked subsystems do we delete at M10 if nobody has touched them?

## Revision history

Revision 1 (2026-09-25) called v5 an unexplained regression, counted 56 environment variables
(the count included compiled files), said retrieval was not the bottleneck (based on a substring
check that passed almost every row), and planned the next accuracy round against the v4 arm. An
independent review found these and other errors; revision 2 corrects them and builds on
`docs/failure-modes.md` and `docs/open-work.md` rather than beside them.
