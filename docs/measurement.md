# Measurement

The baseline has not been run on the current model setup. Execution accuracy, coverage, run-to-run disagreement, cost per question, and the assumption figures are PLACEHOLDER (P16). What the run will fill in is [open work](open-work.md).

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

Execution accuracy is the share of graded rows whose result matched gold. A row with no gold is left out of that share. Coverage is the share of questions whose gold tables were all retrieved. The current figures are:

| | Total | Dev | Holdout |
|---|---|---|---|
| Execution accuracy | PLACEHOLDER (P16) | PLACEHOLDER (P16) | PLACEHOLDER (P16) |
| Coverage | PLACEHOLDER (P16) | PLACEHOLDER (P16) | PLACEHOLDER (P16) |
| Run-to-run disagreement | PLACEHOLDER (P16) | | |
| Cost per question | PLACEHOLDER (P16) | | |

Cost per question comes from the token totals on each row. P16 will turn those totals into the figure above. The tree has no price table, so the printer's lines stop at the rates.

`grade_turn` in [`eval/grade.py`](../src/governed_bi/eval/grade.py) compares an `answered` turn's rows with the gold rows. Whole numbers must match exactly. A fractional number may differ by one part in a billion, or by 1e-12 when the values are near zero. Text and numbers stay different types. `refused`, `clarification`, `capped`, `crashed` and `no_sql` score incorrect, and the grader executes only a statement the engine delivered. A missing gold on an answered turn is `correct: null`, which leaves the row out of the accuracy share.

## Assumption figures

Each answered row can carry `assumption_counts`, a count for `layer`, `convention`, `guess` and `found_in_query`. [`eval/report.py`](../src/governed_bi/eval/report.py) `assumption_figures` reads those counts. It reports the mean number of assumptions per answer for each basis. It reports execution accuracy for answers with at least one guess and for answers with none. It reports how many `found_in_query` entries the server added, which are the choices the model did not state, and how many answers have at least one. It reports the total of `ambiguous_guess_refusals` across every row, not only answers, because a refused guess often ends as a clarifying question. An answered row with no `assumption_counts` is counted as not recorded and left out of the means. Treating that gap as zero would claim the model stated nothing.

Those figures on the current setup are PLACEHOLDER (P16):

| Figure | Value |
|---|---|
| Assumptions per answer, by basis | PLACEHOLDER (P16) |
| Execution accuracy with a guess | PLACEHOLDER (P16) |
| Execution accuracy with no guess | PLACEHOLDER (P16) |
| `found_in_query` entries the model did not state | PLACEHOLDER (P16) |
| Ambiguous-guess refusals | PLACEHOLDER (P16) |

`python -m governed_bi.eval` is the small harness over a SQLite file, with configurations `oracle`, `stub` and `scripted`. Its JSON summary includes this same assumption block, plus execution accuracy, a refusal breakdown, and the single-run checks. The lake command is the one above.
