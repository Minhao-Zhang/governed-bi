"""Decoy-touch attribution: the metric must name a *table*, not just a column.

Audit item C6. ``_touches_suspect`` matched bare column names, so a legitimate
``orders.city`` counted as a decoy touch whenever ``customers.city`` happened to be
flagged — ``decoy_touch_rate`` was biased up by however many innocent same-named
columns the schema had. The counterpart hazard is the opposite one: if qualified
matching cannot resolve the decoy's physical table, the rate silently collapses to a
confident ``0.0``, which reads as the model behaving perfectly. Both directions are
pinned here.
"""

from __future__ import annotations

import json

from governed_bi.eval.arms import _split_suspect_refs, _touches_suspect
from governed_bi.eval.hash_grade import load_trap_columns

# What the real producers emit for one flagged column: both the qualified ref and
# the bare name (``_suspect_from_corpus`` and the trap-manifest loader both did).
CUSTOMERS_CITY = frozenset({"customers.city", "city"})


# --------------------------------------------------------------------------- #
# C6: bare-name matching must not survive
# --------------------------------------------------------------------------- #


def test_same_named_column_in_another_table_is_not_a_decoy_touch():
    """The C6 regression. Bare matching returned True for both of these."""
    assert not _touches_suspect("SELECT city FROM orders", CUSTOMERS_CITY, "postgres")
    assert _touches_suspect("SELECT city FROM customers", CUSTOMERS_CITY, "postgres")


def test_alias_resolves_to_the_physical_table():
    assert _touches_suspect("SELECT c.city FROM customers c", CUSTOMERS_CITY, "postgres")
    assert not _touches_suspect(
        "SELECT o.city FROM orders o JOIN customers c ON c.id = o.id",
        CUSTOMERS_CITY,
        "postgres",
    )


def test_ambiguous_bare_column_counts_when_the_decoy_table_is_in_scope():
    """An unqualified name in a join is ambiguous to us but not to the DB, so it
    counts — an over-count shows up in the rate, an under-count looks like good
    behaviour."""
    assert _touches_suspect(
        "SELECT city FROM orders JOIN customers ON customers.id = orders.id",
        CUSTOMERS_CITY,
        "postgres",
    )


def test_reused_alias_is_resolved_in_its_own_scope():
    """A flat per-statement alias map would bind the outer ``t`` to ``customers`` and
    report a decoy touch on a query that only reads ``orders.city``."""
    assert not _touches_suspect(
        "SELECT t.city FROM orders t WHERE t.id IN (SELECT t.id FROM customers t)",
        CUSTOMERS_CITY,
        "postgres",
    )


def test_matching_is_case_insensitive():
    suspect = frozenset({"Customers.ZipCode"})
    assert _touches_suspect("SELECT zipcode FROM CUSTOMERS", suspect, "postgres")


def test_schema_qualified_suspect_ref_folds_to_table_column():
    """``column_allowlist`` yields ``schema.table.column``; the query names the table."""
    suspect = frozenset({"beer_factory.customers.zipcode"})
    assert _touches_suspect("SELECT zipcode FROM customers", suspect, "postgres")
    assert not _touches_suspect("SELECT zipcode FROM orders", suspect, "postgres")


def test_star_projection_names_no_column_to_attribute():
    assert not _touches_suspect("SELECT c.* FROM customers c", CUSTOMERS_CITY, "postgres")


# --------------------------------------------------------------------------- #
# ...and must not narrow to a silent zero
# --------------------------------------------------------------------------- #


def test_a_suspect_ref_with_no_table_still_matches_bare():
    """A caller that only knows a column name must not be silently ignored: the
    honest reading of an unattributable ref is "any table"."""
    assert _touches_suspect('SELECT decoy_a FROM "t7"', frozenset({"decoy_a"}), "postgres")


def test_bare_name_is_dropped_only_when_a_qualified_ref_covers_it():
    qualified, bare_only = _split_suspect_refs(CUSTOMERS_CITY)
    assert qualified == {"customers.city"}
    assert bare_only == frozenset()  # "city" is covered, so it may not match everywhere
    qualified, bare_only = _split_suspect_refs(frozenset({"customers.city", "region"}))
    assert bare_only == {"region"}  # nothing qualified covers it: keep it


def test_unparseable_sql_is_not_a_touch():
    assert not _touches_suspect("SELECT * FROM", CUSTOMERS_CITY, "postgres")


def test_empty_suspect_set_is_never_a_touch():
    assert not _touches_suspect("SELECT city FROM customers", frozenset(), "postgres")


# --------------------------------------------------------------------------- #
# The manifest side: qualified refs must reach the physical table names
# --------------------------------------------------------------------------- #


def _write_bird(tmp_path, *, trap=None, table_trap=None, rename=None):
    art = tmp_path / "artifacts"
    art.mkdir()
    if trap is not None:
        (art / "trap_manifest.json").write_text(json.dumps(trap), encoding="utf-8")
    if table_trap is not None:
        (art / "trap_table_manifest.json").write_text(
            json.dumps(table_trap), encoding="utf-8"
        )
    if rename is not None:
        (art / "schema_rename_map.json").write_text(json.dumps(rename), encoding="utf-8")
    return tmp_path


def test_missing_manifest_is_not_measured_rather_than_trap_free(tmp_path, caplog):
    trap = load_trap_columns(tmp_path, "beer_factory")
    assert trap == frozenset()
    assert trap.manifest_present is False
    assert "NOT MEASURED" in caplog.text


def test_a_genuinely_trap_free_db_is_distinguishable_from_a_missing_manifest(tmp_path):
    bird = _write_bird(tmp_path, trap=[{"db": "other_db", "table": "t", "names": {}}])
    trap = load_trap_columns(bird, "beer_factory")
    assert trap == frozenset()
    assert trap.manifest_present is True  # the whole point of the flag


def test_trap_refs_are_emitted_under_the_renamed_physical_table(tmp_path):
    """The manifest keys tables by their pre-rename BIRD name while the graded db
    serves the renamed one. Emitting only the manifest's spelling would make every
    qualified match on a renamed db fail, i.e. decoy-touch would read 0.0."""
    bird = _write_bird(
        tmp_path,
        trap=[
            {
                "db": "beer_factory",
                "table": "customers",
                "source_column": "CustomerID",
                "names": {"base": "customer_identifier", "rename": "kunde_nummer"},
            }
        ],
        rename={"beer_factory": {"customers": "kunden", "CustomerID": "kunde_id"}},
    )
    trap = load_trap_columns(bird, "beer_factory")
    assert "kunden.kunde_nummer" in trap
    assert "customers.kunde_nummer" in trap
    assert "kunde_nummer" not in trap  # bare refs re-open C6
    assert _touches_suspect(
        "SELECT kunde_nummer FROM beer_factory.kunden", trap, "postgres"
    )
    assert not _touches_suspect(
        "SELECT kunde_id FROM beer_factory.kunden", trap, "postgres"
    )


def test_decoy_table_manifest_contributes_its_columns(tmp_path):
    """The decoy-table variant carries its own physical table + column names under
    ``names.<variant>``; reading the sibling ``columns`` list (source columns, no
    physical name) yielded nothing, so decoy *tables* were entirely unmeasured."""
    bird = _write_bird(
        tmp_path,
        trap=[],
        table_trap=[
            {
                "db": "beer_factory",
                "source_table": "rootbeerreview",
                "columns": [{"source_column": "StarRating"}],
                "names": {
                    "base": {"table": "rootbeer_feedback", "columns": ["rating_stars"]},
                    "rename": {"table": "wurzelbier_feedback", "columns": ["sterne"]},
                },
            }
        ],
    )
    trap = load_trap_columns(bird, "beer_factory")
    assert "wurzelbier_feedback.sterne" in trap
    assert _touches_suspect(
        "SELECT AVG(f.sterne) FROM wurzelbier_feedback f", trap, "postgres"
    )
    assert not _touches_suspect(
        "SELECT AVG(r.sterne) FROM wurzelbier_bewertung r", trap, "postgres"
    )
