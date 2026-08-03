"""Shared F2 fixtures: tiny two-schema index + assets_by_id."""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.corpus.schema import SchemaAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.assets import AssetType
from governed_bi.retrieve.index import IndexEntry, UnifiedIndex, build_index

SCHEMA_A = "sales_a"
SCHEMA_B = "ops_b"


def _table(schema: str, physical: str, summary: str) -> TableAsset:
    return TableAsset(
        id=f"{schema}.{physical}",
        schema=schema,
        physical_name=physical,
        summary=summary,
    )


def build_two_schema_corpus() -> tuple[UnifiedIndex, dict[str, Any]]:
    """Schema A has many customer tables; B has few — pass-one depth starves A."""
    assets: list[Any] = [
        SchemaAsset(
            id=SCHEMA_A, name=SCHEMA_A, summary="sales_a customer commerce orders"
        ),
        SchemaAsset(
            id=SCHEMA_B, name=SCHEMA_B, summary="ops_b warehouse logistics fleet"
        ),
    ]
    for i in range(12):
        name = f"customer_{i}" if i else "customers"
        assets.append(
            _table(SCHEMA_A, name, f"{name} customer account for registered buyers")
        )
    for name, summary in (
        ("orders", "orders one row per customer purchase"),
        ("invoices", "invoices billing for a customer order"),
        ("payments", "payments settlement for a customer invoice"),
    ):
        assets.append(_table(SCHEMA_A, name, summary))
    assets.extend(
        [
            _table(SCHEMA_B, "sensors", "sensors voltage reading per device"),
            _table(SCHEMA_B, "shipments", "shipments outbound logistics load"),
            _table(SCHEMA_B, "custodian", "custodian warehouse contact not retail"),
        ]
    )

    assets_by_id: dict[str, Any] = {}
    entries: list[IndexEntry] = []
    for asset in assets:
        assets_by_id[asset.id] = asset
        if isinstance(asset, SchemaAsset):
            entries.append(
                IndexEntry(
                    id=asset.id,
                    summary=asset.summary,
                    asset_type=AssetType.schema,
                    schema_tag=asset.name,
                )
            )
        else:
            entries.append(
                IndexEntry(
                    id=asset.id,
                    summary=asset.summary,
                    asset_type=AssetType.table,
                    schema_tag=asset.schema,
                )
            )
    return build_index(entries), assets_by_id


@pytest.fixture
def two_schema_index() -> UnifiedIndex:
    return build_two_schema_corpus()[0]


@pytest.fixture
def two_schema_assets() -> dict[str, Any]:
    return build_two_schema_corpus()[1]


@pytest.fixture
def guard_off_policy() -> GovernancePolicy:
    return GovernancePolicy(guard_rules_enabled={})
