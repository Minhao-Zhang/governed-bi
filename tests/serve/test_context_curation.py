"""The hit / pulled-in split in the rendered context, and what it is allowed to cost.

``resolve`` reaches 66–75 assets by reference closure on a real turn while the question
hits under ten, and ``retrieved.attributions`` / ``retrieved.pulled_in`` are the only
record of which is which. If those two ever render the same way again the prompt goes
back to being a blob, and nothing else in the suite would notice — the block would still
hash, still contain every id, and still be under budget. Hence this file.

Measured on the gold semantic layer (5 schemas, 923 assets, ``route_top_n = 1``) the
split is worth 15–27 % of the delivered characters; the ``## Context`` section alone
falls 24–40 %. Those numbers are in ``docs/plans/context-engineering-2026-08-04.md``,
not asserted here: a threshold on them would be a number nobody measured on the corpus
the test builds.
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
