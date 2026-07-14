# Frontend spec: visualizing the agent's steps (governed serve stream)

_Implementation spec for the UI change. Backend: this repo (`server/agent.py`,
`server/middleware.py`, `server/tools.py`, `api/graph_app.py`). Frontend: the
sibling `governed-bi-ui` repo (Next.js + LangChain `useStream`). Companion to
[ui-frontend-handoff.md](../ui-frontend-handoff.md); relates to
[ADR 0002](../adr/0002-governed-agentic-serve-runtime.md) and
[ADR 0001](../adr/0001-langgraph-server-chat-runtime.md)._

## Problem

The chat stepper renders the deterministic flow's fixed stages (Routing →
Selecting schema → Retrieving → Generating SQL → Checking guardrails → Executing
→ Composing). The [ADR 0002](../adr/0002-governed-agentic-serve-runtime.md) agent
path does not produce those stages. Its real work happens inside one
`agent.invoke()`: a variable loop of `search_corpus` / `inspect_schema` /
`sample_rows` / `run_query` (with repair attempts). During that loop the stepper
streams nothing, so a live run looks frozen for many seconds (observed on a
restaurant question that repaired once before answering). The fixed rail also
cannot represent a loop whose length is unknown up front.

## Goal

A live, auditable step view for the agent path that:

1. Streams each governed action as it happens (no dead wait).
2. Represents a dynamic loop (N inspects, M query attempts), not a fixed list.
3. Surfaces the governance detail the product is about: per-attempt guardrail
   verdicts, which tables were licensed, repair attempts. The Chat header already
   says "audit the governed answer"; this is that audit, live.
4. Degrades cleanly to the deterministic flow path (which still emits the fixed
   stages).

## Principle: the live step view IS the governance ledger

Every governed action already produces a **governance ledger** entry in
`GovernanceMiddleware` (ADR 0002 Inv #10): `{action, verdict, sql, allowed,
licensed_ids, layer, reason, result, attempt}`. The stream events below are the
same records, emitted live instead of only at the end. So the streaming view and
the final `Provenance` audit are one data source, not two. Build the timeline
rows from the event stream; on completion they equal the ledger on
`answer.provenance.governance_ledger`.

Agent internal chat messages (the tool-call `AIMessage`s / `ToolMessage`s) stay
node-local and must NOT enter the user transcript (ADR 0001 / gotcha G2). The
step view is driven by **custom progress events**, not by the message stream.

## The event contract (backend ↔ frontend)

The existing channel already works: `api/graph_app.py` passes
`writer = get_stream_writer()` as `on_event`, and `_emit(on_event, {...})` pushes
custom events that `useStream` receives on `stream_mode="custom"`. This spec
**extends the event payload**; it does not invent a new transport.

Each event is one JSON object:

```jsonc
{
  "seq": 3,                       // monotonic per turn; frontend orders by this
  "kind": "rail" | "tool" | "final",
  "step": "route|refuse_gate|cache|assemble|finalize"   // when kind=rail/final
        | "search_corpus|inspect_schema|sample_rows|run_query", // when kind=tool
  "status": "start|ok|blocked|error|refused|cap|hit|miss",
  "label": "Inspected allgemeine_informationen",         // short human string
  "detail": { }                    // step-specific, see table
}
```

The first event of a turn carries `serve_path: "flow" | "agent"` so the frontend
picks the renderer.

### Events and their `detail`

| kind | step | status | detail fields |
|---|---|---|---|
| rail | `route` | ok | `intent` |
| rail | `refuse_gate` | ok / refused | `negative_example?` (id when refused) |
| rail | `cache` | hit / miss | (hit ends the turn) |
| rail | `assemble` | ok | `schema?`, `tables` (count), `few_shots` (count) |
| tool | `search_corpus` | start / ok | `query`, `tables`, `few_shots`, `metrics` (counts) |
| tool | `inspect_schema` | start / ok | `table_id`, `columns` (count), `licensed: true` |
| tool | `sample_rows` | start / ok / blocked | `table_id`, `rows` |
| tool | `run_query` | start / ok / blocked / error / cap | `attempt`, `sql`, `verdict`, `layer?`, `reason?`, `rows?`, `allowed?` |
| final | `finalize` | ok / refused | `tier`, `semantic_assurance`, `safety_clearance`, `tables_used`, `min_join_confidence`, `coverage_best_effort` |

`run_query` is the important one: `attempt` + `verdict` (+ `layer`/`reason` on a
block) is what makes a repair loop legible ("attempt 1 blocked by
term_semantics, attempt 2 ran").

## Backend work (this repo) — DONE (2026-07-14)

Implemented on `feat/governed-agentic-serve-runtime`. The frontend engineer can
build against the live contract (or the recorded trace the tests capture); these
are the emit points that produce it.

1. **Emitter** (`server/governance.py::GovEventStream`): a per-turn object that
   wraps the raw `on_event` callback, stamps the monotonic `seq`, tags the first
   event with `serve_path:"agent"`, and exposes `rail()` / `tool()` / `final()`.
   Best-effort (a raising sink never breaks a governed answer). The deterministic
   flow keeps using the bare `_emit` legacy `{stage}` shape, so the two paths
   never collide.
2. **Outer rails** (`server/agent.py::build_serve_rails`): `ingest`,
   `refuse_gate`, `cache_lookup`, and `assemble` now emit `rail` events; every
   terminal answer (success, refusal, graded delivery, cache hit) emits one
   `final` event carrying the answer stamp. The shared finalize helpers are
   called with `on_event=None` on the agent path so only the rich contract is
   emitted.
3. **Agent loop** (`server/agent.py::agent_core_node`): switched
   `agent.invoke(...)` → `agent.stream(..., stream_mode=["updates","values"])`.
   Model-node tool calls become `tool … start` events; tools-node ToolMessages
   become `tool … ok/blocked/error/cap/miss` resolves (paired by tool-call `id`,
   trivial because tools are forced sequential — G1). The final accumulated state
   comes from the last `values` chunk (replaces the old `invoke` return value).
   - **`get_stream_writer()` propagation — resolved:** we do **not** rely on it.
     Events are re-emitted from the outer node through the `on_event` callable
     captured as a closure (the same channel the flow path already uses), which
     is thread-safe regardless of the ToolNode worker thread. So the emit point
     is one place and no `get_stream_writer()` call happens inside the agent's
     middleware/tools.
4. Governed-tool (`run_query` / `sample_rows`) event `detail` is built **from the
   ledger entry**, so the live event and the final `governance_ledger` cannot
   drift (verified by a test asserting the run_query event count equals the
   ledger's run_query count).

Tests: `tests/test_agent_step_events.py` (emitter contract + an end-to-end repair
loop trace + negative-example refusal).

## Frontend design (the change to implement)

Recommended: a **two-tier hybrid** — a short phase stepper for the deterministic
rails, with a **live, append-only activity timeline** nested under the agent
phase. Rationale: the rails are fixed and short (stepper fits); the agent loop is
dynamic (timeline fits); the governance detail wants per-row expansion.

```
┌─ How the answer was reached ───────────────────────────────┐
│ ✓ Understood the question                                   │  route
│ ✓ Safety gate: cleared                                      │  refuse_gate/ok
│ ✓ Assembled governed context  (1 schema · 6 tables · 3 ex)  │  assemble
│ ⣷ Reasoning                                                 │  ← agent phase, live
│     🔍 Searched corpus  (4 tables, 2 examples)              │  search_corpus/ok
│     📋 Inspected  allgemeine_informationen  (12 cols) ·licensed │ inspect_schema/ok
│     ▶  Ran query · attempt 1   ⚠ blocked (term_semantics)   │  run_query/blocked
│     ▶  Ran query · attempt 2   ✓ 1 row                      │  run_query/ok
│ ✓ Composed answer   heuristic · cleared                     │  finalize
└─────────────────────────────────────────────────────────────┘
```

### Row behavior

- **Append on `status:"start"`** with a spinner; **resolve on the matching
  `ok/blocked/error`** by `seq`/`step`/`attempt`. Never pre-render future rows
  (the loop length is unknown).
- **Each agent row is expandable.** Expanded `run_query` shows the normalized
  SQL, the verdict, and on a block the `layer` + `reason` and the `allowed`
  tables. Expanded `inspect_schema` shows the licensed table + column count.
- **Attempt badges.** `run_query` rows show `attempt N`; a blocked attempt uses a
  warning affordance, a passing one a success affordance. This is what makes
  "needed multiple attempts to produce valid SQL" self-evident.
- **On completion** the whole block collapses to a one-line summary
  (`Reasoning · 4 steps, 1 repair`) that re-expands into the full trace. This
  collapsed trace is the same data as the existing `Provenance` panel; consider
  merging them so there is one audit surface.

### Map to the existing answer stamp

The `finalize` event carries `tier` / `semantic_assurance` / `safety_clearance`.
Keep rendering the existing two-axis stamp chips (`lineage` / `cleared` /
`heuristic`) on the finished answer. The step view explains *how* the answer got
its stamp (e.g. `heuristic` because a `run_query` repaired).

### Terminal and edge states (each must render distinctly)

| Situation | Event sequence | Render |
|---|---|---|
| Cache hit | `cache/hit` then `finalize` | "Served from cache" then answer; no agent phase |
| Negative-example refusal | `refuse_gate/refused` | Stop at the safety gate, show refusal; no further rows |
| Missing governed join | `assemble` then refusal | "No governed join path" then refusal |
| Repair then success | `run_query/blocked` … `run_query/ok` | stacked attempt rows, last one success |
| Budget exhausted | last `run_query` blocked/error, `finalize/refused` or graded | show the failed attempts then "Delivered unverified" or "Refused" |

### Deterministic flow path (backward compatible)

When `serve_path:"flow"`, only `kind:"rail"` events arrive (no `tool` events).
Render the classic fixed stepper (current behavior). Same component, no agent
timeline. So the change is additive: flow users see today's UI, agent users get
the live timeline.

## Alternatives considered

- **Keep the fixed stepper, collapse the agent loop into one "Reasoning" spinner
  with a counter** ("inspected 3 tables · attempt 2"). Least work; kills the
  dead-wait; but hides the per-attempt guardrail detail that is the whole point of
  a governed/auditable product. Acceptable as a v0 if the timeline is too much for
  one iteration.
- **Pure activity log for both paths** (drop the stepper entirely). Simpler
  component, but the deterministic rails read better as discrete stages, and it
  loses the at-a-glance "where are we" that a stepper gives.
- **Live DAG / Studio-style graph.** Overkill for the product surface; good for
  debugging, not for an end user auditing one answer.

## Definition of done (frontend)

- Agent runs stream a live, ordered, append-only timeline; no frozen wait.
- `run_query` attempts render with attempt number and pass/blocked/error state;
  blocked shows the guardrail layer + reason on expand.
- Completed trace collapses to a summary and re-expands; equals the
  `governance_ledger` on the answer.
- Flow-path runs still render the classic fixed stepper unchanged.
- All terminal states in the table above render distinctly.

## Detailed frontend implementation plan (`governed-bi-ui`)

Grounded in the current repo. Relevant existing files:

- `hooks/use-chat.ts` — defines `ChatTransport` (`{ messages, send, isRunning, activeStage }`) and the mock transport `useChat` (timer-driven stages).
- `hooks/use-stream-chat.ts` — the live `useStream` transport; `onCustomEvent` reads `data.stage` and sets `activeStage`; `streamMode: ["values","messages","custom"]`.
- `hooks/use-rest-chat.ts` — non-streaming fallback (no live stages).
- `lib/stages.ts` — `STAGES`, `STAGE_ALIASES`, `nodeToStage()`.
- `components/chat/stage-stepper.tsx` — `StageStepper({ activeStage })`, fixed vertical stepper.
- `components/chat/message-list.tsx` — mounts `<StageStepper activeStage={…}/>` in the running placeholder bubble.
- `components/chat/{mock,stream,rest}-chat.tsx` → `conversation.tsx` → `message-list.tsx`.
- `components/answer/provenance-drawer.tsx` — the post-answer audit panel; `lib/answer-delivery.ts` has `whyLines()` etc.; `AnswerView.provenance` is `Record<string, unknown>` (this is where `governance_ledger` will land).

The change is **additive and transport-neutral**: extend the contract, add a step-accumulating renderer, and switch `message-list` to it. The flow path keeps today's stepper.

### Step 1 — new module `lib/steps.ts` (wire types + reducer)

```ts
// The custom stream event (backend contract). start/resolve of a tool share `id`.
export interface GovEvent {
  seq: number;
  id?: string;                       // stable per logical step; start + resolve share it
  kind: "rail" | "tool" | "final";
  step: string;                      // route|refuse_gate|cache|assemble|finalize | search_corpus|inspect_schema|sample_rows|run_query
  status: "start"|"ok"|"blocked"|"error"|"refused"|"cap"|"hit"|"miss";
  label?: string;
  detail?: Record<string, unknown>;  // attempt, sql, verdict, layer, reason, rows, tables, columns, few_shots, schema, table_id, ...
  serve_path?: "flow" | "agent";     // present on the first event of a turn
}

// The UI row (start+resolve merged into one row).
export interface TimelineStep {
  key: string; seq: number;
  kind: GovEvent["kind"]; step: string;
  status: "running"|"ok"|"blocked"|"error"|"refused"|"cap"|"hit"|"miss";
  label: string;
  detail: Record<string, unknown>;
}

export function reduceSteps(prev: TimelineStep[], ev: GovEvent): TimelineStep[] {
  const key = ev.id ?? `${ev.step}:${ev.seq}`;
  const status = ev.status === "start" ? "running" : ev.status;
  const i = prev.findIndex((s) => s.key === key);
  const merged: TimelineStep = {
    key, seq: i >= 0 ? prev[i].seq : ev.seq, kind: ev.kind, step: ev.step, status,
    label: ev.label ?? prev[i]?.label ?? defaultLabel(ev),
    detail: { ...(prev[i]?.detail ?? {}), ...(ev.detail ?? {}) },
  };
  const next = i >= 0 ? prev.map((s, j) => (j === i ? merged : s)) : [...prev, merged];
  return next.sort((a, b) => a.seq - b.seq);
}

// Also export: defaultLabel(ev), stepIcon(step), isRail/isTool helpers,
// and buildStepsFromLedger(ledger) so the completed audit reuses the same rows.
```

`buildStepsFromLedger(governance_ledger)` maps each ledger entry to a `TimelineStep`, so the collapsed post-answer trace and the live trace share one renderer and one data shape.

### Step 2 — extend the contract (`hooks/use-chat.ts`)

```ts
export interface ChatTransport {
  messages: ChatMessage[];
  send: (question: string) => void;
  isRunning: boolean;
  activeStage: StageId | null;         // flow path (unchanged)
  steps?: TimelineStep[];              // agent path (new; optional so rest/mock can omit)
  servePath?: "flow" | "agent" | null; // picks the renderer
}
```

Optional fields keep `use-rest-chat.ts` compiling untouched.

### Step 3 — accumulate events (`hooks/use-stream-chat.ts`)

Add state and handle both event shapes in `onCustomEvent`:

```ts
const [steps, setSteps] = useState<TimelineStep[]>([]);
const [servePath, setServePath] = useState<"flow"|"agent"|null>(null);

onCustomEvent: (data) => {
  const ev = data as Partial<GovEvent> & { stage?: string };
  if (ev.serve_path) setServePath(ev.serve_path);
  if (typeof ev.kind === "string" && typeof ev.seq === "number") {
    setSteps((prev) => reduceSteps(prev, ev as GovEvent));      // agent path
    if (ev.kind === "rail") setActiveStage(nodeToStage(ev.step)); // keep stepper coherent too
  } else if (typeof ev.stage === "string") {
    setActiveStage(nodeToStage(ev.stage));                       // legacy flow events
  }
},
```

In `send()`, reset `setSteps([])` and `setServePath(null)` alongside `setActiveStage`. Return `steps` and `servePath` (only when `isRunning`, mirroring `activeStage`, plus keep the final `steps` available to the answer's provenance — see Step 6).

### Step 4 — renderer switch `components/chat/serve-progress.tsx` (new)

```tsx
export function ServeProgress(props: {
  isRunning: boolean; activeStage: StageId | null;
  steps?: TimelineStep[]; servePath?: "flow" | "agent" | null;
}) {
  if (props.servePath === "agent" && props.steps && props.steps.length > 0) {
    return <AgentTimeline steps={props.steps} isRunning={props.isRunning} />;
  }
  return <StageStepper activeStage={props.activeStage} />; // unchanged flow path
}
```

### Step 5 — `components/chat/agent-timeline.tsx` + `step-row.tsx` (new)

- `AgentTimeline` renders `steps` in `seq` order. Rail/final steps render as top-level stepper-style lines (reuse `StageIcon` from `stage-stepper.tsx`, extracted/shared). Tool steps render indented under a "Reasoning" group header that shows a spinner while `isRunning`.
- `StepRow` renders one step:
  - **icon** by `status`: `running`→spinner, `ok/hit`→check, `blocked/cap`→amber warning, `error/refused/miss`→red/x.
  - **label** + a small **badge** for `run_query` `detail.attempt` ("attempt 2").
  - **expandable** (chevron). Expanded content by `step`:
    - `run_query`: `<SqlBlock sql={detail.sql}/>` (reuse `components/answer/sql-block.tsx`), and on `blocked` show `detail.layer` + `detail.reason`; on `ok` show `detail.rows`.
    - `inspect_schema`: `detail.table_id` + `detail.columns` count + "licensed".
    - `search_corpus`: counts (`tables`/`few_shots`/`metrics`).
    - `assemble`: `detail.schema`, `tables`, `few_shots`.
- On completion (`!isRunning`), `AgentTimeline` collapses to a one-line summary (`Reasoning · N steps, M repairs`) with a chevron to re-expand.

### Step 6 — wire it in + merge with provenance

- `components/chat/message-list.tsx`: replace `<StageStepper activeStage={activeStage}/>` with `<ServeProgress isRunning activeStage steps servePath/>`; thread `steps`/`servePath` down from `conversation.tsx` (which already forwards the transport fields).
- `components/chat/{mock,stream,rest}-chat.tsx`: pass the new fields through (rest/mock pass `undefined`, which the renderer treats as flow).
- **Provenance merge:** in `components/answer/provenance-drawer.tsx`, add a "Steps" section that renders `buildStepsFromLedger(answer.provenance.governance_ledger)` with the SAME `StepRow`. Result: the live trace during the run and the audit trace after the run are the identical component over the identical data. (Open question below: fully merge vs. keep the live stepper separate from the drawer.)

### Step 7 — mock transport parity (`hooks/use-chat.ts` + `lib/mock/fixtures.ts`)

So the timeline is demoable offline (USE_MOCKS) and testable:

- Add `MOCK_AGENT_EVENTS: GovEvent[]` to `lib/mock/fixtures.ts` (a scripted trajectory: `assemble/ok` → `search_corpus/ok` → `inspect_schema/ok` → `run_query start→blocked(term_semantics)` → `run_query start→ok` → `finalize/ok`).
- In `useChat`, when a `USE_AGENT_MOCK` flag (or a question keyword) is set, replay `MOCK_AGENT_EVENTS` on the existing `STAGE_INTERVAL_MS` timer through `reduceSteps`, set `servePath:"agent"`, and resolve to `MOCK_ANSWER`. Keeps the mock a faithful stand-in (mirrors the `use-chat.ts` docstring intent).

### Step 8 — backend companion (this repo) — DONE

Landed on `feat/governed-agentic-serve-runtime` (see "Backend work" above for the
detail). Summary of what the frontend now receives:

- `agent_core_node` streams the loop via `agent.stream(stream_mode=["updates","values"])`, emitting `tool` start/resolve events (paired by tool-call `id`) and re-emitting through the captured `on_event` callable (no `get_stream_writer()` inside the agent).
- Rails (`ingest`/`refuse_gate`/`cache`/`assemble`) emit `rail` events; every terminal emits one `final` event with the stamp.
- `run_query`/`sample_rows` event `detail` is the ledger entry (`attempt/verdict/layer/reason/sql/allowed/rows`), so live == audit.
- Flow path (`server/flow.py`) unchanged: still emits legacy `{stage}` events → frontend Step 3's `else if (typeof ev.stage === "string")` branch → classic stepper.

**Note on `id`:** the backend sets `id` = the LangChain tool-call id on `tool` events (start and resolve share it), which is exactly what `reduceSteps` keys on. `rail`/`final` events carry no `id`, so they key on `${step}:${seq}` (each is one-shot, no start/resolve pair).

### File-by-file checklist

| File | Change |
|---|---|
| `lib/steps.ts` | 🆕 `GovEvent`, `TimelineStep`, `reduceSteps`, `defaultLabel`, `buildStepsFromLedger` |
| `hooks/use-chat.ts` | ✏️ extend `ChatTransport` with `steps?` + `servePath?` |
| `hooks/use-stream-chat.ts` | ✏️ accumulate events → `steps`; capture `servePath`; reset on send; return both |
| `components/chat/serve-progress.tsx` | 🆕 renderer switch (agent timeline vs flow stepper) |
| `components/chat/agent-timeline.tsx` | 🆕 dynamic grouped timeline + collapse-on-complete |
| `components/chat/step-row.tsx` | 🆕 one row: icon/label/attempt badge/expandable detail |
| `components/chat/stage-stepper.tsx` | ✏️ export `StageIcon` for reuse (no behavior change) |
| `components/chat/message-list.tsx` | ✏️ `StageStepper` → `ServeProgress` |
| `components/chat/{conversation,mock,stream,rest}-chat.tsx` | ✏️ thread `steps`/`servePath` through |
| `components/answer/provenance-drawer.tsx` | ✏️ add "Steps" section via `buildStepsFromLedger` |
| `hooks/use-chat.ts` + `lib/mock/fixtures.ts` | ✏️ mock agent trajectory for offline/demo/test |

### Testing

- Unit: `reduceSteps` (start→resolve merge by `id`; ordering by `seq`; repair attempts stack; out-of-order arrival). `buildStepsFromLedger` round-trips a sample `governance_ledger`.
- Component: `AgentTimeline` renders a scripted `GovEvent[]`, shows attempt badges, expands `run_query` SQL, collapses on complete.
- Transport: mock agent replay drives the timeline with no backend.
- Regression: flow-path (`servePath !== "agent"`) still renders `StageStepper` unchanged.

### Accessibility

- Timeline is an `<ol>`; each row `aria-current` while `running`; status conveyed by text/`aria-label`, not color alone (mirror `stage-stepper.tsx`). Announce new rows politely (`aria-live="polite"` on the group) so a screen reader hears progress instead of silence.

### Phasing

- **v0 (small):** Steps 1-4 + a collapsed "Reasoning" group that just lists tool labels as they stream (no per-row expand). Kills the dead wait immediately.
- **v1 (full):** expandable rows (SQL + verdict), attempt badges, collapse-to-summary, provenance merge (Steps 5-6), mock parity (Step 7).

## Open questions

- Merge the live trace and the existing `Provenance` panel into one audit surface,
  or keep them separate?
- Show the normalized SQL inline per `run_query` attempt, or only on expand
  (per the Q5 egress posture, SQL is fine to show; result rows already show)?
- How much of `search_corpus` content (few-shots surfaced) to reveal, if any. The
  backend currently emits only `query` on `search_corpus` (counts omitted to avoid
  parsing the rendered tool string); add structured counts later if the UI wants
  them.
- ~~Backend: confirm `get_stream_writer()` propagation~~ — resolved: the backend
  re-emits through the captured `on_event` callable, so no propagation dependency.
