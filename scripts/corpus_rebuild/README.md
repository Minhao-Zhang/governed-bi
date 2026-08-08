# Corpus rebuild scripts

One-shot, BIRD-specific, not portable. Deliberately outside `src/governed_bi`; nothing in the
package imports them. See [`docs/corpus-rebuild-brief.md`](../../docs/corpus-rebuild-brief.md)
for what they are for and what the agent does afterwards.

Plain Python — stdlib, `pyyaml`, `psycopg`, `sqlglot` — with one engine import:
`governed_bi.corpus.identity` for `table_id`, `derive_column_id`, `join_id` and `slug`. Ids must
have exactly one spelling; when they did not, `airline."Air Carriers"` ended up with no table
asset while 24 few-shots cited it.

## Order

| script | reads | writes |
|---|---|---|
| `01_structure.py` | `PG_RENAME_DECOY_DSN` (introspection) | schema / table / column assets, summaries as `TODO <identifier>` |
| `02_joins.py` | `train_final.jsonl`, `*_tables.json` | join assets, `on` schema-qualified |
| `03_few_shots.py` | `train_final.jsonl` | few-shot assets |
| `04_evidence.py` | `train_final.jsonl` | `_build/evidence_clauses.jsonl` (**staging, not assets**) |
| `05_bird_docs.py` | `database_description/*.csv`, `schema_rename_map.json` | `_build/bird_docs.jsonl` (**staging**) |
| `06_samples.py` | `PG_RENAME_DECOY_DSN` (read-only) | `_build/samples.jsonl` (**staging**) |

01–03 write the corpus. 04–06 stage material for the agent and write no asset.

```bash
uv run python scripts/corpus_rebuild/01_structure.py  --out ../BIRD-corpus
uv run python scripts/corpus_rebuild/02_joins.py      --out ../BIRD-corpus
uv run python scripts/corpus_rebuild/03_few_shots.py  --out ../BIRD-corpus
uv run python scripts/corpus_rebuild/04_evidence.py
uv run python scripts/corpus_rebuild/05_bird_docs.py
uv run python scripts/corpus_rebuild/06_samples.py
```

Every script takes `--schemas a b c` for a single-schema pass, which is how the agent works
(one schema, start to finish, one commit).

## What the floor is supposed to look like

After 01–03 the tree **loads and fails conformance**, and that split is the point:

```
V14  0    the engine's loader accepts the file      <- structurally sound
V3   0    the identifier is present
V9   0    every reference resolves
V2   181  summary is still the sentinel             <- nobody has written it
V6   181  no body
```

A bare `TODO` would fail the model's own identifier rule and drown V14 — the one signal that
catches a *broken* file — in noise from every *unwritten* one. Hence `TODO <identifier>`.

Verified: two runs over the same inputs produce byte-identical trees. `write_asset` forces LF
because the corpus repository sets `* -text`, and 1,327 files differed between the last two
corpora on line endings alone.

## Two constraints that will bite

**A join summary must contain the *slugged* component of `left_table` and `right_table`, not the
physical name.** `left_table` is `airline.Air_Carriers_66c534`, so the identifier rule wants
`Air_Carriers_66c534`. For 655 of 656 tables the slug equals the physical name and it reads
naturally; the handful with spaces or accents do not.

**Inline columns carry no `id`, `schema` or `parent_table`.** `store.py::_split_inline_columns`
derives all three, and a file that supplies one is a problem rather than an override.

## Held-out data

`_common.guard()` refuses `test_final.jsonl`, the gold result hashes and the paraphrase file.
The trap manifests are **not** refused — the database under test is the decoy instance and a
steward would know which of its columns are junk. What may be *written* about them is
constrained instead; see the brief, §4.4.
