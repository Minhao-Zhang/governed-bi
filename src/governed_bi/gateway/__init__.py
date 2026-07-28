"""Gateway service: the only path to data.

Read-only, credential-isolated, row-cap + timeout, audit/replay (Architecture
§3-4). One boundary, two permission profiles. Fail-closed lives in the guardrails
(Analyst ``wrap_tool_call``).

**Not RLS-as-user.** ``execute`` accepts ``identity`` and uses it only to write the
audit row: no session role is set, nothing is row-scoped. Four surfaces used to
assert otherwise (AUDIT S6). The parameter is the seam an enterprise fork wires; in
this repo it is provenance, not enforcement.

Row cap is connector-enforced: every dialect uses ``fetchmany(max_rows + 1)``
plus a statement timeout. Postgres/Redshift also best-effort inject a root
``LIMIT`` on simple ``SELECT`` / ``UNION`` SQL that lacks one (sqlglot rewrite;
CTEs kept intact — no subquery wrap). Queries that already carry a LIMIT, or
that fail to parse as a single Select/Union, still rely on fetchmany alone —
client-side cursors can buffer that result set. SQLite has no LIMIT rewrite yet.

The gateway wraps a per-dialect ``Connector``: SQLite is proven against the
committed fixture; Postgres is exercised live by the eval harness
(``eval/run_experiment.py``) and unit-tested offline; Redshift is implemented but
not yet run against a live cluster. See ``docs/analyst.md`` steps 8-9 and
``docs/architecture.md``.
"""

from __future__ import annotations

from .connectors import (
    ColumnInfo,
    Connector,
    Dialect,
    PostgresConnector,
    QueryResult,
    RedshiftConnector,
    SqliteConnector,
    TableInfo,
)
from .factory import build_connector
from .gateway import AuditEntry, Gateway, Identity
from .guardrails import ColumnAllowlist, GuardrailLayer, GuardrailVerdict, check, column_allowlist

__all__ = [
    "AuditEntry",
    "ColumnAllowlist",
    "ColumnInfo",
    "Connector",
    "Dialect",
    "Gateway",
    "GuardrailLayer",
    "GuardrailVerdict",
    "Identity",
    "PostgresConnector",
    "QueryResult",
    "RedshiftConnector",
    "SqliteConnector",
    "TableInfo",
    "build_connector",
    "check",
    "column_allowlist",
]
