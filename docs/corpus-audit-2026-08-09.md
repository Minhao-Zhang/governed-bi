# Corpus audit — 2026-08-09

Audit of the rebuilt semantic layer against
[`docs/corpus-audit-brief.md`](corpus-audit-brief.md). Conformance rules V0–V15 were
**not** re-run as the deliverable; this report is what those rules cannot see.

| Item | Value |
|---|---|
| Corpus | `../BIRD-corpus` |
| Branch | `rebuild-20260808` |
| Commit | `02dd13b821faf811ccbf9a6fd7f653ca36ad23a3` |
| Packets | `scripts/corpus_rebuild/_build/packets/` (57/57 present) |
| Ground truth | `../BIRD-Data-Obfuscation/eval_dataset/` trap + rename maps; train evidence in packets |
| Held-out | Not opened (`test_final.jsonl`, `gold_result_hashes*`, `question_paraphrases.jsonl`) |

**Method in brief.** All 57 schema summaries were read as a routing set. Column bodies were
checked with a stratified sample of 200 numeric/range-claiming columns plus corpus-wide
scans of observed-range phrases. Terms/metrics were counted against every packet’s
`evidence_clauses`. Suspect-column wording used trap manifests through
`schema_rename_map.json` (train questions for decoy-name collisions). Joins were sampled
(n≈40, oversampled high-edge) against packet uniqueness. Parallel workstreams
([W1](df9e5418-9276-49b4-9e16-5fc303f36ea2), [W2](25f3bdf2-3f74-4eaf-9091-d57fa7eea181),
[W3](f2a4dd8b-62fc-44a9-b607-ad9099856124), [W4](0dc082c4-e6ab-458e-a56f-b301e2638261),
[W5](b9e4fd72-dece-47fa-95d0-4123cd09df13), [W6](7c9ae638-5258-4d86-be5c-f4cde642a124))
fed the counts below; every class has at least one hand-verified instance.

---

## 1. What is actually wrong (ranked by cost)

### 1.1 Schema-summary collisions (highest cost)

Schema `summary` is the only string that routes a question to one of 57 databases. Pairwise
content-word Jaccard on hand-picked clusters:

| Pair | Jaccard | Shared vocabulary | Tie-break that must land in the summary |
|---|---|---|---|
| `law_episode` ↔ `simpson_episodes` | **0.35** | air dates, awards, cast, crew, ratings, vote counts, keywords, TV episodes | Show title: *Law and Order* vs *Simpsons* (both present today — keep them dominant) |
| `food_inspection` ↔ `food_inspection_2` | **0.24** | food, health, inspection(s), violations | City: San Francisco vs Chicago (both present) |
| `hockey` ↔ `professional_basketball` | 0.19 | coaches, history, players, playoff, series, teams | NHL/WHA vs NBA/ABA (present) |
| `sales` ↔ `works_cycles` | 0.17 | **bicycle**, catalog, employee, product, sales | Retailer vs manufacturer / BOM / work orders (present, but “bicycle” is shared bait) |
| `retails` ↔ `car_retails` | 0.17 | customers, grouped, items, line, orders, retails | Wholesale/nation-region vs scale-model vehicles (present) |
| `movielens` ↔ `movie_platform` | 0.16 | directors, movies, ratings, users | Mubi lists/critiques vs Movielens demographics (present) |
| `hockey` ↔ `ice_hockey_draft` | 0.12 | hockey, ice, NHL, season, year | Career/Hall of Fame/WHA archive vs draft/prospects/CSS (both present; still compete on “NHL”) |
| `movielens` ↔ `movies_4` | 0.14 | cast, movies, ratings, revenue, runtime | Users/demographics vs budgets/crew/keywords — weakest movie pair |
| `regional_sales` ↔ `superstore` | 0.12 | profit, region, retailer, sales | Channels/warehouses/teams vs furniture/office/tech |

**Residual misnomer tax on `soccer_2016`.** The summary correctly names cricket / IPL /
wickets / Orange Cap
([`soccer_2016/soccer_2016.yaml`](../../BIRD-corpus/soccer_2016/soccer_2016.yaml)).
Packet samples confirm dismissal types (`lbw`, `stumped`, `caught and bowled`). But the
schema **id** tokenizes as `soccer` + `2016`, so BM25 still shares a sports token with
`european_football_2` (Jaccard 0.10 on `match|players|soccer|teams`). Cricket questions that
omit “IPL”/“cricket” and say only “soccer_2016”-adjacent words can still bleed. This is
cheaper than a full domain mis-ID (the content was written to the data, not the name), but
it is still a routing defect class the checker cannot see. **Hard summary≠content failures:
0/57.**

**Dead vocabulary.** Comma-heavy entity lists are common. Most list *business* entities a
user would type, not physical table-name rosters. Worst offenders: `movie_3` (films, actors,
categories, customers, staff, stores, inventory, rentals, payments), `student_club`, and
`works_cycles` (BOM/vendor/work-order roster with weak “Adventure Works / bikes” brand).
57/57 summaries also lead with a schema-slug echo, which spends budget on an identifier the
index already sees.
### 1.2 Lexicographic min/max trusted in column bodies

Packet `value_samples.min` / `.max` are produced by `sorted(..., key=str)` in
`scripts/corpus_rebuild/06_samples.py`. Writers were warned; many still wrote “observed
from / ranging from *lex_min* to *lex_max*”.

**Full sweep** of numeric columns with a strict observed-range claim (not example filter
phrases):

| | |
|---|---|
| Columns with such a claim | **227** |
| Contradicted by the same packet’s `sample_values` | **72 (≈32%)** |
| Of those matching packet lexico min/max | **69/72** |
| Concentration | **`address` 32 + `works_cycles` 32 = 89%**; then `card_games` 5, plus singles in `donor` / `regional_sales` / `video_games` |
| Schemas with any such claim | 16 (10 of them clean; **41/57** never assert the pattern) |

Narrower phrase-only scan (`ranging from <num> to <num>`): 35/81 contradictions, almost all
`address` — same bug, fewer phrase shapes.

**End-to-end proofs**

1. [`address/tables/tbl_address_zip_data.yaml`](../../BIRD-corpus/address/tables/tbl_address_zip_data.yaml)
   lines 195–200 (`white_population`): body “0 to **9935**”; examples include **48196**;
   packet `max='9935'` (lexicographic). Same shape across the demographic/payroll block
   (`households`, populations, mailboxes, payroll, `congress.land_area`).
2. [`works_cycles/tables/tbl_works_cycles_Address.yaml`](../../BIRD-corpus/works_cycles/tables/tbl_works_cycles_Address.yaml)
   lines 15–19 (`AddressID`): body “observed from **104 to 897**”; packet samples
   `16658, 19421, 19580, 21214, 24437` — all outside the asserted range. Writer copied
   lexico min/max.
3. [`works_cycles/tables/tbl_works_cycles_ProductVendor.yaml`](../../BIRD-corpus/works_cycles/tables/tbl_works_cycles_ProductVendor.yaml)
   (`MinOrderQty`): “1 to 75 … though larger values appear elsewhere” while the **same**
   sample list contains 100/500/5000 — the hedge is wrong.
4. `card_games` ids “ranging from 10032 to 9879” — lex order inverted vs numeric order.

**Estimate:** ~**70** column bodies corpus-wide assert a false observed range; nearly all are
lexico copies in **`address` + `works_cycles`**. Not a 57-schema epidemic — two writers’
failure mode.
### 1.3 Wrong closed domains and decoy vocabulary losses

**Invented enums vs samples (`european_football_2`).**
[`atributos_jugador.yaml`](../../BIRD-corpus/european_football_2/tables/tbl_european_football_2_atributos_jugador.yaml):

| Column | Body claims | Packet samples | Lines |
|---|---|---|---|
| `ritmo_trabajo_ataque` | high / medium / low | `y`, `None`, `le`, `norm`, `medium` (7 distinct) | 68–72 |
| `ritmo_trabajo_defensa` | high / medium / low | `_0`, `es`, `o`, `ean`, `ormal` (13) | 76–80 |

These are **renamed real** columns (`attacking_work_rate` / `defensive_work_rate`), not
decoys. Bodies describe FIFA’s clean enum; observed values are truncated garbage — filters on
`'high'`/`'low'` miss. Offside Trap itself is **fixed** (`clase_linea_defensores` names
`Cover` and `Offside Trap`, lines 193–197 of `atributos_equipo.yaml`).

**Decoy name wins the user’s words (6 true losses).** Among 1,351 traps mapped through
`schema_rename_map.json`, 12 train questions use a decoy/humanized decoy name; 4 are
term-rescued (`illustrator`→`artist`, soccer `team_name`, …); **6 have no term and the real
column summary does not reclaim the phrase**:

| Schema | Real column | Decoy | Real summary misses |
|---|---|---|---|
| `card_games` | `sets.code` | `set_code` | says “short uppercase identifier”, not “set code” (`tbl_card_games_sets.yaml:40–43` vs decoy `:160`) |
| `card_games` | `rulings.date` | `ruling_date` | |
| `hockey` | `statistiken.mannschaft_id` | `team_id` | |
| `student_club` | `budget.amount` | `budget_amount` | |
| `works_cycles` | `ProductCostHistory.EndDate` | `product_cost_end_date` | |
| `works_cycles` | `UnitMeasure.UnitMeasureCode` | `unit_measure_code` | |

E2E: train asks for “set code”; decoy is thin+suspect; nothing binds “set code” back to
`sets.code`. Sparse/partly-unreliable columns that train still needs: **not found** (0 train
gold SQL cites a decoy physical name).
### 1.4 Empty / thin glossary on rich-evidence schemas

Per-schema `len(evidence_clauses)` vs term/metric asset counts (all 57; 8,386 clauses total):

| Schema | Evidence | Terms | Metrics | Metric-shaped evidence | Notes |
|---|---|---|---|---|---|
| **`university`** | 133 | **0** | **0** | 33 | Total skip |
| **`regional_sales`** | 136 | **2** | 4 | 35 | Extreme under-deduction (68 clauses/term) |
| **`retails`** | 197 | 23 | **0** | 32 | Terms yes, metrics no |
| **`world`** | 56 | 4 | **0** | 13 | Same |

`university` remains the clearest skip: ranking-system names and university literals in the
packet with nothing in `terms/` or `metrics/`. Schema body/rules are strong
([`university/university.yaml`](../../BIRD-corpus/university/university.yaml)).

**Ambiguity gaps** (one English word → many columns, no disambiguating term pair):
`works_cycles` Title (Document vs Person), `simpson_episodes` role (credit vs award),
`professional_basketball` points (two tables). Contrast: basketball “round” is the gold
pattern (draft vs playoff terms both present).
### 1.5 Metric expressions that do not resolve on `base_table`

V9 only checks that `base_table` exists. After filtering SQL keywords / `DIVIDE`/`SUBTRACT`
helpers / string literals mistaken for columns:

- **465** metrics scanned; on the order of **~15–37** remain suspicious depending how
  aggressively string literals and cross-table joins in the body are excused.
- Concrete broken / incomplete cases:
  - [`sales/metrics/metric_sales_total_sales_value.yaml`](../../BIRD-corpus/sales/metrics/metric_sales_total_sales_value.yaml):
    `expression: SUM(menge * preis)` on `sales.verkaeufe`, but `verkaeufe` columns are only
    `verkaufid, verkaeuferid, kundenid, produktid, menge` — **no `preis`**. The body correctly
    says to join `produkte.preis`; the expression field still lies to any executor that trusts
    it alone.
  - [`ice_hockey_draft/metrics/metric_ice_hockey_draft_average_height.yaml`](../../BIRD-corpus/ice_hockey_draft/metrics/metric_ice_hockey_draft_average_height.yaml):
    `AVG(height_in_cm)` on `PlayerInfo`; height lives on `height_info` via join (body explains;
    expression does not).
  - [`mondial_geo/metrics/metric_mondial_geo_gdp_per_capita.yaml`](../../BIRD-corpus/mondial_geo/metrics/metric_mondial_geo_gdp_per_capita.yaml):
    `gdp / ren_kou` on `jing_ji`; `ren_kou` is on `guo_jia` (body admits the join).
  - Placeholder metrics: `thrombosis_prediction` `AVG(lab_column)` / `MAX(lab_column)`.

**Term bindings:** 591/593 resolve to an existing table/column id. Two misses:
`airline` term targeting `Air_Carriers_66c534.Description` (slug/name drift) and
`superstore` term bound to schema id `superstore` rather than a table/column.

### 1.6 Join cardinality understated; summaries templated

Sample of **40** joins (oversampled `works_cycles` / `mondial_geo` / `hockey`), checked
against packet `value_samples` uniqueness (null-aware):

| Bucket | n |
|---|---|
| Claimed + checkable | 28 |
| Wrong | **6 (21%)** |
| Classes | fan-out understated 3 · orientation flip 2 · soft grain 1 |

**Systematic bug — hockey year-only edges.** 14 joins with `.jahr = .jahr`. On that
predicate alone both sides are non-unique → **many_to_many**. Claimed labels understate
fan-out on **9/14 (64%)** (`one_to_many` or `one_to_one`). Bodies often say “combine with
sibling `mannschaft_id` join,” but the asset’s `on` is year-only — using it alone Cartesians.

Proof — [`hockey/joins/join_hockey_mannschaften_statistiken_sc_cdbc0dea.yaml`](../../BIRD-corpus/hockey/joins/join_hockey_mannschaften_statistiken_sc_cdbc0dea.yaml):

```yaml
'on': hockey.mannschaften.jahr = hockey.statistiken_sc.jahr
cardinality: one_to_many
```

Packet: `mannschaften.jahr` ~96 distinct/500; `statistiken_sc.jahr` ~14/284 → both non-unique.

Second failure mode: **orientation** written from the body narrative instead of
`left_table`→`right_table` (e.g. `cs_semester` registration→student claimed `one_to_many`
while left key is non-unique and right is unique → should be `many_to_one`).

**Summaries.** Hockey: **13/45 (29%)** exact stem clones after table-name normalization
(biography→season lines ×7; team-season year→every row ×6 — same family as the understated
cards). Mondial: soft “Connects …” prefix on 51/51, few exact clones. `works_cycles` joins
are authored (not TODO scaffolds) with filled cardinality; no exact-duplicate summaries in
the high-edge trees. `superstore` region-equality joins correctly marked `many_to_many`.
### 1.7 Cross-schema consistency (misleading divergences only)

Sampled date / money / id columns across ≥8 schemas.

- **Hard date-format falsehood:**
  [`card_games/tables/tbl_card_games_cards.yaml`](../../BIRD-corpus/card_games/tables/tbl_card_games_cards.yaml)
  lines 388–392 (`originalReleaseDate`) asserts “ISO 8601”; packet samples are
  `2015/12/29`, `2017/10/19`, `2000/6/1`. Same wire shape is correctly documented on
  `airline.Airlines.FL_DATE` as `'YYYY/M/D'`. Intra-schema trap: `sets.releaseDate` *is*
  real ISO — a model that just read that line will emit `YYYY-MM-DD` predicates that miss.
- **Date formats otherwise honest** when they differ (airline slash text, regional_sales
  `M/D/YY`, shipping `yyyy-mm-dd` text, codebase_comments .NET ticks).
- **No cents-vs-dollars trap.** Soft trap: many money bodies say only “currency” /
  “unit price” after peers teach USD (`sales.produkte.preis`, `works_cycles` UnitPrice,
  `car_retails.paiements.montant`, …).
- **Surrogate keys** usually careful (works_cycles separates display `SalesOrderNumber` from
  join `SalesOrderID`). Uneven “surrogate” vs bare “unique identifier” wording is a
  prior-transfer hazard, not a false “this int is an ISBN” claim.
### 1.8 Bird-documentation corruption — writers mostly caught it

Heuristic scan of packet `bird_documentation` for classic swaps (lat/long, URL-on-flag,
email-on-hash, min/max swap, Location←JobCandidate): **5** rows, all in the already-known
`mondial_geo` / `works_cycles` set.

| Packet row | Writer body |
|---|---|
| `mondial_geo.shan.wei_du` bird desc = “the longitude of its source” | Body: latitude of summit; pairs with `jing_du` — **caught** |
| `works_cycles.Vendor.ActiveFlag` = “Vendor URL” | Body: 0/1 active flag — **caught** |
| `ProductVendor` Min/Max descriptions swapped | Bodies assert correct min/max direction — **caught** |
| `Location.LocationID` = job candidate id | Body: location PK — **caught** |

So for the known trap class: **the four schemas that “caught it independently” remain the
ones that matter; this scan did not find additional schemas whose bodies still parrot the
swapped bird docs.** The corrupted packet rows are still landmines for any future rewrite.

---

## 2. What is fine

Evidence, not absence of looking:

1. **Domain identification on schema summaries is generally good** (0/57 hard
   summary≠content). Priority case `soccer_2016` describes IPL cricket; movie / retail /
   geo / food-inspection pairs carry their discriminators (Mubi, DVD rental, SF vs Chicago,
   bicycle manufacturer vs retailer, WDI vs Mondial vs world).
2. **`professional_basketball` “round”** disambiguation exists (draft vs playoff terms).
3. **Some decoy vocab rescues work** — `illustrator`→`artist`, soccer `team_name` (contrast
   the 6 losses in §1.3).
4. **Offside Trap literal** is present (not censored).
5. **Suspect thin summaries** do the intended ranking job; train gold SQL never selects a
   decoy physical name (366 sparse traps audited).
6. **Term bindings** nearly always resolve (591/593; narrative mismatch ~0% in a 50-term
   sample). Metric `base_table` always exists (465/465).
7. **Known bird-doc swaps** in `works_cycles` / `mondial_geo` were not copied into bodies
   (0/2 schemas with confirmed corruption still parrot it).
8. **Asymmetric PK→FK joins** in `mondial_geo` and true biography→season `spieler_id` joins
   in hockey look correct.
9. **Field contract awareness** shows up repeatedly: bodies stand alone; reliability notes
   on decoys use the “not dependable / do not use” template; schema rules on `university` /
   decoy twin keys are explicit.
10. **No cents-vs-dollars** money contradiction across the deep-read set.

---

## 3. Checker candidates (V16+) vs judgement

| Candidate | Predicate (sketch) | FP cost |
|---|---|---|
| **V16 — Lexico range vs samples** | Body matches observed-range `from X to Y` (numeric) and any packet `sample_values` numeric falls outside `[X,Y]`. | Low on that phrase family. Would fail ~70 columns, mostly `address`+`works_cycles`. |
| **V17 — Metric columns on base_table** | Bare identifiers in `expression` must exist on `base_table` (or be qualified names V9 resolves). | Medium: force join-open metrics to use qualified columns. |
| **V18 — Empty / starved glossary** | Packet `shape∈{metric,term_*}` count ≥ N and `terms+metrics` below a floor (0, or ≪ median). | Low–medium. N≈10 catches `university`; a clauses/term ceiling catches `regional_sales`. |
| **V19 — Closed-domain vs samples** | Body `one of: a,b,c` and ≥1 sample outside the set. | Medium (Yes/No vs Y/N). Catches `ritmo_trabajo_*`. |
| **V20 — Decoy phrase unreclaimed** | Trap decoy name (or `_`→space) appears in train Q/evidence, and no term synonym / real-column summary·body contains that phrase. | Low with word-boundary match + exclude Qs that already name the real column. Catches the 6 losses. |
| **V21 — Year-only join card** | Join `on` is a single low-cardinality temporal column equality on both sides and `cardinality` ∈ {one_to_one, one_to_many, many_to_one}. | Low–medium; hockey year edges are the prototype. Needs packet distinctness or DB. |
| **Not a rule — routing collisions** | Near-duplicate schema summaries. | Keep as audit / train `routing_recall` (tool currently hardcodes held-out). |
| **Not a rule — bird-doc corruption** | Fix upstream in `05_bird_docs.py`. | |
| **Not a rule — ISO vs slash dates** | Could be a narrow V19-like format check when body says “ISO 8601”. | Low FP if restricted to that phrase + sample regex. |

---

## 4. What could not be checked

| Gap | Need |
|---|---|
| Live routing recall@k | `tools/routing_recall.py` hardcodes `test_final.jsonl` — needs a train path switch before anyone runs it. |
| Join cardinality on live obfuscated DBs | Packet uniqueness covers year-edges; composite keys still need the rename+decoy instance. |
| Exhaustive bird-doc paraphrase beyond known swap patterns | Stronger aligner over ~5.9k columns. |
| Full metric dry-run executability | SQL binder per metric on the live DB. |
| Embedding-space collisions among 57 summaries | Vector index over schema summaries only. |

---

## 5. Suggested fix order (not done in this pass)

1. Strip or rewrite false observed-range bodies in **`address` + `works_cycles`** (~64 columns) — highest density, proven lexico copies.
2. Relabel hockey **year-only** joins to `many_to_many` (or fold year into the sibling `mannschaft_id` edge’s `on`).
3. Add terms/metrics for **`university`**; densify **`regional_sales`**; add metrics for **`retails` / `world`**.
4. Fix `ritmo_trabajo_ataque` / `_defensa` to document observed tokens (or mark unreliable).
5. Add reclaim terms for the **6 decoy-vocab losses** (start with `card_games` “set code”→`sets.code`).
6. Fix `card_games.cards.originalReleaseDate` format claim (`YYYY/M/D`, not ISO 8601).
7. Repair join-open metric expressions (`sales` total value, ice_hockey heights, mondial gdp/capita) or require qualified columns.
8. Soften `soccer_2016` routing: drop slug-lead “soccer” echo; open with “IPL cricket…”.
9. Fix dangling term bindings (`airline`, `superstore`); write Title/role/points ambiguity terms where evidence warrants.
