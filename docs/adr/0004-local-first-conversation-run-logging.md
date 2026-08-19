# 0004: Local-first conversation + run logging

> **Superseded in part (2026-08-18) by [ADR 0014](0014-one-conversation-store.md).** The JSONL
> turn log this ADR decided **no longer exists**: `api/trace_store.py` and `runs/serve/*.jsonl`
> are deleted, and the ~194 historical turns in them were discarded rather than migrated. §5's
> withdrawal of the durable checkpointer is **reversed** — `langgraph.json` mounts
> `serve/checkpointer.py::conversation_checkpointer` (`AsyncSqliteSaver`), and a conversation
> survives a restart. What carries forward is the *record*: one envelope per finished turn
> (`asked_at`, `question`, `answer_text`, `outcome`, `record`), produced on the terminal edge so
> refusals, caps and crashes are recorded beside answers, judged by today's register at read time,
> verbatim and unredacted. What does not carry forward is every claim about *where* it lives, and
> §6's "no TTL". Each affected section carries a dated note below; 0014 is the authority for the
> replacement and for the costs it brings.

- **Status:** Accepted and built (2026-07-22; rewritten against the tree 2026-08-12;
  **superseded in part 2026-08-18** — see the note above).
  The turn log was built and was the conversation history. **The durable checkpointer half
  was never built and was withdrawn** here, not deferred — see §5, and 0014 for why all three
  grounds fell. This page was rewritten
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

> **Superseded, 2026-08-18 (ADR 0014 §1–§2).** There is no file and no `append_turn`.
> `api/graph_app.record_node` returns the envelope onto `ServeState.turns`, an `ACCUMULATING`
> channel deliberately absent from `PER_TURN_RESET`, and the checkpointer persists it.
> `TURN_LOG_DIR` survives only as a property on `api/thread_turns.ThreadTurnLog` — it is the wire
> key `meta.log_dir` that the audit footer renders — and its value is now the conversation
> database, not a directory. The `GOVERNED_BI_TURN_LOG_DIR` override is gone;
> `GOVERNED_BI_CONVERSATION_DB` and `GOVERNED_BI_HARNESS_DB` are what a test or an operator points
> elsewhere (`docs/usage.md`). The five keys below are unchanged: `TurnEntry` is exactly them.

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

> **Superseded, 2026-08-18 (ADR 0014 §2).** There is one producer, because there is one served
> topology: `POST /chat` and `POST /chat/resume` are deleted, so `api/routes._logged` went with
> them. `record_node` is the only site, and it no longer appends anywhere — it returns
> `{"turns": [envelope]}` and the checkpoint is the write. The drift between two write sites that
> this section's last paragraph left to a test is therefore gone by construction. Everything else
> here holds: the node sits after `stamp`, downstream of the terminal funnel and not of a success
> path, and a turn paused for clarification carries no `turn_id` and is skipped until it resumes.

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

> **Amended, 2026-08-18 (ADR 0014 §3).** The posture stands; the reader moved.
> `list_turns` / `get_turn` are on `api/thread_turns.ThreadTurnLog`, which reads thread state
> through the in-process LangGraph client instead of globbing a directory — at the same
> `make_app` seam, returning the same payloads. That seam lost its `graph` parameter with the
> chat pair: nothing this app serves holds a graph any more. `get_turn` is still an
> unindexed scan. Two rules a single time-ordered file never needed are now explicit in that
> module: rows are re-sorted by `asked_at` after collection, because conversations interleave in
> time and per-thread order is not global order; and `limit` counts turns while
> `threads.search`'s counts threads, so it pages until the turn budget is met rather than
> returning the first page.

Nothing reads the turn log back to influence the current turn. It is a historical sink.

The readers are `list_turns` and `get_turn`, projected onto `/audit/turns` and
`/audit/turns/{id}/trace` by `api/routes.turns_page` / `trace_for` (ADR 0009 owns those shapes).
`get_turn` is a linear scan, newest first, with no index: over one developer's log volume an
index would be a second source of truth for a millisecond lookup.

This is the capture-first posture: a log the live path could read is one edit away from
auto-learning from its own output.

### 5. Conversation state is not durable, and the durable checkpointer is withdrawn

> **Reversed, 2026-08-18 by [ADR 0014](0014-one-conversation-store.md).** All three grounds fell,
> and 0014's "Why ADR 0004 §5 could be reversed" records which: the dependency constraint was
> lifted, `ACCUMULATING` answers "only the newest turn per thread", and the third bullet below is
> **factually wrong** — `langgraph.json` has a documented `checkpointer` field, and because the
> `serve` entry is a factory the `local_dev` "custom checkpointer" startup error is never reached.
> What that error guards is `.compile(checkpointer=…)`, which the server overrides *silently*; so
> this page reached the right outcome about the served graph from the wrong mechanism. Live now:
> the server logs *"Using custom checkpointer: AsyncSqliteSaver"*, and
> `serve/graph.compile_durable()` gives the CLI and eval the same durability against a second
> database. `compile_graph()` is unchanged and still defaults to `InMemorySaver`, which is what
> keeps the test suite off a shared file.

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

> **Amended, 2026-08-18 (ADR 0014 §4).** Verbatim and unredacted still hold. "No TTL" does not:
> `langgraph.json` sets `checkpointer.ttl` to `{strategy: "delete", default_ttl: 129600}` — 90
> days, then the thread is deleted. There is no gentler setting available.
> `AsyncSqliteSaver` does not implement `aprune`, so `keep_latest` cannot be chosen and the server
> warns exactly that at startup. A conversation therefore leaves History and Audit together at 90
> days, and the retention this section says the design has none of is now a delete.

The log is on by default and needs no keys. **`runs/` is gitignored, so it is not a backup** —
if a turn matters, it needs a second home.

## Consequences

> **Amended, 2026-08-18 (ADR 0014 §Consequences).** Two of these inverted. "Conversation state is
> lost on restart" is **false** — a thread survives a hard kill, which is what 0014 was verified
> against. "One greppable file" is **gone**: the investigation that established the 194-turn count
> now goes through the SDK. The plaintext-sensitivity bullet transfers intact to
> `runs/conversations.sqlite` — still verbatim, still protected by nothing but the filesystem, and
> reachable over `/audit/turns` and `/threads/*` with no credential (audit A7, open). Two costs
> are new rather than transferred: a turn costs ~3.9 MB of checkpoint, because every super-step
> persists the whole state; and a `values` frame or a `get_state` now returns *every* prior turn's
> record rather than one (audit B1, open). "Two write sites" retires with §3. The linear scan
> survives the change of store.

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
  > **Adopted, 2026-08-18 — ADR 0014.** In dev; a Postgres saver is the deployed runtime's own and
  > not a second decision here. The DSN config field became a filesystem *path*, with
  > `serve/checkpointer.assert_not_a_warehouse` refusing at configuration time anything that looks
  > like a DSN — a checkpointer pointed at the facilities Postgres would write conversation state
  > into real data on the first turn.
- **A normalized analytics SQLite.** Rejected: over-built for "keep history to reference", and a
  schema is a migration surface. JSONL is append-only, greppable, and trivially upgradable to
  tables later without touching the two write sites.
  > **Still rejected, 2026-08-18.** 0014 chose a LangGraph-native primitive over a hand-rolled
  > table, so the SQLite that replaced the log stores the same opaque envelope and has no
  > analytics schema to migrate.
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
- **Eval's own artifacts.** The eval driver writes `runs/eval/` on its own row schema, and
  `docs/measurement.md` is that story. It never wrote a turn record, and under 0014 it gets a
  durable *checkpointer* of its own (`runs/harness-checkpoints.sqlite`) and still no turn record —
  a benchmark is not a conversation. Keeping the two stores apart is the fix for what this log
  never had: 116 of its 194 turns came from a test thread and no field distinguished them.
