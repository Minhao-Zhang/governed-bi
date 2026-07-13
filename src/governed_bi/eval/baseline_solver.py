"""Arm 1: plain text-to-SQL with no semantic layer (three-arm experiment).

Dumps the live catalog (names + types only — decoys included) and asks the
generation LLM for one ``SELECT``. Bypasses retrieval, corpus, and guardrails
licensing; the only fail-closed behaviour is declining when the model returns
no usable SQL.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..curator.profile import profile_database
from ..server.sqlgen import _extract_sql

if TYPE_CHECKING:
    from ..gateway import Gateway
    from ..gateway.connectors.base import Connector
    from ..llm import ChatClient
    from .arms import Solver

_CANNOT_ANSWER = "CANNOT_ANSWER"

_SYSTEM = """\
You are a careful SQL generator. Translate the business question into ONE \
read-only SQL query over the schema listed below.

Hard rules:
- Use ONLY the tables and columns listed. Do not invent names.
- Emit exactly ONE statement: a SELECT (or WITH ... SELECT). No DDL/DML.
- Never SELECT *; project the columns you need.
- Prefer schema-qualified table references when a schema name is shown \
(e.g. "schema"."table").

Output format:
- Return ONLY the SQL, with no explanation and no markdown fences.
- Target SQL dialect: {dialect}.
- If you cannot answer, return exactly: {cannot_answer}
"""


def _schema_dump(connector: "Connector", schema: str) -> str:
    """Render names + types only (no curated descriptions / reliability)."""
    lines: list[str] = [f"Schema: {schema}"]
    for table in profile_database(connector, schema=schema, sample_limit=0):
        lines.append(f'Table "{schema}"."{table.physical_name}":')
        for col in table.columns:
            lines.append(f"  - {col.physical_name}: {col.physical_type}")
    return "\n".join(lines) if len(lines) > 1 else f"Schema: {schema}\n(no tables)"


def no_layer_solver(
    connector: "Connector",
    gateway: "Gateway",
    chat: "ChatClient",
    *,
    schema: str,
    dialect: str = "postgres",
) -> "Solver":
    """Build the Arm-1 solver: raw schema dump → one LLM SELECT (or None).

    ``gateway`` is accepted for API symmetry with other solvers / future
    probe-on-fail behaviour; the baseline path does not execute against it
    before returning SQL.
    """
    del gateway  # reserved for future repair; keeps the public signature stable
    dump = _schema_dump(connector, schema)
    system = _SYSTEM.format(dialect=dialect, cannot_answer=_CANNOT_ANSWER)

    class _NoLayerSolver:
        def solve(self, question: str) -> str | None:
            user = f"{dump}\n\nQuestion: {question}"
            response = chat.complete(system, user)
            sql = _extract_sql(response)
            if not sql or sql.strip().upper() == _CANNOT_ANSWER:
                return None
            # Reject obvious non-SELECT payloads.
            if not re.match(r"(?is)^\s*(with\b|select\b)", sql):
                return None
            return sql

    return _NoLayerSolver()
