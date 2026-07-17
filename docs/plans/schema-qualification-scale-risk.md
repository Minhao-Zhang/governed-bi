# Schema-qualification risk on the scale run

_Status: **RESOLVED 2026-07-17** — dissolved at the source rather than mitigated.
The dual `multi_schema` mode is gone: the engine is now uniformly schema-qualified
(`schema.table` everywhere), so there is no convention to flip between modes and no
end-to-end flag to thread. SQLite fakes its schema by `ATTACH`-ing the file under
the `corpus_pin` alias; Postgres pins one schema (a `db_id`) or spans all with none.
The prep items below are retained for history — the flow they asked to verify no
longer exists. See "Resolution" at the end._

_Original status: OPEN — prep item, not yet actioned. Surfaced 2026-07-16 while
diagnosing the `curated_sme` fix-pass crash ([eval-ladder-results.md
§Findings 4](eval-ladder-results.md#4-internal-validity-passes-one-non-scoring-defect-a3-fix-pass-crash));
determined to be a **separate** issue from that `KeyError`. Relevant to the
[69-schema / 2,030-test scale run](eval-ladder-results.md#known-limitations-of-this-benchmark)
that is the open Increment-3 item in
[clarification-sme-benchmark-build-plan.md](clarification-sme-benchmark-build-plan.md)._

## The concern

Single-schema and multi-schema mode have opposite rules about whether a
table reference may carry a schema qualifier. In single-schema mode
(`multi_schema=False`, today's default) the L4 guardrail rejects any
schema/catalog-qualified table reference outright, fail-closed — see the
`if src.db or src.catalog` branch in
[guardrails.py:561-564](../../src/governed_bi/gateway/guardrails.py#L561-L564).
In multi-schema mode (`multi_schema=True`) the licensed names are themselves
schema-qualified `{schema}.{table}`, and a *bare* reference resolves only to
the configured `default_schema` — or is refused as ambiguous when the bare
name exists in more than one licensed schema — per
[guardrails.py:569-602](../../src/governed_bi/gateway/guardrails.py#L569-L602).
A model that guesses the wrong qualification convention for the mode it is
actually running in gets its SQL refused by L4, not executed.

## Why it matters for the scale run

Today's single-DB eval pins `multi_schema=False` unconditionally when it
builds the `DataSourceConfig` —
[run_experiment.py:240-246](../../src/governed_bi/eval/run_experiment.py#L240-L246).
The upcoming scale run loads all 69 BIRD `db_id`s as Postgres schemas inside
one database, which is exactly the `multi_schema=True` regime. That flips the
qualification convention the model must follow, and the flip has to be
threaded correctly: `DataSourceConfig.multi_schema` → the guardrail's
`check(..., multi_schema=, default_schema=)` gate → `PromptContext.multi_schema`
and `allowed_table_names()` (schema-qualifies the licensed set only when the
flag is set) — [context.py:135](../../src/governed_bi/analyst/context.py#L135),
[context.py:137-145](../../src/governed_bi/analyst/context.py#L137-L145) —
→ the rendered table headers the agent actually reads, which switch between
bare `physical_name` and `schema.physical_name` at
[context.py:359-365](../../src/governed_bi/analyst/context.py#L359-L365).

If any hop in that chain is missed, or the rendered prompt doesn't make the
active convention unambiguous, the failure mode is silent: not a crash,
just L4 refusals that read as lower execution accuracy. That would corrupt the
headline scale-run EX number in a way that's easy to misattribute to a
model/corpus weakness rather than a harness bug — the exact kind of
regression the single-DB eval can't catch, because it never exercises
`multi_schema=True`.

## Not the curated_sme fix-pass crash

Worth stating explicitly, since both surfaced in the same investigation: the
`curated_sme` fix-pass `KeyError: 'restaurant'` was a hard exception raised
inside `agent.invoke` during the curator's Phase-B fix-pass (caught by
`_invoke_agent`, [pipeline.py:253](../../src/governed_bi/curator/pipeline.py#L253)).
**Resolved 2026-07-16** — it was an unguarded `read_corpus` lookup on an unknown
table name (fixed `31c9018`), and the dangling term bindings it left unrepaired
are now prevented at the source (`upsert_term` binding validation) and
deterministically repaired before the agent fix-pass. See
[eval-ladder-results.md §Next steps #1](eval-ladder-results.md#next-steps-in-priority-order).
The guardrail and tools described above only ever turn a schema-qualification
mistake into a recoverable `"error:"` string or a `GuardrailVerdict` refusal —
never a raised `KeyError` — so qualification confusion is not, and cannot be,
the cause of that crash. This doc tracks the separate serve-path, EX-quality
risk; the fix-pass crash is tracked as its own item in
[eval-ladder-results.md §Next steps #1](eval-ladder-results.md#next-steps-in-priority-order).

## What to verify before the scale run

- Confirm the mode flag flows end-to-end: `DataSourceConfig.multi_schema` →
  guardrail `check(..., multi_schema=, default_schema=)` → the
  licensed-scope/context the agent is shown (`PromptContext.multi_schema`,
  `allowed_table_names()`, and the rendered table header — the three sites
  cited above).
- Confirm the agent's context/prompt states the active qualification
  convention plainly: bare names in single-schema, `schema.table` in
  multi-schema — not just implied by the table header format.
- Add an instrumentation counter to the run summary that separates "refused
  because of a schema-qualification mismatch" from other L4/refusal reasons,
  so a silent EX hit is visible in `summary.json` rather than buried in an
  aggregate refusal rate.
- Consider a small pre-flight check — 2-3 schemas under `multi_schema=True` —
  before committing to the full 69-schema run.

## Resolution (2026-07-17)

The `multi_schema` flag was removed entirely. There is now a single qualification
convention, so the failure mode this doc tracked — a model guessing the wrong
convention for the mode it is running in, silently refused at L4 — cannot occur.

What changed:

- `DataSourceConfig` drops `multi_schema` / `is_multi_schema()` and gains
  `serving_schema()` — the schema a bare reference resolves to (the SQLite `ATTACH`
  alias, the pinned Postgres schema, or `None` to span all).
- The guardrail (`column_allowlist`, `_layer_terms`, `_layer_columns`,
  `_layer_cartesian`, `check`) is always schema-qualified; `default_schema` is
  always the serving schema, so a bare reference still resolves fail-closed.
- `PromptContext` drops its `multi_schema` field; `allowed_table_names()` and the
  rendered table headers are always `schema.table`.
- The SQLite connector `ATTACH`es the file under the `corpus_pin` alias, so a
  generated `schema.table` executes natively (read-only preserved). The BIRD
  corpus already stored `schema: <db_id>`, so **no corpus regeneration was needed**.
- The scale run's eval harness no longer pins `multi_schema=False`; a single-DB
  run is just "one schema present," identical in shape to the 69-schema run.

Of the prep items above, the instrumentation counter that separates refusal
reasons in `summary.json` remains independently useful for diagnosing the scale
run and is not made moot by this change; the flag-threading and convention-in-
prompt items are.
