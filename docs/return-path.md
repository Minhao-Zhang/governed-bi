# The return path — working reference

How reader and engineer feedback becomes a corpus change. The binding decision is
[ADR 0015](adr/0015-the-return-path.md); this page describes the code on `design/return-path`.
Where the design and the measurement disagreed, what is written here is what the code does, and
the design that was cut has been deleted from this page rather than marked.

Three surfaces are described below and are **not built**, and each says so where it appears: the
analyst capture UI (§12.2), `/reports` (§12.3), and the re-ask button (§5). One principal holds
every role on this deployment, so a notification loop and a per-reader report list have nobody to
serve — the input is the eval artifact, `tools/import_eval_failures.py`.

Figures marked **measured** were taken against `../MS Fabric Facilities/corpus` and
`../BIRD-corpus` on 2026-08-22/23; every other number is an estimate and says so.

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
the cluster and the detail pane shows the evidence *above* the decision bar (§12): what
was asked and what came back, what Priya said (her `expected` styled as the quotation it is), the
SQL and the attempt ledger in the same components she saw, what the turn was allowed to read with
the router's top-5 ranking, and which corpus assets were in context — with the caveat that the
"rendered" column is derived rather than recorded.

Block 5 is where he sees it: the `term` asset for *active customer* is in context, and its
`summary` says nothing about the `status` column. The engine had no way to know. He runs the
reproduce command the panel gives him — it costs nothing — and it still returns 4,102.

He drafts a change: one field, `term_active_customer.summary`, adding the alias and the rule.
The diff renders that one field word by word, labelled with which field it is, because a change to
`summary` changes what gets found and a change to `body` changes what the model reads. He sets the
three observations to `addressed`.

**Monday, 11:41 — the ladder.** One command, `tools/verify_patch.py`. T0 parses the edited asset
through the production loader. T1 runs whole-tree conformance, `build_structure` and `build_index`
over the tree with the edit substituted in memory, and reports no new finding by rule id. T2
resolves the term's binding against the corpus's own tables and joins. T3 —
`tools/reproduce_observation.py --embed` — replays retrieval with the agent model off: on the three
affected questions the gold tables stay covered and nothing else loses coverage. Total wall clock:
about half a minute. Total spend: **$0** (§10).

Because the patch touches a `summary`, T3 is a real verifier here. Had it touched only a `body`,
T3 could not see it at all — `body` never enters the retrieval index — and the record would say so
rather than report a pass. The field a patch touches decides whether any free tier can check it.

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
turns where every gold table was licensed **and the gold names at least one table** the engine's
measured accuracy is 0.7555 (n=1,145), so about one in
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
  events.py                        the closed vocabularies + Observation / Patch
  validate.py                      faults_with(Observation) / faults_with(Patch) -> list[str]
  lifecycle.py                     TRANSITIONS, PATCH_TRANSITIONS, is_open(), derived_state()
  store.py                         FeedbackStore — the deep module
  cluster.py                       cluster_key(), clusters()

src/governed_bi/corpus/patch.py    # beside store.py: surgical field edits (§6)
src/governed_bi/api/feedback_routes.py

tools/import_eval_failures.py      # an eval artifact's failures -> observations
tools/verify_patch.py              # the free ladder, T0-T2 (§10)
tools/reproduce_observation.py     # T3: does this failure still happen? (§10)
tools/export_bundle.py             # patch -> bundle
tools/check_landed.py              # corpus source_refs -> derived landing states; --verify re-checks
```

**There is no pipeline package.** The agentic triage design — a Diagnoser, an Author, a `triage/`
graph with its own entry point — was cut from this build, and no file of it exists. What a steward
gets instead is the review surface (§12), the free ladder (§10) and their own judgement. There is
also no `attribution.py`: the fields a turn contributes are columns on the observation row (§4), so
a type that carried them separately would be a second place they live.

### Import layering

`tools/check_imports.py::LAYERS` must name every package under `src/governed_bi` — `undeclared()`
fails the run when it does not, and a package the list omits has no constraints at all. One
insertion:

```python
LAYERS = (
    ("paths",), ("credentials",), ("ports",), ("register",), ("measure",),
    ("corpus",),
    ("feedback",),        # <- new: needs register + corpus, nothing above
    ("retrieve",), ("govern",), ("datasource",), ("model",), ("serve",), ("eval",),
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
| It tried and tried and never got there | `attempt_capped` | **neither**, usually. Its own member and not `unverifiable`: "I cannot tell" is a statement about the reader, this one is about the engine |

Operator-only, distinguished by `source` rather than by `kind`:

| `category` | `source` | note |
|---|---|---|
| `column_suspect` | `operator` or `agent` | `Reliability.status` is AI-authorable, so an agent may file it |
| `column_excluded` | `operator` only | `Governance.excluded` is human-only. The store refuses this `category` from any other `source` |
| `reusable_fact` | `operator` | an operator's answer to a clarification, promoted (§9) |

**`source` is a separate column from `category`** because the same observation arrives from three
populations (`reader`, `operator`, `agent`) and the queue sorts them differently. Folding it in
would give thirteen values for ten questions.

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
| a wrong metric expression | **edit**. And it is the one class with a *free* verifier: 107 findings across 85 of 478 expressions do not parse, and 17 name an identifier that resolves nowhere (**measured**) |
| a column that should be `suspect` | **edit** `column.reliability` |
| a column that should be `excluded` | **neither, from the loop's point of view** — it emits a request and a human edits by hand |
| a clarification answer that is a reusable fact | **new** `term` or `few_shot` — or **neither**, if it is a one-off filter |

---

## 4. The store

### Schema

```sql
-- feedback/store.py::_SCHEMA, applied by _migrate(). `PRAGMA journal_mode = WAL` is set outside
-- the transaction, because it is a database-level property and not a change.

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS observation (
  observation_id      TEXT PRIMARY KEY,
  filed_at            TEXT NOT NULL,          -- ISO-8601 UTC, seconds
  source              TEXT NOT NULL,          -- reader | operator | agent
  kind                TEXT NOT NULL,          -- from_refusal | wrong_answer
  category            TEXT,                   -- §3, nullable: the first tap may be all there is
  note                TEXT NOT NULL DEFAULT '',   -- <= 4000 chars, stripped
  state               TEXT NOT NULL,          -- open | triaged | declined | duplicate | addressed
                                              -- | blocked_on_a_person
  decline_reason      TEXT,                   -- required when state = declined; §5
  duplicate_of        TEXT REFERENCES observation(observation_id),
  blocked_note        TEXT NOT NULL DEFAULT '',   -- required by blocked_on_a_person; §5
  triaged_at          TEXT,
  -- attribution, COPIED not joined (see below)
  turn_id             TEXT,
  thread_id           TEXT,
  question            TEXT NOT NULL DEFAULT '',
  outcome             TEXT,
  refused_by          TEXT,
  generated_sql       TEXT,
  licensed_json       TEXT NOT NULL DEFAULT '[]',
  schemas_json        TEXT NOT NULL DEFAULT '[]',
  missing_tables_json TEXT NOT NULL DEFAULT '[]',
  -- the benchmark half of an imported failure. Withheld from an unauthenticated caller by §7.
  gold_sql            TEXT,
  gold_fingerprint    TEXT,
  pred_fingerprint    TEXT,
  quality_flags_json  TEXT NOT NULL DEFAULT '[]',
  corpus_content_hash TEXT,
  prompt_set_hash     TEXT,
  git_sha             TEXT,
  arm                 TEXT,
  question_id         TEXT,
  db_id               TEXT,
  external_key        TEXT UNIQUE             -- an importer re-reading one artifact is idempotent
);
CREATE INDEX IF NOT EXISTS ix_obs_state    ON observation(state, filed_at);
CREATE INDEX IF NOT EXISTS ix_obs_turn     ON observation(turn_id);
CREATE INDEX IF NOT EXISTS ix_obs_category ON observation(category, state);
CREATE INDEX IF NOT EXISTS ix_obs_cluster  ON observation(db_id, category);

CREATE TABLE IF NOT EXISTS patch (
  patch_id                     TEXT PRIMARY KEY,
  created_at                   TEXT NOT NULL,
  author                       TEXT NOT NULL,   -- operator | agent
  intent                       TEXT NOT NULL,   -- new_asset | edit_asset | exclusion_request
                                                -- | engine_defect
  state                        TEXT NOT NULL,   -- draft | exported | withdrawn
  namespace                    TEXT NOT NULL,
  rationale                    TEXT NOT NULL DEFAULT '',
  -- what changes
  asset_type                   TEXT,
  asset_id                     TEXT,            -- null for new_asset until the id is derived
  field_path                   TEXT,            -- "summary" or "body", and nothing else (§6)
  was                          TEXT,            -- read from the live corpus at draft time
  becomes                      TEXT,
  asset_yaml                   TEXT,            -- whole document, new_asset only
  -- what it was verified against
  base_corpus_content_hash     TEXT NOT NULL DEFAULT '',
  expected_corpus_content_hash TEXT,            -- null until the bundle is built
  ladder_json                  TEXT NOT NULL DEFAULT '{}',  -- tier -> GateResult
  withdrawn_reason             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_patch_state ON patch(state, created_at);

CREATE TABLE IF NOT EXISTS observation_patch (
  observation_id TEXT NOT NULL REFERENCES observation(observation_id),
  patch_id       TEXT NOT NULL REFERENCES patch(patch_id),
  PRIMARY KEY (observation_id, patch_id)
);

CREATE TABLE IF NOT EXISTS transition (       -- append-only. The audit trail.
  rowid_     INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  entity     TEXT NOT NULL,                   -- observation | patch
  entity_id  TEXT NOT NULL,
  from_state TEXT,
  to_state   TEXT NOT NULL,
  moved_by   TEXT NOT NULL,                   -- the ACTOR, never empty. §5
  detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_transition_entity ON transition(entity, entity_id, rowid_);
```

**`expected` is not a column.** The filing route accepts it, caps it at 200 characters, and
prepends it to `note` as a line reading `expected: …` (§7). A column of its own was not worth a
migration for one line of the reader's own text, and the review surface reads it out of the note
with the rest.

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
    def move(self, observation_id: str, *, to: ObservationState,
             moved_by: Actor | None = None, detail: str = "",
             decline_reason: DeclineReason | None = None,
             duplicate_of: str | None = None,
             blocked_note: str = "") -> Observation: ...
    def move_patch(self, patch_id: str, *, to: PatchState,
                   moved_by: Actor | None = None, detail: str = "",
                   withdrawn_reason: str = "",
                   expected_corpus_content_hash: str | None = None) -> Patch: ...
    def draft(self, patch: Patch, *, observations: Sequence[str]) -> str: ...
    def amend_note(self, observation_id: str, note: str) -> None: ...
    def record_ladder(self, patch_id: str, tier: str, result: Mapping[str, Any]) -> None: ...

    # reads
    def get(self, observation_id: str) -> Observation | None: ...
    def get_patch(self, patch_id: str) -> Patch | None: ...
    def queue(self, *, states: Sequence[ObservationState] | None = None,
              category: Category | None = None,
              limit: int = 50, offset: int = 0) -> Page: ...
    def patches(self, *, states: Sequence[PatchState] | None = None,
                limit: int = 50, offset: int = 0) -> Page: ...
    def observations_for_turn(self, turn_id: str) -> tuple[Observation, ...]: ...
    def patches_of(self, observation_id: str) -> tuple[Patch, ...]: ...
    def observations_of(self, patch_id: str) -> tuple[Observation, ...]: ...
    def history(self, entity_id: str) -> tuple[dict[str, Any], ...]: ...
    def counts_by(self, column: str) -> dict[str, int]: ...
```

`move` and `move_patch` write the new state **and** its transition row inside one `BEGIN IMMEDIATE`
transaction, guarded by `AND state = ?`, so two stewards deciding the same row at once cannot leave
the audit trail unchained. There is no retention sweep: rows accumulate and nothing deletes them.

`assert_not_a_warehouse` from `paths.py` is applied to the path value, for the reason it exists
there.

### Knobs

```
GOVERNED_BI_FEEDBACK_DB      default runs/feedback.sqlite, resolved against REPO_ROOT
GOVERNED_BI_FEEDBACK_ADMIN   unset -> the steward's four verbs are not mounted at all
```

**Neither of these may become a `register/knobs.py` knob.** `serve/session.py::_resolved_knobs` puts
every declared knob on every serve row and `measure/gates.py::_knobs_resolved_gate` compares them,
so declaring one here moves the config hash of every arm for a value no turn consumes — the
`expand_hops` defect by construction. Pinned by
`tests/feedback/test_the_feedback_store_is_not_a_comparability_knob.py`.

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
licensed **and the gold names at least one table** the engine's measured accuracy is **0.7555**
(n=1,145; **0.7131** over all 1,272 covered turns, the unwinnable 127 included), so about
**one in four** complaints closed
on the strength of a landed commit would still be wrong. `retrieval_verified` is the one upgrade
the free ladder licenses, and it says only that the tables are reachable.

### The re-ask, and why it is not optional

**Not built.** `ui/components/reports/re-ask-button.tsx` does not exist, and neither does the
reports page it would sit on. It is written down because the gap it leaves is real.

Every landing state's copy tells the reader to ask again, and nothing ships a way to. So:
`landed_verified`, `landed_matched` and `retrieval_verified` should carry a **Re-ask** action on the
reports page. It opens the chat surface on a **new** thread, prefilled with the question text
the store already copied off the turn record (§4).

A new thread, not the original. Writing into someone else's thread is what the deleted
`api/raised_write.py` documented at length about not doing, and a second turn on the old thread
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

## 6. Writing YAML: `corpus/patch.py` replaces one field in place

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
# src/governed_bi/corpus/patch.py  — same layer as store.py
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
```

Beside those two: `read_field`, `Span`, and the refusals as exception types —
`FieldNotLocatable`, `StaleValue`, `UnwritableValue`. **There is no create primitive.** `new_asset`
is a declarable patch intent and `asset_yaml` is validated, but no tool exports one:
`export_bundle.py` refuses every intent other than `edit_asset`, because only an edit produces a
diff an engineer can apply. A whole new asset is a hand-written file.

**Two field paths, and no others.** `patch.py::EDITABLE` is `{summary, body}` — deliberately the
same set `feedback/validate.py::EDITABLE_FIELD_PATHS` allows, with an import-time guard that fails
if the two disagree. The reason is `lifecycle.derived_state`: it confirms a landing by comparing
`summary`/`body` text, so a patch to a field it cannot read would land and then read as
`superseded` forever. `reliability`, `binding` and `rules` are hand edits. And four roots can never
be reached whatever a caller asks for: `governance`, `provenance`, `audit`, `columns`.

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
  serve outage, arriving after the commit, past a checker that cannot see it. An edit to a
  column's `summary` goes through `locate`/`apply_edit` on the **table's** file; a new column is a
  warehouse change, not a corpus change.

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
  MANIFEST.yaml        the patch, its observations and question ids, the ladder results, the base hash
  COMMIT_MSG.txt       generated. First line <= 72 chars. Names the observation ids, not the prose
  changes.patch        `git apply -p1`-able, produced against base_corpus_content_hash
  after/               the post-state file, full text, so a reviewer can read the result not the diff
  evidence/
    observations.md    what each reader said, verbatim, in a fenced block
    ladder.json        every tier's GateResult that ran
```

```bash
uv run --frozen python tools/export_bundle.py --patch pat-… --out ./bundles
uv run --frozen python tools/export_bundle.py --patch pat-… --dry-run   # prints the diff, writes nothing
```

`MANIFEST.yaml` deliberately omits `expected_corpus_content_hash`. It is the digest of a tree
nobody has written yet, and a hash-shaped string nobody can compare is worse than an absence;
`tools/check_landed.py` computes it after the commit.

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

**Not built.** `ui/components/clarifications/pending-queue.tsx` carries no link to the review
surface, and `Category.reusable_fact` has no producer. The vocabulary is there and the surface is
not.

`pending-queue.tsx` is read-only by design: answering there would resume a thread this operator was
not the one asked (ADR 0006 B9). That constraint holds unchanged.

What it needs is **a link, not a button.** The link opens the steward surface with a
prefilled `reusable_fact` observation carrying the paused turn's question and the clarification
text. The copy is explicit:

> The paused conversation stays paused, and whoever asked it will not get a reply. What you write
> here becomes a proposed change to the semantic layer, so the next person who asks does not have
> to be asked back.

Nothing calls `command.update`. Nothing calls `POST /threads/{id}/state`. The paused thread is
read, never written.

---

## 10. The verification ladder

Every tier is a **delta gate**. The served corpus already produces 361 `build_structure` problems
(**measured**), so a "zero problems" gate rejects production, gets waived, and a waiver is how a
real finding goes green. What each tier asks is whether *this patch* made things worse.

T0 to T2 are one command and spend nothing:

```bash
uv run --frozen python tools/verify_patch.py --patch pat-…             # through T2
uv run --frozen python tools/verify_patch.py --patch pat-… --tier T0   # the fastest useful answer
```

**Nothing is staged on disk.** `corpus/patch.py::apply_edit` returns the new text and writes
nothing, and the whole-tree checks run over the parsed tree with the one file's mapping substituted.
So there is no copy of a 7,357-file tree per run, and — more importantly — no destination directory
for anything to delete: the ladder never touches `corpus/snapshot.py`, whose `rmtree` was measured
deleting a scratch directory of unrelated files.

| tier | what runs | cost (**measured** where marked) | pass condition |
|---|---|---|---|
| **T0** | the edited asset, alone | ~1.6 s | the file parses, `from_mapping` accepts it, `problems_with` is empty, the id validates |
| **T1** | conformance whole-tree + `build_structure` + `build_index` | 3.4 s (facilities) / 26 s (BIRD) **measured**; index 0.03 s lexical, 0.27 s warm semantic **measured** | no **new** finding by rule id; `build_index` does not raise; `build_structure` problem count does not rise |
| **T2** | the metric-expression resolver over the patched tree | offline, free, no database | every bare identifier in a metric `expression` resolves on `base_table` or through a declared join |
| **T3** | `tools/reproduce_observation.py --embed`, agent model off | minutes, **~$0** — the vector cache is 100% warm and one new asset costs **2** embed calls **measured** | **per question, not per rate**: no question loses gold-table coverage. Report the questions that gained. **Not applicable to a `body`-only patch** — see below |

**T2 needs no live catalog, and that is a correction to the design.** ADR 0015 put the resolver
behind a database, on the grounds that resolving an identifier needs the warehouse. It does not: the
corpus declares its own tables, columns and joins, and *those* are what an expression must be
consistent with — the warehouse is `govern/`'s business at serve time. `check_closed_domains.py` was
the design's name for this tier and no such file exists.

**T3 must be run with `--embed`.** Without it the check runs lexical-only, and the arms were
measured with an embedder. Driving one observation both ways: the row recorded **1** missing gold
table and the lexical re-check reported **2** — a false "still reproduces" that reads exactly like a
real finding. The channel is named in every run's output and the lexical one warns.

**T3 answers a narrower question than the others,** and its output says which every time: the tables
the reference answer reads are reachable again. Not that the answer is right. On turns where every
gold table *was* licensed and the gold names at least one table, measured accuracy is 0.7555
(n=1,145).

**There is no tier above T3.** A targeted paid replay of a cluster's questions and a paired arm are
both things a person launches by deciding to spend money, and neither is built. So a patch touching
only a `body` has no verifier at all: `body` does not enter the retrieval index, T3 cannot see the
change, and the record names which of the three reasons applied rather than reporting a pass.

### The readout, per category

EX is not on this list. `docs/open-work.md` §3.12 gives the reason: MDE ≈ 2.3pp, and §1.5's largest
single coverage bucket is 7 questions — 0.52pp.

| category class | primary readout | tier | resolution |
|---|---|---|---|
| `false_refusal` | the turn's `terminal_reason` stops being `r_table_not_licensed`, and coverage becomes true | T3 | one question |
| `wrong_scope` (coverage) | `all_gold_tables_licensed` per question; `pulled_in.n_connect` | T3 | one question |
| wrong table inside the licensed set | the `licensed` diff, and which gold tables were missing | T3 | exact |
| `wrong_value` (definition) | the metric resolver passes | T2 | exact |
| prose that reaches the prompt | the new content rules | T0/T1 | exact |

Every zero in that table is reported through `measure/stats.py::rule_of_three`, so `0/53` renders
as "≤ 5.7%" and cannot be quoted as "0% false refusals". That function already exists.

**What no tier reads is the answer.** A complaint whose gold tables were all licensed and whose
answer was still wrong is a semantics defect, and the free ladder cannot see it. The panel says so
in that case rather than reporting a pass.

### New conformance rules

Ids continue `tools/check_corpus_conformance.py`'s `RULES` table. Three of the five have a
**non-empty population measured today**, which is what separates them from rules written on a
hunch.

| rule | predicate | live findings |
|---|---|---|
| **V17a** | a metric `expression` parses as SQL at the engine's dialect | **107 across 85 of 478 metrics** on BIRD: `DIVIDE(…)`, `COUNT(x WHERE y)`, `<condition>` |
| **V17b** | every bare identifier in a metric `expression` resolves on `base_table`, or on a table reachable through a declared join — and then the join must be declared | **17** |
| **V19** | no model-visible **`body`** names a `governance.excluded` column or asset. **`body`, not `summary`** — `summary` never enters the prompt (`serve/context.py`), it enters the retrieval index | **zero**, because zero assets are excluded in either corpus. Free to add; cannot regress anything |
| **V21** | model-visible text passes `govern/guard.py::GUARD_RULES` — reusing them, not restating them | **one**: `public_review_platform/few-shots/fs_public_review_platform_0012.yaml` ships two `U+200B` |
| **V23** | asset ids are unique across the tree | **zero today**, and the rule exists because a duplicate passes conformance and then raises `ValueError: duplicate index id` in `build_index` (**measured**) |

The design's count for V17a was **28**, from a parse-only prototype: `DIVIDE(a, b)` parses as SQL
and names a function no dialect has, so the shipped rule also asks
`govern/functions.py::PERMITTED_FUNCTIONS`. A sixth rule was designed and cut — a closed-domain
claim carrying an observation in `audit.evidence` — because it had no live population and no
calibrated false-positive rate, and would have shipped as a rule nobody could size.

**V10 and V12 are not disclosure rules and must not be cited as the existing control.** V10 is "no
text discloses how an unreliable column was made" — it exists for the BIRD obfuscation decoys —
and V12 is held-out-question leakage. Both police benchmark integrity. On a production corpus they
police nothing, so V19 is the *first* control of its kind, not a reinforcement.

**The ratchet.** Pre-existing findings are pinned **by name** in the corpus repository. The set may
shrink freely and may not grow, and closing one fails the build as loudly as adding one — names
and not a count, because 28 findings and 28 *different* findings are the same integer. **Measured
on `../BIRD-corpus`:** 125 findings, which collapse to **101** pinned identities of the form
`(rule, file:asset)` — 24 of the findings share an identity with another.

### Comparability

Two blockers, both **measured**:

1. `comparability_keys()` is 50 names and **not one contains "corpus"**, so an arm whose treatment
   is the corpus cannot declare it and `register/arm_profiles.py` makes it `cannot_evaluate`.
2. `corpus_content_hash('../BIRD-corpus')` at HEAD is `6e5c7b4be83d5682…`; `arms.toml` declares
   `86ed1dbf…` on all four arms. The two commits in between add only `LICENSE` and `README.md` —
   no asset changed — and the digest moved anyway. **`--arm v4` against the checked-out tip is
   refused today.**

So: a comparability knob `corpus_release`, naming a **tag** and not a directory. Patches land
continuously; arms pin releases. Plus `hypothesised_effect` and `readout` on `ArmProfile`, which
gives `eval/power.py::require_power` the caller `open-work.md` §3.10 records it as lacking — at
which point an arm that cannot detect its own hypothesis fails before it spends anything.

**But do not plan a release around a paired arm.** What bounds the cadence is the stock of
detectable effect, and it is nearly spent. Everything T3 can see is the coverage debt — 79
questions whose gold tables were never licensed — worth at most +5.85pp, which at the measured EX
scales to +3.98pp against an EX MDE of 2.33pp: **1.7 detectable releases in the entire debt.** And
each one needs *two* new arms, not one, because no pair on disk reaches `knobs_comparable`
(§Comparability blocker 1 above is why), so the first release has to buy its own control: ~150M
input tokens, ~104 minutes.

Therefore the **release headline is the T3 per-question coverage delta** — resolution one question
(0.08pp), cost ~$0 — and a paired arm is what you buy when a *code* change needs pricing.
`ArmProfile.hypothesised_effect` exists partly to make that refusal automatic: a release arm
declaring a +0.5pp hypothesis fails `require_power` before it spends anything.

**Only one of these declarations is actually caught by CI.** `tools/check_declared_is_consumed.py`
has four rules, over knobs, record fields and state channels. `corpus_release` is a knob, so a
missing reader fails the build by name. `ArmProfile.hypothesised_effect`, `ArmProfile.readout` and
the store's SQLite columns live in namespaces none of the four rules walk — so for those, "declared
with no reader" is held by review and not by CI. Closing it is one more rule of the same shape;
until then this paragraph is the control.

---

## 11. CI

### Engine repository — `.github/workflows/ci.yml`, `test` job

```bash
uv run --frozen python tools/check_imports.py    # LAYERS names feedback
uv run --frozen pytest -q -rs                    # tests/feedback and tests/corpus among them
```

Nothing on the return path has a CI step of its own, and it does not need one.
`check_imports.py::undeclared` fails on a package `LAYERS` omits, which covers the new layer; the
rest is `pytest` over the whole suite, which is the only caller of several `tools/` checks and so
fails loudly when one of them breaks.

### Corpus repository

This is the CI the engineer's commit passes through, and it is specified here because the engine
is where the checker lives. It must **not** need a model credential or a database.

```bash
uv run --frozen python ../governed-bi/tools/check_corpus_conformance.py --corpus-dir .
uv run --frozen python ../governed-bi/tools/check_ratchet.py --pins .conformance/pins.txt
uv run --frozen python -c "from governed_bi.retrieve import build_index; ..."   # T1: it must start
```

### What runs in neither

T3, and anything a person would pay for. T3 needs an observation carrying a gold statement — a row
in an operator's store, not a fixture in this repository — and a warm vector cache. It is free to
run and it is run by hand.

---

## 12. The surfaces

One screen that shipped, two that did not, and one module that owns every string.

### 12.1 New and changed files

| path | what |
|---|---|
| `ui/app/review/page.tsx` | the steward's route |
| `ui/components/review/review-surface.tsx` | the two-pane shell |
| `ui/components/review/review-queue.tsx` | the queue (§12.4) |
| `ui/components/review/cluster-panel.tsx` | the detail pane |
| `ui/components/review/evidence-bundle.tsx` | the evidence (§12.5) |
| `ui/components/review/reproduce-panel.tsx` | block 6 (§12.6) |
| `ui/components/review/asset-diff.tsx`, `ui/lib/asset-diff.ts` | the one-field diff and the word diff behind it (§12.7) |
| `ui/components/review/decision-bar.tsx` | the four actions (§12.8) |
| `ui/components/review/handoff-panel.tsx` | the bundle command and its manifest, post-export |
| `ui/lib/review-copy.ts` | **every** user-facing string in §3, §5 and §12 |
| `ui/lib/schemas.ts`, `types.ts`, `api-client.ts`, `hooks/queries.ts` | the zod schemas, the `z.infer` types, the client methods and the hooks |
| `ui/scripts/check-asset-diff.ts` | the diff's minimality, hermetically — `npm run check:asset-diff` |
| `ui/components/layout/nav.tsx` | one `LINKS` entry, **Review** |

**Not built.** Every path here is absent from the tree: `ui/app/reports/page.tsx`,
`ui/components/answer/category-picker.tsx`, `ui/components/reports/report-list.tsx`,
`report-status.tsx`, `re-ask-button.tsx`, `ui/lib/category-taxonomy.ts`, `ui/lib/my-reports.ts`,
`ui/scripts/check-review-copy.ts`. `ui/components/answer/raise-note.tsx` exists and was not
rewritten.

**`ui/lib/review-copy.ts` is where the honest-copy rule would be made mechanical, and it is only
half made.** Every string lives there, keyed by state, which is what makes the rule checkable at
all — but the check is not written. `ui/scripts/check-review-copy.ts` does not exist, so nothing
asserts that every member of the observation / patch / decline state unions has a string, and
nothing bans `robust`, `seamless`, `comprehensive`, or the two this project cares about most,
**`automatically`** and **`will be fixed`** outside a negation. The module makes the check possible;
review is what enforces it today.

### 12.2 The analyst: capture in two clicks

**Not built.** `raise-note.tsx` still files a note through a textarea, and none of the three states
below exist. It is written down because the input this build actually uses — an eval artifact — has
no analyst in it at all, and the shape of the one it would need is the thing most likely to be got
wrong later.

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
and §12.4 gives them the machinery.

**The receipt copy, verbatim** — and it removes a lie that is in the product today
(`"Filed. It is on the pending list."`, on a list nothing ever clears):

> Filed. A data steward reviews these oldest-first. This engine does not know who you are, so
> nobody will email you — check **My reports** to see what happened.

### 12.3 `/reports`: what the analyst sees afterwards

**Not built.** There is no `/reports` route. With one principal there is no second reader for a
per-reader list to serve.

`GET /observations` filtered by the ids in `localStorage`. **`ui/lib/my-reports.ts` is browser
memory and the page says so** — there is one principal and no user store, so inventing a per-user
notion here would be a boundary that is not one:

> This list is remembered by this browser, not by your account. The engine does not know who you
> are, so a different browser shows a different list.

Each row: the question, when it was filed, the category label, and a status chip whose sentence is
the §5 string for its state. `landed_verified`, `landed_matched` and `retrieval_verified` carry the
**Re-ask** action (§5).

### 12.4 `/review`: the steward's screen, where the money is

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

**Sorted oldest-first on the cluster's oldest member, not by size.** A three-row cluster from this
morning is not more urgent than one row that has waited a month, and sorting by size makes the long
tail permanently invisible.

The caption under the cluster heading is always present, because the clustering is structural —
the key is `(category, schema)` and nothing more, no embedding, no model, no cost:

> Grouped by the kind of problem reported and the tables those turns were allowed to read. Nothing
> here read the questions and decided they mean the same thing — check the rows before you treat
> them as one problem.

**And the measured weakness is under it.** On the imported failures, the largest cluster is **3**
and **49%** of rows are in a cluster at all. The design argued for batching on the strength of
clusters being large; they are not, so this is a list with an optional grouping and never a batch
pipeline.

**Empty state:** `"Nothing to review. Every observation filed on this server has been triaged."` —
a *different* sentence from "nobody has filed anything", because that and "everything is triaged"
are different facts, and reading one as the other is how a queue gets abandoned.

**Deliberately not in the queue:** SQL, ledger, record. All one click away. A queue that shows the
evidence is a queue nobody scans.

### 12.5 The evidence bundle: six blocks, all above the decision

`ui/components/review/evidence-bundle.tsx`. One fetch per selected cluster. **The design specified
seven blocks and six shipped**, because an evaluation artifact does not record what two of them
would show.

Above everything, when the row carries a held-out question: a warning card, not a caption. The
question text comes from the held-out split, and a person who writes corpus prose from it
contaminates the benchmark invisibly. Conformance rule V12 catches a verbatim quote; a paraphrase
cannot be detected at all, so the last line of defence is a reader who knows what they are reading.

1. **What was asked, and what came back.** The question verbatim; `outcome` and `refused_by`; the
   state's label and its §5 sentence; the decline reason or the blocked note when there is one.
2. **What the grader said.** The category, the quality flags, the note — and the reference and
   produced fingerprints side by side. This is the design's "what the reader said" block, replaced
   by something falsifiable: an imported row has no reader, and a fingerprint mismatch is not an
   opinion.
3. **The statement.** `generated_sql` in the existing read-only `<SqlBlock/>`, and the **reference
   statement beside it** when the row carries one. A reader has no gold answer; a benchmark row
   does, which makes this the strongest evidence on the page. A turn that ran no statement says so
   — that is its own defect class, not a missing field.
4. **What the reference answer needed and did not get.** `missing_tables`, which is the direct
   statement; `schema_ranking` is absent from the artifact, so the design's *"the gold schema ranked
   4th"* versus *"it was never a candidate"* cannot be told apart here. `licensed` and the routed
   schemas sit behind `atLeast(mode, "engineer")`. An empty list is the interesting case and it says
   so: every table the reference answer reads was reachable and the answer was still wrong, which is
   a semantics problem the free ladder cannot see.
5. **Which corpus assets were in context** — the block that cannot exist here, saying so. On the v4
   arm, `facet_hits`, `pulled_in` and `turn_id` are on **0 of 1,351** rows (**measured**), so the
   slot carries one sentence instead of a table. A block rendered empty reads as "we did not bother"
   rather than "there is no data", which is why the slot stays.
6. **The reproducer** (§12.6).

Engineer-only, at the foot: **provenance** — the arm, the question id, the corpus content hash and
the filing time. The design's seventh block, the full `GET /audit/turns/{id}/trace` payload, is
absent for block 5's reason: an imported row has no `turn_id`, so there is no trace to fetch.

**What it deliberately does not show: result rows.** `result_table` is live-only by ADR 0006 §11 and
is not in the record, so there is nothing to show, and a slot for it would read as "the rows were
not saved" rather than "the rows are not kept".

**Disclosure.** This surface reads the feedback store and nothing else, so what it can disclose is
exactly what §7's allowlists let through, and the steward's wider view is the same
`GOVERNED_BI_FEEDBACK_ADMIN` switch that mounts the steward's verbs. There is no per-grant
narrowing on this screen. The design had one — corpus assets read through `visible(get_session())`
in block 5 — and block 5 does not exist, so neither does the narrowing.

### 12.6 The reproducer

The steward needs one fact the store cannot give them: *does this still happen?* `cannot_reproduce`
is a decline reason, so it has to be checkable.

**It is a command, not a button, and that is the honest shape.** The check re-routes the question
through the engine with the agent model off, which needs a warehouse connection and a warm vector
cache — neither of which the browser has and both of which the server would have to be configured
for. There is deliberately no HTTP verb: a button that 404'd on most deployments is worse than a
line somebody can copy. `--embed` is in the copied command and is not optional (§10).

**It costs nothing**, which is a correction to the design: for an imported failure, "does this still
happen" is a coverage re-check with the answering model off, not a model call.

What a green result licenses is on the panel permanently — the tables the reference answer reads are
reachable again, not that the answer is right — and the three cases where the check cannot answer at
all are named rather than hidden, because a panel offering a command that cannot answer is how
somebody concludes the tool is broken.

### 12.7 The diff: one field, word by word, never a text diff

```tsx
export function AssetDiff({ assetId, fieldPath, was, becomes }: {
  assetId: string;
  /** "summary" or "body" — the only two paths a patch can carry (§6). */
  fieldPath: string;
  was: string;
  becomes: string;
}): React.JSX.Element;
```

**Not a text diff of the YAML, and this is not negotiable** — it follows from M1. `to_mapping`
omits defaults, so `governance` and `reliability` are absent from a file at default and a text diff
shows a spurious *addition* when one is set; and PyYAML reflows at 80 columns, so a text diff of a
one-word `summary` change is a whole-paragraph diff. A patch carries one field path and two strings,
so the diff is over those and nothing else.

**Which field it is is on the row, because the two fields do different things.** `summary` feeds the
retrieval index and `body` feeds the model's prompt — a change to `summary` changes *what gets
found*, a change to `body` changes *what the model reads*. A reviewer deciding whether an edit fixes
a coverage miss has to know which one they are looking at, and a diff showing only the words would
leave them guessing.

**Colour is not the only signal.** Every run carries a `+`/`−` marker as well. A red/green diff is
unreadable to a colour-blind reviewer, and this is the screen where the decision is made.

**"+0 −0 words" is two situations and gets two sentences.** The replacement can be the text already
there, or it can differ only in whitespace. Both count zero words; only the second is a value the
steward typed and cannot submit, and `classifyEdit` names which.

The property pinned by `ui/scripts/check-asset-diff.ts` is **minimality**, not "it produced spans".
A greedy walk that marks a whole sentence changed when one word moved still renders, still looks
like a diff, and quietly costs the reviewer the ability to see the edit. The check is hermetic — it
imports `lib/asset-diff.ts` and needs no engine, no corpus and no network.

**Two things this component cannot render**, and both are refusals rather than gaps: `governance` in
any form (a screen that can propose an exclusion *is* the tool whose absence is the control — ADR
0015 §8), and any structural change to a table's inline `columns` (§6). Neither is an editable field
path, so neither can reach it.

### 12.8 The decision bar

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

- **Draft a change** → the editor for the field set §12.7 allows, then `POST /patches`.
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

### 12.9 Display mode

The engineer-only parts — §12.5's provenance block and its `licensed`/routed line — sit behind the
existing `atLeast(mode, "engineer")` from `ui/lib/display-mode.ts`. Nothing new is invented: that
module already carries the warning about a display mode not being a security boundary, and this
does not make it one.

---

## 13. What the return path does not do

- **It does not authenticate anybody.** One principal, and reaching the port is still sufficient.
  The admin verbs ship unmounted; that is a deployment switch, not an identity.
- **It does not know who filed an observation.** No `filed_by`, because `api/auth.py` returns one
  principal and a per-user field here would be a boundary that is not one. Nothing tells a reader
  what became of their complaint, because there is no reader-facing surface at all (§12.3).
- **It does not claim a landed patch fixed the question.** See §5.
- **It does not scan prose for injection as a gate.** V21 reuses `GUARD_RULES` and V19 covers one
  named disclosure. Beyond those, the posture is ADR 0006's: the name can reach the prompt and the
  query naming it is refused. An enterprise fork has to decide whether that is enough.
- **It does not author a candidate change by itself.** A steward drafts the patch and the ladder
  checks it; nothing in this repository decides what a corpus should say. The agentic pipeline that
  would have was cut (§2).
- **It does not make this repository the curator.** The corpus is human-owned, versioned outside
  this repository, and not rebuildable from it. The one write to corpus content in the whole loop is
  a person's `git commit` in that repository (§8).
