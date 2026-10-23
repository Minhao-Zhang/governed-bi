# Failure modes

These are the ways a turn can end without a trustworthy answer, as the code behaves now. Each one says what the person sees on the chat page and what the turn record stores. Measured rates for the current model setup are [PLACEHOLDER (P16)](open-work.md). How a turn is graded is in [measurement](measurement.md).

## The input check stops the question

The served app runs five text rules and then a scope check on every question. A rule that fires, or a scope reply that does not clear, blocks the turn before retrieval. The person sees "I can't answer this from your data." and the sentence "This request cannot be processed. Rephrase the question in terms of the data you want to see." The record's outcome is `refused`, `terminal_reason` is `guard`, and `guard` holds the outcome and the rule id. The page does not name the rule. If the scope model errors, the check fails open: the turn continues, and `guard.outcome` is `error_failed_open`. The eval run enables none of these rules, so this ending does not occur there.

## Nothing in the layer matches, or the join cannot be built

`route` declines when no schema scores. `resolve` declines when the referenced assets exceed the limit. `connect` declines when the tables have no declared join path, or when the join would add more tables than the limit allows. If the abstention setting is on, the turn also declines when a retrieval channel failed, when no table was retrieved, when the rendered context is empty, or when the character budget dropped a whole retrieved table. That setting defaults to off, so those four reasons are recorded as `abstention.outcome` `disabled` and the turn continues. On a decline the person sees "I can't answer this from your data." and up to three nearby example questions. The answer text is empty, so the page does not add a longer reason. The record's outcome is `refused` and `terminal_reason` is `no_schema_matched`, `over_resolve_bounds`, `missing_join_path`, `over_connect_bounds`, or one of the four abstention reasons. A decline writes no assistant message. While the thread is still open the page appends the refusal from the answer channel. The turn row is stored either way.

## The query checks refuse the statement

`run_query` returns a refusal to the model, which can try another statement until five attempts are spent. If none pass, the outcome is `refused` and `refused_by` is `guardrail`. The person sees the same short refusal as a decline, without the rule text. The record's `execution.attempts` list the layer and the reason code for each try, including `r_table_not_retrieved` when the statement names a table this turn did not retrieve. A suspect column is refused on the eval run, where `hard_block_suspect` stays at its default, and is allowed on the served app, which turns that setting off. The served answer then carries a `found_in_query` assumption naming the column. An error inside the check counts as `guardrail_error` and the outcome is `crashed`. The status line says the turn failed, and names the stage when the record has one.

## The attempt limit is reached

Five `run_query` calls end the loop, including calls the checks refused. The narrator is told the last successful rows are an intermediate result, and the page shows that incomplete sentence. The record's outcome is `capped` and `refused_by` is `attempt_cap`. The ledger keeps every attempt. The eval marks the row incorrect and leaves the last statement ungraded.

## The turn crashes

An exception in a wrapped node, a crash path from `accept` when the conversation has no question, or a guardrail error sets outcome `crashed`. The record stores `error_type` as the exception class and `failed_stage` as the node. The page shows the answer text when there is any, and otherwise nothing for that turn. If the stream itself fails, the page toasts that no answer was produced and the question can be asked again on the same thread.

## The model ends in prose without answering

The model is told to finish through `answer` or `ask_user`. If it ends in plain text instead, the server reminds it once. If it ends in plain text again, the prose is not used as the answer: the outcome is `crashed`, `error_type` is `NoAnswerCall`, and `answer_tool_called` is false. The page shows nothing for that turn. A refused or capped turn is not reminded, because its outcome is already decided.

## The engine asks, and the person declines

`ask_user` pauses the turn. The person sees the question, one sentence of why, and any choices as buttons, and can type an answer. The conversation moves to "Waiting for you" in the sidebar. The turn row is written when the turn finishes, so the pause itself has no row yet. The interrupt is checkpoint state, so a restarted server can still be answered. If the person declines, the model is told not to guess and not to query, the outcome is `clarification`, and the page shows "The user declined this clarification." The record's clarification row stores the resolution. The eval scores both a pause and a decline incorrect, and it does not look at a statement the model might have drafted.

## The answer never runs a query

The model can call `answer` without a passing `run_query`. The outcome is `no_sql`. The page shows the prose as a paragraph. The assumption list and the result table are shown on an `answered` turn. The record's outcome is `no_sql`, `answer_tool_called` says whether an answer call was accepted, and the assumptions are on the record when a call was accepted. The eval scores the row incorrect.

## The answer is wrong, or it rests on a guess

A finished answer looks the same whether the rows match the question the person asked. The page shows the sentence, the first 20 rows, and the assumptions. The record's outcome is `answered`, with the SQL and the assumption list. Only an eval row's `correct` field says the result matched the gold result. A `layer` claim the server cannot back is rejected on the first `answer` call and stored as `guess` if the model sends it again, so the page marks it "This is a guess." A guess about a phrase two retrieved terms define differently is never accepted. The tool reply tells the model to ask, and `ambiguous_guess_refusals` counts those calls. A choice present in the SQL and absent from the model's list is appended with basis `found_in_query`, and the page says the query does it even though the person did not ask.

## A retrieval channel fails and the answer is still delivered

When the semantic channel cannot run, or a channel errors, and the abstention setting is off, the turn continues on whatever the other channels returned. The person can see an ordinary answer. The record's `channel_scoring` says `failed` or `not_configured` per channel, and `channel_degraded` is true when a channel ran on less than it declared. The status line can name the failed channel while the turn is running.
