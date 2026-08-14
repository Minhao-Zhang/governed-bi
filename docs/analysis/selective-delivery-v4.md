# The abstention headline as a curve: selective delivery on `v4`

**Answer: the engine's own operating point is the best one on the plane, and every
alternative costs answers.** `v4` sits at 94.60% coverage and 0.7144 selective accuracy.
No signal the artifact records improves on that *at that coverage* — they all read
exactly 0.7144 there, for a structural reason (§3) — and the best of them reaches 0.7956
at k = 944 (coverage 0.6987, the largest policy it can realise within 70%) only by
handing the reader **162 fewer right answers**: 751 against the engine's 913. That is
arithmetic, not an inference — the ranked policy delivers a strict subset of the engine's
turns and changes no turn's grade, so it cannot gain one (§4). Nothing reaches 0.90
accuracy at any coverage worth operating. The abstention figure the README quotes,
**0.7742**, reproduces exactly and is over **62 of 73** declines, not over 73.

This page reports what the instrument says. The instrument is
`src/governed_bi/measure/{signals,selective,abstention}.py` and
`tools/selective_curve.py`; nothing here required a model call, an embedding, or the
network. It re-analyses artifacts already on disk.

All figures: arm `v4` (`runs/eval/proxy_v4_corpus30872d3.jsonl`, 1 351 turns, 57
schemas), corpus `86ed1dbf…` = `../BIRD-corpus` @ `30872d3`, Claude-Opus-4.8/high agent,
routing pinned to `v3-fold`. Reproduce with:

```
uv run --frozen python tools/selective_curve.py runs/eval/proxy_v4_corpus30872d3.jsonl
```

## 1. What is new here, and what is not

[Risk coverage](risk-coverage-v4.md) already answered "is there a better operating
point" for `v4` and answered no. That analysis was a one-off script with `sklearn`; its
numbers were not reproducible from this repository and nothing stopped the next arm
being scored a different way. This page is the same question asked by code that ships,
and the first thing it buys is a **check on the old page**: every raw AUC in
`risk-coverage-v4.md` §4 is reproduced here to four decimal places by an independent
implementation.

| signal | §4 reported | recomputed here |
|---|---:|---:|
| `agent_out_tok` | 0.279 | 0.2791 |
| `total_out_tok` | 0.290 | 0.2902 |
| `agent_in_tok` | 0.403 | 0.4034 |
| `agent_model_calls` | 0.410 | 0.4096 |
| `n_attempts` | 0.410 | 0.4103 |
| `n_refusals` (here `n_failed_attempts`) | 0.464 | 0.4642 |
| `n_licensed` | 0.471 | 0.4705 |

Three things this page adds that the old one did not have: AURC over the whole curve
rather than five sampled coverages; the **resolution** of each signal, which turns out
to be the finding for the governance ledger (§5); and paired tests on the trade itself
rather than two accuracies printed next to each other (§4).

Three things it deliberately does **not** do. It fits no model, so there is no
gradient-boosting row and no cross-validation — every signal's direction is *declared*
in `measure/signals.py` with the mechanism behind it, which is why the curves need no
held-out fold to be honest. It does not use the question text, which lives in
`../BIRD-Data-Obfuscation` and is not an artifact field. And it reports no confidence
interval on AURC; §8 says what that costs.

## 2. The engine's operating point, and the price of its declines

| | |
|---|---:|
| turns | 1 351 |
| delivered | 1 278 (**coverage 0.9460**) |
| of those, correct | 913 (**selective accuracy 0.7144**) |
| declined | 73 (49 `attempt_cap`, 20 `guardrail`, 4 clarification) |
| useful answers (delivered **and** correct) | 913 |

Abstention precision, with the denominator it is actually over:

> declines that would have been wrong: **0.7742 (48/62)** over `'proxy_v4_corpus30872d3'` n=62
> (excluded crashed turns → excluded turns the grader could not judge → declined turns
> only → excluded declines the dataset cannot price); **11 of 73** decline(s) carry no
> gold fingerprint, so what the engine would have got there is unknowable, not zero.

That line is one string from `PricedAbstention.render()`, and the rate cannot be
obtained without its denominator. That claim was too strong when it was first written —
`.would_have_been_wrong.value` returned a bare `0.7741935483870968` and `.render(4)` a
bare `"0.7742"`, so only the outer object carried the 62 and nothing forced a caller
through it. The derived value is now a `WouldHaveBeenWrong`, which stores the numerator
and the denominator and no rate at all: no attribute returns the float, `render` prints
`0.7742 (48/62)`, and `share(n)` refuses an `n` that is not the priced count. A caller
holding the number is necessarily holding what it is over.
This is the shape [open work](../open-work.md) §4.1
asked for — the 62/73 split is not a rounding detail, and a figure over a subset the
dataset selected must travel with the subset.

`computed_correct` is never folded into `correct`. A decline whose last statement would
have matched gold is still an abstention, and the operating point above does not move.

## 3. The plane

Selective accuracy at each coverage, `v4`. Coverage is a share of all 1 351 turns;
declines sit below every delivered turn, because a ranking cannot un-withhold one.
Every cell is read at `k = floor(coverage × 1 351)`: 1 215, 1 080, 945, 810, 675. The
realised coverage is `k/1 351`, not the column header — 945 turns is 0.6995, not 0.7000 —
and these are *curve* values averaged through the tie group each k falls inside. The
largest **policy** a signal can realise within a coverage is generally a different k and
a different number; §4 uses that one, and `tools/selective_curve.py` now prints
`accuracy@k` in both places so they cannot be read as one figure.

| signal | rawAUC | AURC | cuts | 0.9 | 0.8 | 0.7 | 0.6 | 0.5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **oracle** (reads the grade) | 0.0000 | **0.0595** | 2 | 0.7514 | 0.8454 | 0.9661 | 1.0000 | 1.0000 |
| **no ranking** | 0.5000 | 0.2867 | 1 | 0.7144 | 0.7144 | 0.7144 | 0.7144 | 0.7144 |
| `agent_out_tok` | 0.2791 | **0.1816** | 622 | 0.7449 | 0.7765 | 0.7952 | 0.8235 | 0.8382 |
| `total_out_tok` | 0.2902 | 0.1892 | 692 | 0.7449 | 0.7769 | 0.7958 | 0.8156 | 0.8370 |
| `agent_in_tok` | 0.4034 | 0.2468 | 1 258 | 0.7358 | 0.7481 | 0.7630 | 0.7556 | 0.7630 |
| `agent_model_calls` | 0.4096 | 0.2527 | 8 | 0.7340 | 0.7569 | 0.7568 | 0.7567 | 0.7565 |
| `n_attempts` | 0.4103 | 0.2557 | 8 | 0.7347 | 0.7568 | 0.7566 | 0.7564 | 0.7560 |
| `n_failed_attempts` | 0.4642 | 0.2727 | 5 | 0.7294 | 0.7294 | 0.7294 | 0.7294 | 0.7294 |
| `n_licensed` | 0.4705 | 0.2714 | 44 | 0.7164 | 0.7176 | 0.7299 | 0.7327 | 0.7310 |
| `sql_len` | — | — | — | *unavailable: 3 delivered turns carry no statement* | | | | |
| `reflect_verdict` | — | — | — | *unavailable: the observer was off on this arm* | | | | |

`rawAUC` is in the artifact's own direction, not the declared one — below 0.5 means
higher value → more wrong. Every signal with a row here is declared `lower_first`, so every
one of them has its mechanism claim confirmed by landing below 0.5; the claim is stated in
`measure/signals.py` before the curve is drawn, and the raw AUC is printed unsigned so a
falsified claim would show as a number above 0.5 rather than as a merely weak curve.
(`reflect_verdict` is the one `higher_first` signal in the registry and has no row here:
the observer was off on this arm.)

**Every curve reads 0.7144 at coverage 0.9460.** That is not agreement, it is
arithmetic: a ranking reorders the turns the engine already agreed to answer, so at the
engine's own coverage every ranking delivers the same set. The consequence is the
headline of this page — *no signal on this arm improves the engine without withholding
more* — and `tools/selective_curve.py` prints it rather than leaving it to be inferred.

AURC is the mean selective risk over every coverage level, so lower is better. The scale
is set by the two reference rows: 0.0595 is what a perfect ranker would achieve on this
arm and 0.2867 is what ranking nothing achieves. `agent_out_tok` at 0.1816 closes
**46%** of that gap. That is a real amount of structure and it is still nowhere near
enough to make selective delivery a product (§4).

## 4. What the trade costs, paired

At 70% coverage the ranked policy is 8.1 points more accurate. It is also *worse* by the
only measure a reader experiences, which is how many right answers arrive:

| policy | coverage | selective accuracy | useful answers |
|---|---:|---:|---:|
| engine (governance + attempt cap) | 0.9460 (1 278/1 351) | 0.7144 | **913** |
| `agent_out_tok`, top 70% | 0.6987 (944/1 351) | **0.7956** | 751 |

The ranked policy delivers a **subset** of the engine's turns — a ranking reorders the
turns the engine already agreed to answer and cannot un-withhold one — and it changes no
turn's grade. So the useful answers it delivers are a subset of the engine's by
construction: **162 lost, 0 gained, 334 turns withheld**. This page previously reported
that as a McNemar (`delta −0.1199, p = 3.4e-49, MDE 0.0264`), which was arithmetically
correct and inferentially empty: with the sets nested, one discordant cell is 0 by
construction and the p-value is a function of the other alone, so *any* coverage below
the engine's yields "decisive, in the wrong direction" whatever the ranking is worth.
`measure.selective.compare_policies` now returns a `NestedPolicies` for this shape and
prints no test, so the presentation cannot recur. The substantive claim is unchanged and
is the one worth making: the trade costs 162 right answers.

Pricing abstentions at zero is a choice and it is the choice
`docs/measurement.md` already makes for the grade. A comparison on accuracy alone —
0.7956 against 0.7144, "+8.1 points" — is exactly the subtraction
[audit E1–E3](audit-2026-08-10.md) flagged in three other tools, and it would have
reported this trade as a win.

## 5. The governance ledger cannot express an operating point

`n_failed_attempts` — how many statements the layer stack refused on a turn — takes five
values over 1 278 delivered turns. Its **realisable coverages are 0.9082, 0.9349,
0.9408, 0.9430 and 0.9460**. There is no cut below 90.8%: the layer stack cannot be
asked to withhold a quarter of the workload, because it does not have opinions about
that many turns.

Read at its own best cut, against the token count at the same coverage:

| | coverage | selective accuracy |
|---|---:|---:|
| `agent_out_tok` | 0.9082 | 0.7408 |
| `n_failed_attempts` | 0.9082 | 0.7294 |

**delta −0.0104**, p = 0.0026, 20 discordant pairs, MDE 0.0093. It clears its own
detection floor by about a tenth of a percentage point, which is as close to the floor
as a result can be while still being one. Replicated (§7) it holds on `v3-fold` and `v5` and fails to
clear on `v3-pinned` and `v4+reflect`. The honest reading is that the count of bytes the
agent emitted ranks turns slightly better than the record of which governance rules
fired, and that the effect is at the edge of what 1 351 questions can see.

This is the same conclusion `risk-coverage-v4.md` §5 reached from AUC (`the whole
governance ledger … 0.47–0.50, no signal`), reached a second way and with a sharper
statement attached: the ledger is not merely uninformative, it is **too coarse to be a
policy** even where it is informative. The layers refuse on form, most turns have no
form problem — `n_failed_attempts` is 0 on **1 227 of the 1 278 delivered turns**
(96.0%) — and a signal that is flat on 96% of its population cannot rank it.

## 6. The reflector, on the only arm that has it

`reflect_verdict` is present on `proxy_v4_reflect_corpus30872d3.jsonl` and on no other
arm. It is also **absent on 2 of that arm's 1 270 delivered turns**, so the strict
instrument refuses a curve for it; the numbers below are over the stated sub-population
(1 349 turns, `delivered turns carrying reflect_verdict`) and are *not* comparable to
§3's table. `mcnemar` refuses to difference them against it, which is the point of
recording the restriction rather than silently dropping two rows.

| verdict | n | accuracy |
|---|---:|---:|
| `answered` | 917 | 0.7634 |
| `unsure` | 77 | **0.7662** |
| `wrong` | 274 | 0.5328 |

That reproduces [risk coverage](risk-coverage-v4.md) §6 exactly, including the row that
matters: the turns the judge called `unsure` are as likely to be right as the ones it
called correct. Raw AUC recomputed here is **0.5953** (§6 reports 0.597).

As a *policy* the judge offers three operating points and nothing between them —
realisable coverages 0.6798, 0.7368, 0.9400. At its best cut, against the token count on
the same sub-population:

| | coverage | selective accuracy | useful answers |
|---|---:|---:|---:|
| `reflect_verdict`, drop `wrong` and `unsure` | 0.6798 (917/1 349) | 0.7634 | 700 |
| `agent_out_tok`, same requested coverage | 0.6790 (916/1 349) | 0.8013 | 734 |

**delta +0.0252 for the token count, p = 0.0337, 242 discordant pairs, MDE 0.0323 — not
decisive.** This one *is* a real paired test: the two policies deliver overlapping but
non-nested sets, so either side could have gained.
The token count looks better and this arm cannot show that it is. That is a more careful
statement than §6's "worse than the token count", and it does not overturn it: §6's AUC
comparison is on a different statistic and the direction agrees. It does mean the *gap*
between a byte counter and an LLM critic reading the SQL is, on 1 351 questions, below
the floor.

Both are far behind the engine. Compared to just answering everything the engine was
willing to answer, the judge's best cut is nested inside the engine's set and loses
**205** useful answers (700 against 905), withholding 351 turns. As in §4 that is
arithmetic and no test is reported for it.

## 7. Replication across every arm on disk

`agent_out_tok` is the lowest-AURC available signal on **six** of the seven, and the trade
is negative on all seven. The exception is `v3-pinned`, where `total_out_tok` edges it —
0.2026 against 0.2047, a gap of 0.0021, which is well inside the null band the next
paragraph establishes. This page said "all seven" until 2026-08-12; the column below was
right and the sentence over it was not.

| arm | coverage | sel. accuracy | useful | AURC `agent_out_tok` | AURC no-ranking | curve acc @ k=945 | useful answers lost | priced declines | would-be-wrong |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| run1 | 0.8801 | 0.6577 | 782 | 0.2314 | 0.3473 | 0.7333 | 89 | 0/162 | *unmeasured* |
| run2 | 0.8697 | 0.6553 | 770 | 0.2446 | 0.3505 | 0.7265 | 84 | 0/176 | *unmeasured* |
| v3-pinned | 0.8712 | 0.7018 | 826 | 0.2047 | 0.3043 | 0.7735 | 95 | 149/174 | 0.8054 |
| v3-fold | 0.9363 | 0.7091 | 897 | 0.1766 | 0.2924 | 0.8011 | 140 | 69/86 | 0.8116 |
| **v4** | 0.9460 | 0.7144 | 913 | 0.1816 | 0.2867 | 0.7952 | 162 | 62/73 | **0.7742** |
| v5 | 0.9482 | 0.6698 | 858 | 0.2094 | 0.3311 | 0.7503 | 149 | 59/70 | 0.8475 |
| v4+reflect | 0.9400 | 0.7126 | 905 | 0.1850 | 0.2887 | 0.8026 | 148 | 63/81 | 0.7778 |

The two coverage columns are two conventions and the row mixes them on purpose, now
labelled: `curve acc @ k=945` is the curve averaged through the tie group at the requested
70%, while `useful answers lost` comes from the largest *realisable* `agent_out_tok`
policy within 70% — `agent_out_tok` on every row, including the one arm where it is not
the lowest-AURC signal, so the column is one signal across seven arms rather than the
per-arm winner. That k is 944 on run2 and v4 and 943 on v4+reflect. The loss column replaces a
paired delta: every one of these trades is nested, so the delta was `lost / 1 351` and the
p-value restated the nesting.

**run1 and run2 give the null band for AURC.** They differ only by seed, and their
`agent_out_tok` AURCs are 0.2314 and 0.2446 — a spread of **0.0132**. Every arm-to-arm
AURC difference in that column smaller than about 0.013 is inside it, which includes
`v4` against `v3-fold` (0.0050) and `v4` against `v4+reflect` (0.0034). There is no
paired test for an AURC difference in this instrument and this replicate pair is the
only guide there is; treat the column as a description of each arm, not as a ladder.

The abstention column is also worth reading as an instrument note: **run1 and run2 have
no priceable declines at all** (`computed_correct` is `None` on all 1 351 rows of both).
The abstention headline therefore cannot be replicated on the designated null pair, and
its seed-to-seed variance is unknown.

## 8. What this does not prove

**It is not out of fold, and for `agent_out_tok` on `v4` that matters.** No parameter is
fitted — the direction is declared and the curve has no free variable — but the
*declaration* was made after `risk-coverage-v4.md` measured this arm. On `v4` the token
count's advantage is therefore in-sample in the weaker sense that a human chose to look
at it. The five other arms in §7 are the out-of-sample version, and the direction holds
on all of them.

**There is no interval on AURC.** `risk-coverage-v4.md` bootstrapped its risk-coverage
row; this instrument does not, and the only uncertainty statement it can make about an
AURC is the run1/run2 spread. A bootstrap over the delivered set is the obvious
extension and it costs nothing but code.

**Coverage-at-accuracy is unstable at high targets, and is floored rather than fixed.**
The reported figure is the *largest* k whose accuracy clears the target, over k ≥ 50
(`MIN_OPERATING_POINT`). Below that floor the answer is a run of luck at the top of the
ranking: with the floor at 2, `agent_out_tok` "reaches" 0.95 on this arm. The floor is a
judgement, and it is the same one `risk-coverage-v4.md` §2 made in prose.

**Nothing here says the engine's declines are well chosen.** §2's 0.7742 says the
statements it withheld would mostly have been wrong. [Open work](../open-work.md) §4.1's
WrenAI contrast says the *questions* it declined are mostly answerable — an ungoverned
engine gets 56.2% of them right. Both are true: abstention tracks this engine's own
competence on the turn, mostly retrieval, and not question difficulty. This page adds
nothing to that argument and does not weaken it.

**The three signals with real resolution are all token counters, and they are the same
signal.** `agent_out_tok`, `total_out_tok` and `agent_in_tok` are not independent
evidence; §3 lists them separately because they are separately recorded, not because
three things agree.

## 9. Row fields found missing or unusable

Everything below is a fact about the artifacts, found by the instrument refusing rather
than by reading `project_turn`. It extends the list in
[declared-not-consumed](declared-not-consumed.md) and `runs/eval/README.md`.

| field | where | what |
|---|---|---|
| `attempts` | **absent on all 1 351 rows of run1 and run2** | The whole governance ledger. Both arms are the designated null replicate pair, so no ledger-derived signal can be measured on the only pair that could give it a null band. A reader that mapped absent to `0` would score `n_attempts` at exactly AUC 0.5000 on both — "the ledger carries no information" — when the truth is that it was never written |
| `model_calls` | absent from every `usage` entry of run1, run2 and v3-pinned | Present from `v3-fold` on. `agent_model_calls` is therefore unavailable on the three oldest arms |
| `computed_correct` | `None` on all 1 351 rows of run1 and run2 | Abstention pricing is impossible on those arms; see §7 |
| `generated_sql` | `None` on 3 delivered `v4` turns | The three `missing_prediction` turns ([open work](../open-work.md) §1.7). It makes `sql_len` unavailable for the whole arm under the strict rule, which is the correct outcome and also the reason `sql_len` has no row in §3 |
| `attempts` | `[]` on 2 delivered `v4` turns | Two of those same three. They answered without ever putting a statement through `check()`, so an empty ledger there is a real measured zero — which is exactly why absent and empty must not be collapsed |
| `reflect_verdict` | `None` on 83 of 1 351 reflect-arm rows | 81 are declines with no statement to judge; **2 are delivered turns**. §6 |
| `narrate` | a `usage` stage on 3 v3-pinned rows and no other arm | Not a defect, but the stage set is not constant across arms, so any per-stage aggregate needs to say which stages it summed |

Two fields were checked and are clean on every arm read here: `facet_degraded` (always
`False`, hence carrying zero bits — `risk-coverage-v4.md` §5 said the same) and
`context_evicted` (never set on `v4`).

## 10. Method

Population: all 1 351 rows, minus crashed turns and minus turns the grader could not
judge. On `v4` both filters remove nothing, and both are recorded on the `Population` so
they appear on every number's provenance line. A crash is excluded rather than counted
as an abstention: it is an instrument failure, not a decision, and folding the two would
credit the engine with declining on purpose when it fell over.

Ranking: delivered turns only, ordered by the signal's **declared** direction; declined
turns are appended below every score. **Ties are averaged, not walked in artifact
order** — every point inside a tie group is the expectation under uniform tie-breaking,
and a delivery *policy* refuses to split one at all, so the operating points a signal
offers are exactly its tie-group boundaries (the `cuts` column). This matters: with
artifact-order tie-breaking, a signal that separates nothing produces a wandering curve
and can outscore one that does.

AURC: mean selective risk over k = 1…1 351, reported beside the oracle and no-ranking
references because it has no scale on its own.

Comparisons: `measure.stats.mcnemar` on `useful_answer` (delivered **and** correct) over
`measure.population.Population`, always with the minimum detectable effect beside the
delta. No two rates are subtracted anywhere in the instrument. A comparison between two
policies where one delivers a subset of the other's turns is **not** reported as a
hypothesis test. `compare_policies` detects the nesting and returns the counts instead,
because the discordant cell one way is 0 by construction and the p-value carries no
information the subset relation did not already carry.

Leakage is controlled by an **allowlist**, not a denylist: `signals.READABLE_FIELDS` is
`usage`, `attempts`, `licensed`, `generated_sql`, `reflect_verdict`, and
`assert_no_signal_reads_the_grade` runs every signal against a key-recording row at
import, failing on any other field. The denylist it replaces had a hole with a name — the
probe moved seven grade-bearing fields but not `computed_fingerprint`, which is on every
real row and is what `computed_correct` is derived from, so a signal reading it changed
nothing between the two probes and passed. The moved-grade probe is kept as a second net
against a value leak through an allowlisted field, and the allowlist itself is checked
against the grade vocabulary so it cannot be widened to the answer. `oracle()` is the
single grade-reading curve and is named for it.

Tests: `tests/measure/test_selective_delivery.py`, mutation-verified — 20 mutations
applied to the three modules, **20 killed**, including tie-breaking by row order, absent
lists read as zero, `computed_correct` folded into `correct`, the priced denominator
widened to every decline, the declared direction ignored, the largest-k operating point
replaced by the first, `as_population` dropping its filter trail, and the leakage guard
made a no-op. A second round of 20 on 2026-08-12 covers the corrections on this page — the
nested-comparison refusal, the allowlist, the rate that carries its denominator, and the
`accuracy@k` cells — **20 killed**. The earlier 20 were **not** re-run after the
2026-08-12 changes to `abstention.py` and `selective.py`, so some may no longer apply in
their original form. The same file runs `tools/selective_curve.py` end to end against a
synthetic five-row artifact, because the driver's real inputs are gitignored and a
`tools/` script nothing executes is the shape [open work](../open-work.md) §3.10 calls
this repository's recurring defect.
