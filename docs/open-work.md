# Open work

What is known to be unfinished, with the evidence for each. Anything closed is deleted from
this page rather than struck through — the git history is the record of what changed, and a
page that carries both states is a page nobody trusts as a to-do list.

Nothing here is carried from an earlier document on the strength of having been written down.
An item survives only if it was re-verified against the current tree, the corpus every number
here is measured on (`../BIRD-corpus` @ `30872d3`), or the 2026-08-09 run artifact. Claims that
could not be re-verified were dropped, not demoted.

`30872d3` is the treatment identity and not that sibling's current tip. Its HEAD is `74ff80c4` as
of 2026-08-22, and the two commits in between add only `LICENSE` and `README.md` — no asset
changed, so the hash is still the right name for the content and the corpus items below still read
on the tip. What this tree *loads* is BIRD, and has since 2026-08-23: `.env` sets
`GOVERNED_BI_CORPUS_DIR=../BIRD-corpus` and leaves `GOVERNED_BI_PG_DSN` unset, so
`credentials.PG_DSN_NAMES` falls through to `PG_RENAME_DECOY_DSN` on port 5435 — the obfuscated
lake. The facilities pair (`../MS Fabric Facilities/corpus`, the 5432 warehouse) is commented out
beside it. A run started here today is on BIRD without touching anything.

Binding design lives in the [ADRs](adr/). This is a work list, not a decision record.

Calls taken while working the 2026-08-10 audit were written up as 30 choices, each with the
alternative that was rejected and what would reverse it, and four of them retract their own
earlier reasoning in place. That note is not on this tree: `2396ca2` deleted
`git-history:docs/analysis/decisions-2026-08-10.md` on 2026-08-20 along with the rest of
`docs/analysis/`. Read it out of git — `git show 2396ca2^:docs/analysis/decisions-2026-08-10.md` —
before re-opening one of those calls, because nothing on this page carries that reasoning and the
argument you are about to make may already be there with the measurement that killed it.

---

## 1. Engine — measured, with a known ceiling

Current arm: **v4**, engine `3c0079a`, corpus `30872d3`, **EX 0.676** (clean 0.6762).
438 failures. Method and per-case diagnosis: [failure modes](failure-modes.md).

Where the remaining failures are. The six rows partition the 438 — every failure lands in
exactly one — so the coverage-based rows below are stated again as cross-cutting totals,
because those are the numbers §1.5 and §7 are about:

| bucket | n | nature |
|---|---:|---|
| full-coverage answered wrong | **259** | genuine semantics — the generic text-to-SQL problem |
| answered, frozen-literal gold | 75 | dataset defect, unwinnable |
| capped | 49 | the agent spent all five attempts without a passing statement |
| answered, coverage incomplete | 31 | retrieval |
| refused | 20 | none with full coverage |
| clarification | 4 | all zero-licensed |

These are the post-fix figures. The 2026-08-24 `table_coverage` repair described in §1.5 moved
two of these rows — 257 → 259 and 33 → 31 — and this table carried the old pair until
2026-08-25 while §1.5 twenty lines below it carried the new one.
[`failure-modes.md`](failure-modes.md) §1 has the before/after.

Across all outcomes: **71** failures had incomplete table coverage and **85** had a
frozen-literal gold. The `refused` and `capped` rows are where those two overlap the
outcome buckets — 19 of the 20 refusals had partial or no coverage and the twentieth
had a tableless gold, and 26 of the 49 capped turns were not fully covered either.

### 1.3 Four turns licensed nothing at all

Four of 1 351 turns routed zero schemas and licensed zero tables. All four asked a
clarifying question — the correct response to an empty context, and the reason they are
**not** an agent-behaviour problem. They are a retrieval defect, isolated and small:
`licensed` has a median of 25 and these are the only rows below 5.

### 1.4 Twenty-two answers were written against the wrong schema

Failures where the prediction and the gold statement share no schema at all. The pairs are
the semantically adjacent decoy sets, `mondial_geo ↔ world` in both directions:

| gold | predicted | n |
|---|---|---:|
| `regional_sales` | `car_retails` | 3 |
| `mondial_geo` | `world` | 2 |
| `world` | `mondial_geo` | 2 |
| `movie_platform` | `movies_4` | 2 |
| `books`, `book_publishing_company` | `car_retails` | 2 |
| `address`, `beer_factory` | `works_cycles` | 2 |
| nine more, one each | — | 9 |

The gold schema was routed in 20 of the 22. This is disambiguation **inside** the licensed
set, not routing recall — the agent is handed tables from several schemas and picks the
wrong one.

### 1.5 Seventy-four questions never had their gold tables licensed

Table coverage on the v4 arm is **0.940** — 1 150 of 1 224 questions with a real gold
statement had every gold table licensed. The engine answered 3 of the uncovered 74 correctly
and missed the other 71, which is the cross-cutting coverage total under §1.

Recomputed 2026-08-24 on the same artifact after a metric fix — `table_coverage` was comparing the
gold statement's identifier against `licensed`, which holds asset **ids**, so the one table of 656
whose id is not its own name (`airline."Air Carriers"` → `airline.Air_Carriers_66c534`) read as
unlicensed on all five questions that had licensed it. It replaces 0.936, 1 145, 6 and 73.
`docs/failure-modes.md` §1 carries the full before/after. The **accuracy pair moved with it
and the engine did not**: 0.7555 (n=1,145) / 0.7131 (n=1,272) — the figure six documents,  <!-- [retired]: the superseded accuracy pair, quoted as history; register/citations.py -->
`tools/reproduce_observation.py::CLAIM` and one test carry — is now **0.7548 (n=1,150) /
0.7126 (n=1,277)**, same arm, same rows, fixed instrument. The test docstring records why, because
a figure that moves without a stated reason reads as a second measurement.

**This line claimed "all ten sites are updated" and two were not** (found 2026-08-25):
`ui/lib/review-copy.ts`'s module header and `ui/components/review/reproduce-panel.tsx`'s both still
said 0.7555, and the first of those sat 196 lines above the reader-facing string that said 0.7548 —  <!-- [retired]: the superseded accuracy pair, quoted as history; register/citations.py -->
a comment contradicting the code in the same file. Both are fixed, and a *count* of updated sites is
not evidence that they were updated, which is why the count is no longer the instrument.

**Closed on the `ui/` side, and only there.** `ui/scripts/check-review-copy.ts` now fails CI if any
accuracy-shaped number appears under `ui/components/review/` or in `ui/lib/review-copy.ts` — in a
comment as readily as in a string — that `tools/reproduce_observation.py::CLAIM` does not carry. It
was written against the planted regression: restoring 0.7555 fails it, naming both the found and the  <!-- [retired]: the superseded accuracy pair, quoted as history; register/citations.py -->
expected figures. `tests/feedback/test_the_reproducer_answers_one_question_for_nothing.py` already
pinned the Python side to `CLAIM`, so the two live ends now agree by construction.

**Closed on the docs side too, and `grep` is no longer the instrument.**
`tests/conformance/test_the_prose_states_the_accuracy_pair_the_claim_carries.py` keys the four
documents to `CLAIM` and restates no figure of its own — it reads both figures and both populations
out of that one literal. Sweep one: every block of prose under `docs/`, `src/`, `tools/` and
`tests/` containing the phrase *measured accuracy* — 8 blocks in 6 files, this paragraph among them
— may state only the numbers `CLAIM` carries (0.7548 / 0.7126, over n=1,150 / 1,277), and a block
that uses the phrase
while naming neither figure fails as well. Sweep two: `glossary.md`, `return-path.md`, `adr/0015`
and this page must each state **both** figures, which is what catches a paraphrase that never
repeats the anchoring phrase — `glossary.md` is the case that needs it. Every floor is asserted
(D13): the walk, the block count, the file count, and these four paths being in the scanned set.

**Written against the planted regression.** Restoring the old figure in `glossary.md` fails both
sweeps, naming the file, the line, the number found and the numbers `CLAIM` carries; planting the
old denominator in `return-path.md` fails the first the same way; and repointing the walk at
`docs/adr/` fails on the floor instead of passing over 16 files.

**The retired spelling is gated separately, and now registered.** A *retired* figure reappearing is
deliberately not checked in the conformance test, because the mechanism already exists:
`register/citations.py::RETIRED_CLAIMS` plus `tools/check_citations.py` fail on a declared retired
pattern anywhere in `src`, `tools`, `docs` or `tests`, with a `[retired]` line marker for a
deliberate quotation. A second denylist in the conformance test would have been a second answer to a
question this repository already answers in one place. The entry landed 2026-08-25 — `0.7555`,  <!-- [retired]: the superseded accuracy pair, quoted as history; register/citations.py -->
`0.7131`, `n=1,145`, `n=1,272` — and the gate immediately named all nine lines that quote the old  <!-- [retired]: the superseded accuracy pair, quoted as history; register/citations.py -->
pair as history, five on this page, one in `return-path.md`, three across two test files. Each now
carries the marker. The populations are in the pattern and not only the accuracies, because a
document that updates one number and not its `n=` is the same staleness wearing a fresh-looking
figure.

Three gates now, one question: `check-review-copy.ts` for the client, the conformance test for
`docs/`, and `check_citations.py` for the stale spelling anywhere. The general form of the defect is
§3.10's: a figure with more than one home and no gate between them.

**What is still not gated.** The anchor is a phrase, so a paraphrase that drops it and states one
figure alone is invisible, and so are other spellings (`75.48%`, a space-grouped `n`) unless they
match a retired pattern.

This is a **licensing figure, not a delivered one**; see §3.3 for what the char budget drops on
top of it. Concentrated in `works_cycles` (7), then `law_episode` and `superstore` (5 each).
`airline` was in that list with 5 until the fix above, and all five were the same mis-compared
table — the schema has no uncovered question left, which is worth knowing before anyone curates it.

This is still the largest *winnable* bucket after the 259 semantic errors, and unlike those it
is corpus and retrieval work rather than generic text-to-SQL.

### 1.6 Twelve capped turns had every gold table and still built no join

Twenty-three of the 49 capped turns had full coverage; in 12 of those the gold answer needs more
than one table and the final draft joins none. The tables were in context. What is missing is
relationship grounding, not table budget — raising `table` budget above 8 does not address it.

The other 26 capped turns had partial coverage, no coverage, or a tableless gold, so the capped
bucket is about half a retrieval problem. Concentrated in `movie_3` and `works_cycles`, 8 each.

### 1.7 Three answers were delivered with no SQL at all — the label is fixed, the behaviour is not

`outcome: answered` with an empty `generated_sql` — the model answered from the delivered
schema descriptions without querying. For a governed system this is the worst available
failure: an answer with no auditable statement.

**Half of this is closed (2026-08-18).** It was *not* a declared state, as this section used to
claim. `stamp._path_signals`'s `path_kind == "answered"` fall-through hardcoded `has_sql=True`
and never read `state["generated_sql"]`, so the register's word for the turn was the one word it
must not have been. Those turns record `Outcome.no_sql` now, derived from `execution.terminal`
(ADR 0006 §5), and the register no longer calls a statement-less turn an answer.

**What is still open is the behaviour.** The engine can end a turn without querying, and nothing
stops it or decides that it should. ADR 0013's policy is the machinery for that decision, and
this case is not in its vocabulary — its rules run *before* the agent, and this one is only
observable after. Either a rule that withholds a statement-less turn, or an accepted decision
that prose over the delivered context is a legitimate answer; today it is neither, just named.

The old figure ("three answers") is from the 2026-08-09 arm and is not the boundary count. Across
the 9,459 rows in `runs/eval/*.jsonl` there are **23** statement-less turns, and in every one
`answer_text` is null. All 23 carry the old `answered` label, so any rate computed across
2026-08-18 mixes two taxonomies — `measure/selective.py::DECLINED` names which figures move.

---

## 2. Corpus — from the 2026-08-09 audit, items not yet applied

The audit's other findings (false observed ranges, Cartesian join labels, invented enums, a
missing glossary, the `card_games.originalReleaseDate` format claim) are fixed in `30872d3`.
These are not:

1. **Metric expressions that do not resolve on `base_table`** — `sales` "total sales value",
   `ice_hockey_draft` heights, `mondial_geo` gdp/capita. Either repair them or require
   qualified columns. Only the first was re-checked, 2026-08-22:
   `sales/metrics/metric_sales_total_sales_value.yaml` is `SUM(menge * preis)` over
   `base_table: sales.verkaeufe`, whose columns are `verkaufid`, `verkaeuferid`, `kundenid`,
   `produktid` and `menge` — `preis` lives on `produkte`, which the metric's own body says to
   join. The other two are carried unverified.
2. **Six decoy-vocabulary losses** — reclaim terms; start with `card_games` "set code" →
   `sets.code`.
3. **Thin coverage** — terms and metrics for `university`; densify `regional_sales`; metrics
   for `retails` and `world`.
4. **`soccer_2016` routing summary** — the description no longer matches the text and nobody
   acted on it. `soccer_2016/soccer_2016.yaml` is unchanged since the corpus rewrite (`e34c90eb`)
   and its summary reads "`soccer_2016` tracks Indian Premier League cricket seasons: …", which
   names IPL cricket in the same clause as the identifier. The old wording — "leads with a slug
   echo of 'soccer'; it should open with 'IPL cricket…'" — is withdrawn. What is left is a
   judgement rather than a defect: whether opening with the schema identifier at all costs
   anything on the lexical channel the §1.4 decoy confusions run through. Open as that question
   and nothing more.
5. **Dangling term bindings** in `airline` and `superstore`.
6. **`ritmo_trabajo_ataque` / `_defensa`** document tokens that were not observed.

Candidate conformance rules the audit proposed and nobody has written: a check that bare
identifiers in a metric `expression` exist on `base_table` (would force §2.1), and a check on
closed-domain claims.

---

## 3. Instrument

### 3.1 `--replay-routing` works, and the one arm that most needed it did not use it

Now exercised on three arms. v4 and v5 both pin to `proxy_v3_fold_opus_high_corpus30872d3.jsonl`:
the artifact offers 1 345 pinnable questions of 1 351 and **1 342 turns on v4 actually ran on the
pinned shortlist** (1 340 on v5, 1 333 on v4-reflect) — the three-to-twelve row gap is
clarifications that ended before `route_node`.

Those three counts are now *produced* rather than asserted. Every artifact in `runs/eval/` was
written when `routing_pinned` recorded the driver's intent, so the shipped one-liner returns 1 345
on all three arms alike; `eval/replay.py::pin_realised` reads the corrected outcome semantics off
an old-semantics row and prints both, plus an independent check that never reads the flag —
ordered-exact agreement of `schemas` against the pin source, which reproduces 1 342 / 1 340 /
1 333 exactly, with **zero** rows holding the pinned schemas in a different order.

Mean residual Jaccard is **0.7049** on v4, 0.7029 on v5 and 0.6997 on v4-reflect, against
**0.5719** for the unpinned run1/run2 pair. The 0.579 previously printed here was not the same
statistic: it was the mean over *every* compared row including the 33 identical ones, which
`eval/replay.py::licensed_drift` deliberately does not compute because rows that score 1.0 by
definition drag it upward. Both sides now go through `replay.drift_against`, so the contrast
cannot mix them again. The error flattered the unpinned baseline, so the conclusion is unchanged
and the printed comparison was not one.

It buys real resolution — the pinned v3-fold → v4 comparison is discordant on 9.3% of questions
against the unpinned null's 12.7%, which is SE(net) 0.83pp instead of 0.97pp.

Both Jaccard figures moved on 2026-08-11 and neither is a re-run: v4's was 0.7020 under a
baseline that included the six rows the pin deliberately skipped, and v5's moved +0.0034 from
the same cause. See §3.7.

**The v3-fold arm itself did not pass the flag.** So v3-fold vs v3-pinned differs by the fold
fix *and* by routing. Routing churn is unbiased (run1 vs run2: net −12, p = 0.40), so the
+5.3pp attribution stands, but the discordance is inflated — 189 against the null's 172. Every
arm since has passed it; it costs nothing.

### 3.2 The corpus is versioned and still not rebuildable

`../BIRD-corpus` is in git and cannot be regenerated from this repository. This engine
loads a versioned tree; it does not write one. Mechanical structure and prose both live
in that sibling checkout. Versioned is not reproducible-from-source, and no document
may describe it as such.

### 3.2a The resolver's `r_ambiguous_fold` family, and the re-check it is still owed

Not a closed item wearing an open item's clothes — the open part is the last line. Two
`r_ambiguous_fold` holes (a derived-source alias; a self-colliding bare name) were fixed
2026-08-12, and a third of the same family, a CTE-scope hole, is written up in
`git-history:docs/analysis/binding-scope-and-statement-timeout-2026-08-19.md`, deleted by
`2396ca2` — `git show 2396ca2^:` that path.

**What is open:** all three were measured on the corpus of the day, and the BINDING layer's answer
depends on what the corpus declares. Re-check both properties before trusting the resolver on a
rebuilt corpus. §3.11 cites this section for the other half of the lesson — the first repair was
tree-wide, scored 2/46 on the adversarial suite's benign half, and was withdrawn for it.

(The first sentence of this section was physically damaged until 2026-08-25: a lost backtick left
it reading "Both \_ambiguous\_fold holes" across a line break, with the leading `r` eaten.)

### 3.3 The char budget is not the binding constraint

Measured on v4, now that `context_evicted` survives the turn: the 80 000-char budget bit on
**18 of 1 351 turns (1.3%)**, dropping bodies only and never a whole table. The advice that the
budget already binds and must not be cut is **withdrawn**; it rested on an offline
reconstruction, not on this measurement.

So there is headroom. Whether to use it is a question about whether the content earns its
place, not about whether it fits. `agent_core` carries **98.7%** of the arm's 74.3M input
tokens at an average of 22 308 per call, and the node makes 2.44 calls per turn — so **1 943
of its 3 290 calls (59.1%)** are the second and later call within a turn, re-sending a context
the model has already seen.

`usage` writes one aggregated `agent_core` record per turn, so a per-call token split is not
recoverable from the artifact; the call counts above are exact and any token figure attributed
to *repeat* calls specifically is an average, not a measurement.

### 3.4 Held back on purpose

1. **Comparability: run1, run2 and v3-pinned ran on `ba8cef2` or earlier.** `r_ambiguous_fold`
   was narrowed after them and it moves ~119 turns, so those three are **not** paired-comparable
   with anything measured since on what the fold touches. **v4 is the control for new arms**,
   and v3-fold is the artifact new arms pin their routing to.
2. **A hard cancel after the agent's grace period can leave an executed statement out of the
   ledger.** A turn killed between `execute` and the ledger write records no attempt for SQL
   the database actually ran. Rare, and it makes the ledger under-count rather than invent —
   but "the ledger is the record of what ran" is a property this repository leans on.
3. **Comparability: the `run_query` tool reply now names the attempt budget, on the default
   arm, and no field records that.** This entry used to say the opposite — that telling the
   agent its remaining budget was a cheap fix held back because it "would become a second
   variable in the next arm", to be applied with its own A/B. It was applied anyway, on
   2026-08-20, as an accepted confound rather than an oversight: `serve/tools.py::_attempt_budget`
   appends `attempt N of M` to every `run_query` reply. Tool replies are outside the prompt
   registry, so `prompt_set_hash` is byte-identical across the change. **`v4` measured before
   2026-08-20 is therefore not paired-comparable with `v4` measured after**, and the artifact
   cannot tell you which side of the line a row is on — only its commit can. Treat the earlier
   `v4` artifacts as a separate arm. See
   [measurement — what `prompt_set_hash` does not cover](measurement.md#what-prompt_set_hash-does-not-cover).


### 3.5 Cost per arm is not in the artifact

`usage` carries tokens. Price is the provider's number and `measure/price.py` is deleted, so an
arm's cost is not recoverable from the artifact alone.

### 3.6 What `--resume` still cannot tell apart

The guard now reads the artifact back before extending it: both treatment hashes, every
comparability knob, the question ids, and whether the rows' routing was replayed. An artifact at
`--out` gets the same treatment as one the tag named, an existing artifact without `--resume`
refuses instead of appending a second population into it, and `--replay-routing` is in the tag.
The refusal now names `--truncate`, not `--force-fresh`: for a while `--force-fresh` both relaxed
the sibling-artifact abort *and* silently deleted a completed artifact at `--out`, which on this
dataset is hours of paid model calls behind one flag documented as doing neither. The two meanings
are two flags, the destructive one prints the row count it is discarding, and `--truncate` with
`--resume` is refused as the contradiction it is.
Two things it still cannot see, stated because a guard whose reach is overstated is worse than
none:

1. **A dataset whose gold statements were edited under unchanged question ids.** The row carries
   `split` and `question_subset`, which identify the *file* and the *set of ids*, not the
   statements. `gold_fingerprint` is attached after the resume decision. A dataset with a
   different question set is caught.
2. **A pinned run resuming an unpinned artifact.** Only the opposite direction is sound:
   `routing_pinned` is an outcome, so a `true` can only come from a replayed run, while a pinned
   run whose kept rows all abstained before routing carries no `true` either. Recording the
   driver's *intent* would need a knob nobody has declared.

**A consequence worth expecting rather than discovering.** Now that `git_sha`, `diff_sha256` and
`working_tree_dirty` are on every row, a run resumed across *any* commit or *any* uncommitted
edit makes `measure/gates.py::_knobs_resolved_gate` report two configurations in one arm, and the
driver prints that a gate did not pass. That is the declared purpose of a resume-drift key and
not a regression — but the key covers the whole working tree, including a docs edit, so the gate
will fire on changes that could not have moved a number. The gate names which key disagreed; the
judgement of whether it mattered is the reader's, which is the trade `diff_sha256`'s note takes.

### 3.6a A clarification turn carries no treatment identity

Every row in the 2026-08-09 artifacts whose `corpus_content_hash` is `None` is a zero-licensed
turn that ended in a clarifying question — 6 of 6 in v3-fold, 8 of 8 in v3-pinned, 4 of 4 in v4,
5 of 5 in v5, 13 of 13 in v4-reflect. A turn that terminates before routing never reaches
whatever stamps the identity, so `None` here does not mean "written before the field existed";
it means the field has a path it is not written on.

It is 0.4% of rows and all of them are abstentions, so no headline number moves. Every reader
now knows the shape — the resume guard counts them instead of warning, and `reconcile` treats
`None` as silence rather than as contradiction — but the underlying hole is in `serve/`, not in
the instrument, and closing it there would make those rows provable rather than merely excused.

### 3.7 What the refusal counts still cannot separate

The two fields that reported intent rather than outcome are fixed and the corrections are
quantified in §3.1 and in `eval/replay.py::licensed_baseline`. The histogram's own shape is fixed
too: `eval/report.py::refusal_histogram` now returns `n_refused` out of `n_rows`, a `by_stage`
split, an `unattributed` bucket for a `refused_by` string in no register, and a `no_reason`
count — and `refusal_report_lines` prints the total in its header, so a histogram that does not
add up says so. What remains is upstream of it:

`attempts: []` on the row conflates three distinct facts — a retrieval decline with an empty
ledger, an absent `execution` record, and a concurrency crash row. `harness._attempt_trace`
returns the same empty list for all three, so the ledger cannot say which happened and no
downstream count can either.

For the record, since the `sample_rows` correction was itself reported with a partial number.
Filtering the ledger through `answering_attempts` moves the printed histogram by, on every
proxy arm in `runs/eval/`: **25 attempts on v3-pinned**, 3 on v3-fold, 1 on v4, 1 on v5, 3 on
v4-reflect — all `PARSE/r_ambiguous_fold`. run1 and run2 record no ledger at all. The earlier note
gave only three of the five arms and omitted the largest, which is eight times the biggest figure
it did quote. Failed-attempt totals move 929→904, 370→367, 295→294, 286→285 and 310→307
respectively.

The "21 `passed`" also cited is real and is over a narrower slice than the sentence implied:
v3-fold's **capped** turns hold 24 `sample`-path attempts, 21 passing and the same 3 refused. Over
the whole arm it is 132 attempts, 129 passing. A histogram of *failed* attempts never counted the
passing ones on either slice.

### 3.9 The eight tests that could not fail are pinned rather than repaired-and-forgotten

All eight are covered by the nine declared mutations under the `s39-` prefix, which moved on
2026-08-19 (`77d5f9f`): `tools/mutation_catalogue.py` is a 26-line re-export shim with no `s39`
hit left in it, and the nine live in `tools/mutation_catalogue_data_2.py`. Verified
caught on 2026-08-11: `routing_pinned` pinned to either
constant, `corpus_content_hash` and `prompt_set_hash` set to `None`, `_attempt_trace` returning
empty, `computed_correct` always `None`, and three anchors along the eviction chain — the
producer in `assemble`, `stamp`'s key set, and the consumer in the eval row. The repairs
themselves landed earlier; what was missing was the mechanism that re-checks them, which is the
whole argument of `mutate.py`'s own docstring — a habit does not survive the person who has it.

What is **not** claimed: that the suite is otherwise good. A mutation nobody declared says
nothing, and the pattern these eight shared — asserting that a constant equals itself — is
cheap to reintroduce anywhere a new field is added to a row.

### 3.10 Declared machinery with no wire is this repository's recurring defect

One shape keeps recurring: something is declared in the register, stamped by a node, or promised
in a docstring, and **nothing on the other end reads it**. Each instance is individually small;
together they are the reason numbers here have twice been quotable and wrong.

A sweep found 28 and the checker now reports **5**. It stood at 6 across the 2026-08-12
access-seam and abstention work, which added two comparability knobs (`access_grant`,
`abstention_policy_enabled`), a record field (`abstention`) and a state channel of the same name,
every one of them with a consumer on the other end; `clarifications` closed on 2026-08-19. That is
the number to watch when adding a declaration: it is easy to move, and the only thing in CI that
moves it is `test_the_declared_but_unconsumed_set_does_not_grow`, which fails on a closure as
loudly as on a new finding so the list cannot outlive its findings.

Fourteen were fixed in the sweep itself; the
eight closed on 2026-08-11 are the driver-side identity — `git_sha`, `git_main_sha`,
`working_tree_dirty`, `diff_sha256`, `serve_workers`, `schemas_under_test`, `split` and
`question_subset`, all resolved in `eval/provenance.py` and stamped onto every row the driver
writes from now on — which is not the same as every row on disk; see below. Evidence and
the per-field decisions are in `git-history:docs/analysis/declared-not-consumed.md`, deleted by
`2396ca2`.

Five remain, and none of them currently corrupts a number:

| | |
|---|---|
| `expand_hops` | a comparability knob with no reader: setting it changes no behaviour and does change the config hash. `pulled_in` now reaches the row, which makes the knob's own question answerable — the measurement half exists, the behaviour half does not |
| `negative_tau`, `facet_model`, `rewrite_model` | dead declarations |
| `build_workers` | **deliberately still open.** The eval driver serves and does not build a corpus, so a number here would be the `embedding_provider` defect — a null reads as unmeasured, a value reads as a measurement. The knob's own note is about a worker that "holds a connection AND a long-lived agent conversation", which is the curator, and the curator is not in this repository. Wiring it from the driver would launder it under K1's blind spot rather than close it |

`clarifications` was the sixth until 2026-08-19. It was a channel with two writers and no reader
outside `state.py`, and what closed it was seeing the consequence rather than the declaration: a
turn that paused at `ask_user`, was answered, and resumed had its SQL chosen by a *person*, and
`/audit/turns/{id}/trace` showed no sign of it. `ThreadTurnLog.clarifications_of` now projects the
channel onto the trace, joined on the `turn_id` the row already carries. Nothing new is stored --
which is why turns served before the reader existed show their clarifications too.

The common cause is that declaring and consuming live in different files and nothing forces them
to meet. **Two of the fixed items were invisible to any static rule by construction** — in each
the declaration had a consumer and the missing wire was on the recording side, so only the
artifacts showed them.

`tools/check_declared_is_consumed.py` closes the statically-visible part: four rules over knobs,
record fields and state channels, mutation-verified against a fixture tree. It reported 27
violations when written and reports **5** now — re-run 2026-08-22, exit 1, every one of them
under K1 and every one a knob: `expand_hops`, `negative_tau`, `facet_model`, `rewrite_model`,
`build_workers`. Those are exactly the five the table above names, so the two counts in this
section agree. The "6" this sentence used to give was the pre-2026-08-19 figure, before
`clarifications` closed.

**Tier 1 is clear as of 2026-08-12**, which is the condition
`tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py` names for a CI step. All five items —
`llm_reasoning_effort` unreadable on the proxy, `llm_utility_provider` and `embedding_provider`
publishing `"openai"`, `chat_model` null on four arms with the value in an undeclared key, three
environment variables outranking `knobs_resolved`, and `sqlglot_version` absent from every row —
now have writers that fire, each asserted **on its value** in
`tests/serve/test_the_record_follows_the_knob.py`.

**Which artifacts gain, precisely.** All **seven proxy arms** in `runs/eval/` predate the wires and
gain nothing, so `git-history:docs/analysis/declared-not-consumed.md` §1–§5 remain the
correct description of every number quoted from them — which is every number in this document.
`2396ca2` deleted that file, so the description has to be read out of git. That note's own
sweep instrument was six of those seven (8 106 rows); `proxy_v4_reflect_corpus30872d3.jsonl` came
later and is in the same state, so "all six arms" is the scope of the *sweep*, not of the defect.
`runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl` is the one artifact that is not a
proxy arm: a two-row smoke written after the wires, carrying the fixed values
(`llm_reasoning_effort: "xhigh"`, `llm_utility_provider`, `embedding_provider`,
`chat_model: "gpt-5.6-luna"`, `sqlglot_version: "30.16.0"`) where the seven carry `None` or nothing
at all. It also carries `git_sha`, `git_main_sha`, `working_tree_dirty`, `diff_sha256`,
`serve_workers`, `schemas_under_test`, `split`, `question_subset` and a resolved `prompt_set` — it
is the only evidence on disk that the eight closed above actually reach a row. Two rows is not a
measurement and nothing here is quoted from it. There is no `runs/index.jsonl` on this tree.

**The gate is still not a CI step**, and the reason has changed. It exits 1 on the five findings
in the table above, so a step would fail every commit, and waiving five genuine findings to go
green is the lie it was written to catch. Two of the five need a *decision* rather than a wire:
`expand_hops` and
`negative_tau` are comparability knobs whose readers would live in `retrieve/`.

What did land is the half that was missing either way:
`test_the_declared_but_unconsumed_set_does_not_grow` runs the gate on **every commit** against
the five findings pinned by name, so a sixth fails the build with the offending name, and
closing one fails it too — because a shrinking list nobody updates is how a stale count survives.
Names and not a count: five findings and five *different* findings are the same integer.

Its own docstring states the blind spot: rule K1 credits any occurrence of a knob's name, so a
coincidental string literal launders one. That is why the eight closed above are also asserted on
their **values**, in `tests/eval/test_the_row_names_the_harness_that_produced_it.py`, and why
`build_workers` is left red rather than given a number.

**Two capabilities landed before their callers (2026-08-19). Both are closed as of 2026-08-23**,
and they are kept here rather than deleted because neither is visible to
`check_declared_is_consumed.py` — it reads the register and not the call graph — so nothing in CI
would have noticed either one being reopened.

- **`eval/power.py::require_power` has a caller, and until 2026-08-24 it could never receive a
  value.** This entry read *"Closed"* on the strength of `ArmProfile` gaining
  `hypothesised_effect` and `readout` and `eval/provenance.py::arm_power_refusal` reading them,
  invoked by `tools/run_datalake_eval.py` beside `arm_startup_refusal` — before the first paid
  question, which is the only point where refusing is free. That much was true. What nothing
  checked is that **`load_arm_profiles` never passed the three new fields**: it constructed
  `ArmProfile` from nine explicit keys, so the gate read `None` for every arm on every run and
  abstained. The old wording defended the silence — "silent when a profile declares no hypothesis" —
  and the reason it gave was the wrong one: no profile *could* declare one. Writing
  `hypothesised_effect` into `arms.toml` parsed clean and did nothing, and the tests could not see it
  because they built `ArmProfile(**base)` directly and never called the loader.

  The wire is real now, and a coverage guard raises at load if a field exists on the dataclass and
  the loader does not pass it — so the next field cannot arrive as a silent `None`. Unknown keys in
  an `[arm.*]` table are refused against the field names, which is the file-side half: the field is
  spelled the British way, and `hypothesized_effect` was one keystroke from the same silence.

  **Still open: no arm declares an effect, so the gate abstains on all four.** That is deliberate.
  Every arm was measured before the field existed, so any number would be read off the arm's own
  result — and a fabricated effect makes the gate *pass* where an absent one makes it abstain, which
  is strictly worse. `../BIRD-corpus` has no git tag either (checked 2026-08-24), so there is no
  honest `corpus_release` to attach retroactively. The absence is now a committed inventory rather
  than an inference: a test asserts the empty set, so the first arm to declare a hypothesis has to
  come through it. `readout` is required alongside the effect and is not in the arithmetic — MDE is
  denominated in points of the whole population, and a draft of the design read a mechanism
  indicator's smaller MDE as the better instrument when its base rate was two orders of magnitude
  lower.
- ~~`corpus/snapshot.py` has no caller.~~ **Still no caller, and now deliberately so.** The
  verification ladder was the path that would have needed it; `tools/verify_patch.py` applies the
  edit **in memory** instead, because `corpus/patch.py::apply_edit` returns text and writes nothing.
  That is faster (no 8.0 s copy of a 7,357-file tree per run, measured) and it removes the hazard
  in F8 below from the ladder path entirely. The module keeps its guard fix and its tests; the first
  path that genuinely writes a corpus during a run will still want it.

**Two more of the same shape, both in the return path, both closed 2026-08-24 — and rule K1 credited
one of them as consumed.** Neither is a knob, a record field or a state channel, so
`check_declared_is_consumed.py` sees neither, and the second is the sharper lesson: K1's evidence is
"the name occurs outside `register/`", which cannot tell a **consumer** from a **producer**.

- **`Source.agent` had no producer.** `feedback/events.py` declared a fourth filing population and
  the four construction sites in `src/` and `tools/` write `reader`, `operator`, `operator` and
  `eval`. Its only occurrence anywhere was `feedback/validate.py::_may_file_operator_only`, reading
  `obs.source is Source.agent and obs.category is Category.column_suspect` — a branch that could not
  evaluate true, under a docstring calling it "the one agent-writable exception ADR 0005 declares".
  So a declared *policy exception* was dead code, and the only thing that ever filed as `agent` was
  the test asserting the store accepted it. Deleted, in the member and in the branch, for the rule
  [ADR 0015](adr/0015-the-return-path.md) had already applied to itself when it refused to declare
  `rendered_asset_ids` against the same unbuilt pipeline: *it lands with its consumer and not
  before.* The design keeps the population — §5's Author and Curator are its producers — and the
  member returns in the commit that builds them, which is where the widened `column_suspect`
  permission gets decided again instead of inherited. `docs/openapi.json` declared the value on
  `ObservationResponse.source` too, so a client was told to handle a value the server could not send;
  `PatchResponse.author` published the whole `Source` vocabulary for a field with one producer. Both
  narrowed. Held by `tests/feedback/test_every_source_has_a_producer.py`, which counts a member
  produced only where it appears inside a `source=`/`author=` value — comparisons are reads and are
  not evidence. **Scoped to `Source` and not widened to the other seven enums in that vocabulary on
  purpose:** `Kind`, `Category` and `DeclineReason` are parsed off a request body, so the same rule
  reports 13 of 13 `Category` members and 2 of 2 `Kind` members unproduced, and a gate that has to
  be waived for correct code teaches people to waive it.
- **`PENDING_SOURCE_INTERRUPT` was the only spelling nothing used.** `api/feedback_routes.py`
  declared and exported it — "the client switches on it to decide which card to draw" — while the
  producer (`api/thread_turns.py::_open_questions_of`) wrote the literal `"interrupt"` and the
  consumer (`ui/lib/schemas.ts`) is a `z.enum` of literals. The wire was never dead; the constant
  was, and it survived `4a0d11a`'s deletion of `serve/raised.py` because nothing on either end
  referred to it. Its home was wrong twice over: `feedback_routes.py` never emits this value —
  `_as_pending_row` fills the same column from `obs.kind.value`, the other half of the axis. Moved
  beside `PENDING_FIELDS` in the module that writes it. The spec declared the three values in a
  `description` only, and `tests/api/test_the_spec_matches_the_server.py` says descriptions are the
  one thing it does not check, so that claim was unfalsifiable; it is an `enum` now.
  `tests/api/test_the_pending_source_axis_has_one_spelling.py` compares Python, `docs/openapi.json`
  and `ui/lib/schemas.ts` against each other, and refuses a literal `source` write under `api/`.

**A fifth rule for the gate — an enum member with no producer — was considered and not written.** It
would be green on this tree after the two fixes above, but only for `Source`: the rule needs to know
which enums code chooses and which are parsed at the edge, and that list is neither derivable from
the AST nor stable. A gate carrying a hand-maintained allowlist of seven exemptions is a gate whose
allowlist is the actual claim. The two tests above hold the same ground with the reasoning attached
to the enum it applies to.

The distinction that keeps these off the list above: a knob with no reader **changes the config
hash** while changing no behaviour, so setting it produces a row that lies. A function with no caller
produces nothing at all. The failure mode is a reader believing the capability is in force, which is
what this entry exists to prevent.

### 3.10a Four things measured while building the return path (2026-08-23)

Each one changed a decision in [ADR 0015](adr/0015-the-return-path.md), which records *which*
decision and points here for the evidence.

**F1 — the `raised` channel held zero rows, on three independent checks.**
`select count(*) from writes where channel='raised'` → 0; a full msgpack decode of all 931
checkpoints in `runs/conversations.sqlite` → 0; the 23 thread rows in
`.langgraph_api/.langgraph_ops.pckl` → 0. A byte-grep shows 47 apparent hits and every one is
English prose inside `messages`. **So the migration needed no drain tool**, which is what let the
channel deletion move earlier and get cheaper. The channel and `api/raised_write.py` are deleted;
`ServeState` went 48 → 47.

**F2 — the cost of deleting a channel is the contract, not the code.** The writer was one path and
the readers were two call sites and a constant, all in `api/thread_turns.py`. What actually took the
time: `docs/openapi.json` pinned `RaisedRowResponse` with seven required non-nullable fields,
`report_id` was declared in the pending queue's `meta.columns` *because a client keys a card on it*,
and `tests/api/test_the_spec_matches_the_server.py` held four assertions over that operation. About
half a day. `docs/return-path.md` called it "a rename with a deleted owner, not a rename with churn"
and that was half wrong; the page now says so.

**And the spec was missing seven operations, not two.** `GET /observations` in both shapes and
`GET /observations/{id}` shipped with the review screen and were never declared, which made
`ui/scripts/check-api-contract.ts`'s "inventory of record" claim about `docs/openapi.json` false.
All seven are declared and driven against real payloads now.

**F7 — `corpus/store.py::write` on an existing id writes a second file with the same id.**
`store.load` returns both with zero problems and `retrieve/index.py:316` then raises
`ValueError: duplicate index id` — after the commit. Measured on the same one-word `summary` edit:
`store.write` touches 343 lines and lands at a different path; `corpus/patch.py::apply_edit` touches
4 and lands where the asset already lives. **This is why a bundle is a `git apply` diff and never a
directory copy**, and why conformance rule V23 exists even though it finds zero today.

**F8 — `corpus/snapshot.py`'s `rmtree` was guarded only against nesting.** `_identify_corpus`
guarded `restore` and not the destination, and it was **measured deleting a scratch directory of
unrelated files**. Fixed: `_identify_corpus(dest)` runs before the `rmtree`, an empty directory is
allowed, and an existing file raises `NotADirectoryError`.

### 3.10b Complaints cluster weakly, measured on the real 73

ADR 0015's open question 7 asked whether complaints cluster. **They do not, and the design's
batching argument does not survive it.** Measured on the 73 coverage-miss failures the importer
produced from `runs/eval/proxy_v4_corpus30872d3.jsonl`:

| key | clusters | singletons | largest | in a cluster ≥2 |
|---|---:|---:|---:|---:|
| `(category, schema, missing[:3])` | 70 | 67 | 2 | **8%** |
| **`(category, schema)`** — shipped | 54 | 37 | 3 | **49%** |
| `(schema, missing[:1])` | 63 | 55 | 4 | 25% |
| `(schema,)` | 36 | 17 | 6 | 77% |
| `(category,)` | 4 | 0 | 33 | 100% |

56 of 73 miss exactly one table and those tables are mostly *different*, so the absent table
identifies a **turn** rather than a problem — which is why `missing_tables` stays on the row as
evidence and out of the key. The largest cluster is 3. Anything sized on "the marginal cost of one
more observation in a cluster is zero" has to be re-sized, and `/review` is a list with an optional
grouping rather than a cluster-first screen.

### 3.10c The 438-row partition now has a producer

§1's table above and `failure-modes.md` §1 carry **⚠ hand-run, no producer in the tree** over the
six-way partition of the 438 failures. `eval/feedback_import.py` reproduces it from the artifact in
code — 1,351 rows read, 438 failures, 73 coverage misses, 87 dataset defects, 278 full coverage, 0
crashed, 0 unparsed — so that partition is the **first** number in this block to have one. The rest
of the hand-run block (the 292-statement diagnosis, the projection recovery counts) still does not,
and the marks there stand.

### 3.10d The return path was walked once, end to end, and the queue is 72% stale

One real observation, 2026-08-24: `open` → triaged → drafted → T0/T1/T2 green → exported →
`git apply` → committed → **`landed_verified`** → T3 → withdrawn. The first `landed_verified` this
project has produced: the exporter predicted `41bbb2e567ea32eb` before the write and the corpus
hashed to exactly that after, which is the strong claim (*this* tree, not *a* tree with the right
text in it) and it only became reachable when `export_bundle` started recording the predicted hash.

**The payoff was zero, and that is the finding.** The case was the cleanest available: the question
asks what was *bought*, the gold needs `student_club.expense`, and that table's `summary` — which is
the retrieval index — did not say the table records what was bought, though `expense_description`
holds it. Adding it left both gold tables unlicensed, **exactly as before the edit**. Verified
against the pre-edit tree rather than inferred, after a first reading that wrongly blamed the edit
for a displacement that predated it. The free tier refused to certify the patch, which is what it is
for, and the patch was withdrawn.

**The funnel, measured.** 73 imported → 71 real → **20 still reproduce** → 13 whose missing table
sits in a schema the router did reach → **11 the only repair this loop offers could plausibly fix**.
15%.

| finding | status |
|---|---|
| **52 of 71 open rows no longer reproduce.** 71 carry `corpus_content_hash 86ed1dbf…` and the corpus is `6e5c7b4b…` — the artifact was measured against a different tree and nothing said so | fixed: `--state` asks the queue in one pass (72s; per-row through the CLI was 32 minutes), `--decline` closes them, and the importer names both hashes. Rows are **not** dropped on import — an observation is a record of something that did happen |
| **7 of the 20 are schema-routing failures.** The router never reached the schema, so no `summary` or `body` edit can help — and nothing in the queue, the reproducer or `/review` says which layer failed | **open.** The loop offers one repair and does not say when it is the wrong one |
| A config error exited **1**, which three of these tools use for a verdict; and `return 1` sat inside `if args.record:`, so a run that found 19 live failures exited **0** | fixed: `corpus_target.Misconfigured` → 2, chosen in `main`, for all four |
| Four tools each carried their own copy of "which corpus", and the copies disagreed about whether `.env` counts | fixed: `tools/corpus_target.py`. `credentials` has been this repo's dotenv reader since 2026-08-03 |
| The exporter's printed `git apply` / `git commit -F` pair **does not work** — the change lands unstaged and the commit exits 1 | fixed: `--index`, and a test parses the commands out of stdout and runs them |
| Withdrawing a patch left its observations reading `addressed` | fixed: `move_patch` returns them to `triaged` and reports what it could not move |
| `airline.Air Carriers` reports missing while `airline.Air_Carriers_66c534` is licensed — one table, compared as physical name against asset id. 2 of 73 rows are false | fixed: `gold_table_ids` offers both spellings a gold could be licensed under, re-deriving the id with the same `slug` that minted it. It was **three** comparisons, not one — `table_coverage`, `retrieval_funnel` and `feedback_import._missing_tables`, the last of which files operator-facing rows. `coverage_miss` 73 → 71 |
| 2 of 73 rows carry no `corpus_content_hash` at all | counted apart from a mismatch; why they have none is unexplained |

**What this says about the feature.** Every defect above was found by *using* it. Three days of
reading and mutation testing beforehand found none of them — they are all in the joins, which is
what you would expect of a loop that had never been driven.

### 3.10e The corpus gate is wired to a nightly, and its baseline still equals the corpus tip

[ADR 0016](adr/0016-gating-the-corpus-repository.md), 2026-08-24. `tools/check_corpus_delta.py`
answers "did the corpus add a conformance finding since somebody last looked", against a corpus
revision recorded here in `tools/corpus_baseline.py` (`74ff80c4842410e54fc81964b30bbe6d4a91f872`).
The findings it is grandfathering are **125 on 101 identities** — different nouns, and the difference
of 24 is why every baseline carries a count per identity — measured 2026-08-24 on `../BIRD-corpus` at
`main` = `74ff80c4` with `tools/check_corpus_conformance.py --json`: V17a 107 findings on 85 of the
478 metric assets, V17b 17 on 15, V21 1 on 1, and the other 19 of 22 rules zero.

| finding | status |
|---|---|
| **No runner had ever run this gate, in either shape.** The first design put the workflow in `BIRD-corpus`; its two commits were never pushed, the GitHub Actions run count there is **0**, and its `main` tree contains zero paths under `.github/` or `.conformance/` | **closed for the wiring, 2026-08-25.** The branch merged, so `ci.yml`'s `corpus` job is on the default branch with a `schedule` and a `workflow_dispatch`, which is the condition GitHub requires; this row said "starts working when `design/return-path` merges and not before" for a day after it had. The tool was already observed — all three exit codes driven by hand 2026-08-24 (0 at 78 s; 1 on a planted duplicate id, head 183 on 159 with each added finding named; 2 on a missing `--dataset-dir`, V11/V12/V15 named), corpus clean with no worktree left after each. ADR 0016 §Consequences 2 carries the table. What is **not** closed is the row below: a scheduled run against a baseline that equals the tip is a green light measuring nothing |
| **It is not a merge gate.** A corpus commit that adds a finding lands, and is caught up to a day later | **accepted**, and the price of the dependency direction. The corpus is human-owned and moves rarely — 9 commits on `main`, 2026-07-11 to 2026-08-18, one of them in the fifteen days before this was written |
| **The baseline equals the corpus tip**, so the delta is empty by construction and the first green run proves nothing | open; the gate becomes informative only after the corpus moves |
| **A bump made without reading the findings is indistinguishable from one made after.** The edit to `BASELINE_SHA` *is* the acknowledgement, and nothing detects a bump that skipped the looking | open, and there is no obvious instrument for it |
| **`check_ratchet.py` still has no automated reader.** The pins moved from the corpus into this repository as `.conformance/bird-corpus-pins.txt` (109 lines, 101 pins), which fixes the *side of the merge* — this repository's CI could read them, and a rule change and a pin edit can now land in one commit. It does not fix enforcement: the nightly runs the delta tool, which passes a closure by design | **open.** The ratchet's policy, "closing a finding must be declared", is enforced only when a person runs it |
| Two records of one fact: `tools/corpus_baseline.py` carries 125/101 and the pin file carries the 101 identities by name | closed by `tests/conformance/test_the_corpus_gate_is_wired_to_the_nightly.py`, which asserts the two agree *and* that the workflow's own `base=` command prints `BASELINE_SHA` |
| `docs/return-path.md` carried a **"### Corpus repository"** section describing CI that ran *there* — three commands from inside the corpus checkout, including `--pins .conformance/pins.txt` | fixed the same day: the section is now **"Corpus repository — none, and the check runs here instead"**, and it prints the `corpus` job's actual command. It was outside the ADR change's file scope and was nearly left behind, which is the whole hazard of splitting a change by file ownership |

### 3.11 Selective prediction is closed at 0.80, and the reflector closed it

The reflector ran, once, as the last untested source of information: everything that does not
read meaning had already been measured and capped at OOF AUC 0.721. **It scores 0.597** — worse
than the count of tokens the agent emitted, and combining the two is worse than the token count
alone. Full result: `git-history:docs/analysis/risk-coverage-v4.md` §6, deleted by `2396ca2`.

The row that matters is `unsure`. The judge called 77 turns unsure and they are **as likely to be
right (0.766) as the ones it called correct (0.763)**. So the follow-ups that suggest themselves —
a graded `confidence`, `right` instead of the ambiguous `answered`, a `TypedDict` of `Literal`s
through `with_structured_output` — all address expression, and expression is not the problem. A
judge whose "I cannot tell" bucket matches its "this is right" bucket has no perception of its own
uncertainty to express.

`with_structured_output` therefore stays unused here, and the reason has changed: not "wait for
the baseline" but "the baseline came back and there is nothing to express". Two facts from that
work survive and are worth keeping if anyone revisits it: `include_raw=True` is mandatory, because
the hand parser fails safe into a recorded `why_unmeasured` and a bare
`with_structured_output` raises and loses the reply; and structured output needs no transport
change, since `tools` in `model/provider.py` only selects OpenAI's Responses API.

Two things the arm settled in passing. The parse-failure rate is **zero** — `why_unmeasured` is
empty on every row — so the hand parser is robust enough, which was left open pending exactly this
data. And the template-echo bug fixed in `95e3b07` **did not fire**: this arm predates the fix and
zero rows carry the signature, so it is uncontaminated.

What remains open is not a better judge. It is what governance itself buys, and **that has a
first number instead of none**. `src/governed_bi/govern/adversarial.toml` is 115 cases as data —
62 attacks, 53 benign statements — loaded by both `tests/govern/test_adversarial_suite.py` and
`tools/govern_bench.py`, so the gate that fails a build and the report that prints the rates
cannot drift apart. It needs no credential and no network, and the layer stack is deterministic,
so unlike every other measurement here it has no noise floor and two runs are identical. On the
current tree: **bypass 0/62**, **misattribution 0/62** (refused, but by the wrong layer or the
wrong rule), **false refusal 0/53**, and **zero guardrail errors** on either half. Per-layer
recall is **1.000** on the six layers that own attacks — PARSE 7/7, NO_WRITE 7/7, FUNCTIONS 13/13,
BINDING 9/9, COLUMNS 11/11, TABLES 15/15. COST owns no attack, so it has no rate at all and prints
as not measured rather than as a pass.

Twenty of those cases are the `authorization` family ADR 0012 added, which is why COLUMNS and
TABLES carry more attacks than the other four layers: they are the two the access grant narrows.
A further twelve `[[probe]]` cases sit beside the statement cases and measure *disclosure* rather
than refusal — whether a withheld asset reaches the prompt, a tool reply or an HTTP body — at
**disclosed 0/7** and **over-withheld 0/5**.

The item stays open because of what those rates are not:

* **They are a fact about 62 attacks somebody thought of.** A bypass rate of 0 over hand-written
  cases is not a rate over the attacks nobody wrote, and adding cases does not turn it into one.
  What the suite gives is a floor that a change has to keep clearing, not a claim about the space
  of statements.
* **Five of ADR 0006's ten bypass families are not in it.** B3, B7, B8, B9 and B10 have no SQL
  surface to aim a statement at, so they are covered by *argument* — the structural claims in
  ADR 0006 — and not by a case. The file declares that per family and the loader enforces the
  declaration both ways, which makes the gap visible; it does not close it.
* **The benign half is the same kind of sample.** 0/53 says that 53 ordinary analytics
  statements are not refused, and the statements a real analyst writes are not that set. Its
  demonstrated value so far is as a cost measurement on a fix rather than as a safety claim: the
  first, tree-wide repair of §3.2a scored 2/46 on the benign half as it then stood, and was
  withdrawn for it.
* **No fork has run any of it.** Every authorization and disclosure figure above is measured on a
  fictional world declared in the suite itself, against a scripted model. The false-refusal rate
  of a real grant over a real corpus is unmeasured, and ADR 0012 records it as owed.
* **The scope gate is still outside the suite.** Its fail-open on affirmative-prefixed replies is
  fixed and pinned by `tests/serve/test_guard_bi_scope.py`, but every case here drives `check()`
  and `prepare()`. A gate that costs a model call cannot go in a suite whose whole property is
  that it costs nothing, so what guards it is unit tests, which is a weaker thing.

### 3.12 The noise floor is five times a comparison system's, and that is architectural

Two runs of this engine with the configuration held fixed — run1 and run2, same prompt, same
corpus, same knobs — disagree on **12.7% of outcomes** (172 of 1 351). WrenAI's two runs over
the same questions and the same database disagree on **2.4%** (33 of 1 351).

The WrenAI pair is a genuine replicate and not one run graded twice: its `generated_sql` is
identical on 919 of 1 351 questions (68%), so a third of its statements were regenerated
differently and still landed on the same outcome.

Nothing is broken. The gap is what this architecture is: an agentic loop that may take up to
five `run_query` attempts, five model-driven facet rewriters sitting above retrieval, and a
layer that can end the turn in a refusal. Each is a place where one sampled token changes the
outcome, and a single-shot generator has none of them.

The consequence is a standing tax on every experiment run here, and it should be stated before
a run rather than discovered after one:

- SE(net) is about **1.0pp** unpinned and **0.83pp** with `--replay-routing`, so the smallest
  effect a 1 351-question arm can resolve at 80% power is roughly **2.3pp** — v3-fold → v4's
  MDE was 2.33pp and its observed delta was 1.18pp. The same comparison against a
  2.4%-discordant system would resolve about 1.0pp.
- A change worth less than ~2pp is not measurable here by running one more arm. It needs the
  mechanism counted instead — the way v4 was accepted on `r_star_projection` going 35/29 to
  2/2 rather than on its EX — or a larger question set, or an intervention that reduces the
  loop's own variance.
- Pinning routing is the only lever currently applied, and it recovers about a quarter of the
  discordance (§3.1). The attempt loop and the facet rewriters are unaddressed.

---

### 3.13 The treatment must be declared, and only four arms have declared it

`arms.toml` arrived on 2026-08-11 with audit D9's fix: `eval/report.py::knobs_comparable`
refuses a pair that cannot name what changed, and the profile is where the name comes from.
Four arms are declared — `v3_fold`, `v4`, `v5` and `ask_first`, the last added with
`treatment = ["prompt_set"]` and `compare_to = "v4"` and no measured run behind it yet
(`register/arms.toml:114-121`). Any other artifact in `runs/eval/` is
`cannot_evaluate` in a comparison until someone writes down what it changed, which is the
intended pressure and not a defect.

**`reconcile` is wired**, to `--arm`: the driver looks the profile up before the first paid
question and refuses a run labelled with an arm whose declared corpus is not the one the session
loaded, then reconciles every row again in the report. Fixing the wire also found the function
was vacuous — reconcile compared two namespaces that could never match.

**That fix was itself incomplete until 2026-08-12, for the arm that mattered most.** `v3_fold`
declared no `corpus_content_hash`, so the repaired guard was never entered: a run launched
`--arm v3_fold` against any corpus at all cleared the pre-flight check *and* was told in the
report that every one of its 1 351 rows agreed with the profile. Two things changed. `v3_fold`
now declares the digest its artifact carries (1 345 of 1 351 rows; the other 6 are §3.6a
clarifications), and the digest is **mandatory** — `_parse_profiles` refuses `arms.toml` without
one and `reconcile` refuses a profile it cannot reconcile, so an unreconcilable arm can no longer
report agreement. The wire itself was untested and now is: `arm_startup_refusal` and
`reconciliation_lines` are pure functions in `eval/provenance.py`, driven from dicts by
`tests/eval/test_the_arm_profile_wire_is_exercised.py`.

**The controls have now been run against the real null pair**, on a machine that has `runs/`. All
six pass (`tests/eval/test_the_delivery_gate_can_fail.py`). What that establishes is narrower than
it looks and is the reason the four artifact-backed ones were downgraded in the first place: every
one of the **seven proxy arms** in `runs/eval/` is missing the same four comparability knobs —
`cost_budget`, `negative_tau`, `semantic_scale_ceiling`, `sqlglot_version` — so `knobs_comparable`
returns `cannot_evaluate` at the absence branch and never reaches the judgement. Re-measured
2026-08-12: still exactly those four, on all seven. The eighth artifact carries all four and is the
two-row smoke of §3.10, so it is not a pair either. **No pair on disk can reach this gate**, which
is what the two synthetic controls exist for.

What is still owed:

* **No real pair is comparable** until an arm records those four knobs. The producing defect is
  closed — `session._resolved_knobs` writes `None` for an `UNSET` knob instead of omitting the
  key, and resolves `sqlglot_version` — but every arm on disk was measured before that, so this
  needs a run, not a fix.
* **`prompt_set` is `null` on every row of v4 and v5**, and it is the treatment both declare. So
  even past the absence branch the gate would report a replicate, correctly: the artifacts
  cannot show that the declared treatment moved. `prompt_set_hash` *does* differ (v3-fold
  `ef30252f`, v4 `b1f9e4d7`, v5 `7a9e7102`), so the arms are distinguishable and not nameable —
  finding 7 of `git-history:docs/analysis/declared-not-consumed.md`, deleted by `2396ca2`.
* **`compare_to`, `description` and `notes` now have a reader** (the driver prints them under
  `--arm`) but nothing checks `compare_to` against the pair a comparison is actually run on.
* `GateResult.render()` prints `field`, `observed`, `population` and `detail` and **omits
  `condition`** — the one line saying what the gate actually required. A reader of the driver's
  output gets the verdict without the criterion. (The withdrawn 95% distinctness rule is no
  longer asserted anywhere: `CONTEXT_HASH_THRESHOLD` survives only as an unused parameter that
  `context_hashes_distinct` reports in its detail line as retired, and both that function and
  `_context_hash_gate` say so in their text.)

---

## 4. Open questions

### 4.1 What the headline should be, and what the contrast arm did to it

On the v4 arm the engine commits to 1 278 of 1 351 turns at **0.714** accuracy and abstains
on 73 (5.4%). Of those 73, **62 can be priced** — for the other 11 the dataset ships no gold
fingerprint, so what the engine would have got is unknowable, not zero. Of the 62, 14 would have
been correct: **77.4% of priced abstentions would have been wrong** had the engine been forced
to answer. Delivered accuracy is **3.16×** the accuracy it withheld.

The 62/73 split is not a rounding detail. Abstention precision is computed over a subset the
dataset selected, not a random one, so it is a figure about the priced population and must be
quoted that way.

**A governance-off contrast arm already exists, and it bounds the claim rather than confirming
it.** WrenAI runs the same 1 351 questions on the same database with `refusal_rate: 0.0` — it
never abstains, which is the comparison the calibration claim needs. On the 73 turns v4
declines, WrenAI answers all 73 and gets **56.2%** of them right, against **68.5%** on the
1 278 turns v4 commits to. The ratio is **1.22×**.

Read that plainly: the questions this engine declines are mostly answerable. If abstention were
tracking *question difficulty*, an ungoverned engine should fall apart on the declined set; it
loses twelve points. What abstention tracks is this engine's own competence on the turn —
almost all of it retrieval, since 19 of the 20 refusals end on `r_table_not_licensed` (§4.2) and
all 4 clarifications licensed nothing at all. That is still a real and useful property: it is
the difference between a retrieval miss surfacing as "I cannot answer" and surfacing as a
confident answer over the wrong table. It is not the stronger claim, which is that the engine
knows which questions are hard.

So the honest framing is narrower than "calibrated abstention": **the engine declines when its
own context is insufficient, and it is right about that 77.4% of the time on the priced
subset.** Whether the project leads with this or with EX decides what gets built next, and the
two point at different work — leading with abstention points at retrieval, since that is what
the declines are made of.

**The decision is now declared, 2026-08-12: [ADR 0013](adr/0013-the-declared-abstention-policy.md).**
It changes nothing above and it does not try to. What it fixes is the sentence *"nothing decided
to withhold"*: a named policy, `context_sufficiency_v1`, runs between `assemble` and `agent_core`
and asks four deterministic questions of the turn's own context — a retrieval channel that
errored, no table licensed, an empty rendered block, a licensed table evicted for space. The
reason it returns is a member of `stages.ABSTENTION_REASONS`, it is written into
`terminal_reason` beside `no_schema_matched` and `missing_join_path`, and the evidence behind it
(what was licensed, what was missing, what share of the question's terms the corpus has) reaches
the record and the artifact row.

Three things it deliberately is not. **It computes no score** — §3.11 measured that and it
failed, and ADR 0007 forbids a trust field on the answer card. **It thresholds nothing** —
`lexical_coverage` is on the evidence and no rule branches on it, for `negative_tau`'s reason.
And **it ships off**, so every number on this page still stands and v4 is still the control.

What is owed is the number: the policy has never run on a real arm, so how many turns it
withholds and what share of those would have been right are both unknown. That is one paired arm
(`tools/run_datalake_eval.py --abstain`), and until it exists the honest claim about ADR 0013 is
that the engine can now *say* why it withheld, not that it withholds better.

What would still be worth building is the *other* contrast: the same engine with Layer 6
relaxed to the whole routed schema instead of the licensed 8 tables (§4.2), so the comparison
holds the model and the corpus fixed and moves only the allowlist. WrenAI differs from this
engine in every dimension at once, which is why it can bound the claim but cannot attribute it.

### 4.2 Whether `licensed` should keep serving two masters

`licensed` is both the retrieval budget (`ASSET_REGISTER[table].budget = 8`) and the governance
allowlist that `check()` Layer 6 enforces. A retrieval miss therefore becomes a hard refusal
rather than a degraded answer — 19 of the arm's 20 refusals end on `r_table_not_licensed`, and
all 20 hit it at some point in the turn.

At 0.940 coverage this is not currently expensive, which is why it is a question and not an
item in §1. Decoupling them (govern over the whole routed schema, retrieve the top 8) would
change what "governed" means and needs an ADR, not a patch — and per §4.1 it is also the
contrast arm that would attribute the abstention property to the allowlist rather than to
everything else that differs between two systems.

**Answered in part, 2026-08-12: [ADR 0012](adr/0012-access-seam-principal-and-authorization.md).**
The ADR does *not* do what the paragraph above imagines. It does not widen governance to the whole
routed schema, and it does not touch the retrieval budget: `licensed` keeps exactly the meaning it
had. What it adds is the **second master, as its own set**. A turn now carries an `authorized` set
derived from an `AccessPolicy` port, and the TABLES layer asks three questions in a fixed order —
`r_table_not_licensed` (retrieval missed), then `r_table_not_authorized` (this principal may not),
then `r_row_predicate_unenforced`. COLUMNS gains `r_column_not_authorized` beside
`r_column_excluded`.

Three consequences for this section and for §4.1.

* **The abstention accounting keeps its shape and gains a falsifier.** Today the argument that
  "19/20 refusals are retrieval misses" rests on there being nothing else `r_table_not_licensed`
  could mean — an argument from an absent feature, which stops working the day anyone deploys this
  behind a permission model. The histogram can now separate the two without a second
  implementation.
* **No number moved, and that was the requirement.** The shipped default is an open grant, and
  under it all three new predicates are constant functions, so the three new branches are
  unreachable. All 95 pre-ADR adversarial cases produce byte-identical verdicts — including
  `layers_evaluated`, `bound` and `Prepared.sql`. The v4 arm is untouched.
* **The Layer 6 contrast arm §4.1 still wants is still not run.** ADR 0012 is the *mechanism* that
  makes it cheap — relaxing the allowlist is now a `Grant`, not a code edit — but the arm has not
  been run and the outward wording does not change until it has
  (`git-history:docs/analysis/strategy-checkpoint-2026-08-11.md` §2.6, deleted by `298465f`).

What is still open here, restated because the ADR narrowed rather than closed it: whether the
*retrieval* budget and the *governance* allowlist should be different sets at all. They still are
not. ADR 0012 added a third set that intersects both and answers a different question.

Two items it created, both in [ADR 0012 §7 and §8](adr/0012-access-seam-principal-and-authorization.md),
and **both closed the same day**. The grant's digest is now the `access_grant` knob, resolved from
`GovernancePolicy.access_grant` and never from the register default — a default carrying the open
digest would publish "open" for a fork shipping a restriction, which is the `agent_recursion_limit`
defect in the security register. And all four of §8's wires exist: `api/graph_app.py` builds the
policy and resolves the one principal, `ToolBounds` carries the grant, `_resolved_knobs` carries
its digest, and `serve/context.py::withheld_by_grant` narrows both the rendered block and
`readable_assets` from one set. `tests/serve/test_the_access_seam_reaches_the_served_app.py`
refuses a licensed, unauthorized table as `r_table_not_authorized` **through `build_serve_graph`**,
with the paired open-grant run of the same statement executing it.

What that buys §4.2 in particular: `r_table_not_licensed` now has a second thing it could have
been, so *"19 of 20 refusals are retrieval misses"* stops being an argument from an absent
feature. The underlying question is still open — `licensed` still serves both masters, and ADR
0012 added a third set rather than splitting the two.

**Filed 2026-08-22: `licensed` is seeded from the post-budget table set, which the ADRs say it
must not be.** `serve/nodes/route_retrieve.py::route_node` (`:140`) sets it from
`retrieved["by_type"]["table"]`, and that `by_type` is assembled in
`serve/nodes/pass_two.py:462-468` out of the hits `apply_budgets(...)` **kept** — a hit the
budget dropped never enters it. Only `resolve_node` (`route_retrieve.py:172-181`, reference
closure) and `connect_node` (Steiner points) widen the set afterwards, and neither restores a
dropped table.

[ADR 0006](adr/0006-execution-time-governance.md) §8 says the opposite in as many words —
"Explicitly **not** the post-budget `by_type["table"]` — budgets shape what is *rendered*, and
licensing what is *reachable*" — and describes `licensed` as an output of `connect`;
[ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) §3.2 and §3.5 carried the same
claim. All three of those sections now carry a 2026-08-22 correction block pointing here. So a gold table
cut by the retrieval cap is unlicensed and Layer 6 refuses the statement `r_table_not_licensed`
— a retrieval-budget outcome recorded as a governance verdict, which is exactly what those
sections say cannot happen. Steiner points survive only because `connect` adds them after the
budget has run; a budget-cut table that is neither a reference nor a Steiner point has no path
back. The consequence for this section is that the refusal accounting above, and §1.5's coverage
figure, measure a seam the ADRs do not describe.

**The pending decision, in the ADRs' words: license the pre-budget table set, or accept the
coupling and stop claiming the separation.** Not taken in this pass — either side moving is a
governance behaviour change, and no code was touched for the note.

---


### 4.3 Nothing authenticates, and audit A1 and A7 are open again

`GOVERNED_BI_API_KEY` was removed on 2026-08-13 with the middleware and the `Auth` plumbing that
read it ([ADR 0007](adr/0007-http-surface-and-the-ui-contract.md) Amendment 3). No route asks for
a credential; reaching the port is sufficient. The two findings the key closed on 2026-08-12 are
therefore live again in the words they were written in:

- **A1** — every route is unauthenticated, so anything that can open a socket to `:2024` can post
  a turn and execute governed SQL against the configured database.
- **A7** — `/audit/turns` and `/audit/turns/{id}/trace` hand that caller every thread's SQL, the
  full turn records, and an absolute path to the conversation database. Wider than that sentence
  was written: `api/routes.py::trace_for` also returns both `clarifications` and `raised`
  (`turn_log.clarifications_of` and `turn_log.raised_of`, keyed on the turn's own `thread_id`), so
  the trace discloses the questions the engine asked, the answers a
  person typed, and every reader-filed note — see the grant-seam item below, which is the same
  surface from the write side. **Wider since 2026-08-18**
  ([ADR 0014](adr/0014-one-conversation-store.md)): the record accumulates on `ServeState.turns`,
  so the platform's own unauthenticated `/threads/{id}/state` — and any `values` stream frame —
  carries the thread's recent turns rather than only the newest one. **Bounded, and the magnitude
  matters:** the same commit that widened this capped the channel at `MAX_TURNS_RETAINED = 25`
  (`src/governed_bi/serve/state.py:260-282`), so one unauthenticated read yields up to 25 whole
  turn records and the older ones only through their own checkpoints. "Every prior turn" was wrong
  and is withdrawn. Twenty-five turns of another thread's SQL to any caller that can open a socket
  is still audit B1's leak surface enlarged by this change and mitigated by nothing.

**Filed 2026-08-22: two mounted routes sit outside the ADR 0012 grant seam, and one of them is a
write.** `api/visibility.py::visible()` is the withholding seam the browse surface passes through,
and its only callers are `api/browse_routes.py:59` and four handlers in `api/routes.py`
(`/corpus/assets`, `/graph`, `/knowledge-graph`, `/audit/corpus`) — nothing else in `api/` names
it. `GET /clarifications/pending` and `POST /turns/{turn_id}/raised` — both in
`api/feedback_routes.py` since ADR 0015 §2 replaced `api/clarification_routes.py` and
`api/raised_write.py` — call neither it nor anything else grant-aware. The GET hands any caller
every unanswered question, and those questions can name assets, which was accepted because
`/audit/turns` already discloses every thread's SQL to the same caller. The POST is a write: any
caller reaching the port can file an observation against any turn, so the queue an operator reads
is attacker-writable. What bounds one row is `NOTE_MAX_CHARS` (4,000) and `QUESTION_MAX_CHARS`
(8,000) in `feedback/validate.py`; **nothing bounds the count**, and nothing sweeps the store.

**Two things this write no longer does**, and both are ADR 0015 §2 improvements rather than
mitigations of the finding above: it does not touch graph state — the
`aupdate_state(as_node="raise_note")` path is deleted, so it reaches neither `command.update` nor
`POST /threads/{id}/state` — and it therefore no longer answers 409 on a paused or in-flight
thread. It still persists attacker-supplied text, into `runs/feedback.sqlite` instead of a
checkpoint channel. The rows are attributable and movable now, which is what makes the flood
cheaper to clean up and does nothing to prevent it.

**And the write feeds a read.** `api/routes.py::trace_for` returns *both* `clarifications` and
`observations`, so `/audit/turns/{id}/trace` hands the same unauthenticated caller back the notes
any caller could have filed, beside the clarification answers a person typed. That is one loop, not
two findings. (The wire key was `raised` until ADR 0015 §2; a client reading the old name reads
nothing.)

Filed as its own item rather than folded into A7 because A7 is a disclosure finding and the POST is
not. The open work is the module's own closing sentence — under a real `AccessPolicy` "both verbs
must apply the same withholding the tools do" — and neither wire exists. Matching corrections
landed the same day at [ADR 0009](adr/0009-browsing-and-filtering-api.md) (the "every route this
app mounts is a read" claim, now false) and
[ADR 0012](adr/0012-access-seam-principal-and-authorization.md) §8, whose repaired sentence names
the exclusions: a grant withholds nothing from `ask_user`, nor from these two routes. `ask_user` is
the fifth entry in the tool list `serve/tools.py` returns, and the only bound on what its prompt
may name is `serve/schema_term_guard.py::find_schema_leak` — called once, from `ask_user` itself,
and a term heuristic that knows nothing about a grant.

**This is a recorded choice, not an oversight.** The engine is one operator on `127.0.0.1` under
`langgraph dev`, and LangGraph Studio's bootstrap fetches (`/info`, `/assistants/search`,
`/assistants/{id}`) carry no custom header — measured 2026-08-13 — so a required key made the
primary debugging client unusable. Reachability won.

**A5 is closed as of 2026-08-18, and it is the one that moved.** The clarification-resume
identity gate is `serve/resume.py::authorise_resume`, called by `ask_user` on the instruction
`interrupt()` returns on, comparing the paused turn's checkpointed `identity` against
`configurable["langgraph_auth_user_id"]` on the resuming run — a slot `langgraph_api` fills from
this repo's `@auth.authenticate` and refuses to let a client name. It fires on the streamed
transport, which is now the only one. What it cannot do here is *distinguish* two callers, because
`api/auth.py` returns one principal to everybody; the gate is correct and its input is degenerate,
which is why this stays under §4.3 rather than being called done. A6 retires with `POST /chat/resume`
rather than being fixed: the route whose check was same-thread-not-same-caller is deleted.

**What is actually open here** is not "put the key back". It is that the repository now has one
control against this class of exposure, the CORS origin list, and that control stops a browser
and nothing else. Two things would have to be settled before this engine is reachable from
anywhere but loopback: a credential Studio can carry (a query parameter or a reverse proxy that
injects the header, neither tried), and whether `/audit/turns` should project past turns to a
caller at all — [ADR 0012 §8.7](adr/0012-access-seam-principal-and-authorization.md) records it as
unfiltered by design, which was a smaller claim when it sat behind a key. Neither is scheduled.
A2, A3 and A4 stay closed throughout: the `@auth.on` handlers that refuse a client-supplied
state-writing `command` are untouched, and `langgraph.json` keeps `auth.path` for them.

---

### 4.4 `/capabilities`' two durability flags: the direction is fixed, the *kind* of answer is not

**Half of this is closed, and the other half is narrower than it was.**

The literals are gone. `capabilities_for` in `api/routes.py` returned `checkpoint_durable: False`
and `hitl_survives_process_restart: False` under a comment explaining them by `POST /chat`'s
process-local `InMemorySaver`; that route was deleted on 2026-08-18 and `langgraph.json` mounts
`serve/checkpointer.py::conversation_checkpointer` ([ADR 0014](adr/0014-one-conversation-store.md)).
Both now read `durable_checkpointer_configured()` and both report **true**, so the surface no longer
denies something that is built.

**The behaviour behind the second flag was observed on 2026-08-19**, which is what this section used
to ask for. A live turn paused at `ask_user`; the server process was killed and confirmed off port
2024; a fresh process was started; the queue still held the question with the same
`clarification_id`, the prompt re-mounted from checkpointed interrupt state, and answering it resumed
the turn to a correct answer. This section warned about one way that could fail: under
`langgraph dev` the thread index is `.langgraph_ops.pckl` on a ten-second flush while the checkpoint
is SQLite, so the two halves can disagree. They did not. One observation is not a guarantee, and it
was made by hand; the note at
`git-history:docs/analysis/adopting-the-downstream-fork-2026-08-19.md` records the procedure so it
can be repeated, and `2396ca2` deleted it, so repeating it starts with reading it out of git.

**What is still open is what kind of answer the flag is.** `durable_checkpointer_configured()` reads
`langgraph.json` and checks that the named module is on disk. Its own docstring is straight about
this. Its words are *"honest about being a configuration reading"*, because the platform injects the
saver into the graph it runs and this custom app never holds that graph, so there is no object here
to ask. The
flag therefore goes false if the file or module disappears, which is what
[ADR 0009 D4](adr/0009-browsing-and-filtering-api.md) asks of a capability flag, and it would stay
true if the saver were configured and failed to open. That is a smaller gap than a literal, and it
is the gap [ADR 0007 §7](adr/0007-http-surface-and-the-ui-contract.md) is about: an observation and
a configuration reading are not the same claim.

`hitl_survives_process_restart` additionally rests on an argument rather than a measurement. An
`ask_user` interrupt *is* checkpoint state, so it cannot survive less well than the checkpoint. The
argument is sound and the code says so at the line. It is still not the thing the flag's name
promises, and nothing re-checks it per process.

**The observed half is automated as far as one process goes, 2026-08-20.**
`tests/serve/test_a_pause_survives_a_restart_on_disk.py` (added by `04450d4`) drives a real
`ask_user` tool call onto `AsyncSqliteSaver` through `serve.graph.compile_durable`, closes that
graph, calls `compile_durable` again on the same file, and answers the pause from a graph that
never wrote it. `test_a_pause_is_resumed_by_a_graph_that_did_not_write_it:163` asserts both halves
— the turn completes *and* the text a person typed reached `clarifications` — and
`test_the_identity_gate_reads_the_token_off_disk:206` asserts the ADR 0006 B9 gate reads the token
off disk, wrong token first, so a resume that lost `identity` cannot pass by failing open. Three
tests, passing, no model key. The sentence this section used to carry — "one hand-run observation
is the whole of the evidence" — is withdrawn.

**What is left open is the process boundary, and the test says so about itself.** Its header
(`:14-23`) is explicit that a second `compile_durable` is a new event loop, connection, saver and
compiled graph but **not** a new interpreter: the imported modules and every piece of module-level
state survive, so a bug living in one of those passes there. Covering it means spawning
`langgraph dev`, killing it, and waiting for a fresh process to bind, which needs a model key and
a port — which is why the hand procedure above stays written down rather than automated. That,
and nothing wider, is the residue.

**The store has no ceiling, and the risk runs the way round nobody expects.** `langgraph.json` sets
`checkpointer.ttl` to `strategy: delete` at `default_ttl: 129600` — minutes, so 90 days — and that
sweep **cannot fire on the runtime this deployment runs**: `langgraph-runtime-inmem`'s
`Threads.sweep_ttl` is `return (0, 0)` ("Not implemented for inmem server") and nothing in
`site-packages` calls it; the only `sweep_ttl` callers are the sqlite *store*'s. Measured
2026-08-20: `runs/conversations.sqlite` at 92.7 MB with **0 freelist pages**, so nothing has ever
been deleted, and the file grows monotonically with no operator-visible signal. What makes this an
open item rather than a note is the inversion: `langgraph-runtime-inmem` was an undeclared
transitive dependency until 2026-08-20, and a minor release that *implements* the sweep would
silently start deleting 90-day-old threads — under a deployment that reads thread state as durable
history. It is bounded `<0.33` in `[tool.uv] constraint-dependencies` now, which buys a deliberate
upgrade rather than a fix. The repair is a retention decision somebody has to make: either prune on
purpose, or say in the glossary that this store is append-only forever. `docs/glossary.md` and
[ADR 0014 §4](adr/0014-one-conversation-store.md) carry the corrected account; neither picks.

No consumer is misled either way: `ui/lib/schemas.ts`'s `capabilitiesSchema` declares neither field,
so zod strips both and nothing in `ui/` reads them. That is what keeps this out of §1, and it is also
why the remaining half has never cost anything.

---

## 5. Presentation surface

Numbered after §4 rather than inserted, because §4.1 and §4.2 are cited by name from `README.md`,
`failure-modes.md` and the ADRs. The work here lives mostly in the frontend, `ui/`, which is now
part of this tree; each item below was verified by reading it, not inferred from the engine side.

### 5.1 The README shows an answer and cannot yet show a refusal

`docs/images/answered-turn.gif` is the only capture in the tree, taken 2026-08-11 against a live
stack, and `README.md` leads with it: embedded above the fold, captioned with what it is, and
followed by the block that reads the physical names out of the SQL. The terminal transcripts and
the two-line footnote below the documentation table are both gone. That half is done.

What is missing is the other half of the argument. `506ad9b` replaced three PNGs with the single
GIF and deleted the clarification pair — a turn that paused, was answered, and resumed — so the
README now demonstrates only the thing every text-to-SQL project can demonstrate. **A governed
non-answer is what almost none can, and there is currently no capture of one.**

**Any such capture is a demonstration, not a measurement, and must never be captioned as one.**
The existing one comes from one small schema restored locally, on a model and corpus combination
that is not any arm in `runs/eval/`; the README's caption says so, and a second capture would need
the same. No number visible in either is quotable.

### 5.2 A degraded retrieval channel does not stop delivery

The authentication gap that blocked all of this was closed on 2026-08-12 by a shared key the UI
presented on all four of its call sites; the key itself is gone again as of 2026-08-13 (§4.3).
Either way the stack stands up, and standing it up surfaced something worth keeping.

`langgraph dev` wraps the event loop in a blocking-call guard. `botocore`'s retry path calls
`time.sleep`, so with a Bedrock embedder every one of the four facet nodes — `facet_entity`,
`facet_term`, `facet_metric`, `facet_example` — raises `BlockingError` and returns nothing. The
dev server's own advice, `--allow-blocking`, resolves it.

**The turn answered anyway.** The UI reported "5 facets · 55 hits, 4 degraded" with four channels
marked *semantic channel not wired*, retrieval fell back to the lexical channel alone, and the
engine delivered a correct answer whose **outcome** is indistinguishable from one that retrieved
normally.

The record is not silent about it. `facet_channels` carries the three-valued `ChannelState` per
facet, `stamp` derives `facet_degraded` from it via `register/facets.py::is_degraded`, and
`measure/gates.py::_facet_channels_gate` fails an arm on any degraded turn — so an *arm* built
this way is unquotable rather than quietly wrong.

What is decided but not running is the turn-level answer. ADR 0013's first abstention rule is
`retrieval_channel_failed`, evaluated before every other rule for exactly this case: the tables
the turn worked from were chosen by a retriever that is not the declared one. It ships off (§4.1),
so today the engine still delivers. What is genuinely still open is the narrower question the
policy does not settle: whether a turn that *delivers* under a failed channel should say so on the
answer itself, rather than only in the arm-level counter and the trace.

### 5.3 Client-side references to surface the engine does not have

Three readers in `ui/lib/answer-delivery.ts` — `whyLines`, `routedSchemasLabel`,
`corpusVersionLabel` — consume `provenance.uncertainty_flags`, `suspect_columns`,
`routed_schemas` and `corpus_release_hash`. The load-bearing claim is about the *record*:
`register/record.py`'s `RECORD_REGISTER` declares none of the four, and the nearest live
equivalent to the last is its `corpus_content_hash` entry (`register/record.py:153`). "None of the
four exists in `src/`" was too strong and is corrected: `suspect_columns` does exist as a corpus
concept — `corpus/analyst.py:73` exposes it and `govern/check.py:97` normalises it into the
COLUMNS layer — it simply never reaches `provenance`, which is why the reader is inert anyway.
`uncertainty_flags` and `corpus_release_hash` are absent from `src/` outright, and `routed_schemas`
appears only inside `eval/datalake.py`'s `tables_in_routed_schemas` counter, which is a different
field. The functions are inert rather than wrong, and are annotated as such at each site.
Repointing the hash is a behaviour change and wants a decision, not a patch.

Separately, six UI files cited a handoff document that was deleted from this repository, at eight
sites (`components/corpus/asset-edit-sheet.tsx`, `components/schema/column-related.tsx`,
`hooks/use-stream-chat.ts` ×2, `lib/api-client.ts` ×2, `lib/capabilities.ts`,
`lib/mock/fixtures.ts`) — that pair of numbers was files *and* sites, and both held when
re-counted 2026-08-22. **Closed the same day:** all eight were repointed at the ADR that actually
covers the claim (0007 §4/§6/§7, 0009 Amendment 1) or dropped where none does, and
`asset-edit-sheet.tsx` was deleted outright with the unreachable edit affordance. The `D15` figure
is the one that did not hold. `D15` is cited as a design decision at **31 sites
across 12 files**, counted the same day over `ui/**/*.ts` and `ui/**/*.tsx` with build output
excluded: `lib/schemas.ts` ×8, `lib/api-client.ts` ×5, `lib/mock/fixtures.ts` ×5,
`components/schema/knowledge-graph.tsx` ×3, `hooks/queries.ts` and `lib/graph-scope.ts` ×2 each,
and one apiece in `lib/capabilities.ts`, `lib/catalog.ts`, `lib/types.ts`, `app/schema/page.tsx`,
`components/schema/er-diagram.tsx` and `components/schema/schema-search.tsx`. The earlier "twelve"
was the file count written as if it were the site count.
`docs/design-decisions.md` is an ADR index and carries no numbered D-entries, so those
client citations are dead. Cross-schema joins themselves are still a live retrieval
property (ADR 0005); only the D15 label is gone from `docs/`.

A third client/engine divergence, found 2026-08-22 and left open because either repair is a
behaviour change. `ui/lib/types.ts`'s `ASSET_TYPES` offers a `note` filter, but `note` is not a
registered asset type: `register/assets.py`'s `ASSET_REGISTER` declares eight — `schema`, `table`,
`column`, `term`, `metric`, `join`, `few_shot`, `negative_example` — and notes are inline `rules`
on the asset that bears them (ADR 0003 was reversed in full by ADR 0005, which is where the
standalone note asset went). So the Corpus page's `note` pill sends `type=note` to `asset_rows`,
which validates against the register and returns `[]`: a filter that can never match anything.
The mirror of it is that the client offers no `schema` or `column` pill, though both are real
registered types `/corpus/assets` serves. Dropping the dead pill is a UI change; adding the two
missing ones changes what the page lists. Neither is a prose repair, so neither was made.

The split these drifted across is closed — the UI is `ui/` in this tree since `506ad9b` — but no
gate reads it. `check_citations.py`'s `STRICT_ROOTS` is `("src", "tools", "docs", "tests")` and
its `SEARCH_SUFFIXES` does not include `.ts` or `.tsx`, so every citation above is still
unchecked by anything. Whether to extend the gate over `ui/` is the open call, and it is now a
one-tree question rather than a cross-repository one.
