"""Delivery hash, tool bounds, and the governed ``sample_rows`` path.
"""

from __future__ import annotations

from test_agent_tools_hitl import _call, _config, _state, _tools

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.delivery import delivery_hash_for, payload_digest
from governed_bi.serve.tools import tool_bounds_from_state


def test_delivery_hash_stable_for_same_tool_payload() -> None:
    delivered = {"c1": payload_digest("hello")}
    assert delivery_hash_for("ctx", delivered) == delivery_hash_for("ctx", delivered)
    assert delivery_hash_for("ctx", delivered) != delivery_hash_for(
        "ctx", {"c1": payload_digest("hello!")}
    )


def test_tool_bounds_from_state_includes_pulled_in() -> None:
    bounds = tool_bounds_from_state(
        {
            "retrieved_tables": ["s.t"],
            "retrieved": {
                "selected": {},
                "pulled_in": {"s.t.extra": "resolve"},
                "attributions": {},
            },
        }
    )
    assert bounds.may_read_body("s.t.extra")
    assert bounds.may_inspect_schema("s.t")


def test_sample_rows_asks_for_the_engines_spelling_in_the_right_schema() -> None:
    """The tool that could tell the analyst a column's value vocabulary never returned a row."""
    from governed_bi.serve.fetch import sample_rows
    from governed_bi.serve.tools import tool_bounds_from_state

    table = TableAsset(
        id="airline.Air_Carriers_66c534",
        schema="airline",
        physical_name="Air Carriers",
        summary="air carriers (Air Carriers): Code, Description",
        columns=("airline.Air_Carriers_66c534.Code",),
    )
    column = ColumnAsset(
        id="airline.Air_Carriers_66c534.Code",
        schema="airline",
        parent_table="airline.Air_Carriers_66c534",
        physical_name="Code",
        summary="Code — Air_Carriers_66c534.Code",
        physical_type="TEXT",
    )
    assets = {a.id: a for a in (table, column)}
    statements: list[str] = []

    class Recorder:
        dialect = "postgres"

        def execute(self, sql, **kwargs):
            statements.append(sql)
            return (["Code"], [("AA",), ("DL",)], False)

    state = _state(retrieved_tables=["airline.Air_Carriers_66c534"])
    state["retrieved"]["by_type"]["table"] = ["airline.Air_Carriers_66c534"]
    state["retrieved"]["selected"] = {
        "airline.Air_Carriers_66c534.Code": {
            "asset_id": "airline.Air_Carriers_66c534.Code",
            "asset_type": "column",
            "score": 1.0,
        }
    }
    payload, ok, attempt = sample_rows(
        "airline.Air_Carriers_66c534.Code",
        limit=5,
        bounds=tool_bounds_from_state(state),
        assets=assets,
        connector=Recorder(),
        corpus=for_analyst([table, column]),
        policy=GovernancePolicy(),
    )

    assert ok, payload
    assert statements, "nothing reached the connector"
    sent = statements[0]
    assert '"airline"."Air Carriers"' in sent, (
        f"asked the engine for {sent!r}; the corpus key is not a relation"
    )
    assert '"Air Carriers"."Code"' in sent, sent
    assert '"AA"' in payload
    # The half the old test could not see: this is a governed executor path now.
    assert attempt is not None and attempt["path"] == "sample" and attempt["passed"]
    assert attempt["executed_sql"] == sent


def test_sample_rows_is_a_governed_executor_path() -> None:
    """``sample`` clears the layer stack and writes a ledger row, or it does not run."""
    from governed_bi.corpus.schema import Reliability, ReliabilityStatus
    from governed_bi.serve.fetch import sample_rows

    table = TableAsset(
        id="sales.customers",
        schema="sales",
        physical_name="customers",
        summary="customers table",
        columns=("sales.customers.ssn",),
    )
    suspect = ColumnAsset(
        id="sales.customers.ssn",
        schema="sales",
        parent_table="customers",
        physical_name="ssn",
        summary="national id — known unreliable",
        physical_type="TEXT",
        reliability=Reliability(status=ReliabilityStatus.suspect),
    )
    assets = {a.id: a for a in (table, suspect)}
    executed: list[str] = []

    class Recorder:
        dialect = "postgres"

        def execute(self, sql, **kwargs):
            executed.append(sql)
            return (["ssn"], [("123-45-6789",)], False)

    state = _state(retrieved_tables=["sales.customers"])
    state["retrieved"]["selected"] = {
        "sales.customers.ssn": {
            "asset_id": "sales.customers.ssn",
            "asset_type": "column",
            "score": 1.0,
        }
    }
    payload, ok, attempt = sample_rows(
        "sales.customers.ssn",
        limit=5,
        bounds=tool_bounds_from_state(state),
        assets=assets,
        connector=Recorder(),
        corpus=for_analyst([table, suspect]),
        policy=GovernancePolicy(hard_block_suspect=True),
    )

    assert not ok
    assert not executed, f"a suspect column's values reached the engine: {executed}"
    assert "123-45-6789" not in payload
    assert attempt is not None
    assert attempt["path"] == "sample"
    assert attempt["passed"] is False
    assert attempt["reason_code"] == "r_column_suspect", attempt
    assert attempt["executed_sql"] is None


def test_the_sample_tool_writes_its_ledger_row_and_does_not_answer_the_turn() -> None:
    """Through the real tool adapter: the row is durable, and ``terminal`` stays ``no_sql``."""
    from governed_bi.serve.ledger import execution_from_attempts

    class Recorder:
        dialect = "postgres"

        def execute(self, sql, **kwargs):
            return (["name"], [("Ada",), ("Grace",)], False)

    tools = _tools(config=_config(connector=Recorder()))
    payload, update = _call(
        tools["sample_rows"], call_id="s-1", column_id="sales.customers.name", limit=3
    )

    assert '"Ada"' in payload, payload
    rows = list((update.get("attempts_by_call") or {}).values())
    assert len(rows) == 1, update
    assert rows[0]["path"] == "sample"
    assert rows[0]["passed"] is True
    assert rows[0]["executed_sql"]

    execution = execution_from_attempts(rows)
    assert execution["attempts"] == rows, "the row must stay in the ledger"
    assert execution["terminal"] == "no_sql", (
        "a passing sample row made the turn look answered; that is the "
        "crash-counted-as-refusal inversion arriving through a second executor path"
    )
    assert execution["guardrail_errors"] == 0


def test_the_model_cannot_widen_the_sample_row_bound() -> None:
    """``limit`` is a model-supplied argument and it is clamped from both ends."""
    from governed_bi.serve.fetch import SAMPLE_ROWS_MAX_VALUES, distinct_values_statement

    class Recorder:
        dialect = "postgres"

        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql, **kwargs):
            self.sql.append(sql)
            return (["name"], [], False)

    connector = Recorder()
    tools = _tools(config=_config(connector=connector))
    _call(tools["sample_rows"], call_id="s-2", column_id="sales.customers.name", limit=10_000)

    ceiling = distinct_values_statement(
        schema="sales",
        table="customers",
        column="name",
        limit=SAMPLE_ROWS_MAX_VALUES,
        dialect="postgres",
    )
    assert connector.sql and connector.sql[0] == ceiling, connector.sql


def test_sample_rows_cannot_escape_its_identifier() -> None:
    """A ``physical_name`` holding a double quote names a column, not more SQL."""
    from governed_bi.serve.fetch import distinct_values_statement

    evil = 'x" FROM "pg_catalog"."pg_shadow" -- '
    sql = distinct_values_statement(
        schema="sales", table="customers", column=evil, limit=5, dialect="postgres"
    )
    assert "pg_shadow" in sql, "the fixture stopped exercising the escape"
    assert '"sales"."customers"' in sql
    # Every occurrence of the payload is inside one quoted identifier, so the only relation
    # named is the intended one.
    import sqlglot

    tree = sqlglot.parse_one(sql, dialect="postgres")
    tables = {t.sql(dialect="postgres") for t in tree.find_all(sqlglot.exp.Table)}
    assert tables == {'"sales"."customers"'}, tables
    columns = {c.name for c in tree.find_all(sqlglot.exp.Column)}
    assert columns == {evil}, columns
