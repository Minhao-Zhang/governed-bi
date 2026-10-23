"""W1: every attempt keeps the statement the agent wrote and why it failed."""

from __future__ import annotations

from typing import Any

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import ToolBounds
from governed_bi.govern.ledger import ERROR_MAX_CHARS
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.fetch import run_query, sample_rows
from governed_bi.serve.ledger import execution_from_attempts
from governed_bi.serve.state import keep_turns

TABLE = TableAsset(
    id="sales.customers",
    schema="sales",
    physical_name="customers",
    summary="customers table",
    columns=("sales.customers.name",),
)
COLUMN = ColumnAsset(
    id="sales.customers.name",
    schema="sales",
    parent_table="customers",
    physical_name="name",
    summary="customer name",
    physical_type="TEXT",
)
BOUNDS = ToolBounds(retrieved_tables=frozenset({"sales.customers"}))


class Connector:
    dialect = "sqlite"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def execute(self, sql: str, **_: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        if self.error is not None:
            raise self.error
        return (["name"], [("Ada",)], False)


def _run(sql: str, connector: Connector) -> Any:
    _payload, attempt = run_query(
        sql,
        bounds=BOUNDS,
        corpus=for_analyst([TABLE, COLUMN]),
        connector=connector,
        policy=GovernancePolicy(),
    )
    return attempt


def test_a_rejected_statement_keeps_its_sql_and_the_checks_detail() -> None:
    sql = "DELETE FROM customers"
    attempt = _run(sql, Connector())
    assert attempt["passed"] is False
    assert attempt["executed_sql"] is None
    assert attempt["submitted_sql"] == sql
    assert attempt["error"]


def test_a_database_error_keeps_its_sql_and_the_message() -> None:
    sql = "SELECT name FROM sales.customers"
    attempt = _run(sql, Connector(RuntimeError('column "x" does not exist')))
    assert attempt["passed"] is True
    assert attempt["submitted_sql"] == sql
    assert attempt["error"] == 'RuntimeError: column "x" does not exist'


def test_a_success_keeps_its_sql_and_no_error() -> None:
    sql = "SELECT name FROM sales.customers"
    attempt = _run(sql, Connector())
    assert attempt["passed"] is True
    assert attempt["submitted_sql"] == sql
    assert attempt["error"] is None


def test_a_long_driver_message_is_cut() -> None:
    attempt = _run("SELECT name FROM sales.customers", Connector(RuntimeError("x" * 5000)))
    assert len(attempt["error"]) == ERROR_MAX_CHARS


def test_the_sample_path_keeps_its_statement_and_error() -> None:
    _payload, ok, attempt = sample_rows(
        "sales.customers.name",
        limit=5,
        bounds=BOUNDS,
        assets={a.id: a for a in (TABLE, COLUMN)},
        connector=Connector(RuntimeError("disk I/O error")),
        corpus=for_analyst([TABLE, COLUMN]),
        policy=GovernancePolicy(),
    )
    assert not ok
    assert attempt is not None and attempt["submitted_sql"]
    assert attempt["error"] == "RuntimeError: disk I/O error"


def test_a_compacted_turn_still_has_both_fields() -> None:
    rejected = _run("DELETE FROM customers", Connector())
    answered = _run("SELECT name FROM sales.customers", Connector())
    record = {
        "turn_id": "t1",
        "execution": execution_from_attempts([rejected, answered]),
        # A bulk key large enough that compaction has to trim something.
        "context_block": "y" * 50_000,
    }
    rows = keep_turns([], [{"record": record}, {"record": {"turn_id": "t2"}}])
    old = rows[0]
    assert old["compacted"]["dropped"], "nothing was compacted, so the test proves nothing"
    kept = old["record"]["execution"]["attempts"]
    assert [a["submitted_sql"] for a in kept] == [
        "DELETE FROM customers",
        "SELECT name FROM sales.customers",
    ]
    assert kept[0]["error"] and kept[1]["error"] is None
