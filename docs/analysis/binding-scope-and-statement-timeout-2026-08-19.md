# A false ambiguity, and the 25-minute turn it caused — 2026-08-19

Tree: `031b955`. Locked versions: `sqlglot 30.16.0`, `langgraph 1.2.11`.

Two defects, one causal chain. The first refused statements Postgres considers unambiguous; the
second let the rewrite that refusal forced run until the agent's wall clock killed the turn. Both
were found from one served thread, not from the benchmark: thread
`01a01b7f-61f4-7761-b5f3-764f1780fab0` in `runs/conversations.sqlite`, question *"Which PM
activities have the highest repeat failure rate?"*, which returned no answer after 25 minutes.

## What the thread recorded

| time (CDT) | event |
|---|---|
| 14:32:20 | run starts |
| 14:32:21–26 | guard → rewrite → facets → route → resolve → assemble → abstain |
| 14:32:46 | attempt 1 refused `r_ambiguous_reference` (BINDING) |
| 14:32:57 | attempt 2 refused `r_ambiguous_reference` (BINDING) |
| 14:33:13 | attempt 3 passes every layer, reaches the database, and does not return |
| 14:57:26 | `agent_node_timeout_s` + hang grace fire; turn stamped `crashed` |

Final state: `path_kind: "crashed"`, `failure: {stage: "agent_core", error_type: "TimeoutError"}`,
`answer_text: null`. Both refusals took 11 seconds each; the other 24 minutes were one query.

## Defect 1 — a CTE is visible everywhere, and that was read as bindable everywhere

`binding.py::_classify_sources` built each scope's source map from `scope.sources`. On sqlglot
30.16.0 that mapping merges every **visible** CTE into every scope, not just the ones the scope
selects from:

```
WITH pm AS (...), cm AS (...), j AS (... FROM pm LEFT JOIN cm ...) SELECT aft FROM j
```

| scope | `scope.sources` | `scope.selected_sources` |
|---|---|---|
| outer `SELECT … FROM j` | `pm`, `cm`, `j` | `j` |

The outer select's `FROM` has exactly one derived source. One derived source is explicitly
allowed — `_bind_columns` maps it to `opaque` — but `pm` and `cm` were counted too, so the
reference fell through to the ambiguity refusal instead. **The exactly-one-derived branch was
unreachable for any statement with more than one CTE.**

Minimal case, needing nothing multi-source at all:

```sql
WITH pm AS (SELECT a FROM s.t1), cm AS (SELECT c FROM s.t2) SELECT pm.a, cm.c FROM pm, cm
```

Inside `cm`'s own scope, bare `c` binds to `s.t2` — its only source — and refused "1 base and 1
derived".

**Measured on the store, not inferred.** Across every `attempts_by_call` row in
`runs/conversations.sqlite`: 16 `run_query` attempts, 10 passed, **6 refused
`r_ambiguous_reference`**, and all 6 carry the "0 base and N derived" shape where the scope's own
`FROM` had exactly one derived source. Not one was ambiguous to Postgres. The turns that did
succeed are the ones where the model gave up on bare names and qualified every reference.

### The fix, and why it is not a relaxation

`selected_sources` is sqlglot's name for "what this scope's `FROM`/`JOIN` actually brought in".
Switching to it restores the intended rule. Three properties were checked by execution:

| statement | verdict |
|---|---|
| sibling CTEs + one bare reference | binds |
| `SELECT id FROM s.t1 JOIN s.t2 ON TRUE` | `r_ambiguous_reference` |
| `WITH pm AS (…) SELECT pm.a FROM s.t2` | `r_unbound_reference` |
| correlated `EXISTS` referencing the outer scope | binds |

The third row is a **new** refusal: a qualified reference to a CTE this scope does not select
from was previously bound. That is what Postgres does with it.

`pipeline.py::_handles_in_scope` moved in lockstep. Its whole justification is that it and
`bind()` must not disagree about what a handle names — `r_ambiguous_fold` exists to catch that
disagreement — so leaving it on `scope.sources` would have made a sibling CTE's name a handle in
scopes whose `FROM` never introduced it.

### A fail-open found on the way

`selected_sources` raises `OptimizeError` where `scope.sources` quietly papered over the problem:
two sources in one scope answering to one name. Measured on sqlglot 30.16.0,
`FROM s.orders AS a, s.audit AS a` yields `{"a": s.orders, "audit": s.audit}` — the first keeps
the alias, and the second is filed under its *table name*, which the alias is supposed to hide.
So `a.id` bound to whichever source came first, and `audit.id` bound through a name the statement
does not offer. Postgres rejects the statement outright. It now refuses
`r_ambiguous_reference`.

## Defect 2 — `run_query` had no bound of its own

`_hang_grace` already named this, as a known limit rather than a finding:

> A `run_query` has no bound of its own (there is no `statement_timeout` on the connection), so
> this is a ceiling on being wedged, not a promise that nothing was in flight when it fired.

`PostgresConnector._connect` set `default_transaction_read_only` and `synchronize_seqscans` and
nothing else. The two bounds that did exist are both in the wrong place to help:

* `agent_node_timeout_s` (1200 s) is checked **between** stream frames, deliberately — cancelling
  mid-`run_query` would leave a statement that reached the database off the projected ledger,
  which is an audit break. A blocking query emits no frames, so it cannot fire.
* the hang grace (`llm_timeout_s`, 300 s) bounds one frame's wait. 1200 + 300 = 1500 s, which is
  exactly the 14:32:26 → 14:57:26 the thread recorded.

The cost layer does not cover this either: it ships `UNSET` (ADR 0006 OQ2), and `shape_estimate`
counts tables, joins and set operations, so attempt 3 — three correlated `EXISTS` subqueries over
a 1.2M-row table, in `SELECT`, inside `ROUND`, and again in `HAVING` — scores low however it is
weighted.

**And the shape was the refusal's doing.** Attempts 1 and 2 were the cheap formulation: CTEs
joined once. Blocked twice, the model rewrote to the only shape that has no derived sources to be
ambiguous about.

### The fix

`statement_timeout_ms` (`Role.comparability`, default 120 000) is applied as a session `SET` when
the connection opens. Sizing is paired with the two knobs it sits beside, the way
`agent_node_timeout_s` is paired with `llm_timeout_s`: `run_query_attempt_cap × 120 s = 600 s`,
half of `agent_node_timeout_s`, so five statements can each time out and still leave the other
half of the budget for model calls. `0` disables it, which is what Postgres already means by that
value.

**`57014` is classified `QueryError`, not `ConnectionError`.** Its class-57 prefix would otherwise
send it down the infrastructure branch, which discards the handle — wrong twice over. Postgres
cancelling its own statement on our instruction leaves the socket usable, and the agent's tool
surface renders a `ConnectionError` as *the warehouse is unreachable* when the fact it needs is
*that query was too expensive*. The rest of class 57 is the operator taking the server away and
still discards.

Had this been in place, the turn would have spent ~120 s on attempt 3 and kept 22 minutes and two
attempts to find a formulation that worked.

## Tests

* `tests/govern/test_check_internals.py`
  * `test_a_sibling_cte_is_not_a_source_this_scope_can_bind_to`
  * `test_a_cte_outside_this_scopes_from_cannot_even_be_qualified_against`
  * `test_two_sources_answering_to_one_name_refuse`
* `tests/datasource/test_seed_contract.py`
  * `test_statement_timeout_cancels_one_statement_and_keeps_the_connection` — against a real
    server: `statement_timeout_ms=50` cancels `pg_sleep(5)`, the error carries `57014`, the handle
    survives, and the same connector still serves `SELECT 1`.
  * `test_the_statement_timeout_default_is_the_registers_and_not_a_literal`

Each refusal above is asserted beside a paired statement that must pass, per the authoring rules
at the top of `test_check_internals.py`: a narrowing that narrowed to nothing would satisfy a
refusal-only assertion.

Full suite after both changes: 1585 passed, 9 skipped, 17 xfailed. The 115-case adversarial suite
is inside that count and unchanged.

## Found while verifying: the attempt cap stranded a tool call

Re-running the same question confirmed the fix — 1 attempt, 36.3 s, `answered`, executing the
very SQL shape attempt 1 had been refused. Six further questions were then run to exercise the
change, and the third one crashed with `ValidationException` at `agent_core`, 0 attempts, 7.6 s.

It was not the binding layer. Thread `01a01bb8-ccb9-7570-aead-7a1026821c35` carried **12
`tool_use` blocks and 11 `tool_result`s**. The unanswered one was issued in a batch of two
parallel `run_query` calls with one slot left on the cap:

| call | upstream verdict | answered by |
|---|---|---|
| `tooluse_h73VL3JBXnCFKqCe3nMrBc` | allowed (the 5th) | **nothing** |
| `tooluse_0WjsorwU6wpoQ4jS0zQcCt` | blocked (the 6th) | `"Tool call limit exceeded…"` |

`ToolCallLimitMiddleware` splits one batch at the boundary. On `exit_behavior="continue"` — how
`_CapEndsTheTurn` constructs it — it writes a `ToolMessage` for the blocked call only; the
allowed one is the tool node's job. But the subclass jumps to `"end"` itself, which skips the
tool node, and its stranded-call filter asked for `name != tool_name` — so it looked straight
past a sibling that shared the capped tool's name.

Bedrock rejects a history containing an unanswered `tool_use`, and these messages sit at the
head of every later turn, so **the thread was permanently unusable**: every subsequent question
raised `ValidationException` before reaching a single attempt.

**The docstring is why this shipped.** It asserted the concern had lapsed — that on the pinned
langchain "the `"end"` branch now answers every stranded sibling with a `ToolMessage` before
jumping". True of upstream's `"end"` branch, and unreachable from `"continue"`. A correct claim
about code that does not run read as coverage.

Fixed by keying the filter on whether a call was answered rather than on what it is named
(`_unanswered_tool_calls`), and the paragraph now says whose job this is.
`test_a_cap_reached_inside_one_batch_of_the_capped_tool_answers_both_calls` reproduces the
strand — it fails on the old filter with the same shape the live thread had — and it sits beside
the pre-existing `..._beside_another_tool_call...` case, which covers the differently-named
sibling and passes either way. That pair is the point: one name filter satisfied one of them.

**This repairs no existing thread.** A conversation already carrying a dangling id stays
unreplayable; the checkpoint history is the record and nothing rewrites it. Affected threads have
to be abandoned or deleted.

## Not done

* **No gold measurement.** Every comparable claim in `binding.py` is sized against the 6 743 gold
  statements; `corpora/` is empty in this tree, so the numbers here come from the served store (16
  attempts) and from execution against the adversarial suite. The gold denominator is the honest
  place to size how many statements the refusal was costing, and it is owed.
* **`sqlite.py` was not touched.** It has no equivalent bound. The BIRD path runs through it, and a
  runaway query there still has only the agent wall clock.
* **`shape_estimate` still cannot see this shape.** A correlated subquery repeated per row scores
  the same as a scalar one. `statement_timeout` bounds the consequence; nothing yet predicts it.
