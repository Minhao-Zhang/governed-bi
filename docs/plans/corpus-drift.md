# When the database moves and the corpus doesn't

> **STATUS 2026-07-31 — LOAD-BEARING. Do not delete yet.**
>
> §7 items 3 and 4 are the **only definition** of checklist items `1.7` (`corpus doctor`) and
> `1.8` (`drift` category in `error_taxonomy`) — and
> [rebuild-checklist.zh.md](rebuild-checklist.zh.md) §7.4 declares a dependency on both while
> **not containing either**. Those are dangling pointers until this file's content is moved in.
>
> Still to migrate: §7.3 → new checklist 1.7 · §7.4 + §3's five-step chain → new 1.8 ·
> §5's two false docstrings → X.5.1 (must add `corpus/loader.py:11`, which X.5.1 currently
> misses) · §2 (`/health` cannot report drift) → a new X item · §4 (`Provenance` has no
> `verified_at`) → non-goals/gate.
>
> **Known error, do not migrate as-is:** §1's table says `eval/harness.py:145` passes a
> connector. False for the live path — `_validate_corpora(corpora, *, connector=None)` and its
> only live caller `run_datalake.py:4505` says `# no connector`. Correct version is
> `open-work.md` C9.
>
> Absorbed already: §6 (rename map = controlled drift) → checklist §7.4, reframed as the
> metadata-lying experiment (decision 21).

An adversarial analysis of corpus staleness. Three analyses so far have covered how the
corpus is **authored** (curator) and **consumed** (retrieval, serve). None has asked what
happens when the database changes underneath it.

Short version: **drift is checked exactly once, at build time, and never again** — and the
one endpoint that looks like it reports corpus health is structurally incapable of
reporting drift. Offsetting that, one exposure the reference book has and we don't: we
cannot serve a stale index, because we never persist one.

Verified at `2187ead`. Smaller than the multi-turn and red-team analyses, and mostly
cheap to fix.

---

## 1. Where drift is detected today

`_check_physical_existence` (`corpus/validate.py:553–570`) is the drift check. It calls
`connector.list_tables()` and `connector.describe_table(...)` and reports assets claiming
tables or columns the live catalog does not have. It runs only when `validate_corpus`
receives a `connector` (`validate.py:285–286`).

Every `validate_corpus` call site:

| Caller | Passes `connector`? | When it runs |
|---|---|---|
| `curator/pipeline.py:869` | **yes** | curator build, before the fix pass |
| `curator/pipeline.py:882` | **yes** | curator build, after the fix pass |
| `curator/adversary.py:78` | **yes** | adversary review, build time |
| `eval/harness.py:145` | **yes** | eval, validating built corpora |
| `viz/presenter.py:366` (`corpus_health`) | **no** | **every `/health` request** |
| `api/app.py:405` (`/corpus/edit`) | **no** | on a human corpus edit |
| `corpus/cli.py:71` | **no** | CLI validate |

So: all four connector-passing callers are **build-time**. The serve path never checks the
corpus against the database, and nothing checks it on a schedule.

## 2. The health endpoint cannot report the thing it exists to report

`corpus_health` runs the full 582-line validator on every request and passes no
connector. So `/health` will tell you about reference breakage, unparseable metric
expressions, and note-budget violations — all corpus-internal — and **cannot tell you that
half your tables no longer exist.**

That is the worst shape a check can have: it looks authoritative and is silent on the
failure mode an operator would actually call it about. (It also re-derives the full
finding set per request, which is the architecture review's I/O-in-pure-logic point #2.)

`ServeStack` already opens a connector (`stack.py:69–93`) and already probes the
datasource on startup (`verify_datasource`, `:95–125`). The ingredients for a real
corpus-vs-database health answer are present and unconnected.

## 3. How a drift failure actually surfaces

Trace a dropped column through the serve path:

1. The corpus still advertises it; `asset_document` still indexes its name and
   description, so **retrieval still ranks the table on it**.
2. `column_allowlist` still licenses it (the allowlist is derived from the corpus, not the
   catalog).
3. L3 `ast_column_allowlist` **passes** — the column is in the allowlist.
4. L4 passes — the table is in scope.
5. `Gateway.execute` runs the SQL and the **database** raises.

So the first component to notice is the database, and the failure arrives as an execution
error at the last possible moment. Two consequences:

- **Drift is indistinguishable from a model mistake.** A hallucinated column and a
  dropped column produce the same class of error at the same point. Our own error
  taxonomy is built around attributing failures (`eval/error_taxonomy.py`), and this
  particular cause has no category — so a drifting corpus would read as the model getting
  worse.
- **The repair loop will try to fix it and cannot.** The agent gets a BLOCKED/failed
  ToolMessage, re-reads the corpus, and the corpus keeps telling it the column exists.
  Attempts burn until exhaustion, then the graded-delivery path takes over (see
  [governance-red-team.md](governance-red-team.md) §A1). Drift therefore converts
  directly into cost and into `unverified` deliveries.

## 4. No asset knows when it was last true

`Provenance` (`corpus/schemas.py:175–187`) carries `source`, `status`, `model`, and
`version`. There is **no verified-against-the-database timestamp**. `at` exists only as an
`extra="allow"` convention appended on human certify (`:178–180`), and `version` is unused
everywhere (the book audit's §2.5 finding).

So there is no field that could answer "when was this asset last known to match the
catalog", and therefore no freshness signal available to the assurance stamp. An answer
grounded entirely in assets last verified six months ago stamps exactly the same as one
verified this morning.

## 5. The one drift exposure we *don't* have — and two docs to correct

The reference book's §3.4 narrates a two-day incident: a metric definition changed without
a version bump, the retrieval engine kept serving old embeddings, and old and new
definitions mixed. Its whole SemVer + re-vectorization design exists to prevent that.

**We cannot have that bug, because we never persist an index.** Two docstrings claim
otherwise:

- `corpus/loader.py:11` — "The `_generated/` directory (search index, embeddings, compiled
  graph) is a derived, rebuildable projection and is never read as source."
- `retrieval/__init__.py:25` — "The vector / BM25 indexes are rebuildable projections
  under `corpus/_generated/`."

**Nothing in `src/`, `scripts/`, or `tests/` writes `_generated/`.** The only references are
two exclusion filters (`app.py:81`, `loader.py:122`) skipping a directory nothing creates.
Indexes are built in-process and held in `RetrievalIndexCache` (memory only, per graph).

Net: no staleness risk, and a real cost — every process start re-embeds the whole routed
corpus from scratch. `RetrievalIndexCache` bounds that *within* a run (it was ~55% of the
serve path's non-model CPU); across runs it is paid again in full. And the two docstrings
should be corrected, because "there is a persisted index" is exactly the belief that
would make someone reach for cache invalidation they don't need.

## 6. A drift arm is nearly free, because we already have a renamed database

The obfuscation work gives us this for almost nothing. `BIRD-Data-Obfuscation` ships
rename maps and a renamed Postgres (`pg_rename_decoy`), and `schema_rename_map.json` is
already an artifact the eval reads.

**A rename map is a drift simulation.** Point a corpus built against names-A at
database-B and every asset is stale in a controlled, fully-known way. That gives:

- a **drift-detection** measurement: does `validate_corpus(connector=...)` find *all* of
  the renamed objects? (recall of the drift check itself — currently unmeasured)
- a **drift-behaviour** measurement: with the check bypassed, what does serve do? Refuse,
  answer wrong, or exhaust into graded delivery? This is the number that says how much a
  stale corpus costs.
- partial drift (rename 20% of columns) to test the realistic case, where most of the
  corpus is fine and a few assets lie.

No new data, no new infrastructure, one new arm.

## 7. Fixes, cheapest first

| # | Item | Size | Notes |
|---|---|---|---|
| 1 | Correct the two `_generated/` docstrings (§5) | XS | They describe an artifact that does not exist |
| 2 | Pass a connector to `corpus_health` — behind a flag or on an interval, not per request (§2) | S | The machinery and the connector both already exist; `/health` currently cannot answer the question it implies |
| 3 | A `corpus doctor` entry point: `validate_corpus(connector=...)` against a live DB, writing the existing `validate_findings.jsonl` shape | S | Runnable in CI and on a schedule; zero new machinery |
| 4 | Add a `drift` category to `error_taxonomy` and attribute catalog-mismatch errors to it (§3) | S | Stops drift reading as model regression |
| 5 | `verified_at` on `Provenance`, set by the build-time check, surfaced as a freshness signal in the assurance stamp (§4) | M | Ties drift into the stamp we already have instead of a new channel |
| 6 | The drift arm (§6) | M | Free data; measures both the check's recall and the cost of staleness |
| 7 | Consider persisting the index **only if** a measured cold-start cost justifies it (§5) | M | And if it lands, it needs content-keyed invalidation — the exposure we currently don't have |

Items 1–3 are an afternoon and remove the misleading-health-endpoint problem entirely.

## 8. What is already right

- **The drift check exists and is correct.** `_check_physical_existence` does the right
  thing against the live catalog; the gap is purely *when* it runs.
- **Build-time validation is genuinely gated.** The curator runs it twice — before and
  after the fix pass — and the adversary runs it again, so a corpus cannot be *built*
  against a database it doesn't match.
- **The connector is injected**, so the pure path is the default and the I/O path is
  opt-in (`validate.py:118`) — the right shape, and the reason item 3 is small.
- **Not persisting the index** is, on balance, the safer default for a corpus that is
  rebuilt wholesale, and it makes the book's SemVer incident structurally impossible.
