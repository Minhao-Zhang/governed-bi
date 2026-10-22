"""``corpus_content_hash`` with one file's bytes substituted.

``overrides`` sits on the one hash function, so two implementations cannot disagree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")

from governed_bi.corpus.hash import corpus_content_hash

_TABLE = """asset_type: table
id: shop.orders
schema: shop
physical_name: orders
summary: orders holds one row per placed order in the shop schema.
body: >-
  Grain is one order.
columns:
  - name: order_id
    physical_name: order_id
    summary: The identifier of this order row.
"""


def _corpus(root: Path) -> Path:
    (root / "shop" / "tables").mkdir(parents=True)
    (root / "shop" / "tables" / "tbl_shop_orders.yaml").write_text(_TABLE, encoding="utf-8")
    (root / "shop" / "notes.md").write_text("A note beside the assets.\n", encoding="utf-8")
    return root


def test_an_override_gives_the_hash_the_tree_would_have_after_the_write(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "corpus")
    target = root / "shop" / "tables" / "tbl_shop_orders.yaml"
    edited = _TABLE.replace("Grain is one order.", "Grain is one placed order.")

    # ``write_bytes``, not ``write_text``: text mode can turn LF into CRLF, and the digest reads bytes.
    # The override is the bytes that will sit on disk.
    payload = edited.encode("utf-8")
    predicted = corpus_content_hash(root, overrides={target: payload})
    target.write_bytes(payload)
    assert predicted == corpus_content_hash(root)


def test_an_override_that_changes_nothing_changes_no_hash(tmp_path: Path) -> None:
    """Same bytes must not change the digest, so the override replaces a walk entry instead of appending one."""
    root = _corpus(tmp_path / "corpus")
    target = root / "shop" / "tables" / "tbl_shop_orders.yaml"
    assert corpus_content_hash(root, overrides={target: target.read_bytes()}) == (
        corpus_content_hash(root)
    )


def test_an_override_naming_a_file_outside_the_corpus_is_refused(tmp_path: Path) -> None:
    """A path outside ``root`` is not in the walk. Ignoring it would return the unedited digest."""
    root = _corpus(tmp_path / "corpus")
    with pytest.raises(ValueError, match="not under"):
        corpus_content_hash(root, overrides={tmp_path / "elsewhere.yaml": b"x"})


def test_a_schema_filter_still_applies_to_an_override(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "corpus")
    (root / "warehouse" / "tables").mkdir(parents=True)
    other = root / "warehouse" / "tables" / "tbl_warehouse_orders.yaml"
    other.write_text(_TABLE.replace("shop", "warehouse"), encoding="utf-8")

    shop_only = corpus_content_hash(root, schemas=["shop"])
    assert corpus_content_hash(
        root, schemas=["shop"], overrides={other: b"anything at all"}
    ) == shop_only
