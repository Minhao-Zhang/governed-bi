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
| capped | 57 | was 133 before the fold fix |
| refused | 23 | **all** coverage failures, none with full coverage |
| clarification | 6 | all zero-licensed |

Two items that used to head this list are **done** and are deleted from it rather than struck
through: output-shape discipline (ANALYST v3, +3.3pp, over-projection 107 → 18) and the
`r_ambiguous_fold` scope (+5.3pp, capped 11.1% → 4.2%). Both are in
[failure modes](failure-modes.md) §9 as evidence.

### 1.2 The agent budgets its attempts blind

`run_query` is capped at `run_query_attempt_cap` (5). A governance-refused attempt **consumes
one**; only an infrastructure exception refunds. The agent is told the cap exists only once it
has already hit it, so it spends attempts on single-table probes (`LIMIT 3`, `LIMIT 5`) against
a budget it cannot see.

Returning "attempt 2 of 5" in the tool reply costs nothing. `serve/tools.py`.

### 1.3 Eight turns licensed nothing at all

Eight of 1 351 turns routed zero schemas and licensed zero tables. All eight asked a
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

### 1.5 Eighty-six questions never had their gold tables licensed

Table coverage is 0.923 on the v3-fold arm — **a licensing figure, not a delivered one**; see
§3.4 for what the char budget drops on top of it. The engine is near zero on the uncovered
remainder. Concentrated in `superstore`, `hockey` and `beer_factory`.

This is now the largest *winnable* bucket after the 262 semantic errors, and unlike those it is
corpus and retrieval work rather than generic text-to-SQL.

### 1.6 Sixty-nine capped turns had every gold table and still built no join

Full coverage, gold requires a join, final draft has none. The tables were in context. What is
missing is relationship grounding, not table budget — raising `table` budget above 8 does not
address it.

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

`../BIRD-corpus` is in git. It cannot be regenerated from anything committed: the generators
that produced it are out of tree, and there is no curator in `src/` for most asset types.
Versioned is not reproducible-from-source, and no document may describe it as such.

### 3.2a `r_ambiguous_fold`'s residual is bounded by the data, not by the code

The rule now resolves a qualified reference against its own table and refuses only what it
cannot decide. Where it *could* still resolve wrongly, the consequence is bounded by a property
of this database rather than by the resolver: **no table in the obfuscated lake has two columns
differing only by case** (70 schemas, 738 tables, 6 909 columns, checked 2026-08-09). So a
mis-resolution names a column that does not exist and Postgres errors, rather than reading a
decoy — the decoys here are differently-named `suspect` columns, not case variants.

Verified absent from 1 351 gold statements, 4 857 corpus few-shots and every model statement
observed so far: none reuses one alias for two tables.

**A corpus rebuild can unbound this.** No rebuild is planned as of 2026-08-09; if one happens,
re-check that property before trusting the resolver, or replace the tree-wide handle map with
the `traverse_scope` walk `binding.py` already uses, which removes the bound entirely.

### 3.4 The char budget is not the binding constraint I said it was

Measured for the first time on v3-fold, now that `context_evicted` survives the turn: the
80 000-char budget bit on **19 of 1 351 turns (1.4%)**, dropping bodies only and never a whole
table. An earlier offline reconstruction put it at 16 of 25 and was wrong — it built the
context from every licensed table's every column, ignoring the per-type budgets pass two
applies. Advice given on that basis ("do not cut the budget, it already binds") is withdrawn.

So there is headroom. Whether to use it is a question about whether the content earns its
place, not about whether it fits. The block is ~22 285 tokens and is re-sent on every model
call; `agent_core` makes 2.5 calls per turn, so the repeated prefix is **58.3%** of all input —
measured via `model_calls`, not bounded.

### 3.3 Held back on purpose

**Telling the agent its remaining attempt budget** (§1.2) is a cheap fix and is *not* applied,
because it changes behaviour and would become a second variable in the next arm. Apply it with
its own A/B, not alongside something else.

One `src/` item from the 2026-08-09 batch is still open; the other two (the false `boto3`
message and the missing `chat_model` knob) are fixed.

0. **Comparability: the engine changed after the three 2026-08-09 arms.** run1, run2 and the
   v3 arm all ran on `ba8cef2` or earlier. `r_ambiguous_fold` was narrowed afterwards, and it
   moves ~119 turns, so a new arm is **not** paired-comparable with those three on anything the
   fold touches. Run the next arm as its own control (v3 prompt plus the narrowed fold) and
   compare it to the v3 arm to isolate the fold's own effect.
1. **A hard cancel after the agent's grace period can leave an executed statement out of the
   ledger.** A turn killed between `execute` and the ledger write records no attempt for SQL
   the database actually ran. Rare, and it makes the ledger under-count rather than invent —
   but "the ledger is the record of what ran" is a property this repository leans on.


### 3.4 Cost per arm is not in the artifact

`usage` carries tokens. Price is the provider's number and `measure/price.py` is deleted, so an
arm's cost is not recoverable from the artifact alone.

---

## 4. Open questions

### 4.1 What the headline should be

The 2026-08-09 run makes this answerable for the first time. The engine commits to 1 189 of
1 351 turns at **0.658** accuracy and abstains on 162, of which **79.6% would have been wrong**
had it been forced to answer. Abstention is 3.2× better than chance at finding its own errors.

That is a claim about *calibration*, and it is orthogonal to EX — a comparison system reporting
a higher EX says nothing about which of its answers to distrust. Whether the project leads with
this or with EX decides what gets built next, and the two point at different work.

Making it a result rather than an observation needs a **contrast arm**: the same questions with
the governance layer off, to show that the turns the engine declined are turns an ungoverned
engine answers wrongly and confidently. That experiment does not exist yet.

### 4.2 Whether `licensed` should keep serving two masters

`licensed` is both the retrieval budget (`ASSET_REGISTER[table].budget = 8`) and the governance
allowlist that `check()` Layer 6 enforces. A retrieval miss therefore becomes a hard refusal
rather than a degraded answer — 18 of the run's 21 refusals are `r_table_not_licensed`.

At 0.925 coverage this is not currently expensive, which is why it is a question and not an
item in §1. Decoupling them (govern over the whole routed schema, retrieve the top 8) would
change what "governed" means and needs an ADR, not a patch.
