# UI foundation — Phase 1 backend endpoints (build plan)

English only. The additive, backward-compatible backend slice the UI can consume
**immediately**, per [ui-frontend-handoff.md](../ui-frontend-handoff.md) §12
("build now") and the frontend's `DESIGN_QUESTIONS.md` §4.1 / Phase 1.

**In scope:** lean/scopeable read projections + additive capability flags. Pure
projections of data the engine already computes (`viz.presenter`); no new
profiling, no new storage.

**Out of scope (later):** the `db` → `schema` rename (D15, gated + breaking);
multi-schema serving; the focus/radius/`node_budget` bounded graph and the
`meta`/`boundary` envelope (Phase 2); server `/search` (deferred, client Fuse is
the default). This slice keeps the flat `db` field verbatim.

## Endpoints

1. **`GET /schema/summary?db=&limit=&offset=`** → `SchemaSummaryResponse`
   `{ total, items: TableSummaryResponse[] }`. Lean catalog for the virtualized
   table list + the client search index. Optional `db` filters to one namespace;
   `limit`/`offset` paginate (default: all, offset 0). `total` is the count
   **before** pagination. **Biggest payload win, zero new data.**
2. **`GET /schema/{table_id}`** → the existing `TableResponse` (full detail),
   `404` when the id is unknown. Fetched lazily on detail-sheet open. `id` alone
   is globally unique, so no compound key.
3. **`GET /capabilities`** gains `can_scope: true` and `can_search: false`
   (additive). `can_scope` advertises the summary/detail/scoping routes;
   `can_search` stays false (no server FTS).
4. **`GET /schema`** gains optional `?db=&limit=&offset=` (additive). Param-less
   behaves **exactly** as today (the backward-compat full dump).

## Shapes

```jsonc
// TableSummaryResponse — heavy fields (sample_values, evidence, description) dropped
{ "id": "tbl_beer_factory_customers", "physical_name": "customers", "db": "beer_factory",
  "row_count": 554, "n_columns": 8, "excluded": false, "has_suspect": true,
  "provenance_status": "certified",
  "columns": [ { "physical_name": "id", "physical_type": "INTEGER",
                 "role": "primary_key", "reliability": "ok", "excluded": false } ] }
```

`has_suspect` = any column `reliability == "suspect"`; `n_columns` = column count.
Column summary rows carry only `physical_name`, `physical_type`, `role`,
`reliability`, `excluded` (enough for search/preview; full detail via `/schema/{id}`).

## Backward-compatibility invariants (must hold)

- No existing route path, method, or response **field** removed or renamed.
- New capability fields are additive; new endpoints are new paths.
- Param-less `/schema`, `/graph`, `/knowledge-graph`, `/chat`, etc. are byte-for-byte unchanged.
- The `db` field name is unchanged (the D15 rename is a separate, later change).

## Tests

- `/schema/summary`: shape, `db` filter, `limit`/`offset` pagination + `total`,
  and that heavy fields are absent.
- `/schema/{id}`: found returns full `TableResponse`; unknown id → `404`.
- `/capabilities`: exposes `can_scope=true`, `can_search=false`.
- `/schema` param-less: identical to the current response (regression guard).

## Where it lands

`api/schemas.py` (new response models), `viz/presenter.py` (a lean
`table_summaries` projection + a by-id lookup), `api/app.py` (the routes),
`api/stack.py` + `CapabilitiesResponse` (the two flags), and the API test module.
Re-export [openapi.json](../openapi.json) after.
