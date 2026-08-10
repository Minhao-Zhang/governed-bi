# State and resume audit of `serve/`

Audited against the principles in LangChain's *Thinking in LangGraph*. Read at
`a5727b0e9b847637fcf7f18f5aa9625f54721975` on branch `v2`, langgraph 1.2.10,
langgraph-checkpoint 4.1.1, langchain agents `factory.py` from the same lock.

Every claim below that says "measured" was produced by running the real graph through
`compile_graph()` / `build_graph(accept=...)` with `ScriptedChatModel`, not by reading.

## Two recommendations this audit does not make

The guide's error-handling table proposes `RetryPolicy` for transient failures. This repository
has none on purpose — re-running a node after it failed resamples a draw after seeing it — and
`llm_max_retries` (provider SDK, default 3) is the different, existing knob. Nothing here asks
for node retry.

The guide shows `Command` for in-node routing; `serve/graph.py` uses conditional edges. No
failure was found that this causes, so it is not reported as one. (`serve/tools.py` does return
`Command` from every tool, which is the case where it is load-bearing: the tools have to write
`attempts_by_call` into the nested agent's checkpointed state.)

---

## 1. The interrupt-first hazard — checked, and the design holds

There is exactly one `interrupt()` in `src/`: `serve/tools.py:374`, in the `ask_user` tool.

Four statements execute before it:

```python
digest = hashlib.sha256(f"{turn_id}\x1f{question}".encode()).hexdigest()[:12]
clarification_id = f"clar-{turn_id}-{digest}"
started = {"clarification_id": clarification_id}
emit(kind="tool", step="ask_user", status="start",
     event_id=tool_event_id("ask_user", _call_id(runtime)), detail=started)
answer = interrupt({...})
```

All of this re-runs on resume. Measured (`stream_mode="custom", subgraphs=True`, one pause, one
resume):

```
pass1 tool events: [('tool','ask_user','start','ask_user:c1', seq 26)]
pass2 tool events: [('tool','ask_user','start','ask_user:c1', seq 28),
                    ('tool','ask_user','ok',   'ask_user:c1', seq 29)]
```

So the `start` row **is** emitted twice. It is the only re-executed side effect, and it is
survivable by construction rather than by luck:

- `clarification_id` is a pure function of `turn_id` and the question text, both stable across
  the pause, so the id the client validates against (`api/routes.py::_pending_on_thread`) is the
  same id the resumed body computes.
- `event_id` is `ask_user:<tool_call_id>`, identical in both passes, and `events.py` states the
  invariant it was chosen for ("Stable rail row id across resume replay"). A client keyed on
  `id` collapses the pair; only `seq` differs, and `seq` is documented as process-monotonic with
  ordering owned by the client.
- `emit` writes to the `custom` stream and nothing else. No durable sink consumes it — there is
  no `stage_events.jsonl` writer anywhere in `src/`, and `api/routes.py::_logged` skips paused
  turns (`if not record.get("turn_id"): return shaped`), so the audit log gets one row per
  completed turn.

The same is true one level out. `wrap_node.inner` runs `_start(state)` (emits
`rail agent_core start`) and `_started(state)` before the body, and both re-run:

```
probe2 repeated across the pause: [('rail','agent_core','start','agent_core:turn-probe')]
```

`_started` is idempotent by the write-only-when-absent guard at `wrap.py:99`, and
`turn_started_at` is already committed by an earlier node, so the resumed turn does not restart
its own clock.

**Nothing with a durable side effect precedes the interrupt.** Measured across a pause + resume:
`usage` holds one row, `clarifications` holds exactly one entry per question asked, and
`tests/serve/test_agent_tools_hitl.py::test_the_ledger_survives_the_interrupt` already pins the
ledger case with the failure it was written for quoted in its docstring. The reason it works is
worth restating because it is not the obvious one: the interrupted outer task's writes are
**discarded**, so the pre-pause `usage`/`clarifications`/`execution` were never committed at
all; the resumed run commits them once, reading them out of the nested agent's own checkpointed
channels (`serve/agent_state.py`). Verified in langgraph 1.2.10 that the two mechanisms this
depends on are real:

- `langchain/agents/factory.py:1881` dispatches `Send("tools", [tool_call])` per pending call,
  filtered by `c["id"] not in tool_message_ids` — a `run_query` that completed before the pause
  is not re-sent.
- `langgraph/pregel/_loop.py:1034` takes the `is_resuming` branch and never reaches
  `map_input`, so `agent.astream({"messages": inbound}, ...)` on the resumed pass **discards the
  input**. This is what keeps `fresh = out_messages[len(inbound):]` correct and stops
  `_question_message`'s freshly-constructed (new-uuid) `HumanMessage` from being appended a
  second time by `add_messages`.

### 1a. DEFECT — two `ask_user` calls in one AI message cross-wire the answer

Nothing bounds `ask_user` to one call per assistant turn, and both the resume route and
`resume.resume_clarification` assume exactly one pending clarification.

`api/routes.py::_clarification` returns the **first** interrupt whose `kind` is `clarification`;
`resume_clarification` issues a bare `Command(resume=answer)`. Measured, the order the two
interrupts surface in is a race, while the resume value always lands on the first tool call:

```
run 0 surfaced: which year?    | all: ['which year?', 'which region?']
run 1 surfaced: which region?  | all: ['which region?', 'which year?']
run 2 surfaced: which year?    | ...
run 3 surfaced: which region?  | ...
```

Concrete failing sequence (measured, thread `t-two2`, calls `c1="which year?"`,
`c2="which region?"`):

1. The model emits both `ask_user` calls in one `AIMessage`. Both tasks interrupt.
2. `__interrupt__` surfaces `which region?` first, so `/chat` returns
   `{"outcome": "clarification", "text": "which region?"}`, and `_pending_on_thread` agrees, so
   the `clarification_id` equality check in `chat_resume` passes.
3. The user answers the region question. `Command(resume=<that answer>)` is delivered to `c1`.
4. Observed final state: `[{'question': 'which year?', 'answer': '2020'}, ...]` — the answer to
   one question recorded against, and handed to the model as, the other.

Two smaller consequences of the same gap: after the first resume the turn silently pauses again
(`after resume#1 interrupts: ['which region?']`, `clarifications: []`), which `_shape` reports
as a fresh `outcome: "clarification"` with no indication that a round-trip was consumed; and
`serve/__main__.py::_pending_clarification` has the same first-wins reader.

The fix is a choice, not a mechanical one — either refuse a second `ask_user` in the same
assistant turn at the tool boundary, or carry the interrupt id through the HTTP surface and use
langgraph's resume-map form (`{interrupt_id: value}`), which `_loop.py:910` already supports and
which is the only shape that disambiguates. Worth noting that langgraph's own guard
("When there are multiple pending interrupts, you must specify the interrupt id when resuming")
does **not** fire here, because both interrupts bubble up through a single outer task.

---

## 2. "Keep state raw, format prompts on-demand"

### `delivery.context_block` — justified, and for a resume-specific reason

Judged, not assumed. The prompt's framing is slightly off: `context_block` is not hashed into
`delivery_hash`. `render_context` returns `(block, sha256(block))` (`serve/context.py:60`), that
digest is `delivery.context_hash`, and `delivery_hash_for` combines `context_hash` with the
sorted `tool_delivered` map. So the block is the **preimage of a published record field**.

Storing it is right for three reasons, in increasing order of force:

1. `assemble` owns the eviction ladder and writes `delivery.evicted`, a `Tier.decision` record
   field. Re-rendering in `agent_core` would re-run eviction, which is a second implementation
   of a derivation AGENTS.md says must have exactly one.
2. `_context_middleware` closes over the block once per `agent_core` build, which is what makes
   it byte-identical on every model call of a turn — the half of prompt caching that is this
   repo's (`_cache_point`).
3. **It is what makes the delivered context survive the pause.** On resume `agent_core` re-runs
   and rebuilds the middleware. If the block were rendered on demand it would be rendered from
   `configurable["assets_by_id"]`, which on `/chat/resume` comes from `_session()` and on the
   platform path from `session_from_environment()` — process-cached today, but not across the
   restart that a durable checkpointer exists to survive. A resumed turn would then deliver a
   different block from the one the pre-pause model calls saw while the checkpointed
   `context_hash` still named the old one: an artifact naming a treatment it did not receive.

The cost is real and already known: the block is checkpointed and re-serialised every superstep
(`compile_graph`'s docstring measures 101 KB after one turn, 844 KB after six). It is in
`PER_TURN_RESET`, so it does not accumulate across turns. No change recommended.

### DEFECT — `NodeFailure.detail` is prose that nothing reads

`settle_failure` (`serve/state.py:184-197`) keeps the first failure and folds a concurrent second
one into a sentence:

```python
also = f"{right.get('stage')}/{right.get('error_type')}"
detail = left.get("detail")
return {**left, "detail": f"{detail}; also failed: {also}" if detail else f"also failed: {also}"}
```

This is exactly the pattern the guide argues against, and here it loses information rather than
merely deferring it. The five facet nodes fan out concurrently, so two of them crashing is the
case this reducer exists for — and the second one's `stage` and `error_type` are recoverable
only by parsing English.

They are not recovered. `detail` has no reader in `src/`: `stamp._path_signals` reads
`failure["stage"]` and `failure["error_type"]`; `events.rail_observation` reads
`failure["error_type"]`; `register/record.py` publishes `failed_stage` and `error_type` and has
no `failure` field. `api/graph_app.py:266` writes a third `detail` ("no human message in the
conversation") which is likewise never read. So a turn in which `facet_term` raised
`TimeoutError` and `facet_metric` raised `KeyError` records one class and formats the other into
a string that is checkpointed and dropped.

Raw shape that would cost nothing: `NodeFailure` gains `also: list[NodeFailure]` (or the reducer
keeps a list and `stamp` names the first), and the record can then report how many rails failed
concurrently — which is currently unanswerable from any artifact.

---

## 3. Channels across a pause and resume

### Checked and correct

- **`usage` (`operator.add`)** does not double. Every writer stamps `turn_index`
  (`agent_core`, `guard`, `facets`, `narrate`, `reflect` all pass it to `usage_row`), and
  `stamp._usage_for_turn` filters. Measured after a pause + resume: exactly one `agent_core`
  row. It cannot double, because the pre-pause write was discarded with the interrupted task.
- **`clarifications` (`operator.add`) does not duplicate on resume.** Measured: one pause →
  one row; two pauses in one turn → two rows, one per question, each carrying its own
  `clarification_id` and `turn_id`.
  `test_agent_tools_hitl.py:440` already pins this with the regression it came from named.
  The reason is structural: the rows are read out of the nested agent's `clarifications_by_call`
  (keyed by `tool_call_id`, `merge_by_call`) and lifted into the outer channel exactly once, on
  the run that commits.
- **`messages` (`add_messages`)** — the resumed `agent_core` commits
  `out_messages[len(inbound):]`, which spans both sides of the pause, once. Correct because
  langgraph discards the re-supplied input on a resume (verified in `_loop.py`, above).
- **`turn_started_at`** survives the pause (`wrap.py:99` writes only when absent), so
  `latency_sec` on a clarified turn includes the human's thinking time. Deliberate and
  documented — the field is how long the user waited.
- **`path_kind` / `failure` and the `RESET` sentinel.** `cleared()` and its docstring correctly
  describe langgraph 1.2.10's `BinaryOperatorAggregate` behaviour: a Union-annotated channel
  seeds `MISSING` and the first write bypasses the reducer, which is why `stamp` normalises.
- **Channel categorisation is complete.** All 45 `ServeState` channels are covered by
  `PER_TURN_RESET ∪ ACCUMULATING ∪ TURN_IDENTITY ∪ TEST_HOOKS` with no orphans in either
  direction (measured), and `tests/serve/test_state_channels.py:273` enforces it. This is the
  single best thing in the file: it is the only reason "conspicuously not in `PER_TURN_RESET`"
  can be answered at all.
- **The nested agent's namespace is per-turn.** Its checkpoint ns is `agent_core:<task_id>` and
  `task_id = f(parent checkpoint_id, ns, step, node, …)` (`pregel/_algo.py:990`), so turn 2 gets
  a fresh nested state — the attempt cap, `thread_tool_call_count` and `clarifications_by_call`
  all reset per turn, which is what `run_query_attempt_cap` means. Resuming reloads the *same*
  parent checkpoint, so the task id is stable and the pause resumes into the same nested state.
  `_CapEndsTheTurn`'s choice of `thread_limit` over `run_limit` is correct for the same reason,
  and its docstring names the failure (`run_tool_call_count` is an `UntrackedValue` and would
  hand a resumed turn a second budget).

### DEFECT — `accept` erases the clock it is supposed to start

`wrap.py`'s module docstring says the wrapper is "where the turn's clock starts, so
`turn_started_at` has one answer rather than one per entry point (the graph starts at `accept`
on the served path…)". It does not, on that path.

`wrap_node.inner` merges update over the stamp:

```python
began = _started(state)          # {"turn_started_at": time.time()} on a fresh turn
update = await _body(state, config)
return {**began, **update}       # update wins
```

`api/graph_app.py::_accept_node` returns `{**PER_TURN_RESET, …}` on both its paths (directly on
the no-human-message branch, and via `Session.turn()` on the happy one), and `PER_TURN_RESET`
contains `"turn_started_at": None` (`state.py:326`). So `accept`'s own stamp is overwritten by
`None` in the same dict literal, on every turn including the first of a fresh thread. Measured:

```
probe1 accept-on-fresh-thread turn_started_at = None
probe1 guard-after-accept     turn_started_at = 1786367248.4956384
```

End to end through a real `build_graph(accept=…)` with an `accept` that sleeps 250 ms (standing
in for `session_from_environment()` plus the query-embedding call at `graph_app.py:274`):

```
real wall clock for the turn : 0.266s
latency_sec in the record    : 0.010s
```

`latency_sec` is a register field. On the served path it silently excludes everything `accept`
does, including a network round-trip to the embedder. Scope is worth stating precisely so this
is not over-read: `eval/harness.py` compiles through `compile_graph()` with **no** `accept`
node, so no BIRD number is affected — the understatement is confined to served turns, i.e.
`runs/serve/*.jsonl` via `_record_node` and the `/chat` responses.

Two ways out, both small: have `_accept_node` drop `turn_started_at` from the dict it returns
the way it already does for `messages` (`turn.pop("messages", None)`, `graph_app.py:277`), or
have `wrap_node` merge the other way for this one key. The first is more honest — `accept` is
the node that knows it is starting a turn.

### `clarifications` is written, checkpointed, and read by nothing

Not a resume bug — it round-trips correctly — but worth recording. `agent_core` is the only
writer, `register/record.py` has no clarification field, and the only readers in the tree are
two assertions in `test_agent_tools_hitl.py`. Combined with `clarification_requested` being
written `False` unconditionally at `agent_core.py:116` and `:522` (so `Outcome.clarification` is
unreachable from `stamp`, which `register/stages.py:177-181` documents as deliberate — the
transport surfaces it via `__interrupt__` instead), the consequence is that **no durable
artifact records that a turn consulted a human.** A resumed turn's record is byte-identical in
shape to one that never asked. That is a measurement gap in a repository whose whole SME story
is about the value of asking, and it should be a decision rather than a leftover.

---

## 4. Node granularity against checkpoint boundaries

`agent_core` is large — model calls, five tools, a middleware stack, a ledger projection — and
the guide's point is that a checkpoint boundary is a node boundary, so a failure re-executes the
whole node. Checked for a concrete duplication or loss; **found none**, and no split is
proposed.

The reason the size is safe is specific and already load-bearing in the code:

- The only thing that re-executes `agent_core` is a resume. There is no `RetryPolicy` anywhere,
  and `graph.py::_node_timeout` returns `None` for `agent_core` on purpose so `wrap_node`'s
  `wait_for` cannot reduce the update to `{failure, path_kind}` and discard the streamed ledger.
- Everything expensive inside it — model calls, executed statements, delivered payloads — is
  committed to the **nested** agent's own checkpoint as it happens, keyed by `tool_call_id`.
  The outer node's re-execution re-reads those channels rather than re-doing the work. This is
  what `serve/agent_state.py`'s module docstring is about, and it is accurate.
- `_run` uses `astream` and keeps the last committed frame precisely so a crash mid-node does
  not erase work that really happened, and re-raises `GraphInterrupt` with **no** partial state
  so a pause cannot commit half a turn. Both docstrings name the measurement that moved them.
- `AttemptBook` is rebuilt on every `build_tools` call with an empty in-flight set and takes its
  committed count from the checkpointed `attempts_by_call`, so a resumed turn does not get a
  fresh attempt budget. `_chargeable` selects rows by `path` rather than key prefix, and
  `CAP_LEDGER_KEY` is a constant so two enforcers collapse to one ledger row.

One residual hazard, labelled as such because I could not construct it deterministically:
`_run`'s hard wall is `asyncio.wait_for(anext(stream), …)`. If it expires in the same instant the
nested graph is raising `GraphInterrupt`, `wait_for`'s `TimeoutError` is what reaches the
`except Exception` clause and the pause becomes `path_kind: "crashed"`. The window is a single
frame boundary and the interrupt write is already persisted by then (langgraph puts the
`INTERRUPT` write during the tick, before `anext` returns), so the thread stays resumable while
the record says the turn crashed. Cheap guard if it is ever worth one: re-check for a pending
interrupt before returning the timeout failure.

---

## Summary

| # | Finding | Severity |
|---|---|---|
| 1a | Two `ask_user` calls in one AI message: the surfaced question and the resumed one are chosen by different rules, and the order is a race. Measured cross-wire. | real, reachable |
| 3 | `_accept_node`'s `PER_TURN_RESET` overwrites the `turn_started_at` that `wrap_node` just stamped, so `latency_sec` on the served path excludes the whole `accept` node including the embedder call. Measured 0.266 s wall vs 0.010 s recorded. Served path only; no BIRD number affected. | real, measurement |
| 2 | `NodeFailure.detail` formats a concurrent second failure into prose that nothing reads; the second rail's `stage` and `error_type` are unrecoverable. | real, minor |
| 3 | `clarifications` and `clarification_requested` leave no trace of a clarification in any durable record. | decision needed |
| 4 | `_run`'s hard wall could convert a pause into `crashed` at a frame boundary. | suspicion, not constructed |

Checked and correct: the interrupt-first ordering in `ask_user` (only a stream event precedes it,
with a stable id); `usage`, `clarifications`, `messages`, `path_kind`, `failure` and
`turn_started_at` across a pause; the nested agent's per-turn namespacing and the attempt cap's
survival of a resume; the completeness of the channel categorisation and the test that pins it;
`delivery.context_block` as a justified formatted-in-state exception; and `agent_core`'s size,
which duplicates nothing.
