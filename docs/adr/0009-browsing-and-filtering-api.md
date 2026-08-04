# 0009: The browsing, filtering and relationship API

- **Status:** Accepted (2026-08-04). Implemented in `api/routes.py` + `api/browse.py`.
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

The UI browses 13 981 assets over 57 schemas, and the read surface it had was three flat
dumps. Measured against the live engine on 2026-08-04:

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
      "columns": [ {"name": "schema", "kind": "string", "ops": ["eq","contains","present"],
                    "sortable": true, "identifier": false, "why": "…"} , … ] }
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

`/knowledge-graph` keeps returning the same payload for now, and saying so is better than two
walks that drift ([0007](0007-http-surface-and-the-ui-contract.md) already records why).

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

### D5 — Filtering runs server-side over the loaded corpus, not in the database

The corpus is already in memory in the session — it is what retrieval runs on. Filtering it
is a scan over at most 13 981 objects, which is microseconds, and it means the browse surface
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
- `/corpus/assets` and `/schema` stay, unchanged, as the pre-scope fallback. They are the
  only reason the UI works against an engine without these routes.
