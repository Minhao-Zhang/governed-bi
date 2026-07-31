# Stage-by-stage error analysis — fixed-code test ladder (20260730T034522Z)

Run `20260730T034522Z-test-ladder-fixed2` · HEAD=3f599b6 +C11 · Opus-4.8 medium · test N=1351/arm.
Gold tables/SQL parsed from `test_final.jsonl` `sql_rename` (gold SQL against the obfuscated live DB).

> **Grader provenance (researched 2026-07-30):** our `hash_grade.py` normalisers
> (`normalise_result`, `normalise_result_strict`, `_canonical_json`, `hash_normalised_result`) are
> **code-identical to BIRD-Obfuscation's `pipeline/_db.py`** — we already run BIRD's exact EX
> semantics (lenient type-collapsing multiset equality + a strict floor, order-insensitive by a
> total-order sort). The only architectural difference: BIRD's `grade()` executes gold SQL fresh at
> grade time; we compare against precomputed gold hashes from the same normaliser and snapshot —
> equivalent. There is no separate BIRD grading script to adopt.
>
> **Correction to an earlier draft:** BIRD excludes **only** the 107 order-sensitive / exec-failed
> qids in `order_sensitive_qids.json` (26 of which are in our 1351). It does **not** exclude the 125
> frozen `VALUES(...)` gold questions — BIRD executes the VALUES literal and grades against it, so a
> frozen-gold miss is a **real miss** (e.g. `train_5315` "location of zip 95819": gold returns 2
> coordinate rows, model returned 1 — genuinely wrong). Excluding frozen gold would drop 90 legitimate
> misses and over-credit EX to 0.618, which is wrong. **The BIRD-comparable metric is EX with only the
> 26 order-sensitive excluded:**
>
> | arm | raw EX | **BIRD EX (lenient)** | BIRD EX (strict) |
> |-----|-------:|----------------------:|-----------------:|
> | baseline | 0.392 | **0.397** | 0.393 |
> | seeded | 0.470 | **0.473** | 0.469 |
> | curated | 0.585 | **0.588** | 0.580 |
> | curated_sme | 0.583 | **0.586** | 0.580 |
>
> (n_kept = 1325). Frozen gold is kept in all stage analysis below; only the 26 order-sensitive are
> dropped where noted.

## Method — the pipeline as a strict funnel

Each question is assigned to the **first stage it fails** (mutually exclusive), stages in execution order:

1. **Retrieval** — gold schema in `shortlisted_schemas`?
2. **Schema-pick** — router narrowed `routed_schemas` to gold? (`routed_hit`) — this is a *hard gate*:
   only 3/788 correct answers had `routed_hit=False`, and **0** correct answers recovered via the
   pooled corpus, so a wrong pick ≈ a wrong answer.
3. **Table coverage** — does the delivered SQL cover all gold tables?
4. **SQL logic** — right tables, wrong result: **wrong-shape** (row count ≠ gold) vs **wrong-value**
   (row count = gold, wrong columns/agg/calc).
5. **Delivery** — refused by a guardrail / no-coverage gate.

---

## 1. Stage waterfall across the ladder (BIRD basis: n = 1325, exclude only the 26 order-sensitive, keep frozen)

| stage | baseline | seeded | curated | curated_sme |
|-------|---------:|-------:|--------:|------------:|
| **OK** | 526 | 626 | 779 | **777** |
| 1 · retrieval miss | 59 | 68 | 34 | 32 |
| 2 · pick wrong | 102 | 105 | 89 | 96 |
| 3 · **table miss** | **281** | **139** | **60** | **58** |
| 4 · wrong-shape | 141 | 155 | 137 | 127 |
| 4 · wrong-value | 193 | 208 | 218 | 228 |
| 5 · refused | 23 | 24 | 8 | 7 |
| **EX** | 0.397 | 0.472 | 0.588 | 0.586 |

**The entire governance lift is one stage — table coverage — collapsing 281→58 (−79%).** Every other
stage is flat or drifts by single digits. Mechanism: the baseline (schema-only) constantly queries the
wrong/incomplete physical tables under obfuscated names (`zip_data`, `wurzelbier_bewertung`, …); the
curated join graph + terms tell the model which tables a question needs. Refusals also drop 23→7.
Conditional solve rate `EX|routed` climbs 0.453→0.650 while routing rates move <0.03 — governance is
almost orthogonal to routing and almost entirely about table→SQL grounding.

Note the mild adverse drift as corpus grows: **wrong-value rises 193→228**. A larger corpus gives the
model more columns/metrics to choose among, and it sometimes picks a plausible-but-wrong one it would
not have had access to at baseline. This is the cost side of governance and it is real, if small.

---

## 2. Stage 1 — Retrieval (32 fail). Nearly solved.
Gold schema in the shortlist for 1315/1351 (97.3%). The 32 misses are genuine retriever recall
failures — gold never surfaced. Not the main problem, but the hard floor on stages 2+.

## 3. Stage 2 — Schema-pick (96 fail). **Top addressable lever, and it is systematic.**

Of the 128 total `routed_hit=False` (answered, wrong), **96 had gold on the shortlist** — retrieval
succeeded and the pick threw it away. Where gold ranked when the pick was wrong:

| gold rank | 1 | 2 | 3+ |
|-----------|--:|--:|---:|
| count | 26 | 31 | 39 |

**26 cases picked wrong with gold at rank #1; 57 at rank 1–2.** Decomposing the pick vs the retrieval
ranking: a large share picked a schema ranked *below* gold — the LLM pick actively *overrode* a better
retrieval rank, the more damning failure (only 1 was a parse-failure fallback).

The confusions are **not random — they are near-twin schema pairs**, several symmetric:

| gold → picked | n | symmetric? |
|---------------|--:|-----------|
| mondial_geo ↔ world | 10 / 3 | yes |
| simpson_episodes ↔ law_episode | 8 / 1 | yes |
| regional_sales → superstore | 7 | one-way |
| food_inspection ↔ food_inspection_2 | 6 / 1 | yes |
| soccer_2016 → ice_hockey_draft | 3 | one-way |

Some schemas are **attractor magnets** for misroutes regardless of the question: superstore (12),
world (12), ice_hockey_draft (9), law_episode (8), food_inspection_2 (7), movies_4 (7). These are
generic-sounding schemas whose names/terms overlap many questions. Example: "When did Bulgaria gain
independence?" — gold `mondial_geo` at rank #1, router picked `world`. The two schemas both describe
countries; the pick can't disambiguate on surface terms.

**Fix:** the pick needs a tie-break that (a) does not override a rank-1 retrieval without strong
evidence, and (b) is hardened on the ~6 attractor schemas and the twin pairs. Worth up to ~57
questions (rank 1–2 cases) ≈ +4.75pp gradeable, no corpus change.

## 4. Stage 3 — Table coverage (58 fail). Governance already did the work.
91.4% coverage given a correct pick — down from 281 baseline failures. Residual concentrates in schemas
with many near-synonym tables under obfuscation: hockey (6), works_cycles (6),
professional_basketball (5), public_review_platform (4), synthea (4). Candidate for stronger
table-disambiguation terms on those specific schemas.

## 5. Stage 4 — SQL logic (355 fail — the dominant residual). Frozen gold KEPT (legitimately graded).

Structural diff of gold vs predicted SQL on the 355 stage-4 misses — the surprising result is the model
**over-elaborates** rather than under-specifies. (Some of these have frozen `VALUES` gold; those are
real misses, not exclusions — e.g. the model returned the wrong number of coordinate rows for a zip.)

| model added spuriously (pred has, gold doesn't) | | model omitted (gold has, pred doesn't) | |
|---|--:|---|--:|
| extra DISTINCT | 75 (21%) | missing DISTINCT | 19 (5%) |
| lookup-join + `LIKE` (pred LIKE, gold exact) | 26 (7%) | | |
| **used more tables than gold (over-join)** | **113 (32%)** | | |

So the earlier "missing DISTINCT" story was **backwards** — spurious `DISTINCT` (75) outnumbers missing
(19) 4:1. The characteristic stage-4 error is **over-joining to a lookup table + `LIKE` on a
description when gold used a direct code**, ignoring the evidence hint:
- `airline` "flights with Oklahoma as origin" — evidence literally says *"Oklahoma as origin refers to
  ORIGIN = 'OKC'"*, gold is `WHERE ORIGIN='OKC'`; model joined `Airports` and filtered
  `Description LIKE '%, OK:%'`. **26 (7%) of stage-4 use LIKE where gold used exact match.**
- **113 (32%) of stage-4 used more tables than gold** — needless joins that shift the result.

The remaining wrong-value errors are genuine column/aggregate/filter reasoning mistakes with fully
adequate context (see §9 — the corpus is *statistically identical* for right and wrong answers). No
mechanical rule covers these; they need model-side or targeted few-shot work and are the hard core.

## 6. Stage 5 — Delivery (7 refused). Negligible. All `no_coverage`/`guardrail`, down from 22.

---

## 7. The SME round — a 51% SQL rewrite for a net-zero result

curated 0.588 → curated_sme 0.586 (BIRD basis, n=1325). This near-identity hides an enormous perturbation:

- **SME changed the generated SQL on 678 / 1325 questions (51%).**
- Net flips: **59 helped, 61 hurt (net −2).**

The flip decomposes into two mechanisms (detailed with traces in §11): 12 helped / 10 hurt were
**routing flips** (the SME corpus delta shifted a near-tie schema-pick), and 47 helped / 51 hurt were
**same-schema SQL rewrites** (SME caveats/terms nudged the query). Both paths are coin-flips.

Concrete hurt cases show the same-schema mechanism — the fold nudges the model to *add* things the
question didn't need:
- `address` (`train_5154`): curated and SME emit the *identical* join+WHERE; SME added only a spurious
  `DISTINCT` → 1236 correct rows collapsed to 242 → wrong.
- `book_publishing_company` (`train_195`): SME injected 2 notes from a *different* schema and the router
  flipped to the wrong DB entirely — a routing flip, not a content effect.

**Conclusion:** folding the clarifications ledger is a high-variance, zero-mean intervention on this
test split. It genuinely rewrites half the queries but adds no systematic signal the curated corpus
didn't already carry. It reshuffles roughly as many questions wrong as right (see §11 for *why*: 404 of
465 changed columns are prose/confidence re-narration, and the 61 verdict flips all target decoys the
curated arm was already avoiding).

---

## 8. Per-schema EX (curated_sme gradeable, min 10 q)

**Worst:** professional_basketball 0.17, movielens 0.29, book_publishing_company 0.31, regional_sales
0.32, cars 0.33, thrombosis_prediction 0.35, simpson_episodes 0.38, works_cycles 0.39.
**Best:** social_media 1.00, codebase_comments 0.88, video_games 0.87, movies_4 0.83, university 0.83,
authors 0.81.

The worst schemas are exactly the stage-2 attractor/twin cluster (professional_basketball,
regional_sales, works_cycles, simpson_episodes, mondial_geo). **Schema disambiguation, not corpus
depth, separates the bottom from the top.**

## 9. Corpus is not under-serving the failures
Averaged over routed-hit-but-wrong vs correct: notes 0.4/0.4, few-shots 2.8/2.9, joins 17.9/17.0,
metrics 5.0/5.0, terms 3.9/4.0, context 21,980/20,898 chars — statistically identical. Stage-4 failures
had the same assets the correct answers did. Volume is not the lever.

## 10. Decoy resistance
`decoy_touch` across the ladder: baseline 143 → seeded 62 → curated/sme **1**. Obfuscation-trap bait is
a solved failure mode; not where the gap lives.

---

## Priority-ordered recommendations

1. **Report the BIRD-comparable EX = 0.586 (curated_sme lenient), excluding only the 26
   order-sensitive qids per BIRD §9.3.** Do NOT exclude frozen `VALUES` gold — BIRD grades it and 90 of
   those are real misses; excluding it (the discarded 0.618 figure) over-credits EX. Ladder,
   BIRD-lenient: baseline 0.397 → seeded 0.473 → curated 0.588 → curated_sme 0.586.
2. **Schema-pick tie-break (stage 2) — highest yield, ~+4.3pp.** 96 misroutes had gold shortlisted, 57
   at rank 1–2; many *overrode a better retrieval rank*. Hardening: don't override rank-1 without strong
   evidence; special-case the twin pairs (mondial_geo/world, simpson/law_episode, food_inspection/_2)
   and the attractor schemas (superstore, world, ice_hockey_draft, movies_4).
3. **Curb stage-4 over-elaboration — ~110+ questions.** Over-joining (113), spurious DISTINCT (75), and
   lookup-join+LIKE (26) dominate. A prompt/term rule: "when evidence gives a literal code, filter on it
   directly; do not join a description table or add DISTINCT unless the join fans out."
   **待重算 (M5 review A3):** the 113 over-join count includes ~69 frozen `VALUES(...)` gold
   rows whose empty table set makes `pred − gold` always nonempty. Excluding them drops
   over-join to ~41; this recommendation's magnitude must be recomputed before acting on it.
4. **Retriever recall (stage 1) — 32 questions.** Gold never shortlisted; distinct from #2.
5. **Table-coverage residual (stage 3) — 58 questions** on near-synonym-table schemas (hockey,
   works_cycles, professional_basketball).
6. **Deprioritise / redesign SME.** It rewrites 51% of queries for net −2 (§7, §11). 404 of 465 changed
   columns are prose/confidence re-narration, and the 61 that do flip a verdict aim at a failure mode
   already at 1 of 1351. It needs an intervention that adds signal
   curated lacks and reduces variance (e.g. only fold clarifications that flip a column verdict or
   correct a filter value, not ones that re-word a description or add a cross-schema note) before it can
   move EX.

---

## 11. Corpus diff: curated vs curated_sme — why EX barely moves despite 54/57 corpora differing

**The corpora are NOT identical** — 54/57 schemas differ on disk (only the 3 empty-ledger schemas —
professional_basketball, synthea, works_cycles — are byte-identical). So "SME does nothing" is wrong at
the file level. The near-zero EX effect happens *despite* real corpus changes, because of **what**
changes.

### What SME actually changes (465 of 5947 shared columns)

> **Correction, 2026-07-30, after this section shipped.** The counts in this subsection were wrong.
> It reported 9 verdict flips out of 656 touched columns. Recomputing the diff gives **61 flips out
> of 465 changed columns**. The 656 was the number of shared *table* files, not columns. The flip
> count is corroborated by this run's own `summary.json`
> (`corpus_census_deltas.curated_sme_minus_curated.n_columns_suspect: 61`), which was already on
> disk when the 9 was written. The section's conclusion holds: prose edits still outnumber decision
> changes. They do so by roughly 5:1, not 72:1. Anyone planning against the 9 was working from a flip
> count 6.8x too low.

Both corpora hold the same 656 tables and the same 5947 columns, so the fold only edits: nothing is
added or dropped at either level. 190 of the 656 table files differ, across 54 of 57 schemas.

| change kind | count | reaches the solver's SQL choice? |
|-------------|------:|----------------------------------|
| reliability status flipped `ok`→`suspect` | **61** | **yes**, can change which column is used |
| description and/or confidence changed, verdict unchanged | 326 | no, inert to SQL |
| reliability note text only | 77 | no |
| `role` field only | 1 | no |
| **columns differing in any field** | **465** | |
| (plus) new few-shots | 14 | maybe |
| (plus) new notes / terms / metrics | 31 / 1 / 1 | maybe |

Every flip runs one way: 61 `ok`→`suspect` and 0 back. The fold never rehabilitates a column the
curator condemned. The 404 non-flip column edits outnumber the flips 6.6 to 1; counting only the 326
that touch a description or a confidence score, 5.3 to 1.

Two table-level counts land exactly on the census, which is how the diff above was checked against
the harness rather than against itself: 33 columns and 13 tables gained a description they did not
have before, matching `n_columns_described: 33` and `n_tables_described: 13`. 108 table descriptions
changed in total.

So the dominant edit is still re-narration, on 404 of 465 changed columns. Concrete example
(`authors.Author`):

```
- description: Canonical author key; referenced by PaperAuthor.AuthorId.   confidence: 0.9
+ description: Canonical/stable author key per SME; referenced by ...       confidence: 0.7
```

Same verdict, reworded, confidence dropped. That pattern accounts for most of the 190 modified
`tables/` files. **That is why EX barely moves: SME mostly rewrites the prose of the corpus, and the
61 decisions it does change all point at columns the model was already avoiding** (see §10, where
`decoy_touch` is already down to 1 of 1351 before the SME round starts).

### The flip mechanism: two distinct paths (net −2 = 59 helped, 61 hurt)

The 61 hurt questions and the 61 column verdict flips above are unrelated counts that happen to
coincide.

| flip driver | helped | hurt |
|-------------|-------:|-----:|
| **routing changed** (SME corpus perturbed schema-pick) | 12 | 10 |
| **same-schema SQL changed** (SME caveats/terms nudged the query) | 47 | 51 |

Both paths are coin-flips. Traced examples:

**Path A — same-schema over-elaboration (`train_5154`, address, HURT):**
Question: "area code of the city with white population 1700–2000." Curated and SME produce the
*identical* join + WHERE; the **only** difference is SME added `DISTINCT`. SME injected 3 extra caveats
(24→27), which nudged the model to dedupe — collapsing 1236 correct rows → 242 wrong. A cosmetic corpus
change (3 caveats) flipped a correct answer via a spurious `DISTINCT`. 5 of the 51 same-schema hurts are
this exact pure-DISTINCT-added pattern.

**Path B — routing flip masquerading as an SME effect (`train_195`, book_publishing_company, HURT):**
Question: "Name all the authors for 'Sushi, Anyone?'." Curated routed to
`book_publishing_company.titres` (gold schema, correct). SME injected 2 notes from a *different* schema
(`note_books_1/2`) and the router flipped to `books.libro` — **wrong schema entirely**
(`routed_hit True→False`). The answer failed at stage 2 (schema-pick), not because of the clarifications
content. 10 of 61 hurts and 12 of 59 helps are these routing flips — the SME corpus delta shifted a
near-tie schema-pick, unrelated to whether the folded clarification was useful.

### Conclusion

SME's file-level footprint is real but **semantically thin**: 404 of 465 changed columns are prose,
note text or confidence, and 61 are verdict flips, all of them `ok`→`suspect` on a failure mode already
saturated at 1 of 1351. The measured net-zero EX is not "SME wrote nothing". It is "SME re-narrated
the curator's existing decisions and occasionally perturbed a near-tie schema-pick, with both directions
equally likely." To make SME move EX it must produce **decisions the curator did not already make**
(status flips, new joins, corrected filter values) rather than re-describe them — and it must stop
emitting cross-schema notes that destabilise routing.
