# The reflector has less signal than the token count

**Arm**: `runs/eval/proxy_v4_reflect_corpus30872d3.jsonl`, engine `2da223c`, corpus `30872d3`,
ANALYST v4, routing pinned to the v3-fold artifact. v4 plus one knob.

The reflector reads the generated SQL against the question and writes a verdict. It had never
run: `reflect_enabled` was `False` on every row of every arm measured before this one. It was the
last untested source of information for selective prediction — every signal that does *not* read
meaning had already been measured and capped at OOF AUC 0.721.

It is worse than the token count.

## The four pre-registered criteria

Stated before the run, in the order they were to be read.

| | | |
|---|---|---|
| 1 | mechanism: verdicts present, distribution not degenerate | **pass** — 1 268 of 1 351, largest label 72.3% against an 80% degeneracy line |
| 2 | guardrail: EX must not move | **pass** — 0.6758 → 0.6699, net −8, p = 0.52 |
| 3 | primary: verdict AUC against the 0.721 bookkeeping baseline | **fail** — 0.597 |
| 4 | operating point: precision at 70% coverage against 0.801 | **fail** — 0.770 |

Criterion 2 is worth keeping: the node is declared to write a verdict and change no control flow,
and the arm confirms it. Whatever else this result says, it is a clean single-variable comparison.

## What the judge knows

Delivered answers only, n = 1 270.

| verdict | n | accuracy |
|---|---:|---:|
| `answered` | 917 | 0.763 |
| **`unsure`** | 77 | **0.766** |
| `wrong` | 274 | 0.533 |
| all | 1 270 | 0.713 |

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

## Two things this arm settled in passing

**The parse-failure rate is zero.** `why_unmeasured` is empty on every row. The question of
whether a hand-written parser is robust enough, left open pending this arm's data, is answered:
it is.

**The template-echo bug did not fire.** This arm ran on `2da223c`, which predates the fix at
`95e3b07`, so `VERDICT: answered | wrong | unsure` echoed back would have parsed as a complete,
favourable verdict. Zero rows carry the signature. The bug was real and reproducible and the model
never triggered it, so the arm is uncontaminated.

## Why a second judge prompt is not the next move

The obvious follow-ups — a graded `confidence` field, `right` instead of the ambiguous `answered`,
a typed schema — all address *expression*. The `unsure` row says the problem is not expression.
A judge whose "I cannot tell" bucket has the same accuracy as its "this is right" bucket does not
have a resolution it is failing to express; it has no perception of its own uncertainty to
express. Changing the output format cannot supply one.

## What it closes

Every source of information available to this engine has now been measured:

| | |
|---|---|
| 68 bookkeeping features, fitted | AUC 0.705 — worse than the best single one |
| `agent_out_tok` alone | **AUC 0.721**, the ceiling |
| the whole governance ledger | AUC 0.47–0.50, no signal |
| self-consistency, k=2 | +2–4pp for double the inference |
| an LLM judge reading the SQL | **AUC 0.597** |

Selective prediction on this engine tops out around **0.80 precision at 70% coverage**. For a
reader who cannot verify SQL — the user this was framed for — one wrong answer in five is not a
product. The direction is closed, and closing it cost one arm of a cheap utility model.
