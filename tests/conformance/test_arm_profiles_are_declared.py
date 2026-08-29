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

ROOT = Path(__file__).resolve().parent.parent.parent

#: The three shipped arms' question set, in the ``question_subset`` knob's format. Every fixture
#: below declares one because :func:`reconcile`'s second lock refuses a profile without one —
#: and a fixture is exactly where the *last* unreconcilable profile was invented, so the fixtures
#: paying the same price as the shipped file is the mechanism working, not friction.
SUBSET = "1351:423a3f4b65fb"


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
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
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
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
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
        name="x",
        description="",
        treatment=frozenset(),
        corpus="30872d3",
        question_subset=SUBSET,
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
        question_subset=SUBSET,
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
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbfef8b325e",
        question_subset=SUBSET,
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
    the one with no digest — so the check that mattered most was the one that ran on nothing.

    **Each arm against its OWN declared digest**, which the literal this used to hold could not
    express. It worked while every arm shared ``86ed1dbf…``; the three arms of
    ``docs/two-planes.md`` §9 run on ``74ff80c`` and declare ``6e5c7b4b…``, and a shared literal
    would have made adding them look like a defect in them rather than in this loop. What the
    test is about is the round trip — a profile's claim is a thing a row can be found to agree
    with and a thing another row can be found to contradict — and that is per-arm either way.
    """
    for name in load_arm_profiles():
        profile = arm_profile(name)
        assert profile.corpus_content_hash, f"[arm.{name}] cannot be reconciled"
        assert reconcile(profile, {"corpus_content_hash": profile.corpus_content_hash}) == ()
        assert reconcile(profile, {"corpus_content_hash": "deadbeef"}), (
            f"[arm.{name}] accepts a corpus it did not run on"
        )


def test_a_turn_that_abstained_before_routing_does_not_contradict_the_profile() -> None:
    """open-work 3.6a: a clarification that ends before routing carries no corpus hash at all.

    There are 4 of them on the v4 arm. Reading ``None`` as a contradiction would put four
    identical complaints into every report of a correct run.
    """
    profile = ArmProfile(
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )
    assert reconcile(profile, {"corpus_content_hash": None, "outcome": "clarification"}) == ()


# ── the question set: the same rule, one field over ───────────────────────────


def test_the_loader_refuses_an_arm_that_declares_no_question_set(tmp_path: Path) -> None:
    """The hole `corpus_content_hash` closed for the corpus and not for the questions.

    Found 2026-08-14 by a downstream fork that needed to know which questions the three
    published arms ran, and had to recover it by filtering four historical versions of
    `BIRD-Data-Obfuscation:eval_dataset/test_final.jsonl` against the 57 schemas
    `BIRD-corpus@30872d3` covers. Until this field existed, a rerun on a replaced dataset
    produced the same n = 1 351, a substantially different population, and passed every
    quotability gate -- because the gates compare the corpus digest and the knobs, and both
    matched. `corpus` alone does not satisfy it and neither does `dataset`: both are git refs,
    and comparing a git ref against a recorded digest is the original defect.
    """
    path = _write(tmp_path, '''
        [arm.no_questions]
        treatment = ["prompt_set"]
        corpus = "30872d3"
        corpus_content_hash = "86ed1dbf"
        dataset = "22fe2a6"
    ''')
    with pytest.raises(ValueError, match="no question_subset"):
        load_arm_profiles(path)


def test_reconcile_catches_an_arm_rerun_on_a_replaced_dataset() -> None:
    """**The defect this field exists for**, as a comparison that now fails.

    Same corpus digest, same knobs, same n -- and a different question population. That pair
    was indistinguishable from a replicate, which is what makes it worse than an obviously
    broken run.
    """
    profile = ArmProfile(
        name="v4",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )
    same_corpus_other_questions = {
        "corpus_content_hash": "86ed1dbf",
        "knobs_resolved": {"question_subset": "1351:0000deadbeef"},
    }

    problems = reconcile(profile, same_corpus_other_questions)

    assert problems and "1351:0000deadbeef" in problems[0]
    assert "question_subset" in problems[0]


def test_reconcile_reads_the_question_set_out_of_the_knob_mapping_where_it_lives() -> None:
    """The mirror image of ``test_reconcile_reads_the_row_and_not_the_knob_mapping``, and it
    has to be, because the two fields live in different places.

    ``corpus_content_hash`` is a ``RecordField`` and sits at the top of the row;
    ``question_subset`` is a ``Role.scope`` knob written by
    ``eval/provenance.py::scope_identity`` and sits in ``knobs_resolved`` -- observed in
    ``runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl``, which records
    ``1351:423a3f4b65fb`` there and nothing at the top level. The 2026-08-11 defect was reading
    a field where it never is, not reading the knob mapping as such; a reader who "corrects"
    this branch to match the corpus branch recreates that defect pointing the other way, and
    this test is what says so.
    """
    profile = ArmProfile(
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )
    row = {"corpus_content_hash": "86ed1dbf", "knobs_resolved": {"question_subset": "9:aaaa"}}
    assert reconcile(profile, row), "the knob mapping was not read"

    agreeing = {"corpus_content_hash": "86ed1dbf", "knobs_resolved": {"question_subset": SUBSET}}
    assert reconcile(profile, agreeing) == ()

    # The bare-mapping fallback, which is what lets the driver ask the same question of a
    # session that has no knob mapping yet.
    assert reconcile(profile, {"corpus_content_hash": "86ed1dbf", "question_subset": "9:aaaa"})

    silent = {"corpus_content_hash": "86ed1dbf"}
    assert reconcile(profile, silent) == (), (
        "a row that names no question set cannot contradict the profile -- the seven proxy_* "
        "artifacts predate the writer and refusing per row would strand all of them"
    )


def test_a_profile_with_no_question_set_says_so_instead_of_agreeing() -> None:
    """The second lock, on the new field. ``ArmProfile`` is constructible directly and a
    fixture is where the last unreconcilable profile came from."""
    profile = ArmProfile(
        name="x", description="", treatment=frozenset(), corpus_content_hash="86ed1dbf"
    )

    problems = reconcile(profile, {"corpus_content_hash": "86ed1dbf"})

    assert problems and "no question_subset" in problems[0]


def test_the_shipped_profiles_name_the_question_set_their_artifacts_carry() -> None:
    """Measured, not reconstructed, and pinned as a literal so a silent relabelling fails here.

    Read on 2026-08-20 off the artifacts in ``runs/eval/``, which are gitignored -- hence the
    literal. The 1 351 ``question_id`` values in each of ``proxy_v3_fold_...``, ``proxy_v4_...``
    and ``proxy_v5_...`` are set-equal to the 1 351 in
    ``BIRD-Data-Obfuscation@22fe2a6:eval_dataset/test_final.jsonl`` -- zero extra, zero missing,
    covering exactly the 57 schemas ``BIRD-corpus@30872d3`` holds. Cross-checked against a value
    the harness produced for itself: ``live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl``
    records ``question_subset = "1351:423a3f4b65fb"``.
    """
    for name in ("v3_fold", "v4", "v5"):
        assert arm_profile(name).question_subset == SUBSET
        assert arm_profile(name).dataset == "22fe2a6", "the git ref is kept, and is not the digest"


def test_every_shipped_arm_declares_a_question_set() -> None:
    """``v3_fold`` is the arm that had no corpus digest while the check said it was fine. The
    same omission on the same arm is what this asserts cannot recur one field over."""
    for name in load_arm_profiles():
        profile = arm_profile(name)
        assert profile.question_subset, f"[arm.{name}] cannot have its question set reconciled"
        assert reconcile(
            profile, {"knobs_resolved": {"question_subset": "1351:0000deadbeef"}}
        ), f"[arm.{name}] accepts a question set it did not run"


def test_the_declared_question_set_is_the_dataset_commit_it_names() -> None:
    """The literal above, checked against the repository it claims to come from.

    Skipped where ``../BIRD-Data-Obfuscation`` is absent, which is the same precondition that
    keeps ``tools/check_corpus_conformance.py`` out of CI -- so this proves the pin only on a
    machine that holds the dataset. That is still worth having: ``SUBSET`` and ``dataset`` are
    two claims about one fact, and nothing else in the tree can catch them drifting apart.

    Verified 2026-08-20: ``22fe2a6:eval_dataset/test_final.jsonl`` holds 1 351 questions over
    exactly the 57 schemas ``BIRD-corpus@30872d3`` covers, and their ids hash to
    ``423a3f4b65fb``.

    **Only the arms that claim the whole split**, which is the four measured ones. The three arms
    of ``docs/two-planes.md`` §9 run a ~100-question probe set that does not exist yet, so they
    declare a string naming itself unresolved — one that no run can match and that
    ``arm_startup_refusal`` therefore refuses, loudly, before the first paid question. Comparing
    *that* against the full split would fail this test for the arm doing the right thing, and the
    obvious repair — declaring ``SUBSET`` on all seven — is the false claim it exists to catch.
    ``test_every_shipped_arm_declares_a_question_set`` above still holds all seven to having one.
    """
    import json
    import subprocess

    from governed_bi.eval.provenance import short_digest

    repo = ROOT.parent / "BIRD-Data-Obfuscation"
    if not (repo / ".git").exists():
        pytest.skip(f"{repo} is not on this machine; the dataset repository is a sibling")

    for name in load_arm_profiles():
        profile = arm_profile(name)
        if str(profile.question_subset).startswith("unresolved:"):
            continue
        blob = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-p",
             f"{profile.dataset}:eval_dataset/test_final.jsonl"],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        assert blob.returncode == 0, (
            f"[arm.{name}] names dataset {profile.dataset!r}, which {repo} cannot resolve"
        )
        ids = {str(json.loads(line)["question_id"]) for line in blob.stdout.splitlines() if line}
        assert profile.question_subset == f"{len(ids)}:{short_digest(ids)}", (
            f"[arm.{name}] declares question_subset {profile.question_subset!r}, but "
            f"{profile.dataset}'s test split hashes to {len(ids)}:{short_digest(ids)}"
        )


# ── the three fields the loader did not read ──────────────────────────────────
#
# `hypothesised_effect`, `readout` and `corpus_release` were added to `ArmProfile` on the
# return-path branch, and the constructor call in `_parse_profiles` passed nine keys, none of them
# these three. So a file could declare an effect size, the loader would drop it, and
# `arm_power_refusal` -- the whole reason the field exists -- read `None` and abstained on every
# arm forever. The power gate was silent by construction and an underpowered paid arm still
# started.
#
# Every test below goes through `load_arm_profiles`. That is the point: the tests that existed
# built `ArmProfile(**base)` directly, which cannot see a loader that drops a field, and that is
# exactly how this survived being written, reviewed, and marked Closed in `open-work.md` §3.10.


def _load(tmp_path: Path, name: str, body: str) -> dict:
    """One TOML file per call, under its own name, driven through the real loader.

    Distinct names because ``load_arm_profiles`` is ``lru_cache``d on the path: two writes to one
    filename inside one test would compare the second body against the first body's result.
    """
    path = tmp_path / f"{name}.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return dict(load_arm_profiles(path))


#: The two mandatory fields, so each fixture below tests the field it is about and not one of
#: those two. Indented to survive ``textwrap.dedent`` after interpolation.
_MINIMAL = """    corpus_content_hash = "86ed1dbf"
    question_subset = "1351:423a3f4b65fb\""""


def test_the_loader_carries_a_declared_hypothesis_off_the_file(tmp_path: Path) -> None:
    """The defect itself, as one assertion.

    ``arm_power_refusal`` reads ``profile.hypothesised_effect``. A loader that drops it makes the
    gate return ``None`` for every arm ever declared, which is indistinguishable from "this arm
    claims nothing" -- and that is the reading the field was added to replace.
    """
    profiles = _load(tmp_path, "effect", f"""
    [arm.v6]
    treatment = ["prompt_set"]
    hypothesised_effect = 0.05
    readout = "EX"
{_MINIMAL}
    """)

    assert profiles["v6"].hypothesised_effect == 0.05, (
        "the file declared an effect size and the loader dropped it"
    )
    assert profiles["v6"].readout == "EX", "the file declared a readout and the loader dropped it"


def test_the_loader_carries_a_declared_corpus_release_off_the_file(tmp_path: Path) -> None:
    """``reconcile``'s ``corpus_release`` branch reads ``profile.corpus_release``, so a dropped
    field makes that block unreachable and the one comparability knob that names the corpus as a
    treatment reconciles against nothing."""
    profiles = _load(tmp_path, "release", f"""
    [arm.v6]
    treatment = ["corpus_release"]
    corpus_release = "v2026.08.20"
{_MINIMAL}
    """)

    assert profiles["v6"].corpus_release == "v2026.08.20"


def test_a_declared_release_reaches_reconcile_and_catches_the_wrong_one(tmp_path: Path) -> None:
    """Driven end to end rather than asserted on the dataclass, because the dataclass was never
    the broken part: ``ArmProfile(corpus_release=...)`` worked all along and the file could not
    reach it."""
    profile = _load(tmp_path, "release_wire", f"""
    [arm.v6]
    treatment = ["corpus_release"]
    corpus_release = "v2026.08.20"
{_MINIMAL}
    """)["v6"]

    agreeing = {
        "corpus_content_hash": "86ed1dbf",
        "knobs_resolved": {"question_subset": SUBSET, "corpus_release": "v2026.08.20"},
    }
    assert reconcile(profile, agreeing) == ()

    other = {
        "corpus_content_hash": "86ed1dbf",
        "knobs_resolved": {"question_subset": SUBSET, "corpus_release": "v2026.08.13"},
    }
    problems = reconcile(profile, other)
    assert problems and "v2026.08.13" in problems[0], (
        "an arm whose declared treatment is the corpus release accepted a different release"
    )


def test_an_unknown_key_in_an_arm_table_is_refused(tmp_path: Path) -> None:
    """The rule that makes a dropped field impossible to add silently.

    Until this existed, ``_parse_profiles`` read nine named keys and ignored everything else, so
    the way this defect looked from the *file* side was that writing ``hypothesised_effect = 0.03``
    into ``arms.toml`` parsed clean and did nothing. The field is spelled the British way, which
    makes ``hypothesized_effect`` the likeliest single edit in this tree to be discarded in
    silence.
    """
    with pytest.raises(ValueError, match="hypothesized_effect"):
        _load(tmp_path, "typo", f"""
    [arm.v6]
    treatment = ["prompt_set"]
    hypothesized_effect = 0.05
    readout = "EX"
{_MINIMAL}
        """)


def test_an_effect_with_no_readout_is_refused_at_load(tmp_path: Path) -> None:
    """``arm_power_refusal`` already refuses this pair, but it is reached only after a corpus, a
    dataset, a database and four models are built. Load is where refusing is free."""
    with pytest.raises(ValueError, match="declares no readout"):
        _load(tmp_path, "no_readout", f"""
    [arm.v6]
    treatment = ["prompt_set"]
    hypothesised_effect = 0.05
{_MINIMAL}
        """)


def test_a_readout_with_no_effect_is_refused_at_load(tmp_path: Path) -> None:
    """The other half, and it is not the mirror of the one above: a readout alone reaches
    ``arm_power_refusal``'s first branch, which returns ``None`` on the missing effect and
    abstains. So this direction has no run-time lock at all, and load is the only place it can be
    caught."""
    with pytest.raises(ValueError, match="declares no hypothesised_effect"):
        _load(tmp_path, "no_effect", f"""
    [arm.v6]
    treatment = ["prompt_set"]
    readout = "EX"
{_MINIMAL}
        """)


def test_an_effect_in_percentage_points_is_refused(tmp_path: Path) -> None:
    """``3`` where ``0.03`` was meant passes the power gate at every n, because the floor is a
    proportion and ``abs(3) > 0.0956`` always. A unit error that makes a gate pass is the same
    class of defect as a field the gate cannot read: ``require_power(discordant=0.29)`` is already
    on record as approving the exact arm shape it exists to refuse."""
    with pytest.raises(ValueError, match="points of the readout"):
        _load(tmp_path, "pp", f"""
    [arm.v6]
    treatment = ["prompt_set"]
    hypothesised_effect = 3
    readout = "EX"
{_MINIMAL}
        """)


def test_a_zero_effect_is_refused_rather_than_read_as_no_claim(tmp_path: Path) -> None:
    """Zero is not "no hypothesis" and must not be spelled the same way as one. No n detects it,
    so an arm declaring it is refused by ``require_power`` at every sample size -- which is a
    load-time fact about the file, not a run-time fact about the sample."""
    with pytest.raises(ValueError, match="points of the readout"):
        _load(tmp_path, "zero", f"""
    [arm.v6]
    treatment = ["prompt_set"]
    hypothesised_effect = 0.0
    readout = "EX"
{_MINIMAL}
        """)


def test_an_arm_whose_treatment_is_the_corpus_release_must_name_one(tmp_path: Path) -> None:
    """The one place ``corpus_release`` is mandatory, and the reason it is conditional elsewhere.

    An arm declaring ``treatment = ["corpus_release"]`` claims the corpus release *is* what
    changed. Not naming it leaves ``reconcile``'s release branch unentered, so the arm's own
    treatment is the single thing about it that nothing checks. Arms whose treatment is something
    else keep the field optional: every artifact on disk predates the knob, and refusing them
    would strand all seven to gain nothing the digest does not already reconcile.
    """
    with pytest.raises(ValueError, match="treatment is corpus_release"):
        _load(tmp_path, "release_unnamed", f"""
    [arm.v6]
    treatment = ["corpus_release"]
{_MINIMAL}
        """)


def test_the_shipped_arms_declare_no_hypothesis_and_that_is_the_committed_state() -> None:
    """**Read this before quoting the power gate as being in force.** On this file it is not.

    All seven arms in ``arms.toml`` declare no ``hypothesised_effect``, so ``arm_power_refusal``
    abstains on every one of them. That is the honest outcome for two different reasons: the four
    measured arms were measured before the field existed, and an effect size written down after
    the measurement is not a hypothesis; the three planned arms are sized against a ~100-question
    probe set nobody has materialised, and a number guessed at here would make the gate *pass*. What
    is *not* honest is leaving that inferable only from the absence of a key, which is how the
    dropped field went unnoticed. This asserts the inventory, so the first arm to declare one has
    to come through here and say so.
    """
    declared = {
        name: (p.hypothesised_effect, p.readout)
        for name, p in load_arm_profiles().items()
        if p.hypothesised_effect is not None or p.readout
    }
    assert declared == {}, (
        f"arms.toml now declares a hypothesis for {sorted(declared)}. That is fine and expected "
        "eventually -- update this test, and check the value was pre-registered rather than read "
        "off the arm's own result"
    )
