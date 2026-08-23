"""``tools/check_ratchet.py`` on a synthetic corpus: it must fail in **both** directions.

The gate itself cannot run in CI — the pin file lives in the corpus repository and the findings are
properties of a corpus tree, both siblings of this one. So the tool is declared manual in
``test_the_lint_gates_fire_on_a_synthetic_violation.py`` and its behaviour is pinned here, on a
three-file corpus this test writes.

**Both directions, and the second one is the point.** A ratchet that only rejected *new* findings
would go loose every time somebody fixed one without updating the pins: the set would then be
larger than the tree's, and the next commit could reintroduce a finding for free. So closing a
finding fails too, until the pin file is rewritten in the same commit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
RATCHET = ROOT / "tools" / "check_ratchet.py"

#: A metric whose expression calls `DIVIDE`, which parses as SQL and names a function no dialect
#: has -- so it is a V17a finding and not a parse error, which is the class a parse-only check
#: accepts.
_METRIC = """asset_type: metric
id: shop.conversion
name: conversion
base_table: shop.orders
expression: DIVIDE(COUNT(order_id), COUNT(*))
summary: conversion divides the counted orders by the total rows.
body: >-
  The share of shop.orders rows that carry an order id.
"""

_CLEAN_METRIC = """asset_type: metric
id: shop.order_count
name: order_count
base_table: shop.orders
expression: COUNT(order_id)
summary: order_count counts the orders in the shop.orders table.
body: >-
  One per row of shop.orders.
"""

_TABLE = """asset_type: table
id: shop.orders
schema: shop
physical_name: orders
summary: orders holds one row per placed order in the shop schema.
body: >-
  Grain is one order.
columns:
  - name: order_id
    summary: The identifier of this order row.
"""


def _corpus(root: Path, *, with_finding: bool) -> Path:
    (root / "shop" / "tables").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "metrics").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "tables" / "tbl_shop_orders.yaml").write_text(_TABLE, encoding="utf-8")
    (root / "shop" / "metrics" / "metric_clean.yaml").write_text(_CLEAN_METRIC, encoding="utf-8")
    dirty = root / "shop" / "metrics" / "metric_conversion.yaml"
    if with_finding:
        dirty.write_text(_METRIC, encoding="utf-8")
    elif dirty.exists():
        dirty.unlink()
    return root


def _run(corpus: Path, pins: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RATCHET),
            "--corpus-dir",
            str(corpus),
            "--pins",
            str(pins),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )


def test_without_a_pin_file_it_refuses_rather_than_inventing_one(tmp_path: Path) -> None:
    """Exit 2 and not 0. A missing pin file is "nothing is pinned", and treating that as "nothing
    is wrong" would pass a tree with any number of findings."""
    corpus = _corpus(tmp_path / "corpus", with_finding=True)
    result = _run(corpus, tmp_path / "pins.txt")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "--write" in result.stderr


def test_a_pinned_finding_passes(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus", with_finding=True)
    pins = tmp_path / "pins.txt"
    assert _run(corpus, pins, "--write").returncode == 0
    assert "V17a" in pins.read_text(encoding="utf-8")

    result = _run(corpus, pins)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "the ratchet holds" in result.stdout


def test_a_new_finding_fails(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus", with_finding=False)
    pins = tmp_path / "pins.txt"
    assert _run(corpus, pins, "--write").returncode == 0

    _corpus(corpus, with_finding=True)
    result = _run(corpus, pins)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "NEW finding" in result.stderr
    assert "V17a" in result.stderr


def test_closing_a_finding_also_fails_until_the_pins_are_rewritten(tmp_path: Path) -> None:
    """The direction a naive ratchet misses. A fix that leaves the pin file alone leaves the
    ratchet loose by exactly that many findings."""
    corpus = _corpus(tmp_path / "corpus", with_finding=True)
    pins = tmp_path / "pins.txt"
    assert _run(corpus, pins, "--write").returncode == 0

    _corpus(corpus, with_finding=False)
    result = _run(corpus, pins)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "are GONE" in result.stderr

    # And the way out is the way the message says.
    assert _run(corpus, pins, "--write").returncode == 0
    assert _run(corpus, pins).returncode == 0


def test_the_identity_is_the_rule_and_the_asset_and_not_the_message(tmp_path: Path) -> None:
    """A pin survives a reworded finding, because a reworded message is not a new finding.

    Checked by pinning the identity and then asserting the pin file carries no prose from the
    message -- if it did, `--write` would have to run every time a message changed.
    """
    corpus = _corpus(tmp_path / "corpus", with_finding=True)
    pins = tmp_path / "pins.txt"
    assert _run(corpus, pins, "--write").returncode == 0

    lines = [
        line
        for line in pins.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert lines, "nothing was pinned"
    for line in lines:
        rule, _, where = line.partition("\t")
        assert rule.startswith("V"), line
        assert where.count(":") == 1, f"the identity is file:asset, got {where!r}"
        assert "permitted function" not in line, "the message leaked into the identity"
