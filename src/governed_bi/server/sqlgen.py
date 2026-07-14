"""SQL value objects shared by the agentic serve core.

Historically this module also held the deterministic-flow SQL generators
(``TemplateSqlGenerator`` / ``LlmSqlGenerator`` behind a ``SqlGenerator``
protocol + ``RepairFeedback``). The flow serve path is gone (ADR 0002 — the
agent generates SQL itself via its tool loop), so only two flow-independent
helpers remain and are consumed by ``server.agent`` / ``server.governance``:

- :class:`GeneratedSql` — a generated statement plus the table-asset ids it
  reads from (the agent core builds one to hand the shared finalizer).
- :func:`_tables_used` — map the physical table names in a SQL string back to
  their asset ids (for the reliability stamp's join plan; never a safety input,
  since the guardrails re-parse the SQL independently).
- :func:`_extract_sql` — pull SQL out of a raw model response (fences/prose
  tolerant), used by the no-layer eval baseline solver.
"""

from __future__ import annotations

import re
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
    multi_schema: bool = False,
) -> frozenset[str]:
    """Map the physical table names in ``sql`` back to their asset ids.

    Best-effort: a parse failure or an unmapped name yields fewer ids, which only
    affects the reliability stamp's join plan - the guardrails re-parse the SQL
    independently, so this is never a safety input. In multi-schema mode the map
    is keyed on the schema-qualified ``schema.table`` name (matching
    :meth:`PromptContext.physical_to_id`).
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return frozenset()
    ids: set[str] = set()
    for table in tree.find_all(exp.Table):
        key = f"{table.db}.{table.name}" if multi_schema else table.name
        asset_id = physical_to_id.get(key)
        if asset_id is not None:
            ids.add(asset_id)
    return frozenset(ids)


def _extract_sql(response: str) -> str:
    """Pull the SQL out of a model response, tolerating markdown fences/prose.

    If the response has fenced code blocks, take the **last** one (the model's
    final answer usually follows any echoed example) and drop its info-string
    (```sql / ```python / bare ```), so a language tag is never captured into the
    SQL. Otherwise strip stray backticks. Trims a trailing semicolon; returns the
    empty string for an empty response.
    """
    text = response.strip()
    if not text:
        return ""
    blocks = re.findall(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)```", text, re.DOTALL)
    if blocks:
        text = blocks[-1].strip()
    else:
        text = text.strip("`").strip()
    return text.rstrip(";").strip()
