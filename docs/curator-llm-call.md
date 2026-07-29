# Agentic BI Curator: LLM Call Walkthrough

This traces the offline curation pipeline (`curator/`) call by call: which step
sends which prompt, what the user message looks like with the placeholders where
dynamic content is injected, and each deep-agent's tool loop shown as an
illustrative transcript. It complements [Curator](curator.md) and
which describe the surrounding design.

**Prompt text itself is not reproduced here.** `_PHASE_A_PROMPT` and
`_PHASE_B_PROMPT` (`curator/prompts.py`) are re-exports of the `curator_phase_a` /
`curator_phase_b` entries in `governed_bi.prompts` — `src/governed_bi/prompts/registry.py`
is the single source, so quoting the text here would drift the moment either is
edited or a variant is added. Read the registry directly, and
[Prompt-variant experiments](prompt-experiments.md) for how a run selects a variant
and why only `v1` exists for these two stages today.

> Implementation: [`prompts.py`](../src/governed_bi/curator/prompts.py),
> [`pipeline.py`](../src/governed_bi/curator/pipeline.py),
> [`seed.py`](../src/governed_bi/curator/seed.py),
> [`deep_agent.py`](../src/governed_bi/curator/deep_agent.py),
> [`prompts/registry.py`](../src/governed_bi/prompts/registry.py).

## Overview: two model-backed steps in the production pipeline

Curation runs per schema, offline, in two model-backed steps plus the deterministic
scaffolding around them (profiling, seeding, validation):

- **(1) Phase A deep agent** — registry stage `curator_phase_a`. Authors the
  semantic layer from (question, gold SQL) pairs and maintains
  `clarifications.jsonl`.
- **(2) Phase B deep agent** — registry stage `curator_phase_b`. Folds
  SME-answered clarifications back into the corpus with certified provenance.

Both deep agents are built by `deep_agent.build_curator_agent`, which wraps
`deepagents.create_deep_agent`, a different harness from the Analyst's `create_agent`:
it adds a filesystem scratchpad (`FilesystemBackend`) so the agent can read/write
`/clarifications.jsonl` with the built-in `ls` / `read_file` / `write_file` /
`edit_file` / `grep` tools, alongside the curator's own grounded tools.
`pipeline.build_curated_corpus` / `pipeline.build_curated_corpus_with_sme` also take
a `settings` parameter now, and stamp the run record each build emits from it rather
than re-deriving config with a fresh `load_settings()` call — see
[Prompt-variant experiments](prompt-experiments.md#why-the-curator-and-sme-producers-had-to-stop-re-deriving-settings)
for why that matters for prompt attribution.

**Aside: the Simulated SME is out of scope here.** Between Phase A and Phase B, an
eval-only component (`curator/sme.py`, `build_sme_brief`) plays the human responder who
answers `clarifications.jsonl`. It has its own model call and its own system prompt
(registry stage `sme_rules` — only the fixed rules block; the rest of the brief is
BIRD column descriptions and train evidence, which the registry does not version). It
is a test harness for the eval-ladder experiment, not part of the production curation
pipeline. See the source file directly if you need its prompt shape.

## (1) Phase A deep agent

`deep_agent.build_curator_agent` builds this agent with
`system_prompt=prompt_text("curator_phase_a", prompt_variants)` (default `v1`, i.e.
`_PHASE_A_PROMPT`) and the tool set from `curator_tools(..., bag=bag)` plus
`FilesystemBackend` file tools. `pipeline.build_curated_corpus` invokes it once per
schema with the full batch of train pairs.

**System prompt:** the `curator_phase_a` registry entry. It sets up the curator as
its own adversary — work the (question, gold SQL) pairs one at a time, call
`read_corpus` to see Facts and prior Inference writes, `run_probe_query` to refute a
claim before asserting it, persist surviving claims via the `upsert_*`/`annotate_*`
tools, and raise a `clarifications.jsonl` entry rather than silently guess when a
table or column's purpose cannot be inferred.

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

## (2) Phase B deep agent

Same harness, same tool set (`curator_tools(..., certified_writes=True)`), different
system prompt and user task. `pipeline.build_curated_corpus_with_sme` invokes it once
per schema after the Simulated SME (or a real SME) has answered the Phase A ledger.

**System prompt:** the `curator_phase_b` registry entry
(`system_prompt=prompt_text("curator_phase_b", prompt_variants)`, default `v1`, i.e.
`_PHASE_B_PROMPT`). It puts the agent in ingest mode: read the answered
`/clarifications.jsonl`, apply each answer via `annotate_*`/`upsert_*` with
`certified=true` and `answered_by` set from the record, and stop once every answered
clarification is reflected in the corpus. `pair:`/`query:`-scoped answers
(data-quality or annotation-error findings raised in Phase A) are not folded this
way — they land as governance rules automatically.

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
its system prompt's method step 4 above.

## End-to-end sequence

1. **Profile** (deterministic, no model): `profile_database` reads the live catalog
   into the Facts tier.
2. **Seed** (deterministic, no model): `seed_from_train_sql` extracts join/metric
   candidates from the train gold SQL via `sqlglot`.
3. **(1) Phase A deep agent**, one agent run for the whole schema, system prompt from
   the `curator_phase_a` registry entry, user task = seed render + train batch; the
   model calls `read_corpus` / `run_probe_query` / `upsert_*` / `annotate_*` / file
   tools repeatedly, writing assets and `/clarifications.jsonl` as it goes.
4. **Validate + optional fix pass** (deterministic `validate_corpus`, then one more
   agent invocation only if findings exist) → the **`curated`** corpus is written.
5. *(Aside, out of scope for this doc)* the Simulated SME (or a real SME) answers
   `/clarifications.jsonl`.
6. **(2) Phase B deep agent**, one agent run, system prompt from the
   `curator_phase_b` registry entry, user task = the fixed ingest instruction above;
   folds answered records into the corpus with `certified=true`.
7. **Validate** again → the **`curated_sme`** corpus is written.

**See also:** [Curator](curator.md) for the proposer/adversary design and the
provenance lifecycle; [Curator](curator.md) for how Phase A/B fit the
eval-ladder experiment; [Prompt-variant experiments](prompt-experiments.md) for the
registry, variant selection, and end-to-end attribution; [Asset schemas](asset-schemas.md)
for what `upsert_*` / `annotate_*` actually write.
