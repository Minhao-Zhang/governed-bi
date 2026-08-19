"""F3: delivery_hash and tool bounds, and the ``sample_rows`` governed executor path.

Split out of ``test_agent_tools_hitl.py`` (ADR 0005 §6 hard cap at 1,000 lines) rather than
appended there. Reuses that file's fixtures (``_state``, ``_tools``, ``_call``, ``_config``)
bare -- ``tests/`` carries no ``__init__.py``, so pytest's rootless import puts ``tests/serve/``
on ``sys.path`` and a sibling module in the same directory is importable by its bare name, the
same pattern ``test_ask_user_choices_and_defer.py`` and
``test_ask_user_outstanding_clarification_latch.py`` already use on this same file.

``test_delivery_hash_stable_for_same_tool_payload`` and
``test_tool_bounds_from_state_includes_pulled_in`` come along rather than staying behind: both
are two of the three things the original module's own docstring names ("tools bounds,
delivery_hash, ask_user HITL + identity-bound resume"), and both are short, generic checks with
no dependency on the ask_user/HITL machinery that stayed in the original file -- moving them here
is what let the boundary land under the cap without splitting the ``sample_rows`` cluster itself.
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
            "licensed": ["s.t"],
            "retrieved": {
                "selected": {},
                "pulled_in": {"s.t.extra": "resolve"},
                "attributions": {},
            },
        },
        {},
    )
    assert bounds.may_read_body("s.t.extra")
    assert bounds.may_inspect_schema("s.t")


def test_sample_rows_asks_for_the_engines_spelling_in_the_right_schema() -> None:
    """The tool that could tell the analyst a column's value vocabulary never returned a row.

    Two independent halves, and it needed both to stay invisible. ``parent_table`` is a
    corpus **key**, so ``.split(".")[-1]`` yielded the slug ``Air_Carriers_66c534`` for a
    table whose engine spelling is ``Air Carriers`` (ADR 0008 D1). And the column's
    ``schema`` was read into a local and then dropped, so ``PostgresConnector`` fell back to
    its private ``schema="public"`` default. On a pooled 57-schema lake the result is
    ``FROM "public"."Air_Carriers_66c534"`` — 42P01 every time, surfaced as a tool error
    that no metric counted.
    """
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

    state = _state(licensed=["airline.Air_Carriers_66c534"])
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
        bounds=tool_bounds_from_state(state, {}),
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
    """``sample`` clears the layer stack and writes a ledger row, or it does not run.

    The path used to reach ``PostgresConnector.execute`` through
    ``connector.sample_values`` — the method ``ports.Connector`` reserves for
    ``govern.pipeline`` — with no PARSE, NO_WRITE, FUNCTIONS, BINDING, COLUMNS or TABLES
    layer and no ``attempt_record``. Two things followed, and this test pins both.

    **The bypass.** ``reliability.status is suspect`` columns stay in ``by_id``, and
    ``hard_block_suspect`` is enforced only inside ``check()``. So under one identical
    policy ``run_query`` refused a suspect column and ``sample_rows`` returned its real
    values. No attacker and no unusual configuration required.

    **The vacuous count.** With no ledger row on the path, ``guardrail_errors == 0`` and an
    empty attempt list were true of every value the tool ever showed the model.
    """
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

    state = _state(licensed=["sales.customers"])
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
        bounds=tool_bounds_from_state(state, {}),
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
    """Through the real tool adapter: the row is durable, and ``terminal`` stays ``no_sql``.

    Two halves that have to hold together. ``attempts_by_call`` is the channel the record is
    built from, so a governed statement missing from it is a statement the audit trail does not
    have. And ``terminal`` must not read a passing ``sample`` row as an answer: a turn that
    sampled a column and then answered from context produced no SQL, and both facts are true of
    it at once.
    """
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
    """``limit`` is a model-supplied argument and it is clamped from both ends.

    It used to be ``max(1, int(limit))`` — a ceiling-free row bound on a tool that grants
    privilege, which is the shape ``ToolBounds`` exists to prevent (ADR 0006 §8).
    """
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
    """A ``physical_name`` holding a double quote names a column, not more SQL.

    ``physical_name`` is deliberately unconstrained in content — ``corpus/identity.slug``
    says it holds the engine's identifier "verbatim: any character, any case, any script",
    and ``corpus/validate.py`` validates only ``slug(physical_name)``, so ``problems_with()``
    raises no objection to the asset below. The old Postgres adapter interpolated it into
    ``f'SELECT DISTINCT "{column}" FROM …'`` with no quote-doubling, and the statement escaped
    its intended relation. No test covered identifier quoting on either adapter.
    """
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


