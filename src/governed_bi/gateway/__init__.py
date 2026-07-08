"""Gateway service: the only path to data.

Read-only, RLS-as-user, credential-isolated, forced LIMIT/timeout, audit/replay
(Architecture §3-4). One boundary, two permission profiles. Fail-closed lives in
the guardrails (server ``wrap_tool_call``).

The gateway wraps a per-dialect ``Connector`` (SQLite implemented; Postgres and
Redshift are seams). See ``docs/server.md`` steps 8-9 and ``docs/architecture.md``.
"""

from __future__ import annotations

from .connectors import ColumnInfo, Connector, Dialect, QueryResult, SqliteConnector, TableInfo
from .gateway import AuditEntry, Gateway, Identity
from .guardrails import GuardrailLayer, GuardrailVerdict, check

__all__ = [
    "AuditEntry",
    "ColumnInfo",
    "Connector",
    "Dialect",
    "Gateway",
    "GuardrailLayer",
    "GuardrailVerdict",
    "Identity",
    "QueryResult",
    "SqliteConnector",
    "TableInfo",
    "check",
]
