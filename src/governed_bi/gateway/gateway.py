"""Governed data-access gateway (Architecture §3).

Read-only execution as the requesting user (D7): credential isolation, RLS,
forced ``LIMIT``/timeout, audit + replay log. In dev this fronts a single SQLite
BIRD database with one all-access identity; in prod it fronts the service fleet
with real per-user RLS. Environment differences come from ``config.Settings``,
not from separate code paths.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    """The acting user (D7). The agent is never broader than this."""

    user: str
    all_access: bool = False  # dev single-identity shortcut


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    row_count: int


class Gateway:
    """The single data boundary. All SQL flows through ``execute``."""

    def execute(self, sql: str, identity: Identity) -> QueryResult:
        """Run guardrail-passed SQL as ``identity``, with forced LIMIT/timeout,
        and append to the audit log. Raises on any policy violation (fail-closed).
        """
        raise NotImplementedError("gateway execution pending the DB layer")

    def catalog(self) -> object:
        """Read-only catalog (tables, columns, dtypes) for the curator's Facts
        tier and the CI physical-existence check."""
        raise NotImplementedError("catalog reader pending the DB layer")
