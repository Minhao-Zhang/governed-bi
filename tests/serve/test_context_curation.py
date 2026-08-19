"""The hit / pulled-in split in the rendered context, and what it is allowed to cost.

``resolve`` reaches 66–75 assets by reference closure on a real turn while the question
hits under ten, and ``retrieved.attributions`` / ``retrieved.pulled_in`` are the only
record of which is which. If those two ever render the same way again the prompt goes
back to being a blob, and nothing else in the suite would notice — the block would still
hash, still contain every id, and still be under budget. Hence this file.

Measured on the gold semantic layer (5 schemas, 923 assets, ``route_top_n = 1``) the
split is worth 15–27 % of the delivered characters; the ``## Context`` section alone
falls 24–40 %. Those numbers are not asserted here: a threshold on them would be a
number nobody measured on the corpus the test builds.
"""

from __future__ import annotations

from typing import Any

from governed_bi.corpus.schema import (
    Cardinality,
    ColumnAsset,
    JoinAsset,
    LogicalType,
    SchemaAsset,
    TableAsset,
)
from governed_bi.serve.context import render_context

SCHEMA = "shop"
HIT_BODY = "THE-QUESTION-HIT-THIS-TABLE"
PULLED_BODY = "THE-CLOSURE-DRAGGED-THIS-IN"
HIT_COLUMN_BODY = "THE-QUESTION-HIT-THIS-COLUMN"
JOIN_BODY = "THE-QUESTION-HIT-THIS-JOIN"


def _corpus() -> dict[str, Any]:
    """One hit table, one pulled-in table, a hit column, a hit join. Every asset has a body."""
    orders = TableAsset(
        id="shop.orders",
        schema=SCHEMA,
        physical_name="orders",
        summary="orders",
        body=HIT_BODY,
        grain="one row per order",
        row_count=4321,
        columns=("shop.orders.id", "shop.orders.customer_id"),
    )
    customers = TableAsset(
        id="shop.customers",
        schema=SCHEMA,
        physical_name="customers",
        summary="customers",
        body=PULLED_BODY,
        grain="one row per customer",
        row_count=99,
        columns=("shop.customers.id", "shop.customers.email"),
    )
    assets: list[Any] = [
        SchemaAsset(id=SCHEMA, name=SCHEMA, summary="shop"),
        orders,
        customers,
        ColumnAsset(
            id="shop.orders.id",
            schema=SCHEMA,
            # Qualified, the way the gold semantic layer spells it. A bare ``orders``
            # is equally legal and ``_parent_table_id`` has to bind both.
            parent_table="shop.orders",
            physical_name="id",
            summary="order id",
            body=PULLED_BODY,
            logical_type=LogicalType.integer,
        ),
        ColumnAsset(
            id="shop.orders.customer_id",
            schema=SCHEMA,
            parent_table="shop.orders",
            physical_name="customer_id",
            summary="fk to customers",
            body=HIT_COLUMN_BODY,
            logical_type=LogicalType.integer,
        ),
        ColumnAsset(
            id="shop.customers.id",
            schema=SCHEMA,
            parent_table="customers",  # bare spelling
            physical_name="id",
            summary="customer id",
            body=PULLED_BODY,
            logical_type=LogicalType.integer,
        ),
        ColumnAsset(
            id="shop.customers.email",
            schema=SCHEMA,
            parent_table="customers",
            physical_name="email",
            summary="customer email",
            body=PULLED_BODY,
            logical_type=LogicalType.string,
        ),
        JoinAsset(
            id="join_shop_orders_customers_abc123",
            left_table="shop.orders",
            right_table="shop.customers",
            on="orders.customer_id = customers.id",
            summary="orders to customers",
            body=JOIN_BODY,
            cardinality=Cardinality.many_to_one,
        ),
    ]
    return {a.id: a for a in assets}


def _retrieved() -> dict[str, Any]:
    """``shop.orders``, its ``customer_id`` and the join are hits; everything else closure.

    The join is in **both** containers, which is not contrived: ``connect_node`` ends with
    ``pulled_in.setdefault(join_id, "connect")`` over ``complete_joins``, so every join the
    question hits is added to ``pulled_in`` after the fact.
    """
    hit = {"asset_id": "shop.orders", "asset_type": "table", "score": 0.9}
    return {
        "by_type": {
            "table": ["shop.orders"],
            "column": ["shop.orders.customer_id"],
            "join": ["join_shop_orders_customers_abc123"],
        },
        "selected": {
            "shop.orders": hit,
            "shop.orders.customer_id": {**hit, "asset_id": "shop.orders.customer_id"},
            "join_shop_orders_customers_abc123": {
                **hit,
                "asset_id": "join_shop_orders_customers_abc123",
                "asset_type": "join",
            },
        },
        "attributions": {},
        "pulled_in": {
            "shop.customers": "connect",
            "shop.orders.id": "resolve",
            "shop.customers.id": "resolve",
            "shop.customers.email": "resolve",
            "join_shop_orders_customers_abc123": "connect",
        },
        "schema_ranking": [(SCHEMA, 1.0)],
        "lexical_coverage": 1.0,
    }


def _render() -> str:
    block, digest = render_context(
        retrieved=_retrieved(), assets_by_id=_corpus(), schemas=[SCHEMA]
    )
    assert len(digest) == 64
    return block


def test_a_hit_spends_its_body_and_a_pulled_in_asset_does_not() -> None:
    """The one property. If these two ever render alike the curation policy is gone."""
    block = _render()

    assert HIT_BODY in block, "the table the question hit must deliver its body"
    assert HIT_COLUMN_BODY in block, "the column the question hit must deliver its body"

    assert PULLED_BODY not in block, (
        "a pulled-in asset spent its body. Reference closure is a correctness rule, not a "
        "relevance one — 66-75 assets arrive that way on a real turn and rendering their "
        "bodies is the blob this split exists to prevent."
    )


def test_a_pulled_in_asset_is_still_present_and_namable() -> None:
    """Withholding the body may not cost the model the ability to write the SQL."""
    block = _render()

    assert "table shop.customers" in block
    for column in ("- email type=string", "- id type=integer"):
        assert column in block, f"pulled-in column line {column!r} is missing"
    # The join predicate survives even though the join is in ``pulled_in``: ADR 0005 §3.6,
    # "or the prompt shows a join the model cannot spell".
    assert "on orders.customer_id = customers.id" in block


def test_pulled_in_and_hit_lines_are_visibly_different() -> None:
    """Fail loudly if the two forms converge, in either direction.

    Asserting only "the body is absent" would still pass if someone made *hits* terse, or
    made pulled-in assets carry every descriptive field again. Both directions are checked
    against fields the two assets genuinely both have.
    """
    block = _render()

    assert "table shop.orders grain=one row per order rows=4321" in block, (
        "the hit table lost its full structural line"
    )
    assert "grain=one row per customer" not in block and "rows=99" not in block, (
        "the pulled-in table is rendering the full structural line again — hits and "
        "pulled-in assets are indistinguishable, which is the state this test exists to "
        "detect"
    )


def test_a_hit_that_is_also_pulled_in_is_a_hit() -> None:
    """``complete_joins`` re-marks every hit join as ``pulled_in``; that must not demote it.

    ``pulled_in`` used to win this comparison, so on the gold layer 2 of 2 and 3 of 3 join
    hits lost their bodies to a bookkeeping ``setdefault`` in ``connect_node``.
    """
    retrieved = _retrieved()
    join_id = "join_shop_orders_customers_abc123"
    assert join_id in retrieved["selected"] and join_id in retrieved["pulled_in"]

    block = _render()
    assert JOIN_BODY in block, "a join in both containers was demoted to pulled-in"
    assert "cardinality=many_to_one" in block


def test_context_and_caveats_spell_a_column_the_same_way() -> None:
    """A prohibition naming a column the context block never showed is not a prohibition."""
    from dataclasses import replace

    from governed_bi.corpus.schema import Reliability, ReliabilityStatus

    assets = _corpus()
    assets["shop.customers.email"] = replace(
        assets["shop.customers.email"],
        reliability=Reliability(status=ReliabilityStatus.suspect, note="DECOY"),
    )
    block, _ = render_context(
        retrieved=_retrieved(), assets_by_id=assets, schemas=[SCHEMA]
    )
    assert "- shop.customers.email: suspect - DECOY" in block
    # ``parent_table`` is the bare ``customers`` here and ``shop.orders.id``'s is qualified;
    # neither may produce ``shop.shop.customers.email``.
    assert "shop.shop." not in block


def test_a_slugged_table_renders_the_key_the_tools_accept() -> None:
    """``airline."Air Carriers"`` is what SQL needs; ``airline.Air_Carriers_66c534`` is what
    every tool needs, and only the first was ever printed.

    ``slug()`` exists because an id becomes a filename and ``"Air Carriers".yaml`` is illegal
    on Windows (``corpus/identity.py``), so the divergence is deliberate and permanent. What
    was missing is that the prompt only ever saw one side of it: ``context.py`` renders
    ``physical_name`` and ``bounds.may_inspect_schema`` tests membership of ``licensed``,
    which holds ids. Every call on the rendered spelling returned ``OUT_OF_SCOPE_MESSAGE`` —
    which ``bounds.py`` makes deliberately indistinguishable from "not licensed", so the model
    could not tell a typo from a permission error and had no way to recover.
    """
    from governed_bi.corpus.schema import ColumnAsset, TableAsset
    from governed_bi.serve.context import render_context

    table = TableAsset(
        id="airline.Air_Carriers_66c534",
        schema="airline",
        physical_name="Air Carriers",
        summary="air carriers (Air Carriers): Code",
        columns=("airline.Air_Carriers_66c534.Code",),
    )
    column = ColumnAsset(
        id="airline.Air_Carriers_66c534.Code",
        schema="airline",
        parent_table="airline.Air_Carriers_66c534",
        physical_name="Code",
        summary="Code — Air_Carriers_66c534.Code",
        physical_type="TEXT",
        nullable=False,
    )
    retrieved = {
        "by_type": {"table": [table.id], "column": [column.id]},
        "selected": {
            table.id: {"asset_id": table.id, "asset_type": "table", "score": 1.0},
            column.id: {"asset_id": column.id, "asset_type": "column", "score": 0.9},
        },
        "attributions": {},
        "pulled_in": {},
        "schema_ranking": [("airline", 1.0)],
        "lexical_coverage": 1.0,
    }
    text, _ = render_context(
        retrieved=retrieved,
        assets_by_id={table.id: table, column.id: column},
        schemas=["airline"],
    )
    assert "table airline.Air Carriers" in text, text
    assert "id=airline.Air_Carriers_66c534" in text, (
        f"the prompt names a table no tool call can address:\n{text}"
    )
    # Nullability decides whether COUNT(col) and COUNT(*) agree and whether
    # NOT IN (subquery) returns the empty set. Populated on every seeded column, never shown.
    assert "nullable=false" in text, text


def test_a_table_whose_key_is_its_name_renders_exactly_as_before() -> None:
    """655 of 656 gold tables agree, so their ``context_hash`` must not move."""
    from governed_bi.corpus.schema import TableAsset
    from governed_bi.serve.context import render_context

    table = TableAsset(
        id="sales.customers",
        schema="sales",
        physical_name="customers",
        summary="customers (customers): id",
        columns=(),
    )
    retrieved = {
        "by_type": {"table": [table.id]},
        "selected": {table.id: {"asset_id": table.id, "asset_type": "table", "score": 1.0}},
        "attributions": {},
        "pulled_in": {},
        "schema_ranking": [("sales", 1.0)],
        "lexical_coverage": 1.0,
    }
    text, _ = render_context(
        retrieved=retrieved, assets_by_id={table.id: table}, schemas=["sales"]
    )
    assert "table sales.customers" in text
    assert "id=" not in text, f"noise on the 99.8% case:\n{text}"


def test_the_analyst_prompt_names_the_id_convention_and_every_tool() -> None:
    """The rendered ``id=`` is useless if nothing tells the model what it is for.

    v1 named two of the five bound tools and then said *"Prefer run_query"*, which is advice
    against calling the three that could have told it a column's value vocabulary — the
    information the corpus does not carry (0 of 5 947 columns have ``sample_values``).

    ``state_assumption`` is the sixth bound tool (Gap 1, 2026-08-07 Power Kiosk audit) and was
    never named here — nothing told the model when to prefer it over ``ask_user``, which is
    exactly why "Who are our best customers?" landed on either one non-deterministically across
    runs instead of reliably asking which metric "best" means.

    v4 adds ``ask_user``'s new required ``basis`` argument — the prompt must name it and both
    of its literal values, or the model has no guidance on which one to pass for either of the
    two triggers this docstring already describes.
    """
    from governed_bi.register.prompts import PROMPT_REGISTRY, prompt_text

    text = prompt_text("analyst")
    assert "id=" in text, "the id convention is rendered but never explained"
    for tool in (
        "read_body",
        "inspect_schema",
        "sample_rows",
        "run_query",
        "ask_user",
        "state_assumption",
    ):
        assert tool in text, f"{tool} is bound on every turn and never mentioned"
    assert "v1" in PROMPT_REGISTRY["analyst"].variants, (
        "v1 is the baseline v2 has to beat; deleting it deletes the comparison"
    )
    assert "basis" in text, "ask_user's basis argument is required but never explained"
    assert "data_definition" in text, "no guidance on when to pass basis=\"data_definition\""
    assert "ranking_ambiguity" in text, "no guidance on when to pass basis=\"ranking_ambiguity\""


def test_the_analyst_prompt_tells_the_model_to_answer_in_the_users_language() -> None:
    """v5: a live-observed bug -- "Who are our best customers?" asked in English against the
    German-language beer_factory corpus got back a German ask_user question. Nothing in any
    variant up to v4 says anything about response language, so the model mirrored the corpus's
    language instead of the user's. v5 must say to match the user's question language, not the
    corpus's/schema's, for both ask_user and state_assumption.
    """
    from governed_bi.register.prompts import prompt_text

    text = prompt_text("analyst")
    assert "same language" in text and "user" in text, (
        "v5 must instruct the model to answer in the user's own language, not the corpus's"
    )
    assert "state_assumption" in text and "language" in text


def test_the_analyst_prompt_scopes_the_language_rule_to_the_final_answer_too() -> None:
    """v6: a live-observed residual of the v5 bug -- v5's rule names only ask_user's
    question/why and state_assumption's text, never the model's own closing prose. A live
    multi-tool-call turn (ask_user -> English answer -> inspect_schema -> sample_rows ->
    run_query -> final answer) reverted to German exactly at that last, unnamed step, even
    though every ask_user/state_assumption call in the same turn stayed in English -- the
    rule was never violated, because it never applied there. v6 must say the rule covers the
    turn's own final/closing answer as well, not just the two tools.
    """
    from governed_bi.register.prompts import prompt_text

    text = prompt_text("analyst")
    assert "final answer" in text or "closing answer" in text, (
        "v6 must extend the language rule to the model's own final answer, not just "
        "ask_user/state_assumption"
    )
    assert "tool call" in text, (
        "v6 must say the rule survives the tool calls in between, since that is exactly "
        "where the live-observed drift happened"
    )


def test_the_analyst_prompt_guides_grounded_multiple_choice() -> None:
    """v5: ask_user's new ``choices`` argument is real but unused unless the prompt tells the
    model when it is appropriate to pass it -- grounded in something actually inspected, never
    invented, and never at the cost of also accepting free text.
    """
    from governed_bi.register.prompts import prompt_text

    text = prompt_text("analyst")
    assert "choices" in text
    assert "grounded" in text or "actually" in text, (
        "the prompt must distinguish grounded candidates from invented ones"
    )
    assert "allow_freeform" in text, "the prompt must say to keep free text available"


def test_the_narrate_prompt_tells_the_model_to_answer_in_the_users_language() -> None:
    """v2: the same root cause as ANALYST v5 -- NARRATE (the fallback generation path when the
    agent finished on a tool call with no prose of its own) was silent on response language too.
    """
    from governed_bi.register.prompts import PROMPT_REGISTRY, prompt_text

    text = prompt_text("narrate")
    assert "same language" in text and "question" in text
    assert "v1" in PROMPT_REGISTRY["narrate"].variants, (
        "v1 is the baseline v2 has to beat; deleting it deletes the comparison"
    )


def test_context_eviction_reports_what_it_dropped() -> None:
    """``_assemble_and_evict`` dropped bodies and whole tables with no signal anywhere.

    No return value, no state field, no record field, no test — so a gold table that was routed,
    retrieved, licensed and then evicted for space was indistinguishable in every artifact from
    one that was rendered. That blind spot sits exactly between "table selection" and
    "generation", the two stages any attribution of the remaining loss has to separate.
    """
    from governed_bi.corpus.schema import TableAsset
    from governed_bi.serve.context import render_context

    hit = TableAsset(
        id="sales.hit",
        schema="sales",
        physical_name="hit",
        summary="hit table",
        body="B" * 4000,
        columns=(),
    )
    pulled = [
        TableAsset(
            id=f"sales.p{i:02d}",
            schema="sales",
            physical_name=f"p{i:02d}",
            summary=f"pulled {i}",
            body="C" * 2000,
            columns=(),
        )
        for i in range(6)
    ]
    assets = {a.id: a for a in [hit, *pulled]}
    retrieved = {
        "by_type": {"table": [hit.id]},
        "selected": {hit.id: {"asset_id": hit.id, "asset_type": "table", "score": 1.0}},
        "attributions": {},
        "pulled_in": {a.id: "connect" for a in pulled},
        "schema_ranking": [("sales", 1.0)],
        "lexical_coverage": 1.0,
    }
    evicted: dict = {}
    text, _ = render_context(
        retrieved=retrieved,
        assets_by_id=assets,
        schemas=["sales"],
        budget_chars=300,
        evicted=evicted,
    )
    assert evicted, "the budget bit and nothing recorded it"
    assert evicted.get("bodies_dropped") or evicted.get("tables_dropped"), evicted
    # When both rungs are exhausted the function returns an OVER-BUDGET block. A caller that
    # believes the budget was honoured is reading something untrue, so the overrun is named.
    if len(text) > 300:
        assert evicted["over_budget"] == len(text) - 300


def test_a_turn_under_budget_records_no_eviction() -> None:
    """Otherwise every turn would carry a field saying nothing happened."""
    from governed_bi.corpus.schema import TableAsset
    from governed_bi.serve.context import render_context

    table = TableAsset(
        id="sales.customers",
        schema="sales",
        physical_name="customers",
        summary="customers",
        columns=(),
    )
    evicted: dict = {}
    render_context(
        retrieved={
            "by_type": {"table": [table.id]},
            "selected": {table.id: {"asset_id": table.id, "asset_type": "table", "score": 1.0}},
            "attributions": {},
            "pulled_in": {},
            "schema_ranking": [("sales", 1.0)],
            "lexical_coverage": 1.0,
        },
        assets_by_id={table.id: table},
        schemas=["sales"],
        evicted=evicted,
    )
    assert evicted == {}
