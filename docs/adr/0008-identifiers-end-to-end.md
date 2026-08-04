# 0008: Identifiers, end to end

- **Status:** Proposed (2026-08-04). No code. Amends
  [ADR 0005](0005-v2-memory-layer-and-faceted-retrieval.md) §1.2/§2.8.2 and
  [ADR 0006](0006-execution-time-governance.md) §3/§4.
- **Deciders:** project owner + design session (2026-08-04)
- **Scope:** every string in this system that names a schema, a table or a column —
  in the corpus, on the filesystem, in the retrieval structure, in the governance
  allowlists, and in the statement handed to Postgres. Which of them are *keys*,
  which are *names*, how one becomes the other, and where the two are allowed to
  be compared.
- **Not in scope:** what an asset *means*, retrieval ranking, the serve graph's
  shape. Those are 0005.

---

## Context

### One table has four names, and the four namespaces do not agree on equality

`address.CBSA` is:

| namespace | spelling | charset | equality | owner |
| --- | --- | --- | --- | --- |
| engine | `"CBSA"` in schema `address` | anything, if quoted | exact when quoted, fold-to-lower when not | Postgres |
| corpus | `address.CBSA` (asset id) | `[A-Za-z0-9_][A-Za-z0-9_.:-]*` | **exact string** | `corpus/identity.py` |
| filesystem | `address/tables/…CBSA.yaml` | `[A-Za-z0-9_]+` per component | **case-insensitive on NTFS/APFS** | `corpus/store.py` |
| governance | `address.cbsa` (folded key) | folded, 2 or 3 parts | **`str.lower()`** | `govern/identifiers.py` |

Three different equality relations over one identifier. Every defect below is a
place where two of these namespaces met and nobody had decided which one was
authoritative.

Measured on the live obfuscated lake (`pg_rename_decoy`, 2026-08-04) and on
`corpora/gold-semantic-layer-20260804`:

```
database   738 tables    81 mixed-case (11.0%)   1 with a space   6909 columns
                                                                   610 mixed-case (8.8%)
                                                                     1 with a space
                                                                     1 non-ASCII
                                                                     1 leading digit
corpus     655 tables    80 mixed-case          13 975 assets     0 fold collisions
                                                                   0 unsafe ids
```

### The inventory: every field that carries an identifier

Read from `corpus/schema.py`, with the count from the corpus and who resolves it.

| field | what it names | shape today | resolved by | count |
| --- | --- | --- | --- | --- |
| `SchemaAsset.id` / `.name` | a namespace | bare component | must equal its directory | 57 |
| `TableAsset.id` | key | `{schema}.{physical_name}` | derived by `table_id()` | 655 |
| `TableAsset.schema` | ref → schema | bare | exact | 655 |
| `TableAsset.physical_name` | **engine name** | verbatim | — | 655 |
| `TableAsset.columns[]` | ref → column | derived ids | exact in `by_id` | 5 942 |
| `ColumnAsset.id` | key | `{schema}.{table}.{column}` | derived | 5 942 |
| `ColumnAsset.parent_table` | ref → table | **bare** | `_bind` + `scope=schema` | 5 942 |
| `ColumnAsset.physical_name` | **engine name** | verbatim | — | 5 942 |
| `ColumnAsset.references` | ref → column | free string | **nothing** | — |
| `JoinAsset.id` | key | `join_{schema}_{l}_{r}_{digest8}` | derived | 928 |
| `JoinAsset.left/right_table` | ref → table | qualified id | `_bind`, no scope | 1 856 |
| `JoinAsset.on` | **SQL fragment** | bare/aliased SQL | `on_digest` only | 928 |
| `MetricAsset.base_table` | ref → table | qualified id | `_bind`, no scope | 399 |
| `MetricAsset.dimensions[]` | ref → column | **bare** | **nothing** | 715 |
| `MetricAsset.expression` | **SQL fragment** | bare SQL | **nothing** | 399 |
| `TermAsset.binding.target_id` | ref → any asset | 858 dotted / 109 bare / 27 absent | exact in `by_id` | 967 |
| `TermAsset.related_terms[].id` | ref → term | free string | **nothing** | — |
| `FewShotAsset.schema` | ref → schema | bare | exact | 5 000 |
| `FewShotAsset.sql` | **SQL fragment** | qualified SQL | sqlglot + `_bind` | 5 000 |
| `FewShotAsset.bound_terms[]` | ref → term | free string | **nothing** | — |
| `NegativeExampleAsset.schema` | ref → schema \| None | bare | exact | 0 |

Five reference fields are resolved by nothing at all. Three of them
(`JoinAsset.on`, `MetricAsset.expression`, `MetricAsset.base_table`) are rendered
into the model's context verbatim by `serve/context.py:298–308`. So the corpus can
state a join key naming a column that does not exist, and the model receives it as
fact.

---

## What has actually gone wrong

Eight defects. Each is measured, and each was invisible with 389 tests green.

### P1 — A statement that passes all six governance layers and the engine cannot run

The headline. Reproduced 2026-08-04:

```
prepare("SELECT cbsa_name FROM address.cbsa LIMIT 1", licensed={"address.CBSA"}, …)
  → passed=True,  sql="SELECT cbsa_name FROM address.cbsa LIMIT 1"
psql: relation "address.cbsa" does not exist
```

`check()` folds both sides (`normalise_table_key`, `fold`), so `address.cbsa`
matches `address.CBSA` and the table layer allows it. Nothing then rewrites the
spelling, so Postgres receives the folded name and cannot find the relation.

ADR 0006 §3 step 2 exists precisely to prevent this — *"rewrite each identifier to
the corpus's declared spelling"* — and it is **not wired**:

- `prepare()` declares `spellings` and `ambiguous_folds` as *optional*
  (`pipeline.py:169–170`);
- the only production caller, `serve/tools.py:485`, passes neither, so
  `canonicalise` runs with `spellings={}` and rewrites nothing;
- `fold_map()`, which produces those two arguments, **has no caller in `src/`** —
  only `tests/govern/test_guard_pipeline_ledger.py`, which hands it a hand-written
  three-entry dict.

So the control has a producer with no caller, a consumer whose argument nobody
passes, and two green tests that exercise it in isolation. This is L§7's vacuous
test in a governance layer.

And wiring it is **not sufficient**. `canonicalise` sets `this` without touching
`quoted`, so sqlglot emits the declared spelling unquoted and Postgres folds it
again:

```
rename only    → SELECT c.CBSA FROM address.CBSA AS c      (Postgres: relation does not exist)
rename + quote → SELECT 1 FROM address."CBSA"             (Postgres: OK)
```

Blast radius: **81 of 738 tables and 610 of 6 909 columns** are unreachable on the
answered path. Every one of them fails *after* a passing verdict, so the ledger
records `passed=True` with a driver error — indistinguishable from a flaky
database.

### P2 — The corpus cannot represent identifiers the engine has, and the loss is table-shaped and silent

`_COMPONENT_RE = [A-Za-z0-9_]+` guards `physical_name`, and `table_id()` derives
the asset id *from* `physical_name`. So an identifier the charset rejects yields no
asset at all. Three exist:

| engine identifier | why rejected | what the corpus lost |
| --- | --- | --- |
| `airline."Air Carriers"` | space | no table asset; 24 few-shots cite it |
| `app_store.playstore."Content Rating"` | space | **the whole table** — 13 columns, no asset |
| `soccer_2016.saison."orange_trophée"` | non-ASCII | **the whole table** — 8 columns, no asset |

The curator's response to an unrepresentable column was to drop its table. Two
tables and 21 columns are absent from a corpus that reports 0 problems for them,
and the only record is a `skipped_identifiers.json` beside the generator that
nothing reads.

At serve time this is unobservable. A question about app ratings routes, licenses
whatever else matched, and either declines `missing_join_path` or answers from the
wrong table. There is no value anywhere meaning *the corpus cannot spell this
table*.

The corpus **does** carry `address.zip_data.1st_quarter_payroll` — the charset
permits a leading digit — and that column can never be queried successfully; see P7.

### P3 — Case-sensitive in the corpus, case-insensitive in governance, case-insensitive-differently on disk

- `_table_lookup` / `by_id` / `references` / `licensed` compare **exact strings**. A
  join endpoint spelled `Customers` against `physical_name: customers` is a
  **dropped edge**.
- `check()` compares **folded** keys. The same mismatch is a **match**.
- `store.write` names the file `{asset.id}.yaml`. Two ids differing only in case are
  **one file** on this developer's machine, and the second write overwrites the
  first.

`fold_map` was built to detect the third case and, as P1 records, is never called.
`build_index` refuses exact duplicate ids and is blind to case-only duplicates.
Zero occurrences in this corpus, and nothing prevents the next one.

### P4 — The dot is both the separator and a legal character inside a component

`_ID_RE` permits `.` after the first character. So `schema.a.b` is a valid id and is
simultaneously *table `a.b` in schema* and *column `b` of table `a`*. The invariant
that makes ids parseable — **an id has exactly as many dots as its depth** — is
stated nowhere and enforced nowhere.

Today it is unreachable, by accident: a *different* rule in a *different* module
(`validate_path_component` on `physical_name`) forbids dots. If it were reachable,
`normalise_table_key` raises `ValueError` on a three-part table key — at
`check.py:115`, which is **above** the `try` at line 128. So the failure mode is an
uncaught exception out of `run_query`, i.e. a crash, which this project has already
once counted as a refusal.

### P5 — Bare references survive in three fields, and each fails differently

The 2026-08-04 migration (decision #47) qualified `left_table`, `right_table` and
`base_table`. Three bare-reference classes remain:

1. **`ColumnAsset.parent_table`** — 5 942, bare. Works, because `_bind` is given
   `scope=asset.schema`. Correct by a mechanism that exists for one field.
2. **`MetricAsset.dimensions`** — 715 bare column names, resolved by nothing. They
   do not enter `references`, so a metric hit never pulls in the columns it needs,
   and a dimension naming a nonexistent column is never reported. Not even
   rendered — write-only data.
3. **`TermAsset.binding.target_id`** — 109 bare, and these are *metric* ids. Because
   `_own_schema` is deliberately one level deep, such a term gets **no schema tag**;
   untagged assets are carried into pass two unconditionally, so one lexical hit
   bridges schemas. `term_shakespeare_character_count` → its metric →
   `shakespeare.parrafos` + 4 columns enter `licensed` on a `beer_factory` question,
   and `connect` cannot join Shakespeare to a brewery. **This is why the pooled lake
   still declines even at `route_top_n=1`.**

### P6 — SQL-bearing fields are never checked against the corpus

`JoinAsset.on` (928), `MetricAsset.expression` (399). Both reach the prompt
verbatim. Neither is parsed against the asset set. `on_digest` parses `on` only to
compute a hash and discards the tree.

There is already a function that does exactly this check — `govern.bind` — and it
runs only at execution time, on the model's SQL, three layers away from the corpus
that supplied the wrong key.

### P7 — sqlglot's parse and Postgres's parse can disagree, and governance believes sqlglot

```
sqlglot:     SELECT 1st_quarter_payroll FROM address.zip_data
          →  SELECT 1 AS st_quarter_payroll FROM address.zip_data
Postgres 18: SyntaxError: trailing junk after numeric literal at or near "1st_quarter_payroll"
```

sqlglot silently reinterprets the identifier as a numeric literal plus an alias.
The reinterpreted statement has **no `Column` node**, so `bind` has nothing to
bind, the column layer has nothing to check, and the statement is **allowed** —
having been checked in a form nobody will run.

Here the engine refuses, so the outcome is fail-closed by luck. The shape is not
luck-bounded: any reinterpretation that is *also* valid Postgres means governance
vouched for a different statement than the one that executes. This is B5's family,
and B5 is the reason ADR 0006 exists.

### P8 — Nothing tells the model the naming rules, and the refusal misnames the cause

`SYSTEM_PROMPT` is three sentences and mentions neither schema-qualification nor
quoting. `tools.py` passes no `default_schema`, so `_classify_sources` keys an
unqualified `FROM customers` as `customers` while `licensed` holds
`beer_factory.customers`. The table layer refuses `r_table_not_licensed` — *"the
model asked for a table it may not see"* — for what is really *"the model omitted
a schema nobody told it to write"*.

---

## Decision

Ten rules. The first four are the design; the rest follow from them.

### D1 — A key is not a name. Split `id`, `slug` and `physical_name`

Today `table_id(schema, physical_name)` makes the corpus key a function of the
engine's spelling, so an identifier the key charset rejects has no asset (P2), and
an identifier the key charset *accepts* but SQL cannot spell unquoted becomes an
unusable asset.

Three fields, three jobs, never conflated:

- **`physical_name`** — the engine's identifier, **verbatim**. Any character, any
  case, any script. Never a path component, never an id component. The only string
  that may reach Postgres.
- **`slug`** — the derived, filesystem-safe, dot-free component. Derived, never
  authored:

  ```
  slug(physical) = physical                                   if physical matches [A-Za-z0-9_]+
                   sanitise(physical) + "_" + sha256(physical)[:6]   otherwise
  ```

  Deterministic, order-independent, and readable in the common case
  (`CBSA` → `CBSA`; `Air Carriers` → `Air_Carriers_9f2c1e`).
- **`id`** — `{schema_slug}.{table_slug}[.{column_slug}]`. Dots are structural
  **only**, so depth is recoverable from the string and P4 closes by construction.

`airline."Air Carriers"` becomes a first-class asset: id `airline.Air_Carriers_9f2c1e`,
`physical_name: "Air Carriers"`, and the 24 few-shots resolve. So do the two
tables the curator dropped.

### D2 — Every identifier reaching the engine is emitted **quoted**, always

Not "quoted when necessary". *Necessary* is a predicate over Postgres's folding
rules, and P1 is that predicate being wrong for 11% of the lake. Always-quote is:

- correct for every observed shape — `"CBSA"`, `"Air Carriers"`,
  `"1st_quarter_payroll"`, `"orange_trophée"`;
- deterministic in the ledger: one spelling per table forever, so two runs'
  `generated_sql` hashes are comparable;
- and it makes `r_ambiguous_fold` unreachable **by construction** rather than a
  refusal that must fire correctly.

Mechanism: `canonicalise` rewrites each known identifier to its declared
`physical_name` **and sets `quoted=True`**. Unknown identifiers (aliases, CTE
names) stay untouched, as today.

### D3 — Fold to *find*, never to *decide*

Folding is how the system recognises what the model wrote. It must not be how the
system decides what is authorised.

- Step 2 (canonicalise) folds to look up the declared spelling. This is the *only*
  place `fold()` is called.
- Step 3 (check) compares **exact** keys, because after D2 the statement carries
  declared spellings and the engine's own equality on quoted identifiers is exact.
- An identifier canonicalisation could not resolve reaches the check unrewritten
  and refuses. Fail-closed, with the right reason.

This deletes P3's asymmetry: one equality relation across corpus, structure and
governance. `corpus/analyst.column_key_for` and `govern/identifiers.column_key` —
two implementations of one convention held together by a conformance test —
collapse into one function, and the conformance test becomes unnecessary rather
than load-bearing.

### D4 — A reference field points at an **asset id**, exactly, or the corpus is broken

There are exactly two kinds of identifier-bearing field, and no field may be both:

- an **asset reference** — must equal an existing asset id, compared exactly;
- an **engine identifier** — `physical_name`, verbatim, quoted on emission.

ADR 0005 §2.8.2's *"a physical name, bare or qualified"* is a third kind, and it
cost 25% of the corpus's joins. It is withdrawn. Normalisation happens **once, in
the loader**, and `structure.py` then performs exact lookups only — `_table_lookup`'s
three-spellings-per-table tolerance is what allowed bare references to persist
unnoticed, and it goes away.

Consequences, by field:

- `parent_table` → the table's id (5 942 rewrites; the `scope=` mechanism in `_bind`
  is then dead code and is deleted).
- `dimensions` → column ids, **and they enter `references`**, so a metric hit pulls
  in the columns it names (P5.2).
- `binding.target_id` → already an id; the **tag** becomes transitive — resolve the
  target's schema through the reference graph rather than one level (P5.3, the
  pooled leak).
- `columns[]`, `bound_terms[]`, `related_terms[].id`, `references` → resolved and
  refused if absent, instead of unread (P2's silence in miniature).

### D5 — SQL-bearing corpus fields are bound at build time, by the binder governance uses

`JoinAsset.on`, `MetricAsset.expression` and `FewShotAsset.sql` are parsed and bound
against the asset set with `govern.bind`. A fragment whose identifiers do not bind
is a recorded problem and does not reach the prompt. One binder, two call sites
(curation and execution), so the corpus cannot assert a key the governance layer
would refuse.

### D6 — The checked statement and the emitted statement must have identical bindings

After step 4, re-parse the emitted string and re-run `bind`; require the bindings to
equal the checked ones. One extra parse per query. This closes P7 generally rather
than special-casing leading digits, and it is also the assertion that D2's quoting
actually took effect — the ledger's hash is then over a statement whose bindings
were verified, not merely over a string produced after a verdict.

### D7 — An optional control argument is a control that will be un-wired

P1 is not a bug in `canonicalise`; it is a bug in `prepare()`'s signature. Three
more instances live in this codebase right now: `default_schema` (never passed),
`route_top_n` / `max_steiner_points` / `max_crossings` (declared
`Role.comparability`, readable only from per-turn `state`, written by no production
entry point — decision #47).

So: **a governance input has no default.** `spellings` and `ambiguous_folds` become
required parameters of `prepare()`, in the same fail-loud shape `check()` already
uses for `licensed=None` and `corpus=None` — *"None is not 'no restriction'"*. A
comparability knob is read through the `(state, knobs_resolved, cfg)` fallback its
siblings use, or it is not a knob.

### D8 — Unrepresentable is a value, not a silence

Under D1 the three known cases become representable, so the skip set should be
empty. The mechanism still matters for what remains (identifiers over 63 bytes,
future scripts), so: a table the corpus cannot carry is recorded **in the corpus**,
and `route` can answer *"unanswerable: `app_store.playstore` is not carried"*
instead of declining for an unrelated reason. `skipped_identifiers.json` beside a
generator is the shape D8 exists to forbid.

### D9 — One corpus-level validation pass, and one definition of *fatal*

Four of these rules are **set** properties that `problems_with(asset)` structurally
cannot see: slug collisions, case-only id collisions, unresolvable references,
orphan reference targets. So `problems_with_corpus(assets) -> list[Problem]` is a
new seam, and it is where D4/D5 are enforced.

`Problem` gains a class, because this design multiplies problem counts and the CLI
and the server currently disagree about what is servable — `python -m
governed_bi.serve` exits 3 on *any* problem while `make_graph()` never checks:

- **`fatal`** — an id is not a key: duplicate id, slug collision, unresolvable asset
  reference. The corpus does not serve.
- **`degradation`** — recorded, servable, and **counted in the record**: a table the
  corpus cannot carry, a dropped few-shot, a join whose `on` does not bind.

Both entry points read the same function, and the turn record carries
`corpus_problems: {fatal, degradation}` so a run over a lossy corpus is not
comparable to a run over a clean one.

### D10 — Tell the model the one rule it must follow, and derive it from the same table

After D2 the model's *spelling* no longer matters — canonicalisation fixes it. Only
qualification does. So `SYSTEM_PROMPT` states: **every table reference is
`schema.table`**; and `default_schema` is passed **iff every licensed table shares
one schema**, otherwise `None`. That removes P8's misleading `r_table_not_licensed`
without ever guessing a schema for a multi-schema turn.

---

## Build order, and the measurement that says each step worked

**Phase 0 — the three cheap ones, none of which needs D1.**

| change | closes | measurement |
| --- | --- | --- |
| wire `spellings` + `ambiguous_folds` into `tools.py`; quote on rewrite; make them required | P1 | a query against `address.CBSA` answers; 81 tables + 610 columns reachable |
| transitive schema tag for metric-bound terms | P5.3 | 136 untagged terms → 0; a `beer_factory` question licenses no `shakespeare` asset |
| the three route knobs read `knobs_resolved` | D7 | setting `route_top_n=1` changes routing *and* the record |

Phase 0 is what stands between the pooled lake and its first answer. It is also
the whole of P1, which is the only defect here that produces a wrong-looking
*database* rather than a wrong-looking corpus.

**Phase 1 — the key/name split.** D1, D4, D9. A corpus migration plus a generator
change; `_table_lookup` and `_bind`'s `scope=` are deleted, not adapted.
Measurement: `airline.Air_Carriers_*` exists, the 24 few-shot problems go to 0,
`app_store` and `soccer_2016` gain their tables, and `problems_with_corpus` reports
`fatal: 0`.

**Phase 2 — the two verification passes.** D5, D6, D8. Measurement: the count of
join `on` clauses that do not bind (currently unknown — that is the point), and a
round-trip assertion that fires on `1st_quarter_payroll`.

---

## Alternatives rejected

**Widen `_ID_RE` to admit spaces and quotes.** An id becomes a filename, and
`"Air Carriers".yaml` is illegal on Windows. It would also make the id a second
spelling of `physical_name`, which is this project's most expensive shape.

**Structured references (`{schema: x, table: y}`) instead of dotted strings.**
Genuinely closes P4, and rejected anyway: ids are already dotted keys, so a struct
would be a *second* addressing scheme living beside the first, and D1 closes P4 by
making dots structural-only. Reconsider if slugs ever need to carry dots.

**Quote only when the identifier needs it.** This is P1 with a smaller radius. The
predicate is over the engine's folding rules; getting it wrong is silent; and
always-quoting costs nothing but two characters.

**Make the corpus layer case-insensitive, so it agrees with governance.** Wrong
direction. Postgres, given quoted identifiers, distinguishes `"CBSA"` from
`"cbsa"`; a corpus that cannot is a corpus that cannot describe a lake it is
pointed at. D3 moves governance to exact comparison instead.

**Leave `licensed` folded and normalise at the boundary.** That is today's design
and it is why P1 passed a verdict: the fold made the check succeed on a string the
engine could not resolve. The fold must happen where the *lookup* happens, not
where the *decision* happens.
