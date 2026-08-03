"""Datasource errors: query faults vs connection faults.

Postgres classification is keyed on SQLSTATE class (parcel C contract):
class ``42`` → query fault; classes ``08`` / ``53`` / ``57`` → infrastructure.
"""

from __future__ import annotations

__all__ = ["DatasourceError", "QueryError", "ConnectionError"]


class DatasourceError(Exception):
    """Base for connector failures."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class QueryError(DatasourceError):
    """The statement was wrong (unknown column, syntax, etc.). Not infrastructure."""


class ConnectionError(DatasourceError):
    """The database could not be reached or opened."""
