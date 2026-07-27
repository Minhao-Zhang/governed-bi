"""SQL value objects shared by the agentic serve core.

Historically this module also held the deterministic-flow SQL generators
(``TemplateSqlGenerator`` / ``LlmSqlGenerator`` behind a ``SqlGenerator``
protocol + ``RepairFeedback``). The flow serve path is gone (ADR 0002 — the
agent generates SQL itself via its tool loop), so only two flow-independent
helpers remain and are consumed by ``analyst.agent`` / ``analyst.governance``:

- :class:`GeneratedSql` — a generated statement plus the table-asset ids it
  reads from (the agent core builds one to hand the shared finalizer).
- :func:`_tables_used` — map the physical table names in a SQL string back to
  their asset ids (for the reliability stamp's join plan; never a safety input,
  since the guardrails re-parse the SQL independently).

``_extract_sql`` (markdown-fence/prose-tolerant SQL extraction) also lived here,
for the retired raw-dump ``no_layer`` solver. That solver is gone
(``eval/baseline_solver.py``, deleted in the terminology refactor) and every eval
rung — including the designed-not-built ``ceiling`` — now runs the same agentic
serve path, where SQL arrives as a governed ``run_query`` tool argument, never as
free text to be scraped out of a model reply. So it is deleted rather than kept
warm for a caller that is not coming.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class GeneratedSql:
    """A generated statement plus the table-asset ids it reads from."""

    sql: str
    tables_used: frozenset[str] = field(default_factory=frozenset)
    metric_id: str | None = None


def _tables_used(
    sql: str,
    physical_to_id: dict[str, str],
    dialect: str | None,
    *,
    default_schema: str | None = None,
) -> frozenset[str]:
    """Map the physical table names in ``sql`` back to their asset ids.

    Best-effort: a parse failure or an unmapped name yields fewer ids, which only
    affects the reliability stamp's join plan - the guardrails re-parse the SQL
    independently, so this is never a safety input. The map is keyed on the
    schema-qualified ``schema.table`` name (matching
    :meth:`PromptContext.physical_to_id`); a bare reference resolves through
    ``default_schema``.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return frozenset()
    ids: set[str] = set()
    for table in tree.find_all(exp.Table):
        schema = table.db or default_schema or ""
        key = f"{schema}.{table.name}"
        asset_id = physical_to_id.get(key)
        if asset_id is not None:
            ids.add(asset_id)
    return frozenset(ids)
