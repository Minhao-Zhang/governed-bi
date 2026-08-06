# Rewriting the corpus summaries — brief for the agent doing it

**Date:** 2026-08-05 · **Corpus under test:** `corpora/gold-semantic-layer-20260804` (57 schemas,
13 981 assets) · **Sample:** 114 questions, 2 per schema, from `test_final.jsonl`

This is a work order, not a proposal. The hypothesis behind it was tested first, **found to be
half wrong**, and corrected; what follows is the corrected version with a measured floor the work
has to beat.

Read [Hard constraints](#hard-constraints-do-not-break-these) before writing anything. One of
them — the train/test split — cannot be repaired after the fact.

---

## The one-paragraph version

Only `summary` is indexed ([ADR 0005](../adr/0005-v2-memory-layer-and-faceted-retrieval.md) I1;
`retrieve/index.py:41`). Every schema and table already carries good domain prose in `body`, and
**the index has never seen a word of it.** Moving that vocabulary into `summary` — mechanically,
with a regex, no model calls — raises gold-table coverage from **0.632 to 0.693** at
`route_top_n=3` and **0.509 to 0.588** at `route_top_n=1`, while licensing *fewer* tables. That
is the floor. The job is to beat it by writing the vocabulary deliberately instead of extracting
it with a stopword list.

---

## 1. What the summaries actually are

Sampled with a census over all 13 981 assets. `summary` is the only indexed text; `body` is what
renders on a hit.

| type | n | `summary` median chars | function-word ratio | has `body` | `body` median |
| --- | --- | --- | --- | --- | --- |
| schema | 57 | 94 | 0.00 | 57 | 260 |
| table | 656 | 61 | 0.00 | 656 | 139 |
| column | 5 947 | 38 | 0.00 | 5 942 | 66 |
| join | 928 | 68 | 0.13 | **0** | — |
| metric | 399 | 71 | 0.14 | 242 | 108 |
| term | 994 | 88 | 0.22 | **16** | 107 |
| few_shot | 5 000 | 73 | 0.43 | 5 000 | 312 |

A function-word ratio of 0.00 across the four structural types is the shape the maintainer called
keyword soup, and the census agrees that is what it is:

```text
schema  hockey      "hockey: 22 tables — abbrev, AwardsMisc, AwardsPlayers, AwardsCoaches,
                     CombinedShutouts, TeamVsTeam, TeamsHalf, TeamsPost, TeamsSC, TeamSplits,
                     Teams, SeriesPost, HOF, Master, Scoring, ScoringSC, ScoringShootout,
                     ScoringSup, Goalies, GoaliesSC, Goali…"        <- truncated at the cap
table   titres      "titles (titres): title_id, title, type, pub_id, price, advance, royalty,
                     ytd_sales, notes, pubdate"
column  bewertung   "review — allgemeine_informationen.bewertung"
join                "region_sales ⋈ game_platform: ventes_region joins jeu_plateforme on
                     game_platform_id=id"
```

And here is the prose that exists **one field away, invisible to both retrieval channels**:

```text
hockey.body    "Historical ice-hockey statistics database. It holds a master record of players
                and coaches with their biographical and career profile, per-season scoring and
                goaltending performance, team season standings and splits, playoff and Stanley
                Cup records, awards, and Hall of Fame inductions."
```

Three schema summaries are truncated mid-identifier and end in a literal `…`. The seed
(`corpus/seed.py:36`) truncates with `[:250]` too, which is the same defect at the source —
`corpus/validate.py:164` explicitly forbids truncation ("Rewrite it; do not truncate — the
indexed text is the treatment") and the producer does it anyway.

## 2. The hypothesis, and how it was wrong

> *"Our summaries are largely keyword soup, and that is hurting retrieval."*

Right about the diagnosis, **wrong about the treatment.** The obvious fix — replace the
identifier list with the prose — was measured and it *loses*:

| arm (BM25 only, no rewriting) | recall@1 | recall@3 | recall@10 |
| --- | --- | --- | --- |
| A as-is | 0.465 | **0.640** | 0.895 |
| B `schema.summary` ← prose | 0.377 | 0.632 | 0.825 |
| C `schema`+`table` ← prose | 0.447 | 0.570 | 0.860 |

And with the semantic channel on, where prose is *supposed* to win, full prose still loses at the
width production ships:

| arm (lexical + semantic, no rewriting) | recall@1 | recall@3 | recall@5 |
| --- | --- | --- | --- |
| A as-is | 0.640 | **0.851** | 0.904 |
| H full prose + table list | 0.693 | 0.825 | 0.886 |
| **E domain nouns added, dense** | **0.711** | **0.877** | **0.921** |

**The mechanism.** Those identifier lists are not noise — they are the *English meanings* of the
tables and columns (`generalinfo, geographic, location`), and under obfuscation they are the only
English in a routing document. Replacing them deletes the discriminating tokens. Meanwhile the
function words that prose brings dilute BM25's term frequencies and add nothing an embedder needs.

> ### ⚠ REVERSED 2026-08-05 (later the same day). Prose wins. Read this before §3.
>
> **Every arm above ran under two defects since repaired**, and both of them were load-bearing
> for the conclusion:
>
> * `nodes/facets.py` combined the channels with `max(raw lexical, raw semantic)`. BM25-after-
>   saturation occupies ~0.60–0.97 and cosine caps near 0.635, so over 32 244 documents both
>   channels scored, **the semantic channel won 0 times**. Every "semantic channel on" arm above
>   was effectively BM25-only — which is exactly why "prose is *supposed* to win" and didn't.
> * `retrieve/lexical.py` tokenised on `\S+`, keeping attached punctuation, so the lexical channel
>   scored nothing at all against all 57 schema summaries on **66.7%** of questions.
>
> Re-measured after the repairs, all 1 351 test questions, paired, one process
> (`runs/ablation/summary-form-1351-20260805.json`):
>
> | indexed text | gold-table coverage | schema recall@3 |
> | --- | --- | --- |
> | identifier lists (today) | 0.6405 | 0.9511 |
> | **the asset's own `body` prose** | **0.7026** | **0.9652** |
> | paired | **+6.21pp, +193 −117, p=1.9e-05, MDE 4.03pp** | +1.41pp, +29 −10, p=0.0034, MDE 1.30pp |
>
> Both deltas are **above** their own detection floor, and the 342-question screen's +6.11pp
> replicated as +6.21pp. Coverage runs the real `pass_two_retrieve` + `apply_budgets` path.
>
> **So the mechanism paragraph above is half right and its conclusion is wrong.** The identifier
> lists *are* the only English in a routing document, and prose's function words *do* dilute BM25 —
> that part holds. What was wrong is the inference: BM25 was the only channel that counted, so a
> BM25 cost read as a total cost. With the channels commensurate, the embedder is paid more by the
> meaning than it is charged by the function words.
>
> **What this does not say.** The prose arm indexes the corpus's existing *machine-written* `body`,
> not summaries authored for retrieval — so +6.21pp is a **lower bound** on deliberate writing, and
> the target below is still not "paste the body in".
>
> **And the cheap version is declined on purpose.** Widening ADR 0005 I1 to index `body` would
> collect this +6.21pp for zero authoring, and the maintainer's call is not to: I1 is what keeps
> `summary` (the retrieval treatment) and `body` (what the model reads on a hit) as separate roles,
> and merging them makes every future retrieval experiment also a context-size experiment. `body`
> also averages 1.8–2.4× `summary`'s length, which moves the corpus-global `avgdl` every BM25 score
> is normalised against and would invalidate every number measured before it. Reasoning recorded in
> `corpus/schema.py`'s module docstring.

**Raising the cap is not the answer either.** Arm G — full body *plus* full identifier list at a
600-character cap — scored 0.667 at recall@3, worse than arm E's 0.746 at 250. Density beats
length. `summary_max_chars` (250, `register/knobs.py:132`) **stays where it is.**

So the target is not prose *instead of* keywords, and not prose *added to* keywords. It is:

> **Dense, discriminating, domain vocabulary — and the identifiers — inside 250 characters, with
> the function words left out.**

Which reads like a keyword list, deliberately. The difference from today is *which* keywords.

> ### ⚠ The target above is superseded by the same reversal.
>
> "Leave the function words out" was a BM25 instruction, issued when BM25 was the only channel that
> counted. The revised target keeps everything that argument got right — the identifiers stay,
> density still beats length, the 250-char cap still holds and was never the binding constraint
> (gold schema summaries average **111** characters, table summaries **81**, and the 600-char arm
> scored *worse*) — and drops the one thing that was an artefact:
>
> > **A sentence a person would write, inside 250 characters, that states the grain, names the
> > domain in the words a question would use, and keeps every identifier the current summary
> > carries.**
>
> Concretely, for `restaurant.geografisch`, today versus the target:
>
> ```
> now:    geographic (geografisch): city, county, region
> target: One row per California city in geografisch (geographic): which county and
>         which region the city belongs to. Use this to answer which cities are in a
>         county, or which region a restaurant's city sits in.
> ```
>
> The second one is 233 characters, contains the physical name (so `corpus/validate.py`'s
> identifier check still passes), keeps `city`/`county`/`region`, and adds the grain and the
> question-shaped phrasing that the +6.21pp is paying for.

## 3. The floor you have to beat

> ### ⚠ The floor moved, and so did the metric it is stated in.
>
> The numbers in this section were measured under the two defects named in §2, and the density
> rewrite they describe turns out to have been **BM25 repair**: re-measured with the channels
> commensurate, densification buys ~0 on schema routing (paired, n=342: +10 −18 under the shipped
> rule, +0 −2 under semantic-decided, both null). Its +6.1pp was real and it was a lift on the
> channel that should not have been deciding.
>
> **The floor for path A is now the prose arm**, which is the same corpus text with no authoring at
> all — and the bar to beat is therefore *higher* than the old one and measured on the right thing:
>
> | | identifier lists | machine `body` prose | **authored target** |
> | --- | --- | --- | --- |
> | gold-table coverage, n=1351 | 0.6405 | **0.7026** | must beat 0.7026 |
> | schema recall@3, n=1351 | 0.9511 | **0.9652** | must beat 0.9652 |
>
> An authored rewrite that lands between 0.6405 and 0.7026 has lost to pasting the body in, which
> costs nothing. **Lead with gold-table coverage**, not `recall@k`: routing is already 0.965 and
> has ~3pp of headroom, while coverage has ~30pp and is what gates whether the SQL can be written
> at all. Reproduce with `runs/ablation/summary-form-1351-20260805.json`'s producer.
>
> Everything below is kept for the record and must not be used as an acceptance bar.

Arm E is a **mechanical** rewrite: take `body`, drop stopwords and duplicates, keep the first 14
content words, prepend them to the existing identifier list. No model, no judgement, ~40 lines. It
is reproducible from `docs/plans/`-adjacent scratch work and materialised as
`corpora/_variant-dense-20260805` (687 files rewritten, loads with 0 problems).

Measured through the **real compiled graph** (`eval.datalake.routing_recall`, `agent_model=None`,
embedder on, rewriters off) — not a replica:

| | baseline | dense (arm E) | delta |
| --- | --- | --- | --- |
| **`route_top_n=3`** | | | |
| all gold tables licensed | 0.632 | **0.693** | **+6.1 pp** |
| schema recall@1 | 0.632 | 0.693 | +6.1 pp |
| schema recall@3 | 0.851 | 0.877 | +2.6 pp |
| mean tables licensed | 13.7 | 13.1 | **−0.6** |
| **`route_top_n=1`** | | | |
| all gold tables licensed | 0.509 | **0.588** | **+7.9 pp** |

Artifacts: `runs/ablation/summary-density-top3.json`, `runs/ablation/summary-density-top1.json`,
`runs/ablation/dense-tool-top3.json`.

> **Every coverage figure in this section is superseded, and the instrument was the reason.**
> Three defects were found afterwards while checking the first delivery, all of them in the
> measurement rather than in any corpus:
>
> * `table_coverage` scored 13 of the 114 sampled questions as unconditional misses — their gold
>   is a constant-folded `VALUES` literal that reads no table — so the ceiling was 0.886 and every
>   figure was deflated by a fixed 11.4%. Corrected, and coverage on the same corpora now reads
>   ~0.71 rather than ~0.64 with no corpus change whatever.
> * `connect` built its join tree in process-hash order in three places, so `licensed` — and
>   therefore coverage — moved by about one question between processes. **Same-process paired
>   comparison was never affected**, which is why `--baseline` is now the only supported mode.
> * `densify_summaries.py` itself left a mid-word identifier fragment as the final entry in **518
>   of the 713** summaries it rewrote. Repairing that turned out **not** to move coverage
>   (+11/−7 questions, p = 0.48) — worth knowing, and the opposite of what was expected.
>
> **The current numbers, over all 1 351 questions, four arms in one process, paired McNemar on
> gold-table coverage** (`runs/ablation/salvage-arms-top3.json`):
>
> | arm | coverage | recall@1 | recall@3 | vs previous arm |
> | --- | --- | --- | --- | --- |
> | `gold` | 0.6430 | 0.6366 | 0.8216 | — |
> | `floor_fixed` — the mechanical rewrite | **0.7108** | 0.6876 | 0.8460 | **+114 −31, p ≈ 0** |
> | `salvage` — floor + 27 term bindings + 453 grain | **0.7181** | 0.6899 | 0.8475 | **+9 −0, p = 0.0039** |
> | `authored` — the first delivery, with its 27 prefixes | 0.7157 | 0.6921 | 0.8505 | +16 −10, p = 0.33 |
>
> Three things this settles. **The stopword regex is the dominant effect** — a net 83 questions,
> more than an order of magnitude larger than anything hand-written on top of it. **The term
> bindings are real** — 9 questions gained, *zero* lost. And **the 27 hand-written summary prefixes
> are worse than nothing**: dropping them turns a non-significant result (`authored`, p = 0.33,
> with 10 regressions) into a significant one (`salvage`, p = 0.0039, with none). `salvage` and
> `authored` are indistinguishable from each other (p = 0.63), so the prefixes bought churn in both
> directions and no net movement.
>
> **Ship `salvage`.** And do not quote the 114-question table above as a bar.

> **Both `corpora/` and `runs/` are gitignored.** The corpus is a build output of a curator run
> and the artifacts are measurements, so neither is in git and neither will be on a fresh clone.
> You need `../BIRD-Data-Obfuscation` for the questions and the `pg_rename_decoy` Postgres on
> `:5435` for the connector, and you rebuild the floor arm with the command in
> [§6](#6-how-to-run-the-gate). Every number in this brief is reproducible from those three
> inputs; none of them can be read out of the repository.

Two things to take from this. **Coverage went up while the net got smaller** — better targeting,
not more licensing, the same property [`retrieval-ceiling-2026-08-04.md`](retrieval-ceiling-2026-08-04.md)
found for the embedder. And a stopword regex bought +6.1 pp, so **a model-authored rewrite that
does not beat +6.1 pp is not worth its tokens.** That is the acceptance bar, not a target.

## 4. Where the corpus is actually empty

Counted, not estimated. This is the scope of "rewrite the entire corpus".

| finding | count | why it matters |
| --- | --- | --- |
| `column.body` is the tautology `Means 'X' (obfuscated to 'Y')` | **2 452** / 5 947 | restates the summary; zero information on hit |
| `column.sample_values` populated | **0** / 5 947 | declared field, nothing writes it; value-level questions have no anchor |
| `column.role` populated | **0** / 5 947 | same |
| `table.grain` populated | **0** / 656 | the field exists and the *prose* says "Grain: one row per…" instead |
| `table.rules` populated | **0** / 656 | the `## Must honour` channel; only `schema` uses it |
| `join.body` populated | **0** / 928 | on a hit a join renders nothing but its ON clause |
| `term.body` populated | **16** / 994 | |
| `term.related_terms` populated | **0** / 994 | |
| `term.binding` **absent** | **27** / 994 | `TagRule.binding_target` → untagged → **these do not vote in `route` at all** |
| `metric.body` populated | 242 / 399 | |
| `few_shot.bound_terms` populated | **0** / 5 000 | |
| `negative_example` assets | **0** | empty by construction on BIRD; leave it |
| `confidence` populated | 0 for schema/table/column/join | populated for term/metric/few_shot |

The 27 unbound terms are the one item here with a *known* retrieval consequence rather than a
suspected one.

## 5. The work, in order

Each item states its own acceptance gate. **Do not batch them.** The whole reason the numbers
above exist is that the plausible version of this work was measured and lost.

### Item 1 — `schema` and `table` summaries (57 + 656 assets) — **do this first**

The only item with measured leverage. Everything else is a hypothesis.

**Per schema, write the `summary` as:**

```
{name}: <8–16 dense domain terms: what this schema is about, in the nouns a business user
would use> . <the existing English table-meaning list, as much as fits>
```

**Per table:**

```
{english_meaning} ({physical_name}): <English column meanings> — <6–12 dense domain terms:
the grain, the entity, and what a user would ask this table for>
```

Rules for the vocabulary you add:

- **No function words.** No `the`, `of`, `a`, `is`, `contains`, `records`, `holds`, `database`,
  `table`. They cost characters and dilute BM25.
- **Nouns a user would type**, not catalogue vocabulary. `restaurant cuisine rating city county`
  beats `entity attributes classification geography`.
- **Discriminating over generic.** `root beer brand brewery` earns its place; `data records id`
  does not. If a term would fit twenty of the 57 schemas, drop it.
- **No synonym padding.** Two words for one concept is one concept and one wasted slot.
- **Keep the identifiers.** They are the only English in an obfuscated schema. Never trade them
  away for prose.
- **≤250 characters, and never truncate to get there.** Cut a term, not a word.

Also, while you are in these files:

- Move the grain out of `table.body` prose and into the `table.grain` field. It is a declared
  field with 0/656 populated and the prose is doing its job badly.
- Fix the 3 schema summaries that end in `…`.

#### 1a. Sibling schemas are the hard case — write against that

> **This section previously printed the held-out miss list** — six schema names with the schemas
> that displaced them, taken from a run scored on `test_final.jsonl` — and then set the acceptance
> gate on those same 114 questions. The first writer to receive it hardcoded exactly that list as
> its edit set, which was **compliance, not misconduct**: a brief that hands over per-question
> test outcomes and then scores the result on the same questions has closed the loop itself. The
> evidence is removed; the instruction below is derivable from the schema names alone and stands
> on its own. See [Who runs the gate](#who-runs-the-gate).

The lake holds sibling schemas: three hockey schemas, four film schemas, two food-inspection
schemas, four sales schemas. Generic domain vocabulary is *precisely* what cannot separate those —
every word that makes `movie_platform` look like a film database makes `movielens` and `movies_4`
look like one too, and they then take the shortlist between them. A schema with a near-twin is
therefore the case where a summary written in isolation fails.

So for any schema with a near-twin, spend part of the budget on **what distinguishes it**:

- `movie_platform` vs `movielens` vs `movies_4` — whose ratings, whose lists, which population,
  which era.
- `food_inspection` vs `food_inspection_2` — what the second one has that the first does not.
- `ice_hockey_draft` vs `hockey` — draft selections and prospects, against career season stats.

Derive the families yourself from the 57 schema names and their `body` text — there are about six —
then write each member's summary *against* its siblings rather than in isolation.

**Two things that look like discrimination and are not.** Both were tried by the first writer and
both are measured-null or worse:

- **Repeating a word already in the summary.** "California cuisine directory rating directory
  California restaurants … cuisine … rating" raises term frequency, not information. 62% of the
  tokens added that way were already present in the string they were prepended to, and the
  prefix-only corpus scored **exactly** the floor to four decimals.
- **Naming the sibling to exclude it** — `"NOT career HOF Stanley"`, `"NEVER football soccer"`.
  BM25 has no negation: the token is in the document and counts *for* it, so this makes a hockey
  schema more retrievable by draft questions. Write what the schema *is*, never what it is not.

**Gate:** `table_coverage` at `route_top_n=3` and at `route_top_n=1`, against the current floor,
paired in one process. **You do not run it and you do not see it** — see below.

### Item 2 — `column.body` for the 2 452 tautologies

Not the summary — the summary is already dense and 5 947 of them dominate the index, so changing
them is the highest-variance edit available and it gets its own measurement or nothing.

Replace `Means 'X' (obfuscated to 'Y')` with what the column *is*: the business meaning, its
unit or format, and **example values** where the train gold SQL shows them being filtered on.
The 347 columns that already do this are the model to copy:

```yaml
summary: review — allgemeine_informationen.bewertung
body: 'Review rating as a real number (e.g. 1.7, 2, 2.7, 4); higher is more popular.
  Filters seen: < 3, = 2, > 4; aggregations MIN/MAX/AVG.'
```

Populate `sample_values` and `role` while you are there — both are 0/5 947 and both are declared.

**Gate:** this changes `body`, not `summary`, so it cannot move routing and **must not be
measured by it.** It changes what the model reads on a hit, so the gate is EX on a live arm, which
is a paid run — flag it for the maintainer rather than running it. Do not claim a retrieval
improvement from this item.

### Item 3 — the 27 unbound terms

Bind each one, or delete it. An unbound term is untagged, and untagged does not vote in `route`
(`register/assets.py:72`). This is 27 assets and a mechanical fix.

**Gate:** `term` count unchanged or reduced; 0 unbound remaining; Item 1's gate does not regress.

### Item 4 — `join.body` (928) and `term.body` (978)

For a join: when to use *this* edge rather than the other one between the same pair of tables.
The corpus contains pairs with two distinct relationships and the summaries alone cannot tell them
apart.

For a term: what the phrase means to the business, and what it is *not*.

**Gate:** same as Item 2 — this is context quality, not retrieval. Do not measure it with
`recall@k`.

### Item 5 — `metric.body` (157) and `few_shot.bound_terms` (5 000)

Lowest leverage. Do it last or not at all.

## Who runs the gate

**The writer does not run the gate and does not see per-question scores.** The maintainer runs it.

This is not process for its own sake; it is the one thing that went wrong the first time. The
writer received six scored runs against the same 114 held-out questions, each with a distinct
`corpus_content_hash`, coverage climbing `0.667 → 0.684 → 0.684 → 0.693 → 0.702`, and reported the
last. Each individual step was reasonable. The sequence is hill-climbing on held-out data, and it
voids out-of-sample status no matter how the edits themselves were chosen.

So:

- The writer gets the corpus, the train questions, the schema list, and this brief.
- The writer may run `corpus.store.load()`, `tools/check_train_only.py`, and any static check.
- The writer may **not** run `tools/routing_recall.py` or read `runs/ablation/*`.
- The maintainer runs the gate **once**, on the delivered corpus, paired against the current floor
  in one process.

If the writer needs a feedback signal, it must come from something the test set cannot see — a
held-out slice of the **train** questions is the obvious one, and building it is cheap.

## 6. How to run the gate

The obfuscated Postgres must be up (`127.0.0.1:5435`, verified 2026-08-05). No model calls: with
`agent_model=None` the stub answer path serves while facets, routing, retrieval, resolve and
connect all run for real. Embeddings for changed summaries cost about **$0.01** for the whole
corpus; unchanged summaries are vector-cache hits.

Rebuild the floor arm (regenerable, so it is not checked in — 41 MB of derived text):

```bash
uv run --frozen python tools/densify_summaries.py --force
```

Then measure **both arms in one process**, which is the only way the embedder, the question
sample and the vector cache are held fixed:

```bash
uv run --frozen python tools/routing_recall.py --corpus-dir corpora/<your-variant> --baseline corpora/gold-semantic-layer-20260804 --top-n 3 --out runs/ablation/<name>-top3.json
```

It prints the delta, leads with `coverage`, and stamps each arm with its
`corpus_content_hash` — a directory name is not an identity, and a variant iterated in place keeps
its path while changing its meaning. **If both arms report the same hash you measured one corpus
twice**; the tool says so on stderr.

Defaults worth knowing: `--per-schema` is now **all 1 351 questions** (~12 min, $0 in model
calls). It used to be 2 per schema — 114 questions, a 95% interval near ±9 pp, wide enough to hide
every effect this tool is used to detect. Pass a small value for a smoke test, never for a result.

Materialise your corpus as a **sibling directory**, never in place.

`routing_recall.py` reports schema recall. **`table_coverage` is the number to lead with** —
[`retrieval-ceiling-2026-08-04.md`](retrieval-ceiling-2026-08-04.md) corrects an earlier document
for concluding from schema `recall@k`: *"those numbers are right; they measure the wrong stage."*
`routing_recall` rows now carry `licensed`, so `eval.datalake.table_coverage` reads them directly.

> **A defect fixed on 2026-08-05 while writing this brief.** `routing_recall` published only
> `licensed_schemas` and `table_coverage` reads `licensed`, so the free harness fed to the
> function documented as *"the EX ceiling"* reported `all_gold_tables_licensed: 0.000` for two
> arms whose schema recall was 0.851 and 0.877 — a publishable-looking number rather than an
> error. `table_coverage` now raises on a row with no `licensed` key, and two tests in
> `tests/eval/test_eval_contract.py` hold the producer and the consumer together. **If your gate
> reports 0.000 coverage, read that test before believing it.**

Always run **both arms in the same process**, against the same question sample, with the same
embedder. A number compared against one from another session is not a comparison.

## Hard constraints — do not break these

1. **The corpus is TRAIN ONLY. `test_final.jsonl` is held out.** `GOLD_LAYER_MANIFEST.json`
   records this as the reason the benchmark is fair. **You may read train questions and their gold
   SQL to author summaries. You may not read `test_final.jsonl`, ever, for any purpose.** The
   measurement harness reads it; the authoring must not. A summary written with a test question in
   context leaks the answer into the index and invalidates every number this repository has
   published since.

   **This is now checked, and the check must pass before you report anything:**

   ```bash
   uv run --frozen python tools/check_train_only.py corpora/<your-variant>
   ```

   It looks for a held-out `question_id` or `test_final.jsonl` cited in any `audit` block, a
   held-out question's wording inside any authored field, and a collision rate materially above
   the train-only control. It catches **copy-paste, not paraphrase** — a pass is the absence of
   the cheap failure, not a certificate. An earlier version of this brief said contamination
   "cannot be detected afterwards"; that is true of a reworded leak and was too strong.
2. **`summary` ≤ 250 characters and non-empty**, enforced by `corpus/validate.py`. Rewrite to fit;
   never truncate. Do not change `summary_max_chars` — arm G measured the longer cap and it lost.
3. **The identifier must appear in `summary`**, per `ASSET_REGISTER[...].identifier_fields`:
   `schema`→`name`, `table`/`column`→`physical_name`, `join`→both endpoints. Only the last
   dot-separated segment has to match (`corpus/validate.py:_bare`).
4. **Never touch `governance`.** `excluded=True` is human-only and there is no tool that writes
   it. Excluded assets are invisible to the analyst and must stay that way.
5. **Never soften a decoy.** 2 282 columns carry `reliability.status: suspect` with an
   evidence-based note. They are adversarial by construction — a decoy that reads as usable is
   worse than no warning at all.
6. **Physical names are the live obfuscated identifiers.** SQL emits these. English meaning lives
   in `summary` and `body`. Do not "correct" a physical name.
7. **`load()` must report 0 problems** after your edits. Run it before measuring anything —
   `store.load()` never raises for a bad item, so a broken file makes the corpus *smaller*, not
   loud, and a silently smaller corpus is how this project has published a wrong number before.
8. **Write to a new corpus directory.** Do not edit `corpora/gold-semantic-layer-20260804` in
   place. The baseline has to survive for the comparison to mean anything.
9. **Report what you did not do.** If you cover 40 of 57 schemas, say so with the list. A partial
   pass reported as complete is the one failure mode that corrupts the measurement rather than
   just limiting it.

## What is not measured, and must not be claimed

- **EX.** Everything here is the *ceiling* — whether the question was answerable under this
  retrieval. Nothing in this brief says the model converts a newly-reachable question. The live
  arm at the time of writing converted about one answerable question in four.
- **Whether the lift survives the facet rewriters. ANSWERED 2026-08-05: they do not interact.**
  All arms above ran with rewriting **off**. This bullet used to compare against the 0.877
  recall@3 in `register/facets.py`, and that figure is now **withdrawn** — it was measured under
  `max(raw lexical, raw semantic)` and the `\S+` tokenizer, both since repaired, at n=114 against
  a detection floor of 8.3pp. Re-measured paired at n=342 with only `facet_schema`'s query
  varying: the rewrite is null on both readouts (recall@3 +0.88pp with keyword summaries,
  −0.29pp with prose; gold-table coverage +0.64pp and 0.00pp; every p ≥ 0.45) and the
  interaction with document form is null too (−1.17pp / −0.64pp). So the summary-form lift and
  the query-form question are independent, and the rewriters neither stack with nor cancel this
  work. See the retired-table note in `register/facets.py`.
- **The 114-question sample.** Two questions per schema. Its 95% interval is roughly ±9 pp, so
  the +2.6 pp at recall@3 is inside the noise and only the +6.1/+7.9 pp coverage figures are
  worth arguing from. **This sample was a mistake and the tool no longer defaults to it** — all
  1 351 test questions fall inside the 57 covered schemas and the run costs no model call, so
  there was never a reason to sample. **Re-measure the floor on the full set before comparing
  your numbers to the ones in §3**, which are the 114-question figures.
- **Items 2, 4 and 5 have no measurement at all.** They change what the model reads, not what
  retrieval finds. Do not report a `recall@k` or a coverage number as evidence for them.
- **The baseline coverage figures here (0.632 / 0.509) are not comparable to the 0.503 in
  `retrieval-ceiling-2026-08-04.md`** — that used 171 questions at 3 per schema. Same metric,
  different sample.
