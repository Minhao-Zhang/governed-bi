# Improvement milestones: the delegated work, one milestone at a time

Status: revision 2, 2026-09-25, after an independent review of revision 1
Companion to: [2026-09-improvement-plan.md](2026-09-improvement-plan.md) (the reasoning).
This file says, for each milestone, what to deliver, what not to deliver, how a reviewer checks
it, and when to stop and ask.

## How to read this

Every milestone has the same sections:

- **Goal**: one sentence. A PR that does not serve it belongs in a different milestone.
- **Tasks**: the steps, in order.
- **Deliverables**: the artefacts that exist when the milestone closes.
- **Acceptance**: checks a reviewer runs. All must pass.
- **Out of scope**: work that will look related and belongs elsewhere. A PR that includes it is
  sent back, however good the work.
- **Stop and ask if**: conditions under which you pause and talk to the reviewer.

Sizes are focused working days at normal hours. "Assignee" and "Reviewer" are filled in at M0.

## Rules for every milestone

1. **One branch per milestone**, named `m<N>/<short-name>`. Several small PRs per milestone are
   preferred.
2. **PR shape**: one intent, under ~400 changed lines of non-test code. A purely mechanical PR
   (type fixes, a rename) may be larger if it does only that.
3. **Commit messages**: a subject line and at most one short paragraph. The investigation goes in
   the PR description.
4. **The freeze**: no new `tools/check_*` gate, conformance test, ratchet, mutation entry, knob,
   environment variable or ADR, unless the milestone asks for it or it pins a defect you actually
   hit. A regression test for a bug you fixed is always welcome.
5. **Comments record decisions; git records history** (`AGENTS.md`). In files you touch, write the
   reason in one to three sentences and put dates, audit IDs and "used to" in the commit message.
6. **Protected areas**: `src/governed_bi/govern/`, the read-only and timeout settings in
   `src/governed_bi/datasource/`, and the `threads.update` denial in `src/governed_bi/api/auth.py`.
   Behaviour there changes only in M1, and only with the governance reviewer's approval.
7. **Green on every PR**: `uv run pytest -q`, `uv run ruff check src tests tools`, and CI. A test
   may be rewritten to assert behaviour instead of wording; a test is deleted only where M9 names
   it.
8. **Backlog first**: when a milestone closes an item in `docs/open-work.md`, update that item in
   the same PR (mark it closed with the commit, or trim it). Keep new open items to a few lines in
   the right section of `open-work.md`.
9. **Public repository**: nothing that identifies a pilot user, their data, or anyone's
   performance goes into the repo.
10. **Weekly note** in the team channel, five lines: what closed, what's next, current best EX,
    blockers, one thing you decided not to do.

## Timeline

| Week | Milestones |
|---|---|
| 1 | M0 kickoff; M1 scope gate starts |
| 2 | M1 closes; M2 instrument and baseline starts; M5 operability and M6 types run in the background |
| 3 | M2 closes (baseline runs take wall time); M3 taxonomy starts |
| 4 | M3 closes; M9 cleanup |
| 5 | M4 accuracy round starts; M7 UI tests; M8 pilot starts (needs M1) |
| 6 | M7 closes; M5, M6 close |
| 7 | M4 closes |
| 8 | M10 retrospective; M8 continues to week 9 |

Hard dependencies: M1 before M8 (the scope gate would refuse the pilot user's questions); M2
before M3; M3 before M4. When one of those stalls, work on M5, M6, M7 or M9.

---

## M0. Kickoff and guardrails

**Goal**: people, budget, branch protection and a baseline exist before any other work starts.
**Size**: 1 to 2 days. **Week**: 1. **Owner**: plan owner, with the assignee.

### Tasks

1. **Name people.** For each milestone: an assignee, and a reviewer who is a different person. Name
   one governance reviewer for anything touching `govern/`. Record the names in the team tracker,
   not in this file.
2. **Agree the model-spend budget** for M1, M2 and M4. At v4's rate a full arm is about 74M input
   tokens; this plan needs 6 to 8 arms plus scope-gate probes.
3. **Turn on branch protection for `main`** (`docs/open-work.md` §6.6), with the plan owner's
   approval because it changes how everyone lands work: pull requests required, the `test` and
   `ui` CI jobs required to pass, one approving review. Close §6.6 in `open-work.md`.
4. **Record the baseline** at the commit you start from, in the table at the bottom of this file:

   | Metric | Command |
   |---|---|
   | Commit | `git rev-parse --short HEAD` |
   | Tests | `uv run pytest -q` |
   | mypy errors | `uv run mypy src/governed_bi` |
   | Env vars read in `src` | `git grep -hoE 'GOVERNED_BI_[A-Z0-9_]+' -- src \| sort -u \| wc -l` |
   | Prose share of `src` | comment tokens plus docstring lines over total lines; a throwaway script in your scratch directory |
   | Gates | `ls tools/check_*.py \| wc -l`, and which run per push in `.github/workflows/ci.yml` |
   | Scope-gate false refusals | blocked count in `runs/eval/bi_scope_gpt-5.6-luna.jsonl` |

5. **Book the meetings**: the weekly prioritisation review and the fortnightly paired walkthrough
   (plan §7).

### Deliverables

- Branch protection on, visible in the repository settings.
- One PR that fills the baseline table and closes `open-work.md` §6.6.

### Acceptance

- [ ] Every milestone has a named assignee and reviewer in the tracker.
- [ ] A budget figure is agreed in writing.
- [ ] A direct push to `main` is refused.
- [ ] The reviewer reproduces every baseline number with the listed command. Expected ballpark:
      31 env vars, 45 mypy errors, ~36% prose, 12 gates (6 per push), 180 of 1,351 blocked.

### Out of scope

- Any code change.
- A reusable metrics tool, CI job or dashboard for the baseline.

### Stop and ask if

- No second person can be named as reviewer. The plan pauses until one is.

---

## M1. The scope gate stops refusing benign questions

**Goal**: the served scope gate refuses under 3% of the benchmark's benign questions and still
refuses clearly out-of-scope ones.
**Size**: 3 to 4 days. **Weeks**: 1 to 2. **Reviewer**: the governance reviewer.
Background: `docs/open-work.md` §6.10.

### Tasks

1. **Build an out-of-scope set first**, so a fix cannot pass by clearing everything. Write 60
   questions the gate should refuse: general knowledge with no table behind it in any served
   schema, requests for code or prose, chit-chat, and questions about other systems. Use the
   format `tools/bi_scope_probe.py --dataset` reads, and commit it under `tests/govern/data/`.
   Have the reviewer read the set before you run anything.
2. **Measure the current prompt on it.** Run `tools/bi_scope_probe.py` on the new set. You now
   have the two numbers that define the gate: benign refusal (13.3%) and out-of-scope recall.
3. **Write `bi_scope` v2** in `register/prompts.py`. The diagnosis in §6.10 is that the prompt
   tells the judge nothing about what the deployment holds, so v2 gives it that, for example the
   served schema names and a one-line description of each. Record in the variant's rationale how
   v2 is built from the corpus and what that means for `prompt_set_hash`.
4. **Measure v2** on a 200-question stride sample (`--stride`), then on the full 1,351, then on
   the out-of-scope set.
5. **Price it** with `tools/shadow_replay.py --scope-verdicts` against the v4 artifact. Compare the
   served-guard EX under v1 and v2.
6. **Make v2 the served default** in a separate PR, if it meets the acceptance criteria.
7. **Add one served-path test** alongside `tests/api/test_the_served_surface_is_not_the_benchmark_arm.py`
   that builds the policy through `api/graph_app.py::serve_policy` and asserts the scope rule is
   enabled with the v2 variant, so the served default cannot quietly drift back.

### Deliverables

- `tests/govern/data/` out-of-scope set (60 questions).
- A `bi_scope` v2 variant with its rationale.
- Probe artifacts for v1 and v2 in `runs/eval/`, with their counts in the PR description.
- A default-flip PR and the served-path test.
- `open-work.md` §6.10 updated with the result.

### Acceptance

- [ ] v2 blocks under 3% of the 1,351 (fewer than 41 questions).
- [ ] v2's out-of-scope recall is at least v1's, and at least 90%.
- [ ] v2 has zero `error_failed_open` verdicts on both sets.
- [ ] Shadow-replayed served-guard EX on v4's rows is within 2 points of the permissive 0.676.

### Out of scope

- The five deterministic guard rules, and any other file in `govern/`.
- Turning the scope gate off in production.
- Enabling the scope gate in the measured arms. The arm stays permissive, and shadow replay
  prices the gate, as `open-work.md` §6.10 decided.
- A classifier, embedding filter or any mechanism other than the prompt.

### Stop and ask if

- v2 cannot reach both thresholds. The trade-off between false refusals and recall is a product
  decision.
- Putting schema names in the prompt raises a disclosure concern for a real deployment.

---

## M2. A current, honest baseline

**Goal**: an accuracy figure for the configuration that ships, measured with a grader that is not
biased against aggregation form, stated where the headline is.
**Size**: 4 days of work plus about two days of run wall time. **Weeks**: 2 to 3.
**Reviewer**: plan owner plus one engineer.

### Tasks

1. **Fix the grader's numeric comparison** (`open-work.md` §6.4) in `eval/grade.py::_coerce_cell`
   and its caller: floats compare with a relative tolerance (propose `1e-6`, agree it in review),
   and text never equals a number (`'00123'` is not `123`). Add a test per case in §6.4's table.
2. **Regrade every existing arm** with `tools/regrade.py`, and report each arm's EX before and
   after in the PR. This changes the instrument, so the old and new figures are both recorded.
3. **Record cost in the artifact** (`open-work.md` §3.5): total input and output tokens per
   question, from the `usage` rows the harness already writes, as a per-row field. Add it to the
   run report.
4. **Fix the development/held-out split.** Shuffle the 57 `db_id`s with a fixed, recorded seed and
   assign the first 28 to `dev` and the rest to `holdout`. Commit the list as
   `docs/data/db-split.csv` (`db_id, half`). From here on, anything designed from failures uses
   `dev` only.
5. **Run the registered baseline `luna_v4_embed` twice** on the full 1,351, with `--arm
   luna_v4_embed --embed --resume`, at the shipped model and effort. The arm's notes explain why
   `--embed` is required.
6. **Report**, for each run and for the mean:
   - EX overall, on `dev`, and on `holdout`;
   - clean EX excluding the frozen-literal golds;
   - EX with the served guard, via `tools/shadow_replay.py` (with M1's v2 verdicts if M1 is done);
   - run-to-run disagreement (questions where the two runs differ);
   - tokens per question.
7. **Update the headline.** Wherever the README or `docs/` quote v4's 67.6%, state the shipped
   configuration's figure first and keep v4 as history. Search for `67.6`, `0.676`, `0.714` and
   `EX`. Keep the existing shape caveat.
8. **Decide on BIRD dev**: one paragraph in `docs/measurement.md` recording whether a corpus build
   for BIRD dev is worth its cost now, and why. Running it is a separate ticket.

### Deliverables

- Grader PR with tests; regrade table.
- Cost field PR.
- `docs/data/db-split.csv`.
- Two baseline artifacts in `runs/eval/`, and a results section in `docs/measurement.md`, one screen.
- Headline PR.

### Acceptance

- [ ] Every §6.4 case has a passing test, and the regrade table covers all seven existing arms.
- [ ] Every row of the new artifacts carries token counts.
- [ ] Both baseline runs pass the arm's gates (`knobs_comparable`, `facet_channels`), with no
      crashed rows left after `--resume`.
- [ ] The README headline describes a configuration that `.env.example` can reproduce.

### Out of scope

- Any change to prompts, retrieval, or the agent.
- Changing the served model or effort.
- Running BIRD dev.
- Unifying the measured and served graphs (`open-work.md` §6.9).

### Stop and ask if

- Either run exceeds its share of the budget, or shows more than 2% crashed rows before resume.
- The two runs disagree on more than 15% of questions (the recorded figure is 12.7%).

---

## M3. Error taxonomy on the development half

**Goal**: know which kinds of SQL mistake dominate the shipped configuration's genuine failures,
in proportions we can act on.
**Size**: 5 days, including about 10 hours of labelling. **Weeks**: 3 to 4.
**Reviewer**: a second engineer who co-labels the calibration set. **Depends on**: M2.

### Tasks

1. **Take the population** from the first M2 baseline run, restricted to `dev` databases, using
   the same partition as `docs/failure-modes.md`: keep only *answered wrong, full table coverage*.
   Frozen-literal golds, incomplete coverage, capped, refused and clarification rows already have
   their bucket and are not relabelled.
2. **Fix the taxonomy before labelling.** Start from these codes. You may merge or rename with the
   reviewer's agreement, and keep it to at most 8:

   | Code | Meaning |
   |---|---|
   | `JOIN` | missing, extra or wrong join path |
   | `FILTER` | wrong predicate, value, literal or date handling |
   | `AGG` | wrong aggregation, grouping, `DISTINCT` or `HAVING` |
   | `COLUMN` | right table, wrong column or wrong column meaning |
   | `SHAPE` | right answer, wrong output shape (columns, ordering) |
   | `GOLD` | the gold SQL is wrong, *or* the question has a reading under which the prediction is right |
   | `GRADER` | the prediction is right and the comparator still rejects it |
   | `OTHER` | none of the above, with a note |

   One primary code per row, an optional secondary.
3. **Sample.** If the population is 150 rows or fewer, label all of it. Otherwise take a simple
   random sample of 150 with a recorded seed, so category shares estimate the population directly
   with no weighting.
4. **Calibrate.** You and the reviewer label the same 30 rows independently. Compute Cohen's kappa
   on the primary code. Below 0.6, tighten the definitions and repeat on a fresh 30.
5. **Label.** For each row, read the question, the gold SQL and the generated SQL, and execute both
   when reading does not settle it. Target three minutes, cap ten; past ten, mark `OTHER` with a
   note and move on.
6. **Summarise**: count, share and a 95% interval per code; the share that is `GOLD` or `GRADER`;
   and the two largest engine-side codes, each with three example `question_id`s.

### Deliverables

- `docs/data/error-labels-dev.csv` with `question_id, db_id, run_id, gold_sql, generated_sql,
  primary, secondary, labeller, note`. The SQL columns are included because `runs/` is not in
  git, and the labels must be checkable without the artifact.
- A section "Error taxonomy (shipped baseline, dev half)" in `docs/measurement.md`, one screen.

### Acceptance

- [ ] Seed and population size recorded; the sample is reproducible from the artifact.
- [ ] Kappa of at least 0.6 on a 30-row calibration set.
- [ ] Two named engine-side categories for M4, each with its share and interval.
- [ ] No `holdout` question appears anywhere in the labels.

### Out of scope

- Any change to `src/`, prompts, the grader or the corpus. Write fix ideas in the note column.
- An automatic classifier or LLM judge. Hand labels are the point.
- Relabelling rows that `failure-modes.md` already buckets.
- Correcting gold labels in the dataset.

### Stop and ask if

- `GOLD` plus `GRADER` exceed 25% of the sample. That changes what the benchmark is worth.
- The two largest engine-side codes together cover under 30% of the sample, which would mean no
  single fix is worth a round.

---

## M4. Accuracy round 1

**Goal**: decide two accuracy hypotheses drawn from M3, with a protocol that cannot pass on
noise or on the questions it was designed from.
**Size**: 8 to 10 days including run wall time. **Weeks**: 5 to 7.
**Reviewer**: plan owner plus the M3 co-labeller. **Depends on**: M3.

### Tasks

1. **Pre-register each hypothesis** before writing its code. Add an `[arm.<name>]` entry to
   `register/arms.toml` with `compare_to = "luna_v4_embed"`, a description naming the M3 category
   and the mechanism, and the existing `hypothesised_effect` and `readout` fields.
   `tests/conformance/test_arm_profiles_are_declared.py::test_the_shipped_arms_declare_no_hypothesis_and_that_is_the_committed_state`
   pins the current "no hypotheses" state and must be updated deliberately in the same PR. Merge
   the registration before any run.
2. **Check power first.** `eval/provenance.py::arm_power_refusal` calls `eval/power.py::require_power`.
   Confirmation uses only `holdout` (about 650 questions), so the detectable effect is larger than
   the full set's 2.3 points (`open-work.md` §3.12). If the gate refuses, raise the hypothesised
   effect only if the mechanism justifies it; otherwise drop the hypothesis.
3. **Implement the smallest change that tests the mechanism**, behind the arm and prompt machinery,
   so the served default is untouched. Shapes that fit the likely categories:
   - `FILTER`: put a few distinct values of filterable columns into the context.
   - `JOIN` or `COLUMN`: choose few-shot examples by SQL skeleton rather than question text.
   - `AGG`: a prompt rule plus one neutral worked example, built the way v3 and v4 were.

   Develop and smoke-test against `dev` questions only (`--limit` on a filtered copy of the
   dataset is fine).
4. **Run each arm twice** on the full 1,351 with the baseline's settings except the treatment.
5. **Decide on `holdout` only.** Keep a hypothesis when a paired McNemar test
   (`eval/report.py::paired_ex`) against the baseline favours it at p < 0.025 (0.05 split across
   two hypotheses) on the pooled pairs of both runs, and no guardrail is worse. Report `dev` too,
   for information.
6. **Report per arm**: `holdout` and `dev` EX for both runs, the paired statistic, wins and losses,
   movement in the targeted M3 category on the labelled rows, and tokens per question against the
   baseline.
7. **Act on the decision.** A kept arm becomes the default in a small separate PR that also updates
   the headline (M2). A rejected arm stays in the register as a recorded result, and its code is
   removed unless something else uses it.

### Deliverables

- Per hypothesis: a registration PR, an implementation PR, two artifacts, and a results section in
  `docs/measurement.md`, one screen.
- At most one default-flip PR per kept hypothesis.

### Acceptance

- [ ] Each registration merged before its arm's first run (visible in git order).
- [ ] The power gate passed for each arm that ran.
- [ ] The decision uses `holdout` only, at α = 0.025, and is stated in one sentence.
- [ ] Tokens per question are reported next to EX.

### Out of scope

- Retrieval, routing or fusion changes. Retrieval accounts for 16% of failures and 19 of 20
  refusals (`failure-modes.md`), so it is round 2 material if M3 and M8 point there.
- More than two hypotheses, or several changes inside one arm.
- Revising a hypothesis after seeing its results. A revised idea is a new arm in round 2.
- Prompt rules aimed at the grader's result digest (v5 priced that at about 4 points).
- Changing the model or effort.

### Stop and ask if

- An arm would exceed its share of the budget.
- A run shows more than 2% crashed rows before resume.
- The two runs of one arm differ by more than 2 points of EX.

---

## M5. Operability: nothing drops silently

**Goal**: a failure that loses an audit row, or a server that cannot serve, is visible.
**Size**: 3 days. **Weeks**: 2 to 6, in the background. **Reviewer**: any engineer.
Background: `docs/open-work.md` §6.1 and §6.8.

### Tasks

1. **Choose the minimum logging setup**: the standard library `logging`, one module-level logger per
   module that uses it, configured once at each entry point. Write the choice as three lines in
   `docs/architecture.md`.
2. **Make the silent drops loud.** Start with `api/graph_app.py`'s `record` node, whose
   `except Exception: return {}` drops an audit row with no trace. Log it at `ERROR` with the
   thread and turn IDs. Then go through the 17 blind excepts without `# noqa: BLE001` listed in
   §6.1: each either logs, narrows its exception type, or gains a `noqa` with a one-line reason.
3. **Add `/readyz`** that touches the session and answers not-ready with a 503 when the DSN, corpus
   or model credential is missing, under both `langgraph dev` and bare `uvicorn`. Keep `/livez`
   as it is.
4. Add a test for the audit-drop log and for `/readyz` in the not-ready and ready states.

### Deliverables

- One PR per task above. `open-work.md` §6.1 and §6.8 updated.

### Acceptance

- [ ] Forcing an exception inside the `record` node produces an `ERROR` log line naming the thread.
- [ ] No unsuppressed blind `except Exception` remains in `src/`.
- [ ] `/readyz` returns 503 with a missing DSN under bare `uvicorn`, and 200 when configured.

### Out of scope

- Structured logging, a log shipper, tracing or metrics.
- Retention or pruning of stores (§6.2).
- Replacing the `print` calls in `serve/__main__.py`, which is a CLI.

---

## M6. Type coverage

**Goal**: `mypy` is clean on the whole package and CI enforces it.
**Size**: 3 to 4 days, in the background. **Weeks**: 2 to 6. **Reviewer**: any engineer; a good
pairing task with a new team member.

### Tasks

1. Fix the 45 errors, one PR per subpackage. Current distribution: `eval/shadow.py` 10,
   `serve/nodes/route_retrieve.py` 9, `serve/wrap.py` 5, `eval/projection.py` 5,
   `conform/check.py` 5, `serve/ledger.py` 4, `serve/graph.py` 4, `eval/attribution.py` 4, and
   eight files with 1 or 2.
2. Prefer a real annotation to `# type: ignore`. Each `ignore` names its error code and a
   half-line reason.
3. Last PR: change the `mypy` step in `.github/workflows/ci.yml` from its named file list to the
   whole package. `pyproject.toml` already sets `files = ["src"]`.

### Acceptance

- [ ] `uv run mypy src/governed_bi` reports 0 errors, and CI runs it on every push.
- [ ] Every `type: ignore` added has a code and reason. If more than 15 are needed, raise it in
      review; it may point at a design problem.

### Out of scope

- Behaviour changes or refactors while fixing types. A type error that reveals a real bug gets its
  own PR with a test.
- Stricter mypy flags, and types for `tools/` or `tests/`.

---

## M7. UI component tests

**Goal**: the three surfaces a first user touches have tests that run in CI.
**Size**: 4 days. **Weeks**: 5 to 6. **Reviewer**: an engineer with frontend experience.

### Tasks

1. Add Vitest and React Testing Library to `ui/` as dev dependencies, with a `test` script.
2. Write tests for:
   - `components/chat/stream-chat.tsx` and `message-list.tsx`: a streamed answer renders from a
     recorded event sequence (reuse `lib/mock/fixtures.ts`), and a refusal and a decline render
     distinctly;
   - `components/answer/answer-card.tsx`: SQL, result table and reliability stamp show; an empty
     and a truncated result are handled;
   - `components/chat/clarification-prompt.tsx`: submitting an answer calls the resume path with
     the right payload.
3. Add the `test` script to the `ui` CI job, and make it a required check (M0).

### Acceptance

- [ ] `npm test` in `ui/` passes locally and in CI.
- [ ] At least 12 cases across the three surfaces, each asserting on visible behaviour (text and
      roles).

### Out of scope

- End-to-end or browser tests, and snapshot tests.
- Refactoring components beyond extracting a prop or two. If a component cannot be tested without
  more, stop and ask.
- Replacing the `check:*` contract scripts, and tests for the audit, corpus, schema or review
  surfaces.

---

## M8. First-user pilot

**Goal**: one real person uses the product weekly for four weeks, safely, and what they hit
reaches the backlog.
**Size**: 3 days of setup, then about 2 hours a week. **Weeks**: 5 to 9. **Reviewer**: plan owner.
**Depends on**: M1.

### Tasks

1. **Clear the data question first.** With the plan owner and the user's data owner, confirm in
   writing that the dataset may be sent to the configured model provider. Without that, use a
   non-sensitive dataset.
2. **Choose the user**: one internal analyst with a Postgres dataset and 10 to 20 questions they
   actually ask.
3. **Build their corpus** with the in-repo seed path (`corpus/seed.py`). Expect it to be weaker than
   the curated BIRD corpus, which was built outside this repository. Record what curation it would
   need, and do only what blocks a first answer.
4. **Run it on a machine only that user uses**, bound to `127.0.0.1`. The API is unauthenticated,
   and `/audit/turns` exposes every thread's SQL and turn records to any local process
   (`api/auth.py`). Network exposure needs authentication back first, which is a separate ticket.
5. **Weekly 20-minute check-in.** Record questions asked, answered, trusted and refused, and the
   reason for any distrust.
6. **File issues** as GitHub issues labelled `pilot`, in terms that do not identify the user or
   their data.

### Deliverables

- The data-sharing confirmation, kept outside the repository.
- A private pilot log, kept outside the repository.
- GitHub issues labelled `pilot`.

### Acceptance

- [ ] Four consecutive weekly check-ins recorded in the private log.
- [ ] Time to first answer on day one recorded.
- [ ] The top three pilot issues are ranked in the backlog above leftovers from M5, M6 and M9.

### Out of scope

- Authentication, multi-user support, or network exposure.
- Features the pilot user did not ask for.
- Extending feedback clustering, bundle export or the enterprise-fork design. Using the feedback
  store that exists is fine.
- Adding their dataset to the benchmark.

### Stop and ask if

- The data cannot go to the model provider and no substitute dataset serves the user.
- More than two days of engine work are needed before a first answer.

---

## M9. Cleanup and audits

**Goal**: remove what is known to be dead weight, fix prose that contradicts the tree, and write
down which config and gates earn their place, without moving anything yet.
**Size**: 3 days. **Week**: 4. **Reviewer**: plan owner.

### Tasks

1. **Retire the tautological parametrization** (`open-work.md` §6.7):
   `tests/serve/test_stream_events.py::test_every_step_status_pair_builds` runs 234 cases that
   assert a value from a test-local set is in that set. Replace it with a handful of cases that
   assert on the emitted `step`, and confirm by mutation (replace `step` with a constant) that the
   new cases go red.
2. **Fix citations of rules `AGENTS.md` no longer contains**: `tools/mutate.py:3` and `:42`,
   `src/governed_bi/eval/provenance.py:12`, and any others `git grep -n "AGENTS.md"` finds. Point
   each at the document that actually holds the rule, or remove the citation.
3. **Config audit**, documentation only. A table of the 31 `GOVERNED_BI_*` variables: name, where
   it is read, class (secret, location, model selection, operational, unmeasured), and a
   recommendation. Put it in `docs/usage.md` if a variable reference already lives there,
   otherwise in `docs/measurement.md`.
4. **Gate audit**, documentation only. For each of the 12 `tools/check_*.py` gates: what it
   protects, where it runs (per push, nightly, manual), and any real catch you can find in git
   history or CI logs, marked "none found" rather than guessed. Same place as the config audit.
5. **Mark parked subsystems**: add one line to the module docstring of feedback clustering
   (`feedback/cluster.py`), bundle export (`tools/export_bundle.py`) and the access seams beyond the
   local principal, saying the module is parked until the 2026-11-20 retrospective.

### Deliverables

- One PR per task.

### Acceptance

- [ ] The suite has about 230 fewer test cases and the new cases catch the `step` mutation.
- [ ] `git grep -n "AGENTS.md"` returns only citations of rules `AGENTS.md` contains.
- [ ] Both audit tables cover every variable and every gate.

### Out of scope

- Removing any environment variable, moving any gate between CI tiers, or deleting any gate.
  Those are decided at M10 from the audit tables.
- Deleting parked code.
- Rewriting prose beyond the stale citations.

---

## M10. Retrospective

**Goal**: decide the next eight weeks from what these eight showed.
**Size**: half a day. **Week**: 8. **Owner**: plan owner, with the assignee presenting.

### Agenda

1. Scoreboard (plan §8) against the M0 baseline.
2. M1 and M2: the served configuration's real accuracy, with and without the guard.
3. M3 and M4: what moved, what it cost, what round 2 should target.
4. M8: what the pilot user needed that the product lacks.
5. Decisions from the M9 audits: which variables to remove, which gates to move or delete, which
   parked subsystems to delete.
6. Whether the remaining `docs/open-work.md` items are still the right backlog, and which of them
   the next eight weeks close.

### Deliverables

- `docs/reviews/2026-11-retrospective.md`, at most two screens, with no individual performance
  content.

---

## Baseline

Filled in by M0.

| Metric | Value |
|---|---|
| Commit | `b16e911` |
| Tests | 2,242 passed, 31 skipped, 10 xfailed, 0 failed (3m30s locally) |
| mypy errors | 45 in 16 files (151 checked) |
| Env vars read in `src` | 31 |
| Prose share of `src` | 35.6% (15,141 of 42,539 lines are comments or docstrings) |
| Gates (total / per push) | 12 / 6 (`file_length`, `one_implementation`, `measurement_locality`, `imports`, `citations`, `no_benchmark_discriminators`); `corpus_delta` nightly; 5 manual |
| Scope-gate false refusals | 180 of 1,351 blocked (13.3%) |

Branch protection (M0 task 3) was declined by the plan owner; see `open-work.md` §6.6.
