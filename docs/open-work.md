# Open work

What is known to be unfinished, with the evidence for each. Anything closed is deleted from
this page rather than struck through — the git history is the record of what changed, and a
page that carries both states is a page nobody trusts as a to-do list.

Nothing here is carried from an earlier document on the strength of having been written down.
An item survives only if it was re-verified against the current tree, the current corpus
(`../BIRD-corpus` @ `30872d3`), or the 2026-08-09 run artifact. Claims that could not be
re-verified were dropped, not demoted.

Binding design lives in the [ADRs](adr/). This is a work list, not a decision record.

The 2026-08-10 implementation audit is a separate page, because it is a one-time systematic sweep
with its own phased remediation order rather than an accumulating list:
[audit-2026-08-10](analysis/audit-2026-08-10.md). Items migrate from there to here as phases close.

Its **calls** are separate again, in [decisions-2026-08-10](analysis/decisions-2026-08-10.md) — 30
choices taken while working it, each with the alternative that was rejected and what would reverse it.
Read it before re-opening any of them: four entries retract their own earlier reasoning in place, so
the argument you are about to make may already be there with the measurement that killed it.

---

## 1. Engine — measured, with a known ceiling

Current arm: **v4**, engine `3c0079a`, corpus `30872d3`, **EX 0.676** (clean 0.6762).
438 failures. Method and per-case diagnosis: [failure modes](failure-modes.md).

Where the remaining failures are. The six rows partition the 438 — every failure lands in
exactly one — so the coverage-based rows below are stated again as cross-cutting totals,
because those are the numbers §1.5 and §7 are about:

| bucket | n | nature |
|---|---:|---|
| full-coverage answered wrong | **257** | genuine semantics — the generic text-to-SQL problem |
| answered, frozen-literal gold | 75 | dataset defect, unwinnable |
| capped | 49 | the agent spent all five attempts without a passing statement |
| answered, coverage incomplete | 33 | retrieval |
| refused | 20 | none with full coverage |
| clarification | 4 | all zero-licensed |

Across all outcomes: **73** failures had incomplete table coverage and **85** had a
frozen-literal gold. The `refused` and `capped` rows are where those two overlap the
outcome buckets — 19 of the 20 refusals had partial or no coverage and the twentieth
had a tableless gold, and 26 of the 49 capped turns were not fully covered either.

### 1.2 The agent budgets its attempts blind

`run_query` is capped at `run_query_attempt_cap` (5). A governance-refused attempt **consumes
one**; only an infrastructure exception refunds. The agent is told the cap exists only once it
has already hit it, so it spends attempts on single-table probes (`LIMIT 3`, `LIMIT 5`) against
a budget it cannot see.

Returning "attempt 2 of 5" in the tool reply costs nothing. `serve/tools.py`.

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

### 1.5 Seventy-nine questions never had their gold tables licensed

Table coverage on the v4 arm is **0.936** — 1 145 of 1 224 questions with a real gold
statement had every gold table licensed. The engine answered 6 of the uncovered 79 correctly
and missed the other 73, which is the cross-cutting coverage total under §1.

This is a **licensing figure, not a delivered one**; see §3.3 for what the char budget drops on
top of it. Concentrated in `works_cycles` (7), then `airline`, `law_episode` and `superstore`
(5 each).

This is still the largest *winnable* bucket after the 257 semantic errors, and unlike those it
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

1. **Metric expressions that do not resolve on `base_table`** — `sales` total value,
   `ice_hockey_draft` heights, `mondial_geo` gdp/capita. Either repair them or require
   qualified columns.
2. **Six decoy-vocabulary losses** — reclaim terms; start with `card_games` "set code" →
   `sets.code`.
3. **Thin coverage** — terms and metrics for `university`; densify `regional_sales`; metrics
   for `retails` and `world`.
4. **`soccer_2016` routing summary** leads with a slug echo of "soccer"; it should open with
   "IPL cricket…". Related to §1.4.
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

`../BIRD-corpus` is in git and still cannot be regenerated from anything committed — but not
for the reason this entry used to give. `tools/corpus_rebuild/01–03` **are** in the tree and
**do** write assets: schema, table and column structure, join edges, few-shots. What has no
producer anywhere is the prose half — every summary, term, metric and note — which those
scripts leave as `TODO <identifier>` for a writing agent to fill in per schema.

So the mechanical half is rebuildable and the corpus is not. Versioned is not
reproducible-from-source, and no document may describe it as such.

### 3.2a `r_ambiguous_fold`'s resolver admits statements it must refuse

**Two confirmed governance defects, both reproduced, both fixed 2026-08-12.** They are here rather
than in §1 because they are properties of the checker, not of the model's answers. Neither had
fired in the field, so no measurement is affected.

**`_sources` is blind to derived sources.** It walks `exp.Table` and excludes only CTE names, so
it has no notion of a subquery, `LATERAL` or `VALUES` alias. `binding.py::_classify_sources`
registers those as `kind="derived"`. A handle that is a derived source in one scope and a
base-table alias in another is therefore *tree-unambiguous* by `_sources`'s conflict test — there
is nothing for it to conflict with — while `binding.py` resolves it to the derived source. The
two resolvers disagree, which is the exact condition the rule exists to detect. Reproduced:

```sql
SELECT p.name
FROM (SELECT o.name, x.name FROM s.places AS o JOIN s.people AS x ON o.id = x.id) AS p
WHERE EXISTS (SELECT 1 FROM s.people AS p WHERE p.id = 1)
```

With `s.places.name` and `s.people.Name` both licensed, this returns `passed: True` and emits
`p."Name"` — the derived source exposes both spellings, so the statement is valid, executes, and
reads a different column of a different table. `bind()` marks `p.name` as `opaque: derived:p`, so
the column layer never inspects it and nothing downstream can catch it. Before the narrowing this
refused with `r_ambiguous_fold`.

**Fixed by the second option, and the first was tried and withdrawn.** Absorbing every derived
alias into a tree-wide set closes the defect and costs false refusals in a shape its own controls
could not see: a handle that is a derived alias in one scope loses per-table spelling in *every*
scope, so `SELECT r."Name" FROM sales.regions AS r WHERE EXISTS (SELECT 1 FROM (SELECT 1 AS z) AS r)`
refused `r_ambiguous_fold` for a reference naming exactly one table. Measured on the adversarial
suite: false-refusal 2/46 under the tree-wide rule, 0/46 under the per-scope one.
`pipeline._column_sources` now resolves each reference in its own scope and then its ancestors —
`binding.py::_lookup`'s walk over the same `scope.selected_sources` mapping — so the two resolvers
agree by construction. (Both read `scope.sources` until 2026-08-19, when that mapping turned out to
merge every visible CTE into every scope and both moved off it together; see
[the binding-scope fix](analysis/binding-scope-and-statement-timeout-2026-08-19.md).) Both spellings of the false-refusal shape are benign cases in
`govern/adversarial.toml`.

One consequence is a widening, not a narrowing: a handle reused for two different tables in two
different scopes now resolves correctly in each rather than refusing.
`tests/govern/test_guard_pipeline_ledger.py::test_a_handle_reused_for_two_tables_is_spelled_from_each_scope_s_own`
pins the spelling, which is what a refusal-only assertion could not.

**A self-colliding table fails to poison its bare name.** The `own_ambiguous` guard returns
before the cross-schema poison write, so a table whose own columns collide by case neither
registers nor poisons its bare key, and another schema's table of the same name takes sole
ownership of it. Two more early returns — an absent corpus entry, and a table with no
`physical_name` — reach the same state. Fix: move the poison write above the guard. Fixed; the
poison write now runs above the guard, and `a_spelling_self_colliding_table_keeps_its_bare_handle`
is the case.

**Field reachability, measured.** Zero of 1 342 parsed statements on the v3-fold arm contain a
derived-source alias that collides with a table handle, and zero of the 656 tables in
`../BIRD-corpus` @ `30872d3` collide with themselves by case. The +5.3pp attributed to the
narrowing is therefore not contaminated. The 28 bare table names that *are* shared across schemas
do exercise the poison path, so only the ordering hole is unreached.

**A corpus rebuild can widen both.** Re-check both properties before trusting the resolver on a
rebuilt corpus.

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

**Telling the agent its remaining attempt budget** (§1.2) is a cheap fix and is *not* applied,
because it changes behaviour and would become a second variable in the next arm. Apply it with
its own A/B, not alongside something else.

1. **Comparability: run1, run2 and v3-pinned ran on `ba8cef2` or earlier.** `r_ambiguous_fold`
   was narrowed after them and it moves ~119 turns, so those three are **not** paired-comparable
   with anything measured since on what the fold touches. **v4 is the control for new arms**,
   and v3-fold is the artifact new arms pin their routing to.
2. **A hard cancel after the agent's grace period can leave an executed statement out of the
   ledger.** A turn killed between `execute` and the ledger write records no attempt for SQL
   the database actually ran. Rare, and it makes the ledger under-count rather than invent —
   but "the ledger is the record of what ran" is a property this repository leans on.


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

### 3.8 The knobs that could not reach the run they name — closed

**Both halves are fixed; the entry stays because the shape recurs and the artifacts still
predate it.**

`w_lexical`, `w_semantic` and `semantic_scale_ceiling` were module constants built from
`knob_default` at import, so no request could move them: an arm could declare `w_lexical: 0.9`,
move its config hash, and behave identically. They now travel as one frozen
`serve/runtime.py::ChannelScale`, resolved per turn by `channel_scale` through `float_knob` — the
same state → `knobs_resolved` → register precedence every other knob uses — and passed into
`combine_channels`, which has no default for it. There is no `FUSE_WEIGHTS` constant left.
Decision and alternatives: [decisions-2026-08-10](analysis/decisions-2026-08-10.md) D-22.

`GOVERNED_BI_RAIL_NODE_TIMEOUT_S`, `GOVERNED_BI_AGENT_NODE_TIMEOUT_S` and
`GOVERNED_BI_AGENT_RECURSION_LIMIT` still outrank the register at their readers — that is the
intended precedence — but the record no longer publishes the default underneath them.
`register/knobs.py::env_override` is the recording half, applied last in
`session._resolved_knobs`, and it copies the readers' two parsing rules (blank is unset; the
declared default decides the cast) so the record cannot disagree with the reader.
`tests/serve/test_the_record_follows_the_knob.py` asserts each **on its value**.

What is not claimed is that either has been exercised in anger: no arm on disk carries anything
but the default weights, and no run configuration in this repository exports the three
variables. Several tests under `tests/serve/` do, which is the point — they are what keeps the
wire alive.

### 3.9 The eight tests that could not fail are pinned rather than repaired-and-forgotten

All eight are covered by the nine declared mutations under the `s39-` prefix in
`tools/mutation_catalogue.py`, verified caught on 2026-08-11: `routing_pinned` pinned to either
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
the per-field decisions are in [declared-not-consumed](analysis/declared-not-consumed.md).

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
violations when written and reports 6 now.

**Tier 1 is clear as of 2026-08-12**, which is the condition
`tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py` names for a CI step. All five items —
`llm_reasoning_effort` unreadable on the proxy, `llm_utility_provider` and `embedding_provider`
publishing `"openai"`, `chat_model` null on four arms with the value in an undeclared key, three
environment variables outranking `knobs_resolved`, and `sqlglot_version` absent from every row —
now have writers that fire, each asserted **on its value** in
`tests/serve/test_the_record_follows_the_knob.py`.

**Which artifacts gain, precisely.** All **seven proxy arms** in `runs/eval/` predate the wires and
gain nothing, so [declared-not-consumed](analysis/declared-not-consumed.md) §1–§5 remain the correct
description of every number quoted from them — which is every number in this document. Its own
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

**The gate is still not a CI step**, and the reason has changed. It exits 1 on the six findings
in the table above, so a step would fail every commit, and waiving six genuine findings to go
green is the lie it was written to catch. Three of the six need a *decision* rather than a wire:
`expand_hops` and
`negative_tau` are comparability knobs whose readers would live in `retrieve/`, and
`clarifications` is a question about the clarification protocol.

What did land is the half that was missing either way:
`test_the_declared_but_unconsumed_set_does_not_grow` runs the gate on **every commit** against
the six findings pinned by name, so a seventh fails the build with the offending name, and
closing one fails it too — because a shrinking list nobody updates is how a stale count survives.
Names and not a count: six findings and six *different* findings are the same integer.

Its own docstring states the blind spot: rule K1 credits any occurrence of a knob's name, so a
coincidental string literal launders one. That is why the eight closed above are also asserted on
their **values**, in `tests/eval/test_the_row_names_the_harness_that_produced_it.py`, and why
`build_workers` is left red rather than given a number.

**Two capabilities landed before their callers, on purpose (2026-08-19).** Both are the shape this
section is about and neither is visible to `check_declared_is_consumed.py`, which reads the register
and not the call graph — so they are written down here instead:

- `eval/power.py::require_power` refuses to declare an arm that cannot detect its own hypothesis.
  **Nothing calls it.** `ArmSpec` carries no hypothesised effect, so there is nothing to enforce it
  against. Closing this means a field on `ArmSpec` and a driver-side check, at which point the arm
  that cannot detect its target fails before it spends anything.
- `corpus/snapshot.py` puts a corpus back. **Nothing calls it**, because no path in this repository
  writes to a corpus during a run. It is here because the first path that does will need it on its
  first turn: adding one file inside `corpus_root` moves `corpus_content_hash`, which
  `measure/gates.py::_corpus_content_hash_gate` reads as an arm running on two corpora.

The distinction that keeps these off the list above: a knob with no reader **changes the config
hash** while changing no behaviour, so setting it produces a row that lies. A function with no caller
produces nothing at all. The failure mode is a reader believing the capability is in force, which is
what this entry exists to prevent.

### 3.11 Selective prediction is closed at 0.80, and the reflector closed it

The reflector ran, once, as the last untested source of information: everything that does not
read meaning had already been measured and capped at OOF AUC 0.721. **It scores 0.597** — worse
than the count of tokens the agent emitted, and combining the two is worse than the token count
alone. Full result: [risk coverage](analysis/risk-coverage-v4.md) §6.

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

At 0.936 coverage this is not currently expensive, which is why it is a question and not an
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
  been run and the outward wording does not change until it has (strategy checkpoint §2.6).

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

---

### 3.13 The treatment must be declared, and only three arms have declared it

`arms.toml` arrived on 2026-08-11 with audit D9's fix: `eval/report.py::knobs_comparable`
refuses a pair that cannot name what changed, and the profile is where the name comes from.
Three arms are declared — `v3_fold`, `v4`, `v5`. Any other artifact in `runs/eval/` is
`cannot_evaluate` in a comparison until someone writes down what it changed, which is the
intended pressure and not a defect.

**`reconcile` is wired**, to `--arm`: the driver looks the profile up before the first paid
question and refuses a run labelled with an arm whose declared corpus is not the one the session
loaded, then reconciles every row again in the report. Fixing the wire also found the function
was vacuous — see the D9 row in [the audit](analysis/audit-2026-08-10.md) for the two namespaces
it was comparing.

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
  declared-not-consumed finding 7.
* **`compare_to`, `description` and `notes` now have a reader** (the driver prints them under
  `--arm`) but nothing checks `compare_to` against the pair a comparison is actually run on.
* `GateResult.render()` prints `field`, `observed`, `population` and `detail` and **omits
  `condition`** — the one line saying what the gate actually required. A reader of the driver's
  output gets the verdict without the criterion. (The withdrawn 95% distinctness rule is no
  longer asserted anywhere: `CONTEXT_HASH_THRESHOLD` survives only as an unused parameter that
  `context_hashes_distinct` reports in its detail line as retired, and both that function and
  `_context_hash_gate` say so in their text.)

---

### 4.3 Nothing authenticates, and audit A1 and A7 are open again

`GOVERNED_BI_API_KEY` was removed on 2026-08-13 with the middleware and the `Auth` plumbing that
read it ([ADR 0007](adr/0007-http-surface-and-the-ui-contract.md) Amendment 3). No route asks for
a credential; reaching the port is sufficient. The two findings the key closed on 2026-08-12 are
therefore live again in the words they were written in
([audit-2026-08-10](analysis/audit-2026-08-10.md)):

- **A1** — every route is unauthenticated, so anything that can open a socket to `:2024` can post
  a turn and execute governed SQL against the configured database.
- **A7** — `/audit/turns` and `/audit/turns/{id}/trace` hand that caller every thread's SQL, the
  full turn records, and an absolute path to the conversation database. **Wider since 2026-08-18**
  ([ADR 0014](adr/0014-one-conversation-store.md)): the record accumulates on `ServeState.turns`,
  so the platform's own unauthenticated `/threads/{id}/state` — and any `values` stream frame —
  now carries *every* prior turn of the thread rather than the newest one. That is audit B1's leak
  surface enlarged by this change and mitigated by nothing.

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
was made by hand; the note in `docs/analysis/adopting-the-downstream-fork-2026-08-19.md` records the procedure so
it can be repeated.

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
`routed_schemas` and `corpus_release_hash`. None of the four exists in `src/`, and
`register/record.py`'s `RECORD_REGISTER` declares no such field; the nearest live equivalent to
the last is the `corpus_content_hash` entry there. The functions are inert rather than wrong, and
are annotated as such at each site. Repointing the hash is a behaviour change and wants a
decision, not a patch.

Separately, six UI files cite a handoff document that was deleted from this repository, at eight
sites (`components/corpus/asset-edit-sheet.tsx`, `components/schema/column-related.tsx`,
`hooks/use-stream-chat.ts` ×2, `lib/api-client.ts` ×2, `lib/capabilities.ts`,
`lib/mock/fixtures.ts`). Twelve cite `D15` as a design decision, and `docs/design-decisions.md`
carries no numbered decisions at all — the only surviving mentions in `docs/` are ADR 0002 and
ADR 0005, both of which cite it *as* a design-decisions.md entry that is not there. So the
citation is dead on both sides of the merge, not only the client's.

The split these drifted across is closed — the UI is `ui/` in this tree since `506ad9b` — but no
gate reads it. `check_citations.py`'s `STRICT_ROOTS` is `("src", "tools", "docs", "tests")` and
its `SEARCH_SUFFIXES` does not include `.ts` or `.tsx`, so every citation above is still
unchecked by anything. Whether to extend the gate over `ui/` is the open call, and it is now a
one-tree question rather than a cross-repository one. Background in
[the strategy checkpoint](analysis/strategy-checkpoint-2026-08-11.md).
