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

Its **calls** are separate again, in [decisions-2026-08-10](analysis/decisions-2026-08-10.md) — 23
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

### 1.7 Three answers were delivered with no SQL at all

`outcome: answered` with an empty `generated_sql` — the model answered from the delivered
schema descriptions without querying. This is a declared state (`stamp.py`), not a
serialization fault, and for a governed system it is the worst available failure: an answer
with no auditable statement.

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
1 345 of 1 351 pinned, mean residual Jaccard 0.702 on v4 and 0.70 on v5, against 0.579 for the
unpinned run1/run2 pair. It buys real resolution — the pinned v3-fold → v4 comparison is
discordant on 9.3% of questions against the unpinned null's 12.7%, which is SE(net) 0.83pp
instead of 0.97pp.

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

**Two confirmed governance defects, both reproduced, neither yet fixed.** They are here rather
than in §1 because they are properties of the checker, not of the model's answers. Neither has
fired in the field, so no measurement is affected — but "has not fired" is a fact about this
dataset, not a property of the code.

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

Fix: `defined` must absorb every non-`Table` source alias in the tree, or the tree-wide handle
map must be replaced with the `traverse_scope` walk `binding.py` already uses, which removes the
disagreement entirely.

**A self-colliding table fails to poison its bare name.** The `own_ambiguous` guard returns
before the cross-schema poison write, so a table whose own columns collide by case neither
registers nor poisons its bare key, and another schema's table of the same name takes sole
ownership of it. Two more early returns — an absent corpus entry, and a table with no
`physical_name` — reach the same state. Fix: move the poison write above the guard.

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

### 3.6 `--resume` does not enforce the treatment identity it records

The artifact filename carries `--model`, `--effort`, `--top-n`, `--embed`, the provider and
`--prompt-variant`, and a renamed tag aborts rather than restarting silently. It does **not**
carry `--corpus-dir`, `--dataset` or `--replay-routing`. Every row carries
`corpus_content_hash` and `prompt_set_hash`, and **nothing reads either back** — the only
mentions of them in the driver are comments and help text.

So: pull `../BIRD-corpus`, resume, and one artifact holds two corpora. Every gate passes and the
driver prints that the numbers are quotable as a single arm. Given that the corpus is the
treatment identity, this is the most severe defect in the instrument. An explicit `--out` bypasses
the tag entirely, so the `--prompt-variant` guard does not apply to a run that names its own file.

Separately, without `--resume` the driver appends to an existing artifact with no dedup, and
reports EX over the duplicated population before the population check raises.

### 3.6a A clarification turn carries no treatment identity

Every row in the 2026-08-09 artifacts whose `corpus_content_hash` is `None` is a zero-licensed
turn that ended in a clarifying question — 6 of 6 in v3-fold, 8 of 8 in v3-pinned, 4 of 4 in v4.
A turn that terminates before routing never reaches whatever stamps the identity, so `None` here
does not mean "written before the field existed"; it means the field has a path it is not
written on.

It is 0.4% of rows and all of them are abstentions, so no headline number moves. Two
consequences that are not zero: those rows cannot prove which corpus produced them, and §3.6's
resume guard warns about them on **every** legitimate resume, which is the shape that teaches a
reader to ignore a warning.

### 3.7 Three fields report the intent rather than the outcome

- **`routing_pinned`** is read off the question dict, where `attach_pinned_routing` wrote it —
  never off the turn. The pin falls back to live routing when no pinned schema is known to this
  corpus, when the pin is partial, and when `route_node` never runs; the row says `true` in all
  three. There is no reader anywhere in `src/` or `tools/`, so the unpinned fraction is not
  recoverable from the artifact at all.
- **The drift baseline** is built from every row of the replayed artifact, including the ones
  `routing_from_artifact` deliberately skipped for having an empty shortlist. Those were never
  pinned, are guaranteed to differ, and enter at Jaccard 0. The printed residual is deflated by
  exactly the rows the pin refused to touch.
- **The refusal histogram counts `sample_rows` probes as governance refusals.** `_attempt_trace`
  iterates the ledger unfiltered; every other reader goes through `answering_attempts` first.
  On the v3-fold arm this moves the capped-turn histogram by 21 `passed` and 3 `r_ambiguous_fold`
  attempts. `serve/ledger.py` states the rule this breaks: three copies of "which attempts count"
  is three answers. The driver is the fourth.

`attempts: []` also conflates three distinct facts — a retrieval decline with an empty ledger, an
absent `execution` record, and a concurrency crash row — and the histogram prints with no total
and no unattributed bucket, so a run whose refusals are mostly declines reads as "governance
rarely refused".

### 3.8 Two comparability knobs cannot reach the run they name

`w_lexical` and `w_semantic` are `Role.comparability`, enter `config_hash_keys()`, and are bound
into `FUSE_WEIGHTS` at import. `combine_channels` takes no state and all three readers go through
it. An arm can declare `w_lexical: 0.9`, move its config hash, and behave identically.

`GOVERNED_BI_RAIL_NODE_TIMEOUT_S`, `GOVERNED_BI_AGENT_NODE_TIMEOUT_S` and
`GOVERNED_BI_AGENT_RECURSION_LIMIT` outrank `knobs_resolved`, which is filled from
`knob_defaults()` alone. Setting one changes behaviour while the record publishes the register
default and the config hash does not move.

Neither has been exercised: every run record carries the default weights, and none of the three
environment variables is set anywhere in the repository.

### 3.9 Eight tests cannot fail

Of 25 mutations applied to the instrument code, 8 survived a green suite. Each names a behaviour
a docstring in the same diff explicitly claims: `routing_pinned` pinned to either constant,
`corpus_content_hash` and `prompt_set_hash` set to `None`, `_attempt_trace` returning empty,
`computed_correct` always `None`, and both ends of the eviction chain — `assemble`'s write and
`stamp`'s projection.

The pattern is one shape: **asserting that a constant equals itself.**
`test_a_measured_row_names_both_treatment_identities` asserts `"corpus_content_hash" in row`,
which `None` satisfies. Its sibling was written to close exactly that hole and asserts against
`register.prompts.prompt_set_hash` instead of against a row, so the row remains free to carry a
constant. `test_comparability_knobs` asserts `FUSE_WEIGHTS["lexical"] == knob_default("w_lexical")`,
which is the tautology behind §3.8.

### 3.10 Declared machinery with no wire is this repository's recurring defect

One shape keeps recurring: something is declared in the register, stamped by a node, or promised
in a docstring, and **nothing on the other end reads it**. Each instance is individually small;
together they are the reason numbers here have twice been quotable and wrong.

A sweep found 28. **Fourteen are fixed** — the five that recorded a *wrong* value rather than a
missing one, the nine record fields that reached the turn record and no artifact, and
`lexical_coverage`, whose producer turned out to be dead rather than absent. Evidence and the
per-field decisions are in [declared-not-consumed](analysis/declared-not-consumed.md).

Fourteen remain, and none of them currently corrupts a number:

Thirteen unconsumed knobs and one half-wired state channel:

| | |
|---|---|
| `git_sha`, `git_main_sha`, `working_tree_dirty`, `diff_sha256` | the resume-drift keys, null on every row of every arm, so the gate compares each against itself |
| `schemas_under_test`, `question_subset`, `split` | scope keys nothing writes |
| `serve_workers`, `build_workers` | null while the driver runs ten workers |
| `expand_hops` | a comparability knob with no reader: setting it changes no behaviour and does change the config hash. `pulled_in` now reaches the row, which makes the knob's own question answerable — the measurement half exists, the behaviour half does not |
| `negative_tau`, `facet_model`, `rewrite_model` | dead declarations |
| `clarifications` | a `ServeState` channel with two writers and no reader outside `state.py` |

The common cause is that declaring and consuming live in different files and nothing forces them
to meet. **Two of the fixed items were invisible to any static rule by construction** — in each
the declaration had a consumer and the missing wire was on the recording side, so only the
artifacts showed them.

`tools/check_declared_is_consumed.py` closes the statically-visible part: four rules over knobs,
record fields and state channels, mutation-verified against a fixture tree. It reported 27
violations when written and reports 14 now. **It is deliberately not wired into CI yet**, and
`tests/conformance/test_register_closure.py` carries that decision with the condition for
reversing it. Its own docstring states the blind spot: rule K1 credits any occurrence of a knob's
name, so a coincidental string literal launders one.

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

What remains open is not a better judge. It is that **the thesis of this project has never been
measured**: the layer stack, the allowlist and the scope gate have no adversarial evaluation, so
what governance buys has no number. The scope gate's fail-open on affirmative-prefixed replies was
found by reading the code, not by a test, because nothing tries to get past it.

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

---

### 3.13 The treatment must be declared, and only three arms have declared it

`arms.toml` arrived on 2026-08-11 with audit D9's fix: `eval/report.py::knobs_comparable`
refuses a pair that cannot name what changed, and the profile is where the name comes from.
Three arms are declared — `v3_fold`, `v4`, `v5`. Any other artifact in `runs/eval/` is
`cannot_evaluate` in a comparison until someone writes down what it changed, which is the
intended pressure and not a defect.

Two things about that fix are owed:

* **Neither half is verified on real artifacts.** `runs/` is gitignored and the designated null
  pair is not on the machine that made the change, so both D9 controls in
  `tests/eval/test_the_delivery_gate_can_fail.py` skipped. The logic was exercised on synthetic
  fixtures and nine mutations, which is not the same as running it on `run1`/`run2`.
* **`reconcile` has one caller: its tests.** It compares a profile's declared corpus against
  what a row recorded, and nothing in the eval driver calls it yet — declared machinery with
  no wire, §3.10's shape, entered here deliberately rather than left to be discovered.

---

## 5. Presentation surface

Numbered after §4 rather than inserted, because §4.1 and §4.2 are cited by name from `README.md`,
`failure-modes.md` and the ADRs. The work here lives mostly in the frontend, `ui/`, which is now
part of this tree; each item below was verified by reading it, not inferred from the engine side.

### 5.1 The README still illustrates the engine with terminal transcripts

Two captures now exist, in `docs/images/`, taken 2026-08-11 against a live stack: one answered
turn showing its SQL, and one clarification that paused, was answered, and resumed. Neither is
referenced from `README.md` yet, which still demonstrates the engine with terminal output and
points at the UI in two lines below the documentation table. The remaining work is the README
edit, not the capture.

**These are a demonstration, not a measurement, and must never be captioned as one.** They come
from one small schema restored locally, on a model and corpus combination that is not any arm in
`runs/eval/`. No number visible in them is quotable.

What they show is the argument the prose already makes: an answer is what every text-to-SQL
demonstration shows, and a governed non-answer is what almost none can.

### 5.2 A degraded retrieval channel does not stop delivery

The authentication gap that blocked all of this is closed — the UI now presents the key on all
four of its call sites — but standing the stack up surfaced something worth keeping.

`langgraph dev` wraps the event loop in a blocking-call guard. `botocore`'s retry path calls
`time.sleep`, so with a Bedrock embedder every one of the four facet nodes — `facet_entity`,
`facet_term`, `facet_metric`, `facet_example` — raises `BlockingError` and returns nothing. The
dev server's own advice, `--allow-blocking`, resolves it.

**The turn answered anyway.** The UI reported "5 facets · 55 hits, 4 degraded" with four channels
marked *semantic channel not wired*, retrieval fell back to the lexical channel alone, and the
engine delivered a correct answer with no outcome-level signal that most of its retrieval had
failed. The degradation is visible in the reasoning trace and nowhere in the record. This is the
retrieval analogue of §3.7: a field reporting intent rather than outcome. Whether a turn whose
semantic channel produced zero hits should be distinguishable from one that worked, in the
record rather than only in the trace, is an open question and a candidate for §1.

### 5.3 Client-side references to surface the engine does not have

Three readers in `lib/answer-delivery.ts` — `whyLines`, `routedSchemasLabel`,
`corpusVersionLabel` — consume `provenance.uncertainty_flags`, `suspect_columns`,
`routed_schemas` and `corpus_release_hash`. None of the four exists in `src/`, and the record
register declares no such field; the nearest live equivalent to the last is
`corpus_content_hash` (`register/record.py:151`). The functions are inert rather than wrong, and
are annotated as such at each site. Repointing the hash is a behaviour change and wants a
decision, not a patch.

Separately, eight UI files still cite a handoff document that was deleted from this repository,
and two cite a `D15` that appears nowhere in `docs/`.

Every item in this section is a citation or a contract that drifted across the repository
boundary, and `check_citations.py` would have caught all of them inside one tree — its
`STRICT_ROOTS` already covers `docs/`, but no gate in either repository can see across the split.
Whether to merge the UI here is open, and is a separate question from keeping the corpus and the
lake external: that one is settled by measurement identity, and merging a client would not touch
`corpus_content_hash`. Recorded in
[the strategy checkpoint](analysis/strategy-checkpoint-2026-08-11.md).
