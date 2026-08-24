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

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
RATCHET = ROOT / "tools" / "check_ratchet.py"
CONFORMANCE = ROOT / "tools" / "check_corpus_conformance.py"

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


# ─────────────────────────────────────────────────────────────────────────────
# Three holes in the identity, each one a way the set grows without growing.
# ─────────────────────────────────────────────────────────────────────────────

#: Two `DIVIDE` calls, so V17a reports **two** findings on one asset. The ratchet keys on
#: `(rule, file:asset)`, so both collapse to the same pin -- which the tool documents as
#: deliberate in the *closing* direction. This fixture is the *opening* direction.
_METRIC_TWO_CALLS = """asset_type: metric
id: shop.conversion
name: conversion
base_table: shop.orders
expression: DIVIDE(DIVIDE(COUNT(order_id), COUNT(*)), COUNT(*))
summary: conversion divides the counted orders by the total rows.
body: >-
  The share of shop.orders rows that carry an order id.
"""

#: The same asset id in two files. V23's own docstring says this "loads with zero problems and
#: then raises ``ValueError: duplicate index id`` in ``build_index`` -- **after** the commit",
#: which is the whole reason `corpus/patch.py` exists.
_DUPLICATE = """asset_type: metric
id: shop.order_count
name: order_count
base_table: shop.orders
expression: COUNT(order_id)
summary: order_count counts the orders in the shop.orders table, declared a second time.
body: >-
  One per row of shop.orders, from a second file carrying the same id.
"""


#: Two columns, neither carrying `physical_name`, each thin enough to trip V3, V4 and V6.
_TABLE_TWO_THIN_COLUMNS = """asset_type: table
id: shop.orders
schema: shop
physical_name: orders
summary: orders holds one row per placed order in the shop schema.
body: >-
  Grain is one order.
columns:
  - name: order_id
    summary: nothing useful here
  - name: buyer_id
    summary: also nothing useful
"""


def test_a_second_violation_on_a_pinned_asset_does_not_pass_for_free(tmp_path: Path) -> None:
    """One `DIVIDE` becomes two, and the build must not stay green.

    The tool documents the collapse honestly in one direction -- "fixing one of the two closes
    nothing" -- and says nothing about the other. The other is the one that matters: the ratchet's
    whole claim is that the finding set *may not grow*, and an asset that is already pinned can
    take on any number of further violations of the same rule without the set growing by one line.
    """
    corpus = _corpus(tmp_path / "corpus", with_finding=True)
    pins = tmp_path / "pins.txt"
    assert _run(corpus, pins, "--write").returncode == 0

    (corpus / "shop" / "metrics" / "metric_conversion.yaml").write_text(
        _METRIC_TWO_CALLS, encoding="utf-8"
    )
    done = _run(corpus, pins)
    assert done.returncode != 0, (
        "a second V17a finding appeared on an already-pinned asset and the ratchet passed:\n"
        f"{done.stdout}\n{done.stderr}"
    )


def test_a_duplicate_asset_id_gets_an_identity_the_ratchet_can_key_on(tmp_path: Path) -> None:
    """V23 fires and the finding must reach the JSON with a ``file:asset`` identity.

    V23's line is ``{asset_id}: declared in N files -- {paths}``. ``_where_of`` takes the first two
    of ``split(":", 2)`` and drops the finding when there is no third field, so the identity depends
    on whether a **path in the message** happens to contain a colon:

    * POSIX paths do not, so the finding is dropped and the ratchet is blind -- on Linux, which is
      where CI runs.
    * Windows paths start ``C:``, so the finding survives with the identity
      ``"shop.order_count: declared in 2 files -- C"`` -- which embeds the *count*, so adding a
      third duplicate file reports one closure and one new finding for the same defect.

    Asserted on the JSON rather than on the exit code, because a duplicate file also trips other
    rules and an exit code cannot tell which rule refused.
    """
    corpus = _corpus(tmp_path / "corpus", with_finding=False)
    (corpus / "shop" / "metrics" / "metric_dupe.yaml").write_text(_DUPLICATE, encoding="utf-8")

    done = subprocess.run(
        [sys.executable, str(CONFORMANCE), "--corpus-dir", str(corpus), "--json"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )
    findings = json.loads(done.stdout)["findings"]
    v23 = [f for f in findings if f["rule"] == "V23"]
    assert v23, (
        "two files declare shop.order_count and no V23 finding reached the JSON, so the ratchet "
        "cannot see the one failure mode that lands after the commit"
    )
    where = v23[0]["where"]
    assert where.endswith(":shop.order_count"), (
        f"V23's identity is {where!r}. It must name the asset, and it must not carry the number "
        "of duplicate files -- a third duplicate would then read as a closure plus a new finding."
    )


def test_two_columns_in_one_table_are_two_identities(tmp_path: Path) -> None:
    """A column with no ``physical_name`` is named ``file.yaml:column``, and so is every other one.

    Measured on a two-column fixture: five findings collapse to three pins, because both columns
    report as ``t.yaml:column`` under V3 and both again under V6. ``_where``'s docstring names half
    of this -- "the writer cannot tell which one failed" -- and stops at the reporting cost. The
    other half is the gate: fixing ``order_id`` while breaking ``buyer_id`` moves no line in the pin
    file, so the ratchet reports a hold on a tree that changed.

    This is the same shape as two ``DIVIDE`` calls in one expression, which the tool documents as
    deliberate. It is not defensible here: a column *has* a stable name to key on.
    """
    corpus = tmp_path / "corpus"
    (corpus / "shop" / "tables").mkdir(parents=True)
    (corpus / "shop" / "tables" / "t.yaml").write_text(
        _TABLE_TWO_THIN_COLUMNS, encoding="utf-8"
    )
    done = subprocess.run(
        [sys.executable, str(CONFORMANCE), "--corpus-dir", str(corpus), "--json"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )
    findings = json.loads(done.stdout)["findings"]
    wheres = {f["where"] for f in findings}
    assert "t.yaml:column" not in wheres, (
        "a finding is keyed on the *kind* of the asset and not the asset: "
        f"{sorted(wheres)}. Two columns then share one pin."
    )
    v3 = {f["where"] for f in findings if f["rule"] == "V3"}
    assert len(v3) == 2, f"two columns, two V3 findings, and {len(v3)} identit(ies): {sorted(v3)}"


def test_a_finding_that_cannot_be_keyed_is_not_dropped(tmp_path: Path) -> None:
    """``_where_of`` returning ``""`` must not mean "leave it out of the report".

    The helper's docstring argues that "an identity the ratchet cannot key on is worse than a
    missing finding". That has it backwards: a missing finding is a **blind gate**, and blind is
    worse than noisy. A rule that emits a line the reporter cannot key on is a bug in that rule,
    and the reporter must say so rather than quietly shrinking the finding set.
    """
    conformance = _load_conformance()
    assert conformance._where_of("no-colon-at-all") == ""
    with pytest.raises(ValueError, match="cannot be keyed|unkeyable"):
        conformance._keyed([("V23", "no-colon-at-all")])


def _load_conformance():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_cc", CONFORMANCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module




def test_write_creates_the_directory_it_was_given(tmp_path: Path) -> None:
    """`--write` must not require somebody to have made `.conformance/` first.

    The default pin path moved into a directory the tool excludes from
    ``corpus_content_hash`` -- a lint's state may sit beside a corpus without entering its
    treatment identity. But `write_text` does not create parents, so the first `--write` against a
    fresh clone raised `FileNotFoundError` from inside a tool whose whole job is to be run on a
    tree nobody has set up yet.
    """
    corpus = _corpus(tmp_path / "corpus", with_finding=True)
    pins = tmp_path / "corpus" / ".conformance" / "pins.txt"
    assert not pins.parent.exists()

    done = _run(corpus, pins, "--write")
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    assert pins.is_file(), "the pin file was not written"
    assert _run(corpus, pins).returncode == 0, "and the ratchet holds against what it just wrote"
