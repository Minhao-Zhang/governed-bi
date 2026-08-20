# 0014: One conversation store, on a durable LangGraph checkpointer

- **Status:** Accepted and built (2026-08-18). The durable checkpointer, the accumulating turn
  channel and the thread-state audit surface are verified against a live server;
  `POST /chat`/`POST /chat/resume`, `api/trace_store.py` and `runs/serve/` are **deleted**, the
  clarification-resume identity gate moved into the graph, and the frontend's cross-store join is
  gone. The ~194 historical turns were **discarded rather than migrated** — the owner's explicit
  call, so the audit surface starts empty rather than carrying the old file forward.
- **Deciders:** project owner + design session (2026-08-18)
- **Supersedes:** [0004](0004-local-first-conversation-run-logging.md) §5 (which withdrew the
  durable checkpointer) and amends its §3/§4 (two write sites; the log as the only place every turn
  survives).
- **Related:** [0007](0007-http-surface-and-the-ui-contract.md) and
  [0009](0009-browsing-and-filtering-api.md) own the `/audit/*` shapes this preserves;
  [0005](0005-v2-memory-layer-and-faceted-retrieval.md) §4 owns *which fields* a record carries;
  [0006](0006-execution-time-governance.md) B9 is the resume identity gate named above.

## Context

One conversation was recorded in two independent places:

| | Where | Written by | Read by |
|---|---|---|---|
| Turn log | `runs/serve/<date>.jsonl` | `api/trace_store.append_turn`, from **two** sites | `/audit/turns`, `/audit/turns/{id}/trace` |
| Conversation state | server thread state (`.langgraph_api/*.pckl`); a separate `InMemorySaver` on `/chat` | the platform | `useStream`, `ui/lib/threads.ts` |

The cost was not duplication, it was the **join**. `ui/hooks/use-stream-chat.ts` fetches
`/audit/turns` for the open thread and aligns log rows to chat messages **by matching question
text**, because a positional index was tried and broke: one thread had four log rows for a two-turn
transcript. A record and the message it describes lived in different stores, so the client guessed
the correspondence by string comparison.

Measured on the tree the day this was decided: the log held **194 turns across 54 threads** while
the server's thread store held **8 threads** — so 46 of 54 conversations existed only in the log.
**116 of the 194 turns were `t-transport`**, a test thread id, because
`tests/serve/test_chat_transport.py` ran against the repository's own log; no field distinguished
test traffic, so every count drawn from that file was contaminated.

### Why ADR 0004 §5 could be reversed

That section withdrew the durable checkpointer on three grounds, and all three fell:

1. *"It would need a new dependency."* The owner lifted that constraint. `pyproject.toml`'s actual
   objection was "zero importers … declaring a dependency for an unbuilt saver would advertise a
   capability nobody built" — spent, not overruled, once the saver is built.
2. *"It would still hold only the newest turn per thread."* **The load-bearing correction.** That
   is true only because `PER_TURN_RESET` clears the per-turn channels. `state.py` already declared
   `ACCUMULATING` for channels that survive it, so the pattern needed already existed in the tree.
3. *"LangGraph Server owns the checkpointer."* **False.** `langgraph.json` has a documented
   `checkpointer` field. What is actually overridden is `.compile(checkpointer=…)`:
   `langgraph_api/graph.py:get_graph` always does `copy(update={"checkpointer": …})`, and because
   our `serve` entry is a *factory* the `local_dev` "custom checkpointer" startup error is never
   reached — the override is **silent**. So 0004 was right about the outcome and wrong about the
   mechanism.

## Decision

### 1. A durable SQLite checkpointer, chosen through `langgraph.json`

`serve/checkpointer.py:conversation_checkpointer` is an `@asynccontextmanager` yielding
`AsyncSqliteSaver`; `langgraph.json` names it under `checkpointer.path`; both it and the harness opener go through one
`_open`, which sets a 30 s busy timeout and `PRAGMA synchronous=NORMAL` (WAL is already on from
`setup()`). A `serde: {pickle_fallback: false}` block was configured here and **removed**: that
setting is read by `langgraph_api`'s *built-in* serializer, and a custom checkpointer brings its own
(`JsonPlusSerializer`), so it was an inert line that read as a hardening control. Verified live: the server
logs *"Using custom checkpointer: AsyncSqliteSaver"*, and a thread's state survived a hard kill and
a restart.

It lives in `serve/` and not `api/` because `tools/check_imports.py` forbids `serve` and `eval` from
importing `api`, and the CLI and eval need it. It imports `governed_bi.paths` **absolutely** because
the server loads the file *by path* (`exec_module` on a spec), so a relative import has no parent
package — the same reason `api/graph_app.py` does.

**Two databases, one mechanism.** `CONVERSATION_DB` holds served conversations; `HARNESS_DB` holds
the CLI's and eval's. Measurement traffic is not conversation: 131 benchmark questions would make
the conversation store mostly benchmark, which is how `t-transport` contaminated the log.

**Neither may be the analytics warehouse.** `assert_not_a_warehouse` raises at configuration time on
a value containing `host=`, `dbname=`, `password=` or a `postgres://`-style URL. The facilities
Postgres holds real data and a checkpointer pointed at it would write conversation state into it on
the first turn; a misconfiguration that fails at import beats one found while reading the wrong
table.

### 2. `ServeState.turns` accumulates the record, so a checkpoint holds every turn

`turns: Annotated[list[TurnEntry], operator.add]`, declared in `ACCUMULATING` and deliberately
absent from `PER_TURN_RESET`. `TurnEntry` is **exactly** the five keys the deleted JSONL line carried
(`asked_at`, `question`, `answer_text`, `outcome`, `record`) — kept, because the audit surface
already read them and `thread_turns.summarise_turn` still projects them.

`api/graph_app.record_node` returns `{"turns": [entry]}` and **takes no sink at all** — the argument
it used to receive was the turn log, and it went with the log. During the transition it briefly wrote
both, and the rule then was that one envelope served both sinks: two constructions of "the same" turn
drift, and a checkpoint disagreeing with the log about a turn's `outcome` gives an auditor two answers
and no way to choose. There is now one store, so the question cannot arise.

`tests/serve/test_state_channels.py` asserts that
`PER_TURN_RESET | ACCUMULATING | TURN_IDENTITY | TEST_HOOKS` partitions every channel, so a new
channel is structurally forced to declare its lifecycle. That test is why this could not be added
carelessly.

### 3. The audit surface changes its **source**, not its shape

`api/thread_turns.ThreadTurnLog` is injected at the `make_app(session, turn_log)` seam — a
**reader-only** duck type (`list_turns`, `get_turn`, `SUMMARY_FIELDS`, `TURN_LOG_DIR`), since there
is no second sink to write to. `make_app` lost its `graph` parameter with the chat pair. Both
`/audit/turns` and `/audit/turns/{id}/trace` return byte-identical payloads, which is what lets
`npm run check:api` serve as the regression test for a change of store — measured after the swap:
**16/16 routes parse, 0 fail.**

Reads go through `get_client(url=None)`, documented to *"first attempt an in-process connection via
ASGI transport"* — valid because this module only runs inside the server that mounts it. No port, no
loopback request, no credential. `extract={"turns": "values.turns"}` rather than selecting `values`:
an unprojected thread carries the whole of `ServeState`, measured at 2.42 MB for sixteen threads.

Two rules the file-backed log never needed, both tested:

- **Global ordering.** The log was one time-ordered file; thread state is per conversation. Reading
  threads newest-updated-first and concatenating their turns yields rows that *look* sorted and are
  not, because conversations interleave in time. Rows are sorted by `asked_at` after collection.
- **`limit` counts turns, not threads.** `threads.search`'s limit counts threads, so filling a
  budget of 500 turns pages until it is met; returning the first page would be indistinguishable
  from the end of the list.

`thread_turns.summarise_turn` is the one projection, moved out of the deleted module rather than
reimplemented. `missing_required` stays computed **at read time**, preserving 0004 §2: a turn is
judged by today's register, not by the register of its write date.

### 3a. `/capabilities` reports durability as an observation

`durable_checkpointer_configured()` reads `langgraph.json` and is true only when `checkpointer.path`
names both a factory and a file that exists, so removing the declaration flips the flag with nobody
editing the line (ADR 0009 D4). It does not claim to see a *live* saver: the platform injects the
saver into the compiled graph and this custom app never holds that graph, so a live handle is
genuinely unobservable from here. `hitl_survives_process_restart` is the same observation rather than
a second belief — an `ask_user` interrupt *is* checkpoint state. What was verified when this ADR
landed is thread state surviving a hard kill. The clarification half **was** watched end to end later, on
2026-08-19: a turn paused on `ask_user`, the process was killed with nothing left listening, a
fresh one re-mounted the prompt from checkpointed interrupt state, and answering it resumed the
turn to a correct answer
(`docs/analysis/adopting-the-downstream-fork-2026-08-19.md`). That is **one hand-run observation
and no test coverage**: every HITL test compiles through `compile_graph`, whose saver is
`InMemorySaver`, and `tests/serve/test_the_durable_saver_survives_a_process.py` reaches the saver
through `update_state` because that file is about persistence rather than the serve graph. So
nothing automated drives a real `ask_user` interrupt and resume across a process boundary
through `AsyncSqliteSaver`.

### 4. Retention is a TTL that **deletes**, and there is no gentler option

`checkpointer.ttl` is `{strategy: "delete", default_ttl: 129600 (90 days), sweep_interval_minutes:
60, sweep_limit: 1000}`.

`keep_latest` — prune old checkpoints, keep the thread and its latest state — was the intended
setting, and it is **not available**: `AsyncSqliteSaver` does not implement `aprune`, and the server
warns exactly that at startup. So there is no "keep the summary, expire the deep trace" middle
state: after 90 days a conversation leaves History and Audit together.

This was checked wrongly first. `hasattr(AsyncSqliteSaver, "aprune")` returns `True` because the
base class defines a stub; the method is not overridden. **A capability probe that cannot tell an
implementation from an inherited stub is not a probe** — the server's own startup warning is the
authority, and it names `adelete_for_runs` and `acopy_thread` as missing too.

**Under `langgraph dev` the sweep cannot fire at all.** `langgraph_runtime_inmem`'s `sweep_ttl` is
`return (0, 0)` with the comment "Not implemented for inmem server", and nothing in that runtime's
lifespan calls it; the only TTL driver is the deployed gRPC path. So retention is **inert locally**,
not merely unobserved. The configuration is nonetheless correct for a deployment: `"delete"` maps to
`delete_all`, which the adapter routes to `adelete_thread`, and `AsyncSqliteSaver` does implement
that. Locally this means growth is bounded by nothing — which is why the quadratic cost above
matters rather than being a curiosity.

### 5. The CLI and eval get durability too, which required changing the sync bridge

`serve/graph.compile_durable()` compiles against a durable saver on a loop it then pins to the
returned `_SyncApp`. `_SyncApp` gained an **optional** `loop`; `None` remains the default, so
nothing changed for the callers already there — `compile_graph()` behaves exactly as before, and in
particular the test suite keeps its in-memory saver rather than sharing one file and reusing the
fixed thread ids (`t-hitl`, `t-ledger`) that would make a run depend on the previous one.

Three properties of `aiosqlite` forced this shape, all measured:

1. It binds its connection to the loop that opened it. Reusing a saver across two `asyncio.run`
   calls does not raise — it **hangs**, because the second call's future belongs to a closed loop.
2. Its worker `Thread` is not a daemon, and CPython joins non-daemon threads **before** running
   `atexit` handlers. So an unclosed saver stops the process from exiting, and `atexit` cannot fix
   it. Hence `_SyncApp.close()` and the `finally` blocks in the CLI and the harness.
3. The synchronous `SqliteSaver` is not an escape: every node here is `async def`, so LangGraph
   calls `aget_tuple`, and that class raises `NotImplementedError` on every async method.

`eval/harness._evict` was changed for the same reason: `AsyncSqliteSaver.delete_thread` *exists and
raises*, and `_evict` probes with `getattr` and swallows exceptions — so eviction would have stopped
silently and the harness database grown without bound. It now prefers `adelete_thread` on the
graph's own loop.

## Consequences

**Positive**

- A conversation survives a restart. Verified by hard-killing the server and reading the thread back.
- The record and the message it describes are in one store, so the client's text-matching join has
  nothing left to do (its removal is pending, §Status).
- `metadata.source` is available as a native filter (`threads.search(metadata=…)`), which is how the
  `t-transport` class of contamination gets labelled rather than guessed at from a thread id.
- `disable_store: true` stays. This design uses **threads**, not the Store — `BaseStore.search()`
  has no sort parameter and `threads.search` does — so the audit surface never needed the `/store/*`
  routes re-opened on a server that has no transport credential.

**Negative / costs**

- **Checkpoint growth is quadratic in turns-per-thread, and this is the change's worst cost.**
  `AsyncSqliteSaver` has exactly two tables, `checkpoints` and `writes`; there is **no per-channel
  blob table** (`PostgresSaver` has one, this saver does not), so each super-step serialises the
  *whole* checkpoint into a single BLOB. An accumulating channel is therefore rewritten ~15 times
  per turn — once per super-step — and carries every prior turn each time. Measured: 1 turn =
  91 KB, 5 turns = **11.4 MB**, marginal cost ~1.1 MB per extra turn and rising. A 20-turn
  conversation writes ~25 MB for its last turn alone. An earlier draft of this ADR said "a turn
  costs ~3.9 MB", which was the cost of turn *one* read as a constant; that measurement came from  [retired]
  the dev server's pickle store, which **does** key blobs per channel, and does not transfer to
  the saver this ADR mounts. Upstream also warns this saver is "not recommended for production
  workloads due to limitations in SQLite's write performance".
- **Audit horizon is now the retention period**, and §4's `delete` strategy is not graceful.
- **Swapping the checkpointer orphaned the eight pre-existing threads' history** — measured: zero
  checkpoints each afterwards. Their thread rows and latest `values` remain in `.langgraph_ops.pckl`,
  so History still lists them, but their per-turn history is stranded in the old pickle shards.
- **Greppability is lost.** `runs/serve/*.jsonl` is readable in three lines of Python, and it is what
  established the 194/54/116 counts above. The same investigation now goes through the SDK.
- **The leak surface widens, on the checkpoint-read surfaces only.** Measured after the change: the
  root `values` frames of a streamed run carry `answer` and `messages` and **not** `turns` (4 frames,
  0 with it), while `get_state` and `GET /threads/{id}/state` return all 47 channels. So what got
  wider is the read-a-thread surface, where one `answer["record"]` per read became *every* prior
  turn's record. Combined with finding **A7** — `/audit/turns` and `/threads/*` require no
  credential — that is a real enlargement, mitigated by nothing yet.
- **Under `langgraph dev` the thread index is still pickle.** `checkpointer.path` replaces the
  *checkpoint* store only; `threads`/`runs`/`assistants` live in `.langgraph_ops.pckl`, owned by
  `langgraph-runtime-inmem`, with no config knob and a 10-second flush. Production's Postgres
  runtime holds both.
- **SQLite is single-writer.** With `workers > 1` the harness opens one connection per worker
  against one file and they contend as writers.

## Alternatives considered

- **A lean summary channel plus the full record fetched from checkpoint history.** The original
  design. Rejected once `trace_store`'s reader was read properly: `incomplete_fields` and
  `missing_required` are computed from the **whole** record against today's register, so a lean row
  cannot produce the list the audit page already renders without freezing that judgement at write
  time. Carrying the full envelope also made the migration path and the projection reuse trivial.
- **A hand-rolled SQLite table replacing `runs/serve/*.jsonl`.** Rejected by the owner: the point is
  a LangGraph-native primitive, for maintainability.
- **The Store (`/store/*`) as the audit index.** Rejected: `BaseStore.search()` has no sort or order
  parameter, and it would mean re-enabling `disable_store` on an unauthenticated server.
- **Reading the platform's checkpointer directly from a custom route.** Rejected: a second handle on
  the same database is a second answer to what a thread contains. The in-process client is the
  supported seam.
- **`ttl.strategy: "keep_latest"`.** Not available — §4.

## What this ADR does not cover

- **Which fields a record carries** — ADR 0005 §4 and `register/record.py`.
- **The HTTP shape of `/audit/*`** — ADR 0007 and 0009. This change preserves it deliberately.
- **Route authentication.** Finding A7 stays open and this change widens what it exposes (§Consequences).
- **Eval's measurement artifacts.** `runs/eval/` is unchanged; `docs/measurement.md` owns it. Eval
  gets a durable *checkpointer*, not a turn record — it is not a conversation.
