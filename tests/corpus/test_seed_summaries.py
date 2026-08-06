"""The seed composes within the cap instead of slicing at it.

``corpus/validate.py`` refuses an oversized summary with *"Rewrite it; do not truncate --
the indexed text is the treatment"*, and the producer used a bare ``[:250]`` on four asset
types. The gold layer inherited 26 table and 3 schema summaries at exactly the cap, at least
one cut mid-identifier.
"""

from __future__ import annotations

from governed_bi.corpus.introspect import (
    ForeignKeyInfo,
    IntrospectedColumn,
    IntrospectedTable,
    Introspection,
)
from governed_bi.corpus.seed import fit_summary, seed
from governed_bi.corpus.validate import problems_with
from governed_bi.register.knobs import knob_default

CAP = int(knob_default("summary_max_chars"))


def _wide_table(name: str, columns: int) -> IntrospectedTable:
    return IntrospectedTable(
        physical_name=name,
        columns=tuple(
            IntrospectedColumn(
                physical_name=f"a_very_long_column_name_number_{i:03d}",
                physical_type="TEXT",
                nullable=True,
            )
            for i in range(columns)
        ),
    )


def test_no_seeded_summary_ends_mid_identifier() -> None:
    """The defect: ``Goali…`` and ``avg_…`` are tokens that name nothing.

    They still occupy an entry in a shared scoring space and they split the IDF of the name
    they were cut out of.
    """
    introspection = Introspection(
        tables=(_wide_table("wide", 80), _wide_table("also_wide", 40)),
        foreign_keys=(),
    )
    assets, problems = seed(introspection, schema="probe")
    assert not problems, problems

    # Every identifier the seed could legitimately have written. Anything in a summary's
    # comma list that is not one of these is a name the composer invented by cutting.
    real_names = {t.physical_name for t in introspection.tables} | {
        c.physical_name for t in introspection.tables for c in t.columns
    }
    for asset in assets:
        summary = asset.summary
        assert len(summary) <= CAP, f"{asset.id}: {len(summary)} chars"
        assert "…" not in summary, f"{asset.id} carries an ellipsis: {summary!r}"
        if ": " not in summary:
            continue
        listed = summary.split(": ", 1)[1]
        for raw in listed.split(", "):
            entry = raw.removesuffix(")").split(" (+")[0].strip()
            if not entry:
                continue
            assert entry in real_names, (
                f"{asset.id} lists {entry!r}, which is not a real identifier — "
                f"the composer cut one in half: {summary!r}"
            )


def test_a_dropped_column_is_counted_rather_than_implied() -> None:
    introspection = Introspection(tables=(_wide_table("wide", 80),), foreign_keys=())
    assets, _ = seed(introspection, schema="probe")
    table = next(a for a in assets if a.id == "probe.wide")
    assert "more)" in table.summary, table.summary
    assert table.summary.endswith(")"), (
        "the closing bracket has to survive the fit, or the list reads as unterminated"
    )
    assert len(table.summary) <= CAP


def test_a_schema_with_many_tables_keeps_the_names_that_fit() -> None:
    tables = tuple(_wide_table(f"table_with_a_long_name_{i:02d}", 2) for i in range(40))
    assets, problems = seed(Introspection(tables=tables, foreign_keys=()), schema="probe")
    assert not problems, problems
    schema_asset = next(a for a in assets if a.id == "probe")
    assert len(schema_asset.summary) <= CAP
    assert "table_with_a_long_name_00" in schema_asset.summary
    assert "more)" in schema_asset.summary


def test_a_join_summary_fits_without_slicing() -> None:
    tables = (_wide_table("orders", 2), _wide_table("customers", 2))
    fk = ForeignKeyInfo(
        from_table="orders",
        from_columns=tuple(f"a_long_foreign_key_column_{i:02d}" for i in range(20)),
        to_table="customers",
        to_columns=tuple(f"a_long_primary_key_column_{i:02d}" for i in range(20)),
    )
    assets, _ = seed(Introspection(tables=tables, foreign_keys=(fk,)), schema="probe")
    joins = [a for a in assets if a.id.startswith("join_")]
    assert joins
    for join in joins:
        assert len(join.summary) <= CAP, join.summary
        assert not join.summary.endswith(","), join.summary


def test_fit_summary_measures_its_tail() -> None:
    """Appending the bracket after the fit is how a 250-cap composer returns 251."""
    entries = [f"column_number_{i:04d}" for i in range(60)]
    out = fit_summary("t (60 columns: ", entries, tail=")")
    assert len(out) <= CAP
    assert out.endswith(")")


def test_every_seeded_asset_passes_the_validator_it_used_to_contradict() -> None:
    introspection = Introspection(tables=(_wide_table("wide", 120),), foreign_keys=())
    assets, _ = seed(introspection, schema="probe")
    for asset in assets:
        assert problems_with(asset) == [], (asset.id, asset.summary)
