# Failure modes: how this engine gets BIRD questions wrong

**Arm**: `proxy_v4_corpus30872d3.jsonl`, **EX 0.676** (clean 0.6762)
**Engine**: `3c0079a`  **Corpus**: `../BIRD-corpus` @ `30872d3`  **Prompt**: ANALYST v4

run1 (0.579), run2 (0.570) and v3-pinned (0.611) ran on engine `ba8cef2` or earlier.
`r_ambiguous_fold` was narrowed after them, so on anything that rule touches they are **not**
paired-comparable with later arms. v3-fold (0.664) ran on `4f7430a`, and it is the artifact v4
and v5 pin their routing to with `--replay-routing`. Every section below names the arm it was
measured on. A number is only true of the engine it came from.

**Evidence**: per question, the row carries `licensed`, `gold_sql`, `generated_sql`,
`gold_fingerprint`, `attempts`, `computed_correct`, `context_evicted` and `routing_pinned`, plus
the two treatment identities `corpus_content_hash` and `prompt_set_hash`. `model_calls` is a key
inside each `usage` record, not a field on the row.

**Arm configuration, as `knobs_resolved` records it** — read off all 1,351 rows of
`runs/eval/proxy_v4_corpus30872d3.jsonl` on 2026-08-22, identical on every row: `chat_model`
Claude-Opus-4.8, `llm_utility_model` Claude-Sonnet-5, `embedding_model`
`proxy:text-embedding-3-large`, `llm_provider` `custom:007df842` (the proxy), `route_top_n` 10,
`run_query_attempt_cap` 5, `context_budget_chars` 80000, **`llm_max_retries` 4**.

**Inferred, not recorded** — stated separately because a header that mixes the two lets an
unrecorded setting be quoted as a measurement. `llm_reasoning_effort` and `serve_workers` are
`null` on every row. The "/high" therefore comes from the arm-name string alone
(`live_Claude-Opus-4.8_high_top10_embed_proxy_analystv4`), there is no field for the utility
surface's effort at all, and the worker count is recorded nowhere — an earlier version of this
header said "10 workers" and no artifact supports it. `llm_max_retries` 4 is a comparability
knob and today's driver default is 8, so a rerun at the default is a different arm.

> The corpus is the treatment identity of every measurement. Every number on this page holds only
> on corpus `30872d3`. The corpus is in git, and it cannot be regenerated from anything committed.

> **And so is the model.** Every number on this page was measured with Claude-Opus-4.8 as the
> agent and Claude-Sonnet-5 as the utility surface, through the `proxy` gateway. **That is not
> what this repo selects today**: `tools/run_datalake_eval.py` defaults to `--model
> gpt-5.6-luna` and `--provider openai`, and `model/provider.py` hard-codes no chat model at all
> — `default_embedding_model` is the only model default in it. Nothing on this page has been
> replicated on the model the driver now picks. The newest full 1,351-question arm on disk is
> `runs/eval/proxy_v4_reflect_corpus30872d3.jsonl` (2026-08-10); the newest artifact of any kind
> is a two-row aborted probe, `runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl`
> (2026-08-12). There is no `runs/index.jsonl` and no `stage_events.jsonl` anywhere in the tree,
> so the artifacts in `runs/eval/` are the whole record — there is no run ledger to check this
> page against. Verified 2026-08-22.

The to-do list is [open work](open-work.md). This page is the evidence it cites.

---

## Method

Three disciplines make these numbers stronger than counting features on a set of failures.

**A control group.** Every feature is computed on the correct answers too, and reported as lift.
Counting only failures makes the base-rate mistake: if 37% of wrong answers over-project and 35%
of right ones do as well, over-projection is not the disease.

**Offline replay of the governance layers.** Each refused statement goes back into `check()` with
the `licensed` set it had at the time, so the failing layer is read directly rather than guessed
from `refused_by`.

**Causal repair experiments.** Connect to the evaluation database, apply one controlled repair to
a wrong prediction — output shape only — then **re-execute and re-fingerprint**. That turns a
correlation into a countable causal upper bound.

**Method validity check** — ⚠ **hand-run, no producer in the tree**: 60 recorded predictions,
re-executed at random, reproduced the recorded `pred_fingerprint` 60 times out of 60. The database
state matched the state at run time, so every re-execution result below held *when it was taken*.

> ⚠ **What "hand-run, no producer in the tree" means, and why it is marked at every number that
> depends on it.** Everything in the third discipline above requires re-executing statements
> against the evaluation Postgres under a modification. **Nothing in `tools/` or `src/` does
> that.** `tools/regrade.py` re-executes a prediction and its gold *as they are* and re-grades;
> it applies no repair, and there is no `RECOVERED` symbol, no repair grid and no oracle pass
> anywhere in the tree (searched 2026-08-22). So these figures cannot be re-derived, cannot be
> checked, and cannot be regenerated after a database change — they are a record of a session at
> a keyboard, not of a producer. They are kept because they are real history and deleting them
> would lose the finding; they are marked because a reader must not spend them as if they were
> reproducible. **The populations they were computed over do reproduce** from the artifacts alone,
> and each is given below, so a maintainer rebuilding the producer knows exactly what to point it
> at. Rebuilding it means: a script that takes an artifact, applies a declared transform to
> `generated_sql`, executes it, and re-fingerprints against `gold_fingerprint` — the same shape as
> `tools/regrade.py` with one transform argument.

---

## 1. The ledger (v4)

```
1351 questions, 913 correct, EX = 0.676       clean (29 excluded) = 0.6762
438 failures
```

These six buckets are **mutually exclusive and exhaustive** over the 438. Each failure lands in
exactly one:

| Bucket | n | Nature |
|---|---:|---|
| **answered wrong, full coverage** | **257** | genuine semantics |
| answered, frozen-literal gold | 75 | dataset defect, unwinnable |
| capped | 49 | five attempts spent with no passing statement |
| answered, incomplete table coverage | 33 | retrieval |
| refused | 20 | none with full coverage |
| clarification | 4 | all zero-licensed |

Cut the same 438 by coverage instead, across outcomes: **73 failures had incomplete table
coverage** and **85 had a frozen-literal gold**. Those two totals overlap the capped, refused and
clarification rows above — of the 20 refusals, 19 had incomplete coverage and one had a gold that
reads no table; of the 49 capped turns, 26 were incompletely covered or had a tableless gold.

**Refusals and clarifications are still 100% retrieval failures.** Not one of them is a case of
"the data was visible and the engine refused anyway."

**Table coverage on real golds is 0.936** — 1,145 of the 1,224 questions with a real gold
statement had every gold table licensed. Retrieval is mostly working, and that decides how to read
every bucket below: only 79 questions were incompletely covered, and the engine answered just 6 of
them correctly.

Outcome against coverage, v4, all 1,351 rows including the correct ones:

| Outcome | Full | Partial | None | Tableless |
|---|---:|---:|---:|---:|
| answered | 1122 | 31 | 8 | 117 |
| capped | 23 | 12 | 7 | 7 |
| **clarification** | **0** | **0** | **2** | 2 |
| **refused** | **0** | **15** | **4** | 1 |

Not one refusal or clarification lands in the full-coverage column. Capped splits about evenly
between full coverage and incomplete.

### 1.1 The historical arm (run1, engine `d121c34`)

Stratified by table coverage, using `table_coverage()`, whose docstring calls this "the EX
ceiling":

| Table coverage | n | EX |
|---|---:|---:|
| full | 1132 | **0.647** |
| partial | 67 | **0.119** |
| none | 25 | **0.000** |
| tableless (frozen-literal gold) | 127 | 0.331 |

The outcome-by-coverage cross-tab is the cleanest structural signal in the whole artifact:

| Outcome | Full | Partial | None | Tableless |
|---|---:|---:|---:|---:|
| answered | 1024 | 40 | 11 | 114 |
| capped | 106 | 14 | 3 | 10 |
| **clarification** | **0** | **0** | **6** | 2 |
| **refused** | **2** | **13** | **5** | 1 |

Refusals and clarifications sit almost entirely in the under-covered cells; capped sits almost
entirely in the fully covered one. These three buckets are not three diseases.

---

## 2. Refusals: the governance layers reporting a retrieval failure

*(run1, 21 cases; v3-fold 23; v4 20. The conclusion does not change.)*

Replaying the 21 statements into `check()` with each one's own `licensed` set:

| Failing layer | n | Reason code |
|---|---:|---|
| **Layer 6 TABLES** | **18** | `r_table_not_licensed` |
| Layer 4 BINDING | 1 | `r_star_projection` (`SELECT *`) |
| replay passed | 2 | — |

The layers print their reason verbatim:

```
beer_factory.kunden resolves to beer_factory.kunden, which this turn does not license
works_cycles.EmailAddress resolves to works_cycles.emailaddress, which this turn does not license
```

In 19 of the 21, the gold table was not in `licensed` at all. **This is not governance
misfiring; it is governance correctly reporting a retrieval failure.** Relaxing this layer would
let the engine query tables it was not licensed for, and what you would then be measuring is no
longer a governed engine.

> The `works_cycles` message shows a case difference (`Product` → `product`). `normalise_table_key`
> normalises both sides symmetrically, so there is **no bug** — the message is displaying the
> normalised form.

The remaining two (train_667, train_5044) **pass** on replay: the recorded `generated_sql` is not
the same statement as the attempt that was refused. Unexplained, and worth its own look.

---

## 3. Clarifications: every one licensed nothing

*(run1, 8 cases; v3-fold 6; v4 4. Still all zero-licensed.)*

All eight clarification turns had an empty `licensed` and an empty `schemas`. The agent's context
held nothing, so "I need more information" is the only correct response.

This is not over-caution, and the fix is not to bias the agent toward answering under evaluation.
That would force an agent that can see no schema at all to invent SQL, trading an honest signal
for a guaranteed wrong answer.

What actually needs fixing is **why routing returned zero schemas on those 8 turns**. `licensed`
has a median of 26, and these 8 rows are the only ones below 5.

---

## 4. Answered, and wrong

*(run1, 292 cases; v3-fold 262; v4 257.)*

EX within this layer is 0.715.

### 4.1 Feature lift, against a control group of 732 correct answers

| Feature | Rate when wrong | Rate when right | **Lift** |
|---|---:|---:|---:|
| projection width differs (106 wider / 12 narrower) | 0.363 / 0.041 | **0.000** | **∞** |
| **missing DISTINCT** | 0.068 | 0.007 | **10.0×** |
| GROUP BY differs | 0.103 | 0.014 | 7.5× |
| extra join | 0.106 | 0.019 | 5.6× |
| ORDER BY differs | 0.082 | 0.029 | 2.9× |
| subquery structure differs | 0.110 | 0.041 | 2.7× |
| missing join | 0.099 | 0.041 | 2.4× |
| aggregate differs | 0.182 | 0.082 | 2.2× |
| **extra DISTINCT** | 0.096 | **0.072** | **1.32×** |
| LIMIT differs | 0.781 | 0.898 | 0.87× |
| shape identical | 0.271 | 0.795 | 0.34× |

**The two populations reproduce; most of the comparison rules were never written down.** Both
denominators recompute exactly off `runs/eval/proxy_full_opus_high_corpus30872d3.jsonl` (recounted
2026-08-22): 292 rows with `outcome == answered`, `correct == false` and every gold table
licensed, against 732 correct rows with full coverage. What no file in the tree states is *how*
each feature was compared, and the rates are sensitive to that at the third decimal. Recomputing
them with the obvious sqlglot definitions: the two DISTINCT rows land exactly (missing 20/292 =
0.068 against 5/732 = 0.007; extra 28/292 = 0.096 against **53**/732 = 0.072), both join rows land
exactly (extra 31/292 and 14/732; missing 29/292 and 30/732), `LIMIT differs` lands on 0.898 when
the top-level `LIMIT` is what is compared (657/732), and projection width lands on 106/12 under
the rule §11 states. **`GROUP BY differs`, `ORDER BY differs`, `aggregate differs` and `subquery
structure differs` do not land** — the nearest definitions give 0.106–0.140 against 0.017–0.022
for GROUP BY, 0.072–0.082 against 0.023–0.030 for ORDER BY, and 0.182–0.219 against 0.083–0.086
for aggregates. Their direction and rough size are the finding; the third decimal is not
quotable, because the rule that produced it is gone.

Two readings to hold on to:

- **An extra DISTINCT is close to harmless.** Lift 1.32, and **53 correct answers carry one too**.
  A directional "use DISTINCT less" rule would break them. The real signal is a **missing**
  DISTINCT.
- **Projection width has infinite lift** — no correct answer differs in width. Recounted
  2026-08-22: **0 of the 732** controls differ, against 106 wider and 12 narrower among the 292.
  (One further wrong row, `train_8505`, is excluded rather than counted wider, because the *gold*
  projects `SELECT *` and §11's rule skips star pairs. Counting it is where this table's earlier
  `107` came from.) The grader hashes the result set, so a width mismatch is a sufficient
  condition for failure. As a diagnosis it is a tautology; the real question is whether the query
  is right once the extra columns come off.
- The safety `LIMIT 200001` is inert: lift 0.87, and **650 of the 732 correct answers in the
  control carry it** — 0.888, counted 2026-08-22 as the literal `200001` appearing in
  `generated_sql`. One denominator, stated: an earlier version of this line said "695", which is
  the same count taken over **all 782** of the arm's correct rows rather than over the 732 this
  table's control is built from, and reading it against 732 makes the safety limit look more
  common than it is.

### 4.2 Causal repair: fix it, then run it again

⚠ **Hand-run, no producer in the tree** — see the Method note. Every count in this block came
from re-executing repaired statements against the evaluation Postgres in a session whose script
was never committed. They cannot be re-derived today.

The repair grid is: drop the safety LIMIT × DISTINCT on or off × keep any k projection columns,
where k is the gold's width. An oracle picks the best repair, so this is an **upper bound**.

```
population (answered, wrong, full coverage) : 292      <- reproduces exactly
  RECOVERED:projection      52                        <- hand-run
  RECOVERED:distinct        27      (15 by adding, 12 by removing)
  semantic                 213

recoverable by output shape alone : 79/292 = 27.1%
irreducible semantic errors       : 213/292 = 72.9%
```

**With output shape perfect, EX goes 0.579 → 0.637 (+5.85pp).** Projection alone: of the
over-projecting statements the hand-run counted as 107, **51 (47.7%) were correct once the extra
columns came off**.

What *is* checkable here is the frame around the hand-run, and it holds: the population of 292 and
the arithmetic reproduce off `runs/eval/proxy_full_opus_high_corpus30872d3.jsonl` (2026-08-22) —
782 of 1,351 correct is EX 0.5788, 79 more is 0.6373, and 79/1351 is 5.85pp, so 52 + 27 recovered
is exactly the claimed lift. The over-projecting population is **106** by today's recount (§4.1),
not 107, so the 51 and the 47.7% are one row out of step with the population they were measured
over. Both numbers stay as the hand-run wrote them rather than being rescaled to a denominator the
hand-run did not use.

The typical shape is "ask for one thing, return that thing plus the measure used to rank or filter
it":

```sql
-- train_5274  "Which brewery made the best-selling root beer in 2016?"
-- GOLD: SELECT brauerei_name … GROUP BY … ORDER BY COUNT(...) DESC LIMIT 1
-- PRED: SELECT brauerei_name, COUNT(wurzelbier_id) AS cnt …     ← one column too many, hash misses
```

---

## 5. Capped turns: join assembly is no longer the main cause

*(v3-fold, 57 cases.)*

```
table coverage full = 21,  partial = 19,  none = 9,  tableless = 8
gold needs a join   : 40 / 57
prediction has one  :  7 / 57
[full coverage + gold needs a join + prediction has none] = 10
```

**This bucket changed character.** Before `r_ambiguous_fold` was narrowed there were 150 capped
turns, 112 of them refused at Layer 1 (see §9). Afterwards there are 57. Of those 57, only 21 have
full table coverage and 28 have partial or no coverage — so **capped is now mostly a retrieval
problem, not "we gave it the tables and it could not join them"**. Only 10 turns land in the
tables-were-there-and-it-needed-a-join cell.

The join-assembly analysis below still holds for those 10, but it is no longer what explains the
bucket.

**It is not timeouts.** The rest of this section is measured on **run1**, where the bucket was
large enough to characterise: `authors` has 18 capped turns out of 21 there, and their final
statements all run in seconds:

```
train_3518: 1.0s  rows=5      train_3515: 0.2s  rows=1      train_3510: 0.0s  rows=3
```

train_3518's final statement is semantically equivalent to gold. Re-executing the final statement
of all 133 of run1's capped turns:

```
ALREADY_CORRECT   23
wrong            103
exec_error         7
```

⚠ **Hand-run, no producer in the tree** — the timings above and this three-way split alike. Both
required executing a capped turn's statement, which is exactly what the harness declines to do
(that is the finding), and no committed tool does it either; see the Method note. The **population
does** reproduce: `runs/eval/proxy_full_opus_high_corpus30872d3.jsonl` has 133 rows with `outcome
== capped`, and 23 + 103 + 7 accounts for all of them (recounted 2026-08-22).

> **23 capped turns ended on a statement that was the correct answer, and scored zero.**

The mechanism is in `eval/projection.py` (`eval/harness.py` until 2026-08-19): a prediction is
executed only when `outcome == "answered"`,
and `grade_turn` returns `correct=False` for `capped` without looking at the SQL. (Since
2026-08-18 `answered` excludes turns that ran no statement — those are `no_sql` — which changes
nothing here: they had no statement to execute either way, and `grade_turn` returns
`correct=False` for them too.)

**That is a defensible scoring policy.** An engine that ran out of attempts and never endorsed an
answer should not collect the score for a statement it would not deliver. But the policy has a
cost, and that cost used to be invisible. `computed_correct` records it now and the report prints
it. The scoring rule is unchanged.

The mechanism that produces it is in `serve/tools.py`: every `run_query` spends one attempt,
**including one the governance layers refuse**, and only an infrastructure exception refunds
(`AttemptBook.refund`, on the generic `except` — a `GovernanceUsageError` re-raises and a refusal
is charged).

> **The "budgets blind" half of this item is closed.** Every `run_query` reply has ended with
> `attempt N of M` since 2026-08-20 — `serve/tools.py::_attempt_budget`, applied on the capped
> reply, the error reply and the successful reply alike, unconditionally rather than behind an arm.
> The arms on this page were all measured before that, so their capped turns really were spent
> blind; a rerun would not be. What that does to the bucket is unmeasured, and it makes the earlier
> `v4` a different arm from today's `v4` under an identical `prompt_set_hash` — see
> [measurement](measurement.md#what-prompt_set_hash-does-not-cover).

Both examples are run1 turns; v3-fold and v4 answer all four of the questions named in this
section correctly:

```
train_5116 (address)  gold needs congress ⋈ zip_congress
           PRED: SELECT DISTINCT district_zip FROM address.zip_congress LIMIT 5
train_3510 (authors)  gold needs Journal ⋈ Paper
           PRED: SELECT Keyword FROM authors.Paper WHERE Year=2008 LIMIT 3
```

---

## 6. Irreducible semantic errors

*(run1, 213 cases.)*

⚠ **Hand-run, no producer in the tree** — see the Method note. Classifying two result sets by how
they relate means executing both, and nothing committed does that under this taxonomy; the labels
below exist in no module. The 213 is the residue of §4.2's hand-run grid, so it inherits that
block's status rather than reproducing on its own. The shares are internally consistent (183/213 =
85.9%, and the six rows account for 212 of the 213 — one case carries no label).

Execute both the prediction and the gold, then classify by how the result sets relate:

| Difference | n | Share | Meaning |
|---|---:|---:|---|
| **values disjoint** | **183** | **85.9%** | it computed a different quantity entirely |
| fewer rows (pred ⊂ gold) | 9 | 4.2% | over-filtered, or a join dropped rows |
| more rows (pred ⊃ gold) | 7 | 3.3% | under-filtered, or a missing DISTINCT |
| partial overlap | 6 | 2.8% | wrong join grain |
| same row count, different values | 4 | 1.9% | deduplication semantics |
| prediction empty | 3 | 1.4% | filter too tight, or a wrong literal |

**151 of the 183 disjoint cases are single-row results** (hand-run, as above). The dominant
semantic failure is computing one scalar and getting it wrong. It is not a list problem; it is an
arithmetic problem.

### Case studies

**A literal that never landed — train_5821 (airline)**

```sql
-- GOLD
SELECT COUNT(*) FROM airline.Airlines WHERE FL_DATE='2018/8/1' AND ORIGIN='JFK'
-- PRED
SELECT COUNT(*) FROM airline.Airlines T2 JOIN airline.Airports T1 ON T1.Code=T2.ORIGIN
WHERE T2.FL_DATE='2018/8/1' AND T1.Description LIKE 'New Yo%'
```

The agent did not know that `ORIGIN` stores the airport code directly, so it joined the airports
table and matched the description with a wildcard. Column values and enumerations are exactly what
the corpus is supposed to carry.

**Cross-schema crossings — 22 across the lake, and the 22 cannot be read back.** ⚠ **No
producer, and no artifact field.** `crossings` is declared on the measurement row, but it appears
on **none** of the seven `proxy_*` artifacts (grepped 2026-08-22, zero hits in each), so nothing
on disk holds this count. The definition that survives is [open work
§1.4](open-work.md)'s — failures whose prediction and gold statement share no schema at all —
and it does not land on 22 either: reconstructing it by parsing the schema prefixes out of
`generated_sql` and `gold_sql` over every row whose `correct` is not `true` gives **23 to 29
across the seven arms** (24 on v3-fold, 28 on v4, 25 on run1), and the gold schema was routed in
19 to 24 of them — **never in all of them**. So the sentence that used to stand here, that the
gold schema *was* routed on these turns, is not supported on any arm; open work already says 20 of
22. Read the claim as directional: **a double-digit number of answers on every arm are written
against a schema the gold never touches, mostly with the gold schema sitting in the shortlist** —
disambiguation inside the licensed set rather than a recall failure. The exact count wants
`crossings` actually written to an artifact before it is quoted again.

**A questionable gold — train_7810 (hockey), 340 rows against 339.** The gold carries a redundant
`AND NOT spieler_id IS NULL`. This is the category [Pervasive Annotation Errors Break Text-to-SQL
Benchmarks](https://arxiv.org/abs/2601.08778) (CIDR 2026) measured at a 52.8% annotation error rate
on BIRD.

**Dialect-level deduplication — train_8833 (food_inspection).** The gold uses Postgres-only
`DISTINCT ON (betrieb_id)`; the prediction uses an ordinary join. Same row count, different values.

---

## 7. Frozen-literal golds (127)

*(The 127 is a dataset property and is identical across arms. How many the engine happens to
match is not: 42 on v4, 39 on v5.)*

For 127 questions the gold is not a query. It is a hard-coded answer literal:

```sql
SELECT "v"."c0" FROM (VALUES ('captain eli''s')) AS "v"("c0")
```

The engine writes a real query, so it can only match by reproducing the frozen shape. On v4 it
matches 42 of them, essentially by luck. That the questions are unwinnable by design is a property
of the dataset, not of the engine.

`attach_quality_flags` now tags these automatically with its fourth flag, `degenerate`. The test is
the same rule `table_coverage` uses for `gold_reads_no_table` — `gold_tables()` returns an empty
set — so the two read one judgement rather than each carrying its own.

**Reporting discipline**: publish the **unfiltered EX**, which is what public BIRD reports, and
which is **0.676** on this arm. A figure with the degenerate questions removed is supplementary
only, and must be labelled as a one-sided exclusion. Shrinking the gap by changing the denominator
is a change of definition, not a result.

---

## 8. Abstention quality, and the ceiling a contrast arm puts on it

*(Includes v4.)*

| Arm | Committed | Abstention rate | Abstained, if forced | Abstention precision |
|---|---:|---:|---:|---:|
| run1 | 0.658 (n=1189) | 12.0% (162) | 0.204 † | 0.796 † |
| run2 | 0.655 (n=1175) | 13.0% (176) | 0.168 † | 0.832 † |
| v3-pinned | 0.702 (n=1177) | 12.9% (174) | 0.195 (29/149) | 0.805 |
| v3-fold | 0.709 (n=1265) | 6.4% (86) | 0.188 (13/69) | 0.812 |
| **v4** | **0.714** (n=1278) | **5.4%** (73) | **0.226** (14/62) | **0.774** |
| v5 | 0.670 (n=1281) | 5.2% (70) | 0.153 (9/59) | 0.847 |

† run1 and run2 have no `computed_correct` field on their rows. Those two columns come from a
separate oracle pricing pass and cannot be recomputed from the artifact. v3-pinned and the three
arms after it can.

The abstention rate fell from 13.0% to 5.4% while precision stayed between 0.77 and 0.85. What
went away were abstentions caused by a bug, not judgement. On v4, **every** refusal and
clarification lands on a question where retrieval failed.

**The "if forced" column covers only the priceable subset.** Of v4's 73 abstentions only 62 can be
priced; for the other 11 the dataset ships no gold fingerprint, so whether the engine would have
been right is **unknown, not zero**. The precision of 0.774 is a figure over those 62, and it has
to be quoted with that denominator.

Delivered accuracy is **3.16×** the accuracy of the priceable withheld set (0.714 / 0.226). If
abstention were random, the withheld set should sit near 0.676.

This behaviour is mechanical rather than narrative: 19 of the 20 refusals terminate on
`r_table_not_licensed` — Layer 6 of `check()` reporting a retrieval failure — and all 4
clarifications had an empty licensed set. **The governance layers are acting as an "I don't know"
detector.**

### 8.1 What the contrast arm does to that claim

The argument above needs a governance-off arm to be a conclusion, and one is already on disk.
WrenAI runs the same 1,351 questions on the same database and **abstains once**:
`refusal_rate: 0.0007`, a single turn of 1,351, against v4's 73. For this contrast that is
governance-off in practice.

Two gradings of that run are on disk, and they disagree by one answer in each cell. **Quote the
newer one**: `runs/eval/wrenai/preds_full_usage_graded.jsonl` (2026-08-07) grades the
token-logged re-run, which is the run whose usage totals `runs/eval/wrenai/RESULTS.md` reports, and
it is the grading `runs/eval/wrenai/preds_full_usage_graded_summary.json` publishes. The older
`preds_full_graded.jsonl` (2026-08-03) graded the earlier non-logged pass; it is the same generator
at temperature 0 and it reproduces to within a question, which is the determinism check, not a
second opinion.

```
                                        08-07 (quote this)      08-03 (superseded)
the 73 questions v4 declines            42 correct = 57.5%      41 correct = 56.2%
the 1278 v4 commits to                 873 correct = 68.3%     875 correct = 68.5%
ratio                                            1.19x                   1.22x
refusals over all 1351                     1 (0.0007)              0 (0.0)
exec_error                                        14                      13
```

Recomputed 2026-08-22 by joining both graded artifacts to the 73 abstained and 1,278 committed
`question_id`s of `runs/eval/proxy_v4_corpus30872d3.jsonl`; the two files carry set-identical ids,
so nothing is lost in the join.

**If abstention tracked question difficulty, an ungoverned engine should collapse on the declined
set. It loses eleven points.** So those questions are mostly answerable. What abstention tracks is
not difficulty but **whether this engine had enough context on this turn** — and that is almost
entirely retrieval: 19 of 20 refusals end on `r_table_not_licensed`, and 4 of 4 clarifications
licensed nothing.

That is still a real and useful property. A missed table surfaces here as "I cannot answer" rather
than as a confident answer written against the wrong table. But it is not the stronger claim —
that the engine knows which questions are hard — and prose slides toward the stronger claim
easily. The honest version is narrow: **the engine abstains when its own context is insufficient,
and on the priceable subset it is right to abstain 77.4% of the time.**

WrenAI differs from this engine on every dimension at once, so it can bound the claim but cannot
attribute it. The arm that would attribute it relaxes Layer 6 from "the 8 licensed tables" to "the
whole routed schema", holding the model and the corpus fixed and moving only the allowlist. See
[open work §4.1 / §4.2](open-work.md).

---

## 9. `r_ambiguous_fold`: an 8pp defect that one field made visible

*(run1 → v3-fold.)*

The `attempts[].reason_code` field only reached the artifact on 2026-08-09. The largest single item
showed up immediately:

```
v3-pinned:  PARSE/r_ambiguous_fold   568 attempts / 119 turns (8.8%)
              112 of those 119 turns ended capped; the 119 scored EX 0.025
              (the unaffected 1,232 scored 0.668)
              they consumed 24% of all input tokens
```

**Mechanism.** `spellings_for` flattened the names of a turn's licensed tables — around 26 of them
across about 8 schemas — into **one namespace**. Any two names differing only in case (`Name` and
`name`) then caused **every** reference to either to be refused, including a fully qualified
`T1."Name"`. The rule's intent, that a case fold might land on a decoy, was right. Its scope was
one level too wide.

**The fix.** Resolve a qualified reference against its own table; register an aliased table only
under its alias, because Postgres hides the table name behind one; and if a handle points at two
tables anywhere in the tree, discard it and fall back to the old behaviour.

> **Those two resolver defects were fixed 2026-08-12** (open-work recorded the repair; both
> had zero hits on this arm — all 1,342 statements and all 656 tables were scanned — so the
> numbers below are unaffected). A later CTE-scope hole of the same family is written up in
> `git-history:docs/analysis/binding-scope-and-statement-timeout-2026-08-19.md`, deleted with the
> rest of `docs/analysis/` by `2396ca2`; read it with
> `git show 2396ca2^:docs/analysis/binding-scope-and-statement-timeout-2026-08-19.md`. [Open work
> §3.2a](open-work.md) points at the same write-up the same way.

**Result** (v3-pinned → v3-fold, same prompt):

```
r_ambiguous_fold   568 attempts / 119 turns  →  109 / 35
attempt_cap        150  →  57            capped rate 11.1% → 4.2%
EX                 0.611 → 0.664         net +71 (130 against 59, 189 discordant)
                                         exact McNemar p = 2.6e-07
input tokens       87.2M → 74.7M         −14.4%
```

**The lesson is not "there was a bug."** It is that the bug sat there for an unknown length of time
because the field that would have shown it stopped at `stamp`. Adding that field paid better than
any prompt intervention tried in the same period.

---

## 10. The context budget

*(First measured on v3-fold.)*

`context_budget_chars = 80000`, **re-sent on every model call**.

What `context_evicted` shows now that it survives the turn:

```
eviction fired  19/1351 = 1.4%      and only bodies_dropped — never a whole table
```

On that evidence, the advice that the budget already binds and must not be cut is **withdrawn**. It
came from an offline reconstruction, not from this measurement.

**The budget is not the constraint.** The question is whether the content earns its place, not
whether it fits.

The companion figures, from the first arm to carry `model_calls`:

```
agent_core: 3,308 calls over 1,345 turns = 2.46 per turn, 22,285 input tokens per call
second and later calls within a turn = 1,963 = 59.3% of all calls
agent_core is 98.7% of the arm's input tokens (73.7M of 74.7M)
```

Call counts and token totals are read straight out of `usage`. But `usage` writes **one** aggregated
`agent_core` record per turn, so how many tokens each repeated call consumed is not recoverable
from the artifact. Any token share attributed to repeat calls specifically is an average, not a
measurement.

The proxy is OpenAI-compatible and caches long prefixes automatically without reporting cache
counts, so these quantities have to come from `model_calls` rather than from the provider's cache
numbers.

---

## 11. The projection rule: how much of this EX is shape matching

*(v4 → v5.)*

Every section above asks why the engine got an answer wrong. This one asks something else: **how
much of the right answers came not from finding the right data, but from arranging the result
columns the way the reference answer arranges them.**

**Mechanism.** ANALYST v4 contains a paragraph about projection — the result table is the answer,
its columns are part of being right, select only the columns the question asks for. That paragraph
is an outlier in the prompt. v4's other two rules — qualify identifiers, `SELECT *` is refused —
state constraints `check()` actually enforces. The projection rule states something **this engine
does not check at all**:

- nothing in `RULES` in `govern/layers.py` constrains select-list width;
- `result_fingerprint` in `eval/grade.py` hashes row tuples only, and its docstring says
  explicitly that `columns` are **not** hashed.

An extra column therefore changes nothing inside the engine. It changes the grader's digest. A rule
that aims at exactly one dimension of the comparator and affects nothing else is measuring the
comparator.

**Method.** v5 is v4 byte-for-byte with **only that paragraph deleted**. Same engine, same corpus
`30872d3`, same `--replay-routing` pin on `proxy_v3_fold_opus_high_corpus30872d3.jsonl`. Both arms
flag 1,345 of 1,351 rows `routing_pinned`, but that field recorded the driver's *intent* when
these rows were written; `replay.pin_realised` is the producer that reads what the turn actually
ran on, and it counts **1,342 of 1,351 realised on v4 and 1,340 on v5**. Residual mean Jaccard
over the shortlists that moved is 0.7049 on v4 and 0.7029 on v5. It is the cleanest pair in the
set: one variable. Paired McNemar, not two EX numbers subtracted.

**Result**:

```
EX                        0.676 → 0.635    net −55  −4.07pp  p = 4.9e-06 (n=1351, 143 discordant)
wider than gold            43 → 125
narrower than gold         40 → 34
abstention precision      0.774 → 0.847
abstention rate            5.4% → 5.2%
```

Width is counted by parsing both statements with sqlglot and comparing the number of select
expressions, skipping any pair where either side fails to parse or projects a star: 1,343
comparable pairs on v4 and 1,341 on v5.

**What it means.** About **4 points** of this engine's EX come from aligning the output column set
to the reference answer rather than from retrieving the right data. This is not specific to this
engine — every system reporting EX on this benchmark carries some of it. Almost nobody measures it,
because measuring it costs a whole second arm.

**Two over-readings to avoid.**

- It does not follow that 0.635 is the honest number. WrenAI's 0.6773 — 915 of 1,351, the
  `EX_lenient_over_all` of `runs/eval/wrenai/preds_full_usage_graded_summary.json`, 2026-08-07 —
  contains a shape component too, and that one cannot be measured from outside. Subtracting on
  this side only makes the comparison less accurate, not more. (The `0.678` this line used to
  carry is the same field in the superseded 08-03 grading; §8.1 has the pair.)
- It does not follow that v5 reasons worse. v5's **abstention precision went up** (0.774 → 0.847),
  and neither the abstention rate nor the refusal mix collapsed. The regression is concentrated in
  shape: over-projection 43 → 125, and almost the whole delta sits in that cell. What got worse is
  compliance with a shape, not judgement.

The right reading is that this is **a measurement of a confounder in the metric**. It also explains
why the projection-width lift in §4.1 is infinite: that was never a diagnosis, only a tautology the
grader's definition creates.

**What it decides about the prompt.** v4 stays the default. In a project graded on EX, deleting a
paragraph you know is worth 4 points is trading someone else's scoreboard for your own tidiness.
But the paragraph belongs in the **scoreboard-fitting** column, not in the column of engineering
that made the engine better. v5 stays in the registry so this account can be recomputed at any
time.

---

## Appendix: external references

- [Pervasive Annotation Errors Break Text-to-SQL Benchmarks and Leaderboards](https://arxiv.org/abs/2601.08778)
  — a 52.8% annotation error rate on BIRD.
- [The Death of Schema Linking?](https://arxiv.org/html/2408.07702) — aggressive pruning loses
  22.6% of required elements. This arm's 0.936 coverage says the current configuration is not in
  that trap.
- [CHASE-SQL](https://arxiv.org/html/2410.01943v1) /
  [DPC](https://aclanthology.org/2026.acl-long.313/) — candidate generation plus selection, BIRD
  73.01%. Expensive, and orthogonal to this project's governance argument.
