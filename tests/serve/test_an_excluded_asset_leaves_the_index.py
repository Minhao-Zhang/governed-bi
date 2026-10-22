"""``governance.excluded`` removes the asset from the index, not only from the analyst corpus.

The fixture declares columns, a join, a metric, and a term. Dropping an asset leaves required
references dangling, and a fixture without them cannot see a corpus that refuses to serve.
"""

from __future__ import annotations

from governed_bi.corpus.schema import (
    AssetType,
    Binding,
    ColumnAsset,
    Governance,
    JoinAsset,
    MetricAsset,
    SchemaAsset,
    TableAsset,
    TermAsset,
)
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.session import from_assets

EXCLUDED = Governance(excluded=True, reason="decoy: fabricated to mimic zip_code")


def _assets(
    *,
    exclude_column: bool = False,
    exclude_table: bool = False,
    bare_join_endpoint: bool = False,
) -> list[object]:
    """A two-table schema with every reference kind pointing into it.

    ``addr.zip_data`` is real; ``addr.zones`` is the one a table-level exclusion removes.
    ``postal_code`` is the decoy column.
    """
    return [
        SchemaAsset(id="addr", name="addr", summary="addr postal geography"),
        TableAsset(
            id="addr.zip_data",
            schema="addr",
            physical_name="zip_data",
            summary="zip_data one row per postal point",
            # The loader always populates this (`corpus/seed.py`), so the fixture must too.
            columns=("addr.zip_data.zip_code", "addr.zip_data.postal_code"),
        ),
        ColumnAsset(
            id="addr.zip_data.zip_code",
            schema="addr",
            parent_table="zip_data",
            physical_name="zip_code",
            summary="the postal code",
        ),
        ColumnAsset(
            id="addr.zip_data.postal_code",
            schema="addr",
            parent_table="zip_data",
            physical_name="postal_code",
            summary="a second postal code on this table",
            governance=EXCLUDED if exclude_column else Governance(),
        ),
        TableAsset(
            id="addr.zones",
            schema="addr",
            physical_name="zones",
            summary="zones geographic areas",
            columns=("addr.zones.zip_code",),
            governance=EXCLUDED if exclude_table else Governance(),
        ),
        ColumnAsset(
            id="addr.zones.zip_code",
            schema="addr",
            parent_table="zones",
            physical_name="zip_code",
            summary="the postal code of the zone",
        ),
        JoinAsset(
            id="join_addr_zip_data_zones_deadbeef",
            # Deliberately the *bare physical name* on one config when asked: an endpoint may be
            # any of four spellings, and a string comparison against asset ids would miss it.
            left_table="addr.zip_data",
            right_table="zones" if bare_join_endpoint else "addr.zones",
            on="addr.zip_data.zip_code = addr.zones.zip_code",
            summary="zip_data joins zones on the postal code",
        ),
        MetricAsset(
            id="metric_points_per_zone",
            name="points per zone",
            base_table="addr.zones",
            expression="count(*)",
            summary="points per zone counts postal points in each zone",
            dimensions=("addr.zip_data.postal_code",),
        ),
        TermAsset(
            id="term_postcode",
            name="postcode",
            summary="postcode, postal code: the identifier of a delivery area",
            binding=Binding(target_type=AssetType.column, target_id="addr.zip_data.postal_code"),
        ),
    ]


def _session(**kwargs: object) -> object:
    return from_assets(
        _assets(**kwargs),  # type: ignore[arg-type]
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="addr",
        corpus_content_hash_="test",
        agent_model=None,
    )


def _fatal(session: object) -> list[str]:
    return [str(p) for p in session.fatal_problems]  # type: ignore[attr-defined]


def test_a_corpus_with_no_exclusions_is_untouched() -> None:
    """The path every existing corpus takes: 0 excluded assets in either BIRD corpus."""
    session = _session()
    assert set(session.index.entries) == {a.id for a in _assets()}  # type: ignore[attr-defined]
    assert not _fatal(session)


def test_an_excluded_column_is_absent_from_the_index_and_still_refusable() -> None:
    """Gone from the three views the analyst reaches; still known to ``check()``."""
    session = _session(exclude_column=True)

    assert "addr.zip_data.postal_code" not in session.index.entries
    assert "addr.zip_data.postal_code" not in session.assets_by_id
    assert "addr.zip_data.postal_code" not in session.structure.references

    # The sibling is untouched -- exclusion is per asset, not per table.
    assert "addr.zip_data.zip_code" in session.index.entries

    # `for_analyst` still receives the whole list, so the key survives for `check()`.
    assert session.corpus.excluded_columns, (
        "an excluded column must stay refusable by name; dropping it from `for_analyst` "
        "too would turn a governed refusal into a silent 'no such column'"
    )


def test_excluding_a_column_does_not_make_the_corpus_unservable() -> None:
    """Excluding a column prunes the parent's derived id, so the corpus stays servable."""
    session = _session(exclude_column=True)
    assert not _fatal(session), _fatal(session)

    parent = session.assets_by_id["addr.zip_data"]
    assert "addr.zip_data.postal_code" not in parent.columns
    assert "addr.zip_data.zip_code" in parent.columns, "only the excluded column may go"


def test_excluding_a_table_carries_to_its_columns_and_to_the_joins_on_it() -> None:
    """Excluding a table also drops its columns and the joins that required it."""
    session = _session(exclude_table=True)

    assert "addr.zones" not in session.index.entries
    assert "addr.zones.zip_code" not in session.index.entries
    assert "addr.zones.zip_code" not in session.assets_by_id
    assert "join_addr_zip_data_zones_deadbeef" not in session.index.entries
    assert not _fatal(session), _fatal(session)

    # The real table and its column are still served.
    assert "addr.zip_data" in session.index.entries
    assert "addr.zip_data.zip_code" in session.index.entries


def test_a_join_naming_an_excluded_table_by_bare_name_is_dropped_too() -> None:
    """Endpoints have four legal spellings, so exclusion resolves them instead of comparing.

    ``right_table: zones`` is the bare ``physical_name``, not the asset id. A string test
    against ids would keep this join and put the fatal problem straight back.
    """
    session = _session(exclude_table=True, bare_join_endpoint=True)
    assert "join_addr_zip_data_zones_deadbeef" not in session.index.entries
    assert not _fatal(session), _fatal(session)


def test_a_required_reference_excludes_its_referrer_and_an_optional_one_is_pruned() -> None:
    """A required reference drops its referrer; an optional binding is pruned and the term stays."""
    # base_table excluded -> the metric cannot resolve, so it leaves with the table.
    session = _session(exclude_table=True)
    assert "metric_points_per_zone" not in session.index.entries
    assert not _fatal(session), _fatal(session)

    # Only the column excluded: the metric keeps its base table and loses one dimension,
    # and the term keeps its own text but loses the binding.
    session = _session(exclude_column=True)
    metric = session.assets_by_id["metric_points_per_zone"]
    assert metric.dimensions == (), "a dimension naming an excluded column must be pruned"

    term = session.assets_by_id["term_postcode"]
    assert term.binding is None, "the bridge to a withheld column must go"
    assert "term_postcode" in session.index.entries, "the term's own vocabulary still serves"
    assert not _fatal(session), _fatal(session)
