# ADR 0007 — The HTTP surface and the UI contract

**Status:** Accepted · **Date:** 2026-08-03 · **Supersedes the runtime half of** ADR 0001

## Context

The frontend (`../governed-bi-ui`, `main` @ `4ada0cc`) is architecturally complete: LangGraph
`useStream` for chat, twelve custom REST routes, an interrupt/clarification flow, a
provenance drawer and a step timeline. It was built against v1.

v2 has **no HTTP surface at all.** `langgraph.json` was deleted in `a506436` ("First code on
the empty floor"), and `src/governed_bi/api/` in `2347ae3` ("Delete v1"). `pyproject.toml`
still declares `fastapi`, `uvicorn`, `langgraph-cli[inmem]` and pins `langgraph-api` for
`/threads` and `/runs/stream`; `tools/check_imports.py` still declares an `api` layer. So the
dependency surface survived the deletion and the code did not.

`docs/openapi.json` is still tracked and is v1's spec for all twelve routes. It is the
spec-of-record for the rebuild, not a historical artifact.

## The decisions

### 1. LangGraph Server, restored with a factory that closes over a `Session`

`langgraph.json` returns with `graphs.serve` (which is where the UI's default
`ASSISTANT_ID = "serve"` comes from) and `http.app` for the custom routes.

The graph **cannot** be loaded as a bare compiled object. Every node needs live objects on
`config["configurable"]` — `policy` (a `GovernancePolicy` dataclass, subscripted unguarded at
`guard.py:21`), `agent_model`, `corpus`, `index`, `structure`, `connector`, `assets_by_id` —
and LangGraph Server can only put **JSON** in `configurable`. `state.py` already records the
same constraint for `policy`: *"the checkpointer cannot msgpack the dataclass."*

So `make_graph` builds a `Session` at server start and closes over it. This is the reason
§2.8.2.2's session seam had to exist before the server could: the server is simply its
second caller, after `python -m governed_bi.serve`.

### 2. The client sends a message. The turn's provenance is minted server-side.

The UI submits exactly one key: `{messages: [{type: "human", content}]}`. The record requires
fifteen fields.

**A client must not be able to set any of them.** `run_id`, `turn_id`, `corpus_content_hash`,
`prompt_set_hash`, `knobs_resolved` are the run's own claims about itself — a
client-settable provenance field is a **forgeable record**, and every quotability gate reads
these. This is the same principle as ADR 0006's *no tool writes to `licensed`*.

So an entry node derives the turn from the last human message via `Session.turn(question)`,
and anything a client sends in those fields is **ignored, not merged**.

### 3. `tier`, `safety_clearance` and `semantic_assurance` are not synthesized. The UI drops them.

The UI's `AnswerView` requires `{tier, safety_clearance, semantic_assurance, text, sql,
escalation, provenance, result}`. **None of the first three exists anywhere in v2** — the
reliability-tier concept was deliberately not carried across the rewrite.

The tempting move is to project `outcome` onto `tier: "governed"` in the server. That is
exactly the defect this rewrite exists to remove: a field reporting the *configuration*
rather than an *observation*, so a broken turn and a clean turn produce identical artifacts.
A synthesized `tier` would be a reliability claim with nothing behind it, on the most
prominent badge in the interface.

**So the UI's contract changes to v2's record**, and `answer` keeps its shape:
`{outcome, text, failed_stage, error_type, refused_by, record}`. `outcome` and the ledger are
observations. If a reliability tier is wanted later it must be *earned* by a measurement, in
its own ADR.

Note this is a hard failure today, not a soft one, and it fails in the worst direction:
`parseAnswer` `safeParse`s and **returns null on mismatch**, so a run completes, no answer
card renders, and no error appears.

### 4. `answer.text` is system copy; the model's answer lives in `messages`

`_path_signals` returns `text=None` on the answered path, so `answer.text` is non-null only
for refusals. That asymmetry looked like a bug and is not one, so it is now stated: **`text`
is what the *system* says** (refusal and decline copy), and the **model's** answer is the
last `AIMessage` in `messages`.

Duplicating the model's text into `answer.text` would create two fields that must agree, and
`useStream`'s `messagesKey: "messages"` already renders it. One source each.

**And `messages` is the conversation at *every* namespace, because the client cannot tell them
apart.** `messagesKey` names a key, not a graph level. LangGraph streams a nested graph's whole
state under `values|<node>:<task_id>`, and the SDK applies the values of any namespace it does
not recognise as a subagent — the test is a `tools:` segment, which `agent_core:<task_id>` does
not have — straight onto *root* state (`@langchain/langgraph-sdk` `dist/ui/manager.js:413`). So
mid-run, `stream.messages` **is the nested agent's message list**, rendered as the root
transcript. `agent_core` put the delivered context block in that list as a `HumanMessage`, and
8.6 KB of scaffolding appeared as the user's own chat bubble for the duration of every turn,
vanishing only when the next root frame clobbered it back. Nothing in the outer state or the
persisted conversation was ever wrong, which is why it survived: `fresh =
out_messages[len(inbound):]` kept the block out of the outer channel and the leak was entirely
on the wire.

The rule this yields is stronger than "do not put scaffolding in the root `messages`":
**nothing may put non-conversation content in a `messages` channel anywhere in the graph.**
Per-turn material a model needs but a reader must not see is injected at model-call time
instead — `agent_core._context_middleware` does this with `wrap_model_call`. Two engine
invariants forbid the obvious alternatives: it cannot ride on `system_prompt`, because
`prompt_set_hash` is a `Role.comparability` field digesting the prompt registry and a per-turn
suffix would make the published hash describe something never sent; and it cannot be solved by
tagging the message for the client to filter, because `stream_subgraphs` is load-bearing (ADR
0010 M2) and a client-side filter would silently mask a re-regression of the engine rule.
`tests/serve/test_model_inputs.py` pins both halves — the block reaches the model, and no
streamed frame at any namespace carries it inside `messages`.

The same clobber has a second consequence that is latent rather than visible: mid-run
`stream.values` is the *nested* state, so `answer`, `delivery` and the rest are absent until the
run settles. The UI only reads them when `idle`, so nothing renders wrong today — but reading
`stream.values` mid-run is a trap.

### 5. Stream events: v2's stage vocabulary in the UI's envelope, emitted from one place

> **Superseded by [ADR 0010](0010-live-stage-events.md), which built this.** The decisions below
> are the ones 0010 implements — the envelope, `stages.py` as the authority, observed status —
> and they held. What did *not* hold is three factual claims about the transport, each measured
> wrong: the wire flag that makes any of it arrive is `stream_subgraphs` and not `subgraphs`;
> without it a correct emitter yields an empty timeline *and* no streamed tokens, because the
> model and every tool run inside `agent_core`'s nested graph; and "emit from `wrap.py`, not from
> the nodes" is right for the rails but cannot reach the tools or `stamp`, so the emitter
> boundary is three places, not one. Read 0010 for the contract.

The UI's `GovEvent` envelope is good — `{seq, id, kind, step, status, label?, detail?}`,
validated on `typeof kind === "string" && typeof seq === "number"` and dropped silently
otherwise. Keep it. **Nothing in v2 emits any custom event**: no `get_stream_writer`, no
`stream_mode="custom"`, anywhere. So the timeline, the progress rail and the step rows —
about 900 lines of UI — currently render nothing.

Two decisions about how to fix that:

**Emit from `serve/wrap.py`, not from the nodes.** Every node is already wrapped, so one
emitter covers every stage, derives its `step` name from the `Stage` register, and cannot
drift per node. Twenty hand-placed `writer(...)` calls is twenty places to forget one, and
the missing-call failure mode is a step that silently never appears.

**The `status` must be observed.** `start` on entry, then the outcome the node actually
produced — never a declared one. This is the same rule that `_channels_for` broke and the
same reason it was a defect: a status computed from configuration makes a broken run and a
clean run look identical.

**The step vocabulary is v2's, and the UI adopts it.** Only five names overlap (`route`,
`assemble`, `inspect_schema`, `sample_rows`, `ask_user`). The UI's `refuse_gate`, `cache`,
`schema_route`, `finalize`, `search_corpus`, `read_notes`, `grep_notes` are v1 stages, some of
which name concepts v2 does not have. `register/stages.py` is the authority.

### 6. The interrupt payload gains an id and a reason

v2 sends `interrupt({"type": "ask_user", "question": ...})`. The UI requires
`kind: "clarification"` (a `z.literal`), `clarification_id`, `question` and `why`. On a
mismatch it drops the interrupt, so `<ClarificationPrompt/>` never mounts and **the turn
deadlocks**: the graph waits, `isLoading` is false, and the interface looks idle with no way
to answer.

The UI's shape is the better one — an id makes the answer attributable to the question, and
`why` is a real thing to record about a clarification. **The engine changes.**

`resume.py`'s `resume_authorised` identity check is not reachable through
`stream.submit(null, {command: {resume}})`, which goes straight to `Command(resume=…)`. That
is a real gap and it is **out of scope here**: it is a security question about a
single-operator local tool, and it gets its own decision rather than an improvised token.

### 7. `/capabilities` reports what is true, and false is a legitimate answer

It is the UI's **first** request; without it the chat panel pins at a skeleton forever. Nine
fields, and each is an observation: `has_live_model` iff a model is actually configured,
`can_stream` true, **`can_edit` false** (the curator is out of scope), `can_clarify` iff the
`ask_user` tool is bound.

Reporting `can_scope` and `can_search` **false** is not a defeat — the UI degrades to the
flat `/schema` dump and a client-side index, which is four routes we do not have to build to
get end to end. That is the cheapest honest path.

Required for the chat path: `/capabilities` plus the graph. Ungated and therefore required
for the other pages not to error: `/health`, `/schema`, `/corpus/assets`, `/graph`,
`/knowledge-graph` — all cheap projections of the `Session`'s assets and, now that
`CorpusStructure` exists, the two graph routes are edges plus assets rather than new work.

## Consequences

- The engine gains `src/governed_bi/api/`, and `tools/check_imports.py`'s already-declared
  `api` layer finally has something in it.
- The UI's answer card, reliability stamp and step vocabulary change. Its transport,
  clarification flow, provenance drawer and schema pages do not.
- `docs/openapi.json` must be regenerated from the implementation rather than kept by hand;
  a spec no process checks is the defect this repository keeps rediscovering.
- Nothing here makes a run quotable. `facet_degraded` is `True` on every turn until the
  embedder and an extraction model land, and that gate is doing its job.
