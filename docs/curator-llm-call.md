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
[Prompt-variant experiments](prompt-experiments.md) for how a run selects a variant.
`curator_phase_a` now has a `v2` (`v1` is unchanged and still the default);
`curator_phase_b` and `sme_rules` carry only `v1`.

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

One thing about that block is worth knowing here, because it is a trap the curator
side shares: the SME holds a read-only `run_probe_query` tool, and the rules bar SQL
from its **answer** only, not from the tool call. The two used to disagree — the rules
banned queries outright while the user message in the same call invited a probe — and
the model resolved it by refusing to answer, which cost 11 of 381 clarifications. See
[Deleting a variant](prompt-experiments.md#deleting-a-variant).

## (1) Phase A deep agent

`deep_agent.build_curator_agent` builds this agent with
`system_prompt=prompt_text("curator_phase_a", prompt_variants)` (default `v1`, i.e.
`_PHASE_A_PROMPT`) and the tool set from `curator_tools(..., bag=bag)` plus
`FilesystemBackend` file tools. `pipeline.build_curated_corpus` invokes it once per
schema with the full batch of train pairs.

**System prompt:** the `curator_phase_a` registry entry. It sets up the curator as
its own adversary — call `read_corpus` to see Facts and prior Inference writes,
`run_probe_query` to refute a claim before asserting it, persist surviving claims via
the `upsert_*`/`annotate_*` tools, and raise a `clarifications.jsonl` entry rather than
silently guess when a table or column's purpose cannot be inferred. `v1` tells it to
work the (question, gold SQL) pairs one at a time; `v2` tells it to batch, group the
pairs by the tables they touch, run the reliability sweep as one `annotate_columns` per
table against `read_corpus(todo_only=true)`, and treat re-verifying seeded joins and
metrics as the first work to drop. See
[Prompt-variant experiments](prompt-experiments.md#the-four-real-variants) for what
would refute `v2`.

**User task message (`pipeline.py`, joined from these parts with blank lines):**

```text
Curate schema `[SCHEMA]`. Work pair-by-pair; persist via tools.

[SEED_RENDER]

[TRAIN_BATCH]

Create /clarifications.jsonl for genuine unknowns (write_file on first create; grep before add; edit_file to broaden/merge).

Mark unreliable or misleading columns suspect. Propose at least the verified seed joins.

Stop once pairs are covered, seed joins verified, and obviously unreliable columns marked.

## Budget
You have about [N] tool calls. Several tool calls in ONE reply cost the same as one, so batch aggressively — emit all the probes for a table together, and use annotate_columns to do a whole table's columns in a single call ([N_TABLES] tables here).
If you cannot do everything, this is the order that matters, most first:
1. Mark unreliable columns suspect (annotate_columns). ...
2. Describe what tables and columns mean.
3. Raise clarifications for genuine unknowns.
4. Few-shots and terms.
5. Re-verifying seeded joins and metrics — they are already recorded, so this is the first thing to skip.
Do not re-issue a call you have already made; read_corpus(todo_only=true) tells you what is left.
```

The `## Budget` block is `_budget_brief(tool_calls, n_tables=...)` in `pipeline.py`
(abridged above; read it there for the full text). `[N]` is the resolved tool-call
budget for this schema, so the figure differs per schema. It exists because nothing
else in the context mentioned a limit while the deepagents base prompt says to keep
working until the task is fully complete, and because the stages that died when the
budget ran out were the ones no other mechanism produces. See
[the step budget](curator.md#the-step-budget).

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

- **`read_corpus(table="", kind="", todo_only=False)`**: "Return the live corpus — Facts
  and Inference written so far. Optional table (physical name) and kind (table/join/
  metric/term/few_shot) filters bound context on wide schemas. Set todo_only=true to
  list ONLY the columns still lacking both a description and a suspect mark — a worklist
  that shrinks as you write, and the cheapest way to check whether the reliability sweep
  is done." The render is capped at `READ_CORPUS_MAX_CHARS` (20,000) and says so when it
  truncates.
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
- **`annotate_columns(table, columns: list[dict])`**: "Annotate MANY columns of one
  table in a single call — the way to do the reliability sweep. … Returns one result
  line per column; a column that fails does not stop the others." Each dict needs
  `column` plus any of `description` / `role` / `reliability` / `suspect` / `note` /
  `confidence`, with the same per-column semantics as `annotate_column`. It swallows a
  per-spec exception on purpose: a raising tool returns nothing at all, so one bad spec
  would cost every good annotation in the call and the agent would redo them, which is
  the churn the tool exists to prevent.

That is seven write tools. `annotate_columns` is the one that exists for the step
budget rather than for a new capability: the reliability sweep is per column over
schemas up to 703 columns wide, and one call per column made the budget scale with
schema width.

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

Same harness, same tool set, different system prompt and user task.
`pipeline.build_curated_corpus_with_sme` invokes it once per schema after the Simulated
SME (or a real SME) has answered the Phase A ledger. `curator_tools` takes a
`certified_writes` flag but the pipeline never sets it and it would not matter if it
did: every write tool passes `certified=False`, because certification is a human,
non-agent path (D6/C6).

**System prompt:** the `curator_phase_b` registry entry
(`system_prompt=prompt_text("curator_phase_b", prompt_variants)`, default `v1`, i.e.
`_PHASE_B_PROMPT`). It puts the agent in ingest mode: read the answered
`/clarifications.jsonl`, locate each target from the record's `scope`, apply the answer
via `annotate_*`/`upsert_*`, and stop once every answered clarification is reflected in
the corpus. Its step 2 is explicit that writes carry curator/proposed provenance and
that the agent must not claim human certification, because that stamp belongs to the
non-agent fold path. `pair:`/`query:`-scoped answers
(data-quality or annotation-error findings raised in Phase A) are not folded this
way — they land as governance rules automatically.

**User task message (verbatim, `pipeline.py`):**

```text
Ingest answered clarifications for schema `[SCHEMA]`. Read /clarifications.jsonl and fold each answered record into the corpus via annotate/upsert tools (curator/proposed provenance only). There are [N_ANSWERED] answered record(s). You have about [N] tool calls; several calls in ONE reply cost the same as one, so batch them, and use annotate_columns for several columns of the same table at once.
```

`[N]` is `30 + 3 * [N_ANSWERED]`, derived rather than constant for the same reason Phase
A's budget is, and because Phase B's work scales with the ledger (one locate plus one
write per record) rather than with schema width.

### Phase B tool loop

Same tools as Phase A, and the exposed wrappers accept neither `certified` nor
`answered_by`: those are `AssetBag` parameters the deterministic fold
(`mark_unrecognised_columns`, `record_caveats`) uses, not agent-callable arguments.

```text
assistant → read_file("/clarifications.jsonl")
tool     → [ANSWERED RECORDS, one JSON object per line]

assistant → read_corpus(table="[TABLE_FROM_SCOPE]")
tool     → [FACTS + INFERENCE SO FAR]  # locate the asset the record's `scope` names

assistant → annotate_columns(table="[T]", columns=[{"column":"[C]","description":"[ANSWER-DERIVED TEXT]"}, ...])
tool     → ok: [ASSET_ID] updated
            ok: [ASSET_ID] updated
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
   the `curator_phase_a` registry entry, user task = seed render + train batch +
   `## Budget`; the model calls `read_corpus` / `run_probe_query` / `upsert_*` /
   `annotate_*` / file tools repeatedly, writing assets and `/clarifications.jsonl` as
   it goes. The run is streamed under `recursion_limit = 3 * budget + 4` and every tool
   call lands in `curator_trace.jsonl`.
4. **Validate + optional fix pass** (deterministic `validate_corpus`, then one more
   agent invocation only if findings exist, on `max(budget // 2, 8)` tool calls) → the
   **`curated`** corpus is written.
5. *(Aside, out of scope for this doc)* the Simulated SME (or a real SME) answers
   `/clarifications.jsonl`.
6. **(2) Phase B deep agent**, one agent run, system prompt from the
   `curator_phase_b` registry entry, user task = the ingest instruction above; folds
   answered records into the corpus with curator/proposed provenance, traced to
   `curator_sme_trace.jsonl`.
7. **Validate** again → the **`curated_sme`** corpus is written.

**See also:** [Curator](curator.md) for the proposer/adversary design and the
provenance lifecycle; [Curator](curator.md) for how Phase A/B fit the
eval-ladder experiment; [Prompt-variant experiments](prompt-experiments.md) for the
registry, variant selection, and end-to-end attribution; [Asset schemas](asset-schemas.md)
for what `upsert_*` / `annotate_*` actually write.
