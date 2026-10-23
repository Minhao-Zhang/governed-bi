# Plan: pages around the chat, and Ossie as the storage format

Status: proposed, 2026-10-23; the owner answered section 7 the same day. Written
against `main` at `491f6c9`, after the [pivot milestones](2026-10-pivot-milestones.md) P0 to P17.

The owner wants two decisions changed:

1. **More than one page.** Besides chat, the owner wants every past conversation with how it
   ended, the SQL behind each answer with the errors on the way, and the semantic layer in the
   Ossie viewer. This
   reverses part of [ADR 0019](../adr/0019-one-plain-chat-page.md) and the "nothing around it"
   part of [0017](../adr/0017-a-served-chat-app-over-a-layer-the-user-brings.md).
2. **Ossie on disk.** The semantic layer should be stored as Ossie, not as per-asset YAML with an
   Ossie export. This reverses §1 of [ADR 0020](../adr/0020-ossie-export-and-a-separate-viewer.md)
   and brings back the core of the superseded [Ossie plan](2026-10-ossie-plan.md).

Both can be done. They touch different code and can run side by side.

## 1. Where things stand

### Branches and worktrees

| Where | What | What to do with it |
|---|---|---|
| `main` (`491f6c9`) | The pivot is finished: one chat page, the Ossie exporter, the P16 baseline | Base for this plan |
| `claude/old-ui-c31279`, worktree `.claude/worktrees/old-ui-c31279` | The old UI merged with `main`, cut to chat and a history page; 7 commits ahead, 0 behind | Take `lib/sql-format.ts` and `sql-block.tsx` from it, then delete the branch and the worktree |
| `../ossie` (`d85e9e1`) | Spec `0.2.0.dev0`, still a draft. `fae8f41` (2026-10-21) added dataset-scoped metrics | The spec we write against |
| `../ossie-viewer` (`c384341`) | At V3. Reads 0.1.1 and 0.2.0.dev0. V4 is not met: its BIRD test reads a stale export at `53418ba` and opens one file | Test it on the converted corpus (S5) |
| `../BIRD-corpus` (`8ba312ed`) | 7,357 YAML files, 13,304 assets, no tooling of its own | Converted in S3 |

A stale export from an earlier session sits in `%TEMP%/ossie-viewer-bird/`. `runs/p15/bird`,
which the viewer's test looks for first, does not exist.

### The old UI is not a shortcut

The old pages called `/corpus/*`, `/schema/*`, `/graph`, `/knowledge-graph` and `/audit/*`. P4
deleted all of them, and ADR 0009 is now a reversal record. The old history page shows the
latest question and a turn count, not how a conversation ended. Bringing it back would mean
bringing back the browse API. The new pages are built on `main` from what it already stores.

### What the server already keeps

Each conversation's checkpoint in `runs/conversations.sqlite` holds `ServeState.turns`, a list
of `TurnEntry` (`serve/state.py:145`). Each entry has the question, `answer_text`, `outcome`,
`assumptions`, the turn record and, for the newest 10 turns, `result_table` (20 rows). The record
has `generated_sql` and `execution.attempts[]`, and each attempt has `path` (`agent`, `sample`,
`profile`), `passed`, `reason_code`, `checked_sql` and `executed_sql` (`govern/ledger.py:37`).

The page reads that list only for the open conversation. No route lists outcomes across
conversations, lists SQL across turns, or lists tables. The custom routes are `/livez`,
`/readyz`, `/capabilities`, `/clarifications/pending` and `/examples`.

Limits that matter for a log: a thread keeps its last 25 turns (older ones are counted in
`elided_turns`), older records are compacted to about 8 KB, and the evaluation writes to
`runs/harness-checkpoints.sqlite`, not to the conversation store.

### Where the format is read

Past the loader, the engine reads only the dataclasses in `corpus/schema.py`. The on-disk format
is known to:

- `corpus/store.py`: `load` walks files; `write` writes `<root>/<namespace>/<id>.yaml`.
- `corpus/identity.py`: `namespace_of` takes the first directory as the schema, so a file at the
  root would be skipped whenever `schemas=` is set.
- `corpus/hash.py`: hashes relative paths and file bytes, so any new layout changes
  `corpus_content_hash`. That hash is a field of its own on each evaluation row. It is not part of
  `config_hash`, which hashes the settings only (`registry/settings.py:237`), and
  `comparison_quotable` (`eval/report.py:487`) compares two runs by their context hashes. A new
  layout that gives the same contexts can still be compared with the P16 baseline.
- `corpus/snapshot.py`: looks for `.yaml` and `.yml` files.
- `serve/session.py:328`: seeding from a live schema calls `store.write` per asset.
- `tools/check_train_only.py` and `tools/check_no_benchmark_discriminators.py`, which build or
  scan YAML paths.

The vector cache is keyed by model, dimensions and summary text, not by file, so it survives.

### What the exporter loses today

Exporting BIRD and reading it back loses four things, pinned at
`tests/corpus/test_ossie_export.py:153`:

| Lost | Count | Why |
|---|---|---|
| `column.physical_type` | 5,947 | Listed as native (`ossie_export.py:89`) but never written. The model sees it through `inspect_schema` (`serve/fetch.py:114`), so this loss would change answers |
| `few_shot.body` | 4,857 | Dropped. All 4,857 are exactly `Question: {summary}\nSQL:\n{sql}`, so it can be rebuilt |
| `schema.summary`, `schema.body` | 1 each | The `books` summary ends in a newline; joined with a blank line, it splits one character late |

Also not lossless, though the test does not count them:

- Rules are joined with newlines, so a rule containing a newline comes back as two.
- A join's `on` text is rebuilt from column pairs, not kept. On BIRD the rebuilt text equals the
  stored text for all 706.
- Negative examples with no schema, and terms with an unknown binding and no schema prefix, have
  nowhere to go. BIRD has none of either.
- Metrics are written at model level with unqualified expressions (`AVG(milisegundos)`). The spec
  wants `dataset.field` there; `validate.py` does not check it, so they pass anyway. The
  dataset-scoped metrics added in `fae8f41` fit BIRD's base table and unqualified expression.

What Ossie cannot express at all: many-to-many joins (112 in BIRD), joins that are not column
equalities, and cardinality.

An independent assessment, written from a facts-only [brief](2026-10-ossie-storage-brief.md)
without this plan, is the [record](2026-10-ossie-storage-assessment.md) behind sections 3 and
Track S. It prototyped the format below on all of BIRD: 57 files, all 13,304 assets read back
equal after one fix, all 57 valid, 196k lines and 8.0 MB against 208k and 8.6 MB today. This plan
follows it except where section 3 says otherwise.

## 2. The words

The terms drift in three places, and the Ossie change makes a fourth:

| Today, used for one thing | Proposed |
|---|---|
| corpus, the layer, the semantic layer | **semantic layer** in prose and on the page; **corpus** only for the directory (`GOVERNED_BI_CORPUS_DIR`) |
| thread, conversation | **conversation** everywhere except LangGraph's API, where it stays `thread` |
| turn record, turn entry | **turn record** for what `finish` writes; **turn** for the entry that holds it, its rows and its answer |
| asset, table, column, join, few-shot | Ossie's words (**model**, **dataset**, **field**, **relationship**, **metric**) when talking about the file; the engine's words (table, column, join, few-shot) for the engine. The code is not renamed: its names reach the prompt, and its types are not Ossie's (a join can be many-to-many; a column is physical, a field may be computed) |

Each accepted row goes into `docs/glossary.md`. Rows that replace a word go into
`docs/plain-names.toml`, so `tools/check_plain_names.py` keeps the old word out.

## 3. Where each field goes

One Ossie `0.2.0.dev0` document per database, at `<root>/<schema>/<schema>.yaml`, whose `name`
equals the directory. Keeping one directory per schema means `identity.namespace_of`, the
`schemas=` filter and `corpus/hash.py` do not change. When 0.2.0 is released, the files move to it
as "Absorbing a spec change" in Track S describes.

### The rule

The owner decided on 2026-10-23 that the file uses only what the spec defines, and that
everything else goes in the JSON string of our extension.

1. Use an Ossie field when one means the same thing.
2. In `ai_context`, use only the keys the spec defines. Only `instructions` is used: for rules,
   and for a relationship's text, which has no `description`. `synonyms` and `examples` stay
   empty, because no other tool is read or served for now.
3. Leave out what the position in the file already says: the type, the schema, parent ids, and
   ids the reader can derive.
4. Everything else goes in the entity's `GOVERNED_BI` extension, under the engine's own field
   names, `audit` and `governance` included. Records Ossie has no entity for (terms, few-shots,
   joins that are not relationships) are lists in that extension: terms in the extension of the
   entity they bind to, few-shots and the other joins in the model's.
5. Delete what nothing reads.

This departs from the assessment, which put those fields in extra `ai_context` keys as plain
YAML. The gain is that the files depend on nothing the spec does not define, so a 0.2.0 that
closes `ai_context` changes nothing. The cost is readability: extension data is JSON inside a
string. Written as below, one changed key is still one changed line, but a few-shot's SQL is one
long line with every `"` escaped.

### The table

Counts are from BIRD. "Derived" means the reader rebuilds the value and a test shows it matches.
"Extension" means the `GOVERNED_BI` extension of the same entity.

| Asset | Field | Goes to | Why, where not obvious |
|---|---|---|---|
| all | `asset_type` | position | |
| all | `governance`, `audit` | extension; for list items, inside the item | `check_train_only.py` reads `audit` |
| all | `confidence` | deleted | 22 values; only range-checked in `corpus/validate.py`, nothing reads it |
| Schema | `id` | derived: model `name` | 57 of 57 equal |
| Schema | `name` | model `name` | |
| Schema | `summary`, `body` | model `description`: summary, a blank line, body | a summary may not contain a newline; 1 fix (`books`) |
| Schema | `rules` | model `ai_context.instructions`, one rule per line | a rule may not contain a newline; 0 do |
| Table | `id` | derived: `table_id(model, dataset name)` | 656 of 656 |
| Table | `schema` | position | |
| Table | `physical_name` | dataset `name`; `source` is `<schema>.<name>`, checked on load | |
| Table | `summary`, `body` | dataset `description`, joined the same way | |
| Table | `rules` | dataset `ai_context.instructions` | 4 tables |
| Table | `grain`, `row_count` | extension | 0 in BIRD; the prompt shows them when set |
| Table | `columns` | derived: the order of `fields` | |
| Column | `id`, `schema`, `parent_table` | derived from position | 5,947 of 5,947 |
| Column | `physical_name` | field `name`; `expression` is the identifier, quoted only when needed | 1 is quoted |
| Column | `summary`, `body` | field `description`, joined the same way | |
| Column | `logical_type` | field `datatype` | `Float` and `DateTimeTz` are chosen from `physical_type` and read back as `decimal` and `datetime` |
| Column | `physical_type`, `nullable` | extension | `inspect_schema` shows both |
| Column | `role` | `primary_key` sets the dataset's `primary_key`; other roles go to the extension | 0 in BIRD |
| Column | `reliability` (`status`, `note`) | extension | 2,285 suspect; the `COLUMNS` check reads it |
| Column | `is_unique`, `sample_values`, `references` | deleted | 0 in BIRD, no reader |
| Join, 592 | `id` | relationship `name`, stored, not recomputed | all 706 ids match `join_id()` today |
| Join | `left_table`, `right_table` | `from`, `to`; swapped for one_to_many, so `from` is always the many side | |
| Join | `on` | `from_columns`, `to_columns`; the text is rebuilt on load | the rebuilt text equals the stored text for 706 of 706 |
| Join | `cardinality` | none for many_to_one; extension for one_to_many (327) and one_to_one (55) | the reader swaps one_to_many back, so the prompt keeps the authored direction |
| Join | `summary`, `body` | relationship `ai_context.instructions`, joined the same way | a relationship has no `description` |
| Join | `cost` | deleted | 0 in BIRD, no reader |
| Join, 114 others | the whole join | an item in the model extension's `joins` | 112 many_to_many and 2 without cardinality. Never a relationship, because other tools would join it as many-to-one |
| Metric | `id` | metric `name` | |
| Metric | `name` (display), `dimensions` | extension | the id follows the name for only 336 of 478; 15 have dimensions |
| Metric | `base_table` | position: that dataset's `metrics` (451); otherwise model `metrics` with `base_table` in the extension (27) | the 27 fail the dataset-scope check |
| Metric | `expression` | `expression`, dialect `ANSI_SQL` | Ossie has no Postgres dialect |
| Metric | `summary`, `body` | `description`, joined the same way | |
| Term, 603 | the whole term but `binding` | an item in the bound entity's extension, `terms` | |
| Term | `binding` | position: field (576), dataset (26), metric (0), model (1) | a term bound to anything else keeps its binding in the model extension's `terms`; an unbound term belongs to no schema, so writing it fails; 0 in BIRD |
| Term | `related_terms` | deleted | 0, no reader |
| Few-shot, 4,857 | `id`, `summary`, `sql`, `audit`, `governance` | an item in the model extension's `few_shots` | |
| Few-shot | `schema` | position | |
| Few-shot | `body` | deleted; rebuilt as `Question: {summary}\nSQL:\n{sql}` | matches 4,857 of 4,857 |
| Few-shot | `bound_terms`, `complexity` | deleted | 0, no reader |
| Negative example | the type | deleted | 0 in BIRD and no reader |

"Joined the same way" means summary, a blank line, then body, with the load check that a summary
has no newline. Term summaries keep their own key inside the extension, since 6 end in a newline.

### Writing the file

- **Canonical.** Ossie keys in spec order. A string with a newline is a literal block (`|`, `|-`,
  `|+`), so trailing newlines survive. No line folding. A test writes, reads and writes again and
  requires the same bytes.
- **Extensions.** At most one `GOVERNED_BI` entry per entity, only when non-empty. `data` is JSON
  with a two-space indent, sorted keys and unescaped UTF-8, in a YAML literal block, so a change
  to one key is one changed line. The model's extension, which holds the few-shots, is the last
  key of the file.
- **Unknown keys.** The reader rejects an `ai_context` key other than `instructions`, and an
  extension key it does not know, so a typo fails the load.
- **The Ossie commit** is a constant in the reader module, not written into the files, so a pin
  bump does not rewrite 57 files. `version` names the spec.
- **Reviewing a change.** `*.yaml diff=ossie` in BIRD-corpus's `.gitattributes`, plus
  `git config diff.ossie.xfuncname '^(- name: .*|  - name: .*)$'` in each clone, makes each hunk
  header name the dataset or field. `docs/corpus-format.md` states it.

### What Ossie checks and what it does not

From the assessment's tests against `validate.py`: dataset-scoped metrics and multi-line extension
JSON pass. Extension `data` as a mapping, and a dialect `POSTGRES`, fail. A relationship whose
`from_columns` names no field passes, so our loader checks relationship columns itself.

## 4. Rules

The [pivot's rules](2026-10-pivot-milestones.md#2-rules-for-every-milestone) apply, except the
snapshot rule, whose tools P17 deleted. Track S restores those tools for its own steps and
deletes them again in S4; its proof is stated at the top of Track S.
Branches are named `w<N>/<short-name>` for the pages and `s<N>/<short-name>` for storage.

`AGENTS.md` points at this plan.

## 5. Milestones

### D0. Record the decisions

1. ADR 0021, "Views around the chat": the three views of section 7, and that 0019's style rules
   (white page, one grey, amber only for guesses, no badges, no dark mode) apply to all of them.
   Amend 0019 and 0017 to point at it.
2. ADR 0022, "Ossie is the storage format": the layout and table of section 3, the pinned Ossie
   commit, and how a spec change is absorbed. Rewrite 0020 as a reversal record: §1 because
   Ossie is now the storage, and the part of §2 that kept the viewer out of the app, because the
   app now embeds its build. The viewer stays its own repository.
3. The glossary and `plain-names.toml` rows from section 2.
4. `AGENTS.md` points at this plan (done with the owner's consent on 2026-10-23).

**Sign-off:** the owner accepts both ADRs; `check_plain_names.py` passes.

### Track W: the pages

The app has three views: chat, the conversation list, and the semantic layer in the Ossie viewer.
There is no SQL log page. Each answer's Details shows which SQL answered it and every attempt that
failed on the way, with its error.

#### W1. Record every attempt's SQL and error

Today an attempt the checks reject keeps no SQL text, only `reason_code`
(`govern/ledger.py:85`: "a refusal keeps no checked text"). An attempt that passes the checks but
fails in the database is recorded with `passed: true` and its `executed_sql`; the error message
goes only to the agent (`serve/fetch.py:300`).

1. Add two fields to `AttemptRecord`: `submitted_sql`, the statement the agent wrote, always set
   when there was one; and `error`, the check's detail on a rejection or `ExceptionType: message`
   on a database error, else `None`.
2. Set them in `serve/fetch.py` for `run_query`, and for the sample and profile paths.
3. Keep both through compaction (`serve/state.py`), so an old turn still shows them.
4. Cap `error` at a fixed length, so a long driver message cannot grow the checkpoint.

**Sign-off:** tests for a rejected statement, a database error and a success, each with the two
fields; the turn record of a compacted turn still has them.

#### W2. How each conversation ended

1. `GET /conversations?limit&offset&outcome=`: per conversation, `thread_id`, `updated_at`,
   whether it is waiting for an answer, and per turn `asked_at`, `question` and `outcome`. Built
   like `/clarifications/pending` in `api/thread_turns.py`; the P4-era `summarise_turn` (at
   `6ea775ab^`) is prior art. Add it to `docs/openapi.json` and `tools/smoke_api.py`.
2. The sidebar shows, in small grey text under each conversation, how its last turn ended:
   "Answered", "Couldn't answer", "Waiting for you", "Stopped" or "Failed".
3. A "Conversations" page lists them all as a plain table, newest first: first question, number
   of turns, how it ended, date, with a filter by ending. A row opens the conversation.

**Sign-off:** route and UI tests; by hand, one answered, one refused, one waiting and one crashed
conversation show correctly; the route answers in under a second on the current
`runs/conversations.sqlite`.

#### W3. The SQL behind each answer

In the answer's Details, under "SQL":

- the statement that produced the answer, formatted with `lib/sql-format.ts` from the old-UI
  branch;
- above it, when there were any, "Tried first:" and each failed attempt in order: its SQL and its
  error in one plain sentence ("The checks stopped it: the table is not one retrieval found." or
  "The database returned: column "x" does not exist."). Reason codes are turned into words by one
  table in the UI.

**Sign-off:** UI tests; by hand, a turn with a rejected attempt, a database error and a final
answer shows all three in order.

#### W4. The semantic layer in the Ossie viewer

The viewer stays its own repository, `ossie-viewer`. The app embeds its build: one self-contained
`index.html` of about 220 KB that opens a model from `?file=<url>` and an item from `#…`.

1. A script, `npm run sync-viewer`, copies `../ossie-viewer/dist/index.html` to
   `ui/public/viewer/index.html` and writes the viewer commit beside it. The copy is committed, so
   the app builds without the viewer checkout.
2. Two read-only routes serve the corpus files as they are on disk: `GET /layer`, the list of
   schemas, and `GET /layer/{schema}`, that schema's Ossie file. Both refuse any path outside the
   corpus directory. The viewer fetches the file from another origin, so the route sends the CORS
   header for the UI's origin.
3. A "Semantic layer" view: a list of schemas, and the viewer in a frame opened on the chosen one.
4. The answer's Details gains "Open in the semantic layer", which opens the view on the first
   table the answer used.

**Depends on:** S3, because the viewer needs the corpus in Ossie form.

**Sign-off:** all 57 BIRD schemas open in the view; the link from Details lands on the right
dataset; `works_cycles` opens quickly enough to use.

#### W5. Documents

Update `docs/usage.md`, `docs/architecture.md`, the README and ADR 0007 for the three views and
the new routes. Delete the old-UI branch and its worktree.

### Track S: Ossie on disk

The proof for every step is the same pair:

- **Equal assets.** Load both trees and compare the dataclasses field by field, sorted by type and
  id (and in load order too, if S1 finds that order matters).
- **Equal contexts.** The pivot's offline snapshot, `tools/pivot_snapshot.py` and
  `tools/pivot_compare.py`, restored from `070db4d5^` for this track and deleted again in S4.
  It renders each question's context with no model and no network and hashes it. It does not
  cover the production embedder or the channel rewriters; equal assets cover those, because past
  the loader the engine reads only the dataclasses.

The corpus hash changes in S1 and S3. That is expected and needs no new baseline run: comparison
with P16 goes through context hashes (section 1). A rerun would prove less, since the two P16 runs
on one corpus already disagreed on 11.3% of questions. The hash stays a byte hash of the tree.

#### S1. Clear the ground

1. Restore the snapshot tools. Take a snapshot of BIRD at `8ba312ed` as the base for the track.
2. Check that asset order does not matter: snapshot with assets in load order and in sorted
   order. If the context hashes differ, sort in `from_assets` first and prove that change the
   same way.
3. Delete the fields nothing reads, from the engine, its tests and BIRD-corpus: `confidence`,
   join `cost`, few-shot `complexity` and `bound_terms`, term `related_terms`, column
   `is_unique`, `sample_values` and `references`, and few-shot `body` (rebuilt where it is read).
   Delete the negative-example type.
4. In BIRD-corpus, drop the trailing newline from the `books` schema summary. Add a load check that
   a schema summary has no newline and a rule has none.

**Sign-off:** the suite passes; BIRD loads with 0 problems; the snapshot matches the base except
the `books` row, listed in the PR before the run.

**Stop and ask if:** a deleted field turns out to have a reader, or the few-shot body differs from
the rebuilt text anywhere.

#### S2. The Ossie reader and writer

Add `corpus/ossie.py` beside the YAML store: the reader, the canonical writer and the placement
table of section 3, plus a one-shot command that converts a YAML tree.

Tests:

1. For BIRD, `read(write(load_yaml()))` equals `load_yaml()`.
2. `../ossie/validation/validate.py` passes every written file. Skipped with a printed reason
   when `../ossie` is absent.
3. Write, read and write again gives the same bytes.
4. No `ai_context` in any written file has a key other than `instructions`.
5. A fixture corpus covers each table row BIRD lacks: `role`, `governance`, an unbound term,
   `grain`, a non-equality join, a one_to_one join, a term bound to a metric.
6. The reader rejects an `ai_context` key other than `instructions`, an unknown extension key,
   and a relationship column that names no field.

Report the size of the written BIRD tree and of its model extensions, so the cost of putting the
few-shots in JSON is known.

**Sign-off:** all six tests pass; the sizes are in the PR.

**Stop and ask if:** `validate.py` rejects a structure section 3 relies on, or the model
extensions make a file too large to review.

#### S3. Convert BIRD-corpus

In `../BIRD-corpus`, one commit deletes the 7,357 asset files and adds 57 written by the command,
plus the `.gitattributes` line. The README describes the format.

**Sign-off:** test 1 passes between the converted tree and the YAML tree of the previous BIRD
commit; the snapshot over all 1,351 questions matches S1's final snapshot, with the corpus hash as
the only difference.

#### S4. Switch the engine and delete the old format

1. `store.load` reads Ossie, and `from_live_schema` writes one file per schema. The seeder writes
   joins with bare table names and no cardinality; the writer resolves the names, and those joins
   land in the model extension's `joins` until a separate change sets many_to_one for foreign keys.
2. Delete the YAML store, the inline-column split, `_shared`, `ossie_export.py` and its tests, the
   conversion command, and the restored snapshot tools.
3. `check_train_only.py` stops building `{id}.yaml` paths; `corpus/snapshot.py` and
   `check_no_benchmark_discriminators.py` read the new files.
4. Rewrite `docs/corpus-format.md`. Record in `docs/measurement.md` the new BIRD commit and corpus
   hash, that it holds the definitions of `8ba312ed` apart from S1, and the snapshot that shows it.

**Sign-off:** the suite passes; BIRD loads with 0 problems; S3's snapshot comparison passes again
on the new loader before the tools are deleted; `git grep -n ossie -- src` returns only
`corpus/ossie.py` and `corpus/store.py`; the server answers a question on the converted corpus.

#### S5. The viewer opens the corpus as it is

In `../ossie-viewer`: point its real-model test at `../BIRD-corpus/*/*.yaml`, open all 57, time
`works_cycles`, and finish V4. The viewer shows the `GOVERNED_BI` extension of `works_cycles` with
255 few-shots in it, so it may need to fold long extension data. Then W4 embeds it.

**Sign-off:** V4's boxes in `docs/open-work.md` are ticked.

#### Absorbing a spec change

The Ossie commit is one constant in `corpus/ossie.py`, and test 2 keeps running on BIRD. On a bump,
move `../ossie` and run the tests. If BIRD still validates, change the constant and stop. If the
format changed, change the writer and reader, rewrite BIRD with load then write, and prove it with
test 1 against the previous commit. When 0.2.0 is released, `version` changes the same way. What
this depends on, most fragile first: dataset-scoped metrics,
the relationship structure, and extension `data` as a string.

## 6. Order

```text
D0 ─┬─ W1 ── W3
    ├─ W2
    └─ S1 ── S2 ── S3 ── S4 ── S5 ── W4 ── W5
```

W1 to W3 and Track S share no files, so they can be two sessions in two worktrees. W2 needs
nothing from W1. W4 waits for S5: the viewer reads the corpus files in Ossie form, and S5 checks that it opens all 57.

Rough size: W1 about 150 Python lines with tests; W2 about 300 Python and 500 UI lines; W3
about 300 UI lines; W4 a script, two small routes and one page. In
Track S, S2 adds about 330 lines (the size of the assessment's prototype reader and writer) with
its tests, and S4 deletes the YAML store and the 616-line exporter, so the track is a net deletion.

## 7. Decisions

Answered by the owner on 2026-10-23:

1. **Views.** Chat, the conversation list with how each ended, and the semantic layer in the
   Ossie viewer, embedded in the app. No SQL log page.
2. **SQL.** Only which SQL answered each question, and the errors of the attempts before it, in
   that answer's Details (W1, W3).
3. **Tables.** No rows from the database. The semantic layer as the Ossie viewer draws it (W4).
4. **Ossie.** The only format on disk. What Ossie 0.2.0 does not define goes in the JSON string of
   the `GOVERNED_BI` extension, not in extra `ai_context` keys (section 3).
5. **Other tools' Ossie files.** Not now.
6. **Reach of the record.** What the conversation store keeps is enough (the last 25 turns per
   conversation, no evaluation runs). No new store.
7. **`AGENTS.md`** points at this plan.

Settled following the assessment: the code is not renamed to Ossie's words (section 2), and the
corpus hash stays a byte hash of the tree (Track S).
