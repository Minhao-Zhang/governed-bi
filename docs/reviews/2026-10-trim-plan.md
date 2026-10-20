# Trim plan: keep the engine, delete what nobody uses

Status: superseded on 2026-10-19 by [ADR 0017](../adr/0017-a-served-chat-app-over-a-layer-the-user-brings.md) (proposed) and the [pivot memo](2026-10-pivot.md). Kept because `AGENTS.md` still points here until the owner approves a change.

## 1. Why

The repository is trying to be four things at once:

1. a text-to-SQL engine;
2. a deployment platform, with identity, an access policy and authentication for LangGraph Server;
3. a feedback product, the return path from ADR 0015;
4. a build system that polices itself, with 12 `check_*` gates, conformance rules V0 to V23 (20
   ids in use), a register of declared knobs and a nightly mutation run.

Only the engine has been measured. The access policy has never held more than one hard-coded
principal, and the return path has never had a reader other than the owner. Each extra subsystem
still costs something on every change. Deleting one module means editing the import layers, the
parcel map in `tests/contracts.py`, the mutation catalogue and the register closure.

Access control belongs to the platform a deployment runs on, which already has identity, row-level
security and single sign-on. That holds only when the engine's statements run as the caller's
database identity, which is how Databricks Genie and Snowflake Cortex Analyst work; behind a shared
service account, this trim removes access control altogether, and a deployment like that has to
add it back at the platform. What this engine can promise on its own is narrower: the model sees
only the tables retrieval selected for this turn, and every statement passes the checks before it
runs. That promise is kept.

## 2. What the project is after the trim

The engine answers questions over Postgres from its semantic-layer corpus, refuses when it cannot
ground the answer, and is measured on BIRD-Obfuscation, this project's BIRD variant with translated
physical names and decoy columns (ADR 0005).

These parts stay:

- retrieval, schema routing, reference closure and join planning (`retrieve/`);
- context assembly, the agent loop and its tools (`serve/`);
- the SQL checks and the execution bounds (`govern/`, `datasource/`), apart from the access parts
  in §3.1;
- the turn stamp and the read-only audit surface;
- clarification through LangGraph interrupts, including resume after a restart;
- the declared abstention policy (ADR 0013), because it controls the trade-off between coverage
  and wrong answers that the next round of measurement is about;
- `eval/` and `measure/`;
- the UI and the HTTP API that serves it, minus the review page and the note control (§3.2).

## 3. What is removed

### 3.1 Access and identity (decided)

| What | Where | Notes |
|---|---|---|
| Access policy and principal types | `govern/access.py` (including `LOCAL_PRINCIPAL`); `Principal`, `Grant`, `Reach`, `RowPredicate`, `PredicateEnforcement`, `AccessPolicy`, `OPEN_GRANT` in `ports.py`; their re-exports in `govern/__init__.py` and `serve/__init__.py`; `GovernancePolicy.access_grant` in `govern/policy.py` | |
| Per-identity checks inside the SQL checks | `govern/check.py` (the `grant.denies_column` branch in `_columns`; the authorization branches in `_tables`); the four rule ids in `govern/layers.py`: `r_column_not_authorized`, `r_column_authorization_unavailable`, `r_table_not_authorized`, `r_row_predicate_unenforced` | **Keep `r_table_not_licensed`.** It enforces the tables retrieval allowed this turn, which is engine logic |
| Grant-aware tool bounds | `govern/bounds.py` (`grant`, `withheld`, `discloses`, the grant parts of `may_inspect_schema` and `may_sample`, `resume_authorised`) | Keep `licensed` and `readable_assets` |
| Grant-aware context | `serve/context.py::withheld_by_grant` and the `withheld` parameter; `serve/delivery.py::grant_for_turn`; `serve/nodes/assemble.py` | |
| The `access_grant` knob | `register/knobs.py`, `serve/session.py`, `eval/provenance.py` | Changes the recorded knob set, so T6 compares against M2 with one expected one-sided key |
| Identity in clarification | `serve/resume.py` (`authorise_resume`, `CALLER_KEY`, the identity token helpers); its caller in `serve/tools.py`; `serve/accept.py` writing `identity`; the `identity` field in `serve/state.py`; the `identity` parameter in `serve/session.py` and its callers in `eval/harness.py` and `api/thread_turns.py` | Interrupt, resume and the checkpointer stay. Resume is keyed by `thread_id`; identity never isolated threads |
| Visibility filtering in the API | `api/visibility.py`; its callers in `api/routes.py` and `api/browse_routes.py` read the session directly | |
| Access policy wiring | `api/graph_app.py` (`access_policy_from_environment`, `resolve_access_grant`, `ACCESS_POLICY_VAR`) | |
| Access benchmark | `tools/govern_bench.py` and its step in `.github/workflows/ci.yml`; the grant fixture in `govern/adversarial.py` and `govern/adversarial_run.py`; the grant cases (including the seven `benign_authz_*` cases) and the `authorized`, `denied_columns` and `row_predicate` world fields in `govern/adversarial.toml`; the matching anchors in `tools/mutation_catalogue_data_1.py` | The attack cases that target the remaining checks stay |
| Documents | `docs/enterprise-fork.md` deleted; ADR 0012 rewritten as a reversal record | |

These stay in place:

- The corpus's own `excluded` and `suspect` column flags. They are facts about the data, not about
  who is asking.
- The `threads.update` and `create_run` state-write denials in `api/auth.py`, and the `auth` block
  in `langgraph.json` that loads them. They stop a client from writing turn state directly, which
  matters as long as LangGraph Server serves the UI, and they are a protected area under the
  September rules.
- An `@auth.authenticate` handler in `api/auth.py`. LangGraph Server refuses to start an `auth`
  block without one, so it stays and returns a constant identity; only its use of
  `LOCAL_PRINCIPAL` goes.

### 3.2 The return path (decided)

- **Backend:** `feedback/`, `api/feedback_routes.py`, `eval/feedback_import.py`, the parts of
  `api/routes.py` and `api/thread_turns.py` that read the feedback store, and the feedback store path in
  `paths.py`.
- **Tools:** `export_bundle.py`, `verify_patch.py`, `import_eval_failures.py`,
  `reproduce_observation.py` and `check_landed.py`; the `FeedbackStore` dependency in
  `tools/smoke_api.py`; the feedback parts of `tools/corpus_target.py`; the `feedback` layer in
  `tools/check_imports.py`.
- **UI:** `ui/app/review/page.tsx` and its link in `ui/components/layout/nav.tsx`; everything in
  `ui/components/review/`; `ui/components/answer/raise-note.tsx` and its use in `answer-card.tsx`;
  the feedback parts of `ui/hooks/queries.ts`, `ui/lib/api-client.ts`, `ui/lib/schemas.ts`,
  `ui/lib/types.ts` and `ui/lib/mock/fixtures.ts`; `ui/lib/review-copy.ts`
  and `ui/lib/patch-fields.ts`; the scripts `check-observation-projections`, `check-review-copy`,
  `check-patch-fields` and `check-asset-diff` with their entries in `ui/package.json`; and the
  feedback part of `ui/README.md`.
- **The clarification queue:** `/clarifications/pending` lives in `api/feedback_routes.py` and
  currently merges interrupts with feedback notes. It moves out of that file and goes back to
  reading interrupts only, and the union-pagination code (`MAX_UNION_OFFSET`) and its test go with
  the notes.
- **`corpus/patch.py`:** nothing in `src/` imports it once `feedback/` is gone. `conform/` only
  names it in the V23 docstring. It is deleted with its two tests, and the docstring is reworded.
- **Documents:** ADR 0015 is rewritten as a reversal record, `docs/return-path.md` is deleted, and
  the return-path items in `docs/open-work.md` (§3.10a to §3.10c) are closed.

### 3.3 Proposed, waiting for the owner

These are off by default or were never measured. Each one is a separate decision.

| What | Why it could go | Why it might stay |
|---|---|---|
| Reflection node (`serve/nodes/reflect.py`) | off by default, and it is not allowed to trigger a retry | a future judge experiment would need it again |
| Negative-example node (`serve/nodes/negative.py`) | BIRD-corpus has no negative examples | the Ossie plan adds a place for unanswerable questions |
| Cost layer (`Layer.COST`, `r_cost_budget_exceeded`) | declared and disabled; no budget was ever configured | cheap to keep |
| Proxy and Bedrock gateways (`model/proxy_gateway.py`, `proxy_embedder.py`, `bedrock_embedder.py`) | only one model setup has ever been measured | the deployment that uses the proxy still exists |

### 3.4 Build machinery (proposed)

M9's gate audit records what each gate has caught. When that table exists, keep ruff, mypy,
pytest, the adversarial suite, the retired-score check and the test-question leak check, and
delete or merge the rest. Until then, change only what a deletion in §3.1 or §3.2 forces.

## 4. What moves to a later plan

The corpus move to Apache Ossie is [its own plan](2026-10-ossie-plan.md). It adds a dependency, a
converter and a new corpus identity, where every step here deletes, and it builds on a spec draft
that says not to depend on it. It starts after T6.

## 5. How we show nothing broke

Every step can be checked without a language model, so equality can be exact and run-to-run noise
does not hide a regression.

T0 records a snapshot at the starting commit. For each of the 1,351 questions it records the
routed schemas, the licensed tables, the join plan and a hash of the rendered context. For each
adversarial and normal statement it records the verdict and the rule that decided it, and for each
leak probe it records the outcome. The snapshot runs in the offline configuration with the
deterministic embedder and with facet query rewriting off, because the rewriters call the utility
model and fall back to the raw question without one.

Each later step reruns the snapshot and compares it with the snapshot of the `main` commit it
branched from. Equality is the default criterion. Any expected difference has to be listed in the
step's PR before the run, and anything not on the list fails the step.

The snapshot covers retrieval, context and the SQL checks. It does not cover the API, the UI or
clarification, so those rely on the tests and the hand checks each step lists.

The final step adds one live run of the shipped configuration, compared question by question with
the latest M2 baseline. That run is a sanity check, not an equality test: run-to-run disagreement
was 12.7% on an earlier pair of runs.

## 6. Order

| Step | What | Depends on |
|---|---|---|
| T0 | snapshot and expected-difference format | none |
| T1 | remove access and identity | T0 |
| T2 | remove the return path | T0 |
| T3 | the proposed removals the owner accepts (§3.3) | T0 |
| T4 | build machinery, after M9's gate audit (§3.4) | T1, T2 |
| T6 | live check, documents and close | T1, T2, T3, and T4 if it runs |

T1, T2 and T3 touch mostly different files and can run in parallel. Where two of them edit the same
file (`api/routes.py`, `api/thread_turns.py`), the second to land rebases onto the
first and reruns the snapshot. There is no T5; §4 says where that work went.

## 7. How this plan relates to the September milestones

This plan comes first. A September milestone continues only when it does not touch files that a
trim step is changing at the time; when it does, the trim step lands first.

For trim work, four of the September rules are replaced:

- **Rule 1, branches.** Trim branches are named `t<N>/<short-name>`.
- **Rule 4, the freeze.** The trim may add the two temporary T0 tools, which T6 deletes.
- **Rule 6, protected areas.** T1 may change or delete `govern/access.py`, `govern/check.py`,
  `govern/bounds.py`, `govern/layers.py`, `govern/policy.py`, `govern/__init__.py`,
  `govern/adversarial.py`, `govern/adversarial_run.py` and `govern/adversarial.toml`, and T3 may
  remove `Layer.COST`, each with the governance reviewer's approval. The `threads.update` and
  `create_run` denials in `api/auth.py` and the read-only and timeout settings in `datasource/`
  stay protected.
- **Rule 7, green on every PR.** Its test-deletion clause is widened: a test may also be deleted
  where a trim step names it. The rest of rule 7 is restated as rule 2 of the trim milestones.

The other September rules still apply to trim work.

Effects on individual milestones:

- **M1.** The scope-gate work touches `register/prompts.py` and the guard, which this plan leaves
  alone, so M1 continues in parallel.
- **M8.** The pilot milestone allowed using the feedback store. After T2, pilot findings go to
  GitHub issues only, which M8 task 6 already requires.
- **M9 and M10.** M9 parked the feedback code and the access seams until the retrospective, and its
  out-of-scope list forbade deleting parked code. This plan takes that deletion decision now, so
  M9 task 5 no longer applies to those two subsystems, and M10 does not revisit them.

## 8. Decisions the owner still has to make

1. Which items in §3.3 to remove.
2. Whether T4 runs in this round or waits for M10.
