# Pivot milestones: the steps, their checks, and what the plan missed

Status: proposed, 2026-10-19. Nothing here starts until the owner accepts ADRs 0017 to 0020.
Written against the working tree of branch `trim/plan` at `eaba82a`, with the pivot memo and the
four ADRs still uncommitted.

These milestones carry out the [pivot memo](2026-10-pivot.md) and the decisions it rests on:
[0017](../adr/0017-a-served-chat-app-over-a-layer-the-user-brings.md) (what stays and what goes),
[0018](../adr/0018-every-answer-states-its-assumptions.md) (assumptions on every answer),
[0019](../adr/0019-one-plain-chat-page.md) (one plain chat page) and
[0020](../adr/0020-ossie-export-and-a-separate-viewer.md) (an Ossie export and a separate viewer).
When they are done, governed-bi is an engine and one chat page. Every answer lists its
assumptions, which the server checks against the SQL. The layer can be exported to Ossie, and
there is a baseline measured twice with one model setup. The viewer is its own project with its
own milestones, V0 to V4, at the end of this file.

The checks below were run against the repository on 2026-10-19. Findings 28 to 34 came from
exploration helpers whose reports arrived late; each was checked against the code before it was
written in.

---

## 1. Findings on the plan

The owner decides whether to fix the memo or the ADRs. Each finding says what the milestones do
about it.

### Claims that are wrong or stale

1. **Nothing from the trim plan was carried out.** The memo (§3, line 44) says the trim plan "cut
   the two subsystems with no users", and 0017 (Context, line 24) says it "removed" them. Both
   are still in the tree: `src/governed_bi/govern/access.py` (386 lines) and
   `src/governed_bi/feedback/` (2,710 lines) exist, and `api/auth.py:68` imports
   `LOCAL_PRINCIPAL` from `govern/access.py`. *Milestones:* P2 and P3 do that deletion, reusing
   the file lists from T1 and T2 of the [trim milestones](2026-10-trim-milestones.md), which are
   accurate.
2. **There are 63 declared knobs, not 58.** `register/knobs.py::KNOB_REGISTER` holds 63 entries:
   53 with `Role.comparability`, 6 operational and 4 scope. The memo (§3) and 0017 (Context) say
   58. *Milestones:* P0 records the real count.
3. **`.env` serves BIRD, not MS Fabric Facilities.** The memo (§7, question 1) says `.env` points
   at the Facilities corpus. `.env` sets `GOVERNED_BI_CORPUS_DIR=../BIRD-corpus`, and the
   Facilities DSN and corpus lines are commented out (`.env` lines 77 and 93; the comment at
   line 72 says so, dated 2026-08-23). Only `.env.bak-before-bird-flip` still points at
   `../MS Fabric Facilities/corpus`. *Milestones:* the pilot question stays open (section 7), but
   it is a choice to make, not the current state.
4. **There is no tool the agent answers through.** 0018 §1 adds an `assumptions` list to "the
   tool the agent answers through". The agent's tools are `read_body`, `inspect_schema`,
   `sample_rows`, `run_query` and `ask_user` (`serve/tools.py:618`). The answer is the agent's
   final prose, which `serve/nodes/narrate.py` adopts as `answer_text`. *Milestones:* P10 must
   first add a structured final answer. The owner chose a new `answer` tool (decision 2).
5. **Keeping result rows on the turn record contradicts ADR 0006, which 0017 keeps.** 0019 §2.2
   says the turn record keeps the first 100 rows. ADR 0006 §11 (`docs/adr/0006-execution-time-governance.md`,
   the retention table under line 691) puts "result rows" in the class the durable projection
   drops, and `serve/nodes/stamp.py:438` keeps `result_table` out of `record` for that reason.
   *Resolved:* that retention table was withdrawn on 2026-08-06 and never implemented (ADR 0006
   §11's superseding note), so nothing in force forbids storing rows; only the comment in
   `stamp.py` still cites it. The owner chose to keep the 20 rows `run_query` already holds
   (decision 3), and P12 deletes the stale comment.
6. **The memo lists HTTP field names that are mostly prose.** Memo §6.2 says `licensed`, `knob`,
   `gate` and `stamp` are fields in `docs/openapi.json`. The fields there are `licensed`,
   `licensed_count`, `knobs_resolved`, `facet_hits`, `facet_degraded`, `facet_channels` and
   `reflect_verdict`; "knob", "gate" and "stamp" occur only in descriptions. *Milestones:* P9
   lists the real fields.
7. **The UI counts are right.** 0019 (Context) says 21,500 lines. TypeScript and TSX under `ui/`,
   excluding `node_modules` and the three build folders `.next`, `.next-mock` and `.next-verify`,
   is 21,512 lines with `ui/tests/` and 21,137 without. (A first count that pruned only `.next`
   gave 22,138, because it included the other two build folders.) The eight pages are right
   (`ui/app/`: the chat page plus `audit`, `clarifications`, `corpus`, `history`, `review`,
   `schema`, `settings`). *Milestones:* P0 records the measured numbers.
8. **The other counts check out.** 42,907 lines in `src/governed_bi` over 151 files; 2,441 in
   `retrieve/`; 51,968 in `tests/`; 39 Python files in `tools/` (40 entries with `__pycache__`);
   12 `tools/check_*.py`; 84 mutations (56 in `tools/mutation_catalogue_data_1.py`, 28 in
   `_data_2.py`); 22 distinct conformance rule ids in `conform/`; 1,351 questions in
   `../BIRD-Data-Obfuscation/eval_dataset/test_final.jsonl`; 57 schemas in `../BIRD-corpus`; 112
   joins with `cardinality: many_to_many`; exactly two `talkingdata` joins with no cardinality;
   `apache/ossie` at `53418ba` (2026-10-18) with `"version": {"const": "0.2.0.dev0"}` in
   `core-spec/ossie-schema.json`. The first commit is `7627eb2` on 2026-07-08.

### Deletions that would break kept code

9. **The "Waiting for you" list lives in the return path.** `/clarifications/pending` is served
   from `api/feedback_routes.py:319`, and it unions interrupts with the feedback store. 0019 §1
   keeps it as the top of the sidebar. *Milestones:* P3 moves it into `api/routes.py`, reading
   interrupts only, before `feedback_routes.py` goes.
10. **The access types sit inside the SQL checks.** `govern/check.py:24`, `govern/bounds.py:29`
    and `serve/delivery.py:17` import `ResolvedGrant`, `resolve_grant` or `OPEN_RESOLVED` from
    `govern/access.py`, and `serve/context.py:518` defines `withheld_by_grant`. Deleting
    `access.py` touches the checks 0017 promises to keep unchanged. *Milestones:* P2 removes the
    grant branches first and the module last, with the snapshot proving no verdict moved.
11. **`register/knobs.py` is the configuration of the whole engine, not only a check.** It is
    imported by `govern/check.py`, `govern/guard.py`, `govern/policy.py`, `retrieve/index.py`,
    `datasource/postgres.py`, `model/embedder.py`, `serve/graph.py`, `serve/session.py`,
    `serve/runtime.py`, `eval/provenance.py`, `eval/report.py` and `corpus/validate.py`. What can
    go is the checking around it (`tools/check_declared_is_consumed.py`, the role and
    comparability machinery); the defaults and the config hash must stay. *Milestones:* P6
    turns it into plain settings, and P8 renames it.
12. **`register/` keeps more than 0017 says.** 0017 §3 keeps "the arm definitions, the prompts
    and the stage vocabulary". Counting importers in `src` and `tools`: `stages` 25, `assets` 19,
    `quantity` 17, `prompts` 9, `record` 8, `facets` 7, `arm_profiles` 3, `citations` 1. So
    `assets.py`, `quantity.py`, `record.py` and `facets.py` also stay, or move. *Milestones:* P6
    keeps them and deletes only `citations.py` with its one caller.
13. **The kept leak checks share a test file with deleted gates.** The train-only check is
    `tools/check_train_only.py`, which needs data and is deliberately not in CI
    (`.github/workflows/ci.yml:108`). The data-free half is
    `tools/check_no_benchmark_discriminators.py`, which runs on every push (`ci.yml:113`). 0017
    names neither file, and `tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py`
    tests both alongside gates that go. *Milestones:* P5 keeps both tools and splits that test.
14. **CI depends on the Bedrock extra and on `tests/contracts.py`.** Every job runs
    `uv sync --frozen --extra bedrock` (`ci.yml:71`, `:367`, `:451`), and the "Parcel acceptance
    report" step imports `tests/contracts.py` (`ci.yml:144`). *Milestones:* P5 removes the parcel
    step with `contracts.py`; P7 drops the extra from `pyproject.toml:125` and from CI together.
15. **The UI CI job runs checks for pages that go.** `ci.yml` runs `check:asset-diff`,
    `check:patch-fields`, `check:observation-projections` and `check:review-copy` (lines 228 to
    251), and `ui/package.json` declares them. *Milestones:* P3 and P4 delete them with their
    pages.

### Work the plan implies but does not name

16. **Rewiring `api/routes.py`.** It imports `browse`, `browse_routes`, `feedback_routes` and
    `visibility` (`api/routes.py:71` to `:74`) and constructs a `FeedbackStore` at line 308. Its
    own routes `/corpus/assets`, `/graph`, `/knowledge-graph`, `/audit/corpus` and
    `/audit/turns/{turn_id}/trace` serve deleted pages. P3 and P4 rewire it and regenerate
    `docs/openapi.json`, which `tests/api/test_the_spec_matches_the_server.py` pins.
17. **Example questions need an endpoint once the browse API goes.** 0019 §3 draws the three
    new-conversation examples from the layer's verified examples, and the refusal offers "nearby
    questions the layer can answer". Today the few-shot assets reach the UI only through the
    browse API. No code finds nearby answerable questions. P12 adds one small endpoint; P14 uses
    it.
18. **0018's ambiguity rule needs a definition of "a phrase the layer defines in more than one
    way".** Nothing computes that today. Term assets (603 in `../BIRD-corpus`) bind phrases to
    columns and metrics, so a phrase bound to two targets is the obvious reading. P11 writes it.
19. **The answer anatomy needs a single executed statement.** A turn can run `run_query` several
    times. 0018 §2 parses "the executed SQL", which must mean the statement whose rows were
    delivered. P10 names it.
20. **The six checks exist only once the cost layer goes.** `govern/layers.py:31` has seven
    layers, the seventh being `COST`. The mockup's "Passed all six" is true only after P6 deletes
    the cost layer, so P14 depends on P6.
21. **The Ossie mapping has gaps the ADR does not cover.** In `../BIRD-corpus`, joins carry
    `left_table`, `right_table` and an `on` expression string, not column lists, and 327 are
    `one_to_many`, so the exporter must parse `on` and flip direction to put the many side in
    `from`. Columns are nested in table files, not separate assets. The corpus has no negative
    examples (asset types are `schema`, `table`, `join`, `metric`, `term` and `few_shot`), so
    `ai_context.unanswerable` will be empty for BIRD. An Ossie `Field` has no `synonyms` key;
    synonyms go in `ai_context.synonyms`. `../ossie/validation/validate.py` needs `jsonschema`,
    `referencing` and `pyyaml`, and `jsonschema` is not in `uv.lock`. P15 handles each.
22. **Measuring "all 1,351" ignores the September split.** `docs/data/db-split.csv` assigns 28
    databases to `dev` and 29 to `holdout`. The memo (§5 step 8) and 0017 §5 measure on all
    1,351. The owner kept the split rule (decision 7): P16 reports both halves and the total,
    anything designed from failures reads `dev` only, and a keep decision reads `holdout` only.
23. **The plain-names check will be noisy as specified.** Memo §6.2 fails CI on any replaced word
    in an identifier. Several rows are ordinary English or library words: "shape", "surface",
    "control", "population", "register", "fold". P9 makes the check cover only the coined terms
    and lists the generic words as prose guidance, with an allowlist file.
24. **About 25 tools have no stated fate.** The memo says tools go "from 40 files to about 10"
    without saying which. P7 proposes the list for the owner to approve.

### Ordering and what the snapshot can prove

25. **The rename should come after the page deletions, or it renames code that is about to be
    deleted.** The memo puts renames (step 4) before the UI rebuild (step 6), so the old chat
    components would be renamed and then rebuilt. These milestones delete the non-chat pages in
    P4, rename in P8 and P9, and rebuild in P13 and P14, so only the chat page is renamed.
26. **The snapshot proves less than "the deletions are safe".** It runs with the deterministic
    embedder (`model/deterministic_embedder.py`), facet rewriting off and no agent model, through
    the stub answer path `eval/datalake.py::routing_recall` already uses (line 297). It can prove
    that routing, retrieval, join planning, context rendering and the SQL checks did not move. It
    cannot prove anything about the production embedder, the rewriters, the prompts, the agent
    loop, abstention, clarification or the UI. Those rest on the test suite and on the P16
    baseline. P2 to P9 say so in their sign-off.
27. **The assumptions work changes the prompt, so it cannot be held to the snapshot.** 0018
    already says the first run after it is a new baseline. P10 and P11 are checked by tests with
    `serve/scripted_model.py`, and P16 measures them.

### Further findings, checked against the code

28. **The rendered context can differ between processes.** `resolve_node` and `connect_node` in
    `serve/nodes/route_retrieve.py` add to `retrieved.pulled_in` by iterating plain sets: `added`
    is a set difference at line 171 and is walked at line 196, and in `connect_node` `added` is a
    `set` (line 258) walked at line 298, followed by `complete_joins` (`retrieve/structure.py:117`,
    which builds a set) at line 303. Dictionary insertion order therefore follows set order, which
    depends on `PYTHONHASHSEED` for strings. `serve/context.py` walks `pulled_in` in that order
    (`_build_pieces`, line 113) and `_collect_rules` (lines 289 to 311) keeps the first-seen order
    of rules. So the context hash can change between processes when two or more pulled-in tables
    carry `rules`; four table files in `../BIRD-corpus` do. *Milestones:* the owner decided
    (decision 11) that the engine sorts. P1's first PR makes `serve/context.py` walk `pulled_in`
    in sorted order, so the served prompt is stable as well as the snapshot, and P1 then takes the
    baseline on the sorted engine.
29. **The deterministic embedder cannot be chosen by configuration, and `tools/routing_recall.py`
    is not reusable as is.** `model/provider.py::embedder` (lines 405 to 442) accepts only
    `openai`, `bedrock` and `proxy`. `tools/routing_recall.py` refuses to run without a DSN
    (line 80) and defaults `--corpus-dir` to `corpora/gold-semantic-layer-20260804` (line 36),
    which does not exist. *Milestones:* P1's tool constructs
    `model/deterministic_embedder.py` in code, needs no DSN, and takes the corpus path as a
    required argument.
30. **The 12 disclosure probes run only in a tool P5 deletes.** The `[[probe]]` cases are run by
    `run_disclosure_probes` in `tools/govern_bench.py` (around line 207), whose docstring (line 28)
    says it lives there because it calls `serve/context.py::withheld_by_grant` and `serve/fetch.py`,
    above `govern/` in the layer order. `tests/govern/test_adversarial_suite.py:585` imports it by
    path. P2 deletes `withheld_by_grant`. *Milestones:* P1 runs the probes through
    `govern_bench.py`'s runner, not `adversarial_run.py`. P2 rewrites the probes that depend on the
    grant and lists them as expected differences. P5 moves the runner into
    `src/governed_bi/eval/disclosure_probes.py` (the import-layer gate goes in the same milestone)
    before deleting `govern_bench.py`.
31. **The test-question leak rule lives in `conform/`.** Rule V12, `check_split_leak`, is in
    `conform/rules_tree.py` and wired at `conform/check.py:238`. 0017 deletes `conform/` and keeps
    the leak checks. *Milestones:* P5 moves V12 into `tools/check_train_only.py` (or
    `eval/`) with its test before `conform/` goes.
32. **Abstention cannot see guesses where it sits, and it cannot ask.** `abstain` runs between
    `assemble` and `agent_core` (`serve/graph.py`, edges at lines 283 to 291), and
    `register/stages.py` (lines 84 to 88) records why: deciding after `run_query` "is not a
    decision". It can only decline (`serve/abstention.py:176`, `path_kind: Literal["decline"]`).
    A clarifying question comes only from the model calling `ask_user` (`serve/tools.py:455`),
    with `basis` limited to `data_definition` and `ranking_ambiguity`
    (`serve/clarification.py:32` to `36`). Assumptions exist only after the agent has answered.
    *Milestones:* the owner chose to enforce it in P10's answer tool (decision 10), which P11 builds.
33. **Every executed statement has a `LIMIT`.** `govern/pipeline.py::apply_row_limit` (line 355)
    forces `LIMIT max_rows + 1` at the statement root. A `LIMIT` check on the executed SQL fires on
    every answer. *Milestones:* P10 parses the statement before the limit is applied (the
    `canonical` field of the pipeline's prepared statement, `govern/pipeline.py:51`, or the raw
    statement).
34. **Clarification choices are not in the payload, and some field names are banned.** The
    interrupt payload is `{kind, clarification_id, question, why, basis}` with no choices
    (`serve/clarification.py:66` to `81`), and `ui/hooks/use-stream-chat.ts` (lines 318 to 323)
    renders free text only for that reason. 0019's choice buttons need a payload change.
    Separately, `tests/serve/test_the_abstention_policy_is_declared.py` (lines 149 to 171) forbids
    `confidence`, `certainty`, `tier`, `probability`, `trust_score` and `reliability_score` in the
    policy files, and `tests/api/test_http_contract_answer_and_stream.py:137` forbids
    `safety_clearance` and `semantic_assurance` in `src/`. The `NARRATE` prompt
    (`register/prompts.py:421`) says "Do not add caveats about accuracy, data quality or your own
    confidence", which can read as forbidding the assumptions list when `narrate` generates the
    text. *Milestones:* P12 adds `choices` to the payload; P10 and P11 avoid the banned names and
    reword `NARRATE`.

---

## 2. Rules for every milestone

1. **One feature branch per milestone**, named `p<N>/<short-name>` (and `v<N>/<short-name>` in the
   viewer repository). A milestone may be one PR or a short series. The owner squashes on merge.
2. **Green checks.** These commands exist and pass on `eaba82a` (mypy and ruff were run for this
   file; the suite collects 2,126 tests):
   - `uv run --frozen ruff check .`
   - `uv run --frozen mypy src/governed_bi`
   - `uv run --frozen pytest -q -rs`
   - if the milestone touches `ui/`, from `ui/`: `npm ci`, `npm run lint`, `npx tsc --noEmit`,
     `npm test` and `npm run build`.

   CI (`.github/workflows/ci.yml`, jobs `test` and `ui`) must be green on the PR. When a
   milestone deletes a CI step, it deletes it in the same PR as the code it ran.
3. **The snapshot rule.** From P2 to P9b, every PR that deletes or renames engine code reruns
   `tools/pivot_snapshot.py` and compares it with the snapshot of the `main` commit it branched
   from, using `tools/pivot_compare.py`. Expected differences are listed in the PR description
   before the run, by question or case id, field and reason. Any other difference fails the PR.
   A rename lists renamed fields; values must still match.
4. **Deletions are complete.** When a module goes, its tests, CI steps, docs and remaining
   references go in the same PR. The sign-off grep must return nothing outside `runs/`, accepted
   ADRs, reversal records and the glossary's mapping note.
5. **Reversal records** follow the rule in `docs/README.md` (line 45): what was decided, what is
   true instead, and what was learned, with no coordinates into deleted files.
6. **Comments record decisions** (`AGENTS.md`). In files you touch, trim long comments to their
   decision in the same commit.
7. **Sign-off** is the owner ticking every box in the milestone. A box that cannot be ticked is a
   reason to stop, not to reword the box.
8. **Public repository.** Nothing that identifies a pilot user or their data goes in.

---

## 3. Milestones

### P0. Accept the decisions and record the starting point

**Goal:** the ADRs are accepted, `AGENTS.md` points here, and the numbers later milestones
compare against are written down.

**Depends on:** nothing.

**Tasks**
1. The decisions in section 7 are settled and written into ADRs 0017 to 0020 (findings 4, 5 and
   22 through decisions 2, 3 and 7). The owner rereads the four ADRs, fixes any stated fact from
   section 1 still wrong in them (for example the 58 knobs, finding 2), and sets their Status to
   Accepted.
2. With the owner's explicit consent, change the "Current priorities" section of `AGENTS.md` to
   point at this file and the memo.
3. Mark `docs/reviews/2026-10-trim-plan.md`, `2026-10-trim-milestones.md` and
   `2026-10-ossie-plan.md` superseded in their Status lines, and update `docs/README.md`.
4. Fill in the "Start" column of the table in section 8.
5. Close PR #3 (the DetentAI fork) with a comment that explains the pivot and credits the fork's
   finding that its `assumptions` field was declared but never filled (decision 6). The owner
   approved this wording on 2026-10-19:

   > Thank you for this, Ryan, and for writing it up so carefully. I'm closing this PR rather
   > than merging it, because governed-bi is changing direction. It's narrowing to the engine and
   > one plain chat page over a semantic layer the user brings. The access seam, the return path
   > and the curator work this branch builds on are all being deleted (ADR 0017 on `trim/plan`).
   >
   > Your first point changed the plan. You found that `assumptions` was declared and rendered but
   > never filled, including the turn that averaged rows a certified rule excludes. ADR 0018 now
   > makes the assumptions list required on every answer, and the server checks it against the
   > executed SQL. That ADR cites your finding as the reason.
   >
   > Your fork is still yours to take wherever you like.

**Deliverables:** accepted ADRs, the `AGENTS.md` change, the filled table.

**Sign-off criteria**
- [ ] `grep -l "Status:\*\* Accepted" docs/adr/001[7-9]-*.md docs/adr/0020-*.md` lists four files.
- [ ] `grep -n "2026-10-pivot" AGENTS.md` returns at least one line, and the commit message says
      the owner consented.
- [ ] The Start column of section 8 has no empty cell.
- [ ] `gh pr view 3 --json state --jq .state` prints `CLOSED`.

**Out of scope:** any code change.

**Stop and ask the owner if:** a finding in section 1 changes what an ADR decides rather than a
fact it states.

**Size:** 6 to 8 Markdown files, under 150 lines changed.

---

### P1. The offline snapshot

**Goal:** a record of what retrieval and the SQL checks do on every question and case, which two
runs reproduce byte for byte with no model and no network.

**Depends on:** P0.

**Tasks**
0. **First PR: sort the pulled-in tables.** Make `serve/context.py` walk `retrieved.pulled_in` in
   sorted order of asset id in `_build_pieces` and `_collect_rules`, with a test that renders the
   same retrieval in two subprocesses with `PYTHONHASHSEED` set to 1 and 2 and gets the same hash.
   This is the one change to `src/` in P1 (decision 11). It changes the rendered context only for
   questions whose pulled-in tables carry `rules`; the PR lists which.
1. Write `tools/pivot_snapshot.py`. It builds a session with `serve/session.py::from_corpus_dir`
   on a corpus path given as a required argument, `agent_model=None`, and an instance of
   `model/deterministic_embedder.py` constructed in code, since no setting selects it
   (finding 29). It needs no DSN. It runs the compiled graph from
   `serve/graph.py::compile_graph` per question, as `eval/datalake.py::routing_recall` does (that
   function explains why the graph must run whole, not node by node); do not reuse
   `tools/routing_recall.py` itself. For each of the 1,351 questions in `test_final.jsonl` it
   writes one JSON Lines row: question id, routed schemas, the schema ranking, licensed tables,
   the join plan, and the SHA-256 of the rendered context.
2. Add one row per statement case in `govern/adversarial.toml` (115 `case` and 16 `guard_case`),
   run through `govern/adversarial_run.py`, with verdict, layer and rule id. Add one row per
   disclosure probe (12 `probe`, reading the 3 `probe_asset` entries), run through
   `run_disclosure_probes` in `tools/govern_bench.py`, which is the only runner (finding 30).
3. Sort rows and keys in the output. Also run every snapshot with `PYTHONHASHSEED=0` set by the
   tool itself (it re-executes itself if the variable is missing), so any set-order dependence the
   sort in task 0 did not reach still cannot change the file. Clear the saver between questions as `routing_recall` does.
4. Write `tools/pivot_compare.py`: it compares two snapshots against a file of expected
   differences, prints each difference, and exits 1 on any unexpected one.
5. State in both tools' `--help` that facet rewriting is off and what the snapshot cannot prove
   (finding 26).
6. Test both tools on a small hand-made fixture, with the network blocked.

**Deliverables:** the two tools, `tests/tools/test_pivot_snapshot.py` (or under `tests/eval/`),
two snapshot files under `runs/` (ignored by git), and their SHA-256 in section 8.

**Sign-off criteria**
- [x] Two runs at the same commit, in separate processes, produce files with the same SHA-256.
- [x] The task 0 test passes: a question whose pulled-in tables include at least two with
      `rules` renders the same context hash in two subprocesses with `PYTHONHASHSEED` set to 1
      and 2.
- [x] The snapshot has 1,351 question rows, 131 statement rows and 12 probe rows (a count by row
      kind).
- [x] Editing one character of one table `summary` in a copy of the corpus makes
      `pivot_compare.py` print the difference and exit 1.
- [x] The tool test passes with sockets blocked, and fails if a model client is constructed.
- [x] Wall time for one full run is recorded in section 8.

**Out of scope:** any change to `src/` other than the sort in task 0.

**Stop and ask the owner if:** two runs differ, or the seed test still shows different hashes
after the sort (another set-order dependence to find first); or a full run takes more than 30
minutes.

**Size:** 3 new files, about 400 lines added, plus about 20 lines in `serve/context.py` and its
test.

---

### P2. Delete access and identity

**Goal:** no code, configuration or document refers to a principal, a grant or an access policy,
and the checks return exactly what they returned under the open grant.

**Depends on:** P1.

**Tasks** (T1 of the [trim milestones](2026-10-trim-milestones.md) lists the files and tests in
full and is accurate against this tree; follow its order)
1. Remove the grant branches from `govern/check.py`, `govern/bounds.py`, `serve/delivery.py`,
   `serve/context.py` (`withheld_by_grant`, line 518) and `serve/nodes/assemble.py`, and the four
   authorization rule ids from `govern/layers.py`.
2. Remove the `access_grant` knob from `register/knobs.py`, `serve/session.py` and
   `eval/provenance.py`.
3. Remove the identity binding from `serve/resume.py`, `serve/tools.py`, `serve/accept.py`,
   `serve/state.py`, `eval/harness.py` and `api/thread_turns.py`.
4. Delete `api/visibility.py` and its uses in `api/routes.py:74` and `api/browse_routes.py:28`.
   Remove the policy wiring at `api/graph_app.py:271`. In `api/auth.py`, keep `@auth.authenticate`
   returning a constant identity without importing `govern/access.py`, and keep the
   `threads.update` and `create_run` handlers unchanged.
5. Delete `govern/access.py` and its re-exports in `govern/__init__.py:13` and `serve/__init__.py`.
   Remove the grant cases from `govern/adversarial.toml` (including the seven `benign_authz_*`).
   Rewrite `run_disclosure_probes` in `tools/govern_bench.py` so it no longer calls
   `withheld_by_grant`, and drop or rewrite the probes that only test a grant (finding 30).
6. Delete `docs/enterprise-fork.md`. Rewrite ADR 0012 as a reversal record.

**Deliverables:** the PRs, the reversal record for 0012.

**Sign-off criteria**
- [ ] The snapshot matches the P1 snapshot for all 1,351 questions with no expected difference.
- [ ] Statement and probe rows match except the listed grant cases and grant probes, which are
      gone or rewritten.
- [ ] `git grep -nE "Principal|ResolvedGrant|AccessPolicy|access_grant|withheld_by_grant|authorise_resume|_not_authorized|row_predicate_unenforced" -- src tests tools ui`
      returns nothing.
- [ ] `tests/api/test_a_run_cannot_write_state.py` passes with no change to the file
      (`git diff main -- tests/api/test_a_run_cannot_write_state.py` is empty).
- [ ] `tests/serve/test_a_pause_survives_a_restart_on_disk.py` passes.
- [ ] By hand: `uv run --frozen langgraph dev` starts, a question that asks for clarification
      pauses, the server restarts, and answering the clarification in the UI resumes the turn.

**Out of scope:** the input guard, the scope gate, the `excluded` and `suspect` flags.

**Stop and ask the owner if:** any question's licensed tables or context hash change, or a
clarification becomes resumable from a thread it was not asked in.

**Size:** about 25 files in `src`, 15 test files changed or deleted; roughly 2,500 lines removed.

---

### P3. Delete the return path, keeping the waiting list

**Goal:** no feedback store, observation, patch or review exists, and the list of clarifications
waiting for an answer still works from interrupts alone.

**Depends on:** P2.

**Tasks**
1. Move `/clarifications/pending` from `api/feedback_routes.py:319` into `api/routes.py`, reading
   interrupt state only. Delete the two-store union and `MAX_UNION_OFFSET`.
2. Delete `src/governed_bi/feedback/`, `api/feedback_routes.py`, `eval/feedback_import.py` and
   `corpus/patch.py`. Remove the `FeedbackStore` construction at `api/routes.py:308` and the
   routers at lines 276 to 283, and the feedback store path in `paths.py`.
3. Delete `tools/export_bundle.py`, `verify_patch.py`, `import_eval_failures.py`,
   `reproduce_observation.py` and `check_landed.py`. Remove feedback from `tools/smoke_api.py`
   and `tools/corpus_target.py`.
4. In `ui/`: delete `app/review/`, `components/review/`, the note control and its use in
   `components/answer/answer-card.tsx`, and the feedback parts of `hooks/queries.ts`,
   `lib/api-client.ts`, `lib/schemas.ts` and `lib/mock/fixtures.ts`. Delete the scripts
   `check-observation-projections`, `check-review-copy`, `check-patch-fields` and
   `check-asset-diff` from `ui/scripts/`, `ui/package.json` and `ci.yml` (lines 228 to 251).
5. Delete `tests/feedback/` and the feedback tests in `tests/api/` listed in trim T2.
6. Regenerate `docs/openapi.json`. Delete `docs/return-path.md`; rewrite ADR 0015 as a reversal
   record. Remove `GOVERNED_BI_FEEDBACK_ADMIN` from `.env.example` if present.

**Deliverables:** the PRs, the regenerated spec, the reversal record.

**Sign-off criteria**
- [ ] The snapshot matches its base with no expected difference.
- [ ] `git grep -nE "governed_bi\.feedback|feedback_routes|feedback_import|FeedbackStore|Observation|corpus\.patch|export_bundle|check_landed" -- src tests tools ui .github`
      returns nothing.
- [ ] A test pages `/clarifications/pending` over more interrupts than one page holds and gets
      every one exactly once.
- [ ] `tests/api/test_the_spec_matches_the_server.py` passes against the regenerated spec.
- [ ] By hand: a paused clarification appears on the clarifications page and answering it resumes.

**Out of scope:** how clarification is asked or resumed.

**Stop and ask the owner if:** the eval harness or an arm needs the feedback store to run.

**Size:** about 60 files deleted, 15 changed; roughly 9,000 lines removed across `src`, `tests`,
`tools` and `ui`.

---

### P4. Delete the browse API and every page except chat

**Goal:** the server serves only what the chat page calls, and the UI has one page with one view.

**Depends on:** P3.

**Tasks**
1. List the requests the chat page makes today (open the browser network panel during a full
   question, a clarification and a refusal) and record them in the PR. Recorded on 2026-10-20
   against the live app: `/capabilities`, LangGraph's `POST /threads`,
   `POST /threads/{id}/runs/stream`, `GET /threads/{id}/state` and `threads.search`, and
   `/corpus/assets`, which only the empty state's starter questions read. The chat page does not
   call `/clarifications/pending`, `/livez` or `/readyz`.
2. Delete `api/browse.py` and `api/browse_routes.py`, and from `api/routes.py` the routes
   `/corpus/assets`, `/graph`, `/knowledge-graph`, `/audit/corpus`, `/audit/turns` and
   `/audit/turns/{turn_id}/trace`, unless step 1 found the chat page calling one. Delete the
   readers in `api/thread_turns.py` that only those routes use. Four custom routes stay:
   `/capabilities`, `/clarifications/pending` (P13's sidebar), and the health checks `/livez`
   and `/readyz`, which a deployment probes even though the page does not.
   Cut `tools/smoke_api.py` and `tests/api/test_smoke_script.py` down to those four routes; the
   tool called nine of the deleted ones. The owner chose on 2026-10-20 to keep it, as the one
   check that a running server answers.
3. In `ui/`: delete `app/audit`, `app/clarifications`, `app/corpus`, `app/history`, `app/schema`
   and `app/settings`, their components, `lib/display-mode.ts`, the display-mode picker and every
   `atLeast(mode, …)` branch (nine files read the display mode). The chat page shows what the
   analyst level shows today. Remove the dead "why" lines and "Schemas considered" from the answer
   card: `lib/answer-delivery.ts:226` reads `provenance.uncertainty_flags` and `:247` reads
   `provenance.routed_schemas`, and nothing in `src/` writes either. Delete the empty state's
   "Try one of these" starters (`components/chat/conversation.tsx`, `EmptyState`), which fetch
   all of `/corpus/assets` to show four few-shot questions. The owner chose on 2026-10-20 to drop
   them here rather than keep the route; P14 brings them back from P12's `GET /examples`.
4. Until P13 adds the sidebar, keep a plain list of recent conversations on the chat page, using
   `lib/threads.ts`, so no feature is lost between milestones.
5. Regenerate `docs/openapi.json`. Rewrite ADR 0009 as a reversal record; trim 0007 to the routes
   that remain.

**Deliverables:** the PRs, the request list, the reversal record.

**Sign-off criteria**
- [ ] The snapshot matches its base with no expected difference.
- [ ] `ls ui/app` shows only `favicon.ico`, `globals.css`, `layout.tsx` and `page.tsx`.
- [ ] `git grep -nE "browse_routes|api\.browse|display-mode|useDisplayMode|whyLines|routedSchemasLabel" -- src tests ui`
      returns nothing.
- [ ] `docs/openapi.json` lists exactly `/capabilities`, `/clarifications/pending`, `/livez`
      and `/readyz`, checked by `test_the_spec_matches_the_server.py`, and the chat page makes no
      request to `/corpus/assets` (the network panel, a new conversation).
- [ ] `uv run --frozen python tools/smoke_api.py --help` exits 0, and
      `tests/api/test_smoke_script.py` passes against those four routes.
- [ ] By hand: ask a question, get an answer with SQL and table; reload with `?thread=<id>` and
      see the same conversation; answer a clarification.

**Out of scope:** any new UI; the sidebar design.

**Stop and ask the owner if:** the chat page depends on a route marked for deletion, or the
eval harness or `tools/smoke_api.py` reads one.

**Size:** about 80 UI files and 5 API files deleted; roughly 12,000 UI lines and 2,000 Python
lines removed.

---

### P5. Delete the checks on the repository

**Goal:** CI runs lint, types, tests, the adversarial suite and the leak check, and nothing that
only checks the repository's own shape.

**Depends on:** P1. Can run beside P2 to P4 if the branches touch different files; in practice
run it after P4 to avoid conflicts in `ci.yml`.

**Tasks**
1. Delete `tools/check_file_length.py`, `check_one_implementation.py`,
   `check_declared_is_consumed.py`, `check_citations.py`, `check_measurement_locality.py`,
   `check_ratchet.py`, `check_corpus_delta.py`, `check_imports.py` and `check_corpus_conformance.py`,
   with their CI steps (`ci.yml` lines 89 to 102).
2. Delete `tools/mutate.py`, the three `mutation_catalogue*.py` files and the `mutate` job
   (`ci.yml:435`).
3. First move rule V12, `check_split_leak` (`conform/rules_tree.py`, wired at
   `conform/check.py:238`), into `tools/check_train_only.py` with its test (finding 31). Then
   delete `src/governed_bi/conform/` (nothing else in `src/` imports it),
   `tools/corpus_baseline.py`, `tools/conformance_findings.py`, the `corpus` job (`ci.yml:321`) and
   the `.conformance/` directory if tracked. Rewrite ADR 0016 as a reversal record.
4. Delete `tests/contracts.py` and the "Parcel acceptance report" step (`ci.yml:144`).
5. Move `run_disclosure_probes` and its helpers from `tools/govern_bench.py` into
   `src/governed_bi/eval/disclosure_probes.py`, and point `tests/govern/test_adversarial_suite.py`
   (line 585) and `tools/pivot_snapshot.py` at it (finding 30). Then delete `tools/govern_bench.py`
   and its step (`ci.yml:131`); the adversarial suite stays in
   `tests/govern/test_adversarial_suite.py`.
6. Delete the `schedule` trigger once no job uses it.
7. Delete `tests/conformance/` except the parts that test kept tools. Move the
   `check_no_benchmark_discriminators` and `check_train_only` cases out of
   `test_the_lint_gates_fire_on_a_synthetic_violation.py` into `tests/eval/`.
8. Trim ADR 0005 to drop the file-length and one-implementation rules.

**Deliverables:** the PRs, the reversal record for 0016, CI time per push before and after.

**Sign-off criteria**
- [ ] The snapshot matches its base with no expected difference.
- [ ] `ls tools/check_*.py` lists exactly `check_no_benchmark_discriminators.py` and
      `check_train_only.py`.
- [ ] `grep -nE "mutate|corpus_delta|contracts|govern_bench|schedule:" .github/workflows/ci.yml`
      returns nothing.
- [ ] `git grep -nE "governed_bi\.conform|mutation_catalogue|tests/contracts" -- src tests tools`
      returns nothing.
- [ ] `tests/eval/test_train_only_check.py` and a test that runs
      `check_no_benchmark_discriminators.py` on a synthetic violation both pass.
- [ ] A test feeds `check_train_only.py` a corpus asset quoting a `test_final.jsonl` question and
      sees the moved V12 rule fail it.
- [ ] `git grep -n "govern_bench" -- src tests tools .github` returns nothing, and the probe rows
      match the base snapshot exactly.
- [ ] CI time of the `test` job is recorded before and after in section 8.

**Out of scope:** the adversarial suite's cases; the leak checks' logic.

**Stop and ask the owner if:** a deleted gate is the only thing testing a rule that stays.

**Size:** about 40 files deleted; roughly 8,000 lines removed (1,422 in `conform/`, most of
`tests/conformance/`'s 6,682, the mutation catalogue and gates).

---

### P6. Delete the parts that are off by default, and make settings plain

**Goal:** the reflection and negative-example nodes, the cost layer and the knob roles are gone,
every remaining setting is a plain default in the repository with the config hash still recorded,
and settings and secrets are read apart, once, at startup.

**Depends on:** P5.

**Tasks**
1. Delete `serve/nodes/reflect.py` (363 lines), `serve/nodes/negative.py`, their nodes and edges
   in `serve/graph.py` (line 212 and the conditional edges), and the `reflect_enabled` and
   `negative_tau` knobs. Delete `tools/score_reflector.py`.
2. Delete `Layer.COST` from `govern/layers.py`, its branch in `govern/check.py`, and the
   `cost_budget` knob. Six layers remain.
3. In `register/knobs.py`, delete `Role` and the comparability and resume keys; keep each
   setting's name, default and environment variable, and the function that hashes the effective
   settings for the turn record. Update `eval/provenance.py` and `eval/report.py` to compare the
   hash and list changed settings, without roles.
4. Delete `register/citations.py`, its caller, and `measure/gates.py` if, after reading it, it is
   only quotability gating. Keep `register/assets.py`, `quantity.py`, `record.py`, `facets.py`,
   `stages.py`, `prompts.py`, `arm_profiles.py` and `arms.toml` (finding 12).
5. **Settings live in the repository.** Every setting the engine reads (the model ids and
   efforts, retries and timeouts, the agent and rail node timeouts, the recursion limit, the
   corpus path, and the checkpoint and vector-cache paths) is a named default in the one
   settings module this milestone leaves in place of `register/knobs.py`. A real environment
   variable `GOVERNED_BI_*` overrides one for a run, and the override enters the config hash.
   Settings are not read from `.env`: a `.env` that sets anything other than the secrets in
   task 6 stops startup with a message naming the key, because an override nobody can see in
   the repository is how two runs drift apart.
6. **Secrets live in `.env` and nowhere else.** The secrets are `OPENAI_API_KEY`,
   `GOVERNED_BI_PG_DSN` (the whole DSN, since a real one carries a password) and, for tracing,
   the `LANGSMITH_*` variables, which LangSmith reads itself. `credentials.py` reads them from
   the environment, then `.env`, and they never enter the config hash or the turn record.
7. **One reader, called once.** Each entry point (`api/graph_app.py`, `serve/__main__.py` and
   the eval driver) builds the settings and the secrets once and passes them down. Delete the
   direct `os.environ` reads in `api/graph_app.py`, `model/provider.py`,
   `serve/nodes/agent_core.py`, `serve/graph.py`, `serve/checkpointer.py`,
   `retrieve/vector_cache.py` and `register/knobs.py::env_override`. Today some of these read
   `.env` through `credentials` and others see it only when a launcher copied it into the
   environment (`serve/__main__.py:58` says so), so the server and an eval run can resolve
   different settings from the same files.
8. Delete the matching tests; update `docs/architecture.md` and `docs/usage.md#environment`.

**Deliverables:** the PRs.

**Sign-off criteria**
- [ ] The snapshot matches its base. Expected differences: suite rows whose layer was `COST`, if
      any, listed before the run.
- [ ] `uv run --frozen python -c "from governed_bi.govern.layers import Layer; print(len(Layer))"`
      prints `6`.
- [ ] `git grep -nE "reflect_node|negative_node|reflect_enabled|negative_tau|cost_budget|Role\.comparability|register\.citations" -- src tests tools`
      returns nothing.
- [ ] A test asserts that changing one setting changes the recorded config hash.
- [ ] `git grep -nE "os\.environ|getenv" -- src` returns only the settings module,
      `credentials.py`, and the two platform flags `LANGSERVE_GRAPHS` and
      `__LANGGRAPH_DEFER_LOOPBACK_TRANSPORT`, each with a one-line reason.
- [ ] A test builds the settings through the server's entry point and through the eval
      driver's from the same environment and `.env`, and gets the same resolved settings and
      config hash.
- [ ] A test puts `GOVERNED_BI_MODEL=x` in a temporary `.env` and startup fails naming that
      key; another sets a fake `OPENAI_API_KEY` and finds its value in neither the config hash
      nor a recorded turn.

**Out of scope:** renaming (P8); the gateways and their variables (P7).

**Stop and ask the owner if:** removing a node changes any snapshot row (it was not off), or
`measure/gates.py` turns out to hold something the paired test or coverage needs.

**Size:** about 30 files; roughly 2,500 lines removed and 200 added for the settings module
and its tests.

---

### P7. Choose the model setup, and delete the other gateways and unused tools

**Goal:** one model setup runs the app and the eval, and `tools/` holds only what that work uses.

**Depends on:** P6.

**Tasks**
1. Support OpenAI only (decision 1). Delete `model/proxy_gateway.py`, `model/proxy_embedder.py`
   and `model/bedrock_embedder.py`, and remove every non-OpenAI branch from `model/provider.py`
   and `model/embedder.py`, including the `bedrock_converse` path and the `proxy` refusal. Keep
   `model/deterministic_embedder.py`, which the snapshot and the tests use.
2. Make the code defaults match the chosen setup: agent and utility model `gpt-6-luna`,
   embeddings `text-embedding-3-large`, provider `openai`, so a fresh checkout with no
   `GOVERNED_BI_*` variable set runs the same setup.
3. Delete the `bedrock` extra (`pyproject.toml:125`), run `uv lock`, and change
   `uv sync --frozen --extra bedrock` in `ci.yml` to `uv sync --frozen` in every job.
4. Delete the tools with no use after the pivot (decision 8): `routing_recall.py` (the snapshot
   replaces it, finding 29), `fanout.py`, `bi_scope_probe.py`, `densify_summaries.py`,
   `graft_corpus_fields.py`, `query_summary_alignment.py`, `score_reflector.py` (its node goes in
   P6), `shadow_replay.py`, and `eval/shadow.py` and `eval/replay.py` if nothing kept imports
   them. Ten stay: `check_train_only.py`, `check_no_benchmark_discriminators.py`,
   `run_datalake_eval.py`, `datalake_report.py` (cut down to accuracy, coverage, the two halves
   and disagreement, which P16 reports), `regrade.py`, `selective_curve.py`, `smoke_api.py`
   (cut to the four remaining routes in P4),
   `load_demo_schema.py`, and the two snapshot tools until P17.
5. **Delete the variables that only chose between gateways or named a second database:**
   `GOVERNED_BI_PROVIDER` and the three `*_PROVIDER` variables, `GOVERNED_BI_AWS_REGION`, the
   three `GOVERNED_BI_PROXY_*` variables, and the `PG_RENAME_DECOY_DSN` alias, so
   `credentials.PG_DSN_NAMES` becomes the one name `GOVERNED_BI_PG_DSN`. Rewrite `.env.example`
   as the secrets of P6 task 6 and one sentence pointing at the settings module; its history
   and explanations are deleted or move into that module's docstrings within the comment budget.
6. Delete the tests and docs of everything removed.

**Owner, after merge:** cut the local `.env` to the same secrets, with the BIRD DSN under
`GOVERNED_BI_PG_DSN`. Startup refuses anything else from P6 on, so this cannot be skipped
silently.

**Deliverables:** the PRs.

**Sign-off criteria**
- [ ] The snapshot matches its base (it uses the deterministic embedder, which stays).
- [ ] `ls tools/*.py | wc -l` is 10.
- [ ] `git grep -niE "proxy_gateway|proxy_embedder|bedrock|langchain_aws|boto3" -- src tests tools pyproject.toml uv.lock .github`
      returns nothing.
- [ ] With no `GOVERNED_BI_*` variable set, the resolved settings name `gpt-6-luna` for both
      models and `text-embedding-3-large` for embeddings.
- [ ] `git grep -nE "PG_RENAME_DECOY_DSN|GOVERNED_BI_(MODEL_|UTILITY_|EMBEDDING_)?PROVIDER|GOVERNED_BI_AWS_REGION|GOVERNED_BI_PROXY_" -- src tests tools docs/usage.md .env.example`
      returns nothing.
- [ ] The only variable names `.env.example` assigns, commented or not, are `OPENAI_API_KEY`,
      `GOVERNED_BI_PG_DSN` and `LANGSMITH_*`.
- [ ] `uv run --frozen python tools/smoke_api.py --help` exits 0.

**Out of scope:** model tuning; the baseline run.

**Stop and ask the owner if:** a kept eval path needs a deleted gateway, or `gpt-6-luna` is no
longer listed by `/v1/models` when the milestone starts.

**Size:** 15 to 25 files deleted; roughly 4,000 lines removed.

---

### P8. Plain names in the engine

**Goal:** the engine's code uses the plain names of memo §6.1, and the snapshot shows the same
values under the new field names.

**Depends on:** P7.

**Tasks**
0. Start fresh (decision 9). Stop any running `langgraph dev`, then move `runs/conversations.sqlite`
   and `.langgraph_api/` into `runs/archive/<date>/`, so the renames below cannot delete a thread
   registry anyone needs. Keep the pickled-name guard in `register/quantity.py`, and update its
   list of names in the same PR as any rename of those classes.
1. Rename in `src/` and `tests/`, one PR per group of rows: retrieval (`facet` to `channel` inside
   `retrieve/` and `serve/nodes/facets.py`, `licensed` to `retrieved_tables`,
   `r_table_not_licensed` to `r_table_not_retrieved`); settings (`register/knobs.py` to
   `settings.py`, `knob_default` to `setting_default`); runs (`arms.toml` to `configs.toml`,
   `--arm` to `--config`, treatment hashes to config hash); nodes and checks (`stamp` to `finish`,
   `CheckVerdict` to `CheckResult`, gate to check, and the remaining rows).
2. Rewrite the sentences around each rename, as memo §6.2 asks, in the files touched.
3. Rename the snapshot fields in `tools/pivot_snapshot.py` in the same PR, and list them.
4. Leave HTTP field names alone; P9 changes them with the UI.

**Deliverables:** one PR per group.

**Sign-off criteria**
- [ ] Each PR's snapshot matches its base with only the listed field renames.
- [ ] `git grep -nwE "facet|facets|licensed|knob|knobs|stamp|CheckVerdict" -- src tests tools`
      returns only HTTP field names that P9 owns, listed in the PR.
- [ ] `uv run --frozen python -m governed_bi.eval --help` shows `--config` and not `--arm`.

**Out of scope:** `runs/`, accepted ADRs, the UI.

**Stop and ask the owner if:** a rename would change a stored checkpoint key that existing
conversations in `runs/conversations.sqlite` read (decide whether to drop old threads).

**Size:** mechanical; 100 to 150 files touched, net lines about zero.

---

### P9. Plain names at the HTTP boundary, in the docs, and the check

**Goal:** the HTTP fields, the UI that reads them and the docs use the plain names, and CI keeps
the coined terms out.

**Depends on:** P8.

**Tasks**
1. Rename the HTTP fields in one PR with the UI code that reads them: `licensed`,
   `licensed_count`, `knobs_resolved`, `facet_hits`, `facet_degraded`, `facet_channels`,
   `reflect_verdict` if it survived P6. Regenerate `docs/openapi.json`.
2. Rewrite the live docs under `docs/` (not `runs/`, accepted ADRs or reversal records).
3. Add the mapping note to the top of `docs/glossary.md`.
4. Write `tools/check_plain_names.py`. It reads the coined terms from one file
   (`docs/plain-names.toml` or a section of the glossary) and fails on them in Markdown, and in
   Python and TypeScript comments, docstrings and identifiers. Generic words ("shape", "surface",
   "control", "population", "register", "fold") are listed as guidance and not failed on
   (finding 23). It has an allowlist and skips `runs/`, accepted ADRs and the mapping note. Add it
   to the `test` job.
5. With the owner's consent, add one line to `AGENTS.md` pointing at the term list.

**Deliverables:** the PRs, the check and its test.

**Sign-off criteria**
- [ ] The snapshot matches its base (field names only).
- [ ] `uv run --frozen python tools/check_plain_names.py` exits 0 on the tree, and exits 1 on a
      fixture file containing "licensed set" in a docstring.
- [ ] `grep -cE "\"(licensed|knobs_resolved|facet_hits)\"" docs/openapi.json` prints 0.
- [ ] By hand: one question answered in the UI shows its SQL and table as before.

**Out of scope:** the UI redesign.

**Stop and ask the owner if:** the check fails on more than 20 lines of third-party or generated
code, which means the term list needs narrowing.

**Size:** 40 to 60 docs and code files; one new tool of about 150 lines.

---

### P9b. Comments cut to decisions

**Goal:** comments and docstrings say only why the code is the way it is, within a fixed length,
and CI keeps it that way. The owner found that the prose misleads more than it helps: on
`b205ad2`, 38% of the non-blank lines in `src/governed_bi` are comments (10%) or docstrings
(28%), 220 docstrings and 18 comment blocks run past 15 lines, and the longest are a 73-line module
docstring in `serve/checkpointer.py` and a 35-line comment block in `register/quantity.py`.

**Depends on:** P9. It comes after the deletions, which remove many of the longest blocks
(`conform/`, the knob registry, the gates), and after the renames, which rewrite words inside
comments anyway.

**Why not delete every comment.** The 2026-09-18 outside review read a comment-stripped copy and
re-filed four settled decisions as defects. The code shows what it does, not why the obvious
alternative was rejected, so the one sentence that says why stays and everything around it goes.

**Tasks**
1. Write `tools/check_comment_budget.py`, with its test, and add it to the `test` job. It reads
   `src/governed_bi`, `tests/` and `tools/`, and fails on:
   - a run of `#` comment lines longer than 3;
   - a function or class docstring longer than 5 lines, or a module docstring longer than 10,
     except module docstrings under `tools/`, which are `--help`;
   - in comments and docstrings, history words: `used to`, `no longer`, `previously`,
     `was changed`, or a date of the form `20YY-MM-DD`. ("Until" is left out because it
     usually describes a loop, not history.)

   It takes no allowlist file. A block that cannot meet the budget is a design decision, and it
   moves to an ADR with a one-line pointer.
2. Sweep one package per PR (or per subagent), largest first: `serve/`, `retrieve/`, `api/`,
   `eval/` and `measure/`, then whatever else remains, then `tests/`, then `tools/`. Each
   comment either becomes the one to three sentences of its decision or is deleted. A comment
   that restates the code is deleted. A comment that contradicts the code is fixed to match the
   code, and the PR lists it, since that is a stale claim someone may have relied on.
3. Move any rationale too long for the budget and not yet in an ADR into the ADR it belongs to,
   or into a new one, and leave a one-line pointer.
4. Record the new comment and docstring share in section 8.

**Deliverables:** the check and its test, the sweep PRs, any ADR additions.

**Sign-off criteria**
- [ ] `uv run --frozen python tools/check_comment_budget.py` exits 0 on the tree, and exits 1 on
      a fixture with a four-line comment block, on one with a six-line function docstring, and on
      one whose comment says "used to".
- [ ] The comment and docstring share of non-blank lines in `src/governed_bi` is under 15%,
      measured the same way as the 38% above, and section 8 records it.
- [ ] The snapshot matches its base exactly. Comments cannot change behaviour, so any difference
      fails.
- [ ] Every PR lists the comments it found contradicting the code.

**Out of scope:** changing code to make a comment true. If a comment shows the code is wrong, it
is filed as a separate fix, not made in the sweep.

**Stop and ask the owner if:** a package cannot reach the budget without deleting a decision that
no ADR records, or if more than ten comments contradict the code, which would mean the code needs
reviewing before its prose.

**Size:** one new tool of about 120 lines; 100 to 150 files touched; net several thousand lines
removed.

---

### P10. Assumptions on every answer, checked by the server

**Goal:** every answered turn carries a list of assumptions, each marked `layer`, `convention`,
`guess` or `found_in_query`, checked against the retrieved assets and the executed SQL, and
stored on the turn record.

**Depends on:** P9b.

**Tasks**
1. Add an `answer` tool to `serve/tools.py` (decision 2), taking `answer_text` and
   `assumptions`, each entry with `statement`, `about`, `basis` and `asset_id`, modelled in a new
   module `serve/assumptions.py`. Like the other tools it returns a `Command` into channels that
   `serve/agent_state.py::GovernedAgentState` declares, and it ends the agent's turn. Its body
   runs the checks in tasks 3 and 4, and when a `layer` claim names an asset that was not
   retrieved it replies with the problem so the agent can correct it, the way `run_query` replies
   to a refused statement. `narrate` adopts `answer_text` from the tool instead of the last AI
   message, and the UI reads the streamed tool-call arguments for the answer text. Ending the
   turn without calling `answer` is a fault the turn records, not a silent prose answer.
2. Update the system prompt in `register/prompts.py` to ask for every assumption, with the
   examples 0018 §1 gives.
3. Downgrade a `layer` claim to `guess` when its `asset_id` does not exist or is not among the
   turn's retrieved assets.
4. Parse the delivered statement (the one whose rows became `result_table`; finding 19) with
   sqlglot, using the statement as it was before `govern/pipeline.py::apply_row_limit` added
   `LIMIT max_rows + 1` (the prepared statement's `canonical` text or the raw statement; finding
   33). Add `found_in_query` entries for: a filter on a literal the question does not contain, an
   inner join, `DISTINCT`, a `LIMIT` the model wrote, and a suspect column (only reachable when
   `hard_block_suspect` is off).
5. Name the new fields so they avoid the words the tests forbid (finding 34): no `confidence`,
   `certainty`, `tier`, `probability`, `trust_score`, `reliability_score`, `safety_clearance` or
   `semantic_assurance`. Reword the `NARRATE` prompt line at `register/prompts.py:421` so it does
   not forbid stating assumptions.
6. Store the list on `answer.record` in `serve/nodes/finish.py` (the renamed `stamp.py`) and
   expose it in the turn projection in `api/thread_turns.py`. Regenerate `docs/openapi.json`.
7. Tests with `serve/scripted_model.py`: a fabricated `asset_id` is downgraded; each of the five
   query patterns produces exactly one `found_in_query` entry; a statement with no `LIMIT` of its
   own produces no `LIMIT` entry, even though the executed text has one; a turn with no query has
   none.

**Deliverables:** the module, the prompt change, the tests, the record field.

**Sign-off criteria**
- [ ] `uv run --frozen pytest -q tests/serve/test_assumptions.py` passes with at least the eight
      cases in task 7.
- [ ] `tests/serve/test_the_abstention_policy_is_declared.py` and
      `tests/api/test_http_contract_answer_and_stream.py` pass unchanged.
- [ ] A test asserts the server adds no model call: the scripted model's call count is the same
      with and without the check.
- [ ] The turn projection of an answered scripted turn includes `assumptions` with every entry's
      `basis` in the four allowed values.
- [ ] Section 8 records the snapshot result (expected: unchanged, since the snapshot does not run
      the agent).

**Out of scope:** asking instead of guessing (P11); the UI (P14).

**Stop and ask the owner if:** the model setup cannot return the structured answer reliably in a
ten-question hand trial, or the record grows a checkpoint test past its limit
(`tests/serve/test_trust_boundary_and_checkpoint_growth.py`).

**Size:** 6 to 10 files; about 600 lines added, mostly tests.

---

### P11. A guess between two definitions becomes a question, and assumptions are measured

**Goal:** a guess about a phrase the layer defines more than one way turns into a clarifying
question, and every eval run reports the assumption figures of 0018 §5.

**Depends on:** P10.

The abstention policy cannot do this where it sits (finding 32): it runs before the agent, it
can only decline, and assumptions exist only after the agent answers. The owner chose
(decision 10) to enforce it in the `answer` tool from P10: the tool refuses an answer that holds
an ambiguous guess and tells the agent to call `ask_user` instead. Code enforces it, the graph does
not change, and ADR 0013 stays as it is.

**Tasks**
1. Define "defined more than one way" as two or more term assets retrieved for the turn whose
   `name` or `synonyms` match the assumption's `about`, case-folded, and whose bindings differ. A
   single term holds only one `binding` (`corpus/schema.py`, `TermAsset`), so the case only
   arises across terms (finding 18).
2. In the `answer` tool, refuse an answer with a `guess` on such a phrase, replying with the
   competing definitions so the agent can call `ask_user` with `basis` `data_definition`, which
   already exists in `serve/clarification.py:34`. No new basis or abstention reason is needed,
   and any new name avoids the words finding 34 lists.
3. Add the guess count, and a count of answers the tool refused for this reason, to the turn
   record so P16 can report both.
4. Add to the run report (`eval/report.py`): assumptions per answer by basis; accuracy with at
   least one guess and with none, with counts; and `found_in_query` entries the model did not
   state.
5. Tests on a small fixture corpus with two terms that share a phrase and bind differently: an
   ambiguous guess makes the tool refuse and the scripted model's next call is `ask_user`, so the
   turn ends in an interrupt; an unambiguous guess answers; the report figures on a three-row
   fixture.

**Deliverables:** the rule, the report section, the tests.

**Sign-off criteria**
- [ ] The new tests in task 5 exist and pass, and `tests/eval/test_report*.py` passes.
- [ ] `tests/serve/test_the_abstention_policy_is_declared.py` and the clarification vocabulary
      tests pass unchanged.
- [ ] On BIRD-corpus, a script lists exactly the phrases task 1 finds (two on 2026-10-19:
      "predominant" in `mondial_geo` and "name" in `superstore`), and the result is in section 8.
- [ ] On the report fixture, the three figures match hand-computed values written in the test.

**Out of scope:** tuning when to ask; that waits for P16's numbers.

**Stop and ask the owner if:** the scripted model can end a turn around the refusal without
calling `answer` or `ask_user`. BIRD has only two ambiguous phrases, so this rule is tested on the
fixture, not measured on BIRD; that is known and is not a reason to stop.

**Size:** 6 to 8 files; about 400 lines added.

---

### P12. What the new page needs from the server

**Goal:** a stored answer keeps the rows the model saw, a clarifying question carries its
choices, and one endpoint returns example questions from the layer.

**Depends on:** P9b.

**Tasks**
1. Store the delivered `result_table` (the first 20 rows `run_query` keeps, `serve/fetch.py:383`,
   with `row_count` and `truncated`) on the turn entry in `serve/state.py::TurnEntry`, outside
   `record` (decision 3). Compaction of archived turns may drop it, oldest first. Delete the
   comment in `serve/nodes/stamp.py` (around line 438) that cites ADR 0006 §11's withdrawn
   retention table.
2. Add an optional `choices` list (short strings, at most four) to the `ask_user` tool in
   `serve/tools.py` and to `serve/clarification.py::interrupt_payload`, extending the payload
   without renaming `kind`, as its docstring requires (finding 34). Update the clarification
   schema in `ui/lib/schemas.ts` so the client accepts it.
3. Add `GET /examples?limit=3`, returning few-shot questions from the corpus, and
   `GET /examples?near=<text>&limit=3` for the refusal, ranked with the existing lexical index in
   `retrieve/lexical.py`. Regenerate `docs/openapi.json`.
4. Tests: a 150-row result is stored as its first 20 rows with `row_count` 150 and `truncated`
   true; reloading a thread returns the
   rows without a database call; an `ask_user` call with two choices surfaces both in the
   interrupt value, and one without choices still works; the examples endpoint returns only
   few-shot questions from the train split.

**Deliverables:** the record change, the endpoint, the tests.

**Sign-off criteria**
- [ ] The five tests in task 4 exist and pass.
- [ ] `tests/serve/test_a_pause_survives_a_restart_on_disk.py` passes with a payload that has
      choices.
- [ ] `tools/check_no_benchmark_discriminators.py` passes, and a test asserts no example returned
      is a `test_final.jsonl` question.
- [ ] The checkpoint growth test still passes; record its new figure in section 8.

**Out of scope:** the page.

**Stop and ask the owner if:** storing the rows makes a checkpoint exceed the growth test's
limit.

**Size:** 5 to 8 files; about 300 lines added.

---

### P13. The new page, part 1: sidebar, conversation and running line

**Goal:** the chat page has the sidebar, single column, composer and running status line of 0019
§1 and §3, in the style of `variant-11-simple.html`.

**Depends on:** P9b (names and comments) and P4 (one page).

**Tasks**
1. Rebuild `ui/app/page.tsx` and `ui/app/layout.tsx` with a sidebar ("New conversation",
   "Waiting for you" from `/clarifications/pending` with an amber dot, then conversations by date
   from `lib/threads.ts`), a 720 pixel column, and the composer with its one line of small print.
2. Keep the stream handling in `hooks/use-stream-chat.ts` and `lib/clarification.ts`; replace the
   components around them. Keep `?thread=<id>` as the conversation URL.
3. Show one grey status line from the stage events (ADR 0010), naming the current stage.
4. Style per 0019 §4: the mockup's colours (`--text #0d0d0d`, `--muted #6b6b6b`, `--bubble #f4f4f4`),
   no badges, no dark mode.
5. Rewrite `ui/tests/` for the new components, and delete tests of removed ones.

**Deliverables:** the page shell and its tests.

**Sign-off criteria**
- [ ] `npm test` passes, with tests for the sidebar order (waiting first), the date groups and the
      status line.
- [ ] `npm run build` passes.
- [ ] By hand: a paused clarification shows under "Waiting for you" with a dot, and disappears
      from it once answered.
- [ ] By hand: while a question runs, exactly one status line is visible and it changes as stages
      pass.

**Out of scope:** the answer's contents (P14).

**Stop and ask the owner if:** `useStream` cannot drive the sidebar without a second client.

**Size:** about 15 UI files; roughly 1,200 lines added, the old chat components removed.

---

### P14. The new page, part 2: the answer and the other states

**Goal:** an answer, a clarifying question, a refusal and a new conversation look and behave as
0019 §2 and §3 describe.

**Depends on:** P10, P12, P13, and P6 (six checks).

**Tasks**
1. Answer: one or two sentences, the result table from the record, "To answer this, I assumed:",
   each assumption with its ending (grey source for `layer`, amber "This is a guess." and
   "Change" for `guess`, "The query does this, though you didn't ask for it." for
   `found_in_query`), and "Copy" and "Details".
2. "Change" sends a follow-up in the same thread with the corrected assumption in the question
   (0018 §4).
3. "Details": where each part came from (physical names in monospace), the SQL, the six checks,
   the model and the time. Build it from the parse P10 wrote.
4. Clarifying question with outlined buttons for the `choices` P12 added to the interrupt
   payload, falling back to free text when there are none, and free text always available; refusal with up to three
   nearby questions from `/examples?near=`; new conversation with three examples from
   `/examples`.
5. Tests for each state, and for "Change" producing the right follow-up text.

**Deliverables:** the components and tests.

**Sign-off criteria**
- [ ] `npm test` passes with one test per state in task 4, one for each assumption basis, and one
      for "Change".
- [ ] `grep -rnE "badge|Badge|dark:" ui/components ui/app` returns nothing.
- [ ] By hand, with the chosen model: one answered question, one clarification and one refusal
      each match the corresponding panel of `variant-11-simple.html`; screenshots go in the PR.
- [ ] By hand: reloading an old conversation shows its table without a new query (no `run_query`
      event in the server log).

**Out of scope:** the "Open in the layer viewer" link (after V3 and P15).

**Stop and ask the owner if:** the Details parse disagrees with the executed SQL on any of the
three hand questions.

**Size:** about 15 UI files; roughly 1,500 lines added.

---

### P15. The Ossie exporter

**Goal:** `python -m governed_bi.corpus.ossie_export` writes one valid Ossie model per database,
57 for `../BIRD-corpus`, with a report of what it could not map.

**Depends on:** P0. Independent of P2 to P14.

**Tasks**
1. Write `src/governed_bi/corpus/ossie_export.py`, reading through `corpus/store.py`. Map per
   0020 §1, with these additions (finding 21): parse each join's `on` into `from_columns` and
   `to_columns`; flip `one_to_many` so `from` is the many side; put `many_to_many` joins in a
   model-level `custom_extensions` list under `GOVERNED_BI`; report the two `talkingdata` joins
   without cardinality; put term synonyms in `ai_context.synonyms`; write
   `"version": "0.2.0.dev0"` and record the Ossie commit in the extension.
2. Write a reader for the exporter's own output and a round-trip comparison that lists what is
   lost.
3. Add `jsonschema`, `referencing` and `pyyaml` to the dev group if missing, and a test that
   runs `../ossie/validation/validate.py` on every exported model. Skip the test with a printed
   reason when `../ossie` is absent, so CI stays green.
4. Count metric expressions sqlglot cannot parse, in the export report.
5. Document the command in `docs/corpus-format.md`.

**Deliverables:** the module, the test, the export report for BIRD.

**Sign-off criteria**
- [ ] The export of `../BIRD-corpus` writes 57 files.
- [ ] All 57 pass `uv run --frozen python ../ossie/validation/validate.py <file>`.
- [ ] The report lists 112 many-to-many joins, 2 joins without cardinality, and a stated count of
      unparsed metric expressions.
- [ ] The round-trip test passes, and its list of lost fields matches a list written in the test.
- [ ] `git grep -n "ossie" -- src` returns only `corpus/ossie_export.py` and its reader.

**Out of scope:** reading Ossie as input (waits for 0.2.0).

**Stop and ask the owner if:** `validate.py` rejects a structure 0020 relies on, or the Ossie
`main` moves past `53418ba` with a schema change before this lands.

**Size:** 3 to 4 files; about 700 lines added.

---

### P16. The baseline

**Goal:** execution accuracy at a stated coverage, measured twice with the chosen model setup on
all 1,351 questions, with the assumption figures, recorded as the baseline later runs compare with.

**Depends on:** P11, P7.

**Tasks**
1. Run the baseline configuration twice with `tools/run_datalake_eval.py` (or
   `python -m governed_bi.eval`), resuming crashed rows.
2. Report execution accuracy and coverage for the whole set, and separately for the 28 `dev` and
   29 `holdout` databases in `docs/data/db-split.csv`.
3. Report run-to-run disagreement and the assumption figures of P11.
4. Record cost per question from the usage rows.
5. Write the result into `docs/measurement.md` and section 8.

**Deliverables:** two run directories under `runs/eval/`, the report, the doc update.

**Sign-off criteria**
- [ ] Both runs cover 1,351 questions with no crashed rows after resume.
- [ ] Section 8 holds accuracy, coverage and disagreement for both runs, by half and in total.
- [ ] The run manifest names engine commit, corpus commit, model and split.

**Out of scope:** tuning to raise the number.

**Stop and ask the owner if:** run-to-run disagreement exceeds 15%, or the two runs' accuracy
differs by more than 2 points.

**Size:** no code expected; documents only. Wall time is the cost.

---

### P17. Documents for what is left

**Goal:** the README and `docs/` describe only what exists, and the snapshot tools are gone.

**Depends on:** P14, P15, P16.

**Tasks**
1. Rewrite `README.md`, `docs/README.md`, `docs/architecture.md`, `docs/usage.md` and
   `docs/failure-modes.md`. State plainly that per-user data access needs database roles (0017,
   Consequences).
2. Close or delete `docs/open-work.md` items about deleted code.
3. Delete `tools/pivot_snapshot.py` and `tools/pivot_compare.py` with their tests.
4. Fill in the "End" column of section 8.

**Deliverables:** the documentation PR.

**Sign-off criteria**
- [ ] `uv run --frozen python tools/check_plain_names.py` exits 0.
- [ ] `git grep -nE "review page|return path|access policy|display level|browse API" -- README.md docs ':!docs/adr' ':!docs/reviews'`
      returns nothing.
- [ ] The End column of section 8 has no empty cell.

**Out of scope:** new features.

**Stop and ask the owner if:** a document describes a behaviour no test covers.

**Size:** 8 to 12 Markdown files.

---

## 4. Order and parallelism

| Milestone | Depends on | Can run beside |
|---|---|---|
| P0 Accept and record | | V0 |
| P1 Snapshot | P0 | P15, V0 to V3 |
| P2 Access and identity | P1 | P15 |
| P3 Return path | P2 | P15 |
| P4 Browse API and pages | P3 | P15 |
| P5 Repository checks | P1 (run after P4) | P15 |
| P6 Off-by-default parts, plain settings | P5 | P15 |
| P7 Model setup, gateways, tools | P6 | P15 |
| P8 Plain names in the engine | P7 | P15 |
| P9 Plain names at the boundary | P8 | P15 |
| P9b Comments cut to decisions | P9 | P15 |
| P10 Assumptions | P9b | P12, P13 |
| P11 Ask on ambiguous guesses, measure | P10 | P13 |
| P12 Server support for the page | P9b | P10, P13 |
| P13 Page part 1 | P4, P9b | P10, P11, P12 |
| P14 Page part 2 | P6, P10, P12, P13 | P16 setup |
| P15 Ossie exporter | P0 | everything from P1 to P14 |
| P16 Baseline | P7, P11 | P14 |
| P17 Documents | P14, P15, P16 | |

The deletions P2 to P6 run in sequence because they share files (`api/routes.py`, `ci.yml`,
`register/knobs.py`) and each compares against the previous one's snapshot. P15 is the only
governed-bi milestone that can run at any time after P0. With one developer, the useful
parallelism is between governed-bi and the viewer.

---

## 5. The viewer project

A new repository, with no backend, that opens an Ossie file and shows it whole (0020 §2). The
same rules as section 2 apply, with checks defined in V0.

### V0. The repository and its checks

**Goal:** an empty static web app with lint, types, tests and a build that pass in CI.

**Depends on:** nothing. The name and owner are settled (decision 4).

**Tasks:** create the repository; pick the toolchain (the chat UI uses Next.js, vitest and
TypeScript; a static Vite app is enough here); add CI running `npm ci`, `npm run lint`,
`npx tsc --noEmit`, `npm test` and `npm run build`; a README.

**Sign-off criteria**
- [ ] CI is green on the first PR.
- [ ] `npm run build` produces static files that open from disk with no server.

**Out of scope:** any view. **Stop and ask the owner if:** the licence or the organisation for the
repository is unclear. **Size:** about 15 files of scaffolding.

### V1. Load, validate, and draw the dataset graph

**Goal:** a file opened from a picker, a URL or a drop is validated against the Ossie schema, its
version is shown, and its datasets and relationships are drawn as a graph.

**Depends on:** V0.

**Tasks:** bundle `core-spec/ossie-schema.json` from `apache/ossie` at a recorded commit, and the
0.1.x schema from the 0.1.1 release; validate in the browser; show the version opened, or the
validation errors; draw datasets as nodes and relationships (from the many side to the one side)
as edges.

**Sign-off criteria**
- [ ] Opening each of `../ossie/examples/*.yaml` shows its version and no error, or a stated error
      that `validate.py` also reports for that file.
- [ ] A test feeds an invalid file and checks the error names the failing path.
- [ ] By hand: `tpcds_semantic_model.yaml` draws every dataset, with one edge per relationship.

**Out of scope:** field and metric views. **Stop and ask the owner if:** a 0.1.x example cannot be
told apart from `0.2.0.dev0` by its `version` field. **Size:** about 10 files, 800 lines.

### V2. Fields, metrics, AI context and extensions

**Goal:** each dataset's fields, the metrics and what they reference, `ai_context` at every
level, and each `custom_extensions` entry by vendor name with its `data` parsed.

**Depends on:** V1.

**Sign-off criteria**
- [ ] Tests render a fixture with one of each: field with `datatype`, metric referencing two
      fields, string and object `ai_context`, and an extension whose `data` is JSON.
- [ ] A metric's referenced fields are listed and each is a working link inside the viewer.
- [ ] By hand: the style is plain per 0019 §4 (no badges, no coloured cards).

**Out of scope:** editing. **Stop and ask the owner if:** extension `data` arrives as a string
that is not JSON in a real example. **Size:** about 10 files, 900 lines.

### V3. Links that open on one item

**Goal:** a URL can name a dataset, field or metric, and the viewer opens on it.

**Depends on:** V2.

**Sign-off criteria**
- [ ] Tests open `?file=<url>#dataset/<name>`, `#field/<dataset>.<field>` and `#metric/<name>` and
      check the right item is in view.
- [ ] An unknown name shows "not found in this file" and the file's overview.

**Size:** 3 to 5 files, 200 lines.

### V4. Test on real files

**Goal:** the viewer opens the Ossie examples and the exported BIRD layer without errors and
fast enough to use.

**Depends on:** V3 and P15.

**Sign-off criteria**
- [ ] All five files in `../ossie/examples` and all 57 exported BIRD models open with no console
      error.
- [ ] The largest exported model opens and draws in under 3 seconds on the owner's laptop;
      record the time.
- [ ] By hand: the 112 many-to-many joins appear under the `GOVERNED_BI` extension of their
      models.

**Then:** add "Open in the layer viewer" to the chat page's Details (0020 §2), a small follow-up
to P14.

**Stop and ask the owner if:** a BIRD model needs more than 3 seconds, which would mean drawing
the graph lazily. **Size:** fixes only.

---

## 6. What this plan deliberately leaves out

- The September milestones' process (second reviewer, PR size limits). 0017 ends them except for
  measurement that feeds P16.
- A new curator, Ossie as storage, and Ossie as input, which 0017 and 0020 reject or defer.

---

## 7. Open decisions

| Decision | Blocks |
|---|---|
| 1. **Decided 2026-10-19:** OpenAI only. Agent and utility model `gpt-6-luna`, the newest luna model the account lists (checked against `/v1/models` on 2026-10-19), embeddings `text-embedding-3-large`. The proxy and Bedrock paths are deleted | done; P7 builds it |
| 2. **Decided 2026-10-19:** a new `answer` tool that ends the turn and checks its own assumptions (finding 4) | done; P10 builds it |
| 3. **Decided 2026-10-19:** store the 20 rows the model saw on the turn entry; ADR 0006 needs no amendment because its retention table was withdrawn (finding 5) | done; P12 builds it |
| 4. **Decided 2026-10-19:** `ossie-viewer`, a public repository under the owner's GitHub account, to be offered to the Ossie project later | done; V0 creates it |
| 5. **Decided 2026-10-19:** the owner, on BIRD-corpus. No pilot user yet, so no milestone covers a second user's access or data | done |
| 6. **Decided 2026-10-19:** close PR #3 with thanks, crediting its `assumptions` finding in ADR 0018. P2 does not keep the fork mergeable | done in P0 |
| 7. **Decided 2026-10-19:** keep the September split rule: report `dev`, `holdout` and total; design from `dev` only; decide on `holdout` only (finding 22) | done; P11 and P16 follow it |
| 8. **Decided 2026-10-19:** delete every tool without a use after the pivot; P7 lists the ten that stay | done; P7 builds it |
| 9. **Decided 2026-10-19:** start fresh. Before P8, archive `runs/conversations.sqlite` and `.langgraph_api/` into `runs/archive/` | done; P8 does it |
| 10. **Decided 2026-10-19:** the `answer` tool refuses an answer with a guess on a phrase two retrieved terms bind differently, and the agent asks instead. ADR 0013 is unchanged (finding 32) | done; P11 builds it |
| 11. **Decided 2026-10-19:** the engine sorts `pulled_in` in `serve/context.py`, as P1's first PR, and the snapshot also pins the seed (finding 28) | done in P1 |

---

## 8. Numbers

| Metric | Start (2026-10-19, `eaba82a`) | End |
|---|---|---|
| Lines in `src/governed_bi` (Python) | 42,907 in 151 files | |
| Lines in `tests/` (Python) | 51,968 in 193 files | |
| Tests collected | 2,126 | |
| Lines in `ui/` (TS and TSX, excluding build folders; with tests / without) | 21,512 / 21,137 | |
| Python files in `tools/` | 39 | |
| `tools/check_*.py` | 12 | |
| Declared knobs or settings | 63 | |
| UI pages | 8 | |
| Snapshot SHA-256 and wall time | `b4f62e8c8b16518176ff223edbf30cf0fafcdf5e9f60dbfbc23116045fd231b4`, 387 s | |
| CI time of the `test` job | (P5) | |
| Checkpoint size after one turn | (P12) | |
| EX and coverage, two runs, total and by half | (P16) | |
| Comment and docstring share of non-blank lines in `src/governed_bi` | 38% (10% comments, 28% docstrings) | (P9b) |
