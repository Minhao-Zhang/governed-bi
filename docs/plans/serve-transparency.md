# Making what the agent saw visible in the frontend

> **This doc is the design and the measurements. To build it, read
> [serve-transparency-handoff.md](serve-transparency-handoff.md)**, which carries the
> backend and frontend requirements per change, and corrects three claims below: the
> `STAGE_ALIASES` hook named in C2 does not exist, the C1 payload example contradicts
> its own prose in a way that silently breaks a working row, and several
> `PromptContext` views carry no `id` field for C1 to send.

## Problem

A user watching a governed answer arrive can see *which steps ran* but not *what the
agent was looking at* when it ran them. The step timeline shipped in
[agent-step-visualization.md](agent-step-visualization.md) and is live: rows stream in,
`run_query` attempts expand to show SQL and the guardrail layer that blocked them. That
part works.

What is missing is content. The stream reports that assembly injected five tables and
three few-shots; it does not say which five. It reports that routing happened; it says
nothing about the 57 candidate schemas, the 10 that were shortlisted, or why one was
picked. The full prompt block handed to the model is computed, hashed, measured, and
then discarded without ever leaving the server.

That gap matters more than it did a week ago, because the two places we most recently
found defects are both invisible from the frontend:

- 70% of routing failures happen at the LLM pick, on a shortlist that already contained
  the right schema. 41 of those 100 failures happen because the picker is shown a
  truncated view of the correct schema. Nothing about this is on the wire.
- Injected content reaches the prompt in quantities nobody can inspect. 17.25 joins per
  question arrive sorted alphabetically by ON clause, and the only signal the frontend
  gets is a count it does not currently render.

A user checking "did it look at the right things?" cannot answer that question today.

## Relationship to the existing step plan

[agent-step-visualization.md](agent-step-visualization.md) owns the *timeline*: the
transport, the `{seq, kind, step, status, detail}` envelope, row lifecycle, terminal
states, and the components (`lib/steps.ts`, `agent-timeline.tsx`, `step-row.tsx`,
`serve-progress.tsx`, all present in the UI repo). None of that changes here.

This plan owns the *contents*: what goes inside `detail`, and one new step. It also
resolves an open question that plan left standing, quoted verbatim from its Open
questions section:

> How much of `search_corpus` content (few-shots surfaced) to reveal, if any. The
> backend currently emits only `query` on `search_corpus` (counts omitted to avoid
> parsing the rendered tool string); add structured counts later if the UI wants them.

The UI wants them. Section 4 says what to send instead of parsing a rendered string.

## What the stream carries today

Verified by reading every emit site, not from the contract table:

| kind | step | `detail` fields that actually exist |
|---|---|---|
| rail | `route` | none; `events.rail("route")` is called with no arguments |
| rail | `refuse_gate` | `negative_example` id, on refusal only |
| rail | `assemble` | `schema`, `tables` count, `few_shots` count, `notes` count |
| tool | `run_query` | `attempt`, `sql`, `verdict`, `layer`, `reason`, `allowed`, `rows` |
| tool | `inspect_schema` | `table_id`, `columns` count, `licensed` |
| tool | `sample_rows` | `table_id`, `rows`, `reason` |
| tool | `search_corpus` | `query` only |
| final | `finalize` | the answer stamp, plus the whole `provenance` dict |

`run_query` is the model to copy. `attempt` plus `verdict` plus `layer` is why a repair
loop is already legible, and it is legible because the payload carries identity and not
just arithmetic.

Two things already reach the frontend and should be surfaced before any backend work,
because they cost nothing: `provenance.schema_route_channel` names which ranking channel
ran (`embedding`, `bm25_fallback`, or `none`), and `provenance.licensed_tables` names the
authorized scope. A silent fall back to BM25 roughly halves routing recall, so that field
belongs on the audit surface regardless of everything below.

## The four changes

### C1. `assemble` emits identity, not counts

Today the event says `tables=5, few_shots=3, notes=0`. The `PromptContext` in hand at
that moment already holds the structured views, so identity costs nothing to add.
`TableView` carries `id`, `physical_name`, `schema`, `description`, `grain`, and a
`retrieved` boolean that distinguishes a table surfaced by retrieval from one pulled in
only because a join made it reachable. That flag is the most useful single field in this
whole plan and it is already computed.

Send, alongside the existing counts:

- `tables`: list of `{id, physical_name, schema, retrieved}`
- `few_shots`: list of `{id, question}`
- `notes`: the existing `injected_note_ids`, plus `normative_force` per note so the UI
  can separate a rule the engine promises to honour from an advisory aside
- `joins`: list of `{on, cardinality, confidence, low_confidence}`
- `terms`, `metrics`: ids and names
- `caveats`: count is enough; the text is already in the rendered block from C3

Emit point: `analyst/agent.py:789`, the existing `events.rail("assemble", "ok", ...)`.

### C2. A `schema_route` rail event

No routing event exists. The UI contract admits this in
[ui-frontend-handoff.md](../ui-frontend-handoff.md): "No `schema_route` stream event
exists." Every value needed is already a local variable in the routing block at
`analyst/agent.py:623-651`.

Send a new `rail` step named `schema_route`, with:

- `candidates`: the shortlist in rank order, `{schema, rank}`, and cosine score where the
  embedding channel ran
- `picked`: the chosen schema
- `channel`: `embedding` / `bm25_fallback` / `none`
- `fallback`: the `SchemaPick.fallback` reason when the pick degraded to `candidates[0]`
- `n_total`: how many schemas were in the pool before shortlisting
- `truncated`: whether the picker's per-candidate summary was clipped, and at what caps

`truncated` is worth the extra field. The picker sees at most 15 tables and 12 columns per
table, chosen alphabetically, and a schema shown at 15 of 73 tables loses to a fully
visible five-table sibling. Surfacing "you are looking at 15 of 42 tables for this
candidate" makes the failure mode legible to a user before it is fixed in the ranker.

Frontend: one `STAGE_ALIASES` entry maps this to a "Selecting schema" row. The handoff
doc already reserves that hook, so no component changes.

### C3. Expose the rendered context block

`analyst/agent.py:771` computes `rendered = context.render()`. That string *is* the
`## Governed context` section of the system prompt. It reaches graph state as
`context_block`, and provenance keeps `context_chars` and `context_hash`. The text itself
is never sent.

So the frontend can say "the agent received 21117 characters, fingerprint 33499efc" and
nothing more. Send the block.

Two things to decide, and the plan recommends one of each:

Transport: stream, not `ChatState`. `ChatState` is `{messages, answer}` and
round-trips through the checkpointer. Adding a ~20k-character field per turn multiplies
thread storage for a payload almost nobody opens twice. Measured mean context size on a
1351-question run is 19537 characters for the curated arm and 21298 for the SME arm. Put
the block on the `assemble` event instead, where it costs nothing durable. The tradeoff is
that custom events are ephemeral: reload the page and the timeline replays `messages` and
`values.answer` from the checkpointer but not the events. If durable review turns out to
matter, add a fetch endpoint keyed by turn rather than widening the persisted state.

Egress: needs an explicit decision, not an inference. `render()` is built on the
`for_analyst()` corpus view, so assets marked `governance.excluded` are already absent,
and this is the same text the model receives. That is a good argument and it is not
sufficient, because this would be the first time the full assembled prompt leaves the
server, and column descriptions and sample values inside it have never been reviewed for
that purpose. Treat the raw block as gated on a PII posture decision. Ship C1 and C2
without waiting for it.

### C4. `search_corpus` reports what it found

The one genuinely new backend work. Today the event carries the query and nothing else,
and the reason recorded in the existing plan was to avoid parsing the rendered tool
string. The fix is not to parse it: `retrieve()` returns a structured `RetrievalResult`
with the id lists, so the tool wrapper can emit from that instead of from its own
rendered output.

Phase in two steps:

1. Hits. Per call, the asset ids returned by type, `{tables, few_shots, metrics,
   terms, notes}`, with each id and name. This answers "what did this pass touch?"
2. Channels. Per hit, which channel ranked it and at what rank: BM25, dense vector,
   the fused order, and whether it arrived through grounding expansion instead of ranking
   (a bound term pulling in its target, a metric pulling its base table, a table pulling
   its columns). This answers "how many passes were there and what did each contribute?",
   which is the actual question, and it needs `retrieve()` to keep per-channel provenance
   it currently discards after fusing.

Step 1 is small and independently useful. Step 2 is the only change here that touches
retrieval internals, so it goes last.

## Payload additions

Extending the envelope from the existing plan, unchanged in shape:

```jsonc
// C2, new step
{ "seq": 2, "kind": "rail", "step": "schema_route", "status": "ok",
  "label": "Selected schema mondial_geo",
  "detail": {
    "n_total": 57,
    "channel": "embedding",
    "picked": "mondial_geo",
    "fallback": null,
    "candidates": [ {"schema": "mondial_geo", "rank": 1, "score": 0.71},
                    {"schema": "world", "rank": 2, "score": 0.68} ],
    "truncated": [ {"schema": "mondial_geo", "tables_shown": 15, "tables_total": 42} ]
  }}

// C1, existing step, detail widened
{ "seq": 4, "kind": "rail", "step": "assemble", "status": "ok",
  "detail": {
    "schema": "mondial_geo",
    "tables": [ {"id": "tbl_mondial_geo_city", "physical_name": "city",
                 "schema": "mondial_geo", "retrieved": true} ],
    "few_shots": [ {"id": "fs_mondial_geo_3", "question": "..."} ],
    "notes": [ {"id": "note_x", "normative_force": "must_honour"} ],
    "joins": [ {"on": "city.country = country.code", "confidence": 0.55,
                "low_confidence": true} ],
    "caveats": 27,
    "context_chars": 21117,
    "context_block": "..."   // C3, gated on the egress decision
  }}
```

## Frontend rendering

Per change, in the components that already exist:

**C1.** The `assemble` row expands to a grouped list. Tables split into "retrieved" and
"reached via join", because that distinction explains most of what a user finds
surprising about scope. Low-confidence joins get a marker; the run this plan draws on had
`confidence` at 0.55 on 841 of 853 joins, so that marker will be common and should be
quiet rather than alarming.

**C2.** A "Selecting schema" row above assembly. Collapsed, it reads "mondial_geo, from
10 candidates". Expanded, it shows the ranked candidate list with scores, the picked one
marked, and a warning affordance when `channel` is `bm25_fallback` or `fallback` is
non-null. On single-schema deployments the row is suppressed entirely.

**C3.** A "Context sent to the model" disclosure on the assemble row, collapsed by
default, monospace, with the character count in the summary line. This is the artifact a
user actually wants when they ask what the agent saw.

**C4.** The `search_corpus` row gains the same treatment as `assemble`: hit lists by
type, and in phase 2 a per-hit channel badge.

## Constraints

- Payload size. C3 adds roughly 20k characters per turn to the stream. Fine on a
  local socket, worth measuring before any remote deployment. C1 and C2 add a few hundred
  bytes.
- Ephemerality. Custom events do not survive a reload. Accepted for now; the fix, if
  needed, is a fetch endpoint and not a wider `ChatState`.
- Best-effort emission. `GovEventStream` swallows exceptions from the sink by design,
  so a malformed payload silently drops rather than breaking an answer. Wider payloads
  raise the chance of hitting that path, so the new fields need a serialization test
  rather than trust.
- Ordering. `schema_route` must carry a `seq` below `assemble`, which it will
  naturally, since routing precedes assembly in the same node.

## Order of work

1. Surface `provenance.schema_route_channel` and `provenance.licensed_tables`. No backend
   change at all; both already arrive on the `final` event.
2. C2, the `schema_route` event. Highest value per line: it makes the stage holding 70% of
   routing failures visible.
3. C1, identity in `assemble`. Small, and unblocks the most-requested view.
4. C4 step 1, `search_corpus` hits.
5. C3, the context block, once the egress posture is decided.
6. C4 step 2, per-channel attribution, which needs `retrieve()` to stop discarding
   channel provenance.

## Definition of done

- A user can answer, without server access: which schema was chosen and from what
  shortlist, which tables and few-shots and notes reached the prompt, which of those
  tables were retrieved rather than join-reachable, whether routing ran on the embedding
  channel or fell back, and what each retrieval pass returned.
- A `bm25_fallback` route is visibly distinct from an `embedding` route.
- A truncated picker view is visible as truncated.
- No new field is persisted through the checkpointer.
