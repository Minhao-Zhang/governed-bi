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
    profile = ArmProfile(name="x", description="", treatment=frozenset(), corpus="30872d3")

    assert reconcile(profile, {"corpus_content_hash": "30872d3abc"}) == ()

    problems = reconcile(profile, {"corpus_content_hash": "deadbeef"})
    assert problems and "deadbeef" in problems[0]


def test_reconcile_is_silent_about_what_the_profile_does_not_claim() -> None:
    """A profile with no ``corpus`` asserts nothing about the corpus, and a gate that invented
    an assertion would refuse arms for a rule nobody wrote."""
    profile = ArmProfile(name="x", description="", treatment=frozenset())

    assert reconcile(profile, {"corpus_content_hash": "anything"}) == ()
