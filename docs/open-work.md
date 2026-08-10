# Open work

What is known to be unfinished, with the evidence for each. Anything closed is deleted from
this page rather than struck through — the git history is the record of what changed, and a
page that carries both states is a page nobody trusts as a to-do list.

Nothing here is carried from an earlier document on the strength of having been written down.
An item survives only if it was re-verified against the current tree, the current corpus
(`../BIRD-corpus` @ `30872d3`), or the 2026-08-09 run artifact. Claims that could not be
re-verified were dropped, not demoted.

Binding design lives in the [ADRs](adr/). This is a work list, not a decision record.

---

## 1. Engine — measured, with a known ceiling

Current arm: **v3-fold**, engine `4f7430a`, corpus `30872d3`, **EX 0.664** (clean 0.6641).
454 failures. Method and per-case diagnosis: [failure modes](failure-modes.md).

Where the remaining failures are:

| bucket | n | nature |
|---|---:|---|
| full-coverage answered wrong | **262** | genuine semantics — the generic text-to-SQL problem |
| coverage incomplete | 86 | retrieval |
| frozen-literal gold | 85 | dataset defect, unwinnable |
| capped | 57 | the agent spent all five attempts without a passing statement |
| refused | 23 | **all** coverage failures, none with full coverage |
| clarification | 6 | all zero-licensed |

### 1.2 The agent budgets its attempts blind

`run_query` is capped at `run_query_attempt_cap` (5). A governance-refused attempt **consumes
one**; only an infrastructure exception refunds. The agent is told the cap exists only once it
has already hit it, so it spends attempts on single-table probes (`LIMIT 3`, `LIMIT 5`) against
a budget it cannot see.

Returning "attempt 2 of 5" in the tool reply costs nothing. `serve/tools.py`.

### 1.3 Six turns licensed nothing at all

Six of 1 351 turns routed zero schemas and licensed zero tables. All six asked a
clarifying question — the correct response to an empty context, and the reason they are
**not** an agent-behaviour problem. They are a retrieval defect, isolated and small:
`licensed` has a median of 26 and these are the only rows below 5.

### 1.4 Twenty-two answers were written against the wrong schema

Failures where the prediction and the gold share no schema at all. The pairs are the
semantically adjacent decoy sets, `mondial_geo ↔ world` in both directions:

| gold | predicted |
|---|---|
| `ice_hockey_draft` | `hockey` |
| `mondial_geo` | `world` |
| `world` | `mondial_geo` |
| `movielens` | `movies_4` |
| `regional_sales` | `address`, `car_retails` |
| `sales` | `movie_3` |

The gold schema was routed in these turns. This is disambiguation **inside** the licensed set,
not routing recall — the agent is handed tables from several schemas and picks the wrong one.

### 1.5 Ninety-four questions never had their gold tables licensed

Table coverage on the v3-fold arm is **0.923** — 1 130 of 1 224 questions with a real gold
statement had every gold table licensed. The engine answered 8 of the uncovered 94 correctly and
missed the other 86, which is the "coverage incomplete" bucket in §1.

This is a **licensing figure, not a delivered one**; see §3.3 for what the char budget drops on
top of it. Concentrated in `superstore`, `hockey` and `beer_factory`.

This is now the largest *winnable* bucket after the 262 semantic errors, and unlike those it is
corpus and retrieval work rather than generic text-to-SQL.

### 1.6 Ten capped turns had every gold table and still built no join

Twenty-one of the 57 capped turns had full coverage; in 10 of those the gold answer needs more
than one table and the final draft joins none. The tables were in context. What is missing is
relationship grounding, not table budget — raising `table` budget above 8 does not address it.

The other 28 capped turns had partial or no coverage, so the capped bucket is now mostly a
retrieval problem rather than a join-assembly one. That is a change of kind from earlier arms,
where 106 of 133 capped turns had full coverage.

### 1.7 Five answers were delivered with no SQL at all

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

### 3.1 `--replay-routing` works, and the arm that most needed it did not use it

Exercised once, on v3-pinned: 1 343 of 1 351 pinned, and the residual licensed-set drift it
prints went from Jaccard 0.579 (unpinned, run1 vs run2) to 0.701.

**The v3-fold arm did not pass the flag.** So v3-fold vs v3-pinned differs by the fold fix
*and* by routing. Routing churn is unbiased (run1 vs run2: net −12, χ²=0.70), so the +5.3pp
attribution stands, but the discordance is inflated — 189 against the null's 172. Pass it next
time; it costs nothing.

### 3.2 The corpus is versioned and still not rebuildable

`../BIRD-corpus` is in git and still cannot be regenerated from anything committed — but not
for the reason this entry used to give. `scripts/corpus_rebuild/01–03` **are** in the tree and
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

Measured on v3-fold, now that `context_evicted` survives the turn: the 80 000-char budget bit on
**19 of 1 351 turns (1.4%)**, dropping bodies only and never a whole table. The advice that the
budget already binds and must not be cut is **withdrawn**; it rested on an offline
reconstruction, not on this measurement.

So there is headroom. Whether to use it is a question about whether the content earns its
place, not about whether it fits. An `agent_core` call averages 22 285 input tokens and the
node makes 2.46 calls per turn, so the 1 963 repeat calls within a turn carry **58.6%** of all
input — measured from `usage`, not bounded.

### 3.4 Held back on purpose

**Telling the agent its remaining attempt budget** (§1.2) is a cheap fix and is *not* applied,
because it changes behaviour and would become a second variable in the next arm. Apply it with
its own A/B, not alongside something else.

1. **Comparability: run1, run2 and the v3 arm ran on `ba8cef2` or earlier.** `r_ambiguous_fold`
   was narrowed after them and it moves ~119 turns, so those three are **not** paired-comparable
   with anything measured since on what the fold touches. **v3-fold is the control for new
   arms.**
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

---

## 4. Open questions

### 4.1 What the headline should be

On the v3-fold arm the engine commits to 1 265 of 1 351 turns at **0.709** accuracy and abstains
on 86 (6.4%). Of those 86, **69 can be priced** — for the other 17 the dataset ships no gold
fingerprint, so what the engine would have got is unknowable, not zero. Of the 69, 13 would have
been correct: **81.2% of priced abstentions would have been wrong** had the engine been forced
to answer. Delivered accuracy is **3.76×** the accuracy it withheld.

The 69/86 split is not a rounding detail. Abstention precision is computed over a subset the
dataset selected, not a random one, so it is a figure about the priced population and must be
quoted that way.

That is a claim about *calibration*, and it is orthogonal to EX — a comparison system reporting
a higher EX says nothing about which of its answers to distrust. Whether the project leads with
this or with EX decides what gets built next, and the two point at different work.

Making it a result rather than an observation needs a **contrast arm**: the same questions with
the governance layer off, to show that the turns the engine declined are turns an ungoverned
engine answers wrongly and confidently. That experiment does not exist yet.

### 4.2 Whether `licensed` should keep serving two masters

`licensed` is both the retrieval budget (`ASSET_REGISTER[table].budget = 8`) and the governance
allowlist that `check()` Layer 6 enforces. A retrieval miss therefore becomes a hard refusal
rather than a degraded answer — 19 of the arm's 23 refusals end on `r_table_not_licensed`, and
21 of the 23 hit it at some point in the turn.

At 0.923 coverage this is not currently expensive, which is why it is a question and not an
item in §1. Decoupling them (govern over the whole routed schema, retrieve the top 8) would
change what "governed" means and needs an ADR, not a patch.
