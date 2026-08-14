# Is there a better operating point? Risk-coverage on `v4`

**Answer: no useful one.** At 70% coverage the best out-of-fold precision on delivered answers is
**0.801** (95% CI 0.774–0.827). Precision 0.90 is reachable only at **6.9%** coverage. The
"deliver 70% of turns at 0.90" trade is not available from anything recorded on the row, and it is
not available from a five-run ensemble either. §3 says what the trade actually costs and §8 says
what this analysis cannot see; read both before quoting any of it.

All figures: arm `v4` (`runs/eval/proxy_v4_corpus30872d3.jsonl`, 1,351 turns, 57 schemas), corpus
`86ed1dbf…` = `../BIRD-corpus` @ `30872d3`, Claude-Opus-4.8/high agent, routing pinned to
`v3-fold`. Replication arm: `v3-fold` (`proxy_v3_fold_opus_high_corpus30872d3.jsonl`), same corpus.

## 1. The question, and what "coverage" means here

The reader cannot check SQL, so a wrong answer costs more than no answer. The objective is
precision on **delivered** answers with coverage as a constraint — not EX.

Coverage is a fraction of all 1,351 turns. `v4` delivers 1,278 of them (94.6%) at precision
**0.7144**; the other 73 are abstentions (49 `attempt_cap`, 20 `guardrail`, 4 clarification) and
all grade wrong by construction, so precision at 100% coverage is exactly EX = 0.6758. Below
94.6% the ranking problem is entirely inside the delivered set, which is where the analysis lives.

Three reference lines are on every table:

- **current operating point** — 94.6% coverage, 0.7144 precision.
- **random** — 0.6758 at 100%, and the base rate 0.7144 at every coverage at or below 94.6%.
  Random ranking still inherits the engine's existing abstention gate, so 0.7144 is the honest
  null, *not* 0.6758.
- **oracle** — perfect ranking. 1.0 until coverage 67.6% (= 913 correct answers exist), then
  913/k.

## 2. The risk-coverage table

Precision at each coverage, `v4`. Every non-oracle, non-random row is **out-of-fold**: 5-fold
stratified CV, models fitted on 4 folds and scored on the held-out one, no in-sample number
anywhere on this page.

| ranker | 100% | 90% | 80% | 70% | 60% | 50% |
|---|---:|---:|---:|---:|---:|---:|
| **oracle** | 0.6758 | 0.7508 | 0.8446 | 0.9651 | 1.0000 | 1.0000 |
| OOF gradient boosting, all deployable signals | 0.6758 | 0.7451 | 0.7752 | **0.8013** | 0.8113 | 0.8195 |
| OOF logistic regression, all deployable signals | 0.6758 | 0.7426 | 0.7780 | 0.7949 | 0.8138 | 0.8299 |
| **best single signal**: `agent_out_tok`, OOF-signed | 0.6758 | 0.7442 | 0.7761 | 0.7949 | 0.8237 | 0.8373 |
| **random** | 0.6758 | 0.7144 | 0.7144 | 0.7144 | 0.7144 | 0.7144 |

Bootstrap 95% CI on the gradient-boosting row (2,000 resamples of the delivered set under the
fixed OOF score vector): 90% → [0.719, 0.771]; 80% → [0.749, 0.799]; 70% → [0.774, 0.827];
60% → [0.786, 0.841]; 50% → [0.791, 0.846].

Two rows that are **not** deployable as stated, included as ceilings:

| ranker | 100% | 90% | 80% | 70% | 60% | 50% | why not deployable |
|---|---:|---:|---:|---:|---:|---:|---|
| OOF GBM + cross-arm agreement | 0.6758 | 0.7467 | 0.7909 | 0.8245 | 0.8360 | 0.8417 | needs 5 extra full inference passes |
| OOF GBM + gold-derived signals | 0.6758 | 0.7475 | 0.7956 | 0.8266 | 0.8496 | 0.8624 | needs the gold statement |

Coverage at which the best deployable OOF ranker reaches a precision target:

| target | coverage | precision achieved |
|---|---:|---:|
| 0.80 | 70.5% | 0.8006 |
| 0.85 | 10.4% | 0.8511 |
| 0.90 | **6.9%** | 0.9032 |
| 0.95 | never (max over k ≥ 50 is 0.9059) | — |

## 3. What the trade actually costs

At 70% coverage the ranker withholds 332 of the 1,278 delivered answers. **177 of those 332 were
wrong and 155 were right.** So the abstention decision is right 53% of the time against a 28.6%
base error rate — better than a coin, nowhere near a filter. The reader gains 9 points of
precision and loses a quarter of the answers, and one in five of what survives is still wrong.

For contrast, the oracle at 70% coverage withholds 332 answers of which all 332 are wrong.

## 4. Single signals, before any combination

AUC for predicting `correct` on the 1,278 delivered `v4` turns. AUC is stated in the raw
direction, so **below** 0.5 means *higher value → more likely wrong*.

| signal | AUC | reading |
|---|---:|---|
| `agent_out_tok` (agent-stage output tokens) | **0.279** | the whole result |
| `total_out_tok` | 0.290 | same signal, +facet stages |
| *(cross-arm)* `XARM_frac_agree` | 0.695 | agreement with the other 5 arms' result fingerprints |
| `agent_in_tok` / `total_in_tok` | 0.403 | prompt size ≈ licensed-context size |
| `agent_model_calls` | 0.410 | |
| `n_attempts` (ledger length) | 0.410 | |
| `q_chars` / `q_words` | 0.417 / 0.431 | question length |
| `n_select_exprs` | 0.453 | |
| `subquery_depth` | 0.462 | |
| `n_refusals`, `any_refusal` | 0.464 | |
| `sql_len` | 0.465 | |
| `n_licensed` | 0.471 | |
| `layer_TABLES` / `r_table_not_licensed` | 0.472 | the most-fired rule, and it is noise |
| everything else | 0.48–0.52 | see §5 |

**One signal is the entire result.** `agent_out_tok`, sign fixed inside each training fold, gives
OOF AUC **0.721** — *higher* than the 68-feature fitted gradient boosting (0.705 ± 0.008 over 10
CV seeds) and higher than logistic regression (0.701 ± 0.010). On the risk-coverage curve the
single signal ties the fitted model at 90/80/70% and **beats** it at 60% and 50%. Fitting a
combination buys nothing here; it costs inspectability and adds a way to be wrong.

The shape is monotone and blunt:

| decile of `agent_out_tok` (v4) | range | n | precision |
|---|---|---:|---:|
| 1 (fewest tokens) | 121–203 | 127 | 0.850 |
| 2 | 203–245 | 128 | 0.898 |
| 3 | 245–276 | 128 | 0.828 |
| 4 | 277–316 | 128 | 0.844 |
| 5 | 316–358 | 128 | 0.781 |
| 6 | 358–410 | 127 | 0.780 |
| 7 | 410–484 | 128 | 0.656 |
| 8 | 484–621 | 128 | 0.656 |
| 9 | 622–876 | 128 | 0.609 |
| 10 (most tokens) | 879–7,248 | 128 | **0.242** |

The top decile is where the wrongness is. The inspectable rule *"deliver iff
`agent_out_tok` ≤ t"*, with `t` fitted on the training folds only:

| target coverage | delivered | actual coverage | OOF precision |
|---|---:|---:|---:|
| 90% | 1,217 | 90.1% | 0.7445 |
| 80% | 1,079 | 79.9% | 0.7766 |
| 70% | 944 | 69.87% | 0.7945 |
| 60% | 809 | 59.9% | 0.8220 |
| 50% | 676 | 50.0% | 0.8343 |

That is a one-line threshold on a counter the engine already emits, and it recovers the whole
fitted model.

`agent_out_tok` correlates 0.67 with `n_attempts` and 0.71 with `agent_model_calls`, but those
carry far less (AUC 0.410 vs 0.279). Retry *count* is a pale version of retry *volume*. No derived
form beat the raw count: `out_per_call` 0.289, `out_over_qwords` 0.314, `agent_out_tok − sql_len/4`
0.276.

## 5. Which signals were useless

Flatly uninformative on `v4` (|AUC − 0.5| < 0.03), and this is the more interesting half:

- **The whole governance ledger.** Every rule and every layer: `r_ambiguous_fold` 0.496,
  `r_function_not_permitted` 0.496, `r_column_not_allowed` 0.497, `r_star_projection` 0.499,
  `r_table_not_licensed` 0.472. A statement that had to be repaired past the layer stack is
  *not* meaningfully more likely to be wrong than one that passed first time. The layers refuse
  on form, and form is not where the errors are.
- **Retrieval health.** `bodies_dropped` 0.499 (18 rows), `facet_degraded` constant `False` on
  all 1,351 rows, `n_licensed` 0.471, `n_licensed_in_primary` 0.479. `facet_channels` never
  degrades, so it carries zero bits.
- **Routing alignment.** `sql_schema_best_rank` / `sql_schema_worst_rank` 0.492 — whether the
  statement used the top-ranked schema or the tenth predicts nothing. `n_unlicensed_tables` is
  0 on 1,270 of the 1,275 parseable statements — the TABLES layer guarantees it, and the five
  exceptions are all `airline."Air Carriers"`, the one identifier whose slug is not its own name
  (the same case `table_coverage` documents), so it is a spelling artifact and not an escape.
  `n_sql_schemas` is 1 on all 1,275: every delivered statement is single-schema.
- **Result shape.** `pred_empty` (5 rows), `pred_single_null` (2), `pred_missing` (3) — all
  0 correct, so the direction is right, but 10 rows cannot move a curve. The engine essentially
  never returns an empty result.
- **SQL structure.** `n_joins` 0.507, `n_distinct_tables` 0.507, `n_table_refs` 0.494,
  `has_union` 0.495, `n_ctes` 0.497, `has_order_by` 0.498, `n_cast` 0.495, `has_like` 0.488,
  `has_having` 0.492, `n_aggregates` 0.489. Join count and table count — the features one would
  reach for first — are worth nothing. `has_limit` is constant because the engine appends an
  outer `LIMIT 200001` row guard to almost every statement; the model-authored variant
  (`model_limit`) is 0.502.
- **Question surface features.** `q_superlative` 0.473, `q_ratio_word` 0.466, `q_n_numbers`
  0.504, `q_n_quotes` 0.507.

Permutation importance on the fitted model agrees: the largest OOF AUC drop from permuting any
one column is 0.0084 (`sql_len`), and the bottom columns have *negative* drops (−0.0018 to
−0.0032, including `total_out_tok`, which is collinear with `agent_out_tok` and so free to be
permuted). The model has no concentrated dependence beyond the token counts.

**The critic verdict was the one missing signal, and it has since been measured.** No arm on disk
carried `reflect_enabled` when this analysis ran. One does now, and it scores **AUC 0.597** —
below `agent_out_tok`, and worse than `agent_out_tok` alone when the two are combined. §6 is that
arm in full.

## 6. The critic verdict: the one signal that reads meaning

**A different arm from the rest of this page.** `runs/eval/proxy_v4_reflect_corpus30872d3.jsonl`,
engine `2da223c`, corpus `30872d3`, ANALYST v4, routing pinned to the v3-fold artifact. v4 plus one
knob. Every other figure here is the `proxy_v4` arm.

The reflector reads the generated SQL against the question and writes a verdict. It had never run:
`reflect_enabled` was `False` on every row of every arm measured before this one. It was the last
untested source of information for selective prediction, every signal that does *not* read meaning
having already been measured and capped at OOF AUC 0.721 in §4.

It is worse than the token count.

### The four pre-registered criteria

Stated before the run, in the order they were to be read.

| | | |
|---|---|---|
| 1 | mechanism: verdicts present, distribution not degenerate | **pass** — 1 268 of 1 351, largest label 72.3% against an 80% degeneracy line |
| 2 | guardrail: EX must not move | **pass** — 0.6758 → 0.6699, net −8, p = 0.52 |
| 3 | primary: verdict AUC against the 0.721 bookkeeping baseline | **fail** — 0.597 |
| 4 | operating point: precision at 70% coverage against 0.801 | **fail** — 0.770 |

Criterion 2 is worth keeping: the node is declared to write a verdict and change no control flow,
and the arm confirms it. Whatever else this result says, it is a clean single-variable comparison.

### What the judge knows

Delivered answers only, n = 1 268.

| verdict | n | accuracy |
|---|---:|---:|
| `answered` | 917 | 0.763 |
| **`unsure`** | 77 | **0.766** |
| `wrong` | 274 | 0.533 |
| all | 1 268 | 0.713 |

**The turns it called `unsure` are as likely to be right as the ones it called correct.** That row
is the finding. The prompt makes `unsure` first-class and tells the judge that guessing is not a
useful answer; the judge takes the option and it carries nothing.

Separation between its two confident labels is **1.43×**. Self-consistency between two identical
runs, measured for free off the run1/run2 pair and costing a second inference, is **2.67×**.

Combining hurts. `agent_out_tok` alone scores AUC 0.719; verdict with the token count as a
tiebreak scores 0.691, and is worse at every coverage:

| ranker | 90% | 80% | 70% | 60% | 50% |
|---|---:|---:|---:|---:|---:|
| `agent_out_tok` | 0.738 | 0.781 | **0.802** | 0.814 | 0.834 |
| verdict + `agent_out_tok` | 0.740 | 0.759 | 0.770 | 0.805 | 0.822 |

### Why a second judge prompt is not the next move

The obvious follow-ups, a graded `confidence` field, `right` instead of the ambiguous `answered`, a
typed schema, all address *expression*. The `unsure` row says the problem is not expression. A judge
whose "I cannot tell" bucket has the same accuracy as its "this is right" bucket does not have a
resolution it is failing to express; it has no perception of its own uncertainty to express.
Changing the output format cannot supply one.

### Two things this arm settled in passing

**The parse-failure rate is zero.** `why_unmeasured` is empty on every row. Whether a hand-written
parser is robust enough, left open pending this arm's data, is answered: it is.

**The template-echo bug did not fire.** This arm ran on `2da223c`, which predates the fix at
`95e3b07`, so `VERDICT: answered | wrong | unsure` echoed back would have parsed as a complete,
favourable verdict. Zero rows carry the signature. The bug was real and reproducible and the model
never triggered it, so the arm is uncontaminated.

## 7. Replication

The pattern replicates. `v3-fold` (1,265 delivered = 93.6%, precision 0.7091, EX 0.6640):

| ranker | 100% | 90% | 80% | 70% | 60% | 50% |
|---|---:|---:|---:|---:|---:|---:|
| **oracle** | 0.6640 | 0.7377 | 0.8298 | 0.9482 | 1.0000 | 1.0000 |
| OOF GBM, deployable | 0.6640 | 0.7344 | 0.7687 | **0.8087** | 0.8298 | 0.8536 |
| single `agent_out_tok`, OOF-signed | 0.6640 | 0.7352 | 0.7743 | 0.8013 | 0.8261 | 0.8447 |
| **random** | 0.6640 | 0.7091 | 0.7091 | 0.7091 | 0.7091 | 0.7091 |

`v3-fold` OOF AUC: 0.738 ± 0.007 (GBM, deployable), 0.725 ± 0.007 (logit), 0.742 for
`agent_out_tok` alone. Same conclusion, same magnitudes, same winner. Its `agent_out_tok` top
decile is 0.213 precision.

`agent_out_tok` holds on **every arm this was measured on** (raw AUC, delivered turns only):

| arm | delivered | precision | `agent_out_tok` AUC |
|---|---:|---:|---:|
| `v4` | 1,278 | 0.7144 | 0.279 |
| `v3-fold` | 1,265 | 0.7091 | 0.259 |
| `v5` | 1,281 | 0.6698 | 0.281 |
| `v3-pinned` | 1,177 | 0.7018 | 0.287 |
| `run1` | 1,189 | 0.6577 | 0.269 |
| `run2` | 1,175 | 0.6553 | 0.298 |

`run1` and `run2` differ only by seed, so the 0.269/0.298 spread is the null band for this
statistic — the arm-to-arm differences above are inside it. The pattern is a property of the
architecture, not of a prompt.

`v5` is worth one extra line because it is the deliberately-worse arm: base precision 0.6698, and
its OOF GBM reaches 0.90 precision at 20.4% coverage rather than 6.9%. A worse engine is *easier*
to triage, which is the correct direction and a small check that the machinery is not fitting
noise. Coverage-70% precision on `v5` is 0.785 — still not 0.90.

Robustness: `GroupKFold` by `db_id` (so no schema appears in both train and test) changes the
`v4` OOF AUC from 0.705 to 0.701. The ranker is not memorising which schemas are hard.

## 8. The answer

**There is headroom, and it is about nine points of precision for a quarter of the coverage —
which is not the trade the framing hoped for.** Ranking the delivered turns takes `v4` from
(94.6%, 0.714) to (70%, 0.801), out of fold, 95% CI [0.774, 0.827]. To reach 0.90 precision the
engine would have to answer 6.9% of questions. Adding five extra inference passes and using
cross-run result agreement moves 70%-coverage precision from 0.801 to 0.825; giving the ranker the
gold statement — a cheat, quoted only as a ceiling — moves it to 0.827. So 0.90 at 70% is not
available from the ledger, the retrieval telemetry, the usage counters, the SQL structure, the
question text, the result shape, or any combination of them, and the gap to the oracle (0.965 at
70%) is almost entirely unexplained by anything the artifact records. Against that, one number
already on every row — `agent_out_tok` — carries the entire signal on its own, replicates on six
arms, and yields 0.7945 at 70% coverage from a single fitted threshold. If selective delivery is
worth doing at all it should be done with that threshold and nothing else; if the product needs
0.90, this direction cannot supply it and the effort belongs in generation, not in triage.

The caveat this analysis shipped with — that every signal here is **structural**, and that
nothing in it reads the generated SQL *against the question* — has since been tested. It was the
one experiment this page said was worth paying for, and it did not pay: an LLM critic reading the
SQL scores AUC **0.597**, below the token count, and the turns it calls `unsure` are as likely to
be right as the ones it calls correct (§6). The 0.695 AUC from
cross-*arm* agreement remains the only semantic signal that beats the structural ceiling, and it
costs a second full inference pass.

---

**Method.** 1,351 rows per arm, restricted to `outcome == "answered"` for ranking; abstentions
sit below every score and are only reached above 94.6% coverage. Target is `correct`. Features:
attempts ledger (count, per-rule, per-layer, path, first-attempt-passed), `licensed` size,
`context_evicted`, `facet_degraded`, `usage` token and `model_calls` aggregates,
`generated_sql` parsed with sqlglot (tables, joins, select width, subquery depth,
DISTINCT/GROUP BY/HAVING/UNION/CASE/CTE/LIMIT, predicate and aggregate counts, column count),
routing alignment against the ranked `schemas` list, question and evidence text from
`../BIRD-Data-Obfuscation/eval_dataset/test_final.jsonl`, and result shape from
`pred_fingerprint` (empty / single-null / missing, identified by hashing those results with
`grade.result_fingerprint`). Models: L2 logistic regression (C=0.1, median imputation,
standardised) and `HistGradientBoostingClassifier` (depth 3, 150 iters, lr 0.06, min leaf 40,
L2 1.0). 5-fold stratified CV; AUC reported as mean ± sd over 10 CV seeds; the risk-coverage
tables use the seed-0 OOF score vector. `GroupKFold` by `db_id` as a robustness check.

**Excluded as leakage:** `computed_correct`, `grade_detail`, `gold_fingerprint`, and the
comparison of `pred_fingerprint` to `gold_fingerprint`. **Quarantined as gold-derived** and
reported only as a ceiling: `quality_flags` (its `degenerate` flag is computed from the gold
statement, `datalake.attach_quality_flags`) and gold-table coverage via `datalake.gold_tables`.
`outcome` is used only to define the delivered set, never as a feature.

## 9. What this closes

Every source of information available to this engine has now been measured:

| | |
|---|---|
| 68 bookkeeping features, fitted | AUC 0.705 — worse than the best single one |
| `agent_out_tok` alone | **AUC 0.721**, the ceiling |
| the whole governance ledger | AUC 0.47–0.50, no signal |
| self-consistency, k=2 | +2–4pp for double the inference |
| an LLM judge reading the SQL | **AUC 0.597** (§6) |

Selective prediction on this engine tops out around **0.80 precision at 70% coverage**. For a reader
who cannot verify SQL, the user this was framed for, one wrong answer in five is not a product. The
direction is closed, and closing it cost one arm of a cheap utility model.
