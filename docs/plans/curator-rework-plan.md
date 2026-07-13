# Curator Rework: Agent-Authored Clarifications + Batch SME Round-Trip

_Status: agreed 2026-07-13 (design discussion). Implements the curator vision in
[pipeline-design.md](../pipeline-design.md) §3–§4 concretely, and **replaces** the
mechanical `emit_clarifications`/`resolve_clarifications` gap-filler. Greenlit to
break the existing curator and build it correctly._

## 1. Why (what's wrong today)

The current curator is `profile → seed(joins+metrics) → deep-agent → emit_clarifications → resolve_clarifications`:

- Clarification questions are generated **mechanically** by `emit_clarifications`
  (a per-asset scan: `description is None or confidence < threshold`), **not by
  the agent**, and are stored **per-asset** on `audit.clarification` — scattered,
  not a single curated list, never refined during exploration.
- The SME step answers **one question per asset, in-process** — no batch document,
  no async hand-off.
- The deep agent has **no way to author or dedupe questions**, and in practice its
  enrichment silently no-op'd (see [three-arm-experiment-results.md](three-arm-experiment-results.md)).

The target is different in kind: the agent acts like an **analyst** — it explores
a batch of `(question, SQL)` pairs, builds a *connected* semantic layer, and
**authors and iteratively refines a single consolidated question list** for SMEs.
Then a **batch** SME round-trip fills answers, and the agent **ingests** them.

## 2. Locked design decisions

1. **Agent runtime:** LangChain **`deepagents`** (0.6.12, already installed),
   `create_deep_agent`, with **`FilesystemBackend`** pointed at the run directory
   (real files on disk — inspectable, and the clarifications file *is* the SME
   hand-off artifact). `StateBackend` (ephemeral) / `StoreBackend` (cross-run) are
   not used.
2. **Batch input:** all **20–30 `(question, SQL)` pairs at once** in context (same
   schema → seeing many SQL together yields broader schema understanding). The
   **prompt** directs the agent to work through them **one at a time**, updating
   the ledger and corpus as it goes.
3. **Clarifications ledger = one file, JSONL.** `clarifications.jsonl` on disk
   (via `FilesystemBackend`); one self-contained record per line — maximal
   separation, `grep`-able by the agent, trivially parseable by the later loader.
   Record shape:
   ```json
   {"id":"q001","scope":"table:PlayerInfo.height","question":"Is `height` a literal or an FK into height_info?","status":"open","raised_by":["t14","t22"],"answer":null,"answered_by":null}
   ```
   `id` = stable handle for `grep`/`edit_file`; `scope` + `raised_by` = dedup +
   traceability; `answer`/`answered_by` = the SME columns filled on the round-trip.
4. **One agent, two prompts.** The SME load-back is the **same deep agent with the
   same tools**, a different prompt, and different input: Phase A sees
   `(question, SQL)` pairs; Phase B sees `(question, SME-explanation)` pairs.
5. **Corpus writes go through validated tools, not free file writes.** The tools
   run Pydantic `model_validate` at write time and return the error **that turn**,
   so a malformed "pure-gold" asset can't be persisted and the agent gets located
   feedback immediately — a *tighter* loop than write-files-then-validate. An
   end-of-run `validate_corpus` pass adds the cross-asset check (see §6).

## 3. The toolset (confusion-minimizing surface)

The agent's whole surface, split read vs write:

| Tool | R/W | Purpose |
|---|---|---|
| `read_corpus(table?/kind?)` | read | Return the **live** corpus — Facts (already profiled) **and** the Inference assets written so far — filtered to avoid flooding context. Lets the agent see what's known *and its own writes* (so it doesn't duplicate/contradict). **Replaces `profile_facts`** (reads the in-memory `AssetBag`; no redundant DB introspection). |
| `run_probe_query(sql)` | read | Run a **read-only SELECT** to test a hypothesis against live data (e.g. "do `PlayerInfo.height` values match `height_info.height_id`?"). The static corpus can't answer this — it's the "resolve it yourself" muscle. |
| `upsert_join` | write | Validated `JoinAsset`. |
| `upsert_metric` | write | Validated `MetricAsset`. |
| `upsert_term` | write | Validated `TermAsset`. |
| `upsert_few_shot` | write | Validated `FewShotAsset` (question + working SQL). |
| `annotate_table(table, description?, …)` | write | Table-level Inference. |
| `annotate_column(table, column, description?, role?, reliability?, suspect?)` | write | Column-level Inference — **merges** the old `set_column_description` + `mark_column_suspect` + role/reliability into one tool. |
| built-in file tools (`ls/read_file/write_file/edit_file/grep`) | r/w | **Only** for `clarifications.jsonl`. |

Net domain tools: **2 read + 6 write** (down from `profile_facts` + `run_probe_query`
+ 7 writes), plus the built-in file tools for the ledger. Reads go through
`read_corpus`; writes go through the validated tools; the two can't be confused.

## 4. Memory model (the part that was unclear)

`deepagents` keeps two kinds of state:
- **Chat history (`messages`)** — the ReAct loop's working memory; grows unbounded
  across turns; **not** a place to track "what's been asked" (can't search/edit).
- **Files (`FilesystemBackend`) + the `AssetBag`** — durable, searchable, editable.

So durable state lives in **files (the clarifications ledger)** and the **corpus
(`AssetBag`, via `read_corpus`/write tools)** — never in chat history. The agent
knows what's been asked because it **reads its own file**, and knows what it's
built because it **reads the corpus** — not because it remembers the conversation.
This is what makes the 20–30-pair batch + large question set tractable.

## 5. End-to-end flow

**Phase A — Explore (input: `(question, SQL)` pairs)**
1. `profile_database` builds the Facts tier into the `AssetBag` (unchanged).
2. (Optional) deterministic sqlglot **seed** of joins/metrics from the gold SQL,
   as cheap grounding the agent verifies/extends (see §8).
3. `create_deep_agent` (FilesystemBackend = run dir), all pairs in context.
   Prompt directs: *work pair-by-pair; resolve uncertainties yourself
   (`read_corpus`, `run_probe_query`, cross-reference the other pairs); commit
   validated assets via the write tools; for genuine unknowns, maintain
   `clarifications.jsonl` — `grep` before adding, `edit_file` to broaden/merge an
   existing question rather than duplicate.*
4. `validate_corpus` → surface cross-asset findings → one agent fix pass.
5. `write_corpus` → the curated corpus (the "A2"-equivalent).

**Export → SME (external / async / batch)**
- `clarifications.jsonl` is already on disk — it *is* the consolidated question
  document handed to SMEs. SMEs fill `answer`/`answered_by` on every record.

**Phase B — Ingest (input: `(question, SME-answer)` pairs)**
- Same agent, same tools, **ingest prompt**. It reads the answered
  `clarifications.jsonl` and, using `read_corpus` + the `scope` field to locate the
  right asset(s), folds the SME knowledge in via the **same validated write tools**
  (stamping human/certified provenance). `validate_corpus` → `write_corpus` (the
  "A3"-equivalent).

## 6. Validation & observability

- **Per-write:** every write tool validates (Pydantic) and returns errors inline.
- **End-of-run:** `validate_corpus` (+ the structural adversary as a *signal*, per
  [pipeline-design.md](../pipeline-design.md) §1) → one fix pass.
- **Tracing:** both agent invokes already pass `tracing_callbacks()` (Langfuse;
  LangSmith is env-native). The swallow-all `except` around `agent.invoke` must
  **record the error and the enrichment tool-call counts** into the run manifest,
  so a no-op agent is visible without opening a trace UI.
- **Durable artifacts per run:** `clarifications.jsonl`, the corpus YAML,
  `validate_corpus` findings, `sme_clarifications.jsonl` (the resolved Q&A log),
  and the tool-call/error summary.

## 7. What this removes / replaces

- **Remove** `emit_clarifications` / `resolve_clarifications` (mechanical per-asset
  gap-fill) — the ledger is now agent-authored in a file.
- **Remove** `profile_facts` tool — replaced by `read_corpus`.
- **Remove** per-asset `audit.clarification` as the question store — questions live
  in `clarifications.jsonl`; asset provenance is still stamped on fold-back.
- **Consolidate** the 7 write tools into 6 (`annotate_column` absorbs
  description/role/reliability/suspect).

## 8. Open notes / smaller decisions

- **Keep the deterministic seed?** Recommended **yes** — cheap grounding over the
  same pairs (the agent reads it via `read_corpus` and verifies/extends), and
  insurance if the agent under-delivers. It doesn't violate "pairs-only input" (it
  only reads the pairs). Can be dropped later if the agent proves reliable.
- **Facts profiling stays** — exploration needs the schema; `profile_database`
  feeds the Facts tier, `read_corpus` serves it. ("Pairs-only" is about the *seed
  signal*, not about denying the agent the catalog.)
- `read_corpus` filter args (`table` / `kind`) to bound context on wide schemas.
- `annotate_column` parameter set (`description`, `role`, `reliability`, `suspect`)
  — all optional; a call sets whichever are provided.
- Phase B asset-location: the agent uses the clarification `scope` + `read_corpus`
  to find the target; no separate mapping tool.

## 9. Implementation plan (phased)

1. **Tools:** add `read_corpus` (reads `AssetBag`, filterable); drop `profile_facts`;
   merge writes to 6 (`annotate_table`/`annotate_column`). Unit-test each write
   tool's validation-reject path.
2. **Phase A rewrite:** batch-all-pairs prompt + the pair-by-pair / grep-before-add
   / broaden-merge discipline; wire `FilesystemBackend(run_dir)`; end-of-run
   `validate_corpus` fix pass; record tool-call counts + errors.
3. **Ledger:** define/validate the `clarifications.jsonl` schema + a loader that
   the parser and Phase B share.
4. **Phase B rewrite:** ingest prompt over answered `clarifications.jsonl`; fold via
   the write tools with human/certified provenance.
5. **Remove** `emit_clarifications`/`resolve_clarifications` and their call sites;
   update `run_experiment.py` and tests.
6. **Tests:** ledger dedup/broaden behavior (grep+edit), write-tool validation,
   Phase B fold-back + provenance, and the tracing-callbacks regression already in
   place.

## 10. Acceptance criteria

- Given 20–30 `(question, SQL)` pairs, Phase A produces a corpus with descriptions
  / joins / metrics / terms / few-shots (not just seed joins+metrics) **and** a
  `clarifications.jsonl` with deduped, scoped, agent-authored questions.
- Re-running with an additional pair that broadens a prior question **edits** the
  existing record (same `id`) rather than appending a duplicate.
- Phase B, given answered clarifications, changes the corpus (provenance flips to
  human/certified) and passes `validate_corpus`.
- The run manifest shows enrichment tool-call counts (a no-op agent is visible),
  and every LLM call appears in Langfuse.

---

## 11. Assumptions & non-issues (recorded 2026-07-13)

- **The SME may see the agent's work — including gold SQL quoted in a
  clarification question — and that is NOT leakage.** A real SME is a
  *knowledgeable reviewer*, not a blindfolded oracle: they know their domain
  regardless of glancing at a query. Concretely it's safe because (1)
  clarifications are raised only from **train** pairs; the SME never sees held-out
  **test** questions or their gold; (2) SME answers are **generalizable domain
  facts** ("higher CSS_rank = better prospect", "height is a lookup id"), exactly
  what curation should capture — they carry no test-specific answer.
  `assert_brief_no_leakage` still guards the *brief* against gold-SQL / test-question
  text; the *question* channel is allowed by design. **Residual to watch (separate,
  not leakage):** an SME that *corrects* a trap the test-gold still follows can
  depress EX — a benchmark-vs-truth tension, not a leak.

## 12. Implementation status & changelog (2026-07-13)

Delivered on top of §1–§10:

- **SME is now a read-only deep agent** (`sme.py::build_sme_agent` / `SimulatedSme`):
  `deepagents` + a single `run_probe_query` tool so it can verify domain claims
  against live data; single-shot fallback when no model/gateway. No write tools.
- **SME brief carries ALL evidence** (uncapped, deduped) — the old 40-question cap
  dropped ~half the BIRD `evidence` hints on cs_semester (90) / ice_hockey (67).
- **#4 fixed** — `pair:`/`query:`-scoped clarifications (trap / annotation-error
  findings) now land as `RuleAsset`s via `AssetBag.record_caveats`, so the caveat
  reaches the served corpus instead of dying in the ledger. Phase A prompt no
  longer few-shots a pair whose question and gold SQL disagree.
- **Empty-ledger guard relaxed** — zero SME questions is acceptable (A3 = A2), not
  a failure; seed fallback only on explicit `--skip-agent`.
- **Phase A prompt nudge** — proactively raise clarifications for uncovered /
  missing / inconsistent things; infer-and-record when it can.
- **Deps consolidated** — all extras moved into core (`[project].dependencies`),
  no `--extra`; `langfuse` now installed by default so tracing is live; see
  memory `deps-no-extras`.
- **Bug fixes** — seed alias→physical joins; `grade_semantic_failures` loadable
  from TOML; Postgres `search_path` pin + `SET statement_timeout` literal;
  coverage-decline best-effort; `OpenAiChatClient` removed (untraceable path).
- **Validated live** — v2 three-arm run (see
  [three-arm-experiment-results.md](three-arm-experiment-results.md)); tracing
  confirmed live.

**Open:** `KeyError: 'train_6985'` in one Phase A agent turn (caught, non-fatal);
the LLM `select_schema` node (§5.1, data-lake) is built + exported but **not yet
wired into `flow.py` serve**; N still 17/23 single-seed.
