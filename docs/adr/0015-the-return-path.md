# 0015: The return path — reader feedback into the corpus

- **Status:** Accepted and built in part (2026-08-23). **Steps 0-6 are on `design/return-path`**:
  the `feedback/` package (the store, the closed vocabulary, the lifecycle table, the clusterer),
  the six CLI tools from `tools/import_eval_failures.py` through `tools/check_ratchet.py`, five new
  whole-tree conformance rules and the ratchet that pins their pre-existing findings, the
  `corpus_release` comparability knob, the steward's four verbs behind
  `GOVERNED_BI_FEEDBACK_ADMIN`, and the `/review` surface. `ServeState.raised`, `serve/raised.py`,
  `api/raised_write.py` and `api/clarification_routes.py` are **deleted** (`4a0d11a`), so the state
  carries 47 channels rather than 48. **Designed and not built, and named as design wherever it
  appears below:** the agentic triage pipeline (Reproducer, Diagnoser, Author, Curator, the
  `triage/` package), verification tiers T4 and T5, the reader's **categorised** capture surface (a minimal `raise-note.tsx` ships and writes; the category picker and `expected` field do not), `/reports`, and the
  re-ask action. Five measurements taken while building changed a decision recorded here; the retro
  below names each one.
- **Deciders:** project owner + design session (2026-08-23) — five independent proposals (intake,
  pipeline, verification, workflow, and a measured prototype), then three adversarial critiques.
- **Working reference:** [return path](../return-path.md) — the build order, the data shapes, the
  route table and the test names. This page is the decision and the reasoning; that page is what
  an engineer implements from.
- **Reading note.** Four figures in the Context section are measurements taken for this decision
  and are not in any other document. They are marked **measured** with the command that produced
  them.

> **What the build changed in this record.** **Five** measurements taken while building steps 0-6
> changed a decision on this page. The evidence is in `docs/open-work.md` §3.10a-3.10c and is
> deliberately not repeated, because this file is the decision record and that one is the work
> list.
>
> | measurement | the decision it changed |
> |---|---|
> | The `raised` channel held **zero** rows, on three independent checks | The migration needs no drain tool, so the channel deletion moved *earlier* — before the review screen was written against its contract — and the compatibility union was dropped outright |
> | Deleting the channel costs the **contract**, not the code | Decision 2's cost estimate; `docs/return-path.md` §1's "a rename with a deleted owner, not a rename with churn" was half wrong and now says so |
> | `corpus/store.py::write` writes a **second file with the same id** on an existing asset | A bundle is a `git apply` diff and never a directory copy (decision 4), `corpus/patch.py` exists at all, and conformance rule V23 ships despite finding zero |
> | `corpus/snapshot.py`'s `rmtree` **deleted a scratch directory of unrelated files** | The guard was fixed first, and the verification ladder was then built to apply the edit *in memory* so it never calls `snapshot` at all |
> | Complaints cluster **weakly** — largest cluster 3, 49% grouped, on the real 73 | **Open question 7 is answered, negatively.** The batching argument does not survive, so the review surface is a list with an optional grouping rather than a cluster-first screen |
>
> Two decisions were narrowed in the build and the pages say so: **T2 needs no database** (the
> corpus declares its own joins, so the resolver is offline and free) and **V18 is cut** (no live
> population, no calibrated false-positive rate — five new rules, not six). The capture UI and
> `/reports` are not built and are not planned in this cut: one principal holds every role here, so
> the input is the eval artifact rather than a person clicking.

---

## Context

### 1. The engine can be told it is wrong, and the telling goes nowhere

This is the tree as it stood when the decision was taken; `4a0d11a` deleted every module named in
this section. Two surfaces existed. `POST /turns/{turn_id}/raised`
(`api/clarification_routes.py:66`) let a reader file a note on a finished turn — `kind ∈
{from_refusal, wrong_answer}`, a free-text `note` capped at `RAISED_NOTE_MAX_CHARS = 4000` — and
appended a row onto the checkpointed accumulating channel `ServeState.raised`.
`GET /clarifications/pending` unioned those open rows with live `ask_user` interrupts and showed
them oldest first.

**Nothing closed an open row, and nothing acted on one.** `serve/raised.py::raised_row` wrote
`open: True` with the comment "until a later closer exists"; there was no later closer. The UI
told the reader "Filed. It is on the pending list." — which was true, and was the whole of what
happened. `ui/components/clarifications/pending-queue.tsx` stated the gap in its own docstring:

> the owner's decision routes an operator's answer into the semantic layer instead, and that path
> waits on a provenance gate the engine does not have yet.

This ADR is that gate.

### 2. Two populations, and they know different things

An analyst knows the business. They know that "active customer" excludes the ones on hold, that
last month's revenue figure was wrong by a factor of ten, that the engine counted every order
twice. They do not know `sales.orders.customer_id`, and a surface that asks them for an asset id
is a surface that collects confident wrong pointers.

An engineer or data steward knows the schema, can read the corpus, and — this is the brief's
premise — **has commit rights on the corpus repository**, so their edit lands through that repo's
own review and CI. The engine does not need to write to git. That asymmetry is the shape of the
whole design: the loop manufactures a reviewable, evidence-backed change; a human commits it.

### 3. The corpus is not in this repository, and that is load-bearing

`GOVERNED_BI_CORPUS_DIR` names a sibling checkout. `docs/open-work.md` §3.2: the corpus "is in
git and cannot be regenerated from this repository. This engine loads a versioned tree; it does
not write one." §3.10 leaves the `build_workers` knob deliberately red because "the curator is
not in this repository."

A design that quietly makes this repository the corpus author reverses three standing decisions
at once. This one does not: **no path added by this ADR writes into `GOVERNED_BI_CORPUS_DIR`.**

### 4. Four measurements taken for this decision

| | Finding | How |
|---|---|---|
| **M1** | **`corpus/store.py::write` cannot edit an asset.** Loading a table asset, changing its `summary`, and calling `write` produced a **second file carrying the same asset id**; `store.load` returned both with **zero problems**; `retrieve`'s `build_index` then raised `ValueError: duplicate index id`. And the write is a whole-file reformat: `store.py:256` is `yaml.safe_dump(to_mapping(asset), sort_keys=False, allow_unicode=True)` with no `width`, and `parse.py::to_mapping` omits defaults — so a round trip drops comments, reflows every string past 80 columns, drops explicitly-written defaults, and reorders keys into dataclass field order. | prototype, on a copy of the served corpus |
| **M2** | **Conformance catches one of three.** On three fresh copies of the served corpus: a `TermAsset.binding.target_id` pointing at a nonexistent asset is **caught** (V9, exit 1); a `MetricAsset.expression` naming a column absent from `base_table` is **not**; a `TermAsset` whose prose names a `governance.excluded` column is **not**; two assets sharing one id is **not**. On the BIRD corpus, 16/16 rules are green over 13,304 assets in 26 s — and **28 of 478 metric `expression` values do not parse as SQL at all**, and **23 metrics reference columns that resolve against nothing on their `base_table`**. | `tools/check_corpus_conformance.py --corpus-dir …`; `sqlglot` at the engine's `postgres` dialect |
| **M3** | **The prose-injection hole is in `body`, not `summary`.** Through the real `_visible` + `render_context`: an excluded `ColumnAsset` is correctly dropped and a table's inline columns correctly pruned, and the rendered block still contains another asset's `body` naming the excluded column. `summary` is **absent** from the block — `serve/context.py` says "`summary` never enters the prompt"; it enters the retrieval index. So ADR 0003's retro is right about the mechanism and imprecise about the field, and the two channels need different rules: `body` is a disclosure surface, `summary` is a retrieval-poisoning surface. **Zero assets are `governance.excluded` in either corpus**, so the rule has never had a live population. | offline harness over a corpus copy; `grep` for a content scanner in `corpus/validate.py` and the conformance tool returns nothing |
| **M4** | **The offline ladder is free, and one new asset does not re-embed the corpus.** Snapshot 0.21 s (0.89 MB, 179 files), `store.load` 0.47 s, `build_structure` 0.05 s, lexical `build_index` 0.03 s, whole-tree conformance 3.4 s, warm semantic `build_index` 0.27 s, `govern_bench` 1.7 s — none needing a credential except the last embed. A spy embedder over a copied vector cache measured a warm full build at **1** embed call and **+1 asset at 2** calls. | prototype timings on the served corpus |

M1 decides how a patch is written. M2 decides what the ladder must add. M3 decides which field a
content rule polices. M4 is why the ladder can be a gate rather than a report.

### 5. The precondition the brief assumed and the tree does not meet

**Measured: `../MS Fabric Facilities` — the corpus served when this was written — is not a git
repository.** It
has a `.gitignore` and no `.git`. `../BIRD-corpus` is one. So "the engineer commits and the corpus
repo's CI runs" is true of the benchmark corpus and false of the one in production.

Worse, its recorded identity does not reproduce. `.env:70` says "Verified with
`corpus.store.load`: 1,432 assets, 0 problems, one namespace, content hash `2f2b296e321d89ba`".
It measures `ddabcc43dc32b4a5…` unrestricted and `8fb6e79f4008d7de` under
`schemas=["facilities"]`. Neither matches, the asset count does, and **there is no history that
can say whether the tree moved or the note was wrong when it was written.**

That is the strongest argument this document has for its own existence, and it is also a
precondition: the return path's landing half needs a versioned tree. Until that corpus is one,
the loop can capture, triage, verify and hand off — and cannot tell `landed` from `superseded`.
One `git init` and a first commit; step 0 of the build order.

**Step 0 was settled the other way (`222d1bf`).** Rather than `git init` a tree with no history,
`.env` now serves `../BIRD-corpus`, which is a git repository, and `docs/corpus-format.md` carries
the swap because `.env` is gitignored. `../MS Fabric Facilities` is still not versioned, so the
landing half is still unavailable against it — the difference is that the corpus the engine serves
is now one where `landed` and `superseded` can be told apart, which is what the precondition
actually asked for.

### 6. What a patch cannot be verified on

`docs/open-work.md` §3.12: two runs of this engine with the configuration held fixed disagree on
**12.7%** of outcomes; `SE(net)` is ≈ 1.0pp unpinned and 0.83pp pinned; the smallest effect a
1,351-question arm resolves at 80% power is ≈ **2.3pp**. §1.5's largest single coverage bucket is
7 questions in one schema — **0.52pp**, a quarter of the detection floor. A full arm costs ~52 min
wall at `workers=10` and ~74M input tokens (`runs/eval/driver_v4.log`).

So pricing a one-asset patch on EX produces a confidence interval containing zero, and the
correct write-up of it is "we learned nothing" — which is what `eval/power.py::require_power`
exists to refuse in advance, and which had no caller when this was written — it has one now, and
§Decision 7 says where.

---

## Decision

### 1. Two layers, and the reader never authors a change

An **Observation** is what a reader saw, in business language, attributed to exactly one turn. A
**Patch** is a typed corpus change an engineer or an agent authors — in the built tree only an
engineer, because `Source` carries no `agent` member (see Decision 5). One observation has zero or
more patches, and **zero is a common, honest outcome** — an observation can be triaged to "the
engine was right", to "the warehouse is wrong", or to "this is an engine defect, not a corpus
gap", none of which is a corpus edit.

The layers are separate tables, not one row with nullable columns. Collapsed, the
`asset_id`/`field`/`was`/`becomes` columns are null on every reader-filed row, one observation
can no longer carry two changes — a missing synonym *and* a wrong join is one complaint — and one
pair of author/timestamp columns has to answer two questions.

The observation vocabulary is closed and the labels are what a reader reads, not what a curator
would write. The full table is in the [working reference](../return-path.md); the rule is: **the first tap always files something valid, refinement is never a
gate, and no choice ever names a table or a column.** `wrong_answer` survives as the "something
is wrong and I cannot say what" bucket, because a vocabulary that forces a taxonomy choice before
the complaint is filed loses the complaint.

Both layers are built: `feedback/events.py` holds the two shapes and the closed vocabulary,
`feedback/store.py` holds the two tables. There are two ways in and they are not equally exercised.
`POST /turns/{turn_id}/raised` ships **mounted and enabled** — it is the one write verb in this ADR
that is not behind the admin switch — and it has a caller: `ui/components/answer/raise-note.tsx`,
which predates this branch, renders on the answer card and files a note through a textarea. This
paragraph used to say no component called it and that the route was "a live writer with no caller";
that was wrong, and it understated the exposure named in Decisions 10 and §4.3 — there is a reachable
UI path that writes to an unauthenticated verb. What is absent is the *categorised* capture the
design specified (the picker, the `expected` field, the three states in the working reference's
§12.2), so a filed row carries no category. Every row that exists today arrived through
`tools/import_eval_failures.py`, which is a statement about what has been run, not about what can
reach the store.

### 2. `ServeState.raised` is deleted, and observations live in their own store

`runs/feedback.sqlite`, stdlib `sqlite3`, **synchronous**, in a new `feedback/` package. Every
observation is written there. `serve/raised.py`, `api/raised_write.py` and
`ThreadTurnLog.append_raised` / `raised_of` are deleted.

**The staging was decided, then measured away.** A big-bang deletion has an undesigned migration:
rows already sitting in checkpoints become unreachable, and "there is none, and the rows are
unreachable" is a sentence somebody has to sign. So the decision was to spread the deletion over
more than one commit — the store lands first and takes every new write, the channel keeps working
for what is already in it, `tools/drain_raised.py` walks the threads and copies those rows in, the
readers union the two sources while the drain has anything left to do, and the union goes when the
drain reports zero and holds. A named end condition, because a compatibility union with no exit is
how two sources of truth become permanent.

Then the channel was counted. **Zero `raised` rows, on three independent checks — the checkpoint
store, the harness store, and all 23 platform thread rows.** There was nothing to drain, so
`tools/drain_raised.py` was never written and the reader union was never built; `4a0d11a` deletes
the channel in one commit. What signs the sentence instead is an assertion: the paused-thread case
in `tests/api/test_an_observation_is_filed_on_a_turn.py` requires that the turn-log seam no longer
expose `append_raised` at all, so a second writer cannot reappear unnoticed. The migration risk the
staging existed to manage was a quantity nobody had counted, and counting it was cheaper than the
plan for it. What the deletion *did* cost was the wire contract — `docs/openapi.json` pinned
`RaisedRowResponse` with seven required fields and the spec test held four assertions over that
operation — which is the opposite of where this ADR expected the cost to fall.

What is explicitly *not* adopted is the cheaper alternative a critic argued for: keep the channel
as the immutable intake receipt and put dispositions in an append-only JSONL folded
last-write-wins at read time. It is genuinely less work in week one, and it is work that gets
thrown away — the fold exists only because the substrate cannot hold a mutable row, which is the
problem being solved. It also leaves the operator queue on the 40-round-trip thread walk below.

The channel fails on four counts, and the first is decisive: **an accumulating channel cannot
hold a row that changes.** `operator.add` means a close is a second row and every reader has to
fold. Then: it is not queryable — `threads.search`'s `values` filter is JSONB containment, and
the in-memory runtime implements it for a list-valued key as equality against the whole list, so
the operator queue is a full unfiltered scan of every thread (`api/thread_turns.py::_pending_async`
spends four paragraphs saying so). It is not sweepable — `Threads.sweep_ttl` is `return (0, 0)`,
so `langgraph.json`'s 90-day TTL is inert and the row is re-serialised into every later
checkpoint of the thread. And the write path is ~250 lines of loop-hopping
(`run_coroutine_threadsafe`, an `_in_flight` runtime probe, an `InFlightUnknown` fail-closed
degradation) that exists solely to write graph state safely.

**This re-opens an alternative ADR 0014 rejected by name** — "a hand-rolled SQLite table…
Rejected by the owner: the point is a LangGraph-native primitive, for maintainability." That
rejection was about the *turn record* and it was right about it: `ACCUMULATING` already existed,
so an accumulating channel on a durable checkpointer answered the requirement exactly. No native
primitive answers this one. The requirement is a **mutable row, queried across threads on fields
the checkpoint is not indexed on**, and 0014 itself rejected the LangGraph Store for the audit
index because `BaseStore.search()` has no sort parameter. A turn happens once; an observation is
edited four times.

Synchronous and not `aiosqlite`, deliberately: every loop-binding hazard `serve/checkpointer.py`
documents exists because the store shares the graph's loop. This one is written and read from
sync FastAPI handlers and never touches it.

What is given up, stated: a third SQLite file under `runs/`, and a schema this repository owns and
migrates. What is kept: `sqlite3 runs/feedback.sqlite "select …"` — the greppability 0014 lists
as the thing it lost.

One consequence is a feature, and it shipped. `api/raised_write.py` refused to file on a paused
thread, because `as_node="raise_note"` would consume the live `ask_user` interrupt. Nothing writes
graph state now, so there is no interrupt to consume and **the reader whose turn is paused — the
one most likely to want to complain — can file.** The 409 is gone, and the test that used to be
about it is now the test that asserts its absence.

### 3. A state is stored if and only if a named actor moves it

Everything else is derived at read time. This is the rule that resolves the lifecycle, and it was
found by building the state machine rather than by arguing about it: a throwaway prototype could
not write seven transitions without inventing an answer, and four of the seven were the same
mistake — a stored state nobody moves.

**Stored** (the steward moves them): `open → triaged → {declined, duplicate, addressed}, and `addressed → triaged` back when every patch for it is withdrawn`.
`decline_reason` is stored beside the state because **the reason is the notification** — there is
no "declined" badge without a sentence.

**`addressed` and not `resolved`, and the word was chosen against a measurement.** A landed patch
establishes that the corpus changed. It does not establish that the reader's question now answers
correctly: an asset edit does not mean retrieval finds it, and even on turns where every gold table
*was* licensed **and the gold names at least one table** the engine's measured accuracy is
0.7548 (n=1,150) — over all 1,277 covered turns it is 0.7126, because 127 of them have a gold
that reads no table and cannot be won. So roughly **one in four** complaints
marked resolved on the strength of a landed commit would still be wrong. There is one cheap upgrade
and exactly one: re-running the affected question's T3 coverage fixture costs ~$0 and licenses the
narrower claim `retrieval_verified` — *the tables needed to answer this are now reachable*. Nothing
in this design licenses `resolved`.

**Derived** (the corpus decides them), recomputed on every read from the loaded corpus, the
bundle's recorded hashes and the bundle's post-state text:

| derived state | condition | why a two-state model cannot express it |
|---|---|---|
| `handed_off` | the loaded corpus still hashes to the patch's base | — |
| `landed_verified` | the loaded corpus hashes to the patch's expected post-hash | — |
| `landed_matched` | the hash differs, but every asset the bundle touched is present and its `summary`/`body` match the bundle's post-state | **the common real case**: two bundles land in one week and exact-hash matching fails for a change that did ship |
| `retrieval_verified` | landed by either test above, **and** the observation's retrieval fixture passes again | the narrowest claim the free ladder licenses — the tables needed to answer are reachable — and the one `addressed` deliberately stops short of |
| `superseded` | the hash moved off the base and the content is not there | a `git apply` conflict, a CI reformat, or a reviewer editing before committing — all normal, and all silently mislabelled "handed off, forever" by a two-state model, which is the unclosable `open: true` this design replaces, reintroduced one level up |

**Five derived states shipped, not the four this table was drafted with.** `retrieval_verified` is
the upgrade named two paragraphs up, and it is a state rather than a flag because a reader asking
"did this land" and a reader asking "can the engine reach it now" are asking two things.
`feedback/lifecycle.py::derived_state` grants it only on a fixture that actually passed: an unrun
fixture is not a pass, which is the same rule `tools/verify_patch.py` applies to an unrun tier.

**`closed` is not a state.** Nothing branches on it. `open` is computed as `state not in
TERMINAL`, never stored, so the unclosable row this design replaces stops being expressible.

### 4. Nothing in this repository writes to the corpus, and the patch is applied by hand

The pipeline stages assets into `<proposal dir>/assets/<namespace>/<id>.yaml`, outside
`corpus_root`, for two reasons that are not taste. `corpus/hash.py::corpus_content_hash` digests
everything under the root, so one staged file voids an in-flight arm through
`measure/gates.py::_corpus_content_hash_gate`. And `corpus/store.py::load` walks the whole tree,
so a staged file with a valid `asset_type` would be **loaded and served** — a model-authored draft
in the analyst's prompt with no review, which is the v1 forgery defect verbatim.

**Because of M1, an edit is not a `write`.** Creates go through `corpus/store.py::write`. Edits go
through a new `corpus/patch.py` that locates the field by PyYAML composer node marks and replaces
the text surgically, so a one-word `summary` change is a one-line diff and comments survive.
`store.write` is a create primitive and this ADR names it as one.

Built, and narrower than drafted. `patch.py` exports `locate`, `read_field` and `apply_edit`, and
**no create function** — a create is still `store.write`, so this module never has to reason about
a file that does not exist. `apply_edit` returns the new file text and does not write it: the
caller is a bundle exporter that wants a diff, and a function that both computes and commits a
change cannot be used to preview one. Two rails came out of the build rather than the design. It
refuses any field outside `EDITABLE = {summary, body}`, and it refuses `governance`, `provenance`,
`audit` and `columns` outright, which puts §Decision 8's prohibition in the module instead of in a
prompt. And it re-parses the text it just produced and requires the field to read back as asked —
without that, a renderer bug lands a patch whose value is not the value it was given, which is a
defect this class produces in every form and which nothing else was looking for.

The handoff is a **bundle**: a directory carrying `MANIFEST.yaml`, `COMMIT_MSG.txt`,
`changes.patch`, `after/`, and `evidence/`, produced by a local CLI. Applying it is `git apply`
plus a commit in a repository this process cannot write to. That is the provenance gate.

**It is `git apply` and never a directory copy, and that is a correctness requirement rather than
a preference.** The design session's first draft applied a bundle with `cp -r assets/. $CORPUS_DIR/`.
The served corpus keeps a table's columns *inline*, one file per table, and
`corpus/store.py::_split_inline_columns` splits them into their own assets at load. So a staged
standalone `column` file copied into that tree gives the loader the same asset id twice — which
`store.load` returns with **zero problems** (M1) and which then raises `ValueError: duplicate index
id` inside `build_index`, killing every `Session` build. That is a total serve outage arriving
*after* the commit, past a conformance checker that cannot see it. Two structural answers, both
taken: the loop **may not create a `column` asset at all** — columns are authored inline under
their table and their ids are derived (`corpus/identity.py::derive_column_id`) — and uniqueness of
asset ids becomes a conformance rule that runs in the corpus repository's CI, before the merge.

> **Amended 2026-08-24: "in the corpus repository's CI, before the merge" is wrong on both halves.**
> [ADR 0016](0016-gating-the-corpus-repository.md) puts the gate in *this* repository's nightly, for
> the reason in *What this ADR does not cover*, so the rule runs here and it runs after the corpus
> commit lands. The rule itself is unchanged and still executes; what it cannot do is block a merge.

### 5. The pipeline is a local process, not a served graph, and each role's boundary is its tool list

**None of this section is built.** There is no `triage/` package, no Reproducer, Diagnoser, Author
or Curator, and `tools/check_imports.py::LAYERS` names `feedback` and does not name `triage`. Steps
0-6 ship the store, the ladder and the steward's surface; a human does the diagnosing and the
authoring, and the reasoning below is what a build of the pipeline would have to answer to. Read
this section as design.

> **Amended 2026-08-24: `Source.agent` is deleted, and this section is the reason.** The vocabulary
> shipped with a fourth population, `agent`, for the roles below to file and author as. Nothing ever
> wrote it — the four construction sites in `src/` and `tools/` write `reader`, `operator`,
> `operator` and `eval` — and its only occurrence anywhere was
> `feedback/validate.py::_may_file_operator_only`, which read `obs.source is Source.agent and
> obs.category is Category.column_suspect` and whose docstring called it "the one agent-writable
> exception ADR 0005 declares". A policy exception that cannot evaluate true is not a narrower gate;
> it is a paragraph a reader believes. That is the failure ADR 0005's own retro on
> `restamp_model_authored` names in one sentence — *an uncalled control is not one either* — and the
> rule this record already applied to itself in Consequences 5, refusing to declare
> `rendered_asset_ids` against this same unbuilt pipeline: **it lands with its consumer and not
> before.** The design is unchanged and stays here: the Author and the Curator are the producers, and
> the member comes back in the commit that builds them, which is also where the widened
> `column_suspect` permission gets decided again rather than inherited from a gate widened for
> nobody. `tests/feedback/test_every_source_has_a_producer.py` holds it, because
> `tools/check_declared_is_consumed.py`'s K1 counts any occurrence of a name as evidence and
> therefore read that dead branch as a producer.

`triage` is a `StateGraph` invoked by `python -m governed_bi.triage`. It is **not** an entry in
`langgraph.json` and it is **not** a subgraph of `serve` — `ServeInput` is the A2/A3 trust
boundary, and a triage run spans many threads so there is no thread it could nest in either.

**The design session proposed registering it, and the red team was right to reverse that.**
`api/auth.py::_no_state_writes_on_a_new_run` inspects only `command`, denying `command.update` and
`command.goto`; a run-create payload of `{"assistant_id": "triage", "input": …}` carries no
`command`, so `_command_of` returns `None` and the hook returns without objecting. Registering the
graph would therefore hand anything that can open a socket to the port a run that spends five
serve turns, touches the warehouse, and writes a checkpoint nothing sweeps.

State the delta honestly rather than as a new hole: the platform's own `/threads` and `/runs`
already let an anonymous caller spend model budget on `serve`, and `api/routes.py` says so in as
many words. What registering `triage` changes is the **ceiling per request** — from one turn
(~45k tokens) to a fan-out whose only bound is an operator-set cap (~290k at the default) — and it
does it on the one graph that also writes files. A local entry point costs nothing, and it is what
the owner's own brief already asked for: the patch-producing step is a CLI, not a route.

**One consequence, and it is a simplification.** Without the deployment's checkpointer there is no
`interrupt()`, so the pipeline has no human-in-the-loop pause. When the Diagnoser cannot settle a
semantic question the run **ends**, writing a `needs_sme` observation into the store; a steward
answers it in the review surface, which drafts the patch. That deletes `authorise_resume` from this
design along with the problem it could not solve — under one principal the gate compares the batch
*launcher* against the resumer, not the reader who complained, so it distinguishes nobody. A pause
that nobody can be identified as answering is worth less than a queue row that says who is waiting
on what.

`deepagents` is **not** used, and `pyproject.toml` already says why: `FilesystemMiddleware`
contributes a non-removable `write_file`, "which is exactly the generic write channel that let v1
forge `source=human, status=certified` on curated assets". A pipeline whose entire subject is
which writes a model may make cannot be built on a harness that mandates a generic write tool. So
`StateGraph` + `langchain.agents.create_agent` nodes, which is what `serve/nodes/agent_core.py`
already is, with a **different tool list per role** — the only boundary this repository trusts,
per `corpus/schema.py::Governance`: "exclusion is human-only, **enforced by the absence of a
tool**."

Four roles, and the separation is not stylistic:

| role | model | sees the database | sees the corpus | may write |
|---|---|---|---|---|
| **Reproducer** | none — it *invokes the serve graph* | through the governed path only | the turn's own | nothing |
| **Diagnoser** | main | yes, governed | the cluster's schemas — **wider** than the failing turn's licence | nothing |
| **Author** | main | **no** | the cluster's schemas | one staging tool |
| **Curator** | none — deterministic file operations | no | — | the triage-run record |

**The design session proposed a fifth — an Adversary that replays held-out questions against a
trial corpus to see whether the patch broke something — and it is cut.** The argument that killed
it is its own load-bearing tool: `replay_question` reads a signal *below the engine's noise floor*.
Two runs with the configuration held fixed disagree on 12.7% of outcomes (`open-work.md` §3.12), so
a handful of replays cannot distinguish a regression the patch caused from a coin this engine flips
anyway. And its `withdraw` vote is a model judging its own engine's output, which is the reflector
at OOF AUC 0.597. What replaces it is cheaper and deterministic: T1 conformance over the whole tree
at ~3–26 s and $0, plus a human reading a diff.

This is not a claim that adversarial review is worthless. It is a claim that **this** adversary's
instrument was noise-limited, and that a control whose measurement cannot resolve what it is
looking for is the kind of thing this repository has twice shipped and then had to withdraw. If it
comes back it comes back with a null: the same nightly re-run that gives the mechanism readout its
noise floor (§Open questions 4) is what would tell anyone whether a replay panel can see anything.

The Reproducer has no agent because a re-implementation would reproduce a different engine, which
is the one thing a reproducer must not do. The Diagnoser's widening is the point: the commonest
defect is that the right table was never licensed, and an agent confined to the failing turn's
licence cannot see the table that should have been there. The Author is blind to the database so
that the reviewer reads an argument whose premises are in the file — this is the weakest of the
five decisions and §Open questions says so.

**"Certified" keeps meaning what it means.** A staged asset's `provenance.status` is `proposed`,
written by code, always. ADR 0003's retro sentence — "'Certified' still means a human signed off,
not that an independent model tried to break it" — stays true, and now trivially so: no model in
this design tries to break anything. Had the Adversary shipped, its verdict would still have been
barred from `Provenance`, because it is a model reading text another model wrote — an injected body
can address it — and it has no measured bypass rate the way `govern/adversarial.toml` has 0/62 for
the SQL gate. An unmeasured instrument may not mint the one status a human owns.

### 6. A patch is never verified on EX

Six tiers. **T0–T3 spend nothing on the agent surface and have no noise floor**; T4 and T5 are the
only tiers that spend. Full definitions and pass conditions are in the working reference.

| tier | what it is | cost | detects |
|---|---|---|---|
| T0 | the patch file alone: parse, identity, validators the loader itself uses | ~1.6 s | a file the engine cannot load |
| T1 | the whole tree: conformance, `build_structure`, `build_index`, the adversarial suite | ~4–30 s | duplicate ids (M2), prose rules, a corpus that cannot start |
| T2 | the tree binds against what the corpus itself declares | ~seconds, offline, $0 | a metric naming a column that does not exist (M2) |
| T3 | **paired retrieval, one process, agent model off** | ~minutes, ~$0 (M4) | coverage: did the gold table become licensed, per question |
| T4 | targeted paid replay of the affected questions | tens of calls | did the answer flip |
| T5 | a paired arm | ~52 min, ~74M input tokens | a release, never a patch |

**Four of the six are built.** T0–T2 are `tools/verify_patch.py` and T3 is
`tools/reproduce_observation.py`. **T4 and T5 are not built**, so the two tiers that spend money are
also the two that do not exist, and the ladder a patch actually carries stops at T3. An unrun tier
is **absent** from that ladder rather than recorded as skipped-therefore-fine, because a tier that
could not run must never read as one that passed. T2 came out cheaper than this table first said:
the corpus declares its own tables, columns and joins, so the resolver is offline and free and
needs no database.

**T3 is the centrepiece**, and the gate is **per-question, not per-rate**: any question that lost
gold-table coverage fails. That resolves a single question at ~$0 and with zero variance, where
the same statistic read off two paid arms resolves 1.94pp. It is also aimed at the right bucket —
73 of the v4 arm's 438 failures are coverage misses, the largest winnable one after the 257
semantic errors, and the semantic errors are invisible below T5. **A patch aimed at coverage is
cheap to verify; one aimed at semantics is not, and the loop must say which it is aiming at before
it is verified.**

**The readout is a stratified EX, and the mechanism count's job is to define the stratum.** The
design session proposed the opposite — retire EX and read a mechanism indicator instead, on the
grounds that a rarer event has less discordance and therefore a smaller MDE. **That argument is a
unit error and is withdrawn.** MDE is denominated in points of the whole population, while the two
readouts' base rates differ by two orders of magnitude: `BINDING/r_star_projection`'s *maximum
possible* effect is 2.15pp, so against its own 1.12pp MDE it has **1.92 resolvable steps**, where
EX has 28.5. A finer scale on a ruler two graduations long is not a better instrument.
`COLUMNS/r_column_not_allowed` is worse still at 1.16× — saturated, and the session's table
labelled it decisive.

What the session had and mis-scored is the readout that actually answers the question a reader
asked. Restricted to the 30 turns where either arm hit that mechanism, **EX moves +23.33pp on 9
discordant pairs, exact McNemar p = 0.0391** — significant. It was called "not decisive" because
23.33pp is under that stratum's post-hoc MDE of 28.02pp, and a post-hoc MDE is not a significance
threshold; `measure/stats.py::mde`'s own docstring says so. So: **a mechanism count selects the
turns a patch could have touched, and EX on that stratum is the verdict.** One instrument for
choosing the population, a different one for the answer.

**And T3 is blind to half the corpus, by construction.** It runs with the agent model off, so what
it exercises is retrieval — and retrieval indexes `summary`. `body` reaches the *prompt*
(M3). So a patch that changes only a `body` scores a clean pass on every T3 condition while
changing exactly the text a model reads. This is not a defect to be fixed in T3; it is what a
retrieval-only gate can do. The consequence has to be carried in the ladder rather than
discovered: **a `body`-only patch has no free verifier and goes to T4**, and the field a patch
touches decides its cheapest honest tier. Recorded on the patch, so a `body` edit cannot be waved
through on a green T3.

**Every gate is a delta gate.** The served corpus already produces 361 `build_structure`
problems, so a "zero problems" gate rejects production. A gate that fires on the pre-existing
population is a gate that gets waived, and a waiver is how a real finding goes green.

### 7. A corpus release is a declared treatment, and the knob for it did not exist

**Measured: `comparability_keys()` was 50 names and not one contained "corpus".** So an arm whose
treatment *is* the corpus cannot declare it, and `register/arm_profiles.py` makes every such arm
`cannot_evaluate`. Separately, `corpus_content_hash('../BIRD-corpus')` at HEAD is
`6e5c7b4be83d5682…` while `arms.toml` declares `86ed1dbf…` on all four arms — the two commits
between add only `LICENSE` and `README.md`, no asset changed, and the digest moved anyway. **So
`--arm v4` against the checked-out tip is refused today.**

Therefore: one new comparability knob, `corpus_release`, naming a **tag** and not a directory;
patches land continuously in the corpus repo and arms pin releases, so the control never moves
under a measurement. `require_power` gets its missing caller by way of a `hypothesised_effect` and
a `readout` on `ArmProfile`, at which point an arm that cannot detect its own hypothesis fails
before it spends anything.

Built, all three: `register/knobs.py` declares `corpus_release` as a comparability knob, so
`comparability_keys()` is 51 names and one of them contains "corpus";
`register/arm_profiles.py::recorded_corpus_release` reads it back off a row and refuses a profile
that claims a different one; and `ArmProfile.hypothesised_effect` / `.readout` exist with
`eval/provenance.py` as `require_power`'s caller, which is the caller `open-work.md` §3.10 recorded
as missing. `readout` is required alongside `hypothesised_effect` for the reason §Decision 6 gives:
MDE is denominated in points of the whole population, and a declaration that omits which quantity
it is denominated in is the unit error this ADR withdrew. There is no `CorpusRelease` type — the
knob names a tag, and a tag in a table is not a class.

**What bounds the release cadence is not money — it is the stock of detectable effect, and the
stock is nearly empty.** Everything T3 can see is the coverage debt: 79 questions whose gold tables
were never licensed, worth at most +5.85pp, which at the measured EX scales to +3.98pp. Against an
EX MDE of 2.33pp that is **1.7 detectable releases in the entire debt** — and each release needs
two new arms, because no pair on disk reaches `knobs_comparable`, so the first one has to buy its
own control (~150M input tokens, ~104 min). A release programme that spends that to measure a
quantity with under two graduations left in it is a programme whose result is "we learned nothing",
written expensively.

So the release headline is the **T3 per-question coverage delta**, at a resolution of one question
(0.08pp) and a cost of ~$0, and a paired arm is what you buy when a *code* change needs pricing —
not what a corpus release routinely pays for. The 75M tokens a release arm would have cost are
better spent producing the null that the whole readout is currently missing (§Open questions 4).

### 8. What the loop may not author

Enforced by the absence of a tool, and by code that overwrites rather than by a prompt that asks.

| field | rule | why |
|---|---|---|
| `governance.excluded` | the loop emits a **request** in prose; a human transcribes it by hand | "human-only, enforced by the absence of a tool" (`corpus/schema.py::Governance`). Nothing in the pipeline may set it, and the review surface may not render it — a screen that can propose an exclusion *is* the tool whose absence is the control |
| `provenance` | stripped and re-stamped in code to `source: curator, status: proposed` | this is v1's forgery — a generic write channel minted `source=human, status=certified` |
| `confidence` | never written from a reproduction rate | `corpus/validate.py:132` already warns in prose: "a curation-time belief and never an outcome score — the first thing a feedback loop will want is to write a hit rate here." The rate goes on the triage run |
| `reliability.status` | **allowed** | ADR 0005 declares it AI-authorable: "`suspect` argues against a column and the analyst still sees it" |

**What shipped is narrower than this table, and only one row of it is live.**
`corpus/patch.py::EDITABLE` is `{summary, body}` and nothing else, and `governance`, `provenance`,
`audit` and `columns` are refused as field paths outright — so the first two rows are enforced in
the module rather than by a prompt, and the last two have no route to exercise: `confidence` and
`reliability.status` are not editable by the shipped ladder at all, which makes
`reliability.status` allowed in the design and unreachable in the build. The table is what a
pipeline able to author a whole asset must obey.

### 9. `body` and `summary` are different channels and need different rules

From M3. `body` reaches the model's prompt; `summary` reaches the retrieval index. A rule that
polices "prose" without saying which field is a rule that misses one of the two.

New conformance rules, whole-tree, added to `tools/check_corpus_conformance.py`'s `RULES`:
a metric `expression` must parse and resolve on its `base_table` (M2's 28 + 23 findings); no
model-visible `body` may name a `governance.excluded` column or asset (M3, currently zero live
population, so it is free to add and cannot regress anything); model-visible text is checked
against `govern/guard.py::GUARD_RULES` rather than a second copy of them; `certified` requires a
human source; and asset ids are unique across the tree (M2's silent duplicate).

**A correction this ADR makes to its own design session.** V10 and V12 were proposed as the
existing content scanners to lean on. They are not disclosure rules: V10 is "no text discloses how
an unreliable column was made", which exists for the BIRD obfuscation decoys, and V12 is
held-out-question leakage. Both police **benchmark integrity**. On a production corpus they police
nothing. So the ADR 0003 hole has no rule at all today, and the new one is not a reinforcement of
an existing control — it is the first one.

### 10. The analyst gets to re-ask, and this is what makes it a loop

**Not built, and not planned in this cut.** There is no `/reports` page and no re-ask action. The
reason is the one that also cut the capture UI: one principal holds every role on this deployment,
so a per-reader report list and a notification have nobody to serve, and the input to the loop is
the eval artifact through `tools/import_eval_failures.py` rather than a person clicking. That makes
what shipped a queue and not a loop, by this section's own definition, and the day there is a
second audience this is the half that has to be built. The design follows.

Every part of this design promises it in prose — the `landed_verified` copy literally says "ask
your question again" — and the design session shipped no way to do it. So: the reports page carries
a **re-ask** action on any observation whose derived state is `landed_verified` or
`landed_matched`. It opens the chat surface on a **new** thread, prefilled with the question text
the store already copied off the turn record.

A new thread and not the original, for the reason `api/raised_write.py` documented about
writing into someone else's thread, and because a second turn on the old thread inherits 25 turns
of context that the comparison should not include.

It costs about half a day and it is the only thing in the design that lets the person who filed the
complaint find out whether the fix worked. Without it this is a queue, not a loop. **What it must
not do is grade itself:** the engine does not compare the new answer to the old one and does not
mark anything resolved on the strength of it. 12.7% of questions flip between two identical runs,
so one re-ask is not evidence — it is the reader looking, which is the only judgement available and
the one that was asked for in the first place.

### 11. The receipt lives in the content

A landed asset carries `obs:<observation_id>` in `Provenance.source_refs`. The engine learns a
change landed by **reading the corpus it already loads** — no webhook, no callback, no second
source of truth. An observation cannot be marked `addressed` by anything other than the change
actually being there.

**What `source_refs` is not: a proof.** It is unvalidated free text in a human-editable file. A
typo makes an observation invisible; a copied block attributes a change to a complaint it did not
come from; and an id the store has never heard of is a dangling reference the engine must report
rather than ignore. So the reconciler is a *reporter*: it prints matched, unmatched, and dangling,
and it never invents a state from a string it cannot corroborate. The derived-state check in
§Decision 3 is the corroboration — the asset has to be present *and* its text has to match the
bundle's post-state — and `source_refs` is what makes the join cheap, not what makes it true.

Not `Audit.extra`: that is the unknown-key hatch, deliberately the one place an unknown key is
kept rather than rejected, and putting a join key there makes it unfindable. Not a new
`ProvenanceSource` member: `source` says who authored the asset, not what prompted it.

Built as `tools/check_landed.py`, and it is a reporter: matched, unmatched, dangling, and no state
written anywhere.

---

## Rejected alternatives

**The engine opens a pull request.** Rejected. It needs a credential this repository has decided
not to hold, it makes this repo the corpus author (§Context 3), and the one control the whole
design rests on is that a human's commit is the write. A bundle plus `git apply` is the same
mechanical cost to the engineer and leaves the authority where it is.

**Keep the channel and close a row by appending a closure with the same `report_id`, folded
last-write-wins.** Rejected on §Decision 2's four counts. It is a workaround for a store that
cannot hold a mutable row, and it grows a channel nothing sweeps by one row per closed complaint.

**Keep the channel and make closure purely a read-time join against the corpus.** Rejected as
*insufficient*, not wrong — the read-time join is adopted for the landing states (§Decision 3).
But an observation triaged to "the engine was right" never lands anything, so a join against the
corpus can never close it. Closure needs a stored state a steward moves *and* a derived state the
corpus determines, which is exactly what §Decision 3 says.

**Gate a patch on a paired arm.** Rejected on the arithmetic in §Context 6. The gate would refuse
nothing detectable and cost ~74M input tokens per attempt.

**Require a cluster of three before triaging.** Rejected. One wrong answer is weak evidence about
*belief*, not about whether to look, and on a fresh deployment there is no path from zero
complaints to three if nobody looks at the first. Confidence belongs on the record where a steward
prices it.

**A model assembles the bundle.** Rejected. Assembling a diff is not a judgement, and a model
assembler is how this repository becomes the corpus author by accident.

**Put the pipeline's prompts in `PROMPT_REGISTRY`.** Rejected, with a measurement.
`register/prompts.py::prompt_set_hash` digests the whole registry — names, variants and text —
and on this tree it is `b1f9e4d7d230cb97`, whose prefix is v4's `b1f9e4d7` in `open-work.md`
§3.13. A triage prompt in that registry moves the treatment identity of every serve arm every
time somebody rewords the Diagnoser, for zero change in serve behaviour. That is the `expand_hops`
defect reproduced on purpose. Two registries in one module, with an import-time assert that they
partition it — because a prompt in *neither* is a prompt no hash covers, which is strictly worse.

**Declare the pipeline's settings in `register/knobs.py`.** Rejected for the same reason:
`_resolved_knobs` puts every declared knob on every serve row and `_knobs_resolved_gate` compares
them, so a triage knob changes the config hash of every serve run while changing no serve
behaviour.

**An in-app approval gate before a patch becomes a bundle.** Rejected. `api/auth.py` returns one
principal, so an approval distinguishes nobody: whoever reaches the port approves. An approval
pause whose approver cannot be identified is a UI affordance labelled as a control. The steward
surface is a queue and a diff; its accept action moves a row and produces a bundle, and it
authorises nothing. The UI copy must say so.

---

## Consequences

1. **Two new packages and a `LAYERS` decision.** `tools/check_imports.py::LAYERS` must name every
   package under `src/governed_bi`, so adding one forces a placement. `feedback` sits immediately
   after `corpus` — it validates a patch with the same validator the loader uses, and must not
   import `serve`, `govern`, `api` or `eval`. **One package, not two:** `feedback` is placed there
   and `triage` is not in the list at all, because the pipeline is not built. `LAYERS` naming every
   package is what will force that decision on the day it is.
2. **`api/visibility.py::visible()` does not cover the new surface, and cannot.** It narrows a
   *corpus projection*; an observation's free text is a human sentence and there is nothing in it
   to narrow. So the new routes need a second narrowing function beside `visible()`, and the
   free-text exemption must be declared and asserted rather than discovered — the same trade
   `/audit/corpus`'s `problems` takes under ADR 0012 §8.5. Built as
   `api/feedback_routes.py::_narrowed`, and it is an **allowlist** rather than a denylist: a field
   the route does not name does not reach a client, so adding a column to the store cannot disclose
   it by default. The fields it holds back are the benchmark's — `gold_sql`, `gold_fingerprint`,
   `pred_fingerprint` — which is a disclosure this ADR did not anticipate having to make.
3. **The engineer-facing verbs are new authority, and they ship unmounted.** Behind
   `GOVERNED_BI_FEEDBACK_ADMIN`, returning 404 rather than 403 — a 403 confirms the route exists.
   The cheapest control a fork can turn on is a token on those verbs only, and unlike 2026-08-13's
   reversal it costs nothing: LangGraph Studio never calls them. Shipped as written, and with one
   principal the environment switch is the whole of the control — which is worth saying plainly
   rather than dressing a switch up as a gate that distinguishes somebody.
4. **On the one verb that ships enabled, this design narrows by arithmetic.** One row once,
   quota'd, sweepable — against the row it replaced, which was re-serialised into every later
   checkpoint of a thread in a store nothing sweeps. The filing route returns **201** rather than
   200 for the same reason: it creates a row in a store, and a client should not have to read the
   body to learn which.
5. **One new register field, `rendered_asset_ids` at `Stage.assemble`.** The set of assets that
   were actually in the block the model read is not recoverable from a turn record —
   `context_hash` digests it and is not invertible, and `evicted` names only what the budget
   dropped. Without it the steward's evidence panel has to *derive* that column and say so in the
   caption. It lands **with** its consumer and not before, because a field with no reader is the
   defect `tests/conformance/test_the_declared_but_unconsumed_set_does_not_grow.py` fails the build
   over. **Not built**, and the rule is the reason: the consumer is the pipeline's reproducer, the
   pipeline is not built, so declaring the field now would be exactly the unconsumed declaration
   that gate exists to refuse. The evidence panel derives the column and its caption says so.
6. **`corpus/snapshot.py` gets its first caller** — the trial corpus T4 replays against. With the
   Adversary cut, that caller is a deterministic driver replaying a fixed question set rather than a
   model choosing what to replay, which is strictly better: it is auditable. §Open questions 2
   carries the safety defect in `snapshot` that has to be fixed before the caller exists.
   **This did not happen, and the reason is a better answer than the one designed.** T4 is not
   built, and the free ladder applies the edit **in memory** rather than against a copied tree, so
   it never calls `snapshot` at all — no scratch directory, no `rmtree`, nothing to point at the
   wrong path. `snapshot` still has no caller outside its own tests. The defect in it was fixed
   anyway (`222d1bf`), because a data-loss bug is not made safe by having no callers yet.
7. **A reader's free text would reach two model calls before any human sees it** — a consequence of
   the pipeline, and therefore not yet a live one. In what shipped the free text reaches **no** model
   call: `tools/reproduce_observation.py` re-runs the observation's *question* and never its note,
   and the first reader of the note is the steward in `/review`. The paragraph below is the residual
   risk a build of the pipeline inherits, and it is unchanged. The design session's claim that
   "the staging tree is outside `corpus_root`" is the structural control is **not complete**. The
   text is read by the Diagnoser and echoed to the Author through the diagnosis; and — this is the
   part that contradicts the claim — staged prose derived from it is rendered into a *real* prompt
   by T4's trial replay. What actually bounds this is weaker and must
   be stated as such: the free text is delimited and framed as data in every prompt that carries it;
   the trial corpus is a copy no serve request can reach and is off unless a scratch directory is
   configured; staged output is checked against `govern/guard.py::GUARD_RULES`; and the loop cannot
   write `governance` or `provenance`. There is no content-scanning gate on prose meaning, and the
   residual risk is a poisoned asset a human approves. (Cutting the Adversary removed two of the
   four read points, which is a security argument for the cut that was not the reason for it.)
8. **The `raised` channel's rows were to be migrated rather than orphaned, and there were none.**
   The decision was §Decision 2's: `tools/drain_raised.py` plus a reader union with a named end
   condition, bought at the cost of one extra build step, because the alternative — delete and
   declare the rows unreachable — is a sentence somebody would have had to sign. Then the channel
   was counted before it was deleted: **zero rows across the checkpoint store, the harness store and
   all 23 platform thread rows.** So nobody had to sign anything and nothing was written. The drain
   tool does not exist, the union does not exist, and `4a0d11a` removed the channel in one commit
   with an assertion that the writer seam is gone in place of a migration. The consequence this
   entry describes is therefore **retired, not delivered** — recorded rather than deleted because
   the reasoning is what a reader needs next time a channel has to go, and the lesson is that the
   count is cheaper than the plan for what the count might have been.

---

## Acceptance criteria

Falsifiable, and each names the mutation it must survive. Four of the nine are asserted by tests on
this branch; four wait on the pipeline; one is retired. Each says which.

1. **The serve treatment identity does not move.** After the prompt registry is split,
   `prompt_set_hash()` on the default variants is byte-identical to `b1f9e4d7d230cb97` — measured on
   this tree, 2026-08-23. Mutation: edit any triage prompt's text; `prompt_set_hash()` unchanged,
   `triage_prompt_set_hash()` moved. **Waiting on the pipeline, and half-satisfied for a duller
   reason:** steps 0-6 added no prompt at all, so there was nothing to partition and
   `prompt_set_hash()` re-measures at `b1f9e4d7d230cb97` today. There is no
   `triage_prompt_set_hash()`, so the mutation half is unasserted.
2. **A full triage run leaves the corpus untouched.** `corpus_content_hash(corpus_root)` before
   equals after, **and** the set of asset ids `store.load` returns is unchanged. Mutation: point
   the staging directory inside `corpus_root`; the test fails. **Waiting on the pipeline.** The
   weaker claim that shipped is the free ladder's: it applies the edit in memory and writes no file
   anywhere, asserted in `tests/conformance/test_the_ladder_checks_the_edit_and_not_the_file.py`.
3. **The Author cannot forge governance or provenance.** A scripted model emitting
   `governance: {excluded: true, by: "human"}` and `provenance: {source: human, status:
   certified}` produces staged YAML with no `governance` key, `provenance.status == proposed`, and
   one exclusion request on the record. **Waiting on the pipeline**, and there is no Author to test.
   What ships in its place is structural: `corpus/patch.py` refuses `governance`, `provenance`,
   `audit` and `columns` as field paths, so the forgery has no route even from a caller that asks
   for it.
4. **Every stored state names its actor.** A walk of the transition table fails on a stored state
   whose `moved_by` is empty. This is the rule of §Decision 3 made mechanical. **Met**, in
   `tests/feedback/test_every_stored_state_names_its_actor.py`.
5. **Conformance catches all four of M2's breakages.** The three it misses today plus the one it
   catches, each as a synthetic fixture in
   `tests/conformance/test_corpus_conformance_rules_fire.py`. **Met**, across that file and
   `tests/conformance/test_the_whole_tree_rules_fire.py` — the whole-tree half went into its own
   file because a rule that needs a second asset to fire cannot be expressed as one fixture.
6. **The new rules do not go green by waiver.** Pre-existing findings are pinned **by name** in
   the corpus repository, the set may shrink freely and may not grow, and closing one fails the
   build as loudly as adding one — because a shrinking list nobody updates is how a stale count
   survives. **Met**, as `tools/check_ratchet.py` and
   `tests/conformance/test_the_ratchet_only_turns_one_way.py`. The identity is the rule plus the
   file and asset, not the message, so rewording a finding does not silently re-pin it.
7. **A closed observation leaves the queue, and a superseded patch does not read as handed off.**
   **Met**, in `tests/feedback/test_the_landing_states_are_derived_and_not_stored.py`.
8. **The revision loop is bounded.** A scripted model whose staged asset never passes `validate`
   ends at `max_revisions` with the patch withdrawn, having called the Author exactly that many
   times. **Waiting on the pipeline.** There is no revision loop and no `max_revisions`.
9. **The drain has an end condition and the union has an exit.** **Retired**, and §Consequences 8
   records why: the channel held zero rows, so `tools/drain_raised.py` and the reader union were
   never built and there is no end condition to assert. The criterion is kept rather than deleted
   because it names the risk the staged deletion was bought to cover, and the reason it stopped
   being a risk was a measurement and not an argument.

---

## Open questions

1. **Can a model localise a defect to an asset at all?** Everything downstream of the Diagnoser
   is conditioned on it, and the nearest measurement is discouraging: the reflector scores OOF AUC
   **0.597** at the easier task of judging whether an executed statement answered the question —
   worse than counting the agent's output tokens — and its `unsure` bucket was as likely to be
   right as its `correct` bucket (`open-work.md` §3.11). If a model cannot tell *whether* a turn
   was wrong, the prior that it can name *which sentence made it wrong* is bad. Mitigation, not
   answer: the vocabulary lets the Diagnoser conclude "no asset", and the first shippable mode is
   diagnosis-only, which writes no YAML. **If the Diagnoser is at reflector quality, the honest
   product is a triage queue with no authoring**, and the authoring half of this ADR is wasted.
2. **`corpus/snapshot.py::snapshot` deletes its destination with no guard.** `_refuse_nesting`
   prevents nesting; `_identify_corpus` guards `restore`, not `snapshot`. Measured: pointed at a
   scratch directory holding unrelated files, `shutil.rmtree` removed them. So a snapshot path
   must never be derived from a caller-influenced id, and the guard belongs in `snapshot` too.
   This is a defect in existing code that this ADR's first caller would weaponise.
   **Closed (`222d1bf`).** Both functions apply the same identification now, and `snapshot` accepts
   one further case `restore` has no reason to — an **empty** directory, which holds nothing to
   lose. The caller that would have weaponised it was never built (§Consequences 6), which is why
   the fix mattered: a data-loss bug in code with no callers is a bug waiting for its first one.
3. **Should the excluded-column-in-`body` rule block or report?** Blocking needs a calibrated
   false-positive rate and nobody has one; a report puts the finding in front of the human already
   reading the diff. But M3 says the current posture rests entirely on execution-time refusal —
   the name reaches the prompt and the query naming it is refused — and an enterprise fork's PII
   story cannot rest on that alone. Currently zero live population, so the decision is cheap
   today and expensive the first time somebody excludes a column.
4. **The mechanism readout has no measured null, and one nightly run would give it one.**
   `run1`/`run2` — the designated replicate — carry **zero ledger rows**, so nothing on disk says
   how much a mechanism indicator moves between two identical runs, and every MDE quoted for one is
   computed from that pair's own observed discordance. Post-hoc by construction, which
   `measure/stats.py::mde`'s docstring insists on. Re-running `run1`'s configuration under the
   current harness produces the null, makes the stratum selection pre-registrable, and is the
   cheapest high-value experiment anywhere in this design. Until it exists, the stratum is chosen
   after seeing the arm — which is the defect `measure/signals.py`'s docstring is a whole paragraph
   about.

   **One number is barred from being quoted at all: `BINDING/r_star_projection`'s MDE of 1.12pp.**
   It carries three independent defects at once — post-hoc from the pair's own discordance, no null
   to compare against, and only 1.92 resolvable steps before its indicator saturates. It reads like
   instrument precision and it is a two-graduation ruler. The design session's mechanism table also
   computed its indicators under the `False`-on-empty convention that the same design forbids
   (12 of 1,351 pairs have an empty `attempts` on one side); under the specified `None` convention
   `mcnemar` correctly returns unmeasured, and restricted to the 1,339 two-sided pairs the effect is
   −1.94pp with the p-value unchanged. **The defect was in where the number came from, not in the
   number** — which is exactly why the convention has to be in the code and not in a habit.
5. **`tools/check_declared_is_consumed.py` cannot see four of the namespaces this design declares
   into.** Its four rules cover knobs, record fields and state channels. `corpus_release` is a knob
   and is therefore caught by name; `ArmProfile.hypothesised_effect` and `.readout`, the mechanism
   register's entries, the store's SQLite columns and `Attribution`'s fields are **not** — so the
   claim that "a sixth finding fails the build" is true for one of this design's declarations and
   false for the rest. Closing it is one more rule of the same shape, and until it exists the new
   declarations are held by review rather than by CI.
6. **Does the analyst's question actually answer correctly once a patch lands?** No, and nothing
   here establishes it. §Decision 3 is why the state is called `addressed`, the $0 upgrade is called
   `retrieval_verified`, and no state is called `resolved`. The user-facing string is an invitation
   to re-ask (§Decision 10) and never a claim. What remains open is whether anyone will accept that
   distinction in practice, or quietly read `addressed` as `fixed`.
7. **Do complaints cluster at all?** Zero exist on this tree. The clustering key is a guess, and
   the first month's distribution is the experiment. If complaints are mostly singletons on
   distinct tables, the batch pipeline is a per-event pipeline wearing a batch pipeline's name.
   **Answered, negatively, on the real 73.** The largest cluster is **3** and **49%** of rows are in
   a cluster at all. The batching argument does not survive that, so `/review` shipped as a list
   with an optional grouping rather than the cluster-first screen the design specified, and a build
   of the pipeline would be a per-event pipeline. This is the one open question in this ADR that a
   measurement rather than a build closed, and it closed against the design.

---

## What this ADR does not cover

- **Authentication.** Audit A1 and A7 are open and this ADR does not close them. It adds one
  narrowing seam and one unmounted-by-default admin surface, and it says plainly that reaching the
  port is still sufficient.
- **Who owns the corpus repository's CI.** The design specifies what that CI must run. It does not
  say who writes it, and nobody has. The served corpus is a git repository since `222d1bf`
  (§Context 5) and it has no CI at all. The pins live in it — `.conformance/pins.txt`, in a directory `corpus_content_hash` excludes so a lint's state does not enter the treatment identity — and
  `tools/check_ratchet.py` reads them from this repository, which is the wrong side of the merge
  and is named as such rather than counted as the control §Decision 4 asked for.

  > **Amended 2026-08-24: the answer is "nobody, by design", and the two sentences above are
  > stale.** [ADR 0016](0016-gating-the-corpus-repository.md) settles this. The corpus still has no
  > CI and now will not get any: a conformance rule is a statement about what *this* engine requires
  > of an asset — V16 imports `governed_bi.serve.context` — so a workflow in the corpus repository
  > would be data asserting a fact about an engine it cannot see. The gate is a nightly job in this
  > repository that checks the corpus out, against a corpus revision recorded here in
  > `tools/corpus_baseline.py`. Two consequences for this record. **The wrong-side-of-the-merge
  > problem is gone as stated and back in a different form:** the pins moved to
  > `.conformance/bird-corpus-pins.txt` *here*, which is the side that can read them, but the
  > nightly runs the delta tool and not the ratchet, so nothing automated reads them yet and §Decision
  > 4's control is still not the control. **And it is not a merge gate at all.** A corpus commit that
  > adds a finding lands, and is caught up to a day later. §Decision 4's sentence that asset-id
  > uniqueness "becomes a conformance rule that runs in the corpus repository's CI, before the
  > merge" is wrong on both halves: the rule runs here, and after. Nothing about the design of the
  > return path changes; where the check runs does.
- **Row-level security, tenancy, or a user store.** `docs/enterprise-fork.md` is unchanged by this
  ADR. In particular the store records no identity for who filed an observation, because
  `api/auth.py` returns one principal and inventing a per-user notion here would be a boundary
  that is not one.
- **Making the engine the curator.** The pipeline authors *candidates*. The corpus remains
  human-owned, versioned outside this repository, and not rebuildable from it.
