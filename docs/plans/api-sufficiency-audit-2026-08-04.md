# API sufficiency audit — the HTTP surface against `governed-bi-ui`

**Date:** 2026-08-04 · **Backend:** `governed-bi` @ branch `v2` · **Client:** `../governed-bi-ui`
· **Method:** static. No server was started; no file outside this one was touched.

Scope: every `get(`/`getLive(`/`post(` in `governed-bi-ui/lib/api-client.ts` (17 methods) against
every `@app.*` / `@router.*` in `src/governed_bi/api/` (17 routes), field by field in both
directions, plus the `useStream` chat path.

---

## 1. Verdict

**No — but it is close, and nothing on the list is large.** The field-level contract is in far
better shape than the route-level one: I reconciled every declared zod field against every
emitted key on all 15 routes the client actually calls and found **zero `BREAKS`** — no
client-required field is absent from any server payload. What is insufficient is (a) one route
that does not exist at all and takes a panel down (`GET /columns/{column_id}/related`), and (b)
**three silent-truncation defects**, which are worse than the missing route because each one
renders a partial corpus as a complete one: `GET /schema/summary` returns 200 of 656 tables and
the client never asks for more; `meta.scope` omits `node_budget`, so the client's
`engineScopeMatches` never fires and **overwrites the server's honest `truncated`/`dropped`
with `false`/`0`**; and `GET /corpus/assets` and `GET /schema` are unbounded dumps.

Shortest path to sufficient, in dependency order — five backend edits, one of them one line:

1. `browse.py:384` — put `node_budget` in `meta.scope`. One line. This alone restores the
   truncation banner on both graph tabs, which ADR 0009 D2 says is the precondition for the
   budget not being a lie.
2. `browse_routes.py:160` — default `/schema/summary` `limit` to the clamp ceiling (1000) and
   echo `offset`/`limit`. Unblocks the namespace rail, the table browser and the Fuse index,
   all of which currently see the alphabetically-first 200 table ids.
3. Add `nullable` + `is_unique` to `_table_summary`'s lean columns, then **delete `GET /schema`**
   — the ER diagram is the only consumer of the 936 KB dump and needs exactly those two fields
   beyond what the lean column already carries.
4. Build `GET /columns/{column_id}/related` (shape in §4).
5. Decide the clarification question: either declare `clarification` in the client's
   `answerViewSchema` and wire `useRestChat` → `POST /chat/resume`, or report
   `can_clarify: false` while `can_stream` is false. Today `/capabilities` reports
   `can_clarify: true` on a transport that cannot render or answer a clarification.

---

## 2. The table

Backend paths are relative to `src/governed_bi/api/`; client paths to `governed-bi-ui/`.

| # | Need | Consumer (file:line) | Route (file:line) | Verdict | Detail |
|---|---|---|---|---|---|
| 1 | What the server can do (gates every other decision) | `hooks/queries.ts:23`, `components/chat/chat-panel.tsx:30` | `routes.py:84` `GET /capabilities` | **OK** | 10 keys emitted, 7 required + 3 optional declared (`lib/schemas.ts:36-53`). `can_scope`/`can_search`/`can_clarify` all declared. `dialect` is `-> str` (`datasource/postgres.py:56`); `model` is `knobs_resolved.get("llm_model")` → `str \| None`. |
| 2 | Corpus health cards + findings list | `components/health/health-overview.tsx:36,44,59,70-73,145-157` | `routes.py:134` `GET /health` | **OK** | All 6 required + both optional (`n_fatal`, `n_degradations`) present (`routes.py:148-165` vs `lib/schemas.ts:57-76`). |
| 3 | Full table+column detail, flat | `components/schema/er-diagram.tsx:222-223`, `hooks/queries.ts:69-71` | `browse_routes.py:54` `GET /schema` | **UNPAGED** | **656 tables** (measured: `find corpora/gold-semantic-layer-20260804 -path '*/tables/*.yaml' \| wc -l` = 656). ADR 0009 measured the response at **936 637 bytes / 1.7 s**. No `limit`/`offset` parameter exists. Field set matches `tableViewSchema` exactly. |
| 4 | Lean catalog → namespace rail, table browser, Fuse index | `hooks/queries.ts:41-52,105-132`, `components/schema/table-browser.tsx:294,299-301`, `app/schema/page.tsx:41-42` | `browse_routes.py:158` `GET /schema/summary` | **UNPAGED** | Shape is **OK**. The defect is the bound: server default `limit=200` (`browse_routes.py:160`), and `api.schemaSummary` is called with `{schema}` only (`hooks/queries.ts:47`) — no `limit`, no `offset`, no pagination anywhere in `table-browser.tsx`. So **200 of 656 tables** reach the client, sorted by `a.id` (`browse_routes.py:176`). `total: 656` is sent and rendered nowhere (`table-browser.tsx:322` counts `rows.length`). Consequence: the rail shows the namespaces of the alphabetically-first 200 ids, not 57. |
| 5 | One table's full detail for the sheet | `hooks/queries.ts:60-76`, `components/schema/node-detail-sheet.tsx:298` | `browse_routes.py:184` `GET /schema/{table_id}` | **OK** | Same `_table_view` as #3, so the dump and the detail cannot disagree. 404 on a non-table (`browse_routes.py:195`) → `ApiError`, which is what the client wants. |
| 6 | Server-ranked search | `hooks/queries.ts:142-151`, `components/schema/schema-search.tsx:41,50,56-60` | NONE | **MISSING** (gated) | No `/search` route. `can_search: false` (`routes.py:122`) so `useServerSearch` is `enabled: false` and never fires. Honest per ADR 0009 D4 — **but** the Fuse fallback indexes `useCatalog`, i.e. the 200 tables of #4, so the fallback is currently truncated too. |
| 7 | Semantic graph (all asset kinds) | `hooks/queries.ts:79-87`, `components/schema/knowledge-graph.tsx:412` | `routes.py:669` `GET /knowledge-graph` | **LOSSY** | Node/edge fields match (`routes.py:351-393` vs `lib/schemas.ts:162-183`); `kind` values are exactly `graphNodeKindSchema`'s 7 (`routes.py:298-300`). Two losses: (a) `meta.scope` omits `node_budget` — see #9; (b) `boundary` never emitted — see #10. Measured node count: 7 977 (656 tables + 928 joins + 399 metrics + 994 terms + 5 000 few-shots, counted on disk). |
| 8 | ER graph (tables + joins) | `hooks/queries.ts:90-98`, `components/schema/er-diagram.tsx:220` | `routes.py:633` `GET /graph` | **OK** | All 7 required edge fields and all 6 required node fields present (`routes.py:243-287`), including `join_ids` + `n_relationships`, which `er-diagram.tsx:327` renders. `label`/`kind`/`provenance_status` on the node are stripped by `erGraphNodeSchema` — harmless, the client gets them elsewhere. |
| 9 | "This graph is truncated — expand the budget" | `components/schema/er-diagram.tsx:389-393,439`, `components/schema/knowledge-graph.tsx:569-570,687`; check at `lib/graph-scope.ts:50-63` | `browse.py:376-390` (`meta` in `subgraph`) | **LOSSY — highest-impact** | The server computes the honest answer (`truncated`, `dropped`, `n_total_nodes`, `n_matched_nodes`, `node_budget`) and the client discards it. `engineScopeMatches` (`lib/graph-scope.ts:61`) requires `applied.node_budget === requested.nodeBudget`; `meta.scope` (`browse.py:384-389`) contains only `schema`/`focus`/`radius`/`kinds`. So it returns **false on every request**, both components fall through to `applyErGraphScope`/`applyKnowledgeGraphScope`, and `lib/graph-scope.ts:199` / `:22-37` rebuild `meta` from the *already-truncated* payload: `truncated: kept.length < candidates.length` → `120 < 120` → **`false`**, `dropped: 0`. The banner is unreachable and 120 of 7 977 nodes reads as complete coverage. This is precisely the failure ADR 0009 D2 says makes the budget "a silent lie". |
| 10 | Navigable cross-schema boundary stubs | `components/schema/er-diagram.tsx:351,451-476`, `components/schema/knowledge-graph.tsx:571,743-750` | NONE | **MISSING** | Neither `_graph_payload` nor `subgraph` emits `boundary`. Declared `.nullish()` (`lib/schemas.ts:238`) so nothing breaks; the panel silently never appears. The client synthesises stubs itself in the fallback path (`lib/graph-scope.ts:176-192`), which only works because #9 forces that path — fixing #9 removes the client's stubs and leaves the panel empty unless the server emits them. |
| 11 | Every asset as a row (fuzzy search tab) | `hooks/queries.ts:153-155`, `components/corpus/asset-browser.tsx:67,214,317-329` | `routes.py:181` `GET /corpus/assets` | **UNPAGED + LOSSY** | 5 required fields all present (`routes.py:199-202`). No `limit`/`offset`/`total`. Register has 8 asset types including `column` (`register/assets.py:47-54`), so this returns every asset; ADR 0009 measured **2 253 297 bytes**. Measured on disk: 8 034 asset files (57 schemas, 656 tables, 994 terms, 928 joins, 399 metrics, 5 000 few-shots) **plus** column assets nested inside table YAMLs, which I did not count — ADR 0009 records 13 981 total from a live run. **LOSSY:** the route sends `schema` (`routes.py:200`); `assetRowSchema` (`lib/schemas.ts:284-290`) does not declare it, so zod strips it and `asset-browser.tsx:110` rebuilds the schema filter from `useCatalog` instead. |
| 12 | Everything touching one physical column | `hooks/queries.ts:163-170`, `components/schema/column-related.tsx:31,43,87-92,104-160`, mounted at `components/schema/node-detail-sheet.tsx:143-144` | NONE | **MISSING — the one dead panel** | No `/columns/{column_id}/related` route exists (verified against all 17 decorators). `useColumnRelated` has `retry: false`, so opening a column in the detail sheet 404s immediately → `column-related.tsx:39` renders the error state. Client id scheme is `col_<table_id minus 'tbl_'>_<physical_name>` (`lib/columns.ts:10-13`); whether that matches v2's `corpus.ids` derivation is a separate check the route will have to settle. |
| 13 | A non-streaming answer | `hooks/use-rest-chat.ts:63-67`, `components/chat/chat-panel.tsx:33` | `routes.py:397` `POST /chat` | **LOSSY** | Request `{question, session_id, history}` — all three read (`routes.py:426-431`). Response is `stamp`'s `Answer` verbatim (`serve/nodes/stamp.py:280-287`) and matches `answerViewSchema` field for field; `outcome` values are exactly the client's 5-member enum (`register/stages.py:194-198` vs `lib/schemas.ts:392`). Three keys the server sends and zod strips: **`clarification`** (`routes.py:571,578`) — see #14; **`audit_logged`** and **`audit_error`** (`routes.py:602-604`) — so "the turn was served but the audit log could not be written" is invisible, which is the distinction `trace_store.py:16-18` exists to preserve. |
| 14 | Ask and answer a clarification | `components/chat/clarification-prompt.tsx:40-107`, `hooks/use-stream-chat.ts:143,153-185`, `lib/clarification.ts:28-38` | `routes.py:442` `POST /chat/resume`; interrupt at `serve/tools.py:228-235` | **ORPHAN + capability not honoured** | The interrupt payload matches `clarificationRequestSchema` exactly (`kind`/`clarification_id`/`question`/`why`). But: `can_stream: false` (`routes.py:107`) → `chat-panel.tsx:33` mounts `<RestChat/>`; `useRestChat` returns `{messages, send, isRunning}` only (`hooks/use-rest-chat.ts:82`) — no `clarification`, no `respondClarification`; and **no frontend code calls `/chat/resume`** (grepped: the only resume path is `stream.submit(null, {command:{resume}})` at `use-stream-chat.ts:184`). Meanwhile `can_clarify: true` whenever a model is attached (`routes.py:130`). Net: an `ask_user` on the live transport surfaces as `outcome: "clarification"` with the question in `text` and no way to answer it. |
| 15 | Validate + write a corpus asset | `components/corpus/asset-edit-sheet.tsx:96`, gated at `components/corpus/asset-browser.tsx:76` | NONE | **MISSING** (gated) | No `POST /corpus/edit`. `can_edit: false` (`routes.py:91`) so the affordance never mounts. Honest per ADR 0007 §7. |
| 16 | Served turns, newest first | `hooks/queries.ts:180-185`, `components/audit/audit-surface.tsx:259-266,350-351,422-429` | `routes.py:709` `GET /audit/turns` | **OK** | All **18** required fields present: 11 from `SUMMARY_FIELDS` (`trace_store.py:42-58`) + `asked_at`/`question`/`answer_text`/`outcome`/`licensed_count`/`attempts`/`attempts_passed`/`incomplete_fields` (`trace_store.py:133-142`) vs `lib/schemas.ts:448-469`. Client sends `limit=500`; server reads it (`routes.py:710`). |
| 17 | One turn's record, by stage | `hooks/queries.ts:188-194`, `components/audit/audit-surface.tsx:491-535` | `routes.py:758` `GET /audit/turns/{id}/trace` | **OK** | `found`/`turn_id` required and always present, including on the not-found branch (`routes.py:777`). `stages[].fields[]` supplies all 6 required keys (`routes.py:784-791`). `ledger` is `.passthrough()` so the raw attempt rows survive. |
| 18 | One turn's raw record + undeclared keys | none | `routes.py:729` `GET /audit/turns/{turn_id}` | **ORPHAN** | No client caller. It is the only route that returns `record` and `undeclared_keys` — both useful, neither reachable. |
| 19 | Corpus shape + fatal/degradation split | `hooks/queries.ts:197-199`, `components/audit/audit-surface.tsx:83` | `routes.py:813` `GET /audit/corpus` | **OK** | All 7 required blocks match (`routes.py:828-848` vs `lib/schemas.ts:527-548`). |
| 20 | Generated filter row for an asset type | `hooks/queries.ts:205-220`, `components/corpus/asset-table.tsx:69,102,146,170-178,251,266` | `browse_routes.py:85` `GET /corpus/fields` | **OK** | `columns_for` emits exactly `{name, kind, ops, sortable, identifier}` (`browse.py:121-131`), and `FieldKind`'s 7 members (`browse.py:44-56`) are exactly `corpusFieldSchema.kind`'s enum (`lib/schemas.ts:561`). Unknown/absent `type` returns `{type: null, columns: [], types, detail}` — parses (`browse_routes.py:96-101`). |
| 21 | Filtered, sorted, paginated rows | `hooks/queries.ts:225-247`, `components/corpus/asset-table.tsx:81-87,152-160,220` | `browse_routes.py:110` `GET /corpus/rows` | **OK** | **Every parameter the client sends is read.** `type`, repeated `where`, `sort`, `order`, `offset`, `limit` (`browse_routes.py:111-117`); `sort`/`order` reach `sort_rows` (`browse.py:226-240`); unapplied predicates come back in `unknown_where` and are rendered (`asset-table.tsx:152-160`). `total` is post-filter (`browse_routes.py:150`). One nit: the echoed `limit` is the clamped page width `end - start` (`browse_routes.py:151`), not the row count — the client only uses it for paging arithmetic, so it is fine. |
| 22 | Liveness | none | `routes.py:76` `GET /livez` | **ORPHAN** | No UI caller, and correctly so — it is an ops probe, not a UI need. Keep. |
| 23 | Live streaming chat + timeline | `hooks/use-stream-chat.ts:96-131,265-268`, `lib/steps.ts:36-47` | LangGraph platform `/threads`, `/runs/stream`; graph at `graph_app.py:238` | **MISSING** (gated) | `can_stream: false` (`routes.py:107`), so `<StreamChat/>` never mounts. The submitted payload `{messages:[{type:"human",content}]}` is what `_accept_node` reads (`graph_app.py:194-208`), and `ASSISTANT_ID` defaults to `"serve"` (`lib/env.ts:16`) which matches `langgraph.json`. Nothing in v2 emits a custom `GovEvent`, so ~900 lines of timeline UI have no input — ADR 0007 §5's own note. |
| 24 | The provenance drawer's three groups | `components/answer/provenance-drawer.tsx:38-40`, `lib/provenance.ts:24-78` | `POST /chat` → `record` (`routes.py:439`) | **LOSSY** | The drawer reads v1 field names the v2 register does not declare. Present in `RECORD_REGISTER`: `outcome`, `cost_est_usd`, `turn_id`, `run_id`, `thread_id`, `prompt_set_hash`. Absent: `governance_ledger` (drawer:39 — the ledger is at `record.execution.attempts`), `stage_events` (drawer:40), `token_usage`/`token_sum` (register has `usage`), `latency_ms` (register has `latency_sec`), `corpus_release_hash` (register has `corpus_content_hash`), `uncertainty_flags`, `routed_schemas` (register has `schemas`), `suspect_columns`, and the other 14 `GOVERNANCE_KEYS`. `pick()` filters on `k in provenance`, so nothing throws — the Governance and Stages groups are simply always empty and everything lands in the "Other" catch-all as `JSON.stringify`. |
| 25 | The answer card's step timeline | `components/answer/answer-card.tsx:43` | `POST /chat` → `record.execution` | **LOSSY** | `buildStepsFromLedger` returns `[]` for anything that is not an array (`lib/steps.ts:259`); `record.execution` is an object `{attempts, terminal}` (`routes.py:807-808`). `answer-delivery.ts:88-95` reads `execution.attempts` correctly, so the shape is known — the card just passes the wrong level. The timeline on a REST answer is therefore always empty. |
| 26 | "corpus @ <hash>" reproducibility chip | `lib/answer-delivery.ts:146-150`, `components/answer/answer-card.tsx:106` | `record.corpus_content_hash` (`register/record.py`) | **LOSSY** | Reads `corpus_release_hash`; the register declares `corpus_content_hash` (`Absence.never`, so it is on every turn). Chip never renders. |
| 27 | "Schemas considered: …" line | `lib/answer-delivery.ts:131-135`, `components/answer/answer-card.tsx:99` | `record.schemas` | **LOSSY** | Reads `provenance.routed_schemas`; the register declares `schemas`. Line never renders. |
| 28 | Uncertainty "why" lines | `lib/answer-delivery.ts:111-128`, `components/answer/answer-card.tsx:82-91` | NONE | **MISSING** | `uncertainty_flags` / `suspect_columns` are not register fields and nothing in v2 observes them. Dead UI, correctly dead — ADR 0007 §3's rule is that a reliability claim must be earned. Listed for completeness, not as work. |
| 29 | Result table (rows of the answer) | `components/answer/result-table.tsx:40`, `lib/schemas.ts:365-370` | NONE | **MISSING** (by design) | v2 does not keep the result set (`answer-card.tsx:94-97`). `<ResultTable/>` has no caller anywhere in the app — client dead code, not a backend gap. |

---

## 3. `BREAKS` in detail

**None.** Zero routes are missing a field the client declares as required. I checked all 15
called routes field by field; the reconciliation was clearly done deliberately and the backend
docstrings record it (`browse_routes.py:8-13`, `routes.py:186-192`, `routes.py:215-221`).

Because "no BREAKS" is the kind of claim that reads as sampling, here is what was verified,
with both line references:

| Route | Client-required fields | Where emitted | Result |
|---|---|---|---|
| `/capabilities` | `environment, dialect, can_edit, edit_mode, can_stream, has_live_model, model` (`lib/schemas.ts:37-43`) | `routes.py:88-131` | all 7 present |
| `/health` | `counts, n_suspect_columns, n_excluded, n_low_confidence_joins, ci_green, findings` (`lib/schemas.ts:58-75`) | `routes.py:148-165` | all 6 present |
| `/schema`, `/schema/{id}` | `id, physical_name, schema, row_count, description, grain, confidence, excluded, excluded_reason, provenance_status, columns` (`lib/schemas.ts:102-114`); per column `physical_name, physical_type, logical_type, nullable, is_unique` (`lib/schemas.ts:81-86`) | `browse_routes.py:252-267`, `:281-298` | all present; `schema` coerced to `""` not null (`:255`), which the required `z.string()` needs |
| `/schema/summary` | `total, items[{id, physical_name, schema, row_count, n_columns, excluded, has_suspect, provenance_status}]` (`lib/schemas.ts:128-143`) | `browse_routes.py:215-225`, `:181` | all present |
| `/graph` | node `id, physical_name, row_count, n_columns, excluded, has_suspect`; edge `id, source, target, on, cardinality, confidence, low_confidence`; meta `n_nodes, n_edges` (`lib/schemas.ts:247-280`) | `routes.py:243-288`, `browse.py:376-378` | all present |
| `/knowledge-graph` | node `id, kind, label, excluded, provenance_status`; edge `id, source, target, relation` (`lib/schemas.ts:162-183`) | `routes.py:351-393` | all present; `kind` constrained to the 7-member enum at `routes.py:298-300` |
| `/corpus/assets` | `id, asset_type, summary, provenance_status, excluded` (`lib/schemas.ts:284-290`) | `routes.py:199-202` | all present; `summary: str` is non-optional on every asset dataclass (`corpus/schema.py:253,275,296,327,349,372,394,414`) so the required `z.string()` holds |
| `POST /chat` | `outcome` (5-member enum), `text` (`lib/schemas.ts:392-393`) | `serve/nodes/stamp.py:280-287` | present; `Outcome` has exactly those 5 members (`register/stages.py:194-198`) — a `declined` turn stamps `refused` + `terminal_reason`, so the enum cannot be overrun |
| `/audit/turns` | 18 fields (`lib/schemas.ts:448-478`) | `trace_store.py:42-58,132-142` | all 18; `cost_est_usd`/`latency_sec` come from `state.get(...)` (`serve/nodes/stamp.py:170-173`) and nothing writes them, so they are `null` — which `z.number().nullable()` accepts |
| `/audit/turns/{id}/trace` | `found, turn_id`; stage `stage, fields`; field `name, tier, value, present, required_and_absent, why` (`lib/schemas.ts:481-519`) | `routes.py:781-810`, `:777` | all present on both branches |
| `/audit/corpus` | `corpus_content_hash, assets{total,by_type}, schemas, structure{5}, problems{4}, servable` (`lib/schemas.ts:527-548`) | `routes.py:828-848` | all present |
| `/corpus/fields` | `type, columns, types` (`lib/schemas.ts:571-576`) | `browse_routes.py:96-107` | all present on both branches |
| `/corpus/rows` | `rows, total, offset, limit, columns, unknown_where` (`lib/schemas.ts:578-590`) | `browse_routes.py:148-155`, `:134-135` | all present on both branches |

**The two BREAKS-adjacent cases**, i.e. where the page does die but the cause is not a missing
field:

1. `GET /columns/{column_id}/related` — the route does not exist. 404 → `ApiError` →
   `column-related.tsx:39` error state. Classified `MISSING`, effect identical to a `BREAKS`.
2. Any 500. `session_from_environment()` (`graph_app.py:75`) is called by every route through
   `_session()` (`routes.py:46`); a corpus or DSN failure raises `RuntimeError` per request.
   Nothing installs an exception handler, so the client gets FastAPI's default 500 and
   `getLive` throws `ApiError("<path> returned 500.")` (`lib/api-client.ts:109`) with no body
   read. The server's own message — which names the missing env var — never reaches the
   screen. `graph_app.py:336-338` mitigates this by building the session at import when
   `LANGSERVE_GRAPHS` is set, so the server fails at startup instead. That mitigation only
   covers the LangGraph-server launch path.

**Error shapes, since they were asked about explicitly.** Three different shapes reach one
client that reads none of them:
- `POST /chat` / `/chat/resume` failures return **HTTP 200** with `_error(...)`
  (`routes.py:608-612`) — `{outcome: "crashed", text: <detail>, …}`. This is the good one: it
  parses as a valid `AnswerView`, so the detail is rendered.
- `GET /schema/{id}` on a miss returns 404 with FastAPI's `{"detail": "..."}`
  (`browse_routes.py:195`). The client discards the body (`lib/api-client.ts:109`) and shows
  `"/schema/x returned 404."`.
- A missing required query parameter (`/corpus/rows` without `type`) returns FastAPI's 422
  validation envelope. Same discard.
- A zod mismatch throws `"<path> response did not match the expected schema."`
  (`lib/api-client.ts:96`) with **no indication of which field** — worth noting, because it
  means any future field drift is reported to the operator as an unlocalised sentence.

---

## 4. The minimal sufficient API set

Twelve routes. Below, `KEEP` means no change; the others are named explicitly.

### KEEP unchanged (8)

| Route | Why it stays |
|---|---|
| `GET /capabilities` | The gate for everything. Nine honest observations. |
| `GET /health` | Matches its consumer exactly. |
| `GET /audit/corpus` | Matches exactly. |
| `GET /audit/turns?limit=` | Matches exactly; `limit` honoured. |
| `GET /audit/turns/{id}/trace` | Matches exactly; the register-derived stage grouping is the right seam — a new record field appears with no client change. |
| `GET /corpus/fields?type=` | The generated-filter-row contract is the best-built thing in this surface. |
| `GET /corpus/rows?type=&where=&sort=&order=&offset=&limit=` | Every parameter read, `unknown_where` honest, `total` post-filter. |
| `GET /livez` | Ops probe, deliberately not a UI route. |

### CHANGE (4)

**1. `GET /graph` and `GET /knowledge-graph` — one line, then boundary.**

```
meta.scope: {schema, focus, radius, kinds, node_budget}   # <- add node_budget (browse.py:384)
```

Without it `engineScopeMatches` (`lib/graph-scope.ts:61`) cannot succeed and the client
overwrites `truncated`/`dropped` with `false`/`0`. This is the whole fix for finding #9.

Then, because fixing #9 also removes the client's self-synthesised boundary stubs, emit the
array both components already render:

```
boundary: [{id, in_scope_table, other_schema, other_table_id, other_label,
            on, cardinality, confidence, low_confidence}]
```

The inputs are already in hand: `structure.joins_by_edge` for the predicate,
`structure.schema_tags` for the other endpoint's namespace — the same two the client uses
(`lib/graph-scope.ts:176-192`).

**2. `GET /schema/summary` — raise the default bound and echo the page.**

```
GET /schema/summary?schema=&limit=1000&offset=0
  → {total, offset, limit, items:[{id, physical_name, schema, row_count, n_columns,
                                   excluded, has_suspect, provenance_status,
                                   columns:[{physical_name, physical_type, role,
                                             reliability, excluded,
                                             nullable, is_unique}]}]}
```

Two edits: default `limit` to the existing clamp ceiling of 1000 (`browse_routes.py:160,179`),
which covers 656 tables in one request; and add `nullable` + `is_unique` to the lean column
(`browse_routes.py:203-212`). Those two booleans are the only fields `ErColumnRow` renders
(`er-diagram.tsx:176-180`) that the lean column lacks — adding them is what lets the ER diagram
drop the flat dump. Echo `offset`/`limit` so a client that *does* page can tell it is paging.

**3. `GET /corpus/assets` — bound it or delete it.** It is 2.25 MB of every asset including
columns, fetched whole so the client can filter it locally. Preferred: **delete it** and point
`asset-browser.tsx` at `GET /corpus/rows` — which already filters, sorts and pages the same
assets from the same in-memory corpus, and whose `columns` descriptor makes the browser's three
hand-written filters (schema / provenance / excluded) generated instead. If it survives, it
needs `?type=&offset=&limit=` and a `{total, offset, limit, rows}` envelope, and it should keep
sending `schema` — the client should declare it rather than rebuild it from the catalog.

**4. `POST /chat` — settle the clarification question.** Pick one:
- *(a)* Declare `clarification` in `answerViewSchema`, give `useRestChat` a
  `clarification`/`respondClarification` pair, and have it `POST /chat/resume`
  `{session_id, clarification_id, answer|choice_id|declined}`. The route already exists and is
  correct (`routes.py:442-495`); only the caller is missing.
- *(b)* Report `can_clarify: can_stream and agent_model is not None`.

Either is honest; the present state — `can_clarify: true` on a transport with no prompt and no
resume caller — is not. Also declare `audit_logged` / `audit_error` client-side, or stop
sending them; a silently-stripped "your turn was not logged" is the exact class of loss
`trace_store.py:16-18` argues against.

### ADD (1)

**`GET /columns/{column_id}/related`** — the only genuinely absent route. Shape from
`columnRelatedResponseSchema` (`lib/schemas.ts:305-361`); everything in it is derivable from
`session.assets_by_id` + `CorpusStructure`:

```
{ column: {id, table_id, table_physical_name, schema, physical_name},
  terms:   [{id, name, synonyms[], confidence, provenance_status}],
  rules:   [{id, kind, statement, confidence, provenance_status}],
  fk_out:  {column_id, table_id, physical_name} | null,
  fk_in:   [{column_id, table_id, physical_name}],
  joins:   [{id, left_table, right_table, other_table_id, on,
             cardinality, confidence, low_confidence}],
  metrics: [{id, name, granularity}],
  meta:    {column_resolvable: bool} }
```

Only `column` and its five keys are hard-required; every list defaults to `[]` and `fk_out`
to `null`, so a lean first version parses. `meta.column_resolvable: false` is the declared way
to say "this id does not name a column I hold" (`column-related.tsx:43`) — return that rather
than a 404, so an unresolvable id reads as a fact instead of a broken page. **Before building
it, check that `lib/columns.ts:10-13`'s `col_<table minus tbl_>_<physical>` scheme is what
`corpus.ids` derives under ADR 0008** — if not, one of the two has to move, and the server's is
the authority.

### DELETE (2)

- **`GET /schema`** — the 936 637-byte flat dump. Its only consumer is
  `er-diagram.tsx:222-223`, and CHANGE #2 gives that consumer everything it needs from
  `/schema/summary`. Keeping both means two projections of a table that can drift; the
  docstring at `browse_routes.py:229-238` is a record of what that drift already cost.
- **`GET /audit/turns/{turn_id}`** — orphaned. Move its two unique fields
  (`record`, `undeclared_keys`) onto `/audit/turns/{id}/trace`, which is fetched at the same
  moment for the same turn, and drop the route. `undeclared_keys` is worth surfacing: it is the
  only signal that a producer is writing a field nobody declared.

### DO NOT BUILD (2)

- **`GET /search`** — `can_search: false` is the honest answer and the Fuse index is adequate
  at 656 tables. It becomes adequate *in fact* only after CHANGE #2, because today the index
  covers 200 of them.
- **`POST /corpus/edit`** — the curator is out of scope; `can_edit: false` keeps the promise.

### Net effect

12 routes (8 kept, 1 added, 3 changed-and-kept, 2 deleted, 2 not built). Every route bounded,
no route unbounded, every capability flag true only where the thing behind it exists, and the
graph tabs able to say what they cut.

---

## 5. What I could not determine statically

- **Whether `/capabilities` actually returns 200 in the current environment.** Every route
  calls `_session()` → `session_from_environment()`, which requires a Postgres DSN
  (`graph_app.py:94-99`). Whether that credential resolves here is not knowable without
  running it. If it does not, *every* route 500s and the whole UI is blank — a state that would
  look identical to a contract failure.
- **The column-asset count, and therefore the true size of `GET /corpus/assets`.** I measured
  the asset **files** on disk (8 034: 57 schemas / 656 tables / 994 terms / 928 joins / 399
  metrics / 5 000 few-shots, via `find corpora/gold-semantic-layer-20260804 -name '*.yaml'`).
  Column assets are nested inside table YAMLs and are not separate files, so counting them
  needs the loader. ADR 0009 records 13 981 total assets and 2 253 297 bytes from a live run on
  2026-08-04; I did not reproduce either. **Not measured by me.**
- **Response byte sizes.** Every byte figure quoted above is ADR 0009's measurement, not mine.
- **Whether `useStream` works at all.** `can_stream: false`, so the path is unexercised. The
  payload shape and `assistant_id` line up on paper; the LangGraph platform routes, the
  interrupt surfacing as `stream.interrupt.value`, and the `blockbuster` interaction
  `routes.py:100-106` describes can only be settled by a run.
- **Whether `record.execution.attempts` rows satisfy `auditLedgerRowSchema`.** It is
  `.passthrough()` with all fields optional, so it cannot fail — but whether the audit page's
  ledger box renders anything useful depends on the real attempt row keys, which I did not
  observe.
- **Whether `cost_est_usd` / `latency_sec` are ever non-null.** Statically, nothing writes them
  into `ServeState`; `stamp` reads `state.get(name)` (`serve/nodes/stamp.py:170-173`). If some
  path does write them, the values could be `Measured` objects, and `json.dumps(default=str)`
  in `append_turn` (`trace_store.py:90`) would then write a **string** into a field the client
  declares `z.number().nullable()` — which would be a genuine `BREAKS` on the whole audit page.
  A single served turn plus `GET /audit/turns` settles it. Flagged rather than asserted.
- **Whether `lib/columns.ts`'s column-id derivation matches v2's.** Needs one read of
  `corpus/ids.py` against ADR 0008 D1 that I did not do, and it is a precondition for the new
  route in §4.
- **Three docstring numbers I could not reconcile and did not chase**: `routes.py:322` says the
  knowledge graph carries "13,981 nodes' worth" and `routes.py:682` says the two graphs "differ
  by 13 325 nodes"; measured from the corpus the semantic node count is 7 977 and the
  difference is 7 321. Not an API defect — noted so it is not read as one later.
