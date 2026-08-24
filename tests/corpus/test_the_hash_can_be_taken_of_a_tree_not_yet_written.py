"""``corpus_content_hash`` over a tree with one file substituted, and why that is needed.

``DerivedState.landed_verified`` means "the loaded corpus is *exactly* the tree this bundle
predicted", as distinct from ``landed_matched`` -- "the assets are right and something else landed
alongside". ``lifecycle.py`` reads ``patch.expected_corpus_content_hash`` to tell them apart.

**Nothing set that field.** ``tools/export_bundle.py`` deliberately omits it from the manifest --
"the digest of a tree nobody has written yet" -- and names ``tools/check_landed.py`` as where it gets
computed; that file does not contain the symbol, does not call ``move_patch``, and never did. So the
field was always ``None``, the branch never fired, and ``landed_verified`` was a declared state with
no producer. The tests that covered it built the hash by hand.

The digest is a deterministic walk of relative path plus file content, so the post-state hash *is*
computable before the commit: same walk, one file's bytes substituted. `overrides` is a parameter on
the one hash function rather than a second function, because two digest implementations is how two
answers to "is this the same corpus" come to disagree.
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
    """The whole claim: predicted before the write equals measured after it.

    This is what makes `landed_verified` mean something. Without it the exporter can only record
    "these assets carry the text I expected", which is `landed_matched` -- true of a corpus where
    three other bundles also landed.
    """
    root = _corpus(tmp_path / "corpus")
    target = root / "shop" / "tables" / "tbl_shop_orders.yaml"
    edited = _TABLE.replace("Grain is one order.", "Grain is one placed order.")

    # `write_bytes`, not `write_text`. On Windows `write_text` translates LF to CRLF, so the same
    # string produces different bytes on disk than in memory -- and the digest reads bytes. The
    # contract is therefore "the bytes that will exist on disk", which is what `git apply` writes:
    # a caller passing a str it then writes in text mode would predict a hash the commit never has.
    payload = edited.encode("utf-8")
    predicted = corpus_content_hash(root, overrides={target: payload})
    target.write_bytes(payload)
    assert predicted == corpus_content_hash(root)


def test_an_override_that_changes_nothing_changes_no_hash(tmp_path: Path) -> None:
    """Substituting a file's own bytes is the identity, which is the sanity check that the override
    is applied at the same point in the walk rather than appended to it."""
    root = _corpus(tmp_path / "corpus")
    target = root / "shop" / "tables" / "tbl_shop_orders.yaml"
    assert corpus_content_hash(root, overrides={target: target.read_bytes()}) == (
        corpus_content_hash(root)
    )


def test_an_override_naming_a_file_outside_the_corpus_is_refused(tmp_path: Path) -> None:
    """A path that is not under ``root`` cannot be in the walk, so honouring it would produce a
    digest of a tree that cannot exist. Silently ignoring it would produce the *unedited* hash and
    report `superseded` on a landing that went perfectly."""
    root = _corpus(tmp_path / "corpus")
    with pytest.raises(ValueError, match="not under"):
        corpus_content_hash(root, overrides={tmp_path / "elsewhere.yaml": b"x"})


def test_a_schema_filter_still_applies_to_an_override(tmp_path: Path) -> None:
    """``schemas`` restricts the digest to named subtrees, and an override must not smuggle a file
    back in: an arm's treatment identity covers exactly the schemas it served."""
    root = _corpus(tmp_path / "corpus")
    (root / "warehouse" / "tables").mkdir(parents=True)
    other = root / "warehouse" / "tables" / "tbl_warehouse_orders.yaml"
    other.write_text(_TABLE.replace("shop", "warehouse"), encoding="utf-8")

    shop_only = corpus_content_hash(root, schemas=["shop"])
    assert corpus_content_hash(
        root, schemas=["shop"], overrides={other: b"anything at all"}
    ) == shop_only
