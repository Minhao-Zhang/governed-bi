# Serve-time clarification (HITL) — server ↔ frontend API contract

_Status: **AGREED; server side IMPLEMENTED** (2026-07-14). The six §11 decisions
are accepted; the engine honours this contract (see "Server implementation
status" below). The **frontend side is open** — this doc is the source of truth
for it. Owner: engine (server, done) + `governed-bi-ui` (to build).
Companion to [agent-step-visualization.md](agent-step-visualization.md) (the live
governance-event stream this extends) and
[ADR 0002](../adr/0002-governed-agentic-serve-runtime.md) (the agentic serve core;
`ask_user`→`interrupt` is its Phase-3 HITL branch)._

## 1. Scope & principles

- **What:** when the governed agent hits genuine ambiguity mid-turn, it **asks the
  user one question and waits**, instead of guessing or refusing. On the answer,
  it resumes the same turn.
- **Server-only.** HITL lives **only in the deployed serve path** (LangGraph
  Server chat graph → inner agent). The eval / offline / programmatic harness
  **never interrupts** — there is no human there; it behaves exactly as today
  (proceed or fail-closed). The `ask_user` tool is registered on the server serve
  path only.
- **Extends, does not replace, the existing transport.** Clarification rides the
  same `useStream` connection as the answer and the Amendment-2 governance-event
  stream. No new endpoint, no new socket.
- **On-thesis:** the question asked and the answer given become **ledger
  entries** (Inv #10) — clarification is a governed, audited action, not a side
  channel.

## 2. Mechanism (pinned to the shipped stack)

Verified against `langgraph>=1.0` (server) and `@langchain/langgraph-sdk@^1.9.25`
(`useStream`):

- **Server raises:** inside the `ask_user` tool, call `interrupt(request)` where
  `request` is the `ClarificationRequest` (§3). This pauses the graph at the
  server-injected checkpointer and streams the interrupt to the client.
- **Client reads:** `stream.interrupt` becomes non-null; its `.value` **is** the
  `ClarificationRequest`. The hook is typed `useStream<ChatState, ClarificationRequest>`.
- **Client answers:** `stream.respond(response)` where `response` is the
  `ClarificationResponse` (§4). This resumes the run (`Command({ resume: response })`
  under the hood); the tool's `interrupt(...)` call returns `response` server-side
  and the agent continues.
- **One pending interrupt at a time** (§6), so the client uses the un-targeted
  `stream.respond(value)` (newest interrupt). No `interruptId`/`namespace`
  targeting in v1.

## 3. Server → client: `ClarificationRequest` (the `interrupt()` value)

```jsonc
{
  "kind": "clarification",          // discriminator; reserved for future interrupt kinds
  "clarification_id": "clar_ab12",  // stable id for this question (ledger + provenance join key)
  "question": "Which 'active' did you mean — logged in last 30 days, or account status = 'active'?",
  "why": "The corpus has two competing definitions of \"active\" and the question is ambiguous between them.",
  "choices": [                      // OPTIONAL. Present => constrained pick; absent => freeform text.
    { "id": "opt_login30", "label": "Logged in within 30 days" },
    { "id": "opt_status",  "label": "Account status = 'active'" }
  ],
  "allow_freeform": true,           // when choices present: may the user also type a freeform answer?
  "tier": "audit"                   // provenance tier of the question (D12 clarification protocol)
}
```

- `question` and `why` are always present (governance transparency: the user sees
  *why* they're being asked).
- `choices` absent ⇒ freeform-only. `choices` present ⇒ render the options;
  `allow_freeform` decides whether a text box is also offered.
- `clarification_id` is the join key across the interrupt, the resume, the
  timeline event (§5), and the final provenance (§7).

## 4. Client → server: `ClarificationResponse` (the `respond()` value)

```jsonc
// Answered (freeform):
{ "clarification_id": "clar_ab12", "answer": "logged in last 30 days" }

// Answered (chose an option):
{ "clarification_id": "clar_ab12", "choice_id": "opt_login30" }

// Declined / cancelled:
{ "clarification_id": "clar_ab12", "declined": true }
```

- Exactly one of `answer` / `choice_id` / `declined:true` is set.
- **Decline semantics (D3):** the agent does **not** guess. It fails closed —
  returns a refusal (or a best-effort answer stamped with lowered
  `semantic_assurance`, per the §6 grade policy). The safe default in v1 is
  **refuse** with reason `clarification_declined`.
- The server validates the `clarification_id` matches the pending question;
  mismatch ⇒ ignore + re-emit the interrupt (defensive; should not happen with a
  single pending interrupt).

## 5. Integration with the governance-event stream (Amendment 2)

The clarification is a **governed tool call**, so it appears in the timeline
([agent-step-visualization.md](agent-step-visualization.md)) as a `tool` event,
keeping "the live step view IS the ledger" invariant:

| kind | step | status | detail |
|---|---|---|---|
| `tool` | `ask_user` | `start` | `{ clarification_id, question }` — timeline shows "Asking a question…" |
| `tool` | `ask_user` | `ok` | `{ clarification_id, answered_by }` — resolves when the user answers |
| `tool` | `ask_user` | `declined` | `{ clarification_id }` — user cancelled ⇒ turn fails closed |

So the UI has **two coordinated surfaces** on the same event: the **timeline row**
(passive, "asking…") and the **interrupt prompt** (active, the actual question
from `stream.interrupt.value`). They share `clarification_id`.

## 6. Lifecycle & edge cases

- **Sequential only (v1):** the agent asks at most one question at a time; a turn
  may ask several in sequence (interrupt → answer → resume → maybe interrupt
  again). No parallel/batched questions.
- **Persistence:** the interrupted turn lives in the thread checkpoint. If the
  user closes the tab, the thread stays paused; re-opening the thread re-surfaces
  `stream.interrupt`. (Depends on checkpointer durability — see §8.)
- **Cancel / decline:** §4 decline path ⇒ fail-closed refusal.
- **Invalid/empty answer:** empty freeform on a freeform question ⇒ client
  disables submit; server treats empty as decline if it arrives.
- **No server timeout** in v1: the server does not abandon a pending
  clarification; the thread waits.

## 7. Final-answer provenance

The final `Answer` (and its `answer_view`) gains a `clarifications` list so the
audit shows what was asked and answered:

```jsonc
"provenance": {
  "clarifications": [
    { "clarification_id": "clar_ab12", "question": "…", "answer": "logged in last 30 days", "answered_by": "user" }
  ]
}
```

`answered_by:"user"` distinguishes a served HITL answer from the curator's
Simulated-SME answers, so the two never get conflated in the ledger.

## 8. Capabilities & feature-gating

- `/capabilities` gains **`can_clarify: boolean`**. The UI only mounts the
  interrupt prompt UI when `can_clarify` is true, so a server built without HITL
  (or the REST/offline profile) degrades cleanly.
- `can_clarify` is true only on the streaming serve path (it needs the
  interrupt-capable transport).

## 9. TypeScript types (frontend, mirrors `lib/steps.ts` style)

```ts
export interface ClarificationChoice { id: string; label: string }

export interface ClarificationRequest {
  kind: "clarification";
  clarification_id: string;
  question: string;
  why: string;
  choices?: ClarificationChoice[];
  allow_freeform?: boolean;
  tier: "audit";
}

export type ClarificationResponse =
  | { clarification_id: string; answer: string }
  | { clarification_id: string; choice_id: string }
  | { clarification_id: string; declined: true };

// Hook: const stream = useStream<ChatStreamState, ClarificationRequest>(…)
// Render when stream.interrupt != null; resolve via stream.respond(response).
```

## 10. Open questions — SERVER-SIDE ONLY (not part of this frontend contract)

These do **not** change the wire contract above; they are the engine's to
resolve, listed so the frontend team knows they exist:

- **Re-execution on resume.** The whole pipeline runs inside the single `answer`
  node compiled without a checkpointer (the server injects one). On resume the
  node re-runs; the deterministic prefix (route/retrieve/assemble) re-executes and
  the inner agent replays completed steps from the checkpoint. Need to confirm the
  inner `create_agent` subgraph checkpoints correctly through the nested
  `answer_question_agent` call, and whether the prefix re-run is acceptable or the
  pipeline should be lifted into graph nodes.
- **Checkpointer durability.** `langgraph dev` injects an in-memory saver, so a
  paused turn dies on server restart. Durable HITL needs the Postgres checkpointer
  (the ADR's deferred item). v1 can ship on the in-memory saver.
- **`ask_user` vs `recursion_limit`.** An interrupt pauses rather than consuming a
  super-step, but the resumed tool round-trip does; confirm the cap accounting.
- **Trigger policy.** Does the *agent* decide to call `ask_user` (freeform, prompt-
  driven), or is there a deterministic ambiguity gate? Draft assumes **agent-
  driven tool** (it decides mid-reasoning); a deterministic gate is the alternative.

## 11. Decisions (agreed 2026-07-14)

1. **Payload shape** (§3/§4): freeform + optional constrained `choices`, with
   `clarification_id` as the join key. ✅
2. **Decline = refuse** (§4, D3): a declined clarification fails closed. ✅
3. **Clarification is a ledger `tool` event** (§5) + lands in provenance (§7),
   `answered_by:"user"`. ✅
4. **`can_clarify` capability flag** (§8) gates the UI. ✅
5. **Sequential, one-at-a-time** (§6), no batching in v1. ✅
6. **Agent-driven `ask_user` tool** (§10 trigger policy), not a deterministic
   ambiguity gate. ✅

## 12. Server implementation status (2026-07-14)

The engine side is built and tested offline; the frontend is the remaining work.

- **`ask_user` tool** — `server/tools.py` (added only when `enable_clarify`), calls
  `interrupt(clarification_request(...))`. Payload builder + response parser:
  `server/clarify.py` (the §3/§4 shapes; `clarification_id` is deterministic in the
  question so a re-run re-derives it — no clock/RNG).
- **Interrupt/resume plumbing** — the inner `create_agent` runs on a per-turn
  in-memory checkpointer (`ServeStack.clarify_checkpointer`); the chat-graph
  `answer` node (`api/graph_app.py`) detects the pause and calls `interrupt(request)`
  so the **outer** graph pauses — i.e. `stream.interrupt.value` == the
  `ClarificationRequest`. `Command(resume=response)` round-trips back into the
  inner agent. Verified by a spike + `tests/test_serve_clarify.py` (interrupt
  surfaces, resume → governed answer + provenance, decline → refuse, and
  disabled-parity).
- **Capability** — `/capabilities` returns `can_clarify` (true only with a live
  model on the streaming path); `openapi.json` regenerated.
- **v1 limits (server-side, don't affect the wire contract):** the clarify
  checkpointer is **in-memory / per-process**, so a paused turn does not survive a
  server restart (durable Postgres checkpointer is the deferred follow-up, §10);
  a declined turn leaves the inner thread paused in memory until GC/thread reuse.
  Single- and sequential multi-clarification per turn are both tested
  (`tests/test_serve_clarify.py`); parallel/batched clarification is out of scope
  (§6).

### LangGraph HITL best-practice compliance

Audited against the `langgraph-human-in-the-loop` guidance:

- **Checkpointer + thread id + JSON-serializable payload** — inner agent runs on
  the stack's `InMemorySaver` with a per-turn `thread_id` (`{outer}:{human-turn}`,
  stable across the resume re-runs); the payload is a plain dict.
- **"The node re-runs from the start on resume; pre-`interrupt` code must be
  idempotent."** Verified: the rails prefix (route/retrieve/assemble/cache-lookup)
  is pure reads; `_working_memory_from` rebuilds a fresh object from history (not a
  persistent append); the `ask_user` tool does only a pure `clarification_request`
  before `interrupt`. The inner agent's **data touches** (`run_query`/`sample_rows`)
  are **replayed from the inner checkpointer, not re-executed** — asserted by a test
  that a `run_query` appears exactly once in the ledger after a resume. `_finalize`
  (cache write, narrate) runs once, only after the agent completes.
- **Resume via `Command(resume=…)`** (never `Command(update=…)` as input); the
  frontend's `stream.respond()` maps to this.
- **Benign re-emission:** on resume the deterministic prefix re-emits its `rail`
  events, but the frontend reducer keys rows by `id`/`step:seq` and the prefix is
  deterministic, so they fold into the same rows (no duplicate timeline rows). This
  relies on that determinism — noted for maintainers.

**Frontend TODO** (against §2/§3/§4/§9): type `useStream<ChatState, ClarificationRequest>`;
render a prompt when `stream.interrupt != null` (question + why + choices/freeform);
resume via `stream.respond(response)`; gate the UI on `capabilities.can_clarify`;
show the `ask_user` timeline row (§5) alongside the active prompt.
