# 0003: Governed notes (`NoteAsset`) and tri-modal retrieval

- **Status:** Reversed in full (2026-08-03) by
  [ADR 0005](0005-v2-memory-layer-and-faceted-retrieval.md). Decided and built
  2026-07-22; deleted with v1 in `2347ae3`. **`NoteAsset` does not exist in this tree and
  neither does the `skill` asset it replaced.** This page was rewritten on 2026-08-12: the
  version it replaces was a 490-line design document whose every coordinate
  (`schemas.py`, `schema_router.py`, `rvgd.py`, `adversary.py`, `agent.py`, `context.py`,
  `api/stack.py`, `viz/presenter.py`) named a v1 file that no longer exists, under a Status
  line that still said "M3 + M4 landed" in the present tense.
- **Deciders:** project owner + design session (4 independent proposals, 3 judges, an
  adversarial red-team)
- **Do not quote this ADR's retrieval numbers.** Its routing-recall figure was later
  re-measured and was wrong by 2.4x. `register/citations.py` carries the corrected values
  and the pattern that fails the build if the old one reappears.

## What was decided

Three things, all reversed:

1. **Delete the `skill` asset and generalize `RuleAsset` into `NoteAsset`** — one governed
   annotation attachable to any asset *or* namespace, carrying `kind`, `scope`, a short
   indexed `summary`, an unbounded `body`, `triggers`, `activation`, `normative_force`,
   `publication_status`, and — new — a `Governance` block.
2. **Tri-modal retrieval**: semantic similarity on the note's own vector (BLEND into RRF),
   regex/keyword triggers (PIN, never blended), and agent-fetch tools `read_notes` /
   `grep_notes` (neither).
3. **Namespace scopes by sentinel prefix** — `schema:beer_factory`, `db:main` — rather than
   promoting `db` and `schema` to asset types.

## What is true instead

**The `summary` / `body` split won; the separate type lost.** The design's real content — a
note carries one short indexed line plus an unbounded body loaded on demand — was correct, and
ADR 0005 pushed it *down into every asset type* as invariants I1 and I2. All eight asset
classes in `corpus/schema.py` (`SchemaAsset`, `TableAsset`, `ColumnAsset`, `JoinAsset`,
`MetricAsset`, `TermAsset`, `FewShotAsset`, `NegativeExampleAsset`) carry `summary: str` and
`body: str | None`. A separate type for "notes about things" turned out to be the same idea
wearing a second name.

**The governance upgrade shipped, without the type.** Every one of those eight classes carries
`governance: Governance`, so any asset can be D6-excluded. That was the strongest argument for
`NoteAsset` — `RuleAsset` was `extra="forbid"` and rejected a `governance:` key at parse — and
it was satisfied by fixing the assets rather than by adding a ninth.

**Tri-modal became bi-modal, and PIN is gone.** Retrieval is two scoring channels, `lexical`
(BM25) and `semantic` (embedding cosine), fused into `hybrid` by weighted sum
(`retrieve/fuse.py::fuse`, re-exported from `retrieve/__init__.py`; corrected 2026-08-22 —
this said `retrieve/index.py`, which contains no fusion). There is no regex-trigger pinning,
no RRF, and no `read_notes` / `grep_notes` tool. The pin-vs-blend contract was written against a measurement — embedding
recall@3 0.70 against BM25 0.35 <!-- [retired]: quoted here only to name what the reversed decision rested on; the corrected channel recalls are in register/citations.py --> — that was later shown to be wrong in both
magnitude and direction, which removed the reason to keep the weak channel out of the fusion.

**`schema` became an asset.** ADR 0005 promoted it (`SchemaAsset`), which is what this ADR
declined to do and worked around with sentinel prefixes. ADR 0005 §1 records that choice.

## What was learned

- **A ~90%-there primitive is an argument to fix the primitive, not to generalize it into a
  new one.** The reasoning here — that `RuleAsset` already had the union membership, the
  indexing and the scoping, so generalizing in place beat a parallel type — was right about
  the alternative it rejected and wrong about its own conclusion. The same argument applied
  one level down said: put the fields on the assets.
- **A retrieval contract written on a measurement inherits that measurement's errors.** The
  PIN rule existed only because fusion was believed to hurt. It did not survive re-measurement.
- **The PII finding was real, and the fix it argued for is not the one that shipped.** This ADR
  found a corpus skill naming an excluded column in prose, injected verbatim into the SQL
  prompt while the column itself was correctly hidden, and concluded that the structural answer
  was a content-scanning validator over asset text. No such validator exists. What shipped
  instead moved the guarantee to execution time: `corpus/analyst.for_analyst` drops excluded
  assets *and* records their column keys, and `govern/check` refuses any statement binding one.
  So the name can still reach the prompt and the query naming it is refused. The prose channel
  is unguarded and the outcome is bounded — which is the ADR 0006 posture, not this ADR's.

## Consequences of the reversal

- The v1 modules this ADR specified were deleted wholesale rather than migrated, so there was
  no rename churn to pay: the `/skills` route, its presenter and the frontend surface went with
  them.
- `docs/glossary.md` no longer defines "Note" as a live term. There is no note primitive.
- The adversary seam (`adversary.refute()`, `NotImplementedError` here) was never built and is
  not in the current tree either. "Certified" still means a human signed off, not that an
  independent model tried to break it.
