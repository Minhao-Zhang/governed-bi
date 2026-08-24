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

**The two content checks are the conformance rules, called.** They used to be re-implemented in
``export_bundle.py`` -- a regex over ``for_analyst(...).excluded_columns`` and a "five shared words"
phrase matcher -- and on six inputs measured 2026-08-24 the copies and the rules disagreed on four,
in both directions. Every case is a test below, and each one names which implementation answered
what.
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


BODY = "Grain is one order."

#: The same table with one ``governance.excluded`` column. ``physical_name`` and no ``name``,
#: because ``ColumnAsset`` refuses the second -- a fixture the loader rejects makes ``for_analyst``
#: return an empty excluded set, and then *every* V19 assertion passes for the wrong reason. It did,
#: on the first draft of these tests.
def _table_with_an_excluded_column(name: str, *, body: str = BODY) -> str:
    return f"""asset_type: table
id: {ASSET}
schema: sales
physical_name: orders
summary: {WAS}
body: >-
  {body}
columns:
  - physical_name: {name}
    summary: the customer social security number, {ASSET}.{name}
    body: the taxpayer identifier, one per customer.
    governance:
      excluded: true
      reason: PII
      by: human
"""


def _corpus(tmp_path: Path, *, table: str = _TABLE) -> Path:
    root = tmp_path / "corpus"
    (root / "sales" / "tables").mkdir(parents=True)
    (root / "sales" / "tables" / "tbl_sales_orders.yaml").write_text(table, encoding="utf-8")
    return root


def _split(tmp_path: Path, *questions: str) -> Path:
    """A held-out split in the shape ``check_split_leak`` reads. ``../BIRD-Data-Obfuscation`` is
    read-only and is not a fixture, so the gate is driven against a file written here."""
    import json

    path = tmp_path / "test_final.jsonl"
    path.write_text(
        "".join(
            json.dumps({"question_id": f"h{i}", "question": q}) + "\n"
            for i, q in enumerate(questions)
        ),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _seeded(
    tmp_path: Path,
    *,
    becomes: str,
    question: str = "how much revenue?",
    source: Source = Source.operator,
    field: str = "summary",
    was: str = WAS,
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
        field_path=field,
        was=was,
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


HELD_OUT = "what is the average female median age in that county"


def test_a_held_out_question_quoted_in_the_new_text_is_fatal(tmp_path: Path) -> None:
    """The leakage channel the importer opens, closed at the one gate a change has to pass.

    A question carried from the graded split into an asset contaminates every EX number measured
    afterwards, and the contamination is invisible. The gate is conformance rule V12 --
    ``check_split_leak`` -- **called**, so the exporter cannot answer differently from the
    corpus-wide report.
    """
    _corpus(tmp_path)
    store, patch_id = _seeded(
        tmp_path,
        becomes=f"{BODY} It answers {HELD_OUT}.",
        question=HELD_OUT,
        source=Source.eval,
        field="body",
        was=BODY,
    )

    assert _export(tmp_path, store, patch_id) == 1, "a held-out quotation must refuse"
    assert not (tmp_path / "bundles").exists(), "a refused export must write nothing"


def test_a_held_out_question_this_patch_does_not_carry_is_fatal(tmp_path: Path) -> None:
    """The disagreement that made the copy worth deleting, measured 2026-08-24.

    The inline copy compared the new text against **the questions of this patch's own
    observations**, on the argument that the steward read those and nothing else. A steward reads
    the review surface; they can also have read a question elsewhere, and the corpus-wide V12 reads
    the whole split. So the copy exported a body quoting a graded question verbatim -- exit 0,
    bundle written -- while ``check_split_leak`` refused the same text.
    """
    _corpus(tmp_path)
    store, patch_id = _seeded(
        tmp_path,
        becomes=f"{BODY} It answers {HELD_OUT}.",
        question="an unrelated operator complaint",
        field="body",
        was=BODY,
    )

    split = _split(tmp_path, HELD_OUT)
    assert _export(tmp_path, store, patch_id, extra=["--test-split", str(split)]) == 1
    assert not (tmp_path / "bundles").exists()


def test_a_partial_overlap_is_not_fatal_because_the_rule_is_about_a_quotation(
    tmp_path: Path,
) -> None:
    """The other direction of the same disagreement, and the sensitivity this fix gives up.

    The copy refused on **five consecutive shared words**. V12 asks whether an asset *quotes* a
    held-out question: the whole question, normalised, as a substring. So this text -- eight words
    of a graded question, not the question -- used to refuse here and passes the corpus-wide gate,
    which is the two-answers problem in one input. One implementation, and the implementation is the
    rule's. If a five-word run should be fatal, that belongs in ``check_split_leak`` and applies to
    the 13,281 assets already in the corpus, not to this caller alone.
    """
    _corpus(tmp_path)
    store, patch_id = _seeded(
        tmp_path,
        becomes=WAS + " Use it for the average female median age in that county.",
        question=HELD_OUT,
        source=Source.eval,
    )
    assert _export(tmp_path, store, patch_id) == 0


def test_an_innocent_edit_passes_both_gates(tmp_path: Path) -> None:
    """The gate has to be usable, and this is the shape of nearly every real patch: a sentence
    about the grain, on a corpus where nothing is excluded and no held-out question is quoted. A
    gate that fires on this is a gate that gets waived."""
    _corpus(tmp_path)
    store, patch_id = _seeded(
        tmp_path,
        becomes=WAS + " One row per order.",
        question="how many orders were placed in the last month by each customer",
        source=Source.eval,
    )
    assert _export(tmp_path, store, patch_id) == 0


def test_the_leakage_gate_says_when_it_could_not_be_asked(tmp_path: Path, capsys) -> None:
    """A rule silently absent from a loop is indistinguishable from a rule that passed.

    V12 needs held-out question text. With no eval-sourced observation on the patch and no split
    file on disk there is none, and the honest answer is *not evaluated* with the path that was
    looked at -- which a person fixes by passing ``--test-split``. Refusing instead would refuse
    every export on a corpus that is not a benchmark.
    """
    _corpus(tmp_path)
    store, patch_id = _seeded(tmp_path, becomes=WAS + " Grain is one order.")

    assert _export(tmp_path, store, patch_id, extra=["--test-split", str(tmp_path / "gone")]) == 0
    printed = capsys.readouterr().out
    assert "V12" in printed and "not evaluated" in printed, printed


def test_an_excluded_column_is_fatal_even_when_its_name_is_not_lowercase(tmp_path: Path) -> None:
    """The V19 disagreement, measured 2026-08-24: the rule refused this body, the copy exported it.

    The copy read ``for_analyst(...).excluded_columns``, whose keys come from ``column_key_for`` --
    ``slug(physical_name).lower()``. It then matched them with a **case-sensitive** ``\\b`` regex, so
    an excluded column called ``SSN`` was searched for as ``ssn`` and never found. Every excluded
    column whose name is not already lowercase was invisible to the gate; ``check_excluded_not_named``
    keys on the name as written and catches it.
    """
    _corpus(tmp_path, table=_table_with_an_excluded_column("SSN"))
    store, patch_id = _seeded(
        tmp_path,
        becomes=f"{BODY} Do not join on SSN.",
        field="body",
        was=BODY,
    )

    assert _export(tmp_path, store, patch_id) == 1
    assert not (tmp_path / "bundles").exists()


def test_an_excluded_name_in_a_summary_is_not_fatal_because_a_summary_is_not_prompt_text(
    tmp_path: Path,
) -> None:
    """The second sensitivity change, and the docstring it falsified.

    The copy scanned ``becomes`` whatever field it was going to, and said in prose that "``summary``
    reaches the retrieval index, so the name would leak". V19 is a **disclosure** rule and
    ``model_visible_text`` is what answers "does the model see this": ``body``, plus a bodyless
    few-shot's ``summary`` and ``sql``. A table's summary reaches the index and no prompt, so a name
    in it is a routing signal, not a disclosure. Two documents disagreed about this and the rule's
    is the one that runs on the whole corpus.
    """
    _corpus(tmp_path, table=_table_with_an_excluded_column("ssn"))
    store, patch_id = _seeded(tmp_path, becomes=WAS + " It excludes ssn.")

    assert _export(tmp_path, store, patch_id) == 0


def test_a_finding_the_edit_did_not_introduce_does_not_refuse_the_bundle(tmp_path: Path) -> None:
    """The exporter-specific half, kept: this gate is about **the value being introduced**.

    ``../BIRD-corpus`` carries 125 conformance findings on 101 pinned identities, so an absolute
    gate here would refuse production, get waived, and a waiver is how a real finding goes green.
    The body below already names the excluded column; the patch touches the summary. Refusing that
    would refuse an unrelated improvement for a disclosure the steward did not write.
    """
    _corpus(tmp_path, table=_table_with_an_excluded_column("ssn", body="Grain is one order per ssn."))
    store, patch_id = _seeded(tmp_path, becomes=WAS + " One row per placed order, always.")

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
