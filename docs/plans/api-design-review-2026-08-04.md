# API design review — the read surface after ADR 0009 Amendment 1

**Date:** 2026-08-04. **Reviewer:** the engineer who wrote the amendment, which is the main
thing to hold against this document.

**What this is not.** It is not
[api-sufficiency-audit-2026-08-04.md](api-sufficiency-audit-2026-08-04.md), which asked "can
the frontend get what it needs" and answered by cross-referencing routes against consumers. It
is not [ADR 0009](../adr/0009-browsing-and-filtering-api.md) Amendment 1, which records what was
decided. This asks a third question: **is the resulting design coherent**, and where is it still
wrong. Sufficiency and coherence are different properties — the surface is sufficient today and
still has seams that will produce the next defect.

Every claim below is checked against the code at this commit. Where I could not check something
without a live model or a corpus that does not exist, I say so instead of guessing.

---

## Verdict

The surface is **sufficient and internally inconsistent**. All 17 routes parse against the
client's real schemas (`npm run check:api`, 0 failures), and the frontend browses 13 981 assets
over 57 schemas with per-column filtering and two working relationship views. But the design has
**one latent defect that re-creates a bug I fixed today**, **one duplicate pair I did not
delete**, and **four different conventions for "not found"** across five routes.

The honest summary of today's work: I removed one duplicated projection of a table (`/schema`)
and left another (`/schema/summary` vs `/corpus/rows?type=table`). The reasoning that justified
the deletion applies just as well to the pair that survived, and I did not notice until writing
this.

---

## Defects that remain

### D-1 (high) — `can_scope: false` reintroduces the zero-edge graph — **FIXED**

> Fixed while writing this review, because checking the claim made it worse than written: the
> gate is not only a `can_scope: false` hazard. `canScope(caps)` is false whenever `caps` is
> merely **undefined**, which is every first render before `/capabilities` resolves — so *every
> mount* fetched each graph unscoped first and `keepPreviousData` held that render until the
> scoped refetch landed. Both hooks now always send the resolved scope, and `canScope` is
> deleted. The wire field stays: it is a true observation, just not a switch.

Two call sites read it, both the same shape (`hooks/queries.ts:73,84`):

```ts
const scoped = canScope(caps);
queryFn: () => api.erGraph(scoped ? scope : undefined)
```

So when `can_scope` is false the client sends **no scope at all**. The server then applies its
own default budget (120) and echoes it, while the client's local pass asks for its own default
(150). Those differ, `engineScopeMatches` fails, and the client falls back to the alphabetical
local truncation that returned **150 nodes and 0 edges** this morning. The fix I shipped works
only on the true branch.

Worse, the flag no longer describes anything. `/schema/summary` was ungated today, and the graph
routes scope unconditionally — a scope parameter is honoured whether or not the flag is set. So
`can_scope` gates nothing real and its false branch is a trapdoor to a known bug.

**Recommendation:** delete the gate; always send the resolved scope. Then delete `can_scope`
from `/capabilities`, or redefine it as something a client can act on. A capability flag whose
false branch is untested is worse than no flag.

### D-2 (high) — two catalogs of the same tables

| route | payload key | per-row columns | filter / sort / page |
|---|---|---|---|
| `GET /schema/summary` | `items` | nested lean columns | `schema`, `offset`, `limit` |
| `GET /corpus/rows?type=table` | `rows` | no | any field, any operator, `sort`, `offset`, `limit` |

Both return the 656 tables from the same in-memory corpus, in different shapes, under different
envelopes, with different capabilities. This is the exact condition that justified deleting
`/schema` — "two projections of a table can disagree, and this pair already had" — and it
survived because I was checking the pair I had already named.

They are not redundant *today*: `/corpus/rows` cannot serve the ER diagram, which needs each
table's columns nested, and `/schema/summary` cannot serve the corpus browser, which needs
arbitrary predicates. But that is an argument about missing features, not about there being two
right answers to "list the tables".

**Recommendation:** one route. Give `/corpus/rows` an opt-in `include=columns` and retire
`/schema/summary`, or state in the ADR that `/schema/summary` is the *diagram's* projection and
is not a catalog — and then stop using it as one (it currently backs the namespace rail, the
table browser and the search index).

### D-3 (medium) — four conventions for "not found"

| route | an absent thing produces |
|---|---|
| `GET /schema/{table_id}` | `404` + FastAPI `{detail}` (`browse_routes.py:209`) |
| `GET /columns/{column_id}/related` | `200` + `meta.column_resolvable: false` (`:379`) |
| `GET /audit/turns/{id}/trace` | `200` + `found: false` (`routes.py:776`) |
| `GET /corpus/rows?type=bogus` | `200` + `detail` + empty rows (`:135`) |

I defended each of these individually in its own docstring, and each argument is locally sound.
Collectively they are incoherent: a client must know, per route, which of four idioms means
"absent", and three of the four are invisible to any HTTP-level error handling.

There is a rule that fits almost all of it: **404 when the path names a resource that does not
exist; 200 with a declared negative field when a query legitimately matches nothing.** Under
that rule `/schema/{id}` (404) and `/corpus/rows?type=bogus` (200, a query) are already right,
`/columns/{id}/related` is a deliberate exception worth keeping — the panel is reached by
clicking a column, so an unresolvable id means the *id scheme* drifted and that deserves a
readable sentence rather than a broken panel — and `/audit/turns/{id}/trace` is simply wrong: a
turn id is addressable, so `found: false` should be a 404.

**Recommendation:** adopt the rule, fix the trace route, and record the one exception with its
reason.

### D-4 (medium) — three envelope conventions

`{total, offset, limit, items}` (`/schema/summary`), `{rows, total, offset, limit, columns,
unknown_where}` (`/corpus/rows`), and a bare JSON array (`/corpus/assets`). No client can write
one pager over this surface. The array-valued key differs (`items` vs `rows`) for no reason I can
reconstruct.

**Recommendation:** one envelope — `{data, total, offset, limit, ...route-specific}` — applied
when D-2 collapses the two catalogs. Not worth a breaking change on its own.

### D-5 (medium) — `/corpus/assets` is unbounded, and its overlap already cost a defect

13 981 rows in one response (2.25 MB, ADR 0009's measurement, not re-measured today), fetched
whole so three components can filter it client-side. `/corpus/rows` already filters, sorts and
pages the same assets from the same corpus.

The overlap is not merely wasteful. Because `/corpus/assets` returns tables *and*
`/schema/summary` returns tables, `mergeAssetCatalog` concatenated both and emitted every table
twice — React reported `two children with the same key, address.alias`, and the type tallies
beside the filters were silently doubled. I fixed the merge; the design that made the collision
available is unchanged. Note also what hid it: the route was failing its zod boundary, so the
merge received an empty list. **A broken route was standing in for a missing dedupe**, and
repairing the route surfaced the flaw.

**Recommendation:** repoint the three consumers at `/corpus/rows` and delete this route.

### D-6 (low-medium) — the node-kind vocabulary exists in three copies

`_SEMANTIC_NODE_KINDS` (`browse.py`), `graphNodeKindSchema` (`lib/schemas.ts`), and
`CLIENT_GRAPH_NODE_KINDS` (`tests/api/test_http_contract.py`). The third exists *because* the
first two can drift, and emitting a kind outside the client's enum takes the whole response down
— that is how the semantic graph rendered `0 of 0 shown` with 107 valid nodes in the payload.

Three copies with a test between two of them is better than two copies with nothing. It is still
three copies of one enum, and the test only fires if someone runs it.

**Recommendation:** generate the client enum from the register, or accept the duplication
explicitly in the ADR. Do not leave it undecided.

### D-7 (low) — `boundary` has no live coverage

Measured across all 57 schemas: **0 cross-namespace destinations**. The lake is 57 independent
BIRD databases pooled together, so no curated cross-schema join exists to exercise the path. Unit
tests cover it; no data does. It will first run for real on a corpus nobody has built yet, which
is the condition under which code is usually wrong.

**Recommendation:** leave it, and do not describe it as working. If cross-schema joins become
real, treat the first run as unverified.

### D-8 (low) — an immutable corpus served without caching

Every response is a projection of a corpus identified by `corpus_content_hash`, which cannot
change without a restart. Nothing carries `ETag` or `Cache-Control`, so every mount refetches
everything — including the 2.25 MB dump in D-5. An `ETag: {corpus_content_hash}` plus
`If-None-Match` would make repeat loads free and cost about ten lines.

### D-9 (low) — offset pagination with no declared ordering guarantee

`/corpus/rows` pages by offset over `sort_rows(matched, sort, order)`. With `sort=None` the order
is `assets_by_id`'s insertion order — stable in CPython in practice, guaranteed by nothing in the
contract. Offset paging over an unstably-ordered set skips and repeats rows.

**Recommendation:** document a total order (id as the tiebreaker) and apply it whether or not
`sort` is given.

---

## Deliberate trade-offs (not defects)

- **Filtering in memory, not SQL** (ADR 0009 D5). Pushing it down would make the browser query
  the lake instead of the semantic layer, so an asset that failed to load would still appear —
  the corpus would look complete because the database is.
- **`/search` unbuilt, `can_search: false`.** The client-side index over the lean catalog works,
  and a server search that ranked worse would be a downgrade dressed as a feature.
- **`200 + column_resolvable: false`** for an unresolvable column id — see D-3.
- **`can_clarify` gated on `can_stream`.** It describes what the mounted transport can do, not
  what the server can do. Both readings are defensible; this one is chosen because the flag's
  only consumer is the switch that mounts the prompt.

---

## What I could not determine

- **Whether `cost_est_usd` / `latency_sec` ever reach the trace as strings.** The audit flagged
  that `json.dumps(default=str)` in `trace_store.py` would serialise a `Measured` object into a
  field the client declares `z.number().nullable()` — a genuine break on the whole audit page.
  Nothing in the corpus produces one today, so I could neither reproduce nor rule it out. One
  served turn with a populated cost settles it.
- **Whether the chat pair is coherent under load.** `POST /chat` and `/chat/resume` were not
  exercised in this review; the eval that would have driven them was stopped, and the
  clarification path is unreachable from the REST transport by design (D-12 in the amendment).

## Order I would fix these in

1. ~~**D-1** — delete the `can_scope` gate.~~ **Done** (see above): two hooks simplified, one
   helper deleted. The surviving path is the one already verified live — `?radius=1&node_budget=`
   on the wire, the `596 more — expand` banner rendering, edges drawn — so this change removes
   the unverified branch rather than introducing a new one.
2. **D-3** — adopt the not-found rule; fix the trace route. Cheap, and it stops the next client
   from guessing.
3. **D-5** — repoint three consumers, delete `/corpus/assets`. Removes 2.25 MB and the condition
   that produced the duplicate-key defect.
4. **D-2** — collapse the two catalogs, and take **D-4**'s envelope with it.
5. **D-8**, **D-9**, **D-6** — cheap correctness and hygiene, no consumer changes.

None of 2–5 is urgent; all are the same *kind* of thing, which is the point of the review. The
surface works. It has three duplicated projections, four ways to say "absent", and three
envelopes, and every defect found today lived in exactly that sort of seam.
