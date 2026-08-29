"""The three arms of ``docs/two-planes.md`` §9, and the corpus digest that used to refuse them.

``arms.toml`` declares seven arms: four measured on ``BIRD-corpus@30872d3`` and three planned
against ``@74ff80c``. The two commits between those refs add ``LICENSE`` and ``README.md`` and
touch no asset, and the whole-tree digest moved anyway — which is what ``docs/return-path.md``
means by "``--arm v4`` against the checked-out tip is refused today".

**That refusal is correct and stays.** v4's 1 351 rows carry ``86ed1dbf…``; re-pointing its
declaration at a tree it never ran on is the silent relabelling
``tests/conformance/test_arm_profiles_are_declared.py`` exists to fail on. What was missing is an
arm declared *for* today's tree, and the evidence that the digest's sensitivity to a README is a
property of the caller rather than of the corpus. Both are pinned here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governed_bi.eval.provenance import arm_startup_refusal
from governed_bi.register.arm_profiles import arm_profile, load_arm_profiles, reconcile
from governed_bi.register.knobs import comparability_keys

ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS = ROOT.parent / "BIRD-corpus"

#: The three planned arms and the ref they pin. One ref across all three is what makes them a
#: comparable set: a corpus patch landing mid-experiment would leave the arms already run
#: unreconcilable against the arms that follow, and the digest is what says so.
PLANNED = ("v4_live", "licensed_pre_budget", "licensed_pre_budget_cap10")
TIP = "74ff80c"
TIP_DIGEST = "6e5c7b4be83d56828bab66183eec03bbdcf486d7454d34acd066530010ebed85"

#: The digest restricted to the 57 schema subtrees, measured identical at ``30872d3`` and
#: ``74ff80c``. Not what any arm declares — see :func:`test_the_asset_digest_is_the_one_that_did_not_move`.
ASSET_DIGEST = "5a556b3c7936ddc3e6e9e1a902f38adc745a62ce9fac0cd44bb49d14b05f6c42"


def _schemas(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def test_the_three_planned_arms_are_declared_against_the_tip() -> None:
    """All three, on one corpus, with the digest a run against that corpus will record."""
    for name in PLANNED:
        profile = arm_profile(name)
        assert profile.corpus == TIP, f"[arm.{name}] pins {profile.corpus!r}, not the tip"
        assert profile.corpus_content_hash == TIP_DIGEST, (
            f"[arm.{name}] declares a digest that is not the tip's"
        )
        # A row naming no question set cannot contradict a profile -- that is ``reconcile``'s
        # "did not say" rule, and it is what keeps the seven ``proxy_*`` artifacts reconcilable.
        # So the corpus half is checked on its own here, and the unresolved question set is
        # driven through the pre-flight below, where a run does name one.
        assert reconcile(profile, {"corpus_content_hash": TIP_DIGEST}) == ()
        assert reconcile(profile, {"corpus_content_hash": "86ed1dbf"}), (
            f"[arm.{name}] accepts the digest of the corpus the measured arms ran on"
        )


def test_each_planned_arm_names_a_real_treatment_or_says_it_has_none() -> None:
    """``v4_live`` is the control and names nothing, because what separates it from ``v4`` is the
    engine — ``git_sha``, which is ``Role.operational`` and has no comparability name. The two
    arms below it each name knobs, and the loader has already refused any name that is not a
    ``Role.comparability`` knob; what this adds is that the *intended* names are the ones there.
    """
    assert arm_profile("v4_live").treatment == frozenset()
    assert arm_profile("licensed_pre_budget").treatment == frozenset(
        {"licensed_seed_pre_budget"}
    )
    assert arm_profile("licensed_pre_budget_cap10").treatment == frozenset(
        {"run_query_attempt_cap", "agent_node_timeout_s"}
    ), (
        "the cap arm must name the node budget too: raising the cap alone is the configuration "
        "register/knobs.py::attempt_cap_pairing_problem refuses, and an arm that moved the "
        "budget without declaring it would be compared against its control on a value that moved"
    )
    for name in PLANNED:
        assert arm_profile(name).treatment <= comparability_keys()


def test_the_cap_arm_is_measured_against_the_licence_arm_and_not_against_the_control() -> None:
    """Licence width and attempt cap are separate treatments, and an arm that moves both cannot
    attribute its own delta. ``docs/two-planes.md`` §9's reading table depends on the chain."""
    assert arm_profile("licensed_pre_budget").compare_to == "v4_live"
    assert arm_profile("licensed_pre_budget_cap10").compare_to == "licensed_pre_budget"


def test_the_live_control_does_not_claim_to_be_comparable_with_v4() -> None:
    """The trap ``v4_live`` exists inside of.

    ``v4`` and ``v4_live`` resolve every comparability knob identically — the difference is the
    harness commit — so ``eval/report.py::knobs_comparable`` would certify the pair and the delta
    would be read as a treatment effect when it is the code. Declaring ``compare_to = "v4"``
    would put that pair in writing.
    """
    assert arm_profile("v4_live").compare_to is None, (
        "v4_live must not name v4 as its control: the two differ only by git_sha, which is "
        "Role.operational and therefore invisible to knobs_comparable"
    )


def test_the_unresolved_question_set_refuses_the_run_rather_than_passing_it() -> None:
    """The probe set does not exist yet, and ``question_subset`` is mandatory. The value declared
    is one that names itself unresolved and can match no run, so the pre-flight refuses before
    the first paid question and prints what to paste back. A placeholder that *silenced* the
    check — ``"1351:423a3f4b65fb"``, the full split — would have passed here and been a lie.
    """
    for name in PLANNED:
        profile = arm_profile(name)
        assert str(profile.question_subset).startswith("unresolved:"), (
            f"[arm.{name}] now declares a resolved question set; if the probe set exists, this "
            "test should be checking it against the ids instead"
        )
        refusal = arm_startup_refusal(
            profile,
            {
                "corpus_content_hash": TIP_DIGEST,
                "question_subset": "103:0123456789ab",
            },
        )
        assert refusal is not None and "question_subset" in refusal, (
            f"[arm.{name}] would start a paid run against an unresolved question set"
        )


def test_no_planned_arm_pre_registers_an_effect_it_cannot_have_measured() -> None:
    """``arm_power_refusal`` abstains on a missing effect and *passes* on a fabricated one, so
    the silence is the safe state and the number has to come from whoever materialises the ~100
    question probe set. Pinned as an inventory, like the four measured arms', so declaring one
    has to come through a test."""
    for name in PLANNED:
        profile = arm_profile(name)
        assert profile.hypothesised_effect is None and not profile.readout, (
            f"[arm.{name}] declares a hypothesis; check it was pre-registered against the probe "
            "set rather than read off the arm's own result"
        )


# ── the digest that moved, and the one that did not ───────────────────────────


def _archive(ref: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(
        ["git", "-C", str(CORPUS), "archive", ref], capture_output=True, check=True
    )
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar.stdout, check=True)


@pytest.mark.skipif(not (CORPUS / ".git").exists(), reason="../BIRD-corpus is a sibling checkout")
def test_the_tip_digest_the_planned_arms_declare_is_the_one_the_tree_hashes_to() -> None:
    """The literal above against the tree it claims to describe. Without this the three arms
    would be refused at the pre-flight for a reason nothing in the repository could explain."""
    from governed_bi.corpus.hash import corpus_content_hash

    assert corpus_content_hash(CORPUS) == TIP_DIGEST, (
        "../BIRD-corpus no longer hashes to what the planned arms declare. If a commit landed, "
        "re-measure and re-declare -- and if it touched no asset, that is the defect the header "
        "note in arms.toml is about, not a reason to relax the check."
    )


@pytest.mark.skipif(not (CORPUS / ".git").exists(), reason="../BIRD-corpus is a sibling checkout")
def test_the_asset_digest_is_the_one_that_did_not_move(tmp_path: Path) -> None:
    """**The measurement the header's two-digests note rests on, run rather than quoted.**

    ``corpus_content_hash(root)`` moved between ``30872d3`` and ``74ff80c``;
    ``corpus_content_hash(root, schemas=<the 57 dirs>)`` did not. So the digest's sensitivity to
    a ``LICENSE`` and a ``README.md`` is a property of the *caller's* manifest, not of the
    corpus, and restricting it is a parameter of the one implementation rather than a second
    answer to "is this the same corpus" (``corpus/hash.py``'s own docstring says as much).

    Both commits are read through ``git archive`` into ``tmp_path``, so neither this checkout nor
    the corpus checkout is disturbed. What stops the scoped digest being what ``arms.toml``
    declares today is that ``tools/run_datalake_eval.py`` passes no manifest to
    ``serve/session.py::from_corpus_dir``, where ``api/graph_app.py`` and ``serve/__main__.py``
    both do — so a row records the whole-tree value. This test is the evidence for that change,
    and it will keep passing after it lands.
    """
    from governed_bi.corpus.hash import corpus_content_hash

    old = tmp_path / "30872d3"
    _archive("30872d3", old)

    assert corpus_content_hash(old) != corpus_content_hash(CORPUS), (
        "the whole-tree digests are equal, so the premise of the arms.toml note is gone"
    )
    assert _schemas(old) == _schemas(CORPUS), "the two refs no longer hold the same schema set"
    assert (
        corpus_content_hash(old, schemas=_schemas(old))
        == corpus_content_hash(CORPUS, schemas=_schemas(CORPUS))
        == ASSET_DIGEST
    ), (
        "the asset-scoped digest moved between 30872d3 and 74ff80c, so an asset changed after "
        "all and the header's claim that only LICENSE and README.md landed is stale"
    )


def test_the_file_still_holds_the_four_measured_arms() -> None:
    """Adding arms must not quietly drop one. ``v4`` is the arm every quoted figure comes from
    and ``v3_fold`` is what it is measured against."""
    assert set(load_arm_profiles()) == {*PLANNED, "v3_fold", "v4", "v5", "ask_first"}
