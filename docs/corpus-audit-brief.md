# Audit brief — is the rebuilt corpus any good?

You are auditing a semantic layer that 57 agents wrote over two days. A conformance checker
already passes on all of it. **Your job is to find what a checker cannot see.**

Do not re-run the checker and report zeros. Assume every mechanical rule passes; if one does
not, that is a bug worth reporting, but it is not what you were called for.

## What you are looking at

- **The corpus**: `../BIRD-corpus`, branch `rebuild-20260808`. 57 schemas, ~13,300 assets —
  5,947 columns, 656 tables, 706 joins, 593 terms, 465 metrics, 4,857 few-shots.
- **The ground truth it was written from**: `../BIRD-Data-Obfuscation`, in particular
  `eval_dataset/trap_manifest.json`, `eval_dataset/trap_table_manifest.json`,
  `eval_dataset/schema_rename_map.json`, `database_description/*.csv`, and `train_final.jsonl`.
- **The checker**: `tools/check_corpus_conformance.py`, rules V0–V15. Read it first — knowing
  what is already guaranteed is what keeps you from wasting the audit on it.

**Do not open** `eval_dataset/test_final.jsonl`, any `gold_result_hashes*` file, or
`question_paraphrases.jsonl`. They are the held-out split. Everything else is fair game.

## The field contract, because every judgement depends on it

Four facts about how the engine uses these assets. They are not style preferences; they decide
what "good" means for each field.

| field | who reads it | consequence |
|---|---|---|
| `summary` | **the retrieval index, and nothing else.** BM25 and the embedding both score this string. The model never sees it. | A summary is a *search target*, not a description. If it is written for a human reader it is doing the wrong job. |
| `body` | **the model, and nothing else.** Rendered only once the asset is retrieved. | A body that assumes the summary was read is broken. It must stand alone. |
| `rules` (schema) | rendered every turn, never dropped | A long list is a tax on every question in that schema. |
| `reliability.note` | rendered every turn, `evictable: False` | Marking a real column suspect tells the model not to use it, forever, and nothing can drop the caveat. |

The schema-level `summary` is the **only** string that routes a question to one database out of
57. Everything else is downstream of that choice.

## Where to look — ranked by what it costs to be wrong

### 1. Schema summaries as a routing set (highest value; nobody has checked this)

Every schema summary was written in isolation by an agent that had never seen the other 56.
Read all 57 together. Report:

- **Collisions.** Two schemas whose summaries compete for the same vocabulary. There are
  multiple movie schemas, multiple sports schemas, multiple retail/sales schemas. Which pairs
  would a router confuse, and what word would break the tie?
- **Domain misidentification.** At least one schema is misnamed for its contents: `soccer_2016`
  holds IPL cricket data under German column names. A summary written to the *name* rather than
  the *data* makes the whole schema unreachable. Check every schema summary against its own
  `value_samples`. This is the single most expensive defect class in the corpus.
- **Dead vocabulary.** Summaries spending their 250 characters on table-name rosters instead of
  the words a business user would actually type.

### 2. Bodies that contradict the data

The bodies assert value domains, ranges, units, and code tables. Sample aggressively and check
them against `value_samples` in `scripts/corpus_rebuild/_build/packets/<schema>.json`.

Two known traps:

- **`value_samples` min/max are computed by lexicographic sort, not numerically.** A writer who
  trusted them wrote false ranges (`max = "9"` on a column containing 10). Writers were warned;
  check whether every one of them listened.
- **`bird_documentation` — the dataset's own column notes — is corrupted in places.** Confirmed:
  `mondial_geo`'s mountain table carried the river table's latitude/longitude notes;
  `works_cycles` had `Vendor.ActiveFlag` described as "Vendor URL", `ProductVendor`'s
  Min/MaxOrderQty descriptions swapped, `Password.PasswordHash` called an e-mail password, and
  `Location.LocationID` carrying `JobCandidate`'s description. Four schemas caught this
  independently. **How many did not?** Cross-check bodies against `original_table` /
  `original_column` and the sample values.

### 3. Unreliable columns — the wording, not the set

V15 already proves the *set* of suspect columns matches the manifest. It says nothing about
whether the treatment is any good:

- **Did a real column lose its vocabulary?** When a decoy imitates a real column, the real one
  should still own the business words. Some writers went further and bound a term to the real
  column naming the decoy (`card_games`: users say "illustrator", the real column is `artist`).
  Most did not. Where the evidence clauses show users spending words on a decoy's name and no
  term wins it back, that is a retrieval loss.
- **Is a suspect column's summary so thin it is unfindable, in a schema where the question
  genuinely is about it?** The thin template is right for ranking, but check it did not get
  applied to a column that is only *partly* unreliable.
- **Did anyone censor real data to satisfy V10?** One writer hit the literal value `Offside Trap`
  and rewrote the body to avoid the banned word, leaving no string a query could filter on. That
  was found and fixed. Look for the same shape elsewhere: a body that describes a code table
  behaviourally instead of naming its values.

### 4. Terms and metrics

- **Bindings resolve (V9) but are they the *right* target?** Spot-check that a term is bound to
  the column the evidence clauses are actually about.
- **Metric expressions.** V9 checks `base_table` exists. Nothing checks that the SQL in
  `expression` references columns that exist on that table, or that the aggregate is the right
  one. Verify a sample against the real schema.
- **Coverage skew.** Counts per schema range from 0 to 41. A schema with rich
  `evidence_clauses` and no terms means a writer skipped the deduction step. Compare
  `len(evidence_clauses)` in each packet against the terms and metrics produced.
- **Genuine ambiguity captured?** The best writers built terms for one-word-many-columns cases
  (`professional_basketball`: "round" means both the lettered playoff round and the numeric draft
  round). Most built one-term-per-column glossaries instead. Where does a schema have a real
  ambiguity that no term resolves?

### 5. Joins

- **Cardinality correctness.** It was inferred, mostly not verified. A `one_to_many` that is
  really `many_to_many` produces silent fan-out and wrong aggregates. Check a sample against the
  actual keys.
- **Templated summaries.** Schemas with many joins (`mondial_geo` 51, `hockey` 45,
  `works_cycles` 89) are where an agent is most likely to have produced 89 variations on one
  sentence. Those strings decide whether the right join is retrieved.

### 6. Cross-schema consistency

The same concept was written 57 times by 57 agents. Sample a recurring one — a date column, a
surrogate key, a money amount — and report how differently it is handled. You are not looking
for uniformity for its own sake; you are looking for places where one schema's convention would
mislead a model that just read another's.

## How to work

Use subagents freely and give them **different** slices — by schema group, by asset type, by
defect class. A single pass over 13,300 assets will be shallow.

**Quantify.** "Some bodies have wrong ranges" is not actionable. "31 of 200 sampled numeric
column bodies assert a max that contradicts `value_samples`, concentrated in 4 schemas" is.
State your sample size and how you drew it. If you extrapolate, say so.

**Verify before reporting.** Several findings in this project's history turned out to be the
measurement's fault, not the corpus's — most recently an audit that reported 930 mis-marked
columns because it compared the manifest's upstream table names against the corpus's renamed
ones without going through `schema_rename_map.json`. Before you report a class of defect, prove
one instance by hand, end to end.

**Rank by cost, using the field contract.** A defect in a schema summary is worse than the same
defect in a column body, because the summary gates whether the body is ever seen at all.

## Deliverable

A report at `docs/corpus-audit-<date>.md`:

1. **What is actually wrong**, ranked by cost, each finding with a named example, the file and
   line, and how many instances you estimate from what sample.
2. **What is fine** — say so explicitly, with the evidence. An audit that only lists problems
   cannot be acted on, because nothing tells the reader where to stop looking.
3. **Which of these should become a checker rule** (V16+) versus which need judgement. The
   checker grew from 15 rules to 16 during the rebuild precisely because running rules against
   real output kept exposing defect classes nobody had anticipated. If you find one that is
   mechanically decidable, say what the rule would be and what it would cost in false positives.
4. **What you could not check**, and what you would need in order to.
