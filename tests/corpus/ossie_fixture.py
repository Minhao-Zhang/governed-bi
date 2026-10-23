"""A small corpus built in memory for the Ossie exporter tests."""

from __future__ import annotations

from governed_bi.corpus.schema import (
    Asset,
    Binding,
    Cardinality,
    ColumnAsset,
    FewShotAsset,
    JoinAsset,
    LogicalType,
    MetricAsset,
    SchemaAsset,
    TableAsset,
    TermAsset,
)
from governed_bi.registry.assets import AssetType


def _table(name: str, columns: list[tuple[str, LogicalType, str]]) -> list[Asset]:
    tid = f"shop.{name}"
    cols = [
        ColumnAsset(
            id=f"{tid}.{col}",
            schema="shop",
            parent_table=tid,
            physical_name=col,
            summary=f"{col}, a column of {name}.",
            physical_type=physical,
            logical_type=logical,
        )
        for col, logical, physical in columns
    ]
    table = TableAsset(
        id=tid,
        schema="shop",
        physical_name=name,
        summary=f"{name} holds one row per {name} record.",
        body="Longer text about the table.",
        rules=("Never use the x column.",),
        columns=tuple(c.id for c in cols),
    )
    return [table, *cols]


def _join(jid: str, left: str, right: str, on: str, cardinality: Cardinality | None) -> JoinAsset:
    return JoinAsset(
        id=jid,
        left_table=f"shop.{left}",
        right_table=f"shop.{right}",
        on=on,
        summary=f"{left} to {right}.",
        cardinality=cardinality,
    )


def fixture_assets() -> list[Asset]:
    return [
        SchemaAsset(id="shop", name="shop", summary="The shop database.", rules=("Join on ids.",)),
        *_table("customers", [("id", LogicalType.integer, "bigint"), ("name", LogicalType.string, "text")]),
        *_table(
            "orders",
            [
                ("id", LogicalType.integer, "bigint"),
                ("customer_id", LogicalType.integer, "bigint"),
                ("total", LogicalType.decimal, "real"),
                ("placed", LogicalType.datetime, "timestamp with time zone"),
            ],
        ),
        *_table("tags", [("order_id", LogicalType.integer, "bigint"), ("tag", LogicalType.string, "text")]),
        *_table("notes", [("order_id", LogicalType.integer, "bigint")]),
        _join(
            "j_many_to_one",
            "orders",
            "customers",
            "shop.orders.customer_id = shop.customers.id",
            Cardinality.many_to_one,
        ),
        _join("j_one_to_many", "customers", "orders", "customers.id = orders.customer_id", Cardinality.one_to_many),
        _join("j_many_to_many", "orders", "tags", "orders.id = tags.order_id", Cardinality.many_to_many),
        _join("j_no_cardinality", "orders", "notes", "orders.id = notes.order_id", None),
        _join("j_not_equality", "orders", "notes", "orders.id > notes.order_id", Cardinality.many_to_one),
        MetricAsset(
            id="metric_revenue",
            name="revenue",
            base_table="shop.orders",
            expression="SUM(total)",
            summary="revenue, the sum of total.",
        ),
        MetricAsset(
            id="metric_broken",
            name="broken",
            base_table="shop.orders",
            expression="DIVIDE(COUNT(id WHERE <condition>), 2)",
            summary="broken, a metric whose expression is prose.",
        ),
        TermAsset(
            id="term_shop_buyer",
            name="buyer",
            summary="buyer: a customer.",
            synonyms=("purchaser", "shopper"),
            binding=Binding(AssetType.table, "shop.customers"),
        ),
        TermAsset(
            id="term_shop_spend",
            name="spend",
            summary="spend: order total.",
            synonyms=("outlay",),
            binding=Binding(AssetType.column, "shop.orders.total"),
        ),
        FewShotAsset(
            id="fs_shop_0000",
            schema="shop",
            sql="SELECT COUNT(*) FROM orders",
            summary="How many orders are there?",
            body="Question: How many orders are there?\nSQL:\nSELECT COUNT(*) FROM orders",
        ),
    ]
