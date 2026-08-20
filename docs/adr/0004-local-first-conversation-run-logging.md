# 0004: Local-first conversation + run logging

- **Status:** Superseded in part (2026-08-18) by
  [ADR 0014](0014-one-conversation-store.md). Decided and built 2026-07-22; rewritten
  against the tree 2026-08-12; rewritten as a reversal record 2026-08-20. The JSONL
  turn log this ADR decided does not exist: `api/trace_store.py` and `runs/serve/*.jsonl`
  are deleted, and the ~194 historical turns in them were discarded rather than migrated.
  §5's withdrawal of the durable checkpointer is reversed. **0014 is the current store.**
- **Deciders:** project owner + design session
- **Related:** [0005](0005-v2-memory-layer-and-faceted-retrieval.md) §4 owns which fields
  a record carries; [0007](0007-http-surface-and-the-ui-contract.md) and
  [0009](0009-browsing-and-filtering-api.md) own the `/audit/*` shapes that project it.

## What was decided

Six things. The record itself survives. Where it lives, and the checkpointer withdrawal, do not.

1. **One append-only JSONL turn log**, written by the engine to `runs/serve/<date>.jsonl`,
   one object per line, never raising on write failure.
2. **The field set is the register**, judged at read time, not a hand-maintained list here.
3. **Two producers**, one per topology: `record_node` on the streamed graph, `_logged` on
   REST `/chat`. Both after `stamp`, so refusals, caps and crashes are recorded beside answers.
4. **Write-only on the live path.** Nothing reads the log back to steer the current turn.
5. **No durable checkpointer.** Conversation state does not survive a restart. The log is
   the only place every turn of a conversation survives. Withdrawn, not deferred, on three
   grounds: a new dependency, a checkpoint that would hold only the newest turn, and a claim
   that LangGraph Server owns the checkpointer.
6. **Unredacted, local-first, no TTL.** Verbatim questions, SQL and answers. No redaction
   column, no content tier, no retention sweep.

## What is true instead

**The envelope survived; the file did not.** A finished turn is still
`asked_at`, `question`, `answer_text`, `outcome`, `record`. `record_node` returns that
envelope onto `ServeState.turns`, an accumulating channel absent from `PER_TURN_RESET`,
and the durable checkpointer `langgraph.json` mounts is what persists it
([ADR 0014](0014-one-conversation-store.md)). There is one producer, because there is one
served topology: `POST /chat` and `POST /chat/resume` are deleted.

**§5's three grounds all fell.** The dependency constraint was lifted.
`ACCUMULATING` already answered "only the newest turn per thread." And `langgraph.json`
has a documented `checkpointer` field: the `local_dev` startup error guards
`.compile(checkpointer=…)`, which the server overrides, so this page reached the right
outcome about the served graph from the wrong mechanism. Live now:
`serve/checkpointer.py::conversation_checkpointer` (`AsyncSqliteSaver`) over
`runs/conversations.sqlite`. `compile_graph()` still defaults to `InMemorySaver`, which
keeps the test suite off a shared file. `compile_durable()` is the CLI and eval path,
against a second file, because a benchmark is not a conversation.

**"No TTL" does not survive.** `langgraph.json` sets `checkpointer.ttl` to delete at 90 days.
Under `langgraph dev` that sweep cannot fire (`sweep_ttl` is unimplemented on the in-memory
runtime), so the file grows. ADR 0014 §4 is the account.

**The audit surface reads thread state**, through `api/thread_turns.ThreadTurnLog`, not a
directory of JSONL. `TURN_LOG_DIR` survives only as the wire key `meta.log_dir`, and its
value is the conversation database path.

## What was learned

- **A checkpoint is not a transcript until something in state accumulates.** §5 was right
  that `PER_TURN_RESET` leaves only the newest turn. It was wrong that this was a property
  of the saver. The pattern needed, `ACCUMULATING`, was already in `state.py`.
- **Two write sites for one record will drift, and deleting a topology is how you prove
  there was only ever one.** The REST chat pair kept its own `InMemorySaver`. Falling back
  to it lost the conversation it was meant to rescue.
- **A greppable file is not a conversation store.** 116 of the 194 turns in the log were
  a test thread, and no field distinguished them. 0014 keeps the harness on a second file
  for that reason.
- **"LangGraph Server owns the checkpointer" was false.** The documented `checkpointer.path`
  in `langgraph.json` is how this deployment mounts one.

## Consequences of the reversal

- `/audit/turns` and `/threads/{id}/state` now carry every prior turn of a thread, not
  the newest. That is audit B1 widened, still unauthenticated (open-work §4.3).
- A turn costs a full checkpoint of accumulated state. There is no "keep the summary,
  expire the trace" middle setting: `AsyncSqliteSaver` does not implement `aprune`.
- `docs/glossary.md` retires "turn log" as a live term. Use **turn record** for what a
  finished turn produces and **conversation store** for where it lives.
