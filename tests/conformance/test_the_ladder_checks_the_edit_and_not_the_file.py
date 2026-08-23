"""``tools/verify_patch.py`` on a synthetic corpus: T0–T2 must catch a regression and pass a fix.

**The property that makes this worth testing is that every tier is a DELTA gate.** A corpus with
101 pre-existing findings is the normal case, so a tier asserting "zero findings" would refuse every
patch, get waived, and a waiver is how a real finding goes green. So each test below puts a finding
in the corpus *before* the patch and checks the tier still passes an unrelated edit.

**And the edit is never on disk.** The whole point of ``corpus/patch.py`` is that it returns text; a
ladder that wrote the file to check it would be the write this design exists to keep out of the
engine's hands. That is asserted directly: the file's bytes are compared before and after a run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
LADDER = ROOT / "tools" / "verify_patch.py"

WAS = "orders holds one row per placed order in the shop schema."
BECOMES = "orders holds one row per placed order. Questions about a purchase read this table."

_TABLE = f"""asset_type: table
id: shop.orders
schema: shop
physical_name: orders
summary: {WAS}
body: >-
  Grain is one order.
columns:
  - physical_name: order_id
    summary: order_id identifies one row of the orders table.
    body: >-
      One value per order, unique.
"""

#: A metric that is already broken, so the tree has standing debt for the delta gates to ignore.
_DIRTY_METRIC = """asset_type: metric
id: shop.conversion
name: conversion
base_table: shop.orders
expression: DIVIDE(COUNT(order_id), COUNT(*))
summary: conversion divides the counted orders by the total rows.
body: >-
  The share of shop.orders rows that carry an order id.
"""


def _corpus(root: Path) -> Path:
    (root / "shop" / "tables").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "metrics").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "tables" / "tbl_shop_orders.yaml").write_text(_TABLE, encoding="utf-8")
    (root / "shop" / "metrics" / "metric_conversion.yaml").write_text(
        _DIRTY_METRIC, encoding="utf-8"
    )
    return root


def _patch(tmp_path: Path, *, becomes: str, was: str = WAS, field: str = "summary") -> tuple[Path, str]:
    """A drafted patch in a scratch store, and its id."""
    from governed_bi.feedback.events import Patch, PatchIntent, PatchState, Source
    from governed_bi.feedback.store import FeedbackStore, mint_patch_id, utc_now
    from governed_bi.feedback.validate import CONTENT_HASH_CHARS
    from governed_bi.register.assets import AssetType

    db = tmp_path / "feedback.sqlite"
    store = FeedbackStore(db)
    patch = Patch(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.edit_asset,
        state=PatchState.draft,
        namespace="shop",
        asset_type=AssetType.table,
        asset_id="shop.orders",
        field_path=field,
        was=was,
        becomes=becomes,
        base_corpus_content_hash="e" * CONTENT_HASH_CHARS,
    )
    store.draft(patch, observations=[])
    return db, patch.patch_id


def _run(corpus: Path, db: Path, patch_id: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(LADDER),
            "--patch",
            patch_id,
            "--db",
            str(db),
            "--corpus-dir",
            str(corpus),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )


def test_a_clean_edit_passes_all_three_tiers_over_a_corpus_that_already_has_findings(
    tmp_path: Path,
) -> None:
    """The delta property, stated as the first test because it is the one that decides whether the
    ladder is usable at all. The tree carries a V17a finding throughout."""
    corpus = _corpus(tmp_path / "corpus")
    db, patch_id = _patch(tmp_path, becomes=BECOMES)

    result = _run(corpus, db, patch_id, "--no-record")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "T0  pass" in result.stdout
    assert "T1  pass" in result.stdout
    assert "T2  pass" in result.stdout


def test_the_ladder_writes_nothing_to_the_corpus(tmp_path: Path) -> None:
    """Compared on the **bytes**, because that is where a write would be. `apply_edit` returns
    text and the ladder substitutes it in memory; a version that staged the file to check it would
    put the engine back in the business of writing the corpus."""
    corpus = _corpus(tmp_path / "corpus")
    target = corpus / "shop" / "tables" / "tbl_shop_orders.yaml"
    before = target.read_bytes()
    db, patch_id = _patch(tmp_path, becomes=BECOMES)

    assert _run(corpus, db, patch_id, "--no-record").returncode == 0
    assert target.read_bytes() == before, "the ladder edited the corpus"


def test_t0_fails_when_the_edit_breaks_a_local_rule(tmp_path: Path) -> None:
    """A summary that drops the identifier the register requires (V3). The edit is well-formed
    YAML and would load, so nothing later in the ladder would notice."""
    corpus = _corpus(tmp_path / "corpus")
    db, patch_id = _patch(
        tmp_path, becomes="This table is about the things people bought from the shop."
    )

    result = _run(corpus, db, patch_id, "--no-record")
    assert result.returncode == 1, result.stdout
    assert "T0  FAIL" in result.stdout
    assert "V3" in result.stdout


def test_t1_fails_when_the_edit_empties_a_summary_the_index_needs(tmp_path: Path) -> None:
    """`build_index` refuses a blank summary, and a blank one also trips V1 -- so this checks the
    ladder stops at the first tier that can see the problem rather than reporting all three."""
    corpus = _corpus(tmp_path / "corpus")
    db, patch_id = _patch(tmp_path, becomes="   ")

    result = _run(corpus, db, patch_id, "--no-record")
    assert result.returncode == 1, result.stdout
    assert "T1  pass" not in result.stdout


def test_a_stale_was_stops_the_ladder_before_the_first_tier(tmp_path: Path) -> None:
    """The refusal that makes every tier's answer meaningful: if `was` no longer matches, the text
    being verified is not the text that would land."""
    corpus = _corpus(tmp_path / "corpus")
    db, patch_id = _patch(tmp_path, becomes=BECOMES, was="something nobody wrote")

    result = _run(corpus, db, patch_id, "--no-record")
    assert result.returncode == 1
    assert "T0 fails before it starts" in result.stderr


def test_an_unrun_tier_is_absent_rather_than_recorded_as_passing(tmp_path: Path) -> None:
    """A tier that could not run must not read as a tier that ran. The review surface renders
    whatever tiers are in the ladder, so an entry saying `skipped: true` would show up as a green
    row to anybody skimming."""
    from governed_bi.feedback.store import FeedbackStore

    corpus = _corpus(tmp_path / "corpus")
    db, patch_id = _patch(tmp_path, becomes=BECOMES)

    assert _run(corpus, db, patch_id, "--tier", "T0").returncode == 0
    ladder = dict(FeedbackStore(db).get_patch(patch_id).ladder)  # type: ignore[union-attr]
    assert list(ladder) == ["T0"], ladder
    assert ladder["T0"]["passed"] is True


def test_the_result_is_recorded_on_the_patch_for_the_surface_to_render(tmp_path: Path) -> None:
    from governed_bi.feedback.store import FeedbackStore

    corpus = _corpus(tmp_path / "corpus")
    db, patch_id = _patch(tmp_path, becomes=BECOMES)
    assert _run(corpus, db, patch_id).returncode == 0

    ladder = dict(FeedbackStore(db).get_patch(patch_id).ladder)  # type: ignore[union-attr]
    assert sorted(ladder) == ["T0", "T1", "T2"]
    assert all(entry["passed"] for entry in ladder.values())
    assert all("detail" in entry for entry in ladder.values()), (
        "a bare pass/fail is not evidence; the bundle carries these verbatim"
    )


def test_a_failing_tier_is_recorded_too(tmp_path: Path) -> None:
    """A red result is a measurement and belongs on the row. Recording only passes would make the
    ladder look unrun on exactly the patches somebody needs to come back to."""
    from governed_bi.feedback.store import FeedbackStore

    corpus = _corpus(tmp_path / "corpus")
    db, patch_id = _patch(tmp_path, becomes="This table is about purchases people made.")
    assert _run(corpus, db, patch_id).returncode == 1

    ladder = dict(FeedbackStore(db).get_patch(patch_id).ladder)  # type: ignore[union-attr]
    assert ladder["T0"]["passed"] is False
    assert ladder["T0"]["new_findings"], "the finding itself must be recorded, not just the verdict"


def test_a_patch_that_authors_nothing_is_refused_rather_than_passed(tmp_path: Path) -> None:
    """`engine_defect` and `no_change` author no asset on purpose, and `exclusion_request` is prose
    a human transcribes. Reporting "all tiers pass" on one would be a green light on a change the
    ladder never looked at."""
    from governed_bi.feedback.events import Patch, PatchIntent, PatchState, Source
    from governed_bi.feedback.store import FeedbackStore, mint_patch_id, utc_now
    from governed_bi.feedback.validate import CONTENT_HASH_CHARS

    corpus = _corpus(tmp_path / "corpus")
    db = tmp_path / "feedback.sqlite"
    store = FeedbackStore(db)
    patch = Patch(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.engine_defect,
        state=PatchState.draft,
        namespace="shop",
        rationale="nothing in the corpus is at fault",
        base_corpus_content_hash="e" * CONTENT_HASH_CHARS,
    )
    store.draft(patch, observations=[])

    result = _run(corpus, db, patch.patch_id, "--no-record")
    assert result.returncode == 2
    assert "author no asset" in result.stderr
