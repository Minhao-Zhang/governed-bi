"""The exporter's contract, and the two content checks that are fatal only here.

**The end-to-end path was driven against the real corpus and found two defects a unit test could
not have.** Both are recorded here as the tests that would now catch them:

1. ``Path.write_text`` defaults to ``newline=None``, which on Windows turns every line feed into a
   carriage-return pair. That made ``changes.patch``'s separators CRLF, and ``git apply`` —
   comparing against the index, where git stores line feeds — read the stray carriage return as
   content and refused with "patch does not apply". On **every bundle the tool would ever have
   produced**. The defect was in the bytes on disk and in no value the code held.
2. A **truncated** corpus hash in ``base_corpus_content_hash`` never equals the 64-character digest
   the landing check compares it against, so a patch nobody had touched reported ``superseded``. A
   wrong landing state is worse than a missing one: it sends a good change back to the steward with
   nothing in the output to suggest the comparison was at fault.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# `tools/` is a directory of scripts and not a package, so it is put on the path the way
# `tests/conformance/test_corpus_conformance_rules_fire.py` does it. One convention, one place to
# change it if `tools/` ever becomes importable properly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import pytest

from governed_bi.feedback.events import (
    DerivedState,
    Kind,
    Observation,
    ObservationState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.store import (
    FeedbackStore,
    Rejected,
    mint_observation_id,
    mint_patch_id,
    utc_now,
)
from governed_bi.feedback.validate import CONTENT_HASH_CHARS

pytest.importorskip("yaml")

HASH_A = "a" * CONTENT_HASH_CHARS
ASSET = "sales.orders"
WAS = "orders is the transaction table, one row per placed order."

_TABLE = f"""asset_type: table
id: {ASSET}
schema: sales
physical_name: orders
summary: {WAS}
body: >-
  Grain is one order.
"""


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "sales" / "tables").mkdir(parents=True)
    (root / "sales" / "tables" / "tbl_sales_orders.yaml").write_text(_TABLE, encoding="utf-8")
    return root


def _seeded(
    tmp_path: Path,
    *,
    becomes: str,
    question: str = "how much revenue?",
    source: Source = Source.operator,
) -> tuple[FeedbackStore, str]:
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    observation = Observation(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=source,
        kind=Kind.wrong_answer,
        state=ObservationState.open,
        question=question,
        turn_id=None if source is Source.eval else "turn-1",
        external_key="k" if source is Source.eval else None,
        arm="v4" if source is Source.eval else None,
        question_id="q1" if source is Source.eval else None,
    )
    store.file(observation)
    patch = Patch(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.edit_asset,
        state=PatchState.draft,
        namespace="sales",
        asset_id=ASSET,
        field_path="summary",
        was=WAS,
        becomes=becomes,
        base_corpus_content_hash=HASH_A,
    )
    from governed_bi.register.assets import AssetType

    patch = Patch(**{**{f: getattr(patch, f) for f in patch.__slots__}, "asset_type": AssetType.table})
    store.draft(patch, observations=[observation.observation_id])
    return store, patch.patch_id


def _export(tmp_path: Path, store: FeedbackStore, patch_id: str, *, extra: list[str] | None = None) -> int:
    from export_bundle import main

    return main(
        [
            "--patch",
            patch_id,
            "--db",
            str(store.path),
            "--corpus-dir",
            str(_existing_corpus(tmp_path)),
            "--out",
            str(tmp_path / "bundles"),
            *(extra or []),
        ]
    )


def _existing_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    return root if root.exists() else _corpus(tmp_path)


def test_the_patch_file_uses_line_feeds_and_git_accepts_it(tmp_path: Path) -> None:
    """The bug that would have made every bundle unappliable.

    Asserted on the **bytes**, because that is where it lived: both the broken and the fixed
    version are strings Python is perfectly happy with.
    """
    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")

    assert _export(tmp_path, store, patch_id) == 0

    patch_file = tmp_path / "bundles" / f"bnd-{patch_id}" / "changes.patch"
    raw = patch_file.read_bytes()
    assert b"\r" not in raw, "the patch carries a carriage return; git apply will refuse it"
    assert raw.count(b"\n") > 0

    after = tmp_path / "bundles" / f"bnd-{patch_id}" / "after" / "sales/tables/tbl_sales_orders.yaml"
    assert b"\r" not in after.read_bytes()


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is not on PATH",
)
def test_git_apply_accepts_the_diff(tmp_path: Path) -> None:
    """The property the bundle exists for, checked with the tool that will be used on it."""
    root = _corpus(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=root,
        check=True,
    )
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")
    assert _export(tmp_path, store, patch_id) == 0

    check = subprocess.run(
        ["git", "apply", "--check", "-p1", str(tmp_path / "bundles" / f"bnd-{patch_id}" / "changes.patch")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr


def test_the_bundle_carries_the_five_things_the_layout_declares(tmp_path: Path) -> None:
    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")
    assert _export(tmp_path, store, patch_id) == 0

    bundle = tmp_path / "bundles" / f"bnd-{patch_id}"
    for name in ("MANIFEST.yaml", "COMMIT_MSG.txt", "changes.patch"):
        assert (bundle / name).is_file(), name
    assert (bundle / "after").is_dir()
    assert (bundle / "evidence" / "observations.md").is_file()
    assert (bundle / "evidence" / "ladder.json").is_file()


def test_the_commit_message_carries_no_reader_prose(tmp_path: Path) -> None:
    """A sentence somebody typed must not become a line of a commit log that some other tool later
    renders unescaped. It lives in `evidence/observations.md`, inside a fence."""
    _corpus(tmp_path)
    store, patch_id = _seeded(
        tmp_path, becomes=WAS + " Grain is one order.", question="a very distinctive question string"
    )
    assert _export(tmp_path, store, patch_id) == 0

    bundle = tmp_path / "bundles" / f"bnd-{patch_id}"
    message = (bundle / "COMMIT_MSG.txt").read_text(encoding="utf-8")
    assert "a very distinctive question string" not in message
    assert "a very distinctive question string" in (
        bundle / "evidence" / "observations.md"
    ).read_text(encoding="utf-8")
    assert message.splitlines()[0].startswith("Reword ")
    assert len(message.splitlines()[0]) <= 72


def test_the_manifest_omits_a_hash_nobody_can_compute_yet(tmp_path: Path) -> None:
    """The *manifest* omits it, and the reason is the reader, not the computability.

    An engineer reading `MANIFEST.yaml` before applying has nothing to compare a post-state hash
    against, so a hash-shaped string there is noise. It is recorded on the **patch**, where
    `derived_state` reads it -- see
    `test_a_bundle_that_lands_alone_reads_landed_verified`. This docstring used to say
    `check_landed.py` computed it after the commit and that a landing therefore "usually reads
    `landed_matched`"; that file has never contained the symbol, and the state was unreachable.
    """
    import yaml

    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")
    assert _export(tmp_path, store, patch_id) == 0

    manifest = yaml.safe_load(
        (tmp_path / "bundles" / f"bnd-{patch_id}" / "MANIFEST.yaml").read_text(encoding="utf-8")
    )
    assert "expected_corpus_content_hash" not in manifest
    assert manifest["base_corpus_content_hash"] == HASH_A
    assert manifest["file"] == "sales/tables/tbl_sales_orders.yaml", "POSIX separators on the wire"


def test_a_held_out_phrase_in_the_new_text_is_fatal(tmp_path: Path) -> None:
    """The leakage channel the importer opens, closed at the one gate a change has to pass.

    A phrase carried from a graded question into an asset contaminates every EX number measured
    afterwards, and the contamination is invisible. Five consecutive words is the threshold: a
    shorter run is ordinary English, and V12 is about a quotation.
    """
    _corpus(tmp_path)
    question = "what is the average female median age in that county"
    store, patch_id = _seeded(
        tmp_path,
        becomes=WAS + " Use it for the average female median age in that county.",
        question=question,
        source=Source.eval,
    )

    assert _export(tmp_path, store, patch_id) == 1, "a held-out quotation must refuse"
    assert not (tmp_path / "bundles").exists(), "a refused export must write nothing"


def test_a_shared_phrase_that_is_ordinary_english_is_not_fatal(tmp_path: Path) -> None:
    """The check has to be usable. Refusing every asset that shares four words with a question
    would refuse every asset, and a gate that always fires is a gate that gets waived."""
    _corpus(tmp_path)
    store, patch_id = _seeded(
        tmp_path,
        becomes=WAS + " One row per order.",
        question="how many orders were placed in the last month by each customer",
        source=Source.eval,
    )
    assert _export(tmp_path, store, patch_id) == 0


def test_only_an_edit_produces_a_bundle(tmp_path: Path) -> None:
    """An exclusion_request is prose a human transcribes; engine_defect and no_change author
    nothing on purpose. A bundle for either would be an empty diff with a commit message."""
    _corpus(tmp_path)
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    patch = Patch(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.engine_defect,
        state=PatchState.draft,
        namespace="sales",
        rationale="r_star_projection; nothing in the corpus is at fault",
        base_corpus_content_hash=HASH_A,
    )
    store.draft(patch, observations=[])
    assert _export(tmp_path, store, patch.patch_id) == 2


def test_a_red_t0_refuses_the_export(tmp_path: Path) -> None:
    """The gate that was a preference until 2026-08-23.

    The bundle has always carried `evidence/ladder.json`, so a patch whose T1 said the tree stops
    indexing exported anyway with the finding sitting in a file the person applying it had to go
    looking for. A finding somebody has to go looking for is not a gate.
    """
    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")
    store.record_ladder(patch_id, "T0", {"passed": False, "detail": "V3 fires on the new summary"})

    assert _export(tmp_path, store, patch_id) == 1
    assert not (tmp_path / "bundles").exists(), "a refused export must write nothing"


def test_the_red_ladder_can_be_overridden_deliberately(tmp_path: Path) -> None:
    """One person holds every role on this deployment, so a deliberate override has to be possible.
    What must not be possible is overriding it by accident."""
    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")
    store.record_ladder(patch_id, "T1", {"passed": False, "detail": "build_index raises"})

    assert _export(tmp_path, store, patch_id, extra=["--despite-a-red-ladder"]) == 0
    assert (tmp_path / "bundles" / f"bnd-{patch_id}" / "changes.patch").is_file()


def test_a_red_t3_warns_and_does_not_refuse(tmp_path: Path) -> None:
    """T3 says this patch does not fix the complaint it is attached to. That sends it back to the
    steward; it does not mean the edit is wrong. Refusing here would refuse every patch that
    improves an asset without closing one specific coverage miss."""
    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")
    for tier in ("T0", "T1", "T2"):
        store.record_ladder(patch_id, tier, {"passed": True, "detail": "fine"})
    store.record_ladder(patch_id, "T3", {"passed": False, "detail": "still misses a gold table"})

    assert _export(tmp_path, store, patch_id) == 0


def test_an_unrun_ladder_warns_and_does_not_refuse(tmp_path: Path) -> None:
    """The free tiers cost nothing, so there is no argument for handing over a change nobody ran
    them on -- and no finding to refuse on either. Manufacturing one would be the "unrun reads as
    failed" defect the derived states exist to avoid."""
    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")
    assert _export(tmp_path, store, patch_id) == 0


def test_a_value_that_would_not_parse_is_refused_rather_than_shipped(tmp_path: Path) -> None:
    """The exporter shipped unparseable YAML with **exit 0**, and the fact was in the bundle.

    Reproduced by a reviewer: `becomes="revenue for the quarter:"`, no ladder rows, and the tool
    printed the diff, printed two NOTEs, returned 0 and wrote `changes.patch` plus `after/...yaml`.
    `yaml.safe_load` on the shipped file raised. An engineer applies it and the corpus stops
    loading -- after the commit, which is the failure mode the whole module exists to prevent.

    There was no `yaml.safe_load` anywhere in the exporter. The fix is one layer down --
    `corpus/patch.py::apply_edit` re-parses its own output -- and this asserts the exporter reports
    it rather than tracebacking through it.
    """
    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes="orders is the transaction table:")

    assert _export(tmp_path, store, patch_id) == 1
    assert not (tmp_path / "bundles").exists(), "a refused export must write nothing"


def test_a_value_that_would_land_differently_is_refused(tmp_path: Path) -> None:
    """The quieter half: an interior newline in a plain scalar is written as a quoted single line,
    which parses and resolves to the newline turned into a space. So the patch lands a *different*
    value, the landing check cannot match it, and a change that shipped correctly reports
    `superseded`."""
    _corpus(tmp_path)
    store, patch_id = _seeded(
        tmp_path, becomes="orders is the transaction table,\none per order."
    )

    assert _export(tmp_path, store, patch_id) == 1
    assert not (tmp_path / "bundles").exists()


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is not on PATH",
)
def test_git_apply_accepts_a_diff_on_a_file_with_no_trailing_newline(tmp_path: Path) -> None:
    r"""`_unified` never emitted `\ No newline at end of file`, so the `-old` and `+new` lines
    concatenated and `git apply --check` answered **rc 128, corrupt patch at line 7**. `_summarise`
    miscounted it too, as "0 line(s) added, 1 removed".

    Not hypothetical for a corpus a person hand-edits: a file saved without a final newline is
    ordinary, and the resulting bundle is unappliable with a message that blames the patch rather
    than the writer.
    """
    root = _corpus(tmp_path)
    target = root / "sales" / "tables" / "tbl_sales_orders.yaml"
    target.write_bytes(target.read_bytes().rstrip())

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=root,
        check=True,
    )

    store, patch_id = _seeded(tmp_path, becomes=WAS + " One added sentence.")
    assert _export(tmp_path, store, patch_id) == 0

    check = subprocess.run(
        ["git", "apply", "--check", "-p1", str(tmp_path / "bundles" / f"bnd-{patch_id}" / "changes.patch")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, f"git apply refused the diff: {check.stderr}"


def test_a_truncated_corpus_hash_is_refused_at_the_store(tmp_path: Path) -> None:
    """The second defect the end-to-end run found. A 16-character prefix -- what every display
    shows -- never equals the digest the landing check compares against, so the patch reported
    `superseded` while nothing had changed."""
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    from governed_bi.register.assets import AssetType

    patch = Patch(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=PatchIntent.edit_asset,
        state=PatchState.draft,
        namespace="sales",
        asset_type=AssetType.table,
        asset_id=ASSET,
        field_path="summary",
        was=WAS,
        becomes=WAS + " More.",
        base_corpus_content_hash=HASH_A[:16],
    )
    with pytest.raises(Rejected, match="16 characters"):
        store.draft(patch, observations=[])


def test_a_bundle_that_lands_alone_reads_landed_verified(tmp_path: Path) -> None:
    """The state exists, and until the exporter recorded the hash nothing could reach it.

    `DerivedState.landed_verified` is the strong claim -- "the corpus is *exactly* the tree this
    bundle predicted" -- and it is what separates a clean landing from `landed_matched`, which is
    also true when three other bundles arrived in the same week. `derived_state` reads
    `patch.expected_corpus_content_hash` to tell them apart, and no caller set it: the exporter
    omitted it and named `check_landed.py`, which never had the symbol.

    Driven end to end rather than by constructing a `Patch` with the field filled in, because a
    hand-built value is what let the state look covered while having no producer.
    """
    from governed_bi.corpus.hash import corpus_content_hash
    from governed_bi.corpus.patch import apply_edit
    from governed_bi.feedback.lifecycle import derived_state

    root = _corpus(tmp_path)
    becomes = WAS + " Grain is one order."
    store, patch_id = _seeded(tmp_path, becomes=becomes)
    assert _export(tmp_path, store, patch_id) == 0

    patch = store.get_patch(patch_id)
    assert patch is not None
    assert patch.expected_corpus_content_hash, "the exporter must record the tree it predicts"

    # Apply the edit the way `git apply` would: the bytes, LF, nothing else touched.
    target = root / "sales" / "tables" / "tbl_sales_orders.yaml"
    target.write_bytes(
        apply_edit(
            target,
            asset_id=ASSET,
            field_path="summary",
            was=WAS,
            becomes=becomes,
        ).encode("utf-8")
    )

    landed = corpus_content_hash(root)
    assert landed == patch.expected_corpus_content_hash, "predicted before, measured after"
    assert (
        derived_state(patch, loaded_corpus_hash=landed, asset_text_now={}, retrieval_ok=None)
        is DerivedState.landed_verified
    )


def test_a_bundle_that_lands_beside_another_change_reads_landed_matched(tmp_path: Path) -> None:
    """The weaker state, and it must stay reachable: this is the common real case.

    Something else landing in the same week is normal, and a two-state model calls it
    `superseded` -- "handed off, forever" -- for a change that did ship. The expected hash makes
    `landed_verified` the narrow claim rather than replacing this one.
    """
    from governed_bi.feedback.lifecycle import derived_state

    _corpus(tmp_path)
    becomes = WAS + " Grain is one order."
    store, patch_id = _seeded(tmp_path, becomes=becomes)
    assert _export(tmp_path, store, patch_id) == 0
    patch = store.get_patch(patch_id)
    assert patch is not None

    assert (
        derived_state(
            patch,
            loaded_corpus_hash="f" * 64,
            asset_text_now={ASSET: (becomes, "Grain is one order.")},
            retrieval_ok=None,
        )
        is DerivedState.landed_matched
    )
