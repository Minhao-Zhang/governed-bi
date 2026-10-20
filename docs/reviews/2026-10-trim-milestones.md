# Trim milestones: the steps and their sign-off criteria

Status: superseded on 2026-10-19 by [ADR 0017](../adr/0017-a-served-chat-app-over-a-layer-the-user-brings.md) (proposed) and the [pivot memo](2026-10-pivot.md). Kept because `AGENTS.md` still points here until the owner approves a change.

Every step uses the same sections as the September milestones: Goal, Tasks, Deliverables,
Acceptance, Out of scope, and Stop and ask if. A step is signed off when every acceptance box is
ticked by someone other than the person who did the work.

## Rules for every step

1. **One branch per step**, named `t<N>/<short-name>`, with several small PRs where the work allows.
2. **Green on every PR.** `uv run pytest -q`, `uv run ruff check src tests tools`,
   `uv run mypy src/governed_bi` and CI must all pass. If the step touches `ui/`, the `ui` job must
   pass as well.
3. **The snapshot decides.** Each step from T1 to T4 reruns the T0 snapshot and compares it with
   the snapshot of the `main` commit it branched from. A step that rebases onto another step's
   merge reruns the comparison against the new base. Before the run, the PR description lists every expected
   difference, giving the question or case id, the field, and the reason. Any difference not on that
   list fails the step.
4. **Deletions are complete.** When a module goes, its tests, register entries, import-layer
   entries, parcel entries, mutation-catalogue anchors and documentation go in the same PR.
   `git grep` for the module's name returns only the reversal record and the commit message.
5. **Superseded ADRs** are rewritten as reversal records, following the rule in `docs/README.md`.
6. **Public repository.** Nothing that identifies a pilot user or their data goes into the repo.

---

## T0. Snapshot and baseline

**Goal**: a record of what the engine does today that later steps can compare against without a
language model.

### Tasks

1. Write `tools/trim_snapshot.py`. It loads the corpus in the offline configuration with the
   deterministic embedder and writes one JSON Lines file:
   - one row per BIRD-Obfuscation question: routed schemas, licensed tables, join plan and the
     SHA-256 of the rendered context;
   - one row per statement in the adversarial suite: verdict, layer and rule id;
   - one row per leak probe: outcome.
2. Run the snapshot with facet query rewriting off, and say so in the tool's `--help`. The
   rewriters call the utility model and fall back to the raw question without one, which is the
   path `tools/routing_recall.py` already uses.
3. Write `tools/trim_compare.py`, which compares two snapshots against a file of expected
   differences, prints every difference, and exits 1 on any difference that is not expected.
4. Run the snapshot twice at the starting commit and confirm the two files are identical.
5. Record the starting commit, the corpus commit, the test counts and the line counts of `src/`,
   `ui/` and `tools/` in the table at the bottom of this file.

### Deliverables

- The two tools, each with a test that feeds it a small hand-made pair of snapshots.
- The baseline table filled in.

### Acceptance

- [ ] Two snapshot runs at the same commit, in separate processes, are byte-identical.
- [ ] Changing one character in one table's `summary` produces a difference that
      `trim_compare.py` reports and fails on.
- [ ] The snapshot covers all 1,351 questions and every case in the adversarial suite.
- [ ] No language model or network call happens during a snapshot. The test asserts this, for
      example with the network blocked.

### Out of scope

- Any change to `src/`.

### Stop and ask if

- Two runs at the same commit differ. That is a determinism bug, and it has to be fixed before
  anything else.

---

## T1. Remove access and identity

**Goal**: no code, configuration or document refers to a principal, a grant or an access policy,
and the engine does exactly what it did under the open grant.

### Tasks

Work in this order, one PR each:

1. **SQL checks.** Remove the identity branches from `govern/check.py` and the four rule ids from
   `govern/layers.py`: `r_column_not_authorized`, `r_column_authorization_unavailable`,
   `r_table_not_authorized` and `r_row_predicate_unenforced`. Keep `r_table_not_licensed`,
   `r_column_excluded`, `r_column_suspect` and `r_column_not_allowed`. This PR needs the
   governance reviewer.
2. **Tool bounds and context.** Remove grants from `govern/bounds.py`, `serve/delivery.py`,
   `serve/context.py` and `serve/nodes/assemble.py`. Remove the `access_grant` knob from
   `register/knobs.py`, `serve/session.py` and `eval/provenance.py`, and the field that feeds it
   from `govern/policy.py`.
3. **Clarification identity.** Remove `authorise_resume`, `CALLER_KEY` and the identity helpers
   from `serve/resume.py` and their caller in `serve/tools.py`; the `identity` writes in
   `serve/accept.py`; and the `identity` field and parameter in `serve/state.py` and
   `serve/session.py`, with the callers that pass it in `eval/harness.py` and
   `api/thread_turns.py`. Interrupt and resume keep working.
4. **API.** Delete `api/visibility.py` and point `api/routes.py` and `api/browse_routes.py` at the
   session directly. Remove the access policy wiring from `api/graph_app.py`. In `api/auth.py`,
   keep an `@auth.authenticate` handler that returns a constant identity without
   `LOCAL_PRINCIPAL`: LangGraph Server will not start an `auth` block that has none. **Keep the
   `threads.update` and `create_run` denials**, and the `auth` block in `langgraph.json` that
   loads them.
5. **Types and tools.** Delete `govern/access.py`, the access types in `ports.py` and their
   re-exports in `govern/__init__.py` and `serve/__init__.py`. Delete `tools/govern_bench.py` and
   its step in `.github/workflows/ci.yml`. Remove the grant fixture from `govern/adversarial.py`
   and `govern/adversarial_run.py`, and from `govern/adversarial.toml` the grant cases (including
   the seven `benign_authz_*` cases) and the `authorized`, `denied_columns` and `row_predicate`
   world fields. Drop the matching anchors from `tools/mutation_catalogue_data_1.py`.
6. **Documents.** Delete `docs/enterprise-fork.md` and its entry in `docs/README.md`. Rewrite
   ADR 0012 as a reversal record. Update `docs/architecture.md`, `docs/glossary.md` and the
   affected `open-work.md` items.

Tests deleted in this step:

- `tests/govern/test_access_seam.py`
- `tests/api/test_the_browse_surface_respects_the_grant.py`
- `tests/serve/test_the_access_seam_reaches_the_served_app.py`
- `tests/serve/test_the_platform_resume_is_identity_bound.py`
- in `tests/conformance/test_a_declared_mechanism_is_reachable_from_the_shipped_config.py`, the
  test `test_an_access_policy_no_principal_can_hold_is_refused_at_startup`

Tests rewritten to drop their grant cases: `tests/govern/test_adversarial_suite.py`,
`tests/govern/test_guard_pipeline_ledger.py`, `tests/serve/test_sample_rows_governed_executor.py`,
`tests/serve/test_trust_boundary_and_checkpoint_growth.py` and `tests/serve/test_model_inputs.py`.
Tests changed to drop `CALLER_KEY`, `identity=` or a removed rule id:
`tests/serve/test_agent_tools_hitl.py`, `tests/serve/test_a_pause_survives_a_restart_on_disk.py`
and `tests/conformance/test_the_counts_stated_in_comments_are_real.py`.
`tests/api/test_a_run_cannot_write_state.py` stays, because it covers the denials that stay.

### Deliverables

- Six PRs as listed, plus the reversal record for ADR 0012.

### Acceptance

- [ ] The snapshot matches T0 for all 1,351 questions with no expected differences.
- [ ] Every adversarial case that remains has the same verdict and the same rule id as in T0. The
      only expected differences are the deleted grant cases.
- [ ] The 46 normal statements that remain (53 less the seven `benign_authz_*` cases) still pass,
      and every remaining attack is still blocked.
- [ ] `git grep -nE "Principal|Grant|AccessPolicy|access_grant|withheld_by_grant|authorise_resume|resume_authorised|_not_authorized|authorization_unavailable|row_predicate_unenforced"`
      over `src tests tools ui` returns nothing.
- [ ] `langgraph dev` starts with the trimmed `api/auth.py`.
- [ ] A clarification can be asked, the server restarted, and the answer resumed, both in a test
      and by hand in the UI.
- [ ] `tests/api/test_a_run_cannot_write_state.py` passes unchanged.

### Out of scope

- The input guard, the scope gate and anything else M1 owns.
- The corpus `excluded` and `suspect` flags.
- Read-only sessions, timeouts and the function allowlist.

### Stop and ask if

- Any question's licensed tables or rendered context changes. Under the open grant they should
  not.
- Removing the identity from resume makes a clarification resumable from a thread it was not
  asked in.

---

## T2. Remove the return path

**Goal**: no feedback store, observation, patch or review exists in the engine, the API or the UI,
and clarification still works.

### Tasks

1. **Clarification queue.** Move `/clarifications/pending` out of `api/feedback_routes.py`, where
   it lives today, and make it read interrupts only. Delete the union-pagination code, including
   `MAX_UNION_OFFSET`, and its test.
2. **Backend.** Delete `feedback/`, `api/feedback_routes.py`, `eval/feedback_import.py`, the
   feedback reads in `api/routes.py` and `api/thread_turns.py`, and the feedback store path in
   `paths.py`.
3. **Tools.** Delete `export_bundle.py`, `verify_patch.py`, `import_eval_failures.py`,
   `reproduce_observation.py` and `check_landed.py`, along with their mutation-catalogue entries.
   Remove the `FeedbackStore` dependency from `tools/smoke_api.py`, the feedback parts of
   `tools/corpus_target.py`, and the `feedback` layer from `tools/check_imports.py`.
4. **UI.** Delete `ui/app/review/page.tsx`, its link in `ui/components/layout/nav.tsx`, all of
   `ui/components/review/`, `ui/lib/review-copy.ts`, `ui/lib/patch-fields.ts`, and `raise-note.tsx`
   with its use in `answer-card.tsx`. Remove the feedback parts of `ui/hooks/queries.ts`,
   `lib/api-client.ts`, `lib/schemas.ts`, `lib/types.ts` and `lib/mock/fixtures.ts`. Delete the
   scripts `check-observation-projections`, `check-review-copy`, `check-patch-fields` and
   `check-asset-diff` and their entries in `ui/package.json`. Update `ui/README.md`. Regenerate
   `docs/openapi.json`.
5. **`corpus/patch.py`.** Once `feedback/` is gone, nothing in `src/` imports it (`conform/` only
   names it in the V23 docstring). Delete it with
   `tests/corpus/test_a_body_edit_survives_a_round_trip.py` and
   `tests/corpus/test_an_edit_does_not_rewrite_the_file.py`, and reword the docstring.
6. **Documents.** Delete `docs/return-path.md`, rewrite ADR 0015 as a reversal record, close
   `open-work.md` §3.10a to §3.10c, and remove the return-path table from `docs/README.md`.

Tests deleted in this step: `tests/feedback/` in full, and these files in `tests/api/`:

- `test_a_reader_cannot_name_a_column.py`
- `test_an_observation_is_filed_on_a_turn.py`
- `test_the_steward_can_draft_and_withdraw_a_patch.py`
- `test_the_pending_queue_unions_two_stores.py`
- `test_the_pending_source_axis_has_one_spelling.py`
- `test_the_queue_does_not_serve_the_benchmark.py`

Also delete `tests/conformance/test_the_ladder_checks_the_edit_and_not_the_file.py`. Remove the
feedback cases from `test_the_audit_window_is_bounded.py`,
`test_the_client_accepts_both_wire_projections.py`, `test_the_spec_matches_the_server.py`,
`test_the_trace_shows_what_the_reader_was_asked.py`, `test_the_pending_queue_reads_interrupt_state.py`,
`test_http_contract.py` and `test_smoke_script.py`. Remove the `feedback_import` use from
`tests/eval/test_a_renamed_table_is_covered_when_it_was_licensed.py`. Drop the feedback parcel
from `tests/contracts.py`. Of the UI tests, any that render the note control lose that case.

### Deliverables

- The PRs above, the regenerated `docs/openapi.json` and the reversal record for ADR 0015.

### Acceptance

- [ ] The snapshot matches its base with no expected differences.
- [ ] `git grep -nE "governed_bi\.feedback|feedback_routes|feedback_import|feedback\.sqlite|FeedbackStore|Observation|raise-note|RaiseNote|export_bundle|corpus/patch"`
      over `src tests tools ui` returns nothing. A bare `feedback` grep would also hit prose
      about feedback loops in `eval/projection.py` and the register text, which stays.
- [ ] The UI builds, the review page and the note control are gone, and the answer card still
      shows the SQL, the result table and the reliability stamp.
- [ ] `/clarifications/pending` paginates interrupts correctly, with a test that uses more rows
      than one page.
- [ ] `tools/check_imports.py` passes with `feedback` removed from its layers.

### Out of scope

- Any change to how clarification is asked or resumed.
- The audit page, the corpus page and the history page, except for removing feedback from them.

### Stop and ask if

- The eval harness or an arm turns out to need the feedback store to run.

---

## T3. Proposed removals (only those the owner accepts)

**Goal**: remove what the owner picked from §3.3 of the plan, one PR per item.

### Tasks

For each accepted item, delete the module, its graph edge or layer, its knob, its tests and its
documentation, then rerun the snapshot.

### Deliverables

- One PR per accepted item, or none if the owner accepts none.

### Acceptance

- [ ] The snapshot matches its base. A node or layer that was off by default should cause no
      difference.
- [ ] `register/` declares no knob for a removed item.

### Out of scope

- Any item in §3.3 that the owner did not accept, and anything that is on by default.

### Stop and ask if

- Removing an item changes any snapshot row. That means it was not really off.

---

## T4. Build machinery

**Goal**: the gates and checks that remain are the ones that have caught something.

Starts only after M9's gate audit table exists, and only if the owner decides to run it this
round.

### Tasks

1. From the audit table, list each gate as keep, merge or delete, and get the owner's approval on
   the list.
2. Delete or merge gates one PR at a time, and update `.github/workflows/ci.yml` in the same PR.
3. Shrink the mutation catalogue to entries whose files still exist, and delete the nightly job if
   nothing in it protects a kept rule.

### Deliverables

- The approved keep, merge or delete list, and one PR per gate change.

### Acceptance

- [ ] The snapshot matches its base with no expected differences.
- [ ] These still run on every push: ruff, mypy, pytest, the adversarial suite, the retired-score
      check and the test-question leak check.
- [ ] Each deleted gate has a one-line reason in the PR, quoted from the audit table.
- [ ] CI time per push is recorded before and after.

### Out of scope

- New gates.

### Stop and ask if

- A gate the audit marks for deletion is the only thing that tests a rule that stays.

---

## T6. Live check and close

**Goal**: the trimmed engine answers as the untrimmed one did, and the documents describe what
exists.

### Tasks

1. Run the registered baseline arm twice on all 1,351 questions with the shipped configuration,
   as M2 did.
2. Compare it question by question with the latest M2 baseline using `eval/report.py::paired_ex`.
   The recorded knob sets differ by the removed `access_grant` key and any knob T3 removed;
   `eval/provenance.py` reports those as warnings, and the PR lists them as expected.
3. Delete `tools/trim_snapshot.py` and `tools/trim_compare.py`.
4. Fill in the "after" column of the table below.
5. Update `README.md`, `docs/README.md` and `docs/architecture.md` so that no page describes a
   removed subsystem in the present tense.

### Deliverables

- The two run directories, the paired comparison, the completed table and the documentation PR.

### Acceptance

- [ ] EX on both runs is within 2 points of the M2 baseline, and the paired test shows no
      significant difference at α = 0.05.
- [ ] Run-to-run disagreement is under 15%, the threshold M2 uses.
- [ ] No crashed rows remain after `--resume`.
- [ ] The table below is complete.

### Out of scope

- Tuning anything to recover EX. A move is a finding about the trim, not a reason to change the
  engine in this step.

### Stop and ask if

- EX moves by more than 2 points. T1 to T4 promised no change, so a move means a snapshot missed
  something.

---

## Baseline and result

Filled in at T0 and T6.

| Metric | T0 | T6 |
|---|---|---|
| Engine commit | | |
| Corpus commit | | |
| Tests passed / skipped / xfailed | | |
| Lines in `src/governed_bi` | | |
| Lines in `ui/` (TS and TSX) | | |
| Lines in `tools/` | | |
| `tools/check_*.py` gates (total / per push) | | |
| CI time per push | | |
| EX, shipped configuration, mean of two runs | | |
