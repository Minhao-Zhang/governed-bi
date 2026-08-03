"""Datasource errors: query faults vs connection faults.

SQLite wraps "no such column" in ``OperationalError``. Classifying that family as
infrastructure hides a wrong answer as a crash (parcel C acceptance contract).
"""

from __future__ import annotations

__all__ = ["DatasourceError", "QueryError", "ConnectionError"]


class DatasourceError(Exception):
    """Base for connector failures."""


class QueryError(DatasourceError):
    """The statement was wrong (unknown column, syntax, etc.). Not infrastructure."""


class ConnectionError(DatasourceError):
    """The database could not be reached or opened."""
