# Agentic BI Curator: LLM Call Walkthrough

This traces the offline curation pipeline (`curator/`) call by call, showing the
*exact* text each model-backed step sends. It complements [Curator](curator.md) and
[Pipeline design](pipeline-design.md), which describe the surrounding design; here the
goal is narrower: every system prompt reproduced verbatim, every user message shown
with the placeholders where dynamic content is injected, and each deep-agent's tool
loop shown as an illustrative transcript.

> Implementation: [`src/governed_bi/curator/llm_proposer.py`](../src/governed_bi/curator/llm_proposer.py),
> [`prompts.py`](../src/governed_bi/curator/prompts.py),
> [`pipeline.py`](../src/governed_bi/curator/pipeline.py),
> [`seed.py`](../src/governed_bi/curator/seed.py),
> [`deep_agent.py`](../src/governed_bi/curator/deep_agent.py).

## Overview: three model-backed steps

Curation runs per schema, offline, in three model-backed steps plus the deterministic
scaffolding around them (profiling, seeding, validation):

- **(1) Profiling enrichment** via `LlmProposer`: one `chat.complete` call per table,
  producing descriptions and suspect flags as JSON. This is the same single-shot seam
  as the server's schema router/narrator (`chat.complete(system, user)` →
  `LangChainChatClient` builds `[("system", system), ("human", user)]`).
- **(2) Phase A deep agent**: authors the semantic layer from (question, gold SQL)
  pairs and maintains `clarifications.jsonl`. System prompt: `_PHASE_A_PROMPT`.
- **(3) Phase B deep agent**: folds SME-answered clarifications back into the corpus
  with certified provenance. System prompt: `_PHASE_B_PROMPT`.

Both deep agents are built by `deep_agent.build_curator_agent`, which wraps
`deepagents.create_deep_agent`, a different harness from the server's `create_agent`:
it adds a filesystem scratchpad (`FilesystemBackend`) so the agent can read/write
`/clarifications.jsonl` with the built-in `ls` / `read_file` / `write_file` /
`edit_file` / `grep` tools, alongside the curator's own grounded tools.

**Aside: the Simulated SME is out of scope here.** Between Phase A and Phase B, an
eval-only component (`curator/sme.py`, `build_sme_brief`) plays the human responder who
answers `clarifications.jsonl`. It has its own model call and its own system prompt,
but it is a test harness for the three-arm experiment, not part of the production
curation pipeline. See the source file directly if you need its prompt shape.

## (1) Profiling enrichment: `LlmProposer`

`llm_proposer.py`'s `LlmProposer` composes over a base (heuristic) proposer: the
heuristic decides roles/confidence/provenance deterministically, and one model call per
table adds prose descriptions and reliability caveats.

**System prompt (verbatim, `_SYSTEM_PROMPT`; the doubled braces in source are Python
`.format`-style escaping, so the actual prompt uses single braces):**

```text
You are a data curator authoring the semantic layer for a governed analytics system. Given one table's catalog Facts (physical names, types, sample values, inferred roles), write concise, accurate business descriptions and flag any column that looks unreliable or misleading for analysis.

Rules:
- Ground every description in the Facts shown. Do not invent columns, values, or relationships you cannot see.
- Flag a column as "suspect" ONLY when the Facts suggest it is unreliable, ambiguous, or misleading (e.g. a plausible-looking name whose samples contradict it). For a suspect column, write a short note starting with "DO NOT USE".
- Keep descriptions to one sentence.

Return ONLY a JSON object, no prose and no markdown fences, of the form:
{
  "table_description": "<one sentence>",
  "grain": "<what one row represents>",
  "columns": {
    "<physical_column_name>": {
      "description": "<one sentence>",
      "reliability": "ok" | "suspect",
      "note": "<DO NOT USE ... , only when suspect>"
    }
  }
}
```

**User message (assembled by `_render_table_facts`):**

```text
Table physical name: [PHYSICAL_NAME]
Row count: [ROW_COUNT]
Columns:
  - [COLUMN] ([LOGICAL_TYPE], role=[ROLE]); samples: [SAMPLE_1], [SAMPLE_2], ...
  - ... (up to 5 sample values per column)
```

For example, a real `customers` table row would render as:

```text
  - ZipCode (integer, role=dimension); samples: 94256
```

The response is parsed as JSON and applied *over* the heuristic base proposal. Facts
are never mutated by this step, and an unparseable response leaves the base proposal
untouched (fail-safe: `LlmProposer._ask` swallows any exception and returns `None`).

## (2) Phase A deep agent

`deep_agent.build_curator_agent` builds this agent with `system_prompt=_PHASE_A_PROMPT`
and the tool set from `curator_tools(..., bag=bag)` plus `FilesystemBackend` file
tools. `pipeline.build_curated_corpus` invokes it once per schema with the full batch
of train pairs.

**System prompt (verbatim, `_PHASE_A_PROMPT`):**

```text
You are the curator: you author the semantic layer (the Inference tier) for one database from a batch of (question, gold SQL) pairs, and you are your own adversary. Be proactive and curious. Your goal is not merely to cover the given pairs but to understand what this database IS and how it is meant to be used, and to leave a semantic layer where everything is connected. Actively explore tables and columns the pairs do not exercise.

Method:
1. Work through the pairs ONE AT A TIME. For each pair, understand the SQL against the live corpus, then update assets and the clarifications ledger.
2. Call read_corpus (optionally filtered by table/kind) to see Facts and your own Inference writes so far. Never contradict Facts.
3. REFUTE before you assert. Use run_probe_query (read-only SELECT) to falsify non-trivial claims AND to explore tables/columns the questions never touch. Keep only claims that survive.
4. Persist surviving claims via upsert_join, upsert_metric, upsert_term, upsert_few_shot, annotate_table, and annotate_column. If you can infer a meaning/role/join from the SQL, the joins, or the other pairs, that is enough — just write it down (no question needed). Prefer verifying seed candidates over inventing new ones. Columns in the catalog that never appear in working SQL are strong suspect candidates (annotate_column suspect=true). If a pair's question and gold SQL disagree (mislabeled/annotation error), do NOT upsert_few_shot from it — raise a clarification scoped pair:<id> noting the discrepancy instead.
5. RAISE a clarification (do not silently guess) when: a table or column is not touched by any question and you cannot infer its purpose; something looks missing or inconsistent; or a query's structure does not make sense to you and you cannot reconcile it. These are exactly what an SME should confirm. Maintain /clarifications.jsonl with the built-in file tools (ls/read_file/write_file/edit_file/grep). Paths are rooted at / (virtual filesystem). Each line is one JSON object:
   {"id":"q001","scope":"table:T.col","question":"...","status":"open","raised_by":["t14"],"answer":null,"answered_by":null}
   ALWAYS grep before adding. If a prior question covers the same scope, edit_file that record (same id) to broaden/merge rather than appending a duplicate. Do not use file tools for corpus assets — only /clarifications.jsonl.
6. Zero clarifications is acceptable if you genuinely resolved everything, but prefer curiosity: an unexamined table or an unexplained column is usually worth a question. Ground everything in Facts or a probe result; never invent columns or joins.
```

**User task message (`pipeline.py`, joined from these parts with blank lines):**

```text
Curate schema `[SCHEMA]`. Work pair-by-pair; persist via tools.

[SEED_RENDER]

[TRAIN_BATCH]

Create /clarifications.jsonl for genuine unknowns (write_file on first create; grep before add; edit_file to broaden/merge).

Mark unreliable or misleading columns suspect. Propose at least the verified seed joins.

Stop once pairs are covered, seed joins verified, and obviously unreliable columns marked.
```

`[SEED_RENDER]` is `SeedBundle.render()`, the deterministic join/metric candidates
extracted from the train gold SQL by `sqlglot`, offered as "verify, do not invent"
material:

```text
## Deterministic seed candidates (verify, do not invent)
### Joins
- [LEFT_TABLE] ⋈ [RIGHT_TABLE] ON [ON_CLAUSE]
(or "### Joins\n(none extracted)" when there are no candidates)
### Metrics
- [METRIC_NAME]: [EXPRESSION] on [BASE_TABLE]
(or "### Metrics\n(none extracted)" when there are no candidates)
```

`[TRAIN_BATCH]` is `_render_train_batch`, the (question, gold SQL, evidence) pairs to
curate from, capped at 40:

```text
## Train (question, gold SQL, evidence) pairs — curate from these
1. id=[QID] Q: [QUESTION]
   evidence: [EVIDENCE]
   sql: [GOLD_SQL]
2. id=[QID] Q: [QUESTION]
   sql: [GOLD_SQL]
... (up to 40 pairs; "... (N more pairs omitted from prompt)" when there are more)
```

(The `evidence:` line only appears when the item has BIRD evidence text.)

### Phase A tool loop

Grounded tools (`curator_tools`, quoted docstrings, i.e. what the model sees as each
tool's description), plus the built-in file tools scoped to `/clarifications.jsonl`:

- **`read_corpus(table="", kind="")`**: "Return the live corpus — Facts and Inference
  written so far. Optional table (physical name) and kind (table/join/metric/term/
  few_shot) filters bound context on wide schemas."
- **`run_probe_query(sql)`**: "Run a read-only SELECT to confirm or falsify a claim
  about the data. Returns the rows (truncated) or an error string. Never mutates data."
- **`upsert_join(left_table, right_table, on, ...)`**: "Record a validated JoinAsset
  between two physical tables."
- **`upsert_metric(name, base_table, expression, ...)`**: "Record a validated
  MetricAsset (aggregate over a base table)."
- **`upsert_term(name, ...)`**: "Record a validated TermAsset mapping business
  language to an asset."
- **`upsert_few_shot(question, sql, ...)`**: "Record a validated FewShotAsset
  (question + working SQL)."
- **`annotate_table(table, description="", ...)`**: "Set table-level Inference fields
  (description, confidence)."
- **`annotate_column(table, column, description="", role="", reliability="",
  suspect=False, note="", ...)`**: "Set column Inference: description, role,
  reliability, and/or suspect."

**Illustrative transcript:**

```text
assistant → read_corpus(table="[TABLE]")
tool     → [FACTS + INFERENCE WRITTEN SO FAR FOR TABLE]

assistant → run_probe_query(sql="[PROBE SELECT]")
tool     → [ROWS, truncated]  # or "error: [MESSAGE]"
            # ^ REFUTE before asserting: the model checks a claim before writing it

assistant → annotate_column(table="[T]", column="[C]", suspect=true, note="DO NOT USE ...")
tool     → ok: [ASSET_ID] updated

assistant → upsert_join(left_table="[L]", right_table="[R]", on="[ON_CLAUSE]")
tool     → ok: [ASSET_ID] created

assistant → grep("[SCOPE]", "/clarifications.jsonl")   # check for an existing record first
tool     → [MATCHING LINES, or none]
assistant → write_file("/clarifications.jsonl", ...)    # or edit_file to merge/broaden
tool     → ok
```

One line of `/clarifications.jsonl`, exactly the shape given in the prompt:

```json
{"id":"q001","scope":"table:T.col","question":"...","status":"open","raised_by":["t14"],"answer":null,"answered_by":null}
```

## (3) Phase B deep agent

Same harness, same tool set (`curator_tools(..., certified_writes=True)`), different
system prompt and user task. `pipeline.build_curated_corpus_with_sme` invokes it once
per schema after the Simulated SME (or a real SME) has answered the Phase A ledger.

**System prompt (verbatim, `_PHASE_B_PROMPT`):**

```text
You are the curator in ingest mode. SMEs have answered clarifications.jsonl. Your job is to fold those answers into the Inference tier.

Method:
1. Read /clarifications.jsonl (file tools). For each answered record, use its scope field plus read_corpus to locate the target table/column/asset.
2. Apply knowledge via annotate_table / annotate_column / upsert_* tools. Stamp human-certified provenance by setting certified=true (and answered_by from the record) on those writes.
3. Do not invent new open questions. Prefer editing existing assets over duplicating them. Use run_probe_query only if an answer still needs a data check.
4. Focus on table:/column:/join:/metric: scoped answers. Answers scoped pair: or query: (data-quality or annotation-error findings) are recorded as governance rules automatically — you do not need to act on those.
5. Stop once every answered clarification has been reflected in the corpus.
```

**User task message (verbatim, `pipeline.py`):**

```text
Ingest answered clarifications for schema `[SCHEMA]`. Read /clarifications.jsonl and fold each answered record into the corpus via annotate/upsert tools with certified=true.
```

### Phase B tool loop

Same tools as Phase A, but every write now carries certified provenance
(`certified=true`, `answered_by=[SME]`):

```text
assistant → read_file("/clarifications.jsonl")
tool     → [ANSWERED RECORDS, one JSON object per line]

assistant → read_corpus(table="[TABLE_FROM_SCOPE]")
tool     → [FACTS + INFERENCE SO FAR]  # locate the asset the record's `scope` names

assistant → annotate_column(table="[T]", column="[C]", description="[ANSWER-DERIVED TEXT]", certified=true, answered_by="[SME]")
tool     → ok: [ASSET_ID] updated
```

Answers scoped `pair:` or `query:` (data-quality or mislabeled-annotation findings raised in
Phase A step 5) are not folded via `annotate_*`/`upsert_*`; they land as governance
rules automatically (`bag.record_caveats`), so Phase B's own tool calls skip them per
Method step 4 above.

## End-to-end sequence

1. **Profile** (deterministic, no model): `profile_database` reads the live catalog
   into the Facts tier.
2. **(1) Profiling enrichment**, one `LlmProposer` call **per table**: system +
   `_render_table_facts(table)` → JSON description/suspect payload, layered over the
   heuristic base proposal.
3. **Seed** (deterministic, no model): `seed_from_train_sql` extracts join/metric
   candidates from the train gold SQL via `sqlglot`.
4. **(2) Phase A deep agent**, one agent run for the whole schema, system prompt
   `_PHASE_A_PROMPT`, user task = seed render + train batch; the model calls
   `read_corpus` / `run_probe_query` / `upsert_*` / `annotate_*` / file tools
   repeatedly, writing assets and `/clarifications.jsonl` as it goes.
5. **Validate + optional fix pass** (deterministic `validate_corpus`, then one more
   agent invocation only if findings exist) → **A2 corpus** written.
6. *(Aside, out of scope for this doc)* the Simulated SME (or a real SME) answers
   `/clarifications.jsonl`.
7. **(3) Phase B deep agent**, one agent run, system prompt `_PHASE_B_PROMPT`, user
   task = the fixed ingest instruction above; folds answered records into the corpus
   with `certified=true`.
8. **Validate** again → **A3 corpus** written.

**See also:** [Curator](curator.md) for the proposer/adversary design and the
provenance lifecycle; [Pipeline design](pipeline-design.md) for how Phase A/B fit the
three-arm experiment; [Asset schemas](asset-schemas.md) for what `upsert_*` /
`annotate_*` actually write.
