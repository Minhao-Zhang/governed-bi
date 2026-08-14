"""``arms.toml`` is the committed half of an arm's identity, so it has to be checkable.

Before it existed, ``runs/eval/`` named an arm and nothing a reader could fetch said what the
name meant — the arm's configuration lived in a gitignored ``.env`` on one machine. That is the
"versioned is not rebuildable" problem the corpus has, one level up.

The file is load-bearing rather than documentation because of audit D9: ``knobs_comparable``
refuses a pair that cannot name its treatment, and this is where the name comes from.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from governed_bi.register.arm_profiles import (
    ArmProfile,
    arm_profile,
    load_arm_profiles,
    reconcile,
)
from governed_bi.register.knobs import comparability_keys


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "arms.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_the_repositorys_own_file_parses_and_names_real_knobs() -> None:
    """The shipped file is the one that matters; a loader that only works on fixtures is a
    loader nobody has run."""
    profiles = load_arm_profiles()

    assert "v4" in profiles, "the arm every quoted figure comes from is undeclared"
    for name, profile in profiles.items():
        assert profile.treatment <= comparability_keys(), (
            f"[arm.{name}] names a treatment that is not a comparability knob"
        )


def test_v4s_declared_treatment_is_the_prompt() -> None:
    """Pinned because it is what makes v3-fold → v4 quotable at all.

    If this silently became empty, ``knobs_comparable`` would report ``cannot_evaluate`` on the
    repository's headline comparison, and it would read as a data problem rather than a
    declaration problem.
    """
    assert arm_profile("v4").treatment == frozenset({"prompt_set"})


def test_a_treatment_that_is_not_a_comparability_knob_is_refused(tmp_path: Path) -> None:
    """Refused at load, not at use.

    A typo reads as "no treatment declared" downstream, which ``knobs_comparable`` turns into
    ``cannot_evaluate`` — a verdict that looks like missing data and sends the reader to the
    artifacts instead of to this file.

    Mutation-verified 2026-08-11: deleting the ``unknown`` check in ``_parse_profiles`` turns
    this red.
    """
    path = _write(tmp_path, '''
        [arm.typo]
        treatment = ["prompt_sett"]
    ''')
    with pytest.raises(ValueError, match="prompt_sett"):
        load_arm_profiles(path)


def test_an_operational_knob_is_not_a_valid_treatment(tmp_path: Path) -> None:
    """``git_sha`` moves between almost any two arms and invalidates nothing, by its own role
    definition. Declaring it as *the* treatment would make every pair look intentional."""
    path = _write(tmp_path, '''
        [arm.wrong]
        treatment = ["git_sha"]
    ''')
    with pytest.raises(ValueError, match="git_sha"):
        load_arm_profiles(path)


def test_an_unknown_arm_raises_rather_than_returning_an_empty_treatment() -> None:
    """The dangerous default. An empty profile for a misspelled arm name would degrade a real
    comparison to ``cannot_evaluate`` and say nothing about why."""
    with pytest.raises(KeyError, match="no_such_arm"):
        arm_profile("no_such_arm")


def test_reconcile_catches_an_artifact_labelled_with_the_wrong_corpus() -> None:
    """The point of writing a claim down is that it can be checked against what ran."""
    profile = ArmProfile(
        name="x", description="", treatment=frozenset(), corpus_content_hash="86ed1dbf"
    )

    assert reconcile(profile, {"corpus_content_hash": "86ed1dbf"}) == ()

    problems = reconcile(profile, {"corpus_content_hash": "deadbeef"})
    assert problems and "deadbeef" in problems[0]


def test_reconcile_reads_the_row_and_not_the_knob_mapping() -> None:
    """``corpus_content_hash`` is a ``RecordField``, and ``reconcile`` looked for it in
    ``knobs_resolved``.

    It is never there, so the lookup returned ``None`` and the function returned agreement for
    every artifact ever produced — declared machinery whose one caller was its own tests, and
    which would have said "fine" if it had been called. Asserted from both sides, because a
    reader that fell back to the knob mapping would still satisfy the first half.
    """
    profile = ArmProfile(
        name="x", description="", treatment=frozenset(), corpus_content_hash="86ed1dbf"
    )

    row = {"corpus_content_hash": "deadbeef", "knobs_resolved": {"corpus_content_hash": "86ed1dbf"}}
    assert reconcile(profile, row), "the knob mapping was read instead of the row"

    knob_only = {"knobs_resolved": {"corpus_content_hash": "deadbeef"}}
    assert reconcile(profile, knob_only) == (), (
        "a row that does not name a corpus cannot contradict the profile"
    )


def test_reconcile_compares_the_digest_and_never_the_git_ref() -> None:
    """The two fields are two namespaces, and comparing them is what made this vacuous.

    ``corpus = "30872d3"`` is the corpus repository's git ref; every row records the content
    digest ``86ed1dbf…``. The old check was ``str(recorded).startswith(profile.corpus)``, which
    could not match on any real artifact and therefore never fired.
    """
    git_only = ArmProfile(
        name="x", description="", treatment=frozenset(), corpus="30872d3"
    )
    problems = reconcile(git_only, {"corpus_content_hash": "86ed1dbfef8b"})
    assert problems and "no corpus_content_hash" in problems[0], (
        "a git ref is not a digest, so a profile carrying only one reconciles nothing"
    )

    both = ArmProfile(
        name="x",
        description="",
        treatment=frozenset(),
        corpus="30872d3",
        corpus_content_hash="86ed1dbfef8b",
    )
    assert reconcile(both, {"corpus_content_hash": "86ed1dbfef8b"}) == ()
    assert reconcile(both, {"corpus_content_hash": "30872d3abc"}), (
        "a row carrying the git ref where the digest belongs is a mislabelled artifact"
    )


def test_a_prefix_of_the_declared_digest_is_not_the_declared_digest() -> None:
    """``startswith`` would accept a truncated hash from another corpus that shares eight hex
    characters. Equality, because a content hash is either the one that was measured or it is
    not."""
    profile = ArmProfile(
        name="x", description="", treatment=frozenset(), corpus_content_hash="86ed1dbfef8b325e"
    )
    assert reconcile(profile, {"corpus_content_hash": "86ed1dbf"})


def test_the_shipped_profiles_name_the_digest_their_artifacts_carry() -> None:
    """v4 is the arm every quoted figure comes from, and its artifact records this digest.

    Pinned as a literal so that repointing ``arms.toml`` at a rebuilt corpus without re-running
    the arm is a test failure rather than a silent relabelling. The value is read off
    ``runs/eval/proxy_v4_corpus30872d3.jsonl``, which is gitignored — hence the literal here.
    """
    digest = "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5"
    for name in ("v4", "v5"):
        assert arm_profile(name).corpus_content_hash == digest
        assert arm_profile(name).corpus == "30872d3", "the git ref is kept, and is not the digest"


def test_a_profile_that_cannot_be_reconciled_says_so_instead_of_agreeing() -> None:
    """The third way this function returned agreement without comparing anything.

    It outlived the other two. ``reconcile`` was repaired on 2026-08-11 and ``v3_fold`` — the
    baseline every v4 figure is measured against — declared no ``corpus_content_hash``, so the
    ``is not None`` guard was never entered. A run launched ``--arm v3_fold`` against *any*
    corpus cleared the pre-flight check and was then told, in the report, that every one of its
    1 351 rows agreed with the profile.

    Silence is the wrong answer here because the caller cannot tell it apart from a pass. Both
    locks are asserted: the loader refuses such a file, and this refuses such an object, because
    ``ArmProfile`` is constructible directly and a fixture is where one gets invented.
    """
    profile = ArmProfile(name="x", description="", treatment=frozenset())

    problems = reconcile(profile, {"corpus_content_hash": "anything"})
    assert problems, "an unreconcilable profile must not report agreement"
    assert "no corpus_content_hash" in problems[0]


def test_the_loader_refuses_an_arm_that_declares_no_digest(tmp_path: Path) -> None:
    """The half that stops the *next* arm reintroducing it.

    Making ``reconcile`` loud fixes the arms already declared; refusing the file is what makes
    a new ``[arm.x]`` with no digest a build failure rather than a check with an off switch
    nobody labelled. ``corpus`` alone does not satisfy it — that is the git ref, and comparing
    it against the recorded digest is the original defect.
    """
    path = _write(tmp_path, '''
        [arm.undeclared]
        treatment = ["prompt_set"]
        corpus = "30872d3"
    ''')
    with pytest.raises(ValueError, match="no corpus_content_hash"):
        load_arm_profiles(path)


def test_every_shipped_arm_can_actually_be_reconciled() -> None:
    """Not just v4 and v5. ``v3_fold`` is the arm the control is measured against, and it was
    the one with no digest — so the check that mattered most was the one that ran on nothing."""
    digest = "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5"
    for name in load_arm_profiles():
        profile = arm_profile(name)
        assert profile.corpus_content_hash, f"[arm.{name}] cannot be reconciled"
        assert reconcile(profile, {"corpus_content_hash": digest}) == ()
        assert reconcile(profile, {"corpus_content_hash": "deadbeef"}), (
            f"[arm.{name}] accepts a corpus it did not run on"
        )


def test_a_turn_that_abstained_before_routing_does_not_contradict_the_profile() -> None:
    """open-work 3.6a: a clarification that ends before routing carries no corpus hash at all.

    There are 4 of them on the v4 arm. Reading ``None`` as a contradiction would put four
    identical complaints into every report of a correct run.
    """
    profile = ArmProfile(
        name="x", description="", treatment=frozenset(), corpus_content_hash="86ed1dbf"
    )
    assert reconcile(profile, {"corpus_content_hash": None, "outcome": "clarification"}) == ()
