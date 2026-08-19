# ADR 0007 — The HTTP surface and the UI contract

**Status:** Accepted · **Date:** 2026-08-03 · **Supersedes the runtime half of** ADR 0001

## Context

The frontend (`ui/`, then its own repository at `main` @ `4ada0cc`) is architecturally complete: LangGraph
`useStream` for chat, twelve custom REST routes, an interrupt/clarification flow, a
provenance drawer and a step timeline. It was built against v1.

v2 has **no HTTP surface at all.** `langgraph.json` was deleted in `a506436` ("First code on
the empty floor"), and `src/governed_bi/api/` in `2347ae3` ("Delete v1"). `pyproject.toml`
still declares `fastapi`, `uvicorn`, `langgraph-cli[inmem]` and pins `langgraph-api` for
`/threads` and `/runs/stream`; `tools/check_imports.py` still declares an `api` layer. So the
dependency surface survived the deletion and the code did not.

`docs/openapi.json` is still tracked and is, at the time of this decision, v1's spec for all
twelve routes. It is the spec-of-record for the rebuild, not a historical artifact. (It has
since been rewritten: it carries `"version": "2"` and the fifteen paths the app actually
mounts, `AnswerResponse` included.)

## The decisions

### 1. LangGraph Server, restored with a factory that closes over a `Session`

`langgraph.json` returns with `graphs.serve` (which is where the UI's default
`ASSISTANT_ID = "serve"` comes from) and `http.app` for the custom routes.

The graph **cannot** be loaded as a bare compiled object. Every node needs live objects on
`config["configurable"]` — `policy` (a `GovernancePolicy` dataclass, subscripted unguarded in
`serve/nodes/guard.py::guard_node`), `agent_model`, `corpus`, `index`, `structure`,
`connector`, `assets_by_id` — and LangGraph Server can only put **JSON** in `configurable`.
`serve/state.py` records the same constraint for `policy` at its `ServeState` declaration: the
policy rides `configurable["policy"]` because it is not msgpack-safe.

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

> **Enforced 2026-08-06, and this section was the only place that had it right.** The decision
> above is correct and was correct when written; the audit (§4.5) found that everywhere *else*
> went on asserting the opposite. `safety_clearance` and `semantic_assurance` were in the
> README's opening paragraph, `architecture.md`, `glossary.md`, `design-decisions.md`,
> `openapi.json`'s required-field list, and two superseded ADRs — ten files, and **zero source
> files**. So a reader who started at the README learned the turn carries a two-axis stamp, and
> only a reader who reached this ADR learned it does not.
>
> The one test that named the fields was a strict-xfail stub in the file whose header states
> this rule. `tests/api/test_http_contract.py::test_the_api_never_synthesizes_a_reliability_field`
> is written now, greps all of `src/` rather than just `api/` — the false claim was about the
> *turn*, not the boundary — and also asserts neither name is in the record register. Bringing
> the badge back is a deliberate edit to that test.
>
> **And it now asserts that it scanned (2026-08-12).** As written it walked `src/`, collected hits
> and asserted there were none, with nothing checking the walk had reached anything: repointing
> its root at a path that does not exist scanned zero files and passed green — audit finding D13
> reintroduced in one of this ADR's two acceptance criteria. It now fails on an empty scan and on
> a scan far below the engine's size.
>
> **`tier` is not banned by name, because it is also a real field property.** `RecordField.tier`
> off `RECORD_REGISTER` says *why a field is recorded* (`identity` | `treatment` | `decision` |
> `outcome` | `cost` | `health`) and `/audit/turns/{id}/trace` serialises it per register row;
> `ui/lib/schemas.ts`'s `auditTraceFieldSchema` documents the distinction from the client side.
> What §3 forbids is the *reliability* tier v1's `AnswerView` required on the answer card. The
> test pins the register's single production site by file and text and fails on any second
> producer of a `"tier"` key under `src/`, so bringing the badge back is still a deliberate edit.
>
> `openapi.json`'s `AnswerResponse` was v1's `AnswerView` verbatim, eight required fields the
> engine produces none of, and is replaced with the shape this section specifies.

Note this was a hard failure at the time, not a soft one, and it failed in the worst
direction: `parseAnswer` `safeParse`s and **returns null on mismatch**, so a run completed, no
answer card rendered, and no error appeared. The parse is still that shape — `parseAnswer` in
`ui/lib/stream-messages.ts` — but `answerViewSchema` is now v2's record, so the two sides
agree.

### 4. `answer.text` is system copy; the model's answer lives in `messages`

`_path_signals` returns `text=None` on the answered path, so `answer.text` is non-null only
for refusals. That asymmetry looked like a bug and is not one, so it is now stated: **`text`
is what the *system* says** (refusal and decline copy), and the **model's** answer is the
last `AIMessage` in `messages`.

Duplicating the model's text into `answer.text` would create two fields that must agree, and
`useStream`'s `messagesKey: "messages"` already renders it. One source each.

> **Amendment 2 (2026-08-05): the answer also rides `answer.answer_text`, written by a
> `narrate` stage.** The half of this decision that held is `text` — it is still system copy and
> still null on the answered path. The half that did not is *"`messagesKey` already renders it"*.
> It does not. `mapStreamToChatMessages` turns the last AI frame into an answer **card** once a
> channel answer exists and drops its text as chatter, so the model's sentence reached the
> client and was never displayed. Measured on a live turn: the agent wrote *"There are **9,590
> restaurants** in total."*, the ledger showed one passing attempt, and the interface rendered
> SQL, a ledger and a provenance drawer **with no answer on it**.
>
> It had already been fixed once, in the wrong place. `routes._shape` set `answer_text` from
> `last_ai_text` at the REST boundary — so `POST /chat` had an answer and the streamed path,
> which is the one the UI uses, did not. A boundary patch that fixes one of two transports is
> how a defect hides behind a route that passes.
>
> So the field is produced by a **node**, after `agent_core`, where every answering path funnels.
> That is a stronger single source than this decision's original one, not a second copy of it:
> the answer is no longer "whatever the loop's last message happened to be" but a declared stage
> with a registered prompt (`prompt_set_hash` covers it) and an observed stage event.
>
> It **usually costs nothing** — the agent narrates for free, so the stage adopts that text and
> calls no model. It generates only when the loop ended on a tool call or on reasoning blocks
> with no prose, which is the case that had nothing to fall back to.
>
> `answer_text` is on the `answer` and **not** in the record, exactly like `result_table` and for
> the same reason: *"There are 9,590 restaurants"* is the result set spelled out, and ADR 0006
> §11 puts result rows in the class the durable projection drops.

**And `messages` is the conversation at *every* namespace, because the client cannot tell them
apart.** `messagesKey` names a key, not a graph level. LangGraph streams a nested graph's whole
state under `values|<node>:<task_id>`, and the SDK applies the values of any namespace it does
not recognise as a subagent — the test is a `tools:` segment, which `agent_core:<task_id>` does
not have — straight onto *root* state (`@langchain/langgraph-sdk`, the `isSubagentNamespace`
branch of the `values` handler in `dist/ui/manager.js`). So
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

> **Closed, 2026-08-18** — forced by retiring `POST /chat/resume`, which was the check's only
> caller, so the gap would have become the *whole* behaviour rather than one transport's.
> The gate moved to `serve/resume.py::authorise_resume`, called by `ask_user` on the instruction
> `interrupt()` returns on: `state` is the paused turn's checkpoint and `config` is the resuming
> run's, so both identities are in one frame. It is not in `api/auth.py` and could not be — an
> auth handler receives `AuthContext` plus the `RunsCreate` payload and no way to read thread
> state, and refusing `command.resume` outright deletes the protocol. `serve/accept.py` supplies
> the other half by storing the transport-authenticated caller as the turn's `identity`, which
> closes the write half of audit A5. No token was improvised: the caller is read from
> `configurable["langgraph_auth_user_id"]`, which `langgraph_api` fills from this repo's own
> `@auth.authenticate` and its request validation refuses to let a client name.
> `tests/serve/test_the_platform_resume_is_identity_bound.py`.

### 7. `/capabilities` reports what is true, and false is a legitimate answer

It is the UI's **first** request; without it the chat panel pins at a skeleton forever. Twelve
fields, and each is an observation: `environment`, `dialect`, `model`, `has_live_model` iff a
model is actually configured, `can_stream` true, **`can_edit` false** with `edit_mode: "none"`
(the curator is out of scope), `can_clarify` iff the `ask_user` tool is bound *and* a model is
configured — re-decided the next day, and [ADR 0009](0009-browsing-and-filtering-api.md) D12 is
what `capabilities_for` computes now: `can_stream and agent_model is not None`, because the flag
is the switch that mounts the prompt and the REST transport has no prompt to mount —
`can_scope` true, `can_search` false, and the two durability flags
`checkpoint_durable` / `hitl_survives_process_restart`, both false because pause/resume does not
survive a process restart on either transport.

> **The two durability flags are now stale, 2026-08-18 ([ADR 0014](0014-one-conversation-store.md)).**
> They are still hardcoded `False` in `capabilities_for`, under a comment that explains them by
> `POST /chat`'s `InMemorySaver` — a route that no longer exists. What does exist is a durable
> checkpointer named by `langgraph.json`, verified by killing the server and reading the thread
> back. So the section's own rule is being broken in the direction it did not anticipate: a flag
> reporting `false` about a capability that is built is as wrong as one reporting `true` about a
> capability that is not, and "false is a legitimate answer" is not a licence to leave a literal
> behind. `hitl_survives_process_restart` additionally needs an *observation* before it flips —
> a paused clarification answered after a restart — and under `langgraph dev` the thread index
> still lives in `.langgraph_ops.pckl` with a ten-second flush, which is a second thing that has
> to hold. Tracked in [`docs/open-work.md`](../open-work.md). `can_clarify`'s expression is
> unchanged and still right; the transport it excluded is simply gone.

Reporting a capability **false** is not a defeat. `can_search` is false and the UI degrades to a
client-side index over what it already has, which is a route we do not have to build to get end
to end. Two flags reporting the *server* rather than the mounted client is the failure this
field set is shaped against — hence `can_clarify` binding to both the tool and the model, and
hence the durability flags saying what the checkpointer actually does.

Required for the chat path: `/capabilities` plus the graph. Ungated and therefore required
for the other pages not to error: `/health`, `/schema/summary`, `/corpus/assets`, `/graph`,
`/knowledge-graph` — all cheap projections of the `Session`'s assets and, now that
`CorpusStructure` exists, the two graph routes are edges plus assets rather than new work.

> **Amendment 1 (2026-08-04): `/health` is deleted; `/audit/corpus` is the ungated corpus
> projection.** The two routes answered one question from one set of session fields — counts by
> type, servable, `n_fatal`, `n_degradations`, the problem strings — and `/audit/corpus` answers
> it better on the only field they shaped differently: it returns `fatal` and `degradations` as
> separate lists, which ADR 0008 D9 requires, where `/health` flattened both into `findings`.
>
> `/health`'s three remaining fields were hardcoded zeros. Two of them (`n_suspect_columns`,
> `n_low_confidence_joins`) are true of an uncurated corpus. The third was not:
> `governance.excluded` is a real per-asset field that `/corpus/assets` reads on every row and
> the browser's "Hide excluded" control filters on, so a single marked asset would have had this
> route report `0` beside a page showing the badge.
>
> Two surfaces over one fact is two things to keep in step; the reason to keep the pair was
> three counters, and one of them was a latent disagreement. `/livez` is unaffected — it is the
> liveness probe, and unlike `/health` it deliberately does not touch the session, so it is the
> one that was always right for that job.

### Amendment 3 (2026-08-13): the transport authenticates nobody

**The original decision said nothing about authentication, and that was the state of the surface
for nine days.** The 2026-08-10 audit named it — A1, "~82 routes with no authentication", and A7,
"`/audit/turns` and `/audit/turns/{id}/trace` return every thread's SQL, full records, and an
absolute log path, unauthenticated". Both were closed on 2026-08-12 by a shared key in
`GOVERNED_BI_API_KEY`, compared in constant time, required on every route but `GET /livez`. That
change was never written down here.

**It is now removed, and A1 and A7 are open again as written.** Deleted: `API_KEY_VAR`,
`API_KEY_HEADER` and `api_key_refusal` from `api/auth.py`; `_require_api_key`, `_OPEN_PATHS` and
`_cors_headers` from `api/routes.py`. `GET /livez` is no longer a special case, because there is
no longer a case to be special about. `@auth.authenticate` remains and allows unconditionally.

**Why.** This is a single-operator engine on `127.0.0.1` under `langgraph dev`, and LangGraph
Studio cannot present a credential on the calls it bootstraps with: measured 2026-08-13, `/info`,
`/assistants/search` and `/assistants/{id}` arrive with no custom header — the server answered
*no credential presented* while the key in Studio's connection dialog was correct. A key that
makes the primary debugging client unusable, on a port that is already loopback-only, was judged
to cost more than it bought. The maintainer chose reachability over transport auth.

**What that costs, stated rather than dropped.** Anything that can open a socket to the port can
drive the engine: post a turn — over the platform's own `/threads` and `/runs`, since 2026-08-18
the only way to serve one — execute governed SQL against the configured database, and read
`/audit/turns`, which returns every thread's SQL, the full turn records, and an absolute path to
the conversation store on disk. That last one got *wider* on 2026-08-18: a `values` frame or a
`get_state` now carries every prior turn's record rather than the newest, because
`ServeState.turns` accumulates ([ADR 0014](0014-one-conversation-store.md), audit B1). The CORS origin list in `langgraph.json` is the only remaining
narrowing, and it narrows browsers only. **The port is the boundary.** A deployment that is not
one operator on loopback needs authentication in front of this engine — see
[`docs/enterprise-fork.md`](../enterprise-fork.md).

**What survives, and why `langgraph.json` keeps `auth.path`.** `api/auth.py` still holds its
`Auth()` instance and both `@auth.on` handlers. `threads.update` and `threads.create_run` refuse a
client-supplied state-writing `command`, which is what keeps audit findings A2, A3 and A4 closed:
thread state carries `licensed`, the bound the layer stack enforces against, and
`corpus_content_hash`, the treatment identity every quotability gate reads. Those denials are
about *what may be written*, not *who is writing*, so removing authentication does not touch them.
`authenticated_principal()` also survives and still returns the one principal the access seam
([ADR 0012](0012-access-seam-principal-and-authorization.md)) is asked about — unchanged in
behaviour, weaker in justification: it was a function of nothing because one shared key could not
distinguish two callers, and it is a function of nothing now because there is nothing to read.

## Consequences

- The engine gains `src/governed_bi/api/`, and `tools/check_imports.py`'s already-declared
  `api` layer finally has something in it.
- The UI's answer card, reliability stamp and step vocabulary change. Its transport,
  clarification flow, provenance drawer and schema pages do not.
- `docs/openapi.json` must be regenerated from the implementation rather than kept by hand;
  a spec no process checks is the defect this repository keeps rediscovering.
- Nothing here makes a run quotable on its own. `facet_degraded` is the gate: it is `True`
  whenever some facet ran on fewer channels than `FACET_CHANNELS` declares
  (`measure/degradation.py::facets_degraded`). The embedder and the extraction model are both
  wired, so a fully configured turn can now clear it, and a `True` names a live failure rather
  than a missing component.
