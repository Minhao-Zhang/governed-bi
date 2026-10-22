"""Postgres class ``42`` is a query fault; ``08``, ``53`` and ``57`` are infrastructure."""

from __future__ import annotations

__all__ = ["DatasourceError", "QueryError", "ConnectionError"]


class DatasourceError(Exception):
    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class QueryError(DatasourceError):
    """The statement was wrong. Not infrastructure."""


class ConnectionError(DatasourceError):
    """The database could not be reached or opened."""
