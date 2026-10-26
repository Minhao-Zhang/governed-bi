# Error analysis

This follows each of the 1,351 questions in the two baseline runs (P16, 2026-10-23, `gpt-6-luna` at `xhigh` for the agent and `medium` for the utility calls) through the pipeline, and says where each wrong answer was lost. [Sol, by the same measures](#sol-by-the-same-measures) does the same for the two `gpt-6.1-sol` runs and compares them. The run conditions are in [Measurement](measurement.md#the-baseline). The rows are `runs/eval/p16/run_a.jsonl` and `run_b.jsonl`, which are not committed.

Every rate below has its count and a 95% Wilson interval. Where the two runs differ, both are given, run A first.

## The pipeline, stage by stage

A question passes through these stages in order. Each stage has one metric, and each metric has its own denominator, so a loss is counted once at the stage that caused it.

| Stage | Metric | Denominator | Run A | Run B |
|---|---|---|---|---|
| 0. Gold | Gold reads at least one table | all questions | 1224/1351 = 0.906 | same |
| 1. Pause | Turn paused on a clarifying question | all questions | 180/1351 = 0.133 [0.116, 0.152] | 176/1351 = 0.130 [0.113, 0.149] |
| 2. Route | Gold schema in the top 3 | turns not paused | 1136/1171 = 0.970 [0.959, 0.978] | 1145/1175 = 0.974 [0.964, 0.982] |
| 3. Resolve | Refused for too many assets | turns not paused | 14/1171 = 0.012 | 10/1175 = 0.009 |
| 4. Retrieve | Every gold table retrieved | turns not paused, gold reads a table | 1032/1069 = 0.965 [0.953, 0.975] | 1042/1073 = 0.971 [0.959, 0.980] |
| 5. Finish | Ended in an answer | turns not paused | 1131/1171 = 0.966 | 1139/1175 = 0.969 |
| 6. Check | First agent statement passed the SQL checks | answers | 1112/1131 = 0.983 [0.974, 0.989] | 1125/1139 = 0.988 [0.979, 0.993] |
| 7. Correct | Result matched gold | answers | 747/1131 = 0.660 [0.632, 0.687] | 735/1139 = 0.645 [0.617, 0.673] |
| | Result matched gold | answers with every gold table retrieved | 710/1011 = 0.702 [0.673, 0.730] | 694/1017 = 0.682 [0.653, 0.710] |
| End to end | Execution accuracy | all questions | 747/1351 = 0.553 [0.526, 0.579] | 735/1351 = 0.544 [0.517, 0.570] |

Routing works: the gold schema ranked first for 85% of turns and in the top three for 97%. When it was in the top three, every gold table was retrieved 99% of the time. When it was not, the gold tables were never all retrieved, and none of those turns was answered correctly.

The checks rarely stop the model. Of the 1,929 statements run A sent to the checks, from the agent and from sampling, 35 were refused: 13 for a function not on the allowed list, 8 for a table this turn did not retrieve, 7 for an ambiguous term, 5 for a column not allowed, and 2 that would not parse.

The loss is at the two ends: the turns that paused, and the answers whose SQL was wrong.

## Where the wrong answers went

Each question is placed at the first stage it failed. A correct answer is counted as correct wherever it came from.

| First failure | Run A | Run B |
|---|---|---|
| Paused on a clarifying question | 180 | 176 |
| Refused for too many assets to resolve | 14 | 10 |
| A gold table was not retrieved | 27 | 23 |
| Ended without an answer: capped, refused by the checks, no SQL, or crashed | 23 | 25 |
| Answered, and the gold is a constant that reads no table | 59 | 59 |
| Answered with a paged statement (`LIMIT 20`, `OFFSET 20`) | 19 | 30 |
| Answered with wrong SQL | 282 | 293 |
| Total wrong | 604 | 616 |
| Correct | 747 | 735 |

Of run A's 604 wrong questions, 30% paused and 47% were answered with wrong SQL. Resolve refusals, retrieval misses and turns that ended without an answer together account for 11%.

### Wrong SQL, by cause

The grader's `failure_cause` compares the delivered statement with the gold statement and takes the first difference it finds, in this order. Run A, after removing the paged statements and the constant golds:

| Cause | Count | What differs |
|---|---|---|
| `table_set_differs` | 61 | a different set of tables, although every gold table was retrieved |
| `projection_extra` | 14 | more output columns than gold |
| `projection_missing` | 29 | fewer output columns |
| `aggregation_differs` | 29 | one side aggregates and the other does not |
| `filter_differs` | 50 | different columns in `WHERE` or `HAVING` |
| `unattributed` | 97 | the same tables, columns, aggregation and filter columns, and a different result |
| `unparseable` | 2 | the statement does not parse |

`unattributed` is the largest cause, so its 110 rows (before removing paged statements) were split further by the first difference between the two statements:

| Difference | Run A | Run B |
|---|---|---|
| `DISTINCT` on one side only | 36 | 31 |
| A different string literal | 27 | 37 |
| A different number literal | 19 | 25 |
| No difference this split can see | 14 | 15 |
| A different comparison, such as `BETWEEN` against `<` and `>` | 8 | 4 |
| A different aggregate function | 4 | 4 |
| A different `ORDER BY` or `LIMIT` | 2 | 1 |
| A different join type | 0 | 2 |

`DISTINCT` appears on one side only in 51 of the 110 rows, as the first difference or a later one. Most are a count of duplicated rows, where the gold counts them and the model does not, or the reverse. The string literals are often a gold that formats its output, such as `TO_CHAR(date, 'YYYY-MM-DD HH24:MI:SS') || '.0'` or `CASE ... THEN 'YES' ELSE 'NO'`, and sometimes a typo in the benchmark's hint, such as "AI Gore" for "Al Gore". Some of these are grading strictness rather than a wrong answer, and this split cannot tell which. The next section reads them.

### The wrong SQL, read by hand

All 282 wrong-SQL answers in run A were read against their question, the benchmark's hint and the gold statement, and given one main mistake from a fixed list. Three reviewers read a third each. The eval row keeps the SQL and the attempts but not the model's reasoning, so this says what the SQL got wrong, not why the model chose it.

| Main mistake | Count | Judged defensible | Right in run B |
|---|---|---|---|
| Right rows, different output: extra or missing columns, names joined into one column, Yes/No for a stored value | 62 | 61 | 5 |
| The gold looks wrong, or the question has two readings and the model took the other | 50 | 49 | 1 |
| `DISTINCT` added or left out, or `COUNT` against `COUNT(DISTINCT)` | 50 | 50 | 6 |
| Wrong table or join path | 27 | 14 | 4 |
| Ties and top-N: every tied row against the gold's `LIMIT 1` | 23 | 22 | 3 |
| A filter dropped, or one added that nothing asked for | 18 | 14 | 4 |
| Wrong aggregate or grouping level | 13 | 12 | 0 |
| Wrong column | 10 | 7 | 1 |
| A filter value that does not match the data | 10 | 2 | 1 |
| Ratio or arithmetic: the denominator, x100, rounding | 7 | 6 | 0 |
| Answered a different question | 5 | 0 | 2 |
| NULL handling, comparison boundaries, dates | 7 | 4 | 0 |

The reviewers judged 241 of the 282 (85%) answers a reasonable analyst could accept, 19 clearly wrong and 22 unclear. That is generous: a second check of 8 answers judged defensible agreed on 6, and found 2 arguable, an answer that unioned atom pairs into one column and one that added a "current employees only" filter nobody asked for. Read the 85% as a ceiling.

Three groups make up 185 of the 282, or 66%: the same rows shown differently, `DISTINCT` and ties, and a doubtful gold. These are disagreements with the grader more than mistakes, and few of them flip in a second run, because the model makes the same choice again. Among the doubtful golds, the gold filters on `vorname = 'Adam'` for a customer called Adrian, uses business 15 when the question asks about 35, or has an `OR` without parentheses so its date filter does not apply. In a dozen, the model followed the hint and the gold did not.

The mistakes that are the engine's own are about a third, and they repeat:

- It copies a value from the hint without checking the data. The hint says `'AI Gore'`, `'banned'`, `'photo'` or the year 1970. The data has `'Al Gore'`, `'Banned'`, `'photos'`, and the question says 1960. Five of the 19 clearly wrong answers are this. A statement that returns no rows for a filter value is the signal, and the agent already has a sample path to look at a column's values.
- It picks a look-alike schema. Routing gives the agent three schemas, and in five answers it queried the wrong one: orders from `car_retails` instead of `regional_sales`, countries from `world` instead of `mondial_geo`.
- It takes a shortcut the question does not support: `bai_fen_bi = 100` instead of the largest share, or a hard-coded list of atom ids.
- It adds a condition: `jahr > 2001`, `code > 'NPL'`, year bounds 1851 to 2012.
- It answers a nearby question: a count of movies instead of their average rating, or the share of over-50s among postal complainants instead of the share of postal complainants among over-50s.

If every answer judged defensible were counted right, run A would score about 0.73 instead of 0.553. That is not a score. It bounds how much of the gap is the grader and the gold rather than the engine, and it says a grader that accepts the right rows in a different layout would move execution accuracy more than any change to retrieval.

## Findings

### 1. A paused turn records nothing

When the agent calls `ask_user`, the turn pauses, and the eval writes its row from an empty record ([`eval/projection.py`](../src/governed_bi/eval/projection.py) `project_turn`). All 180 paused rows in run A have no tokens, no schema ranking, no retrieved tables and no attempts. Three things follow.

- The funnel cannot say why 13% of questions paused.
- Cost per question leaves out what those turns spent. If a paused turn cost as much as the average finished one, run A cost about $4.28, not $3.71, or $0.0032 a question, not $0.0027.
- The published coverage counts a paused turn as retrieving nothing. Coverage over all 1,224 questions whose gold reads a table is 0.843. Over the turns that did not pause it is 0.965. Retrieval misses a gold table on 3.5% of turns, not 15.7%.

The pauses are mostly the same questions: 111 paused in both runs, and 245 in at least one. A sample of the 111 includes "How many cities are in the Philippines?" and "List the names of business in AZ with a rating of 5.", which read as answerable. Whether the model is too cautious could not be checked from these runs. The rerun after the fix could, and it mostly is not: [After the fixes](#after-the-fixes) shows that about half of the paused turns had not retrieved the tables the question needs.

Fixed after these runs: the eval now reads a paused agent's own checkpoint and builds the row with the same projection `finish` uses (`with_paused_agent` in [`eval/harness.py`](../src/governed_bi/eval/harness.py), `paused_record` in [`serve/nodes/finish.py`](../src/governed_bi/serve/nodes/finish.py)). In the two full reruns, all 159 and 171 paused rows carried them.

### 2. The delivered statement can be a page of the answer

`run_query` returns at most 20 rows to the model ([`serve/fetch.py`](../src/governed_bi/serve/fetch.py)), with the full row count. When the count is larger, the model sometimes reads further with `LIMIT 20 OFFSET 20`. The last statement it ran becomes the delivered SQL, so the graded result is one page. Every such answer was wrong: 19 in run A and 30 in run B, or 1.4% and 2.2% of all questions. In Sol's run A, 41 wrong answers were paged.

Fixed after these runs. A statement that re-runs an earlier one with an `OFFSET` above zero is recorded as a page read ([`serve/paging.py`](../src/governed_bi/serve/paging.py)): it counts against the attempt limit, but it never replaces the delivered statement or the result table. A result longer than 20 rows now tells the model that the answer delivers every row, so it need not page. A model that starts with `LIMIT 20` and pages from there still delivers its first page, because no full statement ran.

### 3. 9% of golds cannot test the engine

127 golds are a constant `VALUES` row and read no table. The engine has to compute the same values from the database. It did so for 37 and 40 of the 96 and 99 answered, against 70% for golds that read tables. Accuracy on the 1,224 questions whose gold reads a table is 0.580 [0.552, 0.607] and 0.568 [0.540, 0.595].

### 4. Retries mark a wrong answer, as a guess does

An answer that took one agent statement was right 69% of the time in run A. With two statements it was 61%, and with three or more 18% (9 of 50). Run B was 68%, 56% and 21% (12 of 57). An answer with a guess was right 50% and 46% of the time. Both signals are on the turn record, and either could tell the reader to check the answer.

### 5. Most of the run-to-run disagreement is pausing

The runs disagreed on 152 questions. In 48, one run paused and the other answered. In 74, both answered and one was wrong. 665 questions were right in both runs (49%), and 534 were wrong in both (40%).

## Sol, by the same measures

The two Sol runs (2026-10-26, `gpt-6.1-sol` at `high` for the agent and `low` for the utility calls) were followed through the same stages. The conditions are in [Measurement](measurement.md#gpt-61-sol). Luna's runs come first in each cell, then Sol's.

| Stage | Luna A, B | Sol A, B |
|---|---|---|
| Paused on a clarifying question | 0.133, 0.130 | 0.113, 0.108 |
| Gold schema ranked first | 0.850, 0.860 | 0.833, 0.832 |
| Gold schema in the top 3 | 0.970, 0.974 | 0.970, 0.967 |
| Every gold table retrieved, turns not paused | 0.965, 0.971 | 0.952, 0.948 |
| Ended in an answer, turns not paused | 0.966, 0.969 | 0.967, 0.960 |
| First agent statement passed the checks | 0.983, 0.988 | 0.991, 0.988 |
| Answers that also ran sample queries | 0.237, 0.231 | 0.763, 0.744 |
| Correct, of answers | 0.660, 0.645 | 0.639, 0.629 |
| Correct, of answers with every gold table retrieved | 0.702, 0.682 | 0.682, 0.674 |
| Execution accuracy | 0.553, 0.544 | 0.548, 0.539 |
| Right in both runs, wrong in both, disagreed | 665, 534, 152 | 685, 567, 99 |

Sol pauses less, and its two runs agree more, so it is right in both runs more often and wrong in both more often. Its answers are a little less often right, and the two cancel. It looks at the data more: three quarters of its answers ran sample queries first, against a quarter of Luna's.

Retrieval is a point or two worse on Sol. The routing and retrieval code is the same; the utility model that rewrites the question for each channel is not. A gold table was missed while the gold schema was in the top three on 15 and 17 Sol turns, against 2 and 1 on Luna.

Where the wrong answers went:

| First failure | Luna A, B | Sol A, B |
|---|---|---|
| Paused on a clarifying question | 180, 176 | 153, 146 |
| Refused for too many assets to resolve | 14, 10 | 16, 17 |
| A gold table was not retrieved | 27, 23 | 43, 45 |
| Ended without an answer | 23, 25 | 5, 13 |
| Answered, and the gold is a constant | 59, 59 | 64, 65 |
| Answered with a paged statement | 19, 30 | 41, 39 |
| Answered with wrong SQL | 282, 293 | 288, 298 |
| Correct | 747, 735 | 741, 728 |

Sol delivers a paged statement twice as often, because it reads more rows before answering. Its signals for checking an answer also changed. It marked a guess on 9 and 4 answers, so the guess marker is gone. Retries separate more sharply: an answer that took two agent statements was right 39% and 34% of the time on Sol, against 61% and 56% on Luna.

### Sol's wrong SQL, read by hand

Sol run A's 288 wrong-SQL answers were read the same way, with one change: the reviewers were told to call an answer defensible only if it answers the question as asked, and to count an added condition or merged columns as unclear. So the defensible counts are stricter than Luna's and the two should not be compared.

| Main mistake | Sol A | Luna A |
|---|---|---|
| Right rows, different output | 68 | 62 |
| The gold looks wrong, or the question has two readings | 51 | 50 |
| `DISTINCT` or `COUNT(DISTINCT)` | 44 | 50 |
| Ties and top-N | 19 | 23 |
| A filter dropped or added | 19 | 18 |
| A filter value that does not match the data | 19 | 10 |
| Wrong table or join path | 18 | 27 |
| Wrong aggregate or grouping level | 16 | 13 |
| Wrong column | 10 | 10 |
| Ratio or arithmetic | 9 | 7 |
| Comparison boundaries, dates, NULLs | 12 | 7 |
| Answered a different question | 3 | 5 |

The mix is close to Luna's. The same four groups, the layout, the doubtful gold, `DISTINCT` and ties, are 182 of the 288 (63%), against 66% on Luna. The reviewers judged 189 defensible, 84 unclear and 15 clearly wrong under the stricter rule.

Two things are more common on Sol:

- It merges what the question asks for separately. 45 of its wrong answers join first and last name with `||` or collapse a list into one cell with `STRING_AGG`, against 25 of Luna's. The rows are right, and the grader sees one column where the gold has two.
- It follows the hint over the data and over the question. 19 answers have a filter value that does not match, against 10. The hint says `'CRIME SEXUAL ASSAULT'` and the data says `'CRIM SEXUAL ASSAULT'`. The hint says year 1990 and the question says after 1990. The hint says `'Low income'` and the question says lower middle income. All four answers one reviewer judged clearly wrong were a hint copied without checking.

Fewer of Sol's mistakes are a wrong table or join (18 against 27), but it still picked the wrong database three times, and twice it hard-coded the answer it expected, `'Madagascar'` and `'China'`, instead of querying for it. Once it delivered an exploratory statement, `SELECT DISTINCT JobTitle`, as the answer.

## After the fixes

The Luna baseline was run twice more with both fixes in ([Measurement](measurement.md#after-the-fixes) has the conditions). Execution accuracy was 0.569 and 0.568, against 0.553 and 0.544. Paired by question and averaged over the two runs, that is 2.0 points better, with a 95% interval from 0.6 to 3.5 points.

| | P16 A, B | Fixed A, B |
|---|---|---|
| Answers delivering a page | 20, 30 | 0, 0 |
| Capped at five statements | 15, 13 | 1, 2 |
| Paused on a clarifying question | 180, 176 | 159, 171 |
| Correct, of answers | 0.660, 0.645 | 0.658, 0.666 |
| Correct, of answers with every gold table retrieved | 0.702, 0.682 | 0.705, 0.711 |
| Accuracy on golds that read a table | 0.580, 0.568 | 0.599, 0.600 |
| Right in both runs, wrong in both, disagreed | 665, 534, 152 | 699, 513, 139 |

The clearest gains are the answers that used to be pages or caps. Paging used up the attempt limit, so removing it freed both. Of P16 run A's 20 paged answers, 13 are now right, and so are 7 of its 15 capped turns. 33 of its 180 paused questions were answered correctly this time.

### Why turns pause

Paused turns now record their routing and retrieval, so the question in finding 1 has an answer. The gold schema was in the top three for 82 of 159 paused turns in run A (52%) and 100 of 171 in run B (58%), against 98% for turns that did not pause. Among paused turns whose gold reads a table, a gold table was missing from what was retrieved in 78 of 139 and 80 of 146, more than half. 34 paused turns in each run had run a statement first.

So the model mostly asks when it cannot see the data the question needs, and the question is a reasonable response to a retrieval miss. The pauses are a retrieval problem more than a caution problem, and retrieval on these questions, not the agent's prompt, is the place to reduce them. 106 questions paused in both runs and 224 in at least one. In 50 of the 139 disagreements, one run paused and the other did not.

With paused turns counted, retrieval misses a gold table on about 9% of questions whose gold reads a table (coverage 0.910 and 0.908), and most of those misses are on the turns that paused.

## Metrics to track

These are the metrics a run should report, with the denominator each one needs. Each can be read from the rows today, except where noted.

| Metric | Definition | Use |
|---|---|---|
| Execution accuracy | correct / all questions | The headline. Report it with the interval and both runs. |
| Accuracy on tabled golds | correct / questions whose gold reads a table | The engine's accuracy without the constant golds. |
| Pause rate | paused / all questions | The largest single loss. |
| Routing recall at 3 | gold schema in the top 3 / turns not paused | Routing. |
| Coverage | every gold table retrieved / questions whose gold reads a table | Retrieval. Since the paused-turn fix the printed figure includes paused turns correctly; report it beside coverage on turns that did not pause. |
| Answer rate | answered / turns not paused | How often a turn that started ends in an answer. |
| Answer accuracy | correct / answered | What a person who gets an answer can expect. |
| Answer accuracy given coverage | correct / answered with every gold table retrieved | The model's share of the loss, with retrieval held fixed. |
| First-statement acceptance | first agent statement passed the checks / answers | How often the checks interrupt the model. |
| Paged deliveries | answers whose SQL has `OFFSET` above 0 or `LIMIT 20` / answered | Should be zero. |
| Retry and guess accuracy | accuracy for answers with three or more statements, and with a guess | Whether the warning signals still separate. |
| Stability | right in both runs, wrong in both, disagreed / all questions | Run-to-run noise. Any change smaller than the disagreement needs both runs. |
| Cost per question | priced tokens / all questions | Includes paused turns since the fix. |

The figures that can be reported with confidence are execution accuracy, accuracy on tabled golds, the pause rate, routing recall, answer accuracy and stability, and, from the fixed runs on, coverage and cost.

The two runs disagree on 152 questions, which puts the standard error of a paired difference in execution accuracy at about 0.9 points. A difference of under about 2 points between two configurations is within what a rerun of the same configuration produces. The intervals in the tables are for one run.

## How these were computed

The rows are read by scripts kept outside the repository. A turn is paused when its outcome is `clarification` and it recorded no model usage. A gold reads no table when [`gold_tables`](../src/governed_bi/eval/datalake.py) returns an empty set. A gold table counts as retrieved by `gold_table_ids`, the same match `table_coverage` uses. The `unattributed` split parses both statements with sqlglot and compares their string literals, number literals other than the engine's own row limit, `DISTINCT`, comparisons, aggregates, `ORDER BY` and `LIMIT`, and join types.
