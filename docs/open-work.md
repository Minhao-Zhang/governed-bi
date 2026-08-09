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

Every figure in this section is from the 2026-08-09 full run (1 351 questions, corpus
`30872d3`, agent Claude-Opus-4.8/high). Method and per-case diagnosis: [failure
modes](failure-modes.md).

### 1.1 The agent projects columns the question did not ask for

The `analyst` prompt says nothing about output shape. 107 predictions returned more columns
than the gold; **51 of them become correct when the extra columns are dropped and the statement
re-executed**, and no correct answer anywhere in the run has a column-count mismatch.

Adding DISTINCT discipline to the same prompt reaches **79 of 292** fully-covered wrong
answers — EX 0.579 → 0.637 if output shape were perfect.

The fix is a `v3` variant of `ANALYST` in `register/prompts.py`. Two cautions the measurement
imposes on the wording:

- **Do not write a directional DISTINCT rule.** Spurious DISTINCT appears in 53 *correct*
  answers (lift 1.32); a rule that says "use less DISTINCT" would break them. The signal is
  *missing* DISTINCT (lift 10.0).
- Requires `--replay-routing` to be worth running. See §3.1.

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

### 1.5 Ninety-two questions never had their gold tables licensed

Table coverage is 0.925 over the 1 224 real golds. On the remainder the engine is at
EX 0.119 (partial coverage, n=67) and 0.000 (none, n=25). Concentrated in `superstore` (0.615
coverage), `hockey` (0.793) and `beer_factory` (0.778).

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

### 3.1 Nothing pins routing across arms yet — **done, unexercised**

`--replay-routing` exists (`eval/replay.py`, `tools/run_datalake_eval.py`) and is covered by
`tests/eval/test_routing_replay.py` and `tests/serve/test_routing_replay_node.py`. **It has
never been run against a real arm.** The first paired experiment is also its first exercise;
read the printed licensed-drift block before trusting the delta.

### 3.2 The corpus is versioned and still not rebuildable

`../BIRD-corpus` is in git. It cannot be regenerated from anything committed: the generators
that produced it are out of tree, and there is no curator in `src/` for most asset types.
Versioned is not reproducible-from-source, and no document may describe it as such.

### 3.3 Three `src/` fixes held back while an arm was running

Each was verified on 2026-08-09 and deliberately not applied: a paid run was executing against
`ba8cef2`, and the source tree is not covered by `corpus_content_hash` or `prompt_set_hash`, so
a mid-run edit is invisible in the artifact. None is urgent; all are small.

1. **`model/proxy_gateway.py:71,77` tells the user something false.** The lazy `boto3` import
   raises *"boto3 is not in this project's dependencies (pyproject.toml has no extras)"*, and
   the docstring above says the same. Both were true for five days and are not now — the
   `bedrock` extra brings `boto3`, so the fix the message should name is
   `uv sync --extra bedrock`. This was the fifth copy of one stale sentence; the other four
   were in `pyproject.toml`, `README.md` and `docs/usage.md` (twice).
2. **A hard cancel after the agent's grace period can leave an executed statement out of the
   ledger.** A turn killed between `execute` and the ledger write records no attempt for SQL
   the database actually ran. Rare, and it makes the ledger under-count rather than invent —
   but "the ledger is the record of what ran" is a property this repository leans on.
3. **`knobs_resolved` carries no `chat_model`.** `llm_utility_model` is recorded and the agent
   model is not; it survives only in the `arm` string, which is a filename convention. Same
   class as §1's treatment identities, one field further down.

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
