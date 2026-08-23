# 0009: The browsing, filtering and relationship API

- **Status:** Accepted (2026-08-04); **amended the same day** -- Amendment 1 records the
  audited, sufficient set and the seven changes that got there. Implemented in
  `api/routes.py`, `api/browse_routes.py` + `api/browse.py`.
- **Deciders:** project owner + design session (2026-08-04)
- **Scope:** the read surface the UI browses the semantic layer with — filtering and
  sorting over any asset type, the lean table catalog, one table's detail, and the
  bounded relationship subgraph. **Not** chat, not the audit surface ([0007](0007-http-surface-and-the-ui-contract.md),
  and `/audit/*` from decision #51), not corpus writes.
- **Related:** [ADR 0005](0005-v2-memory-layer-and-faceted-retrieval.md) §1 declares the
  asset types this derives from; [ADR 0008](0008-identifiers-end-to-end.md) D1 is why an
  asset id and a physical name are two different columns here.

---

## Context

On 2026-08-04 the UI browsed 13 981 assets over 57 schemas, and the read surface it had was
three flat dumps. Measured against the live engine that day (the asset count is a property of
the corpus, and the corpus has been rebuilt since):

| route | bytes | note |
| --- | --- | --- |
| `GET /corpus/assets` | **2 253 297** | every asset, every time; the browser filters client-side |
| `GET /schema` | **936 637** | 656 tables with columns inline, 1.7 s |
| `GET /graph` | 165 793 | 656 nodes / 556 edges — cheap to send, **not** cheap to lay out |

So "the page is slow" has two different causes and they need different fixes. The dumps are
a *transfer and filtering* problem. The graph is a *layout* problem: dagre is synchronous and
runs in the browser, and 656 nodes is not a diagram a person can read even once it lands.

Four routes the client was already written against returned **404**: `/schema/summary`,
`/schema/{id}`, `/search`, `/columns/{id}`. `capabilities.can_scope` and `can_search` reported
`false`, which is why nothing was visibly broken — the UI has a documented fallback to the
flat dumps for exactly this case, and the fallback is what we were measuring above.

---

## Decision

### D1 — One filter contract, and its columns are **derived**

```
GET /corpus/fields?type=table
  → { "type": "table",
      "columns": [ {"name": "schema", "kind": "string",
                    "ops": ["contains","eq","neq","present"],
                    "sortable": true, "identifier": false} , … ] }
```

The column list comes from the asset dataclass's own fields plus
`register/assets.ASSET_REGISTER`, never from a list written in the route. A field added to
`corpus/schema.py` becomes filterable with **no change to this API and no change to the
UI** — the UI renders its filter row from this response. A hand-maintained column list here
would be the drift `register/` exists to end, and it would drift silently because a missing
column looks like a column somebody chose not to expose.

```
GET /corpus/rows?type=table
      &where=schema:eq:airline
      &where=summary:contains:carrier
      &where=row_count:gte:1000
      &sort=id&order=asc&offset=0&limit=50
  → { "rows": [...], "total": 12, "offset": 0, "limit": 50,
      "columns": [...], "unknown_where": [] }
```

**Repeated `where=field:op:value` rather than one query parameter per field.** The parameter
set must not grow with the field set: `?schema=…&summary_contains=…&row_count_gte=…` needs a
new parameter, a new zod field and a new UI control for every asset field, which is three
places to forget. One opaque triple is parsed once, validated against `/corpus/fields`, and
**a `where` naming an unknown field or operator is returned in `unknown_where` rather than
ignored** — a filter that silently does nothing shows the user a filtered-looking list that
is not filtered, which is the same class of defect as a gate that never fires.

`total` is the count **after** filtering and before pagination. Returning the unfiltered
total beside a filtered page is how a reader concludes their filter did nothing.

**Rejected:** GraphQL — a second query language for one client, and the filtering it buys is
this triple. **Rejected:** a POST body — a filtered view should be a URL you can paste into
a ticket, and a POST is neither linkable nor cacheable.

### D2 — The relationship graph is bounded, and says what it cut

`/graph` accepts the scope the client already sends:

```
GET /graph?schema=airline&focus=airline.Airlines&radius=1&node_budget=120&kinds=table,join
  → { "nodes": [...], "edges": [...],
      "meta": {"n_nodes": 7, "n_edges": 6, "n_total_nodes": 656,
               "truncated": false, "dropped": 0, "node_budget": 120,
               "scope": {...}} }
```

- `schema` narrows to one namespace; `focus` + `radius` walks outward from one table over the
  join graph; `kinds` filters node types.
- `node_budget` bounds what is returned, and **`truncated` / `dropped` are the point**. A
  view that quietly renders 120 of 656 nodes reads as complete coverage, and this repository
  has published a number on top of exactly that shape before. If the budget bites, the
  response says so and the UI shows it.
- With no scope at all, the response is still bounded — the default budget applies. There is
  no request that returns an unlayoutable graph.

`/graph` and `/knowledge-graph` are two payloads over one scope contract. `_graph_payload()`
is the ER view: table nodes only, one edge per declared join pair, carrying `on`, `cardinality`,
`join_ids` and `n_relationships`. `_knowledge_payload()` is the semantic view: every asset kind
in `_SEMANTIC_NODE_KINDS` as a node, columns re-pointed onto their owning table, and edges from
the reference closure labelled `relation` (`join` / `measures` / `grounds` / `exemplifies` /
`belongs_to` / `has_column`). Both go through the same `subgraph()` bound, so scope, budget and
`truncated` / `dropped` behave identically; only the walk differs. Two walks that drift is the
hazard [0007](0007-http-surface-and-the-ui-contract.md) records, and what keeps them from
drifting is that both read `CorpusStructure` rather than each re-deriving the corpus.

### D3 — The catalog is lean; detail is fetched per table

`GET /schema/summary?schema=&limit=&offset=` returns `{total, items[]}` where an item is the
table plus **lean** columns (`physical_name`, `physical_type`, `role`, `reliability`,
`excluded`) — enough for a browser row and a suspect/excluded badge, not the summaries and
bodies. `GET /schema/{table_id}` returns the full `TableView` for the one table a detail
sheet opened.

Splitting these is what removes the 937 KB: the catalog is the common request and carries no
prose, and the prose is fetched once for the one table someone clicked.

### D4 — `can_scope` becomes true, and it is an **observation**

`capabilities` reports what the process can actually do. It said `can_scope: false` because
the four routes did not exist; with them built it reports `true`, and the UI's scoped hooks
engage. `can_search` stays **false**: `/search` is not built, and reporting a search the
server cannot do would make the omnibox blame the corpus for an empty result. The flag is
flipped by building the thing, never to unlock a UI path.

> **Noted 2026-08-22: `can_scope` is not an observation, it is a literal.**
> `api/routes.py:356` returns `"can_scope": True` hardcoded. The value happens to be correct — every
> scoped route this decision describes is mounted, as `docs/openapi.json` shows — but nothing reads
> the route table to decide it, so if one of them were deleted the flag would keep reporting `true`.
> Its neighbours in the same dict *are* derived: `can_stream = served_graph_declared()` walks
> `langgraph.json` and stat()s the module, and `checkpoint_durable` /
> `hitl_survives_process_restart` come from `durable_checkpointer_configured()`. This decision's
> rule — "flipped by building the thing, never by editing the line" — is therefore stated for
> `can_scope` and enforced only for the others.

### D5 — Filtering runs server-side over the loaded corpus, not in the database

The corpus is already in memory in the session — it is what retrieval runs on. Filtering it
is one pass over the loaded assets — order 10⁴ objects, microseconds — and it means the browse surface
and the retrieval surface are looking at **the same assets**. Pushing these filters into SQL
would make the corpus browser query the *lake* rather than the semantic layer, so an asset
that failed to load would still appear in the browser — the corpus would look complete
because the database is.

---

## Consequences

- The UI's filter row is generated, so a new asset field is filterable without touching
  TypeScript. The cost is that the UI cannot assume a column set at compile time; it reads
  `/corpus/fields` and renders what it is told.
- `unknown_where` gives the client a way to show "this filter was not applied", which it must
  do — otherwise D1's honesty property is only true of the server.
- Bounded graphs mean a user *can* be shown a partial relationship view. That is acceptable
  only because `truncated` is in the payload and rendered; if that ever stops being rendered,
  the budget becomes a silent lie and should be removed instead.
- ~~`/corpus/assets` and `/schema` stay, unchanged, as the pre-scope fallback.~~ **Superseded
  by Amendment 1 D11:** `/schema` is deleted, and the "pre-scope fallback" framing was itself
  the problem -- keeping a second, unbounded projection of every table alive as insurance is how
  the UI spent months fetching 936 KB to render a card, and how two shapes for one thing got the
  chance to disagree. `/corpus/assets` survives, unbounded, on notice.

---

## Amendment 1 — the sufficient set (2026-08-04)

An audit of this surface against the frontend's actual needs
(`plans/api-sufficiency-audit-2026-08-04.md` (deleted))
cross-referenced every client method in `lib/api-client.ts` against every route. It found
**zero field-level breaks** -- no route omitted a field the client requires -- and three
truncation defects, one absent route, and one capability flag that described the server rather
than what the mounted client could do. All are now closed. The seven changes:

**D6. The applied scope echo must be complete.** `meta.scope` carried `schema`, `focus`,
`radius` and `kinds` while `node_budget` sat one level up in `meta`. The client compares the
echo field-for-field against what it asked for and re-scopes the payload itself when they
differ -- then rebuilds `meta` from its own pass, overwriting `truncated: true, dropped: 7827`
with `false`/`0`. So the budget was never honest, and D2's whole point was unreachable: the
"expand" banner could not appear, and 150 of 7 977 nodes read as full coverage. The echo is now
complete, and the client sends the resolved scope so the comparison can succeed.

**D7. Truncation is connectivity-first, not alphabetical.** With no `focus` there is no centre,
and the budget was spent in id order. An alphabetical prefix of 150 out of 7 977 nodes rarely
contains *both* ends of any edge: measured, the unscoped semantic graph returned 150 nodes and
**zero** edges, which the client drew as one 18 000-pixel column of unrelated cards. A
relationship view that shows no relationships is worse than a truncated one -- it reads as a
corpus with no relationships. The budget now grows neighbourhoods from the best-connected seed.
Same input, after: 150 nodes, 166 edges. Isolated nodes sort last, so an edgeless corpus
degrades to the old id order rather than to nothing.

**D8. A join leaving the scope is a destination, not a silence.** Both endpoints must be in
scope for a line to be drawn, so a cross-namespace join vanished from a scoped view and its
table drew as isolated -- a claim about the schema rather than about the window. `subgraph` now
returns `boundary`: one stub per (in-scope table, far table), carrying the predicate. No
severity attaches; a cross-schema join executes, so the far end is somewhere to go. Only
join-bearing edges qualify -- a term grounding a column across namespaces is not navigable.

**D9. A default that truncates the only fetch anybody makes is not a page bound.**
`/schema/summary` defaulted to `limit=200` against 656 tables, and no consumer pages: the
namespace rail, the table browser and the client-side search index each read the whole list. So
the default hid 456 tables, and `can_search: false` pointed at an index that had never been sent
most of the corpus. The default is now the clamp ceiling (1 000), and `offset`/`limit` are echoed
as applied -- `total: 656` beside 200 items cannot otherwise be told apart from the end of the
list, and only one of those readings is the caller's to fix.

**D10. Column ids are sent, never derived.** The client computed `col_<table>_<physical>` --
v1's scheme -- while [0008](0008-identifiers-end-to-end.md) D1 mints
`{table_id}.{slug(physical_name)}`, where `slug` hashes any name needing sanitisation. So every
id the column panel asked for was one the engine had never heard of, and the scheme could not be
honestly reimplemented in TypeScript anyway: that would be a second answer to what identifies a
column. Every column projection now carries `id`; the client reads it. D4's rule ("references
are asset ids") only holds if something supplies them.

**D11. One route per thing.** Two routes deleted, both second projections with no unique caller:

- `GET /schema` -- 936 637 bytes inlining every column of every table, and the same tables
  `/schema/summary` returns lean. Its last consumer, the ER diagram, needed exactly two fields
  the lean column lacked (`nullable`, `is_unique`); those are now on it. Per-column prose is
  still available for the **one** table someone opens, from `/schema/{table_id}`. Two
  projections of a table can disagree, and this pair already had.
- `GET /audit/turns/{turn_id}` -- orphaned; nothing ever called it. Its two unique fields moved
  onto `/audit/turns/{id}/trace`, fetched at the same moment for the same turn: `record`, and
  `undeclared_keys` -- the only signal that a producer writes a field **nobody declared**. The
  trace's `stages` is derived *from* the register, so it structurally cannot show that, and looks
  complete either way.

> **The two surviving `/audit/turns` routes changed store, not shape, on 2026-08-18**
> ([ADR 0014](0014-one-conversation-store.md) §3). They read a turn out of LangGraph thread state
> (`api/thread_turns.ThreadTurnLog`) rather than out of `runs/serve/*.jsonl`, which is deleted.
> Payloads are byte-identical by intent, which is what lets `npm run check:api` be the regression
> test for the swap. One value changed meaning without changing key: `meta.log_dir` is now the
> path of the conversation database, not a directory of files. D2's argument that the list owes a
> truncation field is now *more* owed, not less: the reader caps its scan at 1 000 threads and the
> wire has nowhere to say so.

And one route added: **`GET /columns/{column_id}/related`**, the only genuinely absent one. The
column detail panel opened on it and the client's query declares `retry: false`, so it went
straight to an error state. An unknown id answers **200 with `column_resolvable: false`**, not
404: the panel is reached by clicking a column, so an id that does not resolve means the id
scheme drifted, which is worth saying rather than rendering as a broken panel. Joins are matched
by *parsing* the ON clause -- `id` occurs inside `customer_id`, and the panel's claim is that a
relationship uses this column.

**D12. A capability flag describes what the mounted client can do.** `can_clarify` reported
`true` whenever a model was attached. The server half is genuinely built -- `ask_user` binds,
`POST /chat` surfaces the interrupt, `POST /chat/resume` accepts the answer -- but the flag is
the switch that mounts the interrupt prompt, and with `can_stream: false` the UI mounts
`<RestChat/>`, whose transport has no clarification state and no resume call anywhere in it. So a
question could be asked, displayed nowhere, and answered by nobody while the graph stayed paused.
It is now `can_stream and agent_model is not None`, and it flips by building the client's half --
never by editing the line.

**It has since flipped, exactly that way.** [ADR 0010](0010-live-stage-events.md) built the stage
events, `can_stream` became true, the UI mounts `<StreamChat/>` -- which does have a clarification
pair -- and `can_clarify` came true as a consequence. The expression is unchanged, which is the
whole claim of this decision surviving contact with the thing it was waiting for.

> **And then the loser was deleted, 2026-08-18 ([ADR 0014](0014-one-conversation-store.md)).**
> `POST /chat` and `POST /chat/resume` are gone, so the two clauses above are the record of a
> transport rather than a description of one. `can_stream and agent_model is not None` is still
> the expression and still correct -- there is now only one transport for it to be true of.

### The sufficient set: 14 reads and one write

`GET /capabilities` | `GET /livez` | `GET /schema/summary` |
`GET /schema/{table_id}` | `GET /corpus/fields` | `GET /corpus/rows` |
`GET /columns/{column_id}/related` | `GET /graph` | `GET /knowledge-graph` |
`GET /audit/turns` | `GET /audit/turns/{id}/trace` | `GET /audit/corpus` -- and, since
2026-08-18, **that is the whole set** *(superseded — a write has since been mounted; see the
2026-08-22 correction below)*: the chat pair `POST /chat` and `POST /chat/resume` is
deleted ([ADR 0014](0014-one-conversation-store.md)), so every route this app mounts is a read and
none of them needs a model. A turn is served only by the graph `langgraph.json` mounts, over the
platform's own `/threads/{id}/runs/stream`. `make_app` lost its `graph` parameter with the pair,
because nothing left here calls one.

`GET /health` was in this list, which is why the list ran to **thirteen** under a heading that
said twelve. It is deleted ([0007](0007-http-surface-and-the-ui-contract.md) Amendment 1): it and
`/audit/corpus` projected the same session fields, and the survivor is the one that keeps `fatal`
apart from `degradations`. The heading is now the count.

> **Added 2026-08-19.** `GET /clarifications/pending` is a fourteenth read: unanswered
> `ask_user` prompts, oldest first, out of interrupt state
> (`api/clarification_routes.py`). `/corpus/assets` was already mounted beside the twelve.
> The heading above is the 2026-08-18 count, not the current one.

> **Correction, 2026-08-22. "Every route this app mounts is a read" is false, and the route that
> makes it false is unauthenticated.** `POST /turns/{turn_id}/raised` is mounted
> (`api/clarification_routes.py:66`) and declared in `docs/openapi.json`. It writes: the handler
> validates `kind` and a `RAISED_NOTE_MAX_CHARS`-bounded `note`, resolves the turn's `thread_id`
> through `turn_log.get_turn`, and `api/raised_write.py:99` appends the row onto checkpointed
> `ServeState.raised` via `aupdate_state(as_node="raise_note")`. It is 409 on an in-flight or
> paused thread and it resumes nothing, but it does persist attacker-supplied text into the
> conversation store. The 2026-08-19 note above patched the *count* of reads and never mentioned
> that a verb other than `GET` had appeared.
>
> Nothing on this surface authenticates — `api/routes.py:37-45` records A7 as knowingly open and
> the credential middleware as deliberately removed — so **any caller that can reach the port can
> file a note against any turn**. The queue an operator reads at `/clarifications/pending` is
> therefore caller-writable, and the only bound on how much a single caller can grow a store
> nothing sweeps is `RAISED_NOTE_MAX_CHARS` per note. `api/clarification_routes.py:9-24` states
> the same thing in the code's own words, including that the POST "deliberately cannot *act*" —
> it reaches neither `command.update` nor `POST /threads/{id}/state`.
>
> **Neither clarification route goes through `api/visibility.py::visible()`.** Its only callers are
> `api/browse_routes.py`'s session dependency and the four `visible(get_session())` call sites in
> `api/routes.py`, so `GET /clarifications/pending`
> and `POST /turns/{turn_id}/raised` both sit outside the ADR 0012 grant withholding — the GET as a
> read path around a grant, the POST as a write path around one, which is exactly the consequence
> `clarification_routes.py`'s docstring says must be carried forward "under a real `AccessPolicy`".
> Tracked as an open item in `docs/open-work.md`; this note records the exposure, not a plan.
>
> **The inventory of record is `docs/openapi.json`, not this list.** It has 15 paths — the fourteen
> `GET`s (`/livez`, `/capabilities`, `/corpus/assets`, `/corpus/fields`, `/corpus/rows`,
> `/schema/summary`, `/schema/{table_id}`, `/columns/{column_id}/related`, `/graph`,
> `/knowledge-graph`, `/audit/turns`, `/audit/turns/{turn_id}/trace`, `/audit/corpus`,
> `/clarifications/pending`) plus the one `POST`. The list above is kept as the 2026-08-04 record
> of what the audited *sufficient* set was; it is not maintained as an inventory, because it has now
> drifted from the mounted surface three times. The heading is the current count.

`GET /search` is deliberately **not** built: `can_search: false` is the honest answer and the
client's index over the lean catalog works. `GET /corpus/assets` survives unbounded, on notice:
its three consumers filter it locally over 2.25 MB, and `/corpus/rows` already filters, sorts and
pages the same assets -- but repointing them is UI work, not an API gap.

### What checks this from now on

The contract had been reconciled by hand three times and drifted three times, because nothing
compared the two sides. The frontend now has `npm run check:api`: it fetches every route from
a live engine and validates each response against the client's **real** zod schemas, reporting
both parse failures (a blank page) and silently stripped keys (a page that renders missing data
and looks fine). It found a route four browser sessions had not -- `/corpus/assets` answering 200
with two absent required fields, taking three components down at once. It needs a live engine and
a loaded corpus, so it sits beside `npm run lint` rather than in CI.
