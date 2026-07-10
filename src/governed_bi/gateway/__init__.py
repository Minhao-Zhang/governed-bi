"""Gateway service: the only path to data.

Read-only, RLS-as-user, credential-isolated, forced LIMIT/timeout, audit/replay
(Architecture §3-4). One boundary, two permission profiles. Fail-closed lives in
the guardrails (server ``wrap_tool_call``).

The gateway wraps a per-dialect ``Connector``: SQLite is proven against the
committed fixture; Postgres and Redshift are implemented behind optional extras
and unit-tested offline, but not yet run against a live server. See
``docs/server.md`` steps 8-9 and ``docs/architecture.md``.
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
    "check",
    "column_allowlist",
]
