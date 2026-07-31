# Serve transparency: backend ↔ frontend handoff

> **STATUS 2026-07-31 — one unique measurement, then it is history.**
>
> Decision 13 ("the backend is authoritative, the frontend adapts, a full rewrite is acceptable")
> invalidates every frontend line-number instruction in §3.3 / §4.3 / §5.1. Superseded overall by
> [rebuild-checklist.md](rebuild-checklist.md) §5.3.
>
> **What is unique and unmigrated:** §3.1 + §3.2's causal quantification of picker truncation —
> of 1351 questions, routed 1208, picker-chose-wrong-but-gold-was-in-shortlist 100, never-recalled
> 43; rank-1 71.4%, top-10 96.8%, post-pick 89.4%. **87 of the 100 picker failures happened when
> it only saw a partial view of the gold schema** (41 from the 15-table cap, 46 because a gold
> table was wider than 12 columns, 13 fully visible); error rate 6.2% when gold fit inside 15
> tables vs 11.6% when it did not; `mondial_geo` (42 tables shown as 15) lost to the five-table
> `world` twelve times. That is an un-filed defect with a named cause. Reconcile against
> `docs/experiments/20260730T034522Z-curated-sme-error-analysis.md` §2-3 before filing.
>
> Also unmigrated: §5.2's per-channel attribution (needs `retrieve()` to keep the pre-fusion
> ranking) → 5.3.6 · §8's egress measurement (rendered context block averages 19537/21298 chars —
> send it or not?) → 5.3.9's per-key egress decision · §9.1's `STAGE_ALIASES` claim, which names a
> symbol that **does not exist in the UI repo** → add to 5.3.10's verification grep.
>
> Not worth migrating: §7's type-invariance rules (superseded by 5.3.7's `CONTRACT_VERSION`),
> §2.2's status table (superseded by [analyst.md](../analyst.md):85-100), §10's first three steps
> (done).

Implementation handoff for making the governed answer's *inputs* visible in the UI.
Design rationale and the measurements behind it: [serve-transparency.md](serve-transparency.md).
The event transport this extends: [Analyst](../analyst.md#the-event-contract-per-step).

**Two repos.** Backend is this repo (`governed-bi`). Frontend is the sibling
`governed-bi-ui` (Next.js, LangChain `useStream`), local at `../governed-bi-ui`. Every
path below is prefixed with the repo it belongs to.

**Written for someone who has not worked in either repo.** §1 is the vocabulary, §2 is
how the wire works today, §3–§6 are the four changes, §7–§9 are the traps. If you read
nothing else, read §7: the obvious way to implement C1 silently breaks a row that works
today.

---

## 1. Vocabulary

| Term | What it means here |
| --- | --- |
| **serve** | Answering one user question. The code path is `analyst/agent.py`. |
| **rail** | A deterministic step *outside* the model's reasoning loop: routing, the refuse gate, context assembly. Named "rails" because they wrap the agent. |
| **tool** | Something the model chose to call inside its loop: `search_corpus`, `inspect_schema`, `sample_rows`, `run_query`. |
| **schema** | One BIRD database loaded as one Postgres schema. A pooled run holds 57 of them in one database, so the system must pick one per question. |
| **routing / the pick** | Choosing which schema answers. Two stages: rank all schemas to a shortlist (`shortlist_schemas`), then one LLM call picks one from that shortlist (`pick_schema`). |
| **assembly** | Turning retrieved asset ids into the `## Governed context` text block that goes in the system prompt. `analyst/context.py`. |
| **licensed** | A table the guardrails will permit this turn. Derived from assembly, so the model can only query what assembly showed it. |
| **provenance** | A dict attached to the finished answer recording how it was produced. Reaches the client on the `final` event. |
| **`GovEvent`** | One custom stream event. The wire format between the two repos. |

---

## 2. The transport as it exists today

Verified by reading every emit site, not from a contract table. Do not trust the older
docs on this; two of their claims are wrong and §9 lists them.

### 2.1 The envelope

`governed-bi` · `analyst/governance.py:521` (`GovEventStream._emit_event`) builds every
event:

```python
payload = {"seq": self._seq, "kind": kind, "step": step, "status": status}
if step_id is not None: payload["id"] = step_id
if label is not None:   payload["label"] = label
payload["detail"] = {k: v for k, v in (detail or {}).items() if v is not None}
if not self._started: payload["serve_path"] = self._serve_path
```

Three things to internalise:

1. **`None` values are dropped from `detail`.** An optional field you emit as `None`
   does not arrive as `null`; the key is absent. The frontend must treat absent and
   null identically.
2. **Emission is best effort.** `_emit_event` wraps the callback in
   `try/except Exception: pass` (`:543-546`). A payload that fails to serialise
   **silently disappears** and the answer still succeeds. This is deliberate (a broken
   UI must not break a governed answer) and it means a bug in your new payload looks
   like "the event never fired". Test serialisation directly rather than trusting a
   live run.
3. **`seq` is monotonic per turn**, reset by `GovEventStream.reset()`. The frontend
   orders by it.

Emit helpers: `.rail(step, status="ok", *, label=None, **detail)` and
`.tool(step, status, *, step_id=None, label=None, **detail)`.

### 2.2 What each event carries right now

| kind | step | `detail` keys that actually exist | emit site (`governed-bi`) |
| --- | --- | --- | --- |
| rail | `route` | *none* — called with no arguments | `analyst/agent.py` |
| rail | `refuse_gate` | `negative_example` (on refusal only) | `analyst/agent.py` |
| rail | `assemble` | `schema`, `tables`, `few_shots`, `notes` — **all four are integers** | `analyst/agent.py:789` |
| tool | `run_query` | `attempt`, `sql`, `verdict`, `layer`, `reason`, `allowed`, `rows` | `analyst/agent.py:834` |
| tool | `inspect_schema` | `table_id`, `columns`, `licensed` | `analyst/agent.py:860` |
| tool | `sample_rows` | `table_id`, `rows`, `reason` | `analyst/agent.py:848` |
| tool | `search_corpus` | `query` only | `analyst/agent.py:870` |
| final | `finalize` | the answer stamp plus the whole `provenance` dict | `GovEventStream.final` |

There is **no routing event of any kind**. Routing happens at
`analyst/agent.py:613-660` inside the `_assemble_inner` node and emits nothing; only
`assemble` fires, after the fact.

`run_query` is the model to copy. It is legible because `attempt` + `verdict` + `layer`
carry *identity*, not just arithmetic.

### 2.3 How the frontend consumes it

`governed-bi-ui` · `hooks/use-stream-chat.ts:100`:

```ts
onCustomEvent: (data) => {
  const ev = data as Partial<GovEvent> | null | undefined;
  // ... requires `kind` and a numeric `seq`
  const next = reduceSteps(stepsRef.current, ev as GovEvent);
}
```

**There is no allowlist of step names.** Any event with `kind` and a numeric `seq`
reaches `reduceSteps`. So a brand-new rail step renders without touching the transport.
What it renders is the problem: `lib/steps.ts:166` `defaultLabel` falls through to
`return ev.step`, so an unrecognised step shows the literal string `schema_route`, gets
the generic `Sparkles` icon (`:113`), and `step-row.tsx:131` `hasDetail` returns `false`
for unknown steps, so **it is not expandable and its `detail` is invisible**.

`reduceSteps` (`lib/steps.ts:175`) merges a tool's `start` and its resolution into one
row by shared `id`, and the merge is **shallow**: `{...prev.detail, ...ev.detail}`. A
nested object in `detail` is replaced wholesale by a later event, never deep-merged.

---

## 3. C2 — a `schema_route` rail event

**Do this first.** It is the highest value per line: it makes visible the stage that
holds 70% of routing failures, and every value it needs is already a local variable.

### 3.1 Why

Measured on the 20260730 pooled run (57 schemas, 1351 questions, `route_top_k=10`,
`use_embedder=True`), reproduced from `generations.curated.jsonl`:

| stage | n | rate |
| --- | --- | --- |
| routed correctly | 1208 | 89.4% |
| picker chose wrong, gold **was** in the shortlist | 100 | 7.4% |
| ranking never surfaced gold | 43 | 3.2% |

Ranking puts the right schema at rank 1 on **71.4%** of questions and inside the top 10
on **96.8%**. The LLM pick converts that into 89.4%. So the pick earns ~18 points over
naively taking rank 1, and leaves 7.4 on the table. None of this is on the wire, so a
user cannot tell a routing failure from a SQL failure.

### 3.2 Backend requirements

**File:** `governed-bi` · `src/governed_bi/analyst/agent.py`, inside `_assemble_inner`,
after the `pick_schema` block (~`:651`) and **before** the `assemble` emit at `:789`.

Emit `events.rail("schema_route", "ok", **detail)` with:

| field | type | source (all already in scope) |
| --- | --- | --- |
| `n_total` | int | number of schemas in the pool |
| `channel` | str | `route_channel["schema_route_channel"]` — `"embedding"` / `"bm25_fallback"` / `"none"` |
| `degraded` | bool | `route_channel["schema_route_degraded"]` — an embedder was configured and ranking still fell back |
| `candidates` | list | `shortlisted`, in rank order, as `{schema, rank}` plus `score` when the embedding channel ran |
| `picked` | str \| None | `decision.schema` |
| `fallback` | str \| None | `decision.fallback` |
| `truncated` | list | per candidate, `{schema, tables_shown, tables_total}`, emitted only where `tables_total > tables_shown` |

`SchemaPick` is a `NamedTuple` (`retrieval/schema_router.py:505`) of exactly
`(schema, fallback)`. `fallback` is `None` only for a pick the model cleanly stated;
otherwise it names why the row is not a real model decision (`"call_failed"`,
`"unparseable_reply"`, `"parsed_nonfinal_line"`). **Surface it.** A UI that renders a
fallback pick identically to a real one is showing a decision that never happened.

**On `truncated`, read this before writing it.** The per-candidate summary the picker
sees is capped at 15 tables and, separately, at `schema_pick_max_columns` (default 12)
columns per table. The column cap is configurable and reaches the call site; **the
15-table cap is not**. `agent.py` calls `pick_schema(...)` passing only
`max_columns=settings.schema_pick_max_columns`, so `max_tables` takes its hardcoded
default of `15` (`retrieval/schema_router.py:335` and `:529`). To emit `tables_shown`
you must therefore either import that default or thread a knob through. **Import the
default; do not add a config knob.** The fix for the underlying defect is to rank which
15 tables get shown, not to raise the cap (see `open-work.md` R1), and a knob would
invite raising it instead.

Why the field is worth the extra work: **87 of the 100 picker failures happen while the
picker is looking at a partial view of the correct schema** (41 truncated by table
count, 46 with at least one gold table wider than 12 columns, 13 fully visible). Pick
error is 6.2% when the gold schema fits inside 15 tables and 11.6% when it does not.
`mondial_geo`, 42 tables shown as 15, loses to five-table `world` twelve times.

**Tests** (`governed-bi` · `tests/`): extend `test_agent_step_events.py`. Assert the
event fires before `assemble` (compare `seq`), that `candidates` is in rank order, that
`fallback` survives to the payload, and that the whole `detail` survives
`json.dumps`. The last one matters because §2.1's swallowed exception would otherwise
hide a serialisation bug.

### 3.3 Frontend requirements

`governed-bi-ui`. Four small edits, two files. **Not zero — see §9.1.**

1. `lib/steps.ts` · `stepIcon` (`:90`): add `case "schema_route": return Waypoints;`
   (or another `lucide-react` icon not already used).
2. `lib/steps.ts` · `defaultLabel` (`:134`): add a `case "schema_route"` returning
   `Selected schema <picked>` when `picked` is a non-empty string, else
   `"Selecting schema"`. Use the existing `str(d, "picked")` helper.
3. `components/chat/step-row.tsx` · `hasDetail` (`:115`): add
   `case "schema_route": return ["candidates", "picked", "channel"].some((k) => d[k] != null);`
   Without this the row will not expand.
4. `components/chat/step-row.tsx` · `StepDetail` (`:135`): add a `case "schema_route"`
   rendering the ranked candidate list with the picked one marked, plus a warning
   affordance when `channel === "bm25_fallback"` or `fallback` is non-null.

**Suppress the row entirely when `n_total <= 1`.** Single-schema deployments (SQLite,
the BIRD demo) route trivially and the row is noise.

### 3.4 Acceptance

- A pooled turn shows a "Selecting schema" row above assembly, collapsed as
  `mondial_geo, from 10 candidates`.
- Expanded, it lists all 10 candidates in rank order with scores, the pick marked.
- `channel: "bm25_fallback"` is visually distinct from `"embedding"`.
- A candidate shown at 15 of 42 tables reads as truncated.
- A single-schema turn shows no such row.

---

## 4. C1 — `assemble` emits identity, not only counts

### 4.1 Why

The event says `tables=5, few_shots=3, notes=0`. It does not say *which*. The
`PromptContext` in hand at that moment already holds the structured views, so identity
costs no new computation.

The single most useful field is `TableView.retrieved` (`analyst/context.py:94`):
`True` means retrieval surfaced the table, `False` means it is only reachable because a
join made it so. That distinction explains most of what users find surprising about
scope, and it is already computed.

### 4.2 Backend requirements

**File:** `governed-bi` · `src/governed_bi/analyst/agent.py:789`, the existing
`events.rail("assemble", "ok", ...)`.

**Read §7 first.** The existing keys `tables` / `few_shots` / `notes` are integers that
the frontend already renders. Keep them integers. Add identity under a new nested key:

```python
events.rail(
    "assemble", "ok",
    schema=default_schema,
    tables=len(context.tables),          # unchanged, still an int
    few_shots=len(context.few_shots),    # unchanged
    notes=len(context.injected_note_ids),# unchanged
    caveats=len(context.caveats),        # new, an int
    context_chars=len(rendered),         # new, an int
    items={                              # new, all identity lives here
        "tables": [...],
        "few_shots": [...],
        "notes": [...],
        "joins": [...],
        "terms": [...],
        "metrics": [...],
    },
)
```

What goes in each list, and what the dataclasses actually give you
(`analyst/context.py:76-157`):

| `items` key | Emit | Available directly? |
| --- | --- | --- |
| `tables` | `{id, physical_name, schema, retrieved}` | **Yes.** All four are `TableView` fields. |
| `joins` | `{on, cardinality, confidence, low_confidence}` | **Yes.** All four are `JoinView` fields. |
| `few_shots` | `{question}` | **Partly.** `FewShotView` is `(question, sql)` with **no id**. Either emit `question` alone, or resolve ids from `retrieval.few_shot_ids`. Do **not** emit `sql`; it is long and the user has the rendered block for that. |
| `notes` | `{id, normative_force}` | **No.** `context.injected_note_ids` gives ids only. `normative_force` lives on the asset, so you must look it up: `corpus.by_id(nid)`. Worth it — it separates a rule the engine promises to honour from an advisory aside. |
| `terms` | `{name}` | **Partly.** `TermView` is `(name, synonyms, binds_to)`, no id. Name is enough. |
| `metrics` | `{name}` | **Partly.** `MetricView` is `(name, expression, base_table, dimensions)`, no id. |

Do not add `id` fields to those dataclasses just to satisfy this event. They are frozen
value objects consumed by `render()`, and widening them for a UI payload puts UI
concerns in the prompt-assembly layer.

**Do not send `caveats` text here.** There are ~34 injected per question on a curated
run. The count is enough; the text is in the rendered block (C3).

**Tests:** extend `test_agent_step_events.py`. Assert the counts stay integers (this is
the regression that §7 describes), that `items.tables` entries carry `retrieved`, and
that a note with `normative_force="must_honour"` survives to the payload.

### 4.3 Frontend requirements

`governed-bi-ui` · `components/chat/step-row.tsx`:

- `hasDetail` `case "assemble"` (`:127`) already returns true on `schema` / `tables` /
  `few_shots`, so it needs **no change**.
- `StepDetail` `case "assemble"` (`:205`) currently renders one line via `countLabel`.
  Extend it: keep the existing count line as the summary, then when `d.items` is
  present render grouped lists. Split tables into **"retrieved"** and **"reached via
  join"** on the `retrieved` flag.
- Mark low-confidence joins quietly. On the measured run `confidence` was 0.55 on 841
  of 853 joins, so the marker will be on nearly every join. It must read as ordinary
  metadata, not as an alarm.

### 4.4 Acceptance

- The assemble row still shows `schema X · 5 tables, 3 examples` when collapsed
  (proving §7's regression did not happen).
- Expanded, it lists table ids grouped by retrieved vs join-reachable.
- A `must_honour` note is visually distinct from an advisory one.

---

## 5. C4 — `search_corpus` reports what it found

Two phases. Phase 1 is small and independently useful; phase 2 is the only change in
this document that touches retrieval internals, so it goes last.

### 5.1 Phase 1: hits

**Backend** · `governed-bi` · `analyst/agent.py:870`. Today:

```python
events.tool("search_corpus", "ok", step_id=tcid, query=args.get("query"))
```

The reason recorded in the older plan for emitting nothing else was to avoid parsing the
tool's rendered output string. **Do not parse it.** `retrieve()` returns a typed
`RetrievalResult` (`retrieval/rvgd.py:231`) with `table_ids`, `column_ids`, `term_ids`,
`metric_ids`, `few_shot_ids`, `note_ids`, `triggered_note_ids`, and `scores`. Emit from
that.

The obstacle is structural, not technical: `_resolve_tool` is a *dispatcher* that sees
the tool's arguments and the ledger entry, not the `RetrievalResult` that
`search_corpus` computed internally (`analyst/tools.py:279`). So phase 1 needs the tool
closure to stash its structured result somewhere the dispatcher can read, in the same
spirit as the ledger entry for governed tools. Add the ids per type with each id and its
name; nest them under `items` for consistency with C1.

**Frontend** · `step-row.tsx` `hasDetail` `case "search_corpus"` (`:124`) already tests
`["tables","few_shots","metrics","query"]`, so it needs no change if you keep those
count keys as integers (§7 again). Extend `StepDetail` `case "search_corpus"` (`:194`)
the same way as assemble.

### 5.2 Phase 2: per-channel attribution

Per hit, which channel ranked it and at what rank: BM25, dense vector, the fused order,
and whether it arrived through **grounding expansion** rather than ranking (a bound term
pulling in its target, a metric pulling its base table, a table pulling its columns).

This answers the question users actually have, and it needs a backend change beyond the
event: `retrieve()` **discards per-channel provenance after fusing**. `RetrievalResult.scores`
keeps one post-fusion number per id, and its docstring warns the scale differs by
channel (raw BM25 with no embedder, Reciprocal Rank Fusion values of ~1/(60+rank) with
one). Grounded additions are in the id lists but absent from `scores` entirely, so
"no score" already means "not ranked, added by grounding" and that much is derivable
today. True per-channel attribution requires keeping the pre-fusion orders.

Do not display the fused score as a percentage or a confidence. It is a bounded rank
artefact, not a probability.

---

## 6. C3 — the rendered context block

**Blocked on a decision that is not yours to make.** Ship C1, C2 and C4 without it.

`analyst/agent.py:771` computes `rendered = context.render()`. That string *is* the
`## Governed context` section of the system prompt. It reaches graph state as
`context_block` and provenance keeps `context_chars` and `context_hash`, so the
frontend can say "the agent received 21117 characters, fingerprint 33499efc" and
nothing more.

**Transport, if it ships: the stream, not `ChatState`.** `ChatState` is
`{messages, answer}` and round-trips through the checkpointer. Measured mean context
size on the 1351-question run is **19,537 chars** (curated arm) and **21,298** (SME
arm), so persisting it multiplies thread storage for a payload almost nobody opens
twice. The tradeoff is that custom events are ephemeral: after a reload the timeline
replays `messages` and `values.answer` from the checkpointer but **not** the events. If
durable review turns out to matter, add a fetch endpoint keyed by turn rather than
widening the persisted state.

**Egress is the blocker, and see §8** — the redaction seam you would expect to protect
this does not run on the stream.

---

## 7. The trap: counts are numbers and the frontend depends on it

The source plan's payload example shows `"tables": [ {...} ]`, replacing the count with
a list. Its prose says "alongside the existing counts". **The prose is right and the
example is wrong.** Implementing the example breaks a working row, silently.

`governed-bi-ui` · `lib/steps.ts:229`:

```ts
function countLabel(d: Record<string, unknown>, specs: [string, string][]): string {
  return specs
    .map(([key, noun]) => {
      const n = d[key];
      if (typeof n !== "number") return null;   // <-- an array lands here
      return `${n} ${noun}${n === 1 ? "" : "s"}`;
    })
    .filter((s): s is string => s !== null)
    .join(", ");
}
```

An array fails `typeof n !== "number"`, returns `null`, and is filtered out. So
`assemble` would render as `schema mondial_geo · ` with an empty count list, and
`search_corpus` would lose its counts too. No error, no warning, no test failure unless
someone asserts on the rendered string.

`hasDetail` (`step-row.tsx:115`) is the second victim: it only tests `!= null`, so it
would still return `true`, meaning the row stays expandable but expands to nothing
useful.

**The rule for this handoff: existing keys keep their existing types forever. New
information goes in new keys.** Hence the nested `items` object in C1 and C4. It also
survives `reduceSteps`'s shallow merge (§2.3), because `assemble` emits once per turn
and nothing else writes `items` on that row.

---

## 8. Egress: the stream bypasses the redaction the REST surface applies

Verified, and it is a live gap rather than a hazard introduced by this work.

`governed-bi` · `viz/presenter.py:820-843` redacts provenance before it leaves over
REST: result rows are emptied (`rows_redacted: True`) and **`reason` is nulled**
(`reason_redacted: True`). The comment states why: for `verdict="error"`, `reason` is a
raw `str(err)`, and libpq embeds the offending statement (`LINE 1: SELECT ...`), so a
guardrail message can echo question literals and PII.

The custom event stream does not pass through that function. `api/graph_app.py:174`
wires the emitter straight to the LangGraph writer:

```python
writer = get_stream_writer()
...
on_event=writer,
```

So `run_query` stream events carry `reason` and `sql` verbatim while the REST audit
surface redacts `reason` from the same ledger entry. The frontend already anticipates
the redacted form (`step-row.tsx:169` renders "reason withheld from the client" on
`reason_redacted`), which means the UI is written for a policy the stream does not
enforce.

Consequences for this work:

1. **Do not treat "the model already saw it" as an egress argument.** That reasoning
   would also have permitted `reason`, which the repo decided to redact on the surface
   that had a policy.
2. **C3 has no redaction seam to attach to.** Adding a ~20k-character prompt block to
   the stream would be the first time the full assembled prompt leaves the server, and
   the column descriptions and sample values inside it have never been reviewed for
   that purpose. `render()` is built on the `for_analyst()` corpus view so
   `governance.excluded` assets are already absent — necessary, not sufficient.
3. **The asymmetry itself should be fixed or recorded**, independently of C1–C4. Either
   the stream gets the same redaction pass, or the difference is written down as
   intentional with the reason. It is tracked in
   [open-work.md](../open-work.md) under Governance gaps.

C1, C2 and C4 phase 1 emit ids, names, counts, schema names and ranks. None of that is
row data or error text, so none of it is blocked on the above.

---

## 9. Corrections to the older docs

Three claims a reader would otherwise act on. Recorded so they are not re-derived.

### 9.1 `STAGE_ALIASES` does not exist

Both [serve-transparency.md](serve-transparency.md) §C2 and
[ui-frontend-handoff.md](../ui-frontend-handoff.md) say a new routing row costs "one
`STAGE_ALIASES` entry — no component change". Searched the whole UI repo: **there is no
such symbol.** It belonged to the deterministic stage stepper, which is gone;
`components/chat/serve-progress.tsx` now just delegates to `<AgentTimeline/>`.

The real cost is four small edits in two files (§3.3). An unmapped step *does* render,
which is why the claim survived, but it renders as the raw string `schema_route` with a
generic icon and **no expandable detail**.

### 9.2 The C1 payload example contradicts its own prose

See §7. Follow the prose.

### 9.3 `PromptContext` views carry fewer ids than the plan assumes

`FewShotView`, `TermView` and `MetricView` have **no `id` field**
(`analyst/context.py:106-124`), and `injected_note_ids` carries ids without
`normative_force`. §4.2 says what to do for each. The plan's `{id, question}` for
few-shots is not directly available.

---

## 10. Order of work

1. **Surface what already arrives.** `provenance.schema_route_channel` and
   `provenance.licensed_tables` are both on the `final` event today. Zero backend
   change. A silent fall back from embedding to BM25 roughly halves routing recall
   (recall@3: 0.70 embedding, 0.35 BM25), so that field belongs on the audit surface
   regardless of everything else here.
2. **C2**, the `schema_route` event. Highest value per line.
3. **C1**, identity in `assemble`. Small, and it is the most-requested view.
4. **C4 phase 1**, `search_corpus` hits.
5. **C3**, the context block, once the egress posture in §8 is decided.
6. **C4 phase 2**, per-channel attribution, which needs `retrieve()` to stop discarding
   channel provenance.

Steps 2, 3 and 4 are independent of each other and can be split across people.

## 11. Definition of done

A user can answer all of the following without server access:

- Which schema answered, and from what shortlist, in what rank order.
- Whether routing ran on the embedding channel or fell back to BM25.
- Whether the pick was a real model decision or a `fallback` substitution.
- Whether a candidate was shown to the picker truncated, and at what caps.
- Which tables, few-shots and notes reached the prompt.
- Which of those tables were *retrieved* versus merely *reachable via a join*.
- What each retrieval pass returned.

Plus two invariants:

- **No new field is persisted through the checkpointer.**
- Every new payload has a test that round-trips it through `json.dumps`, because
  `GovEventStream` swallows serialisation failures (§2.1) and a dropped event is
  indistinguishable from a stage that never ran.

## 12. Constraints

- **Payload size.** C1 and C2 add a few hundred bytes per turn. C3 adds ~20k
  characters; fine on a local socket, measure before any remote deployment.
- **Ephemerality.** Custom events do not survive a page reload. Accepted; the fix, if
  ever needed, is a fetch endpoint, not a wider `ChatState`.
- **Ordering.** `schema_route` must carry a `seq` below `assemble`. It will naturally,
  since routing precedes assembly in the same node, but assert it (§3.2) rather than
  assume it.
- **PINs stay additive.** If schema-tier note coverage is ever restored (`open-work.md`
  R2), a triggered note may prepend to the shortlist but must never evict a ranked
  candidate. A UI that shows `candidates` in rank order will make a violation visible,
  which is a reason to ship C2 before that work, not after.
