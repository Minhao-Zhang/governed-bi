# The return path — working reference

> ## What shipped, and where it differs from this page (2026-08-23)
>
> Steps **0-6 are built and on `design/return-path`**. This page is the design as agreed; six
> things came out differently once measured, and a reader acting on the page rather than on this
> note would get each of them wrong. `docs/open-work.md` §3.10a-3.10c carries the evidence.
>
> 1. **`tools/check_closed_domains.py` does not exist, and T2 needs no database.** §11 put the
>    metric-expression resolver behind a live catalog. It does not need one: the corpus declares its
>    own tables, columns and joins, and *those* are what an expression must be consistent with — the
>    warehouse is `govern/`'s business at serve time. T2 is conformance rule **V17b** over the
>    patched tree, offline and free.
> 2. **V18 is cut.** Five new rules, not six. It had no live population and no calibrated
>    false-positive rate, so it would have shipped as a rule nobody could size.
> 3. **The measured findings, which are what separate these rules from rules written on a hunch:**
>    V17a **107 across 94 of 478 metrics** (the design's 28 was a parse-only prototype — `DIVIDE(a,
>    b)` parses as SQL and names a function no dialect has, so the shipped rule also asks
>    `govern/functions.py::PERMITTED_FUNCTIONS`); V17b **17**; V19 **0**; V21 **1**, the file the
>    design named; V23 **0**. The ratchet pins **101** identities.
> 4. **Complaints cluster weakly**, which answers §12's open question 7 with a negative result: the
>    largest cluster is 3 and 49% of rows are in a cluster at all. The design's batching argument
>    does not survive it, and `/review` is a list with an optional grouping.
> 5. **The reproducer must be run with `--embed`.** §11's T3 is built as
>    `tools/reproduce_observation.py`, and driving it found that a lexical-only re-check reported 2
>    missing gold tables where the row recorded 1 — a false "still reproduces" that reads exactly
>    like a real finding.
> 6. **The capture UI and `/reports` are not built and are not planned in this cut.** One principal
>    holds every role on this deployment, so a notification loop and a per-reader report list have
>    nobody to serve. The input is the eval artifact: `tools/import_eval_failures.py`. §15's capture
>    surfaces (`raise-note.tsx`'s rewrite, `category-picker.tsx`, `my-reports.ts`,
>    `report-status.tsx`) are the design for a second audience that does not exist yet.
>
> Also not built, and named in §13 as later steps rather than as this cut: the agentic pipeline
> (`triage/`), T4, T5, and `CorpusRelease` as anything more than the `corpus_release` knob.

How reader and engineer feedback becomes a corpus change. The binding decision is
[ADR 0015](adr/0015-the-return-path.md); this page is what an engineer implements from.
中文版：[回流路径 —— 工作参考](return-path.zh.md)。

**Nothing on this page exists yet.** Every path, signature, route and test name is a design.
Where a sentence describes code that is already in the tree it says so and names the file.
Figures marked **measured** were taken on `governed-bi@464d1cb` against
`../MS Fabric Facilities/corpus` and `../BIRD-corpus@74ff80c4` on 2026-08-22/23; every other
number is an estimate and says so.

---

## 0. The loop, end to end

One walkthrough, so the rest of this page reads as a feature and not as a pile of parts. The
example is the commonest real case: an analyst gets a number that is wrong because a business term
means something else in this warehouse.

**Monday, 09:14 — the analyst.** Priya asks *"how many active customers did we add last month?"*
and gets 4,102. She knows it is about 400. She clicks **This answer is wrong** on the answer card,
and a five-row list expands in place. She taps *"A word in my question means something else here"*.
That files immediately — no submit button, two clicks, zero typing. The receipt appears where the
form was and offers two optional lines; she fills one: `expected: "about 400, not 4102"`. Nothing
else is asked of her, and the receipt says what will happen and what will not:

> Filed. A data steward reviews these oldest-first. This engine does not know who you are, so
> nobody will email you — check **My reports** to see what happened.

An `Observation` row lands in `runs/feedback.sqlite` with `category: term_mismatch`, `state: open`,
and a **copy** of the turn's question, SQL, licensed table set, outcome and treatment hashes (§4).

**Monday, 11:30 — the steward.** Dev opens `/review`. The queue is oldest-first and grouped
structurally: Priya's row sits in a cluster of three, all `term_mismatch`, all on
`facilities.occupancy`. The caption above it says the grouping never read the questions. He selects
the cluster and the detail pane shows seven evidence blocks *above* the decision bar (§15): what
was asked and what came back, what Priya said (her `expected` styled as the quotation it is), the
SQL and the attempt ledger in the same components she saw, what the turn was allowed to read with
the router's top-5 ranking, and which corpus assets were in context — with the caveat that the
"rendered" column is derived rather than recorded.

Block 5 is where he sees it: the `term` asset for *active customer* is in context, and its
`summary` says nothing about the `status` column. The engine had no way to know. He clicks
**Reproduce** — one model call, and the button says so — and it still returns 4,102.

He drafts a change: one field, `term_active_customer.summary`, adding the alias and the rule.
The diff renders field-by-field in the register's declared order with a live character count
against the cap, because finding out about a 251-character summary *after* the export is a wasted
round trip. He sets the three observations to `addressed`.

**Monday, 11:41 — the ladder.** T0 parses the staged file through the production loader. T1 runs
whole-tree conformance, `build_structure`, and `build_index` against a **snapshot** of the corpus —
not the corpus — and reports no new finding by rule id. T2 resolves the term's binding against the
live catalog. T3 replays retrieval, paired, with the agent model off: on the three affected
questions the gold tables stay covered and nothing else loses coverage. Total wall clock: about
half a minute. Total spend: **$0** (§11, M4).

Because the patch touches a `summary`, T3 is a real verifier here. Had it touched only a `body`,
the patch would carry a note saying T3 cannot see it and the honest tier is T4 — the field a patch
touches decides its cheapest honest tier, and the record says which.

**Monday, 11:45 — the engineer.** Dev runs one command:

```bash
uv run --frozen python tools/export_bundle.py --patch pat-… --out ./bundles
```

and gets a directory: the surgical `changes.patch` (a one-line diff, because
`corpus/patch.py` edits the field in place instead of re-dumping the file), a generated
`COMMIT_MSG.txt` carrying no reader prose, the post-state file in full, and `evidence/` with what
each reader said verbatim inside a fence. He applies it in the corpus repository:

```bash
cd ../BIRD-corpus && git checkout -b return/pat-… && git apply -p1 …/changes.patch
git commit -F …/COMMIT_MSG.txt
```

That commit goes through the corpus repo's own review and CI — conformance, the ratchet, and a
`build_index` that must start. **This is the only write to corpus content anywhere in the loop**,
and it is a human's. Nothing in this repository can make it.

**Tuesday — the loop closes itself, by reading.** The engine reloads the corpus. The landed asset
carries `obs:<observation_id>` in `Provenance.source_refs`, so `derived_state` matches Priya's
observation against the loaded corpus and finds the asset present with its `summary` equal to the
bundle's post-state. Her row now reads `landed_verified`. No webhook, no callback: **the receipt is
in the content**, so a complaint can only be marked addressed by the change actually being there.

**Tuesday — Priya checks.** *My reports* shows her row with one action, **Re-ask**. It opens a new
thread prefilled with her original question. She gets 412. The engine does **not** compare that to
4,102 and does not mark anything resolved: 12.7% of questions flip between two identical runs, so
one re-ask is not evidence. It is the reader looking — which is the only judgement available, and
the one she asked for on Monday.

**What the loop refused to claim, at every step.** The state is `addressed`, never `resolved` — on
turns where every gold table was licensed the engine's measured accuracy is 0.7555, so about one in
four complaints closed on a landed commit would still be wrong. The one free upgrade is
`retrieval_verified`, and it says only that the tables are reachable.

### The same walkthrough when it goes wrong

Four branches, because a feature is defined as much by these:

| what happens | where it goes |
|---|---|
| Dev reproduces and it now answers correctly | `declined` / `cannot_reproduce`, and Priya reads *"asked again against the corpus running now, it answered correctly. If you can still reproduce it, file it again with the new answer."* |
| The defect is in the engine, not the corpus — say `r_star_projection` | `declined` / `engine_defect`. There is nothing to patch. A pipeline that cannot conclude this will patch anyway, which is why the vocabulary has the word |
| The `git apply` conflicts, or the corpus CI reformats the file | `superseded`, derived on the next read, and it goes back to the steward. A two-state model calls this "handed off, forever" — which is today's unclosable `open: true` reintroduced one level up |
| Dev cannot settle what *active customer* means and there is nobody to ask | `blocked_on_a_person` with a required one-line note. Priya reads *"Waiting on a person: <note>. Nobody is chasing this automatically."* There is no assignee dropdown, because there is no user store to populate one |

---

## 1. Vocabulary, and the collisions it avoids

This codebase has already spent most of the obvious words. Each canonical term below was chosen
against a collision that would otherwise put two meanings on one noun.

| canonical | what it is | id | rejected, and why |
|---|---|---|---|
| **Observation** | one thing a reader or operator saw, attributed to exactly one turn | `obs-{yyyymmddThhmmssZ}-{8hex}` | *Signal* — `measure/signals.py::Signal` is a selective-prediction ranking feature. *Report* — collides with `eval/report.py` and, worse, means "dashboard" to every BI user alive |
| **Cluster** | observations sharing a localisation key. **Derived, never stored** | `cls-{16hex of the key}` | *Finding* — `tools/check_corpus_conformance.py::Finding` is a conformance violation line |
| **Patch** | a candidate corpus change: one or more asset creates/edits, with an intent | `pat-{obs or run id}-{6hex}` | *Proposal* — `eval/projection.py` already uses "proposal" for the model's ungoverned SQL, executed nowhere. Two meanings on one word in a repository that audits for exactly that |
| **Triage run** | one execution of the pipeline, and the evidence it produced | `trg-{yyyymmddThhmmssZ}-{8hex}` | — |
| **Bundle** | the directory an engineer applies in the corpus repo | `bnd-{patch id}` | *Handoff* is what it is for, not what it is |
| **Corpus release** | a tag in the corpus repository plus its `corpus_content_hash`. What an arm pins | — | *Corpus version* — the directory pointer is already called that in `.env` and is the thing this replaces |
| **Category** | the reader's refinement of *what* was wrong. The field is `category` | — | *Signal* again — a field named for a type this codebase already owns is the same collision one level down |
| **Return path** | the whole loop, as one noun in conversation | — | *Feedback loop* is fine in prose; it is too vague to be a package name |

**Two naming rules, both catching a real in-tree ambiguity.** A lifecycle field is always `state`
and never `status` — `status` already means three different things in this tree (a run's, a
column's `reliability.status`, a provenance `status`), and a fourth is how a reader comes to read
the wrong one. And **no type may be called `Evidence`**: `corpus/schema.py::Audit.evidence` owns
that word, and an evidence bundle is a `Bundle`.

**Wire values that must not be renamed.** `kind ∈ {from_refusal, wrong_answer}` is already sent by
`ui/components/answer/raise-note.tsx`, validated by `serve/raised.py::RAISED_KINDS`, and read by
`api/thread_turns.py::_open_raised_of`'s narrowing. Widening or renaming it breaks four call sites
at once for no gain. `wrong_answer` keeps a real job: the "something is wrong and I cannot say
what" bucket.

**`report_id` is retired, and this page under-priced it.** A critic argued for keeping *Report*
as the canonical noun on the strongest available grounds: `report_id` and the `rpt-` prefix were
*already* the wire. It loses on one count — "report" would be the **third** meaning of the word in
one system, after `eval/report.py` and the thing every BI user means by it, and a word carrying two
meanings in one system is the specific defect this repository audits itself for. `Observation`
collides with nothing and is more honest: a reader says what they saw, not what is wrong.

**What this page got wrong was the cost.** It said "a rename with a deleted owner, not a rename
with churn", on the grounds that `serve/raised.py::mint_report_id` was being deleted anyway. The
owner was deleted; the **contract** was not. Measured while doing it: `docs/openapi.json` pinned
`RaisedRowResponse` with seven required non-nullable fields, `report_id` was declared in the
pending queue's `meta.columns` *because the client keys a card on it*, and
`tests/api/test_the_spec_matches_the_server.py` held four assertions over that operation. The
rename touched all of them plus `ui/lib/schemas.ts` and `pending-queue.tsx`. Still the right call,
and about half a day rather than the nothing this claimed.

---

## 2. Where the code goes

```
src/governed_bi/feedback/          # the store and the vocabulary. No models, no graph.
  __init__.py                      docstring only (house rule for packages)
  events.py                        the closed vocabularies + Observation / Patch / Attribution
  validate.py                      problems_with(Observation) / problems_with(Patch) -> list[str]
  lifecycle.py                     TRANSITIONS, ACTORS, is_open(), derived_state()
  store.py                         FeedbackStore — the deep module
  attribution.py                   attribution_from_turn(entry) -> Attribution
  cluster.py                       cluster_key(), clusters()

src/governed_bi/triage/            # the pipeline. Imports feedback, corpus, retrieve, govern, serve, eval.
  __main__.py                      the ONLY entry point, and the only place triage reads os.environ
  state.py  graph.py  wrap.py  scope.py  tools.py  stamp.py  trial.py  records.py
  nodes/{intake,reproduce,triangulate,diagnose,author,validate,arbitrate,assemble,close}.py

src/governed_bi/corpus/patch.py    # NEW, beside store.py: surgical field edits (§6)
src/governed_bi/api/feedback_routes.py
src/governed_bi/api/triage_routes.py   # reads only. No route starts a triage run (§10)

tools/export_bundle.py             # patch -> bundle
tools/check_landed.py              # corpus source_refs -> derived landing states; --verify re-checks
tools/drain_raised.py              # ServeState.raised -> the store, and reports what is left
tools/check_proposal_fields_are_consumed.py
```

There is no `api/triage_app.py` and no `ask_sme`/`refute` node: the pipeline is not a served graph
(§10) and the Adversary is cut (ADR 0015 §5).

### Import layering

`tools/check_imports.py::LAYERS` must name every package under `src/governed_bi` — `undeclared()`
fails the run when it does not, and a package the list omits has no constraints at all. Two
insertions:

```python
LAYERS = (
    ("paths",), ("credentials",), ("ports",), ("register",), ("measure",),
    ("corpus",),
    ("feedback",),        # <- new: needs register + corpus, nothing above
    ("retrieve",), ("govern",), ("datasource",), ("model",), ("serve",), ("eval",),
    ("triage",),          # <- new: needs serve (reproduce) + eval (replay) + feedback
    ("api",),
)
```

`feedback` sits immediately after `corpus` for one reason worth stating: it must judge a patch
with **the same validator the loader uses** (`corpus/validate.py::problems_with`,
`corpus/parse.py::from_mapping`), not a second copy of the rules. It must not import `serve`,
`govern`, `api` or `eval` — in particular not `api/visibility.py`; the grant narrowing is composed
in `api/`, where the session lives.

`feedback` is **not** `STDLIB_ONLY` (it reaches `yaml` through `corpus`). `sqlite3` is stdlib, so
`store.py` adds no dependency.

---

## 3. The observation vocabulary

Closed. Chosen so a reader can pick one **without knowing the schema**. `kind` is the existing
two-value wire field; `category` is the new optional refinement.

On a delivered answer (`kind: "wrong_answer"`):

| the analyst reads | `category` | typical corpus effect |
|---|---|---|
| The number is wrong | `wrong_value` | **edit** `metric.expression`; **edit** `metric.summary`/`body`; sometimes **new** `term` |
| It used the wrong data — wrong table, wrong filter, wrong dates | `wrong_scope` | **edit** `table.rules` / `join.on`; **new** `join` |
| It counted or combined the wrong records | `wrong_rows` | **edit** `join.cardinality` or `join.on`; **edit** `table.grain` |
| It answered a different question than I asked | `misread_question` | usually **neither** — a generation defect |
| A word in my question means something else here | `term_mismatch` | **edit** `term.summary` (aliases must be in `summary`, ADR 0005 I1); **new** `term` |
| I can't tell whether this is right | `unverifiable` | unknown until triage |

On a refusal, `no_sql`, or an abandoned clarification (`kind: "from_refusal"`):

| the analyst reads | `category` | typical corpus effect |
|---|---|---|
| This data does exist — it should be able to answer | `false_refusal` | **new** `join` / `term` / `schema.rules`; or **neither** (retrieval defect) |
| The question it asked me back didn't make sense | `bad_clarification` | **neither** — a prompt or policy question |
| It's right that it can't answer, but it should have said why | `unclear_refusal` | **neither** |

Operator-only, distinguished by `source` rather than by `kind`:

| `category` | `source` | note |
|---|---|---|
| `column_suspect` | `operator` or `agent` | `Reliability.status` is AI-authorable, so an agent may file it |
| `column_excluded` | `operator` only | `Governance.excluded` is human-only. The store refuses this `category` from any other `source` |
| `reusable_fact` | `operator` | an operator's answer to a clarification, promoted (§9) |

**`source` is a separate column from `category`** because the same observation arrives from three
populations (`reader`, `operator`, `agent`) and the queue sorts them differently. Folding it in
would give twelve values for nine questions.

### Which cases are a new asset, an edit, or neither

The distinction matters because "neither" is the modal outcome for three of the nine categories, and
a pipeline that cannot conclude "there is nothing to patch" will patch.

| the complaint | new / edit / neither |
|---|---|
| right SQL, wrong definition of a business term | **edit** the `term` or `metric` |
| right definition, wrong join grain | **edit** `join.cardinality`, or **new** `join` |
| a refusal that should have succeeded, gold table never licensed | **new** `term`/`join` to make it retrievable — or **neither**, if the table was licensed and the layer stack refused for a different reason |
| a refusal that should have succeeded, `r_star_projection` | **neither** — an engine defect |
| a missing synonym | **edit** the existing `term`'s `summary`, not a new asset. Aliases live in `summary` because that is the retrieval channel (see M3, ADR 0015) |
| a wrong metric expression | **edit**. And it is the one class with a *free* verifier: 28 of 478 expressions do not parse and 23 resolve nowhere (**measured**) |
| a column that should be `suspect` | **edit** `column.reliability` |
| a column that should be `excluded` | **neither, from the loop's point of view** — it emits a request and a human edits by hand |
| a clarification answer that is a reusable fact | **new** `term` or `few_shot` — or **neither**, if it is a one-off filter |

---

## 4. The store

### Schema

```sql
-- feedback/store.py::_SCHEMA. Applied by _migrate() in one transaction.
PRAGMA journal_mode = WAL;          -- one writer, many readers

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS observation (
  observation_id   TEXT PRIMARY KEY,
  filed_at         TEXT NOT NULL,          -- ISO-8601 UTC, seconds
  source           TEXT NOT NULL,          -- reader | operator | agent
  kind             TEXT NOT NULL,          -- from_refusal | wrong_answer
  category         TEXT,                   -- §3, nullable: the first tap may be all there is
  note             TEXT NOT NULL DEFAULT '',   -- <= 4000 chars, stripped
  expected         TEXT NOT NULL DEFAULT '',   -- <= 200 chars. The highest-value optional field
  state            TEXT NOT NULL,          -- open | triaged | declined | duplicate | addressed
  decline_reason   TEXT,                   -- required when state = declined; §5
  duplicate_of     TEXT REFERENCES observation(observation_id),
  triaged_at       TEXT,
  -- attribution, COPIED not joined (see below)
  turn_id          TEXT NOT NULL,
  thread_id        TEXT NOT NULL,
  run_id           TEXT,
  question         TEXT NOT NULL,
  outcome          TEXT,
  terminal_reason  TEXT,
  refused_by       TEXT,
  generated_sql    TEXT,
  licensed_json    TEXT NOT NULL DEFAULT '[]',
  rendered_json    TEXT NOT NULL DEFAULT '[]',   -- needs the new register field `rendered_asset_ids`, §15.5
  schema_ranking_json TEXT NOT NULL DEFAULT '[]',
  corpus_content_hash TEXT,
  prompt_set_hash  TEXT,
  git_sha          TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_state  ON observation(state, filed_at);
CREATE INDEX IF NOT EXISTS ix_obs_turn   ON observation(turn_id);
CREATE INDEX IF NOT EXISTS ix_obs_category ON observation(category, state);

CREATE TABLE IF NOT EXISTS patch (
  patch_id         TEXT PRIMARY KEY,
  created_at       TEXT NOT NULL,
  author           TEXT NOT NULL,          -- operator | agent
  intent           TEXT NOT NULL,          -- new_asset | edit_asset | exclusion_request
                                           -- | shared_request | engine_defect | no_change
  state            TEXT NOT NULL,          -- draft | exported | withdrawn
  triage_run_id    TEXT,
  rationale        TEXT NOT NULL DEFAULT '',
  -- what changes
  asset_type       TEXT,
  namespace        TEXT NOT NULL,
  asset_id         TEXT,                   -- null for new_asset until the id is derived
  field_path       TEXT,                   -- e.g. "summary", "reliability.status", "binding.target_id"
  was              TEXT,                   -- read from the live corpus at draft time
  becomes          TEXT,
  asset_yaml       TEXT,                   -- whole document, new_asset only
  -- what it was verified against
  base_corpus_content_hash     TEXT NOT NULL,
  expected_corpus_content_hash TEXT,       -- null until the bundle is built
  ladder_json      TEXT NOT NULL DEFAULT '{}'   -- tier -> GateResult
);
CREATE INDEX IF NOT EXISTS ix_patch_state ON patch(state, created_at);

CREATE TABLE IF NOT EXISTS observation_patch (
  observation_id TEXT NOT NULL REFERENCES observation(observation_id),
  patch_id       TEXT NOT NULL REFERENCES patch(patch_id),
  PRIMARY KEY (observation_id, patch_id)
);

CREATE TABLE IF NOT EXISTS transition (       -- append-only. The audit trail.
  rowid_           INTEGER PRIMARY KEY AUTOINCREMENT,
  at               TEXT NOT NULL,
  entity           TEXT NOT NULL,          -- observation | patch
  entity_id        TEXT NOT NULL,
  from_state       TEXT NOT NULL,
  to_state         TEXT NOT NULL,
  moved_by         TEXT NOT NULL,          -- the ACTOR, never empty. §5
  detail           TEXT NOT NULL DEFAULT ''
);
```

**Attribution is copied, not joined.** The turn's own record is the natural foreign key and it is
the wrong one: `MAX_TURNS_RETAINED = 25` elides older records off `ServeState.turns`, and the
thread index is a pickle whose loader deletes the file on a bare `Exception`
(`serve/checkpointer.py`). A join into a store that removes rows is a join that returns nothing
six months later, which is exactly when a steward wants to read the queue. Copying costs ~2 KB per
observation and makes the row self-describing.

### Interface

```python
# src/governed_bi/feedback/store.py
class FeedbackStore:
    """Observations, patches, and the transitions between their states.

    Synchronous on purpose. Every loop-binding hazard `serve/checkpointer.py` documents comes
    from a store sharing the graph's loop; this one is written and read from sync FastAPI
    handlers and from `tools/`, and never touches it.
    """

    def __init__(self, path: Path | str) -> None: ...          # _migrate() runs here

    # writes
    def file(self, obs: Observation) -> str: ...               # -> observation_id
    def transition(self, entity: str, entity_id: str, *, to: str,
                   moved_by: str, detail: str = "",
                   decline_reason: str | None = None) -> None: ...
    def draft(self, patch: Patch, *, observations: Sequence[str]) -> str: ...
    def record_ladder(self, patch_id: str, tier: str, result: Mapping[str, Any]) -> None: ...

    # reads
    def get(self, observation_id: str) -> Observation | None: ...
    def queue(self, *, state: str | None = None, category: str | None = None,
              limit: int = 50, offset: int = 0) -> Page[Observation]: ...
    def patches_of(self, observation_id: str) -> list[Patch]: ...
    def observations_of(self, patch_id: str) -> list[Observation]: ...
    def history(self, entity_id: str) -> list[dict[str, Any]]: ...

    # maintenance
    def sweep(self, *, older_than_days: int, dry_run: bool = True) -> SweepReport: ...
```

`sweep` deletes terminal rows older than the cutoff and **reports** non-terminal ones without
touching them — the second half is the important one, because "nothing triaged this in 90 days"
is a fact an operator needs and a deletion would hide it.

`assert_not_a_warehouse` from `serve/checkpointer.py` is reused verbatim on the path value, for
the reason it exists there.

### Knobs

```
GOVERNED_BI_FEEDBACK_DB      default runs/feedback.sqlite, resolved against REPO_ROOT
GOVERNED_BI_FEEDBACK_ADMIN   unset -> the four engineer verbs are not mounted at all
GOVERNED_BI_PROPOSAL_DIR     default .governed_bi/proposals
GOVERNED_BI_TRIAL_SCRATCH    unset -> trial mode is off and T4 refuses to run
```

**None of these may become a `register/knobs.py` knob.** `serve/session.py::_resolved_knobs` puts
every declared knob on every serve row and `measure/gates.py::_knobs_resolved_gate` compares them,
so declaring one here moves the config hash of every arm for a value no turn consumes — the
`expand_hops` defect by construction. Pinned by
`tests/feedback/test_no_comparability_knob_names_the_feedback_store.py`.

---

## 5. The lifecycle, and the actor for every state

**The rule: a state is stored if and only if a named actor moves it. Everything else is derived
at read time.** A stored state with no actor is what today's unclosable `open: true` is.

### Stored — observation

| from → to | actor | precondition |
|---|---|---|
| — → `open` | `reader` \| `operator` \| `agent` | the turn exists and is finished |
| `open` → `triaged` | `steward` | — |
| `triaged` → `declined` | `steward` | `decline_reason` is set |
| `triaged` → `duplicate` | `steward` | `duplicate_of` names an open or addressed observation, **and the same observation joins that one's patch set** — otherwise landing counts one affected observation instead of two |
| `triaged` → `addressed` | `steward` | ≥ 1 patch in `draft` or `exported` |
| `triaged` → `blocked_on_a_person` | `steward` | a one-line `blocked_note` is set. **Not a routing action** — there is nobody to escalate to, so this is a state with a name rather than an assignee. Its copy says nobody is chasing it |
| `blocked_on_a_person` → `triaged` \| `declined` \| `addressed` | `steward` | the block cleared |
| `declined` → anything | **refused.** Re-opening is a *new* observation, because the evidence bundle of the original is attached to the turn that produced it | |

### Stored — patch

`draft → exported → ` (terminal from the store's point of view) and `draft|exported → withdrawn`.
The actor for `exported` is `engineer` and for `withdrawn` is `steward`.

### Derived — recomputed on every read, never stored

```python
# src/governed_bi/feedback/lifecycle.py
def derived_state(patch: Patch, *, loaded_corpus_hash: str,
                  asset_text_now: Mapping[str, tuple[str, str]]) -> str:
    """One of handed_off | landed_verified | landed_matched | superseded.

    `asset_text_now` maps asset_id -> (summary, body) from the corpus the session loaded.
    Nothing is stored: the answer changes when the corpus changes, and a stored copy would be
    a second answer to "did this land" that can disagree with the first.
    """
```

| state | condition | what the analyst reads |
|---|---|---|
| `handed_off` | `loaded == patch.base` | Handed to an engineer to commit. It is not in the engine yet, and nobody here can say when it will be. |
| `landed_verified` | `loaded == patch.expected` | The change is in the corpus this server is running. Ask your question again — the answer may be different now. |
| `retrieval_verified` | `landed_*`, **and** the observation's T3 coverage fixture re-run passes | The tables needed to answer this are now reachable. That is not the same as the answer being right — ask again and see. |
| `landed_matched` | hash differs, every touched asset is present, and its `summary`/`body` match the bundle's post-state | The change is in the corpus this server is running, alongside other changes that landed at the same time. Ask your question again. |
| `superseded` | hash moved off base and the content is not there | The corpus changed and this change is not in it — it was dropped or rewritten on the way. It is back with the reviewer. |

`landed_matched` is the common real case: two bundles land in one week and exact-hash matching
fails for a change that did ship. `superseded` covers a `git apply` conflict, a corpus-CI reformat,
and a reviewer editing the patch before committing — all normal.

**Note what the copy never says.** It never says the question is now answered correctly. Landing
establishes that the corpus changed and nothing more; 12.7% of questions flip between two
identical runs, so even a passing re-run would not establish it. "Ask again" is an invitation.

And the stored state is `addressed`, never `resolved`. On turns where every gold table *was*
licensed the engine's measured accuracy is **0.7555**, so about **one in four** complaints closed
on the strength of a landed commit would still be wrong. `retrieval_verified` is the one upgrade
the free ladder licenses, and it says only that the tables are reachable.

### The re-ask, and why it is not optional

```
ui/components/reports/re-ask-button.tsx     (new, ~0.5 day)
```

Every landing state's copy tells the reader to ask again, and nothing in the design session shipped
a way to. So: `landed_verified`, `landed_matched` and `retrieval_verified` carry a **Re-ask** action
on the reports page. It opens the chat surface on a **new** thread, prefilled with the question text
the store already copied off the turn record (§4).

A new thread, not the original: writing into someone else's thread is what
`api/raised_write.py` documents at length about not doing, and a second turn on the old thread
inherits up to `MAX_TURNS_RETAINED` turns of context that the comparison should not include.

**It does not grade itself.** The engine does not compare the new answer to the old one and does not
move any state on the strength of it. One re-ask is not evidence — 12.7% of questions flip between
identical runs — it is the reader looking, which is the only judgement available and the one that
was asked for in the first place. Without this button the return path is a queue; with it, it closes.

### The decline vocabulary, and the exact string for each

The reason **is** the notification. There is no declined badge without a sentence.

| `decline_reason` | what the analyst reads |
|---|---|
| `working_as_intended` | Reviewed and closed: the engine was right. The answer matches what is in the data. |
| `not_a_corpus_problem` | Reviewed and closed: the data itself is wrong or missing. The semantic layer cannot fix that, and this engine is not where it gets fixed. |
| `needs_a_schema_change` | Reviewed and closed: answering this needs a table or column that does not exist in the warehouse. Someone has to build it first. |
| `engine_defect` | Reviewed and closed as a defect in the engine, not the semantic layer. It has been written down where engine defects are written down. |
| `out_of_scope` | Reviewed and closed: this is not a question this engine is meant to answer. |
| `cannot_reproduce` | Reviewed and closed: asked again against the corpus running now, it answered correctly. If you can still reproduce it, file it again with the new answer. |
| `insufficient_detail` | Closed without a change: there was not enough here to act on. This engine does not know who filed this report, so nobody could be asked for more. |
| `wont_fix_cost` | Reviewed and closed: fixing this properly is more work than it is worth right now. It is a real problem and it is not being fixed. |

The last two are the ones this project's copy rule demands. `insufficient_detail` states the
*structural* reason nobody followed up rather than implying the reader was unhelpful. `wont_fix_cost`
says "we are not going to fix this" without hedging — a `deferred` state that never moves is the
same lie as the current pending list.

---

## 6. Writing YAML: `store.write` creates, `corpus/patch.py` edits

**Measured (M1).** Loading a table asset, changing `summary`, and calling
`corpus/store.py::write` produced a **second file with the same asset id**; `store.load` returned
1,434 assets and **zero problems**; `build_index` then raised `ValueError: duplicate index id`. The
served corpus is 1,432 assets in 178 files — one table plus ~50 inline columns per file — and
`write` puts an asset at `<root>/<namespace>/<id>.yaml`, which is not where the table it came from
lives.

And `write` is a whole-file reformat: `store.py:256` is `yaml.safe_dump(to_mapping(asset),
sort_keys=False, allow_unicode=True)` with no `width`, and `parse.py::to_mapping` "omits
defaults". So a round trip on a human-authored file drops comments, reflows every string past 80
columns, drops any explicitly-written default, and reorders keys into dataclass field order.

```python
# src/governed_bi/corpus/patch.py  — new module, same layer as store.py
def locate(path: Path, *, asset_id: str, field_path: str) -> Span:
    """Byte span of one field's value inside the file that declares `asset_id`.

    Uses `yaml.compose` node marks, not a regex and not a re-dump: an inline column's
    `summary` is nested two levels inside its table's document, and only the composer knows
    where. Raises `FieldNotLocatable` when the field is absent or the node is a merge key or
    an alias — an aliased scalar cannot be edited in one place.
    """

def apply_edit(path: Path, *, asset_id: str, field_path: str,
               was: str, becomes: str) -> str:
    """Replace one field's value in place. Returns the new file text.

    Refuses when the current value is not `was` — the concurrency check, and the reason a
    patch carries `was` at all. Preserves the block/quoting style of the original scalar,
    because changing `>` to `"` on an untouched neighbour is a diff a reviewer has to read.
    """

def apply_create(root: Path, *, asset_yaml: str, namespace: str) -> Path:
    """A new asset. This IS `store.write`, called through `from_mapping` first so the file
    that lands is one the loader accepts."""
```

Three things `patch.py` refuses to touch, and all three are refusals rather than TODOs:

- **`governance`.** ADR 0015 §8. There is no code path that writes it, and the review surface does
  not render it — a screen that can propose an exclusion *is* the tool whose absence is the control.
- **A structural change to a table's inline columns** (adding, removing or reordering `columns`).
  Column ids are derived (`corpus/identity.py::derive_column_id`), so a structural edit silently
  re-keys downstream assets. It is a hand edit with a human reading the whole file.
- **Creating a `column` asset as its own file.** This is the outage the red team found and it is
  worth spelling out. The served corpus keeps a table's columns inline, and
  `corpus/store.py::_split_inline_columns` splits them into their own assets *at load*. So a
  standalone `column` file for a column its table already declares gives the loader the same asset
  id twice — which `store.load` accepts with **zero problems** (M1) and which then raises
  `ValueError: duplicate index id` inside `build_index`, killing every `Session` build. A total
  serve outage, arriving after the commit, past a checker that cannot see it. An edit to a column's
  `summary` or `reliability` goes through `locate`/`apply_edit` on the **table's** file; a new
  column is a warehouse change, not a corpus change.

---

## 7. HTTP surface

Mounted from `api/routes.py::app` alongside `make_clarification_router`.

### Ships enabled — the reader's two verbs

| method + path | body | codes | discloses |
|---|---|---|---|
| `POST /turns/{turn_id}/raised` | `{kind, category?, note?, expected?}` | 201, 404 unknown turn, 422 bad `kind`/`category`/over-cap | nothing back but the id |
| `PATCH /observations/{id}` | `{note?, expected?}` — the after-the-fact extras | 200, 404, 409 if not `open`, 422 | nothing |

The path and the `kind` values are unchanged, so today's UI keeps working. **The 409 on a paused
thread is gone**, and that is a feature: nothing writes graph state any more, so there is no live
interrupt to consume, and the reader whose turn is paused can file.

**There is no rate limit, and this page used to say there was.** It described
`GOVERNED_BI_FEEDBACK_RATE`, "5 observations per hour per turn", and concluded "one turn cannot be
used to grow the store without bound". That variable exists nowhere in the tree and the invariant
does not hold: the write verbs are unauthenticated, so a caller reaching the port can grow
`runs/feedback.sqlite` until the disk fills. What bounds a single row is `NOTE_MAX_CHARS` (4,000) and
`QUESTION_MAX_CHARS` (8,000); nothing bounds the count. Open, and named as open in
[open work](open-work.md) rather than described here as done.

### Ships enabled — reads

| method + path | notes |
|---|---|
| `GET /observations?state=&category=&limit=&offset=` | oldest first. `meta.truncated` is load-bearing (ADR 0009) |
| `GET /observations/{id}` | the row plus its patches plus the derived state of each |
| `GET /clarifications/pending` | unchanged shape. Its note half now comes from one indexed query instead of a 40-round-trip thread walk, and it passes the narrowing seam, which it does not today |

### Unmounted unless `GOVERNED_BI_FEEDBACK_ADMIN` is set — the steward's four verbs

404 when unmounted, not 403: a 403 confirms the route exists.

| method + path | body |
|---|---|
| `POST /observations/{id}/triage` | `{to: "declined" \| "duplicate" \| "addressed", decline_reason?, duplicate_of?}` |
| `POST /patches` | a `Patch` draft |
| `POST /patches/{id}/withdraw` | `{reason}` |
| `GET /patches?state=` | — |

**Producing a bundle is a CLI, not a route** (§8). A route that writes a directory an engineer
then applies is a route that lets anyone reaching the port stage a corpus change.

### What actually narrows these payloads

**`narrow_feedback_rows` does not exist, and this section used to describe it as though it did** —
with a signature, a docstring and a test file name, none of which are in the tree. There is no
grant-based narrowing on the return path. What shipped is coarser and worth stating exactly, because
the difference is the difference between "narrowed per grant" and "narrowed per switch".

`api/feedback_routes.py` projects from three **allowlists** —
`PUBLIC_OBSERVATION_FIELDS`, `PUBLIC_PATCH_FIELDS`, `PUBLIC_TRANSITION_FIELDS` — and widens to every
field only when `GOVERNED_BI_FEEDBACK_ADMIN` is set, which is the same read that mounts the steward's
verbs. Withheld from an unauthenticated caller:

| withheld | why |
|---|---|
| `gold_sql`, `gold_fingerprint`, `pred_fingerprint` | the **held-out** benchmark's reference answer. V12 keeps a held-out question out of the corpus; serving the answer over HTTP is the same contamination with the gate bypassed |
| a patch's `was`, `becomes`, `rationale`, `base_corpus_content_hash` | the steward's working draft. `GET /patches` 404s under the same switch, and until this was fixed the detail route served the content regardless |
| a transition's `detail` | whatever the steward typed. The *shape* of the append-only trail is public; the sentences are not |

An **allowlist and not a denylist**, because the denylist is what produced the defect: the projection
enumerated the dataclass, so a field added to `Observation` reached an unauthenticated route by the
next deploy. `gold_sql` arrived exactly that way. Asserted by
`tests/api/test_the_queue_does_not_serve_the_benchmark.py`, which fails on any field reaching the
wire that is not on a list.

**Still disclosed, and unchanged:** `question`, `generated_sql`, `licensed`, `missing_tables`. Those
are what make a row reviewable, and `/audit/turns/{id}/trace` already discloses a turn's SQL to the
same caller — the accepted position that predates this surface. A per-grant seam is the honest next
step and is not built.

---

## 8. The bundle

```
bnd-pat-…/
  MANIFEST.yaml        the patch, its observations, the ladder results, both hashes, the engine sha
  COMMIT_MSG.txt       generated. First line <= 72 chars. Names the observation ids, not the prose
  changes.patch        `git apply -p1`-able, produced against base_corpus_content_hash
  after/               the post-state files, full text, so a reviewer can read the result not the diff
  evidence/
    observations.md    what each reader said, verbatim, in a fenced block
    turn-<id>.json     question, SQL, ledger, licensed, rendered, schema_ranking
    ladder.json        every tier's GateResult, including the ones that did not run and why
    reproduction.md    what the reproducer found, or that it was not run
```

```bash
uv run --frozen python tools/export_bundle.py --patch pat-… --out ./bundles
uv run --frozen python tools/export_bundle.py --patch pat-… --dry-run   # prints the diff, writes nothing
```

`COMMIT_MSG.txt` carries **no reader prose**. The commit message is model- or template-generated
from the typed fields; the reader's sentence lives in `evidence/observations.md`, inside a fence,
where it cannot become a line of a commit log that some other tool later renders unescaped.

**Applying it is manual, and the doc says the whole command:**

```bash
cd ../BIRD-corpus && git checkout -b return/pat-… && git apply -p1 ../governed-bi/bundles/bnd-pat-…/changes.patch
git commit -F ../governed-bi/bundles/bnd-pat-…/COMMIT_MSG.txt
```

There is no `--apply` flag on `export_bundle.py` and there will not be one. The write is the
human's.

**Nobody authorises applying a bundle, and that has to be said rather than left blank.** On this
deployment one principal drafts the patch, accepts it, and applies it — there is no separation of
duties because there is no second identity to separate into. The only real control is the corpus
repository's own review: a fork that wants two people in the loop gets it by requiring a reviewer
on that repo's pull requests, which is outside this engine and is where it belongs.

**A bundle can go stale, and there is a command for it.** Between export and commit the corpus can
move — another bundle lands, or someone edits by hand. `apply_edit` refuses when the current value
is not `was`, so a stale patch fails loudly at `git apply` rather than silently overwriting. To
check before trying:

```bash
uv run --frozen python tools/check_landed.py --verify --bundle ./bundles/bnd-pat-…
```

It reports one of: applies cleanly; the base moved but the touched fields are untouched (re-export
and go); or a touched field changed under it (back to the steward). Without this the engineer finds
out from a conflict, which is a worse place to learn it.

---

## 9. An operator's answer becomes a corpus fact without resuming anyone's thread

`ui/components/clarifications/pending-queue.tsx` is read-only by design: answering there would
resume a thread this operator was not the one asked (ADR 0006 B9). That constraint holds
unchanged.

The pending queue gains **a link, not a button.** The link opens the steward surface with a
prefilled `reusable_fact` observation carrying the paused turn's question and the clarification
text. The copy is explicit:

> The paused conversation stays paused, and whoever asked it will not get a reply. What you write
> here becomes a proposed change to the semantic layer, so the next person who asks does not have
> to be asked back.

Nothing calls `command.update`. Nothing calls `POST /threads/{id}/state`. The paused thread is
read, never written.

---

## 10. The triage pipeline

**Not registered in `langgraph.json`.** It is a `StateGraph` compiled and invoked by a local entry
point:

```bash
uv run --frozen python -m governed_bi.triage --cluster cls-… --stop-after diagnose
```

**Why not a served graph.** `api/auth.py::_no_state_writes_on_a_new_run` denies only
`command.update` and `command.goto`; a payload of `{"assistant_id": "triage", "input": …}` carries
no `command` at all, so `_command_of` returns `None` and the hook returns without objecting. The
platform already lets an anonymous caller spend budget on `serve` — `api/routes.py` says so — but
registering `triage` raises the ceiling per request from one turn (~45k tokens) to a fan-out bounded
only by an operator-set cap (~290k at the default), on the one graph that also writes files. There
is no rate limiter anywhere in `api/`. A local entry point costs nothing.

**So there is no `interrupt()` and no HITL pause.** When the Diagnoser cannot settle a semantic
question the run **ends** and writes an observation with `category: needs_sme` into the store; a
steward answers it in the review surface, which drafts the patch. This deletes
`serve/resume.py::authorise_resume` from the design along with the problem it could not solve: under
one principal the gate compares the batch *launcher* against the resumer, not the reader who
complained, so it distinguishes nobody.

Nodes:

```
START -> intake
intake --(Send x K)--> reproduce_one -> triangulate
intake --(nothing to reproduce)--> triangulate
triangulate --> {diagnose, close}
diagnose    --> {author, close}                # `close` when locus is no_asset_* or needs_sme
author      --> validate                       # the ladder, T0-T2
validate    --> {refute, arbitrate}
refute      --> arbitrate                      # the ladder, T3 (+T4 if enabled)
arbitrate   --> {author, assemble, withdraw}   # bounded: revision < max_revisions
assemble    --> close
withdraw    --> close
close       --> END
```

Reducers: `operator.add` on `reproductions`, `critiques`, `usage`, `sme_answers`. **No reducer**
on `diagnosis` or `patch` — the revision loop overwrites them, and `operator.add` there would make
"the patch" a list whose last element every downstream node has to remember to take.

`arbitrate` returns a `Command[Literal["author", "assemble", "withdraw"]]` and must have **no**
`add_edge` out of it, only `add_conditional_edges` — otherwise every destination runs.

### Tools, by role

Read: `read_asset` (minus `audit`, minus `governance`), `list_assets`, `retrieval_trace`,
`sample_column`, `probe_query`, `read_diagnosis`. **No tool replays a question** — the trial
corpus belongs to the ladder (§11 T4), not to an agent.
Write: **one**, `stage_asset`, plus `stage_exclusion_request`, `stage_shared_request`,
`withdraw_staged`.

`stage_asset` does six things in this order, and the order is the control:

1. `yaml.load` through `corpus/store.py::_loader_class()` — the same loader, so YAML 1.1 `on:`
   aliasing and utf-8-sig behave as they do in production.
2. `triage/stamp.py::restamp_model_authored` **drops** `governance` and **overwrites**
   `provenance` to `source: curator, status: proposed`.
3. `corpus/parse.py::from_mapping` then `corpus/validate.py::problems_with`. Problems come back as
   a **tool reply the model can act on**, not an exception — so it can fix its own summary length.
4. `corpus/identity.py::validate_asset_id` and `validate_path_component(namespace)`.
5. Write into `<proposal dir>/<id>/assets/<namespace>/<id>.yaml`.
6. Record the staged row on state.

**The asset id is derived, never taken from the model** (ADR 0008 §1.2). A model-supplied `id` is
a problem, not an override.

### `audit` never reaches a model

`corpus/schema.py::Audit`: "Never enters the analyst context." A triage agent is not an analyst,
but extending the rule is the cheap choice and reversing it later is a decision someone can make
with evidence. `governance` is withheld for a different reason: an Author that can read one can
pattern-match one, and showing it the shape is the first half of teaching it to forge it.

### Prompts

`register/prompts.py`, in a **second registry** with its own digest:

```python
TRIAGE_PROMPT_REGISTRY: Mapping[str, Prompt] = {...}
def triage_prompt_set_hash(overrides=None) -> str: ...

def _assert_the_two_registries_partition_this_module() -> None:
    """Every `Prompt` at module scope is in exactly one registry.

    `prompt_set_hash` is the serve arm's treatment identity and digests PROMPT_REGISTRY in
    full, so a triage prompt in it moves every serve arm's identity on an edit that changes no
    serve behaviour. Two digests is the fix; the cost is that a prompt could be in NEITHER,
    which is a prompt no hash covers — strictly worse than the problem. Hence the assert.
    """
```

### Trial corpus

`triage/trial.py::corpus_under_trial(...)` — **T4's** facility, and the only place staged prose is
rendered into a real prompt. It was designed as an agent tool for an Adversary that is now cut
(ADR 0015 §5); moving it into the ladder is strictly better, because a deterministic driver
replaying a fixed question set is auditable in a way a model choosing what to replay is not. This
is also what finally gives `corpus/snapshot.py` a caller.

- `mode="off"` — the default when `GOVERNED_BI_TRIAL_SCRATCH` is unset. T4 refuses to run.
  Fails closed, because a trial that silently mutates the live corpus is the most expensive failure
  in this package.
- `mode="copy"` — `corpus/snapshot.py::snapshot(corpus_root, scratch)`, then the staged tree over
  the copy, then a `Session` on the copy. `corpus_root` is never touched.
- `mode="in_place"` — opt-in, lock-guarded by an exclusive `<corpus_root>.trial.lock` that
  **refuses rather than waits** (the holder may be a 1,351-question arm), and always `restore`s
  with a post-condition `not drifted(corpus_root, expected)`. A failed post-condition is a crash,
  not a warning.

> **Fix `snapshot` before writing this caller.** `corpus/snapshot.py:83` is
> `if dest.exists(): shutil.rmtree(dest)`, guarded only by `_refuse_nesting`; `_identify_corpus` —
> the "is this actually a corpus" check — guards `restore` only. **Measured:** pointed at a scratch
> directory holding unrelated files, they were deleted. Worked example from the red team: with
> today's `.env`, `GOVERNED_BI_TRIAL_SCRATCH=C:\Users\zhang\Code\governed-bi` passes the nesting
> check against `../MS Fabric Facilities/corpus` and deletes the working tree.
>
> Three fixes, all required: `snapshot` applies `_identify_corpus` to `dest` when `dest` exists, so
> it will only replace something that is already a corpus; `corpus_under_trial` requires the scratch
> path to be **absent or an identified corpus** and refuses otherwise; and the scratch path is
> composed as `<GOVERNED_BI_TRIAL_SCRATCH>/<run id>` where the run id is minted by the process, so
> no caller-supplied string ever reaches `rmtree`.

### Cost

Estimates, from the cap structure and not from a measurement, and the whole table is sensitive to
the first line. A delivered context block is capped at 20,000 rendered chars by conformance V16, so
~5k tokens per agent call.

| | model calls | tokens (est.) |
|---|---:|---:|
| one serve turn (the unit) | ~8 | **~45k** |
| `reproduce_one` × 3 | 3 serve turns | 135k |
| `diagnose` | ~7 | 27k |
| `author` | ~5 | 18k |
| `refute` (incl. 2 replays) | ~6 + 2 serve turns | 110k |
| **default cluster** | ~18 + 5 serve turns | **~290k** |

**79% of the bill is the five serve turns.** Marginal cost of one more observation in a cluster is
+45k up to the reproduce cap, then **zero** — which is the batching argument, quantified.

Cheap paths, in the order to ship them:

1. `stop_after="diagnose"`, `reproduce_mode="from_record"`, `refute_enabled=False` — **~30k, one
   tenth.** Output is a localised finding, no YAML at all. **Ship this first.**
2. `reproduce_cap=1`, `replay_cap=0`, `refute_enabled=False` — ~80k. The patch records
   `assurance: unrefuted` and the steward reads that word.
3. `reproduce_mode="from_record"` with authoring on — ~45k, and the record says `reproduced: null`
   so nobody thinks the complaint was re-checked.

`reproduce_workers = 1` (serial) by default. LangGraph runs every `Send` of one super-step
concurrently and offers no per-fan-out limit, so serialisation means fanning out N at a time and
re-entering the router; at N=1 that is a chain. Project experience: one curator-sized turn is
~60% of the local TPM quota, and paid work runs on the server.

---

## 11. The verification ladder

Every tier is a **delta gate**. The served corpus already produces 361 `build_structure` problems
(**measured**), so a "zero problems" gate rejects production, gets waived, and a waiver is how a
real finding goes green.

| tier | command | cost (**measured** where marked) | pass condition |
|---|---|---|---|
| **T0** | `tools/check_corpus_conformance.py` over the staged tree | ~1.6 s | the file parses, `from_mapping` accepts it, `problems_with` is empty, the id validates |
| **T1** | conformance whole-tree + `build_structure` + `build_index` + `tools/govern_bench.py` | 3.4 s (facilities) / 26 s (BIRD) **measured**; index 0.03 s lexical, 0.27 s warm semantic **measured**; govern_bench 1.7 s **measured** | no **new** finding by rule id; `build_index` does not raise; `build_structure` problem count does not rise |
| **T2** | `tools/check_closed_domains.py` + the metric-expression resolver, against the live catalog | seconds, needs a DB, no model | every bare identifier in a metric `expression` resolves on `base_table` or through a declared join |
| **T3** | `tools/routing_recall.py --baseline`, paired, agent model off | minutes, **~$0** — the vector cache is 100% warm and one new asset costs **2** embed calls **measured** | **per question, not per rate**: no question loses gold-table coverage. Report the questions that gained. **Not applicable to a `body`-only patch** — see below |
| **T4** | targeted replay of the cluster's questions | tens of paid calls | the specific mechanism changed — see below |
| **T5** | a paired arm | ~52 min wall at `workers=10`, ~74M input tokens **measured off `runs/eval/driver_v4.log`** | a **release** gate. Never a patch gate |

**`tools/govern_bench.py` is in T1 but it is not a patch gate.** It runs the fictional world
declared in `govern/adversarial.toml` (`open-work.md` §3.11 says so, and a prototype confirmed
byte-identical output before and after a corpus patch). It is there to catch a *code* change
riding along in the same commit, and the design says so rather than letting somebody believe the
suite is watching the corpus.

### The readout, per category

EX is not on this list except at T5. `docs/open-work.md` §3.12 gives the reason: MDE ≈ 2.3pp, and
§1.5's largest single coverage bucket is 7 questions — 0.52pp.

| category class | primary readout | tier | resolution |
|---|---|---|---|
| `false_refusal` | the turn's `terminal_reason` stops being `r_table_not_licensed`, and coverage becomes true | T3 | one question |
| `wrong_scope` (coverage) | `all_gold_tables_licensed` per question; `pulled_in.n_connect` | T3 | one question |
| wrong table inside the licensed set | `licensed` diff + `schema_ranking` gold rank | T3 report, T4 to confirm the answer flipped | exact |
| `wrong_value` (definition) | the metric resolver passes, then T4's `generated_sql` binds the intended column | T2 + T4 | exact |
| answer shape (projection, DISTINCT) | `BINDING/r_star_projection` turn-hit count, McNemar on the indicator | T5 | ~1.1pp |
| `bad_clarification` | `outcome == clarification` and `licensed == ∅` counts | T4 | per question |
| a benign statement refused | the adversarial suite's benign half | T1 | zero-noise |
| prose that reaches the prompt | the new content rules | T0/T1 | exact |

Every zero in that table is reported through `measure/stats.py::rule_of_three`, so `0/53` renders
as "≤ 5.7%" and cannot be quoted as "0% false refusals". That function already exists.

### The readout at T4/T5: a mechanism selects the stratum, EX on the stratum is the verdict

This is the correction that survived the critique round, and the reasoning matters more than the
recipe because the first two attempts were both wrong.

**Attempt 1 — read EX on the whole arm.** On the v3_fold → v4 pair, same 1,351 questions, EX is
+1.18pp with 126 discordant against an MDE of 2.33pp. Not decisive, and it reproduces
`open-work.md` §3.1 exactly, which is what licenses everything below.

**Attempt 2 — retire EX and read a mechanism indicator instead**, on the grounds that
`BINDING/r_star_projection` moves −1.94pp on 29 discordant pairs against an MDE of 1.12pp, so a
rarer event resolves better on the same n. **Withdrawn: it is a unit error.** MDE is denominated in
points of the whole population, and the two readouts' base rates differ by two orders of magnitude.
That indicator's *maximum possible* effect is 2.15pp, so it has **1.92 resolvable steps** before it
saturates, against EX's 28.5. `COLUMNS/r_column_not_allowed` is 1.16× — already saturated — and the
first draft of the table labelled it decisive.

**What holds — attempt 3.** Restricted to the 30 turns where either arm hit that mechanism, **EX
moves +23.33pp on 9 discordant pairs, exact McNemar p = 0.0391.** Significant. The first draft
called it "not decisive" because 23.33pp is under that stratum's post-hoc MDE of 28.02pp — and a
post-hoc MDE is not a significance threshold; `measure/stats.py::mde`'s own docstring says so.

So the procedure is: **count the mechanism to choose the population a patch could have touched,
then read EX on that population with an exact McNemar test.** Two instruments, two jobs.

Three limits, all load-bearing:

1. **There is no measured null for a mechanism count.** `run1`/`run2` — the designated replicate —
   carry **zero ledger rows**, so nothing on disk says how much an indicator moves between two
   identical runs, and the stratum is therefore chosen after seeing the arm. One nightly re-run of
   `run1`'s configuration under the current harness fixes this and is the cheapest high-value
   experiment in the design.
2. **`mechanism_indicator` must return `None` on an empty ledger, not `False`.** The first draft of
   the table computed `False`-on-empty, and 12 of 1,351 pairs have an empty `attempts` on at least
   one side. Under the specified convention `mcnemar` correctly reports unmeasured; restricted to
   the 1,339 two-sided pairs the effect is −1.94pp with the p-value unchanged. **The defect was in
   where the number came from, not in the number** — which is exactly why the convention belongs in
   the code, with a declared mutation, and not in a habit.
3. **One number is barred.** `BINDING/r_star_projection`'s MDE of **1.12pp** may not be quoted: it
   is post-hoc from the pair's own discordance, it has no null to compare against, and it is only
   1.9× smaller than the largest effect its indicator can express. It reads like instrument
   precision and it is a two-graduation ruler.

### New conformance rules

Ids continue `tools/check_corpus_conformance.py`'s `RULES` table. Four of the six have a **live
population measured today**, which is what separates them from rules written on a hunch.

| rule | predicate | live findings |
|---|---|---|
| **V17a** | a metric `expression` parses as SQL at the engine's dialect | **28 of 478** on BIRD: `DIVIDE(…)`, `COUNT(x WHERE y)`, `<condition>` |
| **V17b** | every bare identifier in a metric `expression` resolves on `base_table`, or on a table reachable through a declared join — and then the join must be declared | **23 metrics / 28 column refs**; 10 reachable only through a join, 18 unreachable anywhere |
| **V18** | a closed-domain claim ("one of", "always", "only") carries an observation in `audit.evidence` | not measured |
| **V19** | no model-visible **`body`** names a `governance.excluded` column or asset. **`body`, not `summary`** — `summary` never enters the prompt (`serve/context.py`), it enters the retrieval index | **zero**, because zero assets are excluded in either corpus. Free to add; cannot regress anything |
| **V21** | model-visible text passes `govern/guard.py::GUARD_RULES` — reusing them, not restating them | **one**: `public_review_platform/few-shots/fs_public_review_platform_0012.yaml` ships two `U+200B` |
| **V23** | asset ids are unique across the tree | **zero today**, and the rule exists because a duplicate passes conformance and then raises `ValueError: duplicate index id` in `build_index` (**measured**) |

**V10 and V12 are not disclosure rules and must not be cited as the existing control.** V10 is "no
text discloses how an unreliable column was made" — it exists for the BIRD obfuscation decoys —
and V12 is held-out-question leakage. Both police benchmark integrity. On a production corpus they
police nothing, so V19 is the *first* control of its kind, not a reinforcement.

**The ratchet.** Pre-existing findings are pinned **by name** in the corpus repository. The set may
shrink freely and may not grow, and closing one fails the build as loudly as adding one — names
and not a count, because 28 findings and 28 *different* findings are the same integer.

### Comparability

Two blockers, both **measured**:

1. `comparability_keys()` is 50 names and **not one contains "corpus"**, so an arm whose treatment
   is the corpus cannot declare it and `register/arm_profiles.py` makes it `cannot_evaluate`.
2. `corpus_content_hash('../BIRD-corpus')` at HEAD is `6e5c7b4be83d5682…`; `arms.toml` declares
   `86ed1dbf…` on all four arms. The two commits in between add only `LICENSE` and `README.md` —
   no asset changed — and the digest moved anyway. **`--arm v4` against the checked-out tip is
   refused today.**

So: one new comparability knob `corpus_release`, naming a **tag** and not a directory. Patches
land continuously; arms pin releases. Plus `hypothesised_effect` and `readout` on `ArmProfile`,
which finally gives `eval/power.py::require_power` the caller `open-work.md` §3.10 records it as
lacking — at which point an arm that cannot detect its own hypothesis fails before it spends
anything.

**But do not plan a release around a paired arm.** What bounds the cadence is the stock of
detectable effect, and it is nearly spent. Everything T3 can see is the coverage debt — 79
questions whose gold tables were never licensed — worth at most +5.85pp, which at the measured EX
scales to +3.98pp against an EX MDE of 2.33pp: **1.7 detectable releases in the entire debt.** And
each one needs *two* new arms, not one, because no pair on disk reaches `knobs_comparable`
(§Comparability blocker 1 above is why), so the first release has to buy its own control: ~150M
input tokens, ~104 minutes.

Therefore the **release headline is the T3 per-question coverage delta** — resolution one question
(0.08pp), cost ~$0 — and a paired arm is what you buy when a *code* change needs pricing. The
tokens a release arm would have spent are better spent on the null the readout above is missing.
`ArmProfile.hypothesised_effect` exists partly to make that refusal automatic: a release arm
declaring a +0.5pp hypothesis fails `require_power` before it spends anything.

**Only two of this design's declarations are actually caught by CI.** `tools/check_declared_is_consumed.py`
has four rules, over knobs, record fields and state channels. `corpus_release` is a knob, so a
missing reader fails the build by name. `ArmProfile.hypothesised_effect`, `ArmProfile.readout`, the
mechanism register's entries, the store's SQLite columns and `Attribution`'s fields live in
namespaces none of the four rules walk — so for those, "declared with no reader" is held by review
and not by CI. Closing it is one more rule of the same shape; until then this paragraph is the
control.

---

## 12. CI

### Engine repository — `.github/workflows/ci.yml`, `test` job

```bash
uv run --frozen python tools/check_imports.py                    # LAYERS names feedback + triage
uv run --frozen python tools/check_proposal_fields_are_consumed.py
uv run --frozen pytest tests/feedback tests/triage -rs
```

The nightly `mutate` job gains the return path's declared mutations (§13).

### Corpus repository

This is the CI the engineer's commit passes through, and it is specified here because the engine
is where the checker lives. It must **not** need a model credential or a database.

```bash
uv run --frozen python ../governed-bi/tools/check_corpus_conformance.py --corpus-dir .
uv run --frozen python ../governed-bi/tools/check_ratchet.py --pins .conformance-pins.txt
uv run --frozen python -c "from governed_bi.retrieve import build_index; ..."   # T1: it must start
```

### What runs in neither

T4 and T5. They cost money, so they are launched by a person who has decided to spend it, and the
artifact records what they cost.

---

## 13. Build order

Steps 0–4 spend nothing and are independently useful. Step 5 is the first place this design can be
wrong in a way that costs money. Day estimates are for one engineer familiar with the tree.

| # | what | days | why here |
|---|---|---:|---|
| **0** | **`git init` the served corpus**, first commit, and fix `corpus/snapshot.py::snapshot`'s unguarded `rmtree` | 0.5 | the landing half has nothing to land into, and the first `snapshot` caller would weaponise a real defect |
| 1 | `feedback/{events,validate,lifecycle,cluster}.py` + `store.py` + `attribution.py`; `LAYERS`; the two reader verbs writing to the store; the readers unioning store + channel | 4 | closes "nothing ever closes an open row" with no model anywhere. **Answers the first real question: do complaints cluster at all?** |
| 2 | the analyst capture UX (§15.2) + `/observations` reads + `/reports` (§15.3) + **the re-ask button** + `review-copy.ts` and its check script | 3.5 | the copy stops being a small lie, and the reader can check for themselves |
| 3 | `corpus/patch.py` + `tools/export_bundle.py` + `tools/check_landed.py` (incl. `--verify`) + `/review` (§15.4–15.8) + the four admin verbs | 5 | **a complete loop with no agents in it.** A steward can hand an engineer a bundle today |
| 4 | the ladder T0–T2, the six new rules, the ratchet, `corpus_release`, `ArmProfile.hypothesised_effect` | 4 | free gates and the comparability fix, both independent of the pipeline |
| 5 | `tools/drain_raised.py`, then delete `serve/raised.py`, `api/raised_write.py`, `ThreadTurnLog.append_raised`/`raised_of` and the reader union | 1.5 | **after** the drain reports zero and holds. The channel deletion is its own step because its risk is entirely migration risk |
| 6 | the prompt-registry split + `triage/` skeleton + `diagnose` at `stop_after="diagnose"` | 4 | **first tokens spent.** Ship and measure the Diagnoser before building on it |
| 7 | `reproduce` in `replay` mode; T3 wired as a gate | 3 | |
| 8 | `stamp.py`, `stage_asset`, `author`, `assemble` | 4 | |
| 9 | `trial.py` (+ the `snapshot` fix) and T4; `arbitrate`, the bounded revision loop | 3 | the trial corpus is a ladder facility, so this step is useful even if step 6 kills the pipeline |

**Steps 0–3 are the minimum viable loop: ~13 days, and it contains no model call.** Everything
from step 6 on is conditional on the step-6 measurement. There is no step for `ask_sme` or for an
Adversary: both are cut (ADR 0015 §5).

### Three things the design cannot know, and the cheapest experiment for each

| unknown | experiment | when |
|---|---|---|
| do complaints cluster? | step 1 ships `cluster_key` and `GET /observations` reports the size distribution. Zero cost | after ~30 real observations |
| can a model localise a defect to an asset? | step 5's diagnosis-only mode over 20 observations. ~600k tokens. Score against a steward's own localisation | before step 7 |
| will analysts use a picker? | step 2 ships `category` as optional. Measure the share of observations that carry one | after ~30 |

If the Diagnoser scores at reflector quality (OOF AUC 0.597 on the easier task), **stop at step 6**
and the honest product is a triage queue with no authoring.

---

## 14. Test names

Grouped by what breaks. Names are sentences, per the house convention.

**The store and the lifecycle**
- `tests/feedback/test_every_stored_state_names_its_actor.py` — walks `TRANSITIONS`, fails on a stored state whose actor is empty
- `tests/feedback/test_a_declined_observation_cannot_be_reopened.py`
- `tests/feedback/test_a_duplicate_joins_the_patch_set_of_its_original.py` — the prototype found landing counted 1 affected observation instead of 2
- `tests/feedback/test_a_note_can_be_filed_on_a_paused_thread.py` — the 409 that went away
- `tests/feedback/test_no_comparability_knob_names_the_feedback_store.py`
- `tests/feedback/test_the_derived_landing_states_are_not_stored.py`
- `tests/feedback/test_a_superseded_patch_does_not_read_as_handed_off.py`

**The corpus write**
- `tests/corpus/test_an_edit_does_not_create_a_second_file_with_the_same_id.py` — M1, as a regression
- `tests/corpus/test_a_one_word_summary_edit_is_a_one_line_diff.py`
- `tests/corpus/test_an_edit_refuses_when_the_current_value_is_not_was.py`
- `tests/corpus/test_patch_refuses_a_governance_field.py`
- `tests/corpus/test_snapshot_refuses_a_destination_that_is_not_a_corpus.py` — the rmtree finding

**The pipeline**
- `tests/triage/test_a_full_run_leaves_corpus_content_hash_unmoved.py` — and the asset-id set unchanged
- `tests/triage/test_the_author_cannot_write_a_governance_block.py`
- `tests/triage/test_source_human_status_certified_is_restamped_curator_proposed.py`
- `tests/triage/test_the_reproduction_rate_never_lands_in_confidence.py`
- `tests/triage/test_the_revision_loop_is_bounded.py` — a scripted model whose patch never passes `validate`
- `tests/triage/test_a_trial_replay_leaves_the_corpus_root_byte_identical.py`
- `tests/triage/test_an_in_place_trial_restores_and_asserts_no_drift.py`

**Identity and comparability**
- `tests/conformance/test_the_two_prompt_registries_are_disjoint.py::test_prompt_set_hash_is_unmoved_by_a_triage_prompt_edit` — asserts `b1f9e4d7d230cb97`
- `tests/conformance/test_corpus_conformance_rules_fire.py` — extended with all four of M2's breakages
- `tests/eval/test_a_corpus_release_is_a_declarable_treatment.py`
- `tests/api/test_the_return_path_respects_the_grant.py::test_the_reader_note_is_a_declared_exemption`

### Declared mutations

Under an `rp-` prefix in `tools/mutation_catalogue_data_2.py`, because §3.9 of `open-work.md` is
about tests that could not fail:

| id | mutation | must be caught by |
|---|---|---|
| `rp-1` | `restamp_model_authored` returns its input unchanged | the restamp test |
| `rp-2` | `stage_asset` writes into `corpus_root` | `test_a_full_run_leaves_corpus_content_hash_unmoved` |
| `rp-3` | `derived_state` always returns `handed_off` | `test_a_superseded_patch_does_not_read_as_handed_off` |
| `rp-4` | `apply_edit` drops the `was` check | `test_an_edit_refuses_when_the_current_value_is_not_was` |
| `rp-5` | V19's predicate returns no findings | the conformance fixture |
| `rp-6` | V23's predicate returns no findings | the duplicate-id fixture |
| `rp-7` | `narrow_feedback_rows` returns its input | the grant test |
| `rp-8` | the admin router mounts unconditionally | a 404 assertion with the env var unset |
| `rp-9` | `sweep` deletes non-terminal rows | the sweep test |
| `rp-10` | `mechanism_indicator` returns `False` instead of `None` on an empty ledger | a test asserting `mcnemar` reports unmeasured on a pair with an empty `attempts` — this is the convention that produced a wrong provenance for a right number, so it is pinned rather than remembered |
| `rp-11` | `derived_state` upgrades to `retrieval_verified` without re-running the fixture | a test that the upgrade requires a passing T3 re-run |
| `rp-12` | `check_landed.py` treats an unmatched `source_refs` id as matched | a test with a deliberately mistyped `obs:` ref, asserting it is reported as dangling |

---

## 15. The surfaces

Three roles, three screens, and one module that owns every string.

### 15.1 New and changed files

| path | what |
|---|---|
| `ui/app/reports/page.tsx` | new route, analyst-facing |
| `ui/app/review/page.tsx` | new route, steward-facing |
| `ui/components/answer/raise-note.tsx` | rewritten (§15.2) |
| `ui/components/answer/category-picker.tsx` | new |
| `ui/components/reports/report-list.tsx` | new |
| `ui/components/reports/report-status.tsx` | new — the status chip **and** its sentence, one component so §5 has one renderer |
| `ui/components/reports/re-ask-button.tsx` | new (§5) |
| `ui/components/review/review-surface.tsx` | new — the two-pane shell |
| `ui/components/review/review-queue.tsx` | new |
| `ui/components/review/cluster-panel.tsx` | new |
| `ui/components/review/evidence-bundle.tsx` | new |
| `ui/components/review/reproducer.tsx` | new |
| `ui/components/review/asset-diff.tsx` | new |
| `ui/components/review/decision-bar.tsx` | new |
| `ui/components/review/handoff-panel.tsx` | new — the bundle download and its manifest, post-export |
| `ui/components/clarifications/pending-queue.tsx` | one link and two paragraphs of copy (§9) |
| `ui/lib/category-taxonomy.ts` | new — `category` → label. The only mapping |
| `ui/lib/review-copy.ts` | new — **every** user-facing string in §3, §5 and §15 |
| `ui/lib/my-reports.ts` | new — the `localStorage` store |
| `ui/lib/schemas.ts`, `types.ts`, `api-client.ts`, `hooks/queries.ts` | the zod schemas, `z.infer` types, 9 client methods, 6 hooks |
| `ui/components/layout/nav.tsx` | two `LINKS` entries |

**`ui/lib/review-copy.ts` is the honest-copy rule made mechanical.** Every string lives there,
keyed by state, and `ui/scripts/check-review-copy.ts` runs beside `npm run lint` like the other
`check-*.ts` scripts. It asserts two things: every member of the observation / patch / decline
state unions has a string, and no string matches a banned list — `robust`, `seamless`,
`comprehensive`, and the two this project cares about most, **`automatically`** and
**`will be fixed`**, outside a negation. Neither check is possible with strings inline in
components, which is the whole reason the module exists.

### 15.2 The analyst: capture in two clicks

Three states, and the analyst may stop after the first.

**State 1 — the trigger.** One `variant="outline" size="sm"` button on the answer card, same place
as today, same label (`"This answer is wrong"` / `"This refusal looks wrong"`). It already works and
it is the one string a reader recognises.

**State 2 — the picker.** Clicking expands **in place** — no dialog, no navigation, because the
analyst is about to point at the answer — into a vertical list of five rows (delivered) or three
(refused), from §3. **Every row is one tap and files immediately. There is no submit button.** The
median interaction is two clicks and zero typing, against today's two clicks plus an empty textarea.

**State 3 — the extras, shown *after* the file succeeded.** Two optional single-line inputs on the
receipt, each saveable on its own:

- `expected` — `"If you know it: what should the answer have been?"`, 200 chars. **The single
  highest-value field a steward can get**, because it is the only falsifiable claim on the page, and
  it needs no schema knowledge (a number, a name, "about 400, not 40").
- `note` — the existing free text, cap unchanged at `RAISED_NOTE_MAX_CHARS = 4000`, relabelled
  `"Anything else that would help (optional)"`.

**This inversion is the point.** Today the note gates the filing, so an analyst who does not want to
write gives you nothing. Here the filing is already done and the extras are a bonus — the only
arrangement under which they get filled in.

**What the picker deliberately does not do.** It never names a table, column, metric or term. Not
because an analyst could not pick from a dropdown, but because a dropdown of 13,281 assets turns a
two-click action into a search task, and a *wrong* pick is worse than no pick: it sends the steward
to the wrong asset with a confident-looking pointer on it. `term_mismatch` is as close as this UI
gets, and it names a *class* of object, never an instance. Locating the asset is the steward's job
and §15.4 gives them the machinery.

**The receipt copy, verbatim** — and it removes a lie that is in the product today
(`"Filed. It is on the pending list."`, on a list nothing ever clears):

> Filed. A data steward reviews these oldest-first. This engine does not know who you are, so
> nobody will email you — check **My reports** to see what happened.

### 15.3 `/reports`: what the analyst sees afterwards

`GET /observations` filtered by the ids in `localStorage`. **`ui/lib/my-reports.ts` is browser
memory and the page says so** — there is one principal and no user store, so inventing a per-user
notion here would be a boundary that is not one:

> This list is remembered by this browser, not by your account. The engine does not know who you
> are, so a different browser shows a different list.

Each row: the question, when it was filed, the category label, and a status chip whose sentence is
the §5 string for its state. `landed_verified`, `landed_matched` and `retrieval_verified` carry the
**Re-ask** action (§5).

### 15.4 `/review`: the steward's screen, where the money is

A new route with a nav entry between **Pending** and **Settings**. **Not a third pane on `/audit`**,
for `pending-queue.tsx`'s own stated reason applied one turn further: `/audit` is newest-first and
every turn; this is oldest-first and only what somebody complained about, and putting both scroll
directions on one screen makes each worse.

```tsx
// ui/components/review/review-surface.tsx
export function ReviewSurface(): JSX.Element {
  // `?cluster=` in the URL, not useState: a steward's whole job here is handing a decision to
  // somebody else, and "look at this" has to be a link.
  const [cluster, setCluster] = useQueryParam("cluster");
  return (
    <div className="flex h-full min-h-0 flex-col gap-6">
      <ReviewQueue selected={cluster} onSelect={setCluster} />
      {cluster && <ClusterPanel clusterId={cluster} />}
    </div>
  );
}
```

`PageShell` description, permanent, on the page — the product boundary in one sentence:

> Answers and refusals people flagged, grouped by what looks like the same problem. Oldest first.
> Deciding here drafts a change to the semantic layer — it does not apply one.

**The queue.** `GET /observations?state=open,triaged&group=cluster`, clusters with their members
inlined (3–20 short rows; a second fetch per cluster would be a round trip per click for nothing).
Each row: `n` observations · the category label · the schema · the oldest `filed_at` · two or three
table names · a badge with the count of **distinct questions**, which is the number that says
whether this is one person clicking twice or five people hitting one wall.

**Sorted oldest-first on the cluster's oldest member, not by size.** A five-observation cluster from
this morning is not more urgent than one that has waited a month, and sorting by size makes the long
tail permanently invisible.

The caption under the cluster heading is always present, because the clustering is structural:

> Grouped by the kind of problem reported and the tables those turns were allowed to read. Nothing
> here read the questions and decided they mean the same thing — check the rows before you treat
> them as one problem.

**Empty state:** `"Nothing to review. Every observation filed on this server has been triaged."` —
a *different* sentence from `/reports`' empty state, because "nobody filed anything" and "everything
is triaged" are different facts, and reading one as the other is how a queue gets abandoned.

**Deliberately not in the queue:** SQL, ledger, record. All one click away. A queue that shows the
evidence is a queue nobody scans.

### 15.5 The evidence bundle: seven blocks, all above the decision

`ui/components/review/evidence-bundle.tsx`. One fetch per selected cluster.

1. **What was asked, and what came back.** The question verbatim; then `outcome`, and for a
   non-`answered` turn `terminal_reason` and `refused_by` rendered through the existing
   `lib/answer-delivery.ts::terminalLabel` — **so the steward reads the same sentence the analyst
   read**; then `answer_text`.
2. **What the reader said.** Category label, `expected`, `note`. `expected` is styled as a quotation
   and given the most visual weight in the block, because it is the only falsifiable claim on the
   page.
3. **The statement.** `generated_sql` in the existing read-only `<SqlBlock/>`, plus the attempt
   ledger through `<AgentTimeline/>` / `buildStepsFromLedger(execution)` — the same components the
   answer card uses. **On the same screen as the decision, not behind a tab.** A steward who has to
   navigate away to read the SQL decides without it.
4. **What the turn was allowed to read.** `licensed` (the allowlist Layer 6 enforced against) and
   `schemas` (the router's pick), beside `schema_ranking`'s top 5 with scores — because *"the gold
   schema ranked 4th"* and *"it was never a candidate"* are different problems with opposite fixes,
   and the register field exists to tell them apart.
5. **Which corpus assets were in context.** The crux, in three columns, each asset linking into
   `/corpus`:
   - **Found** — one row per `facet_hits` entry with its `asset_type`, the facet that found it, and
     its `lexical`/`semantic` scores.
   - **Reachable** — `pulled_in` (`asset_id → resolve|connect`), merged and marked.
   - **Rendered** — *derived*: found ∪ pulled_in, minus `budget_dropped`, minus
     `evicted.dropped_ids`.

   The caption, which is the honest part and belongs in the panel rather than in a doc:

   > "Rendered" is derived, not recorded. No register field lists the asset ids that were actually
   > in the block the model read — `context_hash` is a digest, and `evicted` names only what the
   > budget dropped. This column is *found, minus what the caps and the budget removed*, which is
   > the same set unless something between retrieval and rendering removed an asset without saying
   > so.

   **The one-field fix, so that caveat is a decision and not a shrug: add `rendered_asset_ids` to
   `RECORD_REGISTER` at `Stage.assemble`.** A `Tier.treatment` field whose consumer is this panel,
   turning a derivation into an observation. It lands **with** this panel and not before —
   `tools/check_declared_is_consumed.py` and
   `test_the_declared_but_unconsumed_set_does_not_grow` are the reason, and they are right.
6. **The reproducer** (§15.6).
7. **The full record**, collapsed, `atLeast(mode, "engineer")` only — the same
   `GET /audit/turns/{id}/trace` payload `/audit`'s `TracePanel` already renders, reused rather than
   re-implemented. If `incomplete_fields > 0` it is **not** collapsed and carries: *"This turn's
   record is missing N required fields. Do not draft a change off it — something about this turn was
   not recorded."*

**What it deliberately does not show: result rows.** `result_table` is live-only by ADR 0006 §11 and
is not in the record, so there is nothing to show, and a slot for it would read as "the rows were
not saved" rather than "the rows are not kept".

**Disclosure, stated because ADR 0012 §8.7 requires it.** This surface reads `turn_log`, which is
not grant-aware, so it discloses exactly what `GET /audit/turns/{id}/trace` already discloses to the
same unauthenticated caller. **It widens nothing, and it narrows nothing either.** The corpus-asset
half of block 5 *is* narrowable and therefore *is* narrowed: those assets are read through
`visible(get_session())`, so a withheld asset is omitted the way it is omitted from
`/corpus/assets`. The asymmetry is the only one available — refusing to show SQL that the route next
door serves would be theatre, and withholding an asset here when `visible()` exists would be a hole.

### 15.6 The reproducer

The steward needs one fact the record cannot give them: *does this still happen?* `cannot_reproduce`
is a decline reason, so it has to be checkable. **It is a button, it costs a model call, and the
button says so.** It starts a **new** conversation (never the complainant's thread) and its result
is recorded on the observation, not on the corpus.

### 15.7 The diff: field-by-field, never a text diff

```tsx
export type FieldEdit = {
  path: string;             // "summary" | "body" | "reliability.note" | "columns[betrieb_id].body"
  before: string | null;    // null on a create
  after: string | null;     // null on a removal
  kind: "scalar" | "block" | "list";
};
export function AssetDiff({ edit, fieldOrder }: {
  edit: AssetEdit;
  /** The engine's declared field order for this type, from `GET /corpus/fields?type=`. */
  fieldOrder: CorpusField[];
}): JSX.Element;
```

**Not a text diff of the YAML, and this is not negotiable** — it follows from M1. `to_mapping`
omits defaults, so `governance` and `reliability` are absent from a file at default and a text diff
shows a spurious *addition* when one is set; and PyYAML reflows at 80 columns, so a text diff of a
one-word `summary` change is a whole-paragraph diff. Field order is read from
`GET /corpus/fields?type=`, so a field added to `corpus/schema.py` appears here with no change to
this component.

- **`scalar`** (`summary`, `reliability.note`) — one line, inline **word-level** diff, additions and
  removals both visible, plus a live character count against the cap. Finding out about a
  251-character summary *after* the export is a wasted round trip.
- **`block`** (`body`) — two panes, **line-level**. Word-level on 8,000 characters is unreadable,
  and `body` is the field that actually reaches the prompt, so it gets the most room on screen.
- **`list`** (`synonyms`, `rules`, `source_refs`) — added and removed items as chips, **never a
  reordered text diff**. YAML sequence order is not semantic for any of these, and rendering a
  reorder as a change trains a reviewer to skim.
- **Unchanged fields are collapsed**, not hidden, behind *"Show the 9 fields this does not change"*
  — a field absent from the diff and a field absent from the asset otherwise look identical.

**Two things this component refuses to render**, and both are refusals rather than gaps:
`governance` in any form (a screen that can propose an exclusion *is* the tool whose absence is the
control — ADR 0015 §8), and any structural change to a table's inline `columns` (§6).

### 15.8 The decision bar

Sticky at the bottom of the detail pane so it is on screen with the evidence at every scroll
position. This is the most important layout decision on the page, and it is why the pane scrolls
internally instead of the page growing.

```tsx
export function DecisionBar({ cluster, patch, blocked }: {
  cluster: ObservationCluster;
  patch: PatchDraft | null;
  /** Non-empty disables Draft/Export and is rendered verbatim: conformance + content + governance. */
  blocked: readonly string[];
}): JSX.Element;
```

Four actions, and the fourth is the one most review tools omit:

- **Draft a change** → the editor for the field set §15.7 allows, then `POST /patches`.
- **Decline** → a `Select` over the eight `decline_reason` members, each rendering its §5 sentence
  **as the option's description**, so the steward reads what the analyst will read before choosing.
  No free-text-only decline: a reason nobody can aggregate is a reason nobody reviews.
- **Fold into another observation** → `duplicate`, and it joins that one's patch set (§5 — otherwise
  landing counts one affected observation instead of two).
- **Escalate.** There is nobody to escalate *to* — one principal, no assignees. So it is not a
  routing action, it is **a state with a name**: `blocked_on_a_person` plus a required one-line
  note. Analyst-facing copy: *"Waiting on a person: <note>. Nobody is chasing this automatically."*
  An assignee dropdown was rejected: there is no user store to populate it, and a dropdown of one is
  a lie about the workflow.

### 15.9 Display mode

The engineer-only blocks (§15.5 block 7, the `schema_ranking` scores, the ladder detail) sit behind
the existing `atLeast(mode, "engineer")` from `ui/lib/display-mode.ts`. Nothing new is invented:
that module already carries the warning about a display mode not being a security boundary, and
this design does not make it one.

---

## 16. What this design does not do

- **It does not authenticate anybody.** One principal, and reaching the port is still sufficient.
  The admin verbs ship unmounted; that is a deployment switch, not an identity.
- **It does not know who filed an observation.** No `filed_by`, because `api/auth.py` returns one
  principal and a per-user field here would be a boundary that is not one. The reports page
  remembers what *this browser* filed, in `localStorage`, labelled as browser memory.
- **It does not claim a landed patch fixed the question.** See §5.
- **It does not scan prose for injection as a gate.** V21 reuses `GUARD_RULES` and V19 covers one
  named disclosure. Beyond those, the posture is ADR 0006's: the name can reach the prompt and the
  query naming it is refused. An enterprise fork has to decide whether that is enough.
- **It does not make this repository the curator.** The pipeline authors candidates; the corpus is
  human-owned, versioned outside this repository, and not rebuildable from it.
