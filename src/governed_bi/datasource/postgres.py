"""On open: read-only and ``synchronize_seqscans=off`` (ADR 0006). ``statement_timeout``
is set only when the configured milliseconds are above zero. SQLSTATE class, never the
message: ``42`` is a query fault; ``08``, ``53`` and ``57`` are infrastructure, except
``57014`` (our timeout), which keeps the handle. No SQLSTATE uses ``closed`` / ``broken``
and the DB-API split. On psycopg 3.3.4 a closed connection has ``sqlstate=None`` and was
filed as bad SQL while the dead handle was reused.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from ..corpus.introspect import (
    ForeignKeyInfo,
    IntrospectedColumn,
    IntrospectedTable,
    Introspection,
)
from ..registry.settings import setting_default
from .errors import ConnectionError, QueryError

__all__ = ["PostgresConnector"]

_log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_S = 5
#: Driver gave no code on a connect-path failure (e.g. TCP timeout). Class 08.
_FALLBACK_CONNECTION_SQLSTATE = "08006"
#: ``query_canceled``. Class 57 by number, a statement fault here because it is our timeout.
_STATEMENT_TIMEOUT_SQLSTATE = "57014"


class PostgresConnector:
    def __init__(
        self,
        dsn: str,
        *,
        max_rows: int = 200_000,
        statement_timeout_ms: int | None = None,
    ) -> None:
        self._dsn = dsn
        self._max_rows = max_rows
        #: ``None`` means the declared default, so the recorded number and the enforced number match.
        #: It is not "no bound". Pass ``0`` to disable, which is what Postgres already means by zero.
        self._statement_timeout_ms = (
            int(setting_default("statement_timeout_ms"))
            if statement_timeout_ms is None
            else int(statement_timeout_ms)
        )
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

    def _discard(self) -> None:
        """Never raises. ``_conn`` is cleared before ``close()``, which can fail on a broken handle.
        """
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - cleanup on a handle already known to be bad
            pass

    def __del__(self) -> None:  # pragma: no cover - GC path
        try:
            self.close()
        except Exception:  # noqa: BLE001 — a finaliser must not raise during garbage collection
            pass

    @property
    def dialect(self) -> str:
        return "postgres"

    @property
    def endpoint(self) -> dict[str, Any]:
        """Host, port and database, never the credential. An unparseable DSN returns ``{}`` and does not raise.
        """
        try:
            from psycopg.conninfo import conninfo_to_dict  # noqa: PLC0415 (lazy: heavy import)

            parsed = conninfo_to_dict(self._dsn)
        except Exception as err:  # noqa: BLE001 — a page describing the target must not raise on it
            # The type only: the message of a DSN parse error can quote the DSN.
            _log.warning("could not parse the DSN to describe the target (%s)", type(err).__name__)
            return {}
        out: dict[str, Any] = {}
        for wire, key in (("host", "host"), ("port", "port"), ("dbname", "database")):
            value = parsed.get(wire)
            if value not in (None, ""):
                out[key] = str(value)
        return out

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
            # SET takes no parameters; psycopg raises on a placeholder, so the int is interpolated.
            # The server cancels this statement and leaves the connection usable. A client abort
            # of the whole turn does not give the agent a ``QueryError`` it can rewrite.
            if self._statement_timeout_ms > 0:
                conn.execute(f"SET statement_timeout = {self._statement_timeout_ms}")
        except Exception as err:  # noqa: BLE001 — classified and re-raised
            self._raise_classified(err, connecting=True)
        self._conn = conn
        return conn

    def _connection_is_unusable(self, err: BaseException) -> bool:
        """From structured state only: ``closed`` or ``broken``, else ``OperationalError`` if SQLSTATE is absent.
        Never the message. psycopg may reword "the connection is closed".
        """
        conn = self._conn
        if conn is not None and (
            bool(getattr(conn, "closed", False)) or bool(getattr(conn, "broken", False))
        ):
            return True
        try:
            import psycopg
        except ImportError:  # pragma: no cover - _connect raises before reaching here
            return False
        return isinstance(err, psycopg.OperationalError)

    def _raise_classified(self, err: BaseException, *, connecting: bool = False) -> None:
        """By SQLSTATE class. Every ``ConnectionError`` discards the cached handle so the next call reconnects.
        """
        sqlstate = getattr(err, "sqlstate", None)
        if isinstance(sqlstate, bytes):
            sqlstate = sqlstate.decode("ascii", errors="replace")
        if sqlstate is not None:
            sqlstate = str(sqlstate)

        if sqlstate and sqlstate.startswith("42"):
            raise QueryError(str(err), sqlstate=sqlstate) from err
        # Before the class-57 branch, which would discard a healthy handle. ``57014`` is our
        # timeout: the connection is fine, and the agent should rewrite an expensive query
        # rather than be told the warehouse is unreachable.
        if sqlstate == _STATEMENT_TIMEOUT_SQLSTATE:
            raise QueryError(str(err), sqlstate=sqlstate) from err
        if sqlstate and (
            sqlstate.startswith("08")
            or sqlstate.startswith("53")
            or sqlstate.startswith("57")
        ):
            self._discard()
            raise ConnectionError(str(err), sqlstate=sqlstate) from err
        if connecting:
            # Still infrastructure when connect fails without class 08. Never invent class 42.
            self._discard()
            raise ConnectionError(
                str(err),
                sqlstate=sqlstate or _FALLBACK_CONNECTION_SQLSTATE,
            ) from err
        # No SQLSTATE means the server did not classify it. Calling that a query fault
        # made one blip look like bad SQL for every later question.
        if self._connection_is_unusable(err):
            self._discard()
            raise ConnectionError(
                str(err), sqlstate=sqlstate or _FALLBACK_CONNECTION_SQLSTATE
            ) from err
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
        except Exception as err:  # noqa: BLE001 — classified and re-raised
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
        except Exception as err:  # noqa: BLE001 — classified and re-raised
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


def _split_name(name: str, *, default_schema: str) -> tuple[str, str]:
    if "." in name:
        schema, _, table = name.partition(".")
        return schema.strip('"'), table.strip('"')
    return default_schema, name.strip('"')
