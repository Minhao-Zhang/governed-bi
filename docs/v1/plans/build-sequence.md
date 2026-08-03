# Build sequence

> **Superseded 2026-07-30 by [rebuild-checklist.md](rebuild-checklist.md).** A grill
> session replaced the four-phase structure below with eleven cross-cutting items plus four
> parallel tracks, retired `run_experiment.py`, demoted 3.17, and added seven items that are
> not in this file. The decisions and what they overturned are in
> [rebuild-decisions.md](rebuild-decisions.md). **Keep this file as the evidence index**
> — its Sources table and Appendix still map every item back to the analysis that found it.
>
> **2026-07-31 — this file is more load-bearing than "superseded" suggests.** An audit found that
> roughly **28 of the 41 items below reached neither the new checklist nor its non-goals** — they
> vanished silently. A grep of the checklist returns **zero** hits for `RetryPolicy`,
> `ServeDeployment`, `get_stream_writer`, `on_event`, `_generated`, `verified_at`, `durability`,
> `circuit`, `EXPLAIN`, `sanitize_note_text`, `Connector.explain`, and the summariser. The
> checklist's own §7.4 depends on `1.7` / `1.8`, and their **only definition is at :79–:80 of this
> file**. So until a carried / dropped / retired table exists, deleting this loses real work.
>
> Two things here have no other copy at all:
> - **The Appendix's nine architecture-review candidates.** The original HTML report was written
>   to a temp directory and is gone. Candidate 2 (`ServeDeployment`) and candidate 7 (lift the
>   summariser out of the driver) are exactly what the audit says the plan needs in order to
>   answer the "1000-line files" complaint at all.
> - **The Sources table** (ARCH / BOOK / FW / MT / RT / DRIFT → document + item count). Without it
>   those six tags, used throughout, cannot be resolved.

The consolidation of five analyses into one ordered plan. This is the "what do we
actually do, in what order" document; it is not a new analysis and contains no new
findings.

**Why this exists.** Between 2026-07-29 and 2026-07-30 five separate analyses produced
**62 worklist items** across five documents, with real overlap — the gateway row cap
appears in three of them, the user-input injection check in two, `get_stream_writer` in
two. None reached [open-work.md](../open-work.md), which declares itself the single
tracker. This file deduplicates to **41 items**, sequences them by dependency, and names
what each one unblocks.

**Division of labour with the tracker.** [open-work.md](../open-work.md) is a flat
inventory of open defects by category (C / E / X). This file is the *order*. Where an item
here corresponds to an existing tracker id, it is cited. Nothing is duplicated: if you
want to know what is broken, read the tracker; if you want to know what to do next, read
this.

## Sources

| Tag | Document | Items |
|---|---|---|
| **ARCH** | Architecture review (module depth) — **HTML, temp dir, ephemeral** | 9 candidates |
| **BOOK** | [book-fidelity-assessment.md](book-fidelity-assessment.md) | 15 unintentional gaps (U-1…U-15) |
| **FW** | [framework-and-logging-audit.md](framework-and-logging-audit.md) | 14 |
| **MT** | [multi-turn-adversarial.md](multi-turn-adversarial.md) | 8 |
| **RT** | [governance-red-team.md](governance-red-team.md) | 9 |
| **DRIFT** | [corpus-drift.md](corpus-drift.md) | 7 |

> **ARCH is not in the repo.** The architecture review was written to
> `%TEMP%/architecture-review-20260730T2140Z.html` and will vanish. Its nine candidates
> are preserved in §Appendix below so the analysis survives the temp directory. Re-render
> it into `docs/` if the visuals are wanted.

---

## Phase 0 — Claims the code does not keep

Everything here is a case where a docstring, a doc, or a design promise is **false as
implemented**. These come first not because they are large but because every downstream
decision made while they are open is made on bad information.

| # | Item | Source | Size |
|---|---|---|---|
| ~~0.1~~ | ~~**Langfuse mask does not mask.**~~ **Closed 2026-08-02 by removing Langfuse (D20), not by fixing the mask.** The finding was real — the legacy `mask` hook does not cover third-party instrumentation, and the LangChain handler is third-party, so DB row previews exported verbatim. It is now moot: LangSmith is the only tracer, it has no mask hook at all, and traces log in full **by decision** (non-production repo; sensitive columns are filtered at the datasource). `obs.py`'s claims were corrected in the same change — that half of the item did land. A production deployment would need a masking layer; there is none here. | FW-F1 | S |
| 0.2 | **Write the graded-delivery scope test.** `governance.py:698` re-checks with `allowed_tables=None`, which skips L4 entirely. Hypothesis: `SELECT COUNT(*) FROM <unlicensed>.<table>` clears L3 and executes. Test first, before any fix. | RT-R1 | S |
| 0.3 | **Decide 0.2.** Either pass `allowed_tables` to the recheck and drop `term_semantics` from the forgivable set, or state the exception in L4's docstring — which currently promises fail-closed containment unqualified. Silence is the only wrong answer. | RT-R2 | S/M |
| 0.4 | **Phase B drops notes and negative examples.** `pipeline.py:1528`'s `if/elif` chain has no `else`, so `curated_sme` silently loses two asset types — and the acceptance gate at `:1654` checks whether the corpora *differ*, not whether the corpus *grew*, so losing every note passes. | ARCH-6 | S |
| 0.5 | **`_generated/` does not exist.** Two docstrings describe a persisted index directory that nothing writes. Correct them — "there is a persisted index" is the belief that makes someone reach for cache invalidation they don't need. | DRIFT-D1 | XS |
| 0.6 | **`/health` cannot report drift.** `corpus_health` runs the full validator with no connector, so it reports corpus-internal findings and is silent on "half your tables are gone." Pass a connector behind a flag or on an interval. | DRIFT-D2 | S |
| 0.7 | **`retrieval/__init__.py` overstates R.** The docstring claims exact id/physical-name lookup ships; there is no exact-match step in `retrieve()`. The only real R-channel is `fire_triggers`, notes-only, keyword-only. | BOOK-U-15 | XS |
| 0.8 | **State the row-level scope of the safety claim.** `identity` is recorded on the audit row and nothing else. "The guardrails enforce access control" is currently false at row level by design; say so in the safety docs. | RT-R9 | XS |

Phase 0 is roughly one focused week and removes every known false claim.

---

## Phase 1 — Instrumentation, before any prioritisation

You cannot rank Phase 3 without these. Each is small; together they are the difference
between deciding and guessing.

| # | Item | Source | Size | Unblocks |
|---|---|---|---|---|
| 1.1 | **`tracing_config(ctx)`** — one `metadata` dict attributing every run. Shipped as N12a, then **corrected 2026-08-02 (D20)**: the `langfuse_*` half is deleted, and the fields the eval driver was declaring but never passing (`arm`, and a real `corpus_content_hash` rather than the mode label `corpus_pin`) are now threaded. | FW-F2 | S | 1.2, 3.x, all trace-joined analysis |
| 1.2 | **`configure_logging()`** at every entry point + a ContextVar filter injecting `run_id`/`turn_id` into every record. The library keeps not calling `basicConfig`. | FW-F3 | S/M | makes the 32 existing `logger.` calls visible for the first time |
| 1.3 | **`retrieval_eval.py` scores a session, not a question.** No LLM needed. | MT-M1 | S | 2.1, 2.3, 3.6, 3.7 |
| 1.4 | **`max_rows` / `timeout_s` into `Settings`**, and stamp all three eval-vs-serve permissiveness deltas in the manifest. Currently a 200× cap divergence recorded nowhere. | BOOK-U-10 = RT-R3 = ARCH-4a | S | honest comparability |
| 1.5 | **Raise dependency floors** to what we run (both checkpoint packages `>=3.1`). A `>=3.0` pin resolving to a later major can silently regress. *(The `langfuse>=4.14` half is void — the dependency was dropped 2026-08-02, D20.)* | FW-F4 | XS | — |
| 1.6 | **`RetryPolicy` on model-calling nodes.** We added `error_type` specifically to tell a rate limit from a bug, and never absorb the rate limit. | FW-F5 | S | eval trust at `--workers > 1` |
| 1.7 | **`corpus doctor`** — `validate_corpus(connector=...)` against a live DB, writing the existing `validate_findings.jsonl` shape. Runnable in CI and on a schedule. | DRIFT-D3 | S | 2.4 |
| 1.8 | **`drift` category in `error_taxonomy`.** A dropped column and a hallucinated column are currently the same error; drift reads as model regression. | DRIFT-D4 | S | 2.4 |
| 1.9 | **`report()` / `logger` split** + manifest fields for the four record-worthy prints (dropped caveats, seed collapse, reference repairs, skipped corpus files). Triage, not a blanket rewrite of 105 prints. | FW-F6 | M | — |
| ~~1.10~~ | ~~**Delete dead Langfuse v2 fallback; reword the v3 comments.**~~ **Done 2026-08-02 (D20)** — the whole Langfuse handler went, fallback included. | FW-F13 | XS | — |
| 1.11 | **Collapse the two `n_human` derivations** into one function. (They are equivalent — not a bug, just derived twice.) | MT-M7 | XS | — |
| 1.12 | **Set `max_turns` at both `InMemoryWorkingMemory` sites; cap the rendered history block.** Both are currently unbounded and verbatim in every prompt. | MT-M4 | S | 2.1 |

---

## Phase 2 — The arms that decide everything after

Four measurement instruments. Phase 3's ordering is guesswork until these exist.

| # | Item | Source | Size | Depends on |
|---|---|---|---|---|
| 2.1 | **Multi-turn arm** — synthetic pronoun follow-ups over BIRD gold (gold unchanged, so grading and leakage are untouched), plus `turn_depth` in the comparability gate. Turns "every number is turn 1" into a number. | MT-M2 | M | 1.3, 1.12 |
| 2.2 | **Pooled-valid out-of-scope negative set** + call `eval_refuse_gate` from a driver. The scorer is built and unit-tested; the cross-DB set inverts under pooling (X6). Refusal recall is half the product and unmeasured. | RT-R5 | M | — |
| 2.3 | **Red-team arm** — four families (out-of-scope-for-pool, scope escape, injection, conversational evasion). The key assertion is an invariant, not a rate: no ledger entry executes SQL touching a table outside that turn's licensed set. | RT-R8 | M/L | 1.3, 2.2 |
| 2.4 | **Drift arm** — point a names-A corpus at `pg_rename_decoy`. A rename map *is* a controlled drift simulation, so this needs no new data. Measures both the drift check's recall and the cost of staleness. | DRIFT-D6 | M | 1.7, 1.8 |

---

## Phase 3 — Build, in the order the arms justify

Split into the two things that are actually independent: making the code shaped for
change, and the retrieval bets.

### 3a. Deepening (module shape)

| # | Item | Source | Size | Notes |
|---|---|---|---|---|
| 3.1 | **`GenerationRow`** — the 70-key dict with 2 producers and 205 `.get()` reads becomes a record. `metrics.py`'s `ROW_*` register dissolves into it. | ARCH-1 | M | top ARCH recommendation; also unblocks 3.9 and 3.3 |
| 3.2 | **`ServeDeployment`** + module-level rails nodes. Kills the 17-kwarg re-threading, the 6 hand-specified construction sites, and the two `inspect.getsource` tests. | ARCH-2 | M/L | |
| 3.3 | **`get_stream_writer()` in the emitting node**; delete `on_event` from five signatures. Fixes the REST-vs-Server timeline asymmetry by construction. | FW-F9 = part of ARCH-2 | M | do with 3.2 |
| 3.4 | **One `Step` value** carrying node name, wire name, `Stage`, and tool bindings. `"schema_route"` vs `Stage.schema_pick` is the same step under two names, on the step we are currently measuring. | ARCH-3 | M | |
| 3.5 | **Layer severity into the verdict**, and the `governance.excluded` predicate into one home (5 spellings across 9 files). Two of five layers currently have no declared disposition. | ARCH-4b = RT-R4 | M | |
| 3.6 | **One declaration per config knob.** Four knobs declared 2–4× with different values; two TOML keys are dead because argparse defaults always win. | ARCH-5 | S/M | |
| 3.7 | **`AssetBag.from_corpus` / `install`**, dicts private, 4 dead aliases deleted. | ARCH-6 | M | after 0.4 |
| 3.8 | **Lift the summariser out of `run_datalake`** — 1,300 lines of statistics, all private, imported by 6 test files via underscore names. Not driver unification (deferred by decision). | ARCH-7 | M | 3.1 helps |
| ~~3.9~~ | ~~**Langfuse scores** from the eval verdicts we already compute.~~ **Void 2026-08-02 (D20)** — no Langfuse. The equivalent on LangSmith is feedback on the run, and the grill already argued against it (`grill-agenda.md` T6.Q3: a second path to a question `generations.<arm>.jsonl` already answers). Re-open only with a question the trace UI can answer and the row files cannot. | FW-F7 | M | 1.1, 3.1 |
| 3.10 | **presenter ↔ `api/schemas` parity test**; redaction becomes a parameter at the view interface rather than a private helper. | ARCH-8 | S | |
| 3.11 | **Delete the six dead public names** (`route_schemas`, `Connector.explain`, two `PromptContext` methods, 4 `AssetBag` aliases, `sanitize_note_text`) and correct `context.py`'s stale contract docstring. | ARCH-9 | S | |

### 3b. Retrieval and conversation (measure, then build)

Every item here should be priced against `retrieval_eval.py` before it is built. Ordered
by cost-to-test, not by expected payoff.

| # | Item | Source | Size | Notes |
|---|---|---|---|---|
| 3.12 | **Act on the coverage signal** — an admission floor in retrieval, *and* route stickiness on follow-ups. One signal, two problems, both currently measured-and-ignored. | BOOK-U-12 + MT-M3 | S | cheapest retrieval win |
| 3.13 | **Retrieve on `(previous turn + current question)`.** Free, deterministic, offline-testable. | MT-M3 | S | with 3.12 |
| 3.14 | **Stopwords out of the BM25 query.** `_QUESTION_STOPWORDS` reaches `lexical_coverage` and not `rank()`, so function words are an unmeasured prose-density prior. | BOOK-U-13 | XS | |
| 3.15 | **Assurance stamp honest on follow-ups** — a blind-retrieval turn must not stamp `unflagged`. | MT-M5 | S | 2.1 |
| 3.16 | **Term-binding propagation** — binding-aware ranking, a binding constraint in the prompt, term fidelity in L4. The reference's strongest anti-hallucination lever, inert in our corpus. | BOOK-U-1 | M | decide explicitly; silence is the worst option |
| 3.17 | **Column-level retrieval units**, enabling ranked and pruned columns. Today every column of every licensed table ships, alphabetically, unpruned. | BOOK-U-11 | L | price via recall@k-vs-prompt-size first |
| 3.18 | **A reranker with stage ① and the `certified` half of ②.** A reranker with one stage is still a reranker, and it is the only way asset quality can affect what the model sees. | BOOK-U-2 | M | 3.16 |
| 3.19 | **Token-unit budgets** instead of item counts. | BOOK-U-14 | M | |
| 3.20 | **Few-shot SQL validated against the schema** in CI — warn, don't block (the reference tells us the right level). | BOOK-U-3 | S | |
| 3.21 | **Unbound `TermAsset` is a CI finding.** An unbound term is a term that cannot route. | BOOK-U-8 | XS | |
| 3.22 | **User-input injection check.** ~10 lines; we built the harder half (corpus-content sanitisation) already. | BOOK-U-4 = RT-R6 | S | |
| 3.23 | **Mixed-case guardrail parametrisation** per dialect, so L3 and L4 agree on the same input. | RT-R7 | S | |

---

## Phase 4 — Needs a decision, not a sprint

Each of these is a real design question. Listed so they are not rediscovered as bugs.

| # | Item | Source | The decision |
|---|---|---|---|
| 4.1 | Serve-turn checkpointer scope | FW-F8 | One scope for the turn, or record why the rails-plus-inner-thread split is deliberate |
| 4.2 | Carry-forward licensing across turns | MT-M6 | Deliberately widens scope, which is what AUDIT S4 narrowed. If adopted, must be visible in the stamp |
| 4.3 | Curator `store` + `CompositeBackend` | FW-F10 | Also the answer to D8 when memory returns — `Store`, not a bespoke table |
| 4.4 | Curator checkpointer with recorded resume | FW-F11 | A budget-exhausted 57-schema build is currently discarded; resume vs. eval hygiene |
| 4.5 | `verified_at` + corpus freshness in the stamp | DRIFT-D5 | Ties drift into the existing stamp instead of a new channel |
| 4.6 | DeepAgents `skills=` for curator instructions | FW-F12 | Prompt size is a step-budget cost; needs measurement |
| 4.7 | Circuit breaker / model fallback chain | BOOK-U-6 | Per-task model assignment is meaningless without an intent taxonomy; the breaker is not |
| 4.8 | L5 numeric `EXPLAIN` cost | BOOK-U-5 | Per dialect; the layer most protective of a production data plane |
| 4.9 | Algebraic rewrites (chasm trap, redundant join, `EXISTS`) | BOOK-U-7 | The reference attributes 80% of its failures to these patterns; needs a complexity signal to gate them |
| 4.10 | Few-shot accumulation from verified successes | BOOK-U-9 | Gated on interaction signals; the reference's three-step approval is a usable blueprint |
| 4.11 | Persist the retrieval index | DRIFT-D7 | Only if a measured cold-start cost justifies it — and then it needs content-keyed invalidation, an exposure we currently don't have |
| 4.12 | `durability="sync"` on checkpointed eval paths | FW-F14 | After 4.1 |
| 4.13 | `WorkingMemory` is a projection, not a store | MT-M8 | Stop rebuilding it per turn from checkpointed `messages`, or say plainly that it is a projection |
| 4.14 | Term-relationship hierarchy | BOOK-B-6 | The one real graph-DB residual. Prototype with the `networkx` we already import; do **not** buy AGE/Neo4j for it |

---

## Explicit non-goals

Recorded so they are not re-proposed. Each was decided, not overlooked.

- **Collapsing the two eval drivers** — deferred by decision. Item 3.8 does not require it.
- **Splitting `guardrails.py`, `validate_corpus`, `middleware.py`, or the `llm/` protocols** — all four are already deep. `test_guardrails.py` and `test_presenter.py` use zero monkeypatching, which is the payoff.
- **Rebuilding the semantic SQL cache** — deleting it was right; rebuilding before a real query distribution exists is premature.
- **A graph database (AGE / Neo4j)** — G-as-retrieval is a deliberate non-build with an equivalent path (Steiner points widen the licensed set). See 4.14 for the only residual.
- **Four observability channels (Kafka / Prometheus / a facade)** — two tracers plus the ledger answer the same questions with three fewer services.
- **RLS / CLS / PII grading** — out of scope for this repo, recorded.
- **Reopening ADR 0002 (agentic core as the only serve path) or ADR 0003 (notes, tri-modal retrieval).**
- **A feature-set expansion** — premature until 2.1 exists. A feature set designed on an unmeasured multi-turn path is guessing.

---

## Appendix — the ARCH candidates, preserved

The architecture review lives only as an ephemeral HTML file. Its nine candidates, so the
analysis survives:

| # | Candidate | Strength | Core finding |
|---|---|---|---|
| 1 | Give the graded question a module | Strong | 70-key untyped dict, 2 hand-written producers, 205 `.get()` reads across 12 modules; `metrics.py:525` documents the failure mode in a comment |
| 2 | Split the serve rails from the deployment | Strong | `build_serve_rails` is 1,032 lines with 13 unaddressable closures; two tests parse its source text with a hand-rolled paren matcher |
| 3 | One step, three string spaces | Strong | graph node / live wire name / durable `Stage` diverge; `"schema_route"` vs `Stage.schema_pick` is one step under two names |
| 4 | Pull governance constants inside the gateway | Strong | severity policy lives in two consumer modules with two types; `max_rows`/`timeout_s` have no `Settings` field; `governance.excluded` has 5 spellings across 9 files |
| 5 | One declaration per knob | Strong | `schema_route_top_k` = 3 / 10 / 10 / 3 in four places; two TOML keys dead because argparse defaults always win |
| 6 | Close `AssetBag`'s six open dicts | Strong | 18 production reach-ins; `tables` keyed by physical name and the rest by id, forcing every caller to branch; Phase B's `if/elif` silently drops notes |
| 7 | Lift the summariser out of the driver | Worth exploring | 1,300 lines of statistics private to a 5,371-line driver; 6 test files import underscore names |
| 8 | Derive the wire shape from the view | Worth exploring | 20 presenter records mirrored by ~25 pydantic models, `from_attributes=True`, no parity test; the only redaction control is a private helper |
| 9 | Delete the interfaces nothing calls | Speculative | 6 dead public names; `context.py`'s docstring still asserts a contract whose seam moved to `middleware.py` |

Vocabulary note: these were written in the `/codebase-design` idiom (module, interface,
depth, seam, adapter, leverage, locality) and the terms are load-bearing.
