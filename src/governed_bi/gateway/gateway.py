"""Governed data-access gateway (Architecture §3).

The single data boundary. It wraps a :class:`~governed_bi.gateway.connectors.base.Connector`
and adds the governance the connector does not: an audit/replay log, and the
identity/RLS seam. Reads are read-only with a forced row cap and timeout (the
connector enforces those). In dev this fronts a single SQLite BIRD database with
one all-access identity; in an enterprise deployment it fronts real per-user RLS.
Those differences come from ``config.Settings``, not separate code paths.

Guardrails (syntax / policy / AST allowlist / term-semantics / cost) run in the
server middleware *before* SQL reaches ``execute``; this class assumes the SQL it
receives has already passed them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .connectors.base import Connector, QueryResult

__all__ = ["Identity", "QueryResult", "AuditEntry", "Gateway"]


@dataclass(frozen=True)
class Identity:
    """The acting user (D7). The agent is never broader than this."""

    user: str
    all_access: bool = False  # dev single-identity shortcut


@dataclass(frozen=True)
class AuditEntry:
    user: str
    sql: str
    row_count: int
    truncated: bool


class Gateway:
    """Wraps a connector; every query flows through ``execute`` and is audited."""

    def __init__(
        self, connector: Connector, *, max_rows: int = 1000, timeout_s: float = 30.0
    ) -> None:
        self._connector = connector
        self._max_rows = max_rows
        self._timeout_s = timeout_s
        self._audit: list[AuditEntry] = []

    def execute(self, sql: str, identity: Identity) -> QueryResult:
        """Run already-guardrail-passed SQL as ``identity``, read-only with the
        forced row cap and timeout, and record it in the audit log.

        RLS-as-user is an environment toggle (D7): in dev the single all-access
        identity is a pass-through; an enterprise deployment scopes the session
        to the real user at this point.
        """
        result = self._connector.execute(
            sql, max_rows=self._max_rows, timeout_s=self._timeout_s
        )
        self._audit.append(
            AuditEntry(
                user=identity.user,
                sql=sql,
                row_count=result.row_count,
                truncated=result.truncated,
            )
        )
        return result

    def catalog(self) -> Connector:
        """The read-only catalog (tables, columns, dtypes) for the curator's
        Facts tier and the physical-existence check."""
        return self._connector

    @property
    def audit_log(self) -> list[AuditEntry]:
        return list(self._audit)
