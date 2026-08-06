"""Postgres connector adapter (BIRD obfuscated path).

Session settings required by ADR 0006 §10 are applied on open:
``default_transaction_read_only = on`` and ``synchronize_seqscans = off``.

Error taxonomy is SQLSTATE class lookup (parcel C): ``42`` → ``QueryError``;
``08`` / ``53`` / ``57`` → ``ConnectionError``. Never message-regex.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..corpus.introspect import (
    ForeignKeyInfo,
    IntrospectedColumn,
    IntrospectedTable,
    Introspection,
)
from .errors import ConnectionError, QueryError

__all__ = ["PostgresConnector"]

_CONNECT_TIMEOUT_S = 5
#: Driver gave no code on a connect-path failure (e.g. TCP timeout). Class 08.
_FALLBACK_CONNECTION_SQLSTATE = "08006"


class PostgresConnector:
    """Read-only Postgres access. Context-managed or lazy-connect on first use."""

    def __init__(self, dsn: str, *, max_rows: int = 200_000) -> None:
        self._dsn = dsn
        self._max_rows = max_rows
        self._conn: Any = None

    def __enter__(self) -> PostgresConnector:
        self._connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:  # pragma: no cover - GC path
        try:
            self.close()
        except Exception:
            pass

    @property
    def dialect(self) -> str:
        return "postgres"

    def _connect(self) -> Any:
        if self._conn is not None:
            return self._conn
        try:
            import psycopg
        except ImportError as err:
            raise ConnectionError(
                "psycopg is required for PostgresConnector",
                sqlstate=_FALLBACK_CONNECTION_SQLSTATE,
            ) from err
        try:
            conn = psycopg.connect(
                self._dsn,
                autocommit=True,
                connect_timeout=_CONNECT_TIMEOUT_S,
            )
            conn.execute("SET default_transaction_read_only = on")
            conn.execute("SET synchronize_seqscans = off")
        except Exception as err:
            self._raise_classified(err, connecting=True)
        self._conn = conn
        return conn

    def _raise_classified(self, err: BaseException, *, connecting: bool = False) -> None:
        """Map a driver fault to QueryError / ConnectionError by SQLSTATE class."""
        sqlstate = getattr(err, "sqlstate", None)
        if isinstance(sqlstate, bytes):
            sqlstate = sqlstate.decode("ascii", errors="replace")
        if sqlstate is not None:
            sqlstate = str(sqlstate)

        if sqlstate and sqlstate.startswith("42"):
            raise QueryError(str(err), sqlstate=sqlstate) from err
        if sqlstate and (
            sqlstate.startswith("08")
            or sqlstate.startswith("53")
            or sqlstate.startswith("57")
        ):
            raise ConnectionError(str(err), sqlstate=sqlstate) from err
        if connecting:
            # Connect-path without a class-08 code (e.g. TCP timeout): still
            # infrastructure. Never invent a class-42 code.
            raise ConnectionError(
                str(err),
                sqlstate=sqlstate or _FALLBACK_CONNECTION_SQLSTATE,
            ) from err
        # Statement path: prefer query fault when the class is not infrastructure.
        raise QueryError(str(err), sqlstate=sqlstate) from err

    def execute(
        self, sql: str, *, max_rows: int | None = None
    ) -> tuple[Sequence[str], Sequence[tuple[Any, ...]], bool]:
        cap = self._max_rows if max_rows is None else max_rows
        try:
            conn = self._connect()
            cur = conn.execute(sql)
            columns = [d.name for d in (cur.description or ())]
            rows = cur.fetchmany(cap + 1)
        except (QueryError, ConnectionError):
            raise
        except Exception as err:
            self._raise_classified(err)
        truncated = len(rows) > cap
        if truncated:
            rows = rows[:cap]
        return columns, rows, truncated

    def introspect(self, schema: str) -> Introspection:
        try:
            conn = self._connect()
            table_names = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """,
                    (schema,),
                )
            ]
            tables: list[IntrospectedTable] = []
            for name in table_names:
                cols = tuple(
                    IntrospectedColumn(
                        physical_name=row[0],
                        physical_type=(row[1] or "text"),
                        nullable=row[2] == "YES",
                    )
                    for row in conn.execute(
                        """
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = %s
                        ORDER BY ordinal_position
                        """,
                        (schema, name),
                    )
                )
                tables.append(IntrospectedTable(physical_name=name, columns=cols))

            fk_rows = list(
                conn.execute(
                    """
                    SELECT
                        tc.constraint_name,
                        kcu.table_name,
                        kcu.column_name,
                        ccu.table_name,
                        ccu.column_name,
                        kcu.ordinal_position
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_schema = kcu.constraint_schema
                     AND tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON tc.constraint_schema = ccu.constraint_schema
                     AND tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema = %s
                    ORDER BY tc.constraint_name, kcu.ordinal_position
                    """,
                    (schema,),
                )
            )
        except (QueryError, ConnectionError):
            raise
        except Exception as err:
            self._raise_classified(err)

        grouped: dict[str, list[tuple[str, str, str, str]]] = {}
        for constraint, from_t, from_c, to_t, to_c, _ord in fk_rows:
            grouped.setdefault(constraint, []).append((from_t, from_c, to_t, to_c))
        foreign_keys: list[ForeignKeyInfo] = []
        for parts in grouped.values():
            from_t = parts[0][0]
            to_t = parts[0][2]
            foreign_keys.append(
                ForeignKeyInfo(
                    from_table=from_t,
                    from_columns=tuple(p[1] for p in parts),
                    to_table=to_t,
                    to_columns=tuple(p[3] for p in parts),
                )
            )
        return Introspection(
            tables=tuple(tables),
            foreign_keys=tuple(foreign_keys),
        )

    def list_tables(self, schema: str = "public") -> Sequence[str]:
        return [t.physical_name for t in self.introspect(schema).tables]

    def describe_table(self, name: str, *, schema: str = "public") -> IntrospectedTable:
        schema_name, table_name = _split_name(name, default_schema=schema)
        for table in self.introspect(schema_name).tables:
            if table.physical_name == table_name:
                return table
        raise QueryError(
            f'relation "{schema_name}.{table_name}" does not exist',
            sqlstate="42P01",
        )

    # ``sample_values`` was here and is gone; see ``ports.Connector``. It hand-built
    # ``f'SELECT DISTINCT "{column}" FROM "{schema}"."{table}"'`` — Postgres has no
    # quote-doubling and this adapter did none, so a ``physical_name`` containing a double
    # quote escaped its intended relation, and ``corpus/validate.py`` validates only
    # ``slug(physical_name)`` and so raises no objection to such an asset. Its one caller now
    # builds the statement from ``exp.Identifier`` nodes and runs it through ``govern``.


def _split_name(name: str, *, default_schema: str) -> tuple[str, str]:
    if "." in name:
        schema, _, table = name.partition(".")
        return schema.strip('"'), table.strip('"')
    return default_schema, name.strip('"')
