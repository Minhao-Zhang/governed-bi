# Prompt-variant experiments: registry, selection, attribution

Prompts used to be bare module-level strings, so a prompt was identified by the
file it lived in and nothing else. Two consequences, both measurement
failures: the `baseline`/`curated`/`curated_sme` ladder is a **corpus-content**
axis and sends byte-identical prompt text across all three arms, so changing
the arm never changed a prompt and nothing recorded that; and `serve_config_hash`
had no notion of prompt text, so "we changed a prompt and EX moved" was
unfalsifiable after the fact — two runs on different prompts were
indistinguishable in the record, and an *edited* prompt was indistinguishable
from the prompt it replaced.

`src/governed_bi/prompts/registry.py` fixes both: a stage maps to a set of
named variants, a run resolves one variant per stage, and that map is hashed
**over the text** and stamped end-to-end. This doc is the runbook — how to add
a variant, select one, read what got stamped, and decide which variant a
measured failure actually calls for.

> Implementation: [`src/governed_bi/prompts/registry.py`](../src/governed_bi/prompts/registry.py),
> [`src/governed_bi/prompts/__init__.py`](../src/governed_bi/prompts/__init__.py).
> Tests: [`tests/test_prompt_registry.py`](../tests/test_prompt_registry.py),
> [`tests/test_prompt_attribution.py`](../tests/test_prompt_attribution.py),
> [`tests/test_prompt_attribution_gaps.py`](../tests/test_prompt_attribution_gaps.py).

## The registry

`PromptVariant(stage, variant, text, rationale)` is one named prompt text for
one stage. `rationale` is not decoration: the dataclass docstring says it
plainly — "a variant whose rationale names no observable failure mode is a
knob, not an experiment." `REGISTRY` (`stage -> variant id -> PromptVariant`)
is built from a flat tuple at import time and raises `RuntimeError` on a
duplicate `(stage, variant)` pair, so a copy-paste typo in a new entry fails at
import, not at some later lookup. `DEFAULTS` maps every stage to `"v1"`.

Six registered stages: `agent_core`, `schema_pick`, `narrator`,
`curator_phase_a`, `curator_phase_b`, `sme_rules`. Five of the six `v1`s are
byte-identical to the text this system sent before the registry existed; the
exception is `sme_rules`, whose original `v1` and `v2` were both deleted and
replaced (see [Deleting a variant](#deleting-a-variant)). The old module-level
constants (`SYSTEM_PROMPT` in `analyst/agent.py`, `SCHEMA_PICK_SYSTEM` in
`retrieval/schema_router.py`, `_NARRATOR_SYSTEM` in `analyst/narrate.py`,
`_PHASE_A_PROMPT`/`_PHASE_B_PROMPT` in `curator/prompts.py`,
`_SME_SYSTEM_RULES` in `curator/sme.py`) are now *derived* from the registry
(`prompts.get(stage).text`) rather than holding their own copy, so the call
site and the registry cannot silently disagree — `test_prompt_registry.py`
pins a sha256 digest of each stage's `v1` text as an extra guard against an
in-place edit quietly redefining the baseline every measured number was taken
against.

The functions:

- `get(stage, variant="v1")` — one `PromptVariant`, or `KeyError` naming the
  valid ids.
- `resolve(overrides)` — the full `stage -> variant` map for a run: `DEFAULTS`
  plus overrides, every stage always present. A partial map, an empty map, and
  the explicit full-default map all resolve identically — the map describes
  what was *sent*, not how the caller happened to spell it.
- `text(stage, variants_map)` — the text `stage` should send under that map.
- `prompt_set_hash(variants_map)` — sha256 over the sorted
  `(stage, variant, sha256(text))` triples. The **text** digest is in the
  payload, not just the variant id, so editing `v1` in place moves this hash —
  the exact trap `serve_config_hash`'s hand-maintained field list fell into
  before this module existed.
- `parse_cli_overrides(items)` — turns repeated `--prompt stage=variant`
  strings into a validated override map.
- `stages()` / `variants(stage)` — the known ids, for error messages and CLI
  help text.

Text and pure functions only: no I/O, no settings import, no model. Both the
serve path and the curator path import it, and `provenance.py` hashes from it,
so a dependency cycle would break both directions if this module ever grew one
(same shape as `governed_bi.stages` — see [`measurement.md`](measurement.md)).

## The four real variants

Everything below `v1` exists because a specific failure mode, measured by
`eval.analysis` or `summary.json`, named it. Quoting the rationales as written
in `registry.py`, so they stay accurate as the registry grows:

| Stage | Variant | Rationale |
|---|---|---|
| `schema_pick` | `v2` | "Forces one explicit rejection reason per candidate, turning a topical-similarity guess into a column-vocabulary check, and moves the answer onto a strict FINAL: line. Refuted if `pick_accuracy` in the `by_gold_rank['1']` bucket does not rise — no other bucket is its fault." |
| `agent_core` | `v2` | "Makes the suspect/duplicate-copy check its own step with visible output, so a long context cannot bury it. Refuted if `n_selection_miss` does not fall with `n_retrieval_miss` flat (also watch `decoy_touch_rate` and `total_tokens`)." |
| `agent_core` | `v3` | "Commits to the output columns and grain before writing SQL, targeting the right-rows/wrong-projection class. Refuted if `n_wrong_but_nrows_match` does not fall, or falls without `ex_gradeable` rising by about the same count." |
| `curator_phase_a` | `v2` | "Re-budgets v1 rather than rewriting it: same contracts, different cost. The 2026-07-29 run capped 30 of 57 Phase A agents at the step limit, and the prompt is half the cause … v2 batches explicitly, does the sweep as one `annotate_columns` per table with `read_corpus(todo_only=true)` as the worklist, says seeded joins/metrics are already recorded and are the first thing to drop, and states the 40-pair render cap. Refuted if the cap rate does not fall, or if it falls while suspect coverage per column drops or `decoy_touch_rate` on the curated arms rises … Watch `repeat_summary.distinct/total` for the churn it targets." |

Concretely: `schema_pick@v2` makes the picker write one line per candidate —
either the columns that cover every part of the question, or the first part it
cannot supply — before naming a schema on a `FINAL: <schema name>` line (`v1`
only asks it to reason freely and name the schema on the final line).
`agent_core@v2` turns "read each table's description before choosing" into its
own numbered step that must state which table it rejected and why.
`agent_core@v3` adds a step zero: state the exact output columns and grain
*before* writing SQL, then check the `SELECT` list against that statement and
delete anything not on it.

`curator_phase_a@v2` is the odd one out: its target is not accuracy but *completion*.
It keeps every contract `v1` states and changes what they cost. Three things in `v1`
spend the budget without buying anything. "Work through the pairs ONE AT A TIME" is the
worst of them, because N tool calls in one assistant message cost a single super-step
while N calls across N replies cost 3N, so serialising is pure loss. The reliability
sweep runs column by column off an unfiltered `read_corpus` that grows as the agent
writes. And the close, "prefer curiosity", is unbounded pressure against a bounded
loop. `v2` batches, sweeps with one `annotate_columns` per table against
`read_corpus(todo_only=true)`, names the seeded joins and metrics as already recorded
and therefore droppable, and stated the 40-pair render cap that `_render_train_batch`
imposed at the time. `v1` is unchanged and remains the default, so the comparison is
available rather than assumed. See [the step budget](curator.md#the-step-budget) for the
run that produced the cap rate.

`v3` (2026-07-30) changes what the curator asks about, and pairs with the intake fix
that removed the 40-pair ceiling, so its batching paragraph replaces v2's statement of
that cap. Three changes: clarifications move from rank 3 to rank 2 in the triage order,
because nothing else in the system asks a person anything; step 6 describes who reads
the questions, a domain expert holding column documentation who has never seen this
database and cannot run a query, and lists the four kinds worth asking; and count-based
questions are ruled out explicitly, since a row-count anomaly is the curator's own
finding to record with `annotate_columns(suspect=true, note=…)` rather than something an
SME can answer. Step 7 is the quota. Motivated by measurement rather than taste: on the
20260730 run the curator raised a median of 3 clarifications per schema against roughly
104 columns, and 45.7% of the answers it did get disclaimed knowledge of the object it
asked about. See [plans/sme-channel-repair.md](plans/sme-channel-repair.md) F2 and F3.

Note that `_budget_brief` in `curator/pipeline.py`, not the registered variant, is what
ranks clarifications for `v1` and `v2`. It is un-versioned code sent on every run, so it
takes a `triage` flag whose default branch is byte-identical to the shipped text;
`_SELF_TRIAGING_PHASE_A_VARIANTS` suppresses it for variants that carry their own order.
Editing that text directly would have silently redefined the baseline of every run
already stamped `v1` or `v2`. One naming slip to know about: the rationale points at
`repeat_summary.distinct/total`, and the field is `tool_calls.repeats` in
`run_manifest.json`.

`narrator`, `curator_phase_b`, and `sme_rules` have only `v1` today, each for a reason
recorded in its own rationale: the narrator "runs after grading and cannot move EX"
(there is no failure mode a narrator variant could be measured against); a
`curator_phase_b` variant "means rebuilding every corpus to test it" (no cheap A/B
against an already-built corpus); `sme_rules` is "the rules block inside the
code-assembled SME brief (the rest of that brief is data, not a prompt
variant)" — the bulk of the brief is BIRD column descriptions and train
evidence, which the registry has no business versioning.

## Deleting a variant

`sme_rules` is the one stage where a variant was deleted rather than added, and
the reasoning generalises. Its `v1` and its `v2` candidate both said "Never
write database queries. Describe meaning in prose only." while the runtime user
message in the *same* model call (`SimulatedSme.answer`) said "You may run
read-only probe queries to check the data first if it helps." Measured over 381
real clarifications, 11 answers (2.9%) came back as the canned "Unsure —
declining to invent a definition" fallback, concentrated on the
decoy-confirmation questions the curator most needed answered (`card_games`,
`restaurant` and `world` at 25% each), with transcripts visibly stuck between
the two rules: *"I can't write SQL, but I can describe the intended logic."*

Neither was kept as a baseline. A prompt that contradicts its own call site is
not a measurement worth preserving comparability with, and the numbers taken
under it are discarded anyway. The replacement permits the `run_probe_query`
tool explicitly, restricts the SQL ban to the answer text — which is all
`_sanitize_sme_answer` enforces — and keeps `v2`'s absent-identifier rule, the
part that turns the brief's silence about a decoy into an answer.

Two things follow for anyone doing this again. The pinned digest in
`V1_DIGESTS` (`tests/test_prompt_registry.py`) has to be repinned, and that
edit is the deliberate act of discarding the prior baseline — it is not a test
fix. And `prompt_set_hash` moves for *every* run, including default ones, so no
run recorded before the swap is comparable to one after it.

## Adding a variant

Append a `PromptVariant(stage=..., variant=..., text=..., rationale=...)` to
the `_ALL` tuple in `registry.py`, with a rationale that names the metric that
would refute it. Nothing else has to change for it to become selectable:
`get`/`resolve`/`text`/`prompt_set_hash` all read `REGISTRY` directly, and
`--prompt`/`[prompts]` validate against `variants(stage)`. A duplicate
`(stage, variant)` id raises `RuntimeError` at import time, so a typo that
collides with an existing id fails immediately rather than silently shadowing
one entry.

Adding a variant of an *existing* stage (say `agent_core@v4`) needs nothing
else. Adding a *new stage* also needs: a `v1` baseline registered for it, a
call site that derives its constant from `prompts.get("new_stage").text`
instead of holding its own copy, and a pinned digest added to
`V1_DIGESTS` in `test_prompt_registry.py` —
`test_every_registered_stage_has_a_pinned_v1_digest` asserts
`set(prompts.stages()) == set(V1_DIGESTS)` and fails otherwise.

For `curator_phase_a` / `curator_phase_b` / `sme_rules` specifically: there is
no cheap way to try a new variant against an already-built corpus. Testing one
means rebuilding `curated` / `curated_sme` under it, which is why `curator_phase_b` and
`sme_rules` still carry only `v1`. `curator_phase_a@v2` was worth the rebuild because
the failure it targets was throwing away whole schemas, not shaving a rate.

## Selecting a variant

Two independent mechanisms, and they do not compose the way you might expect.

**`[prompts]` in `governed_bi.toml`** (`Settings.prompt_variants`) is what the
*live serve stack* reads — `api.stack.build_stack` calls `load_settings()` and
nothing else, so this TOML table is the only way a deployment can run on a
non-default prompt. Validated at load time: `load_settings()` calls
`prompts.resolve()` over the `[prompts]` table and re-raises a bad stage or
variant as `ValueError` naming the config path, so a typo takes down the whole
process at startup instead of silently serving `v1` while the file claims
`v9`.

```toml
[prompts]
schema_pick = "v2"
agent_core = "v3"
```

**`--prompt STAGE=VARIANT`** (repeatable) is a CLI flag on
`eval/run_datalake.py`, parsed by
`parse_cli_overrides()` and resolved *before* any Postgres connection or model
call — a bad `--prompt` is a `parser.error()` usage exit, not a crash mid-run.

`run_datalake()` builds its `Settings` from
`Settings.for_env(Environment.dev, models=base_settings.models, ...)` — only
`.models` is carried forward from `load_settings()`, and `prompt_variants` is
set separately from `resolve_prompts(prompt_variants)` where `prompt_variants`
is whatever `--prompt` produced (empty if none was passed). **Setting
`[prompts]` in `governed_bi.toml` has no effect on the eval driver** — for
an experiment, `--prompt` is the only lever.

## What gets stamped, hop by hop

1. `Settings.prompt_variants` — a partial or full `stage -> variant` map.
2. `build_serve_rails` resolves it **once per stack build**, not once per turn:
   `prompt_variants = prompts.resolve(settings.prompt_variants)`, then
   `agent_core_prompt` / `schema_pick_prompt` are computed once and closed
   over by the graph's nodes. `agent_core_node` appends `## Governed context`
   and `## Current time` *after* the variant text — the variant replaces the
   instruction block, never the assembled context.
3. `serve_config_hash(settings)` folds in `prompt_set_hash(settings.prompt_variants)`
   unconditionally. Even a run that selects nothing hashes `v1`'s text, so
   editing `v1` in place moves `serve_config_hash` for every default run too,
   not only for runs that opted into a variant.
4. `Answer.provenance` carries `prompt_variants` (the full resolved map) and
   `prompt_set_hash`, stamped by `finalize_and_log` / `emit_run_record`. Both
   keys are listed in `METADATA_PROVENANCE_KEYS`
   (`src/governed_bi/analyst/run_log.py`), the set every terminal `Answer`
   must carry, alongside `turn_id`, `run_id`, `corpus_release_hash`, and the
   rest.
5. The portable run record (`load_run_record`) carries the same two keys, so a
   turn looked up outside eval — from `runs/` or the durable log — still says
   which prompt set produced it.
6. `eval.arms.agent_solver` relays the stamp from `Answer.provenance` into the
   solver's per-question metadata. An **unstamped** turn relays `None` for
   both keys, never the `v1` defaults — "nothing recorded which prompt ran"
   and "`v1` ran" are different facts, and only the second may print as `v1`.
7. The scored row in `generations.<arm>.jsonl` carries `prompt_variants` /
   `prompt_set_hash` (`_run_pool_arm` in `run_datalake.py`).
8. `manifest.json` carries the resolved map and
   the hash. In `run_datalake.py` the hash is also a `_RESUME_KNOBS` entry
   (see Fail-closed below).
9. `eval.index.COMPARABILITY_KEYS` includes `prompt_set_hash`, so
   `runs/index.jsonl`'s `comparable(a, b)` flags a prompt-set difference
   between two runs by name.

## Why the curator and SME producers had to stop re-deriving Settings

`build_curated_corpus`, `build_curated_corpus_with_sme`, and `SimulatedSme`
now take a `settings` parameter and stamp their run records
(`emit_run_record`) from it — via `pipeline._settings_or_load(settings)` and
`SimulatedSme._resolved_settings()` — instead of calling `load_settings()`
fresh. Both helpers fall back to a fresh load **only** when handed `None`
(standalone CLI use with no caller-resolved config).

This matters because of a gap an adversarial review found after the registry
landed (see `tests/test_prompt_attribution_gaps.py`): a corpus built with
`--prompt curator_phase_a=v2` still ran its curator agent on the `v2` text
(threaded explicitly via `system_prompt=prompt_text("curator_phase_a", ...)`),
but if the run-record stamp came from a *fresh* `load_settings()` call instead
of the caller's resolved `Settings`, that record would read whatever
`governed_bi.toml`'s `[prompts]` says — `v1` by default, since (per the
section above) the eval driver doesn't even read `[prompts]`. The practical
consequence: querying the log for "every turn produced under prompt set X"
would return the serve turns (correctly stamped, because `answer_question_agent`
always has the caller's `settings` in scope) but silently miss the
curator/SME turns that built the corpus those serve turns queried — the two
halves of one experiment, one attributed correctly and one not, with no signal
that anything was wrong.

## Fail-closed: every place this raises rather than falling back

An unknown stage or variant must never resolve to `v1` — the whole point of
tracking prompt identity is that a run reports the variant it actually sent.
Concretely, from the test suite:

- `prompts.get("agent_core", "v9")` / `prompts.get("sqlgen", "v1")` — `KeyError`
  naming the valid variants or known stages.
- `prompts.resolve({"agent_core": "v9"})` — `KeyError`, not a silent fall back
  to `v1` for that one stage.
- `--prompt schema_pick` / `--prompt =v2` / `--prompt schema_pick=` /
  `--prompt ""` / `--prompt "schema_pick:v2"` — `ValueError` from
  `parse_cli_overrides`, surfaced as a `parser.error()` usage exit before the
  CLI touches Postgres or a model.
- `--prompt agent_core=v2 --prompt agent_core=v3` — `ValueError` ("twice");
  repeating the *same* value twice (`agent_core=v2 --prompt agent_core=v2`) is
  harmless and stays legal, since it says nothing contradictory.
- `[prompts]` in `governed_bi.toml` naming an unknown stage or variant —
  `ValueError` from `load_settings()`, naming the config path, so a typo takes
  down the whole process at startup.
- **A `--resume-from` under a different `prompt_set_hash` is fatal**
  (`RuntimeError`, message contains "prompt set"), unlike every other
  `_RESUME_KNOBS` entry (`model`, `route_top_k`, `route_llm_pick`,
  `schema_pick_max_columns`, `use_embedder`, `skip_agent`, `git_sha`), which
  only print a `*** WARNING: resuming ... with changed knobs ***` and continue.
  This one had to be escalated from a warning after review: `_merge_resume_manifest`
  keeps the *original* manifest's top-level knobs and files a resume attempt's
  values under `resumes`, and `eval/index.py`'s `record_for_run` reads only the
  top level. A directory scored half under `v1` and half under `v2` would
  therefore present itself as a clean `v1` run and get compared against one —
  a reader can at least see the other knobs in the manifest and judge whether
  they matter; a mixed prompt set cannot be judged after the fact at all,
  because nothing downstream can tell which rows are which.

## Which variant to try is decided by measurement, not taste

`eval.analysis.table_selection_report()` splits a right-schema failure into
`n_retrieval_miss` (the gold table was never offered to the model) versus
`n_selection_miss` (offered and unused), and `rank_report()`'s
`by_gold_rank` buckets separate a shortlist miss from a picker error. Only
some of those are prompt problems:

| Signal | Where | Reading | Try |
|---|---|---|---|
| `by_gold_rank["miss"]` is large | `summary.json` → `arms.<arm>.by_gold_rank` | retrieval ran and never surfaced the gold schema | **not a prompt fix** — widen `schema_route_top_k` or improve the embedder/shortlist |
| `by_gold_rank["no_shortlist"]` is large | `summary.json` → `arms.<arm>.by_gold_rank` | no shortlist was recorded — an oracle rung, or turns that ended before retrieval. Not a retrieval failure, and it used to be folded into `miss` | nothing; check the arm is what you think it is |
| gold schema at rank 1, but `pick_hit` is false | `summary.json` → `arms.<arm>.by_gold_rank["1"].pick_accuracy` | the picker saw the right schema and picked wrong | `schema_pick@v2` |
| `n_selection_miss` > `n_retrieval_miss` | `analysis.json` (`table_selection_report`) | the agent core was shown the gold table and did not use it | `agent_core@v2` |
| `n_wrong_but_nrows_match` is large | `summary.json` (per arm) | right row count, wrong projection or ordering | `agent_core@v3` |

A prompt cannot pick what it was never shown, so a shortlist miss calls for a
retrieval fix regardless of how the numbers otherwise look.

`by_gold_rank` is in `summary.json`, which every run writes. `table_selection_report`
is only in `analysis.json`, which nothing writes automatically — produce it with
`uv run python -m governed_bi.eval.analysis <run_dir>`.

**Do not run a combined variant before running its halves separately.**
A paired McNemar test (`eval.power`, exported as `paired_mcnemar`; quote deltas
from this one, not from `eval.analysis.mcnemar`, because only this one reports
what the run could resolve) works over the *one* shared question pool two runs
both scored. If `(schema_pick=v2, agent_core=v2)` runs as a single
combined arm against the `v1` baseline, a McNemar delta over that pool cannot
attribute the change to either half — there is no run of the untried half over
the same pool to pair against, so the two effects (and any interaction between
them) are inseparable after the fact.

## Comparing two runs

**`eval.index` comparability** (`runs/index.jsonl`, `COMPARABILITY_KEYS`)
checks `split`, `model`, `prompt_set_hash`, `route_top_k`, `route_llm_pick`,
`schema_pick_max_columns`, and `use_embedder`. Two runs that differ only by
`--prompt` read as **not comparable** unless every other knob also matches,
and the reported diff names it: `prompt set: '<hash a>' vs '<hash b>'`.

**A paired McNemar test** (keyed on `question_id`) is the actual significance
test between two prompt sets, once `eval.index` has confirmed they are otherwise
comparable. Use `eval.power`'s — exported as `paired_mcnemar`, and what
`run_datalake` writes into `summary.json`. It reports the run's noise floor and minimum
detectable effect beside the p-value, so a delta cannot be read without also
reading whether the run could resolve it. `eval.analysis.mcnemar(rows_a, rows_b)`
is the offline sibling behind `analysis.json`: same exact test, same p-value,
different signature, and no statement of resolution. Quote from the former.

**Point estimates across unpaired runs are not a substitute for either.**
Serve decoding is not pinned, so two runs of the *literal same* arm and prompt
set disagree on a nontrivial share of questions (see
[`datalake-run.md`](plans/datalake-run.md)'s retired-numbers caveat) — a raw
EX delta between an old default run and a new `agent_core@v2` run could be
entirely decoding noise, not the prompt. McNemar isolates the discordant
pairs, which is the only part of the data that carries information about
which variant is actually better.

## CLI cheatsheet

One-off single-schema experiment on the `v2` schema picker:

```bash
uv run python -m governed_bi.eval.run_datalake --dbs beer_factory --prompt schema_pick=v2
```

Data-lake dry run on `agent_core@v2`, five dbs, into its own output directory
(run a separate default invocation to get the baseline to compare against):

```bash
uv run python -m governed_bi.eval.run_datalake --limit-dbs 5 --prompt agent_core=v2 --out runs/datalake/
```

Both halves of a combined change, tried and measured separately before ever
combining them (two invocations, not two `--prompt` flags on one):

```bash
uv run python -m governed_bi.eval.run_datalake --limit-dbs 5 --prompt schema_pick=v2 --out runs/datalake/
uv run python -m governed_bi.eval.run_datalake --limit-dbs 5 --prompt agent_core=v2 --out runs/datalake/
```

Index both runs and render pairwise comparability:

```bash
uv run python -m governed_bi.eval.index --add runs/datalake/<ts-schema-pick-v2>
uv run python -m governed_bi.eval.index --add runs/datalake/<ts-agent-core-v2>
uv run python -m governed_bi.eval.index
```

Offline attribution over a finished run (no model, no database, no API cost):

```bash
uv run python -m governed_bi.eval.analysis runs/datalake/<timestamp> --bird-dir ../BIRD-Data-Obfuscation
```

A malformed or unknown `--prompt` fails before any Postgres connection or
model call:

```bash
uv run python -m governed_bi.eval.run_datalake --prompt sqlgen=v9 --out runs/datalake/
# usage error: unknown prompt stage 'sqlgen'; known stages: agent_core, curator_phase_a, ...
```

**See also:** [Measurement](measurement.md) for the rest of the attribution
chain (outcome/stage taxonomy, the run ledger); [Data-lake run](plans/datalake-run.md)
for the full pooled-experiment runbook this CLI wiring plugs into;
[Analyst LLM-call walkthrough](analyst-llm-call.md) and
[Curator LLM-call walkthrough](curator-llm-call.md) for what each stage's
prompt actually does inside its call site.
