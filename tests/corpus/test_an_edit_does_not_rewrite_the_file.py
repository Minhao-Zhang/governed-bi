"""``corpus/patch.py`` replaces one field. ``store.write`` replaces the file, at the wrong path.

The contrast is the whole reason this module exists, and it is measured rather than asserted: on a
311-line table asset with 34 inline columns, the same one-word edit is **4 changed lines** through
``apply_edit`` and **343** through ``store.write`` — which also lands the result at
``<namespace>/<id>.yaml`` instead of in the file it came from, so the corpus then declares one asset
id twice. ``store.load`` accepts that with zero problems and ``retrieve``'s ``build_index`` raises on
it: a serve outage arriving *after* the commit.

Fixtures here are hand-written YAML rather than corpus files, so the tests run with no sibling
checkout. The 4-vs-343 figure above is from the real corpus and is quoted, not re-measured.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest
import yaml

from governed_bi.corpus.identity import derive_column_id
from governed_bi.corpus.patch import (
    EDITABLE,
    FieldNotLocatable,
    StaleValue,
    apply_edit,
    locate,
    read_field,
)

TABLE_ID = "sales.orders"

#: A table with a folded `summary`, a folded `body`, and two inline columns — one plain scalar and
#: one folded. Both scalar styles matter: the replacement has to come back in the style it went in.
_TABLE = """asset_type: table
id: sales.orders
schema: sales
physical_name: orders
summary: >-
  orders is the transaction table, one row per placed order, holding the customer, the date and
  the total.
body: >-
  Grain is one order. Joins to customers on customer_id.
columns:
  - physical_name: order_id
    summary: The primary key, one per order.
    logical_type: integer
  - physical_name: placed_at
    summary: >-
      Timestamp the order was placed, in UTC, and the column every date filter should use rather
      than shipped_at.
    logical_type: datetime
"""


#: The same asset with an anchored scalar referenced twice. At module scope because an indented
#: triple-quoted string inside a function body is not the YAML it looks like.
_ANCHORED = """asset_type: table
id: sales.orders
schema: sales
physical_name: orders
summary: &shared A shared sentence.
body: *shared
"""


def _file(tmp_path: Path, text: str = _TABLE) -> Path:
    path = tmp_path / "tbl_sales_orders.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _changed_lines(before: str, after: str) -> list[str]:
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
    return [d for d in diff if d.startswith(("+", "-")) and not d.startswith(("+++", "---"))]


def test_a_plain_scalar_edit_is_one_line_each_way(tmp_path: Path) -> None:
    """The best case, and the common one for a column: one line out, one line in."""
    path = _file(tmp_path)
    column_id = derive_column_id(TABLE_ID, "order_id")
    was = read_field(path, asset_id=column_id, field_path="summary")
    assert was == "The primary key, one per order."

    updated = apply_edit(
        path,
        asset_id=column_id,
        field_path="summary",
        was=was,
        becomes="The primary key, exactly one per order.",
    )

    changed = _changed_lines(path.read_text(encoding="utf-8"), updated)
    assert len(changed) == 2, changed
    assert yaml.safe_load(updated)["columns"][0]["summary"] == (
        "The primary key, exactly one per order."
    )


def test_a_folded_block_reflows_and_nothing_else_does(tmp_path: Path) -> None:
    """A block rewraps, so the change is *minimal* rather than one line — and the rest of a
    311-line file staying untouched is the property that matters."""
    path = _file(tmp_path)
    original = path.read_text(encoding="utf-8")
    was = read_field(path, asset_id=TABLE_ID, field_path="summary")

    updated = apply_edit(
        path,
        asset_id=TABLE_ID,
        field_path="summary",
        was=was,
        becomes=was.replace("transaction table", "canonical transaction table", 1),
    )

    changed = _changed_lines(original, updated)
    assert len(changed) <= 6, changed
    # Everything outside the edited block is byte-identical.
    assert "physical_name: order_id" in updated
    assert "Grain is one order." in updated
    assert yaml.safe_load(updated)["columns"] == yaml.safe_load(original)["columns"]


def test_the_scalar_style_survives_the_edit(tmp_path: Path) -> None:
    """A folded block comes back folded and a plain scalar comes back plain.

    Turning one into the other is a whole-paragraph diff for a one-word change, which is the defect
    this module exists to avoid — and a reviewer who sees the style change cannot tell what else
    changed with it.
    """
    path = _file(tmp_path)
    was = read_field(path, asset_id=TABLE_ID, field_path="body")
    updated = apply_edit(
        path, asset_id=TABLE_ID, field_path="body", was=was, becomes=was + " Never fan out."
    )
    assert "body: >-" in updated, updated

    column_id = derive_column_id(TABLE_ID, "order_id")
    plain = apply_edit(
        path,
        asset_id=column_id,
        field_path="summary",
        was="The primary key, one per order.",
        becomes="One row per order.",
    )
    assert "summary: One row per order." in plain, plain


def test_the_indentation_of_the_file_is_kept_rather_than_imposed(tmp_path: Path) -> None:
    """A file indented four spaces comes back indented four. Guessing two because that is this
    corpus's convention would reformat every file that is not this corpus."""
    text = _TABLE.replace("\n  orders is", "\n    orders is").replace(
        "\n  the total.", "\n    the total."
    )
    path = _file(tmp_path, text)
    was = read_field(path, asset_id=TABLE_ID, field_path="summary")

    updated = apply_edit(
        path, asset_id=TABLE_ID, field_path="summary", was=was, becomes=was + " Always."
    )

    body = updated.split("summary: >-\n", 1)[1].splitlines()[0]
    assert body.startswith("    "), repr(body)


def test_a_stale_patch_is_refused_rather_than_applied(tmp_path: Path) -> None:
    """`was` is the concurrency check, not documentation. Between drafting a patch and applying it
    the corpus can move, and a patch that overwrote whatever it found would discard that."""
    path = _file(tmp_path)
    with pytest.raises(StaleValue, match="corpus moved under this patch"):
        apply_edit(
            path,
            asset_id=TABLE_ID,
            field_path="summary",
            was="a summary this file has never held",
            becomes="anything",
        )


def test_governance_cannot_be_reached(tmp_path: Path) -> None:
    """ADR 0005: exclusion is "human-only, enforced by the absence of a tool", and this is the
    tool whose absence is the control."""
    path = _file(tmp_path)
    for field in ("governance", "governance.excluded", "provenance", "audit"):
        with pytest.raises(FieldNotLocatable):
            locate(path, asset_id=TABLE_ID, field_path=field)


def test_only_the_two_landable_fields_are_editable(tmp_path: Path) -> None:
    """A path this module can splice but ``lifecycle.derived_state`` cannot confirm produces a
    patch that lands and reads as ``superseded`` forever."""
    assert EDITABLE == {"summary", "body"}
    path = _file(tmp_path)
    with pytest.raises(FieldNotLocatable, match="not an editable field path"):
        locate(path, asset_id=TABLE_ID, field_path="physical_name")


def test_an_unknown_asset_says_what_the_file_does_declare(tmp_path: Path) -> None:
    path = _file(tmp_path)
    with pytest.raises(FieldNotLocatable) as caught:
        locate(path, asset_id="sales.nope", field_path="summary")
    assert "sales.orders" in str(caught.value)
    assert "inline column" in str(caught.value)


def test_a_shared_scalar_is_refused_rather_than_split(tmp_path: Path) -> None:
    """An anchored scalar is referenced from more than one place, so replacing it at one site
    changes the others. Detected by node identity, because PyYAML's nodes carry no `anchor`
    attribute — `compose` returns the *same object* at every reference, which makes identity both
    the available check and the precise one.
    """
    path = _file(tmp_path, _ANCHORED)
    with pytest.raises(FieldNotLocatable, match="more than one place"):
        locate(path, asset_id=TABLE_ID, field_path="summary")


def test_a_value_that_would_change_meaning_unquoted_is_quoted(tmp_path: Path) -> None:
    """Writing `no` plain makes it the boolean `False`, and writing `1.5` plain makes it a float.
    A summary that loads as something other than a string is worse than a quoted one."""
    path = _file(tmp_path)
    column_id = derive_column_id(TABLE_ID, "order_id")
    for value in ("no", "1.5", "true", "- leading dash", "key: value"):
        updated = apply_edit(
            path,
            asset_id=column_id,
            field_path="summary",
            was="The primary key, one per order.",
            becomes=value,
        )
        loaded = yaml.safe_load(updated)["columns"][0]["summary"]
        assert loaded == value, f"{value!r} came back as {loaded!r}"


def test_apply_edit_does_not_write(tmp_path: Path) -> None:
    """It returns the text. The exporter writes, so a caller can preview a change — and a function
    that both computes and commits one cannot be used to preview it."""
    path = _file(tmp_path)
    before = path.read_text(encoding="utf-8")
    was = read_field(path, asset_id=TABLE_ID, field_path="summary")

    apply_edit(path, asset_id=TABLE_ID, field_path="summary", was=was, becomes=was + " More.")

    assert path.read_text(encoding="utf-8") == before
