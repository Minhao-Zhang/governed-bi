"""Postgres connector adapter (BIRD obfuscated path).

Session settings required by ADR 0006 §10 are applied on open:
``default_transaction_read_only = on`` and ``synchronize_seqscans = off``.

Error taxonomy is SQLSTATE class lookup (parcel C): ``42`` → ``QueryError``;
``08`` / ``53`` / ``57`` → ``ConnectionError``. Never message-regex.

**A failed statement must discard a dead connection** (audit §9.1). Reusing the
cached handle let one network blip poison every remaining question in the run, and
because psycopg raises ``the connection is closed`` with ``sqlstate=None`` the fault
was recorded as *the model wrote bad SQL* (reproduced on psycopg 3.3.4). The
no-SQLSTATE case is therefore decided from structured driver state —
``Connection.closed`` / ``Connection.broken`` and the DB-API ``OperationalError`` /
``ProgrammingError`` split — never message text: an exception the server classified
carries a SQLSTATE, so one without is a client-side or transport failure.
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

    def _discard(self) -> None:
        """Drop the cached handle so the next call reconnects. Never raises.

        Separate from :meth:`close` because it runs on the failure path: a broken handle
        can raise from ``close()``, which would replace the classified datasource error
        with a cleanup exception. ``_conn`` is cleared *first*, so the handle is dropped
        whether or not the close succeeds.
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
        except Exception:
            pass

    @property
    def dialect(self) -> str:
        return "postgres"

    @property
    def endpoint(self) -> dict[str, Any]:
        """Which database this is, with the credential removed. Safe to serve.

        **The redaction lives here, in the object that owns the DSN, rather than in the caller
        that wants to display it.** A route reaching for ``connector._dsn`` and stripping the
        password itself is one forgotten regex away from serving the credential, and the next
        surface that wants an endpoint would write that regex again. This returns the three
        fields that identify a warehouse and never parses out ``user`` or ``password`` at all,
        so there is nothing for a caller to leak.

        Returns ``{}`` when the DSN cannot be parsed — an unparseable DSN is not a reason to
        raise on a page that is only trying to say where it is pointed.
        """
        try:
            from psycopg.conninfo import conninfo_to_dict  # noqa: PLC0415 (lazy: heavy import)

            parsed = conninfo_to_dict(self._dsn)
        except Exception:
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
        except Exception as err:
            self._raise_classified(err, connecting=True)
        self._conn = conn
        return conn

    def _connection_is_unusable(self, err: BaseException) -> bool:
        """Whether this failure means the *connection* is gone, from structured state only.

        Two signals, in order of directness:

        * ``Connection.closed`` / ``Connection.broken`` — psycopg's view of the handle it
          just failed on. If either is set, the next statement on it cannot succeed.
        * ``isinstance(err, psycopg.OperationalError)`` — the DB-API split. A *server*
          error always carries a ``sqlstate``, and this method is only reached when there
          is none, so the exception is client-side or transport, which is precisely what
          ``OperationalError`` means (against ``ProgrammingError`` for a statement fault).

        Never message text: ``"the connection is closed"`` is a string psycopg may reword
        in any release.
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
        """Map a driver fault to QueryError / ConnectionError by SQLSTATE class.

        Discards the cached handle on every path that raises :class:`ConnectionError`, so the
        next call reconnects instead of reusing a socket the driver has given up on.
        """
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
            self._discard()
            raise ConnectionError(str(err), sqlstate=sqlstate) from err
        if connecting:
            # Connect-path without a class-08 code (e.g. TCP timeout): still
            # infrastructure. Never invent a class-42 code.
            self._discard()
            raise ConnectionError(
                str(err),
                sqlstate=sqlstate or _FALLBACK_CONNECTION_SQLSTATE,
            ) from err
        # Statement path, no SQLSTATE: the server did not classify this, so it is not a
        # verdict on the statement. Falling through to QueryError here is how one blip made
        # every remaining question read as "the model wrote bad SQL".
        if self._connection_is_unusable(err):
            self._discard()
            raise ConnectionError(
                str(err), sqlstate=sqlstate or _FALLBACK_CONNECTION_SQLSTATE
            ) from err
        # A client-side fault that is not the connection: a statement problem after all.
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
    # ``f'SELECT DISTINCT "{column}" FROM "{schema}"."{table}"'`` with no quote-doubling,
    # so a ``physical_name`` containing a double quote escaped its intended relation —
    # and ``corpus/validate.py`` validates only ``slug(physical_name)``, so no such asset
    # is rejected. Its one caller now builds the statement from ``exp.Identifier`` nodes
    # and runs it through ``govern``.


def _split_name(name: str, *, default_schema: str) -> tuple[str, str]:
    if "." in name:
        schema, _, table = name.partition(".")
        return schema.strip('"'), table.strip('"')
    return default_schema, name.strip('"')
