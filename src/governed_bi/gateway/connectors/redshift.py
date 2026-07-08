"""Redshift connector (seam).

Not implemented yet. Redshift speaks the Postgres wire protocol, so this reuses
``PostgresConnector`` and only differs where Redshift diverges: catalog
introspection via ``svv_*`` / ``pg_catalog`` views, Redshift-specific cost from
``EXPLAIN``, and its own access model. Needs an AWS cluster to test, so it stays
a design-only seam for now. Install the optional extra when implementing:

    uv sync --extra redshift
"""

from __future__ import annotations

from typing import Any

from .base import Dialect
from .postgres import PostgresConnector

_SEAM = (
    "RedshiftConnector is a seam. Reuses the Postgres wire protocol; implement "
    "svv_* catalog introspection and install the extra: uv sync --extra redshift"
)


class RedshiftConnector(PostgresConnector):
    dialect = Dialect.redshift

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        raise NotImplementedError(_SEAM)
