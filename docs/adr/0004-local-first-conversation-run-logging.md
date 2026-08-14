# 0004: Local-first conversation + run logging

- **Status:** Accepted and built (2026-07-22; rewritten against the tree 2026-08-12).
  The turn log is built and is the conversation history. **The durable checkpointer half
  was never built and is withdrawn**, not deferred — see §5. This page was rewritten
  because the version it replaced described a v1 implementation deleted in `2347ae3`
  and enumerated seams (`stack.py`, `middleware.py`, `governance.py`, `analyst/agent.py`)
  that no longer exist; it also closed with "portable record format … both implemented;
  default SQLite", which was never true in either tree.
- **Deciders:** project owner + design session
- **Related:** [0002](0002-governed-agentic-serve-runtime.md) (the governance ledger this
  log persists — Inv #10's deferred durable sink is this file);
  [0005](0005-v2-memory-layer-and-faceted-retrieval.md) §4 (the declared register, which owns
  *which fields* a record carries); [0007](0007-http-surface-and-the-ui-contract.md) and
  [0009](0009-browsing-and-filtering-api.md) (the `/audit/*` routes that project it).

## Context

The owner's need was **durable conversation history to reference later, with metadata
alongside**, held by the engine rather than by a client, so the UI, the CLI and eval inherit
one record instead of each building their own. The storage backend was explicitly not
prescribed.

Two properties of the runtime make that need sharper than it sounds:

- **A checkpoint is not a transcript.** `serve/accept.py` applies `PER_TURN_RESET` at the top
  of every turn, so a thread's checkpoint only ever describes its *newest* turn. Read the
  checkpoint back and the earlier turns of the same conversation are gone.
- **Cloud tracing is a no-op without keys and is vendor-shaped.** Tracing is whatever the
  LangSmith environment variables the SDK reads say it is; there is no module in the tree
  that owns it. A record that only exists when someone configured a vendor is not a record.

## Decision

### 1. One append-only JSONL turn log, written by the engine

`api/trace_store.append_turn` writes **one JSON object per line** to
`runs/serve/<YYYY-MM-DD>.jsonl`. `TURN_LOG_DIR` is overridable
(`GOVERNED_BI_TURN_LOG_DIR`) so a test never writes into the repository's own log.

Each entry is `asked_at`, `question`, `answer_text`, `outcome`, and `record`. The first four
sit **beside** the record rather than merged into it: merged, every record read back out of
this file would fail `register/record.undeclared_keys`.

`append_turn` **never raises**. It returns `(turn_id, error)` and the caller decides — a turn
that answered is not a turn that failed because the log could not be written. On the REST path
that error surfaces to the client as `audit_error`; on the served path `record_node` swallows
it, because nothing follows `record` that could receive a `crashed` stamp.

### 2. The record's field set is declared, not hand-maintained

What a record *contains* is `register/record.RECORD_REGISTER` (ADR 0005 §4), where every field
names its producing stage, its tier, and what absence means. This ADR does not re-author that
list, and a reader who wants the fields should read the register.

That indirection is the correction of the v1 design, which listed fields in prose here. A
hand-maintained list is how a degradation counter reached the summary that no gate ever read.

`missing_required` is computed **at read time**, not stored, so an entry written before a
register row existed is judged by today's register: "is this turn quotable" is a question about
the current declaration, not about the day it was written.

### 3. Two producers, one per topology, both after `stamp`

The record is assembled by `serve/nodes/stamp.py`. Appending it happens at exactly two sites,
because there are exactly two served topologies:

| Topology | Where the append happens |
|---|---|
| The graph `langgraph.json` runs (`accept` in front, streamed) | the optional `record` node `serve/graph.build_graph` mounts after `stamp`, supplied by `api/graph_app.record_node` |
| The REST `/chat` fallback (no `accept`, whole `ServeState` in and out) | `api/routes._logged` |

Both are downstream of the terminal funnel rather than of a success path, which is the part of
the v1 design that mattered and survived: a node exception writes `failure: NodeFailure` and
routes to `stamp`, which stamps `Outcome.crashed` with the failing stage. Refusals, caps,
crashes and clarifications are logged on the same edge as answers. Turns paused for
clarification carry no `turn_id` and are skipped until they resume.

There is **no `Sink` port and no `record/` package**, and the turn log is passed in rather than
imported, so a test can watch what a served turn writes without redirecting the repository's own
`runs/serve`.

### 4. Write-only on the live path

Nothing reads the turn log back to influence the current turn. It is a historical sink.

The readers are `list_turns` and `get_turn`, projected onto `/audit/turns` and
`/audit/turns/{id}/trace` by `api/routes.turns_page` / `trace_for` (ADR 0009 owns those shapes).
`get_turn` is a linear scan, newest first, with no index: over one developer's log volume an
index would be a second source of truth for a millisecond lookup.

This is the capture-first posture: a log the live path could read is one edit away from
auto-learning from its own output.

### 5. Conversation state is not durable, and the durable checkpointer is withdrawn

Three compile sites, none of them durable:

- **`serve/graph.compile_graph`** — `InMemorySaver` by default; `checkpointer=False` is the
  explicit no-saver option, which also means no `ask_user`, since a saver-less graph cannot
  interrupt. The eval harness calls `delete_thread` per question to contain its growth.
- **`api/routes._graph`** — the REST `/chat` fallback's process-wide `InMemorySaver`, compiled
  once so `thread_id` stays meaningful across turns of a live process.
- **`api/graph_app.make_graph`** — the server entry, compiled with **no** checkpointer. This is
  not an omission: LangGraph Server injects its own, and under `API_VARIANT == "local_dev"` a
  registered `Pregel` carrying a `BaseCheckpointSaver` is a hard startup error. Bringing one
  would not degrade `langgraph dev`; it would refuse to start it.

`/capabilities` reports `checkpoint_durable: false` and `hitl_survives_process_restart: false`.
`langgraph-checkpoint-sqlite` and `-postgres` are deliberately not dependencies; `pyproject.toml`
carries the reasoning.

**So conversation state does not survive a restart, and the turn log is the only place every
turn of a conversation survives.** That is the inversion of the original decision, which named
the checkpointer as the conversation store and the append as a secondary audit copy. Withdrawn
rather than deferred: a durable saver would store the newest turn per thread, which is the one
thing the log already holds, and the two would then be two answers to "what was said".

### 6. Unredacted, and local-first by default

Records are written verbatim: the question, the answer, and `executed_sql`. There is no
redaction column, no content tier, no TTL and no `log_full_content` knob — the v1 design
declared all four and enforced none, and `register/record.py` records why the column was
removed: this is a local-first single-user tool and the log is the user's own transcript. A
redaction vocabulary needs a threat model first; a declaration with no enforcer reads as
behaviour.

The log is on by default and needs no keys. **`runs/` is gitignored, so it is not a backup** —
if a turn matters, it needs a second home.

## Consequences

**Positive**

- Every turn of every conversation survives in one vendor-independent, greppable file, keyed by
  `turn_id` / `run_id` / `thread_id`, with the governance ledger and token counts attached.
- The audit surface is a projection of that file rather than a second store, so a transcript
  rebuilt after the fact shows the same governance badge the live turn showed.
- Refusals, caps and crashes are logged on the same edge as answers, which is what makes the
  outcome distribution readable at all.

**Negative / costs**

- **Conversation state is lost on restart.** Threads resume within a process and not across one.
  A user who reloads mid-conversation on the REST path gets a fresh thread.
- **The log is a sensitive artifact.** Verbatim questions, SQL and answers in plaintext under
  `runs/serve/`, protected by nothing but the filesystem. Acceptable for a single-operator local
  tool and not for anything else; a deployment that changes that premise has to build the tier
  system this ADR withdrew.
- **A linear scan is the read path.** Fine at one developer's volume, and the first thing to
  break under a real one.
- **Two write sites** (`record_node`, `_logged`) can drift. They are held together by both
  taking the record `stamp` produced and by `tests/api/test_audit_surface.py`, not by a shared
  helper.

## Alternatives considered

- **A durable checkpointer as the conversation store** (`SqliteSaver` in dev, `PostgresSaver` in
  prod). Withdrawn — §5. It would need a new dependency, a DSN config field, and would still
  hold only the newest turn per thread.
- **A normalized analytics SQLite.** Rejected: over-built for "keep history to reference", and a
  schema is a migration surface. JSONL is append-only, greppable, and trivially upgradable to
  tables later without touching the two write sites.
- **Cloud tracers only (LangSmith).** Rejected: vendor-locked, a silent no-op without keys, and
  not a backend-owned frontend-agnostic record.
- **Making the log a live-path input** (read past turns to steer the run). Rejected by the owner
  — §4.
- **A second and third producer (curator, SME) on Deep Agents.** Not in scope and not possible as
  written: Deep Agents is not used in this project (`pyproject.toml` carries the reasoning) and
  there is no curator module in `src/` — the corpus is authored out of tree in `../BIRD-corpus`.

## What this ADR does not cover

- **Which fields a record carries** — ADR 0005 §4 and `register/record.py`.
- **The HTTP shape of `/audit/turns` and `/audit/turns/{id}/trace`** — ADR 0007 and ADR 0009.
- **Cost in currency.** There is none. `measure/price.py` is deleted and no price table replaced
  it; the record carries tokens and latency, and USD is whatever the provider bills.
- **Eval's own artifacts.** The eval driver writes `runs/eval/`, not `runs/serve/`, on its own
  row schema. `docs/measurement.md` is that story.
