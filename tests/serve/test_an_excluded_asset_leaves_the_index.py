"""``governance.excluded`` must reach the index, not just the analyst corpus.

``Governance``'s docstring promises exclusion "removes the asset from everything the analyst
sees, in every environment". Only ``for_analyst`` honoured it. The index was built from the
full asset set, so an excluded column still scored in both channels, still spent one of the
30 column slots, and still rendered once the reference closure pulled it in from its parent
table.

This matters for a specific measurement. On BIRD-Obfuscation the corpus documents 422 planted
decoy columns; the arm that answers "is it better to warn about a decoy or to withhold it?"
needs withholding to actually withhold. Implemented as a flag rather than a file deletion so
both arms load the same tree and differ in one field.
"""

from __future__ import annotations

from governed_bi.corpus.schema import ColumnAsset, Governance, SchemaAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.session import from_assets

EXCLUDED = Governance(excluded=True, reason="decoy: fabricated to mimic zip_code")


def _assets(*, exclude_table: bool = False) -> list[object]:
    return [
        SchemaAsset(id="addr", name="addr", summary="addr postal geography"),
        TableAsset(
            id="addr.zip_data",
            schema="addr",
            physical_name="zip_data",
            summary="zip_data one row per postal point",
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
            summary="decoy mimicking zip_code",
            governance=EXCLUDED,
        ),
        TableAsset(
            id="addr.zip_data_clone",
            schema="addr",
            physical_name="zip_data_clone",
            summary="decoy clone of zip_data",
            governance=EXCLUDED if exclude_table else Governance(),
        ),
        ColumnAsset(
            id="addr.zip_data_clone.zip_code",
            schema="addr",
            parent_table="zip_data_clone",
            physical_name="zip_code",
            summary="decoy clone column",
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


def test_an_excluded_column_is_absent_from_the_index_and_still_refusable() -> None:
    """Gone from the three views the analyst reaches; still known to ``check()``."""
    session = _session()

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


def test_excluding_a_table_carries_to_its_columns_without_a_fatal_problem() -> None:
    """Otherwise using the feature costs an unservable corpus.

    A column id is ``{table_id}.{slug(physical_name)}``, so the table's exclusion reaches its
    columns by prefix. Without that, the orphaned column's ``parent_table`` would not bind and
    ``build_structure`` would raise a **fatal** problem -- exclusion punished as corruption.
    """
    session = _session(exclude_table=True)

    assert "addr.zip_data_clone" not in session.index.entries
    assert "addr.zip_data_clone.zip_code" not in session.index.entries
    assert "addr.zip_data_clone.zip_code" not in session.assets_by_id
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]

    # The real table and its column are still served.
    assert "addr.zip_data" in session.index.entries
    assert "addr.zip_data.zip_code" in session.index.entries


def test_nothing_moves_when_no_asset_is_excluded() -> None:
    """The path every existing corpus takes: 0 excluded assets in either BIRD corpus."""
    session = _session()
    indexed = set(session.index.entries)
    assert indexed == {a.id for a in _assets() if not a.governance.excluded}  # type: ignore[attr-defined]
    assert not session.fatal_problems
