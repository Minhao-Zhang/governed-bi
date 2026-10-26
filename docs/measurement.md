# Measurement

The baseline was run twice on 2026-10-23 (P16), on all 1,351 questions. Execution accuracy was 0.553 and 0.544, coverage was 0.843 and 0.851, the two runs disagreed on 11.3% of questions, and a question cost about $0.0028. [The baseline](#the-baseline) gives each figure by half and the conditions it was measured under. Two runs on `gpt-6.1-sol` on 2026-10-26 scored 0.548 and 0.539 at about $0.032 a question ([gpt-6.1-sol](#gpt-61-sol)). Where each wrong answer was lost is in [Error analysis](error-analysis.md).

## How a lake run works

[`tools/run_datalake_eval.py`](../tools/run_datalake_eval.py) serves each question through the eval graph, grades the answer against the gold result, and appends one JSON row as the question finishes. A resumed file is one run: the printed report reads the whole file.

```bash
uv run --frozen python tools/run_datalake_eval.py --embed --resume
```

The agent and utility models default to `gpt-6-luna`, and the reasoning effort defaults to `xhigh`, from [`registry/settings.py`](../src/governed_bi/registry/settings.py). `--embed` builds the semantic index with `text-embedding-3-large`. Without it the run is lexical only. The corpus default is the sibling checkout `../BIRD-corpus`, and the dataset default is `../BIRD-Data-Obfuscation/eval_dataset`, which supplies `test_final.jsonl`. The driver needs `OPENAI_API_KEY` and `GOVERNED_BI_PG_DSN`, and it exits before spending if either is missing. It never prints the key or the DSN.

The served app enables every input-check rule and leaves `hard_block_suspect` off. The driver enables no input-check rule, so the scope check stays off, and it leaves `hard_block_suspect` at its default, which refuses a suspect column. The abstention policy stays off unless you pass `--abstain`. Both of those choices move accuracy and coverage, so a run with either flag is a different configuration from one without it.

`--workers` defaults to 2. Each worker has its own graph and connection. `--max-retries` defaults to 8 and `--timeout` to 240 seconds. A rate-limit error that escapes those retries is a crashed row, and `--resume` drops crashed rows and runs those questions again while keeping every finished row. `--truncate` deletes the file at `--out` and prints how many rows it is discarding. `--against` names a second artifact so the report can compare the two. `--replay-routing` reuses a prior run's schema shortlist. A pinned score is for that comparison. The engine's own score is an unpinned run.

Rows land in `runs/eval/`, one JSON object per line. The name is derived from the model, the effort, the retrieval channel, and the flags that change the configuration.

## What the printed report says

After the last row, [`tools/datalake_report.py`](../tools/datalake_report.py) prints the row count, then execution accuracy and table coverage for the whole file. It prints the same two rates for the `dev` half and the `holdout` half in [`docs/data/db-split.csv`](data/db-split.csv), which assigns 28 databases to `dev` and 29 to `holdout`. A question whose database is not in that file is counted as unassigned and left out of both halves. Anything designed from failures reads `dev` only, and a decision to keep a change reads `holdout` only. With `--against`, it also prints how often the two runs disagree on `correct`, matched by question id.

Execution accuracy is the share of graded rows whose result matched gold. A row with no gold is left out of that share. Coverage is the share of questions whose gold tables were all retrieved.

## The baseline

Both runs used engine commit `64ffa4c` (branch `p16/caps`, squash-merged to `main`), BIRD-corpus `8ba312ed`, the `test_final` split of 1,351 questions, and `--embed`. The agent ran `gpt-6-luna` at `xhigh` with a 16,000-token output cap, and the utility calls ran it at `medium` with a 2,000-token cap. Run A used 16 workers and took about 65 minutes. Run B used 32 workers and took about 37 minutes. The artifacts are `runs/eval/p16/run_a.jsonl` and `run_b.jsonl`, which are not committed.

| | Run A total | Run A dev | Run A holdout | Run B total | Run B dev | Run B holdout |
|---|---|---|---|---|---|---|
| Execution accuracy | 747/1351 = 0.553 | 352/609 = 0.578 | 395/742 = 0.532 | 735/1351 = 0.544 | 352/609 = 0.578 | 383/742 = 0.516 |
| Coverage | 0.843 | 0.852 | 0.835 | 0.851 | 0.856 | 0.848 |

| Figure | Value |
|---|---|
| Run-to-run disagreement | 152/1351 = 0.113 |
| Cost per question | $0.0027 (A) and $0.0028 (B), or $3.71 and $3.75 per run |
| Clarifying questions, scored incorrect | 180 (A) and 176 (B), about 13% |
| Refused, capped and no SQL | 1.6%, 1.1% and 0.3% (A); 1.2%, 1.0% and 0.4% (B) |
| Median, 90th-percentile and longest question time | 41 s, 67 s and 218 s (A); 43 s, 69 s and 296 s (B) |

Run A's one crashed row was retried and answered. Run B's one crashed row, `train_4799`, failed with a Postgres `OperationalError`. Its retry was refused because the uncommitted `.claude/launch.json` had changed since the run, and the resume guard refuses to add rows to a run when the uncommitted diff has changed since it started. So the row stays crashed and scores incorrect, which moves run B's accuracy by at most 0.07 points.

Cost per question comes from the token totals on each row, priced per million tokens at $0.10 for input, $0.01 for cache reads, $0.125 for cache writes and $0.50 for output. A question averaged about 25,300 input tokens, of which about 15,000 were cache reads and 9,900 were cache writes, plus 2,660 output tokens over 5.8 model calls. The printer reports only the rates, so the cost was computed from the rows separately.

The token totals on the rows come to about 600,000 tokens a minute at 16 workers, which is 30% of the 2,000,000 tokens-per-minute limit. Neither run hit a rate-limit error.

### The corpus after the Ossie conversion

BIRD-corpus `f9392dca` stores the same definitions as `8ba312ed`, the baseline's corpus, as one Ossie model per database ([ADR 0022](adr/0022-ossie-is-the-storage-format.md)). Its corpus hash is `449d31603161410f3dc9e501a143f2a533f63c42f8ff5164570599fc38fac76a`. In between, `5bf45e4a` removed fields nothing read: 22 `confidence` values and the 4,857 few-shot bodies, which the engine now builds from the question and SQL. The offline snapshot of retrieval, join planning, context rendering and the SQL checks, over all 1,351 questions, 111 statements and 4 probes, is identical between `8ba312ed` read as YAML and `f9392dca` read as Ossie, except for the corpus hash. Runs are compared by their context hashes, not the corpus hash, so a run on `f9392dca` can be compared with the baseline.

## gpt-6.1-sol

The same 1,351 questions were run twice on 2026-10-26 with the agent on `gpt-6.1-sol` at `high` and the utility calls on `gpt-6.1-sol` at `low`. Output caps, `--embed` and every other setting were the baseline's. Both runs used engine commit `35840723` (branch `docs/sol-experiment`, a clean tree) and BIRD-corpus `f9392dca`, 10 workers, and took about 52 and 54 minutes. The artifacts are `runs/eval/sol/run_a.jsonl` and `run_b.jsonl`, which are not committed. Run B's one crashed row, `train_8349`, hit the agent's recursion limit; `--resume` ran it again and it was answered, wrongly.

The engine commit is not the baseline's. Between `64ffa4c` and `35840723` are the Ossie storage work, the views, and a dependency upgrade. The corpus reads the same ([above](#the-corpus-after-the-ossie-conversion)), but the comparison below is of two configurations, not of two models alone.

| | Luna A | Luna B | Sol A | Sol B |
|---|---|---|---|---|
| Execution accuracy | 0.553 | 0.544 | 0.548 (741) | 0.539 (728) |
| Dev / holdout | 0.578 / 0.532 | 0.578 / 0.516 | 0.565 / 0.535 | 0.553 / 0.527 |
| Coverage, as printed | 0.843 | 0.851 | 0.852 | 0.856 |
| Clarifying questions | 13.3% | 13.0% | 11.3% | 10.8% |
| Refused, capped, no SQL | 1.6%, 1.1%, 0.3% | 1.2%, 1.0%, 0.4% | 2.1%, 0.1%, 0.7% | 2.4%, 0.4%, 0.7% |
| Cost per question | $0.0027 | $0.0028 | $0.0321 | $0.0324 |
| Cost per run | $3.71 | $3.75 | $43.41 | $43.83 |
| Median and 90th-percentile time | 41 s, 67 s | 43 s, 69 s | 20 s, 32 s | 20 s, 34 s |
| Run-to-run disagreement | 152 (11.3%) | | 99 (7.3%) | |

Sol costs about 12 times as much per question and scores the same. Averaged over each model's two runs, Sol's execution accuracy is 0.4 points lower, with a 95% interval from 2.2 points lower to 1.2 points higher, paired by question. Sol is twice as fast and more repeatable: its runs disagree on 99 questions where Luna's disagree on 152. 576 questions were right in all four runs and 892 in at least one.

Sol is priced at $2.00 per million input tokens, $0.10 for cache reads, $2.50 for cache writes and $10.00 for output, from OpenAI's pricing page on 2026-10-25. A question averaged about 28,100 input tokens, of which 18,500 were cache reads and 9,100 cache writes, plus 670 output tokens over 6.3 model calls. Sol writes a quarter of the output Luna does, so most of its cost is the context: cache writes are about 70% of it. At 10 workers the runs used about 750,000 tokens a minute against Sol's 1,000,000 limit, and hit no rate-limit error.

Neither model's cost includes the turns that paused on a clarifying question, which record no tokens, and neither coverage figure excludes them. [Error analysis](error-analysis.md) gives the size of both gaps.

`grade_turn` in [`eval/grade.py`](../src/governed_bi/eval/grade.py) compares an `answered` turn's rows with the gold rows. Whole numbers must match exactly. A fractional number may differ by one part in a billion, or by 1e-12 when the values are near zero. Text and numbers stay different types. `refused`, `clarification`, `capped`, `crashed` and `no_sql` score incorrect, and the grader executes only a statement the engine delivered. A missing gold on an answered turn is `correct: null`, which leaves the row out of the accuracy share.

## Assumption figures

Each answered row can carry `assumption_counts`, a count for `layer`, `convention`, `guess` and `found_in_query`. [`eval/report.py`](../src/governed_bi/eval/report.py) `assumption_figures` reads those counts. It reports the mean number of assumptions per answer for each basis. It reports execution accuracy for answers with at least one guess and for answers with none. It reports how many `found_in_query` entries the server added, which are the choices the model did not state, and how many answers have at least one. It reports the total of `ambiguous_guess_refusals` across every row, not only answers, because a refused guess often ends as a clarifying question. An answered row with no `assumption_counts` is counted as not recorded and left out of the means. Treating that gap as zero would claim the model stated nothing.

The baseline's figures, run A then run B:

| Figure | Value |
|---|---|
| Assumptions per answer, by basis | `layer` 0.69 and 0.68, `convention` 2.93 and 2.95, `guess` 0.14 and 0.13, `found_in_query` 1.79 and 1.75, over 1,131 and 1,139 answers with none unrecorded |
| Execution accuracy with a guess | 67/135 = 0.496 and 60/130 = 0.462 |
| Execution accuracy with no guess | 680/996 = 0.683 and 675/1009 = 0.669 |
| `found_in_query` entries the model did not state | 2,019 in 968 answers (86%) and 1,991 in 971 answers (85%) |
| Ambiguous-guess refusals | 1 and 0 |

Sol's figures, run A then run B:

| Figure | Value |
|---|---|
| Assumptions per answer, by basis | `layer` 0.87 and 0.84, `convention` 3.02 and 3.06, `guess` 0.01 and 0.00, `found_in_query` 1.61 and 1.58, over 1,159 and 1,157 answers with none unrecorded |
| Execution accuracy with a guess | 3/9 = 0.333 and 2/4 = 0.500 |
| Execution accuracy with no guess | 738/1150 = 0.642 and 726/1153 = 0.630 |
| `found_in_query` entries the model did not state | 1,869 in 956 answers (82%) and 1,828 in 943 answers (82%) |
| Ambiguous-guess refusals | 0 and 0 |

Sol almost never marks a guess, so on Sol the guess marker no longer singles out the answers worth checking.

`python -m governed_bi.eval` is the small harness over a SQLite file, with configurations `oracle`, `stub` and `scripted`. Its JSON summary includes this same assumption block, plus execution accuracy, a refusal breakdown, and the single-run checks. The lake command is the one above.
