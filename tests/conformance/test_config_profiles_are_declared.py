"""``configs.toml`` is the committed half of a config's identity, so it has to be checkable.

Before it existed, ``runs/eval/`` named a config and nothing a reader could fetch said what the
name meant — the config's configuration lived in a gitignored ``.env`` on one machine. That is the
"versioned is not rebuildable" problem the corpus has, one level up.

The file is load-bearing rather than documentation because of audit D9: ``settings_comparable``
refuses a pair that cannot name its treatment, and this is where the name comes from.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from governed_bi.registry.config_profiles import (
    ConfigProfile,
    config_profile,
    load_config_profiles,
    reconcile,
)
from governed_bi.registry.settings import setting_names

ROOT = Path(__file__).resolve().parent.parent.parent

#: The three shipped configs' question set, in the ``question_subset`` setting's format. Every fixture
#: below declares one because :func:`reconcile`'s second lock refuses a profile without one —
#: and a fixture is exactly where the *last* unreconcilable profile was invented, so the fixtures
#: paying the same price as the shipped file is the mechanism working, not friction.
SUBSET = "1351:423a3f4b65fb"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "configs.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_the_repositorys_own_file_parses_and_names_real_settings() -> None:
    """The shipped file is the one that matters; a loader that only works on fixtures is a
    loader nobody has run."""
    profiles = load_config_profiles()

    assert "v4" in profiles, "the config every quoted figure comes from is undeclared"
    for name, profile in profiles.items():
        assert profile.treatment <= setting_names(), (
            f"[config.{name}] names a treatment that is not a comparability setting"
        )


def test_v4s_declared_treatment_is_the_prompt() -> None:
    """Pinned because it is what makes v3-fold → v4 quotable at all.

    If this silently became empty, ``settings_comparable`` would report ``cannot_evaluate`` on the
    repository's headline comparison, and it would read as a data problem rather than a
    declaration problem.
    """
    assert config_profile("v4").treatment == frozenset({"prompt_set"})


def test_a_treatment_that_is_not_a_comparability_setting_is_refused(tmp_path: Path) -> None:
    """Refused at load, not at use.

    A typo reads as "no treatment declared" downstream, which ``settings_comparable`` turns into
    ``cannot_evaluate`` — a result that looks like missing data and sends the reader to the
    artifacts instead of to this file.

    Mutation-verified 2026-08-11: deleting the ``unknown`` check in ``_parse_profiles`` turns
    this red.
    """
    path = _write(tmp_path, '''
        [config.typo]
        treatment = ["prompt_sett"]
    ''')
    with pytest.raises(ValueError, match="prompt_sett"):
        load_config_profiles(path)


def test_git_sha_is_a_declared_setting_and_a_legal_treatment(tmp_path: Path) -> None:
    """Roles are gone. A difference in ``git_sha`` fails a comparison unless it is the treatment."""
    path = _write(tmp_path, '''
        [config.wrong]
        treatment = ["git_sha"]
        corpus_content_hash = "86ed1dbf"
        question_subset = "1351:423a3f4b65fb"
    ''')
    profiles = load_config_profiles(path)
    assert profiles["wrong"].treatment == frozenset({"git_sha"})


def test_an_unknown_config_raises_rather_than_returning_an_empty_treatment() -> None:
    """The dangerous default. An empty profile for a misspelled config name would degrade a real
    comparison to ``cannot_evaluate`` and say nothing about why."""
    with pytest.raises(KeyError, match="no_such_config"):
        config_profile("no_such_config")


def test_reconcile_catches_an_artifact_labelled_with_the_wrong_corpus() -> None:
    """The point of writing a claim down is that it can be checked against what ran."""
    profile = ConfigProfile(
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )

    assert reconcile(profile, {"corpus_content_hash": "86ed1dbf"}) == ()

    problems = reconcile(profile, {"corpus_content_hash": "deadbeef"})
    assert problems and "deadbeef" in problems[0]


def test_reconcile_reads_the_row_and_not_the_setting_mapping() -> None:
    """``corpus_content_hash`` is a ``RecordField``, and ``reconcile`` looked for it in
    ``knobs_resolved``.

    It is never there, so the lookup returned ``None`` and the function returned agreement for
    every artifact ever produced — declared machinery whose one caller was its own tests, and
    which would have said "fine" if it had been called. Asserted from both sides, because a
    reader that fell back to the setting mapping would still satisfy the first half.
    """
    profile = ConfigProfile(
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )

    row = {"corpus_content_hash": "deadbeef", "knobs_resolved": {"corpus_content_hash": "86ed1dbf"}}
    assert reconcile(profile, row), "the setting mapping was read instead of the row"

    setting_only = {"knobs_resolved": {"corpus_content_hash": "deadbeef"}}
    assert reconcile(profile, setting_only) == (), (
        "a row that does not name a corpus cannot contradict the profile"
    )


def test_reconcile_compares_the_digest_and_never_the_git_ref() -> None:
    """The two fields are two namespaces, and comparing them is what made this vacuous.

    ``corpus = "30872d3"`` is the corpus repository's git ref; every row records the content
    digest ``86ed1dbf…``. The old check was ``str(recorded).startswith(profile.corpus)``, which
    could not match on any real artifact and therefore never fired.
    """
    git_only = ConfigProfile(
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

    both = ConfigProfile(
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
    profile = ConfigProfile(
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbfef8b325e",
        question_subset=SUBSET,
    )
    assert reconcile(profile, {"corpus_content_hash": "86ed1dbf"})


def test_the_shipped_profiles_name_the_digest_their_artifacts_carry() -> None:
    """v4 is the config every quoted figure comes from, and its artifact records this digest.

    Pinned as a literal so that repointing ``configs.toml`` at a rebuilt corpus without re-running
    the config is a test failure rather than a silent relabelling. The value is read off
    ``runs/eval/proxy_v4_corpus30872d3.jsonl``, which is gitignored — hence the literal here.
    """
    digest = "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5"
    for name in ("v4", "v5"):
        assert config_profile(name).corpus_content_hash == digest
        assert config_profile(name).corpus == "30872d3", "the git ref is kept, and is not the digest"


def test_a_profile_that_cannot_be_reconciled_says_so_instead_of_agreeing() -> None:
    """The third way this function returned agreement without comparing anything.

    It outlived the other two. ``reconcile`` was repaired on 2026-08-11 and ``v3_fold`` — the
    baseline every v4 figure is measured against — declared no ``corpus_content_hash``, so the
    ``is not None`` guard was never entered. A run launched ``--config v3_fold`` against *any*
    corpus cleared the pre-flight check and was then told, in the report, that every one of its
    1 351 rows agreed with the profile.

    Silence is the wrong answer here because the caller cannot tell it apart from a pass. Both
    locks are asserted: the loader refuses such a file, and this refuses such an object, because
    ``ConfigProfile`` is constructible directly and a fixture is where one gets invented.
    """
    profile = ConfigProfile(name="x", description="", treatment=frozenset())

    problems = reconcile(profile, {"corpus_content_hash": "anything"})
    assert problems, "an unreconcilable profile must not report agreement"
    assert "no corpus_content_hash" in problems[0]


def test_the_loader_refuses_an_config_that_declares_no_digest(tmp_path: Path) -> None:
    """The half that stops the *next* config reintroducing it.

    Making ``reconcile`` loud fixes the configs already declared; refusing the file is what makes
    a new ``[config.x]`` with no digest a build failure rather than a check with an off switch
    nobody labelled. ``corpus`` alone does not satisfy it — that is the git ref, and comparing
    it against the recorded digest is the original defect.
    """
    path = _write(tmp_path, '''
        [config.undeclared]
        treatment = ["prompt_set"]
        corpus = "30872d3"
    ''')
    with pytest.raises(ValueError, match="no corpus_content_hash"):
        load_config_profiles(path)


def test_every_shipped_config_can_actually_be_reconciled() -> None:
    """Not just v4 and v5. ``v3_fold`` is the config the control is measured against, and it was
    the one with no digest — so the check that mattered most was the one that ran on nothing.

    Each config is reconciled against **its own** declared digest, not against one constant. The
    constant was here until 2026-09-20 and it encoded an assumption the file had outgrown:
    that every config shares one corpus. ``luna_v4_embed`` is on the corpus *tip*, which is
    ``30872d3``'s content plus a root ``LICENSE`` and ``README.md`` — an unchanged corpus with
    a changed digest, because an unmanifested ``corpus_content_hash`` counts every file. A test
    asserting one digest for all configs would have forced that config to declare a hash no session
    can produce, which is the failure mode this whole file exists to prevent, arrived at from
    the other side.
    """
    for name in load_config_profiles():
        profile = config_profile(name)
        assert profile.corpus_content_hash, f"[config.{name}] cannot be reconciled"
        own = {"corpus_content_hash": profile.corpus_content_hash}
        assert reconcile(profile, own) == (), f"[config.{name}] rejects the corpus it declares"
        assert reconcile(profile, {"corpus_content_hash": "deadbeef"}), (
            f"[config.{name}] accepts a corpus it did not run on"
        )


def test_a_turn_that_abstained_before_routing_does_not_contradict_the_profile() -> None:
    """open-work 3.6a: a clarification that ends before routing carries no corpus hash at all.

    There are 4 of them on the v4 config. Reading ``None`` as a contradiction would put four
    identical complaints into every report of a correct run.
    """
    profile = ConfigProfile(
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )
    assert reconcile(profile, {"corpus_content_hash": None, "outcome": "clarification"}) == ()


# ── the question set: the same rule, one field over ───────────────────────────


def test_the_loader_refuses_an_config_that_declares_no_question_set(tmp_path: Path) -> None:
    """The hole `corpus_content_hash` closed for the corpus and not for the questions.

    Found 2026-08-14 by a downstream fork that needed to know which questions the three
    published configs ran, and had to recover it by filtering four historical versions of
    `BIRD-Data-Obfuscation:eval_dataset/test_final.jsonl` against the 57 schemas
    `BIRD-corpus@30872d3` covers. Until this field existed, a rerun on a replaced dataset
    produced the same n = 1 351, a substantially different population, and passed every
    quotability check -- because the checks compare the corpus digest and the settings, and both
    matched. `corpus` alone does not satisfy it and neither does `dataset`: both are git refs,
    and comparing a git ref against a recorded digest is the original defect.
    """
    path = _write(tmp_path, '''
        [config.no_questions]
        treatment = ["prompt_set"]
        corpus = "30872d3"
        corpus_content_hash = "86ed1dbf"
        dataset = "22fe2a6"
    ''')
    with pytest.raises(ValueError, match="no question_subset"):
        load_config_profiles(path)


def test_reconcile_catches_an_config_rerun_on_a_replaced_dataset() -> None:
    """**The defect this field exists for**, as a comparison that now fails.

    Same corpus digest, same settings, same n -- and a different question population. That pair
    was indistinguishable from a replicate, which is what makes it worse than an obviously
    broken run.
    """
    profile = ConfigProfile(
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


def test_reconcile_reads_the_question_set_out_of_the_setting_mapping_where_it_lives() -> None:
    """The mirror image of ``test_reconcile_reads_the_row_and_not_the_knob_mapping``, and it
    has to be, because the two fields live in different places.

    ``corpus_content_hash`` is a ``RecordField`` and sits at the top of the row;
    ``question_subset`` is a ``Role.scope`` setting written by
    ``eval/provenance.py::scope_identity`` and sits in ``knobs_resolved`` -- observed in
    ``runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl``, which records
    ``1351:423a3f4b65fb`` there and nothing at the top level. The 2026-08-11 defect was reading
    a field where it never is, not reading the setting mapping as such; a reader who "corrects"
    this branch to match the corpus branch recreates that defect pointing the other way, and
    this test is what says so.
    """
    profile = ConfigProfile(
        name="x",
        description="",
        treatment=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )
    row = {"corpus_content_hash": "86ed1dbf", "knobs_resolved": {"question_subset": "9:aaaa"}}
    assert reconcile(profile, row), "the setting mapping was not read"

    agreeing = {"corpus_content_hash": "86ed1dbf", "knobs_resolved": {"question_subset": SUBSET}}
    assert reconcile(profile, agreeing) == ()

    # The bare-mapping fallback, which is what lets the driver ask the same question of a
    # session that has no setting mapping yet.
    assert reconcile(profile, {"corpus_content_hash": "86ed1dbf", "question_subset": "9:aaaa"})

    silent = {"corpus_content_hash": "86ed1dbf"}
    assert reconcile(profile, silent) == (), (
        "a row that names no question set cannot contradict the profile -- the seven proxy_* "
        "artifacts predate the writer and refusing per row would strand all of them"
    )


def test_a_profile_with_no_question_set_says_so_instead_of_agreeing() -> None:
    """The second lock, on the new field. ``ConfigProfile`` is constructible directly and a
    fixture is where the last unreconcilable profile came from."""
    profile = ConfigProfile(
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
        assert config_profile(name).question_subset == SUBSET
        assert config_profile(name).dataset == "22fe2a6", "the git ref is kept, and is not the digest"


def test_every_shipped_config_declares_a_question_set() -> None:
    """``v3_fold`` is the config that had no corpus digest while the check said it was fine. The
    same omission on the same config is what this asserts cannot recur one field over."""
    for name in load_config_profiles():
        profile = config_profile(name)
        assert profile.question_subset, f"[config.{name}] cannot have its question set reconciled"
        assert reconcile(
            profile, {"knobs_resolved": {"question_subset": "1351:0000deadbeef"}}
        ), f"[config.{name}] accepts a question set it did not run"


def test_the_declared_question_set_is_the_dataset_commit_it_names() -> None:
    """The literal above, checked against the repository it claims to come from.

    Skipped where ``../BIRD-Data-Obfuscation`` is absent, which is the same precondition that
    the dataset is a sibling checkout and is absent in CI -- so this proves the pin only on a
    machine that holds the dataset. That is still worth having: ``SUBSET`` and ``dataset`` are
    two claims about one fact, and nothing else in the tree can catch them drifting apart.

    Verified 2026-08-20: ``22fe2a6:eval_dataset/test_final.jsonl`` holds 1 351 questions over
    exactly the 57 schemas ``BIRD-corpus@30872d3`` covers, and their ids hash to
    ``423a3f4b65fb``.
    """
    import json
    import subprocess

    from governed_bi.eval.provenance import short_digest

    repo = ROOT.parent / "BIRD-Data-Obfuscation"
    if not (repo / ".git").exists():
        pytest.skip(f"{repo} is not on this machine; the dataset repository is a sibling")

    for name in load_config_profiles():
        profile = config_profile(name)
        blob = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-p",
             f"{profile.dataset}:eval_dataset/test_final.jsonl"],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        assert blob.returncode == 0, (
            f"[config.{name}] names dataset {profile.dataset!r}, which {repo} cannot resolve"
        )
        ids = {str(json.loads(line)["question_id"]) for line in blob.stdout.splitlines() if line}
        assert profile.question_subset == f"{len(ids)}:{short_digest(ids)}", (
            f"[config.{name}] declares question_subset {profile.question_subset!r}, but "
            f"{profile.dataset}'s test split hashes to {len(ids)}:{short_digest(ids)}"
        )


# ── the three fields the loader did not read ──────────────────────────────────
#
# `hypothesised_effect`, `readout` and `corpus_release` were added to `ConfigProfile` on the
# return-path branch, and the constructor call in `_parse_profiles` passed nine keys, none of them
# these three. So a file could declare an effect size, the loader would drop it, and
# `config_power_refusal` -- the whole reason the field exists -- read `None` and abstained on every
# config forever. The power check was silent by construction and an underpowered paid config still
# started.
#
# Every test below goes through `load_config_profiles`. That is the point: the tests that existed
# built `ConfigProfile(**base)` directly, which cannot see a loader that drops a field, and that is
# exactly how this survived being written, reviewed, and marked Closed in `open-work.md` §3.10.


def _load(tmp_path: Path, name: str, body: str) -> dict:
    """One TOML file per call, under its own name, driven through the real loader.

    Distinct names because ``load_config_profiles`` is ``lru_cache``d on the path: two writes to one
    filename inside one test would compare the second body against the first body's result.
    """
    path = tmp_path / f"{name}.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return dict(load_config_profiles(path))


#: The two mandatory fields, so each fixture below tests the field it is about and not one of
#: those two. Indented to survive ``textwrap.dedent`` after interpolation.
_MINIMAL = """    corpus_content_hash = "86ed1dbf"
    question_subset = "1351:423a3f4b65fb\""""


def test_the_loader_carries_a_declared_hypothesis_off_the_file(tmp_path: Path) -> None:
    """The defect itself, as one assertion.

    ``config_power_refusal`` reads ``profile.hypothesised_effect``. A loader that drops it makes the
    check return ``None`` for every config ever declared, which is indistinguishable from "this config
    claims nothing" -- and that is the reading the field was added to replace.
    """
    profiles = _load(tmp_path, "effect", f"""
    [config.v6]
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
    field makes that block unreachable and the one comparability setting that names the corpus as a
    treatment reconciles against nothing."""
    profiles = _load(tmp_path, "release", f"""
    [config.v6]
    treatment = ["corpus_release"]
    corpus_release = "v2026.08.20"
{_MINIMAL}
    """)

    assert profiles["v6"].corpus_release == "v2026.08.20"


def test_a_declared_release_reaches_reconcile_and_catches_the_wrong_one(tmp_path: Path) -> None:
    """Driven end to end rather than asserted on the dataclass, because the dataclass was never
    the broken part: ``ConfigProfile(corpus_release=...)`` worked all along and the file could not
    reach it."""
    profile = _load(tmp_path, "release_wire", f"""
    [config.v6]
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
        "a config whose declared treatment is the corpus release accepted a different release"
    )


def test_an_unknown_key_in_an_config_table_is_refused(tmp_path: Path) -> None:
    """The rule that makes a dropped field impossible to add silently.

    Until this existed, ``_parse_profiles`` read nine named keys and ignored everything else, so
    the way this defect looked from the *file* side was that writing ``hypothesised_effect = 0.03``
    into ``configs.toml`` parsed clean and did nothing. The field is spelled the British way, which
    makes ``hypothesized_effect`` the likeliest single edit in this tree to be discarded in
    silence.
    """
    with pytest.raises(ValueError, match="hypothesized_effect"):
        _load(tmp_path, "typo", f"""
    [config.v6]
    treatment = ["prompt_set"]
    hypothesized_effect = 0.05
    readout = "EX"
{_MINIMAL}
        """)


def test_an_effect_with_no_readout_is_refused_at_load(tmp_path: Path) -> None:
    """``config_power_refusal`` already refuses this pair, but it is reached only after a corpus, a
    dataset, a database and four models are built. Load is where refusing is free."""
    with pytest.raises(ValueError, match="declares no readout"):
        _load(tmp_path, "no_readout", f"""
    [config.v6]
    treatment = ["prompt_set"]
    hypothesised_effect = 0.05
{_MINIMAL}
        """)


def test_a_readout_with_no_effect_is_refused_at_load(tmp_path: Path) -> None:
    """The other half, and it is not the mirror of the one above: a readout alone reaches
    ``config_power_refusal``'s first branch, which returns ``None`` on the missing effect and
    abstains. So this direction has no run-time lock at all, and load is the only place it can be
    caught."""
    with pytest.raises(ValueError, match="declares no hypothesised_effect"):
        _load(tmp_path, "no_effect", f"""
    [config.v6]
    treatment = ["prompt_set"]
    readout = "EX"
{_MINIMAL}
        """)


def test_an_effect_in_percentage_points_is_refused(tmp_path: Path) -> None:
    """``3`` where ``0.03`` was meant passes the power check at every n, because the floor is a
    proportion and ``abs(3) > 0.0956`` always. A unit error that makes a check pass is the same
    class of defect as a field the check cannot read: ``require_power(discordant=0.29)`` is already
    on record as approving the exact config shape it exists to refuse."""
    with pytest.raises(ValueError, match="points of the readout"):
        _load(tmp_path, "pp", f"""
    [config.v6]
    treatment = ["prompt_set"]
    hypothesised_effect = 3
    readout = "EX"
{_MINIMAL}
        """)


def test_a_zero_effect_is_refused_rather_than_read_as_no_claim(tmp_path: Path) -> None:
    """Zero is not "no hypothesis" and must not be spelled the same way as one. No n detects it,
    so a config declaring it is refused by ``require_power`` at every sample size -- which is a
    load-time fact about the file, not a run-time fact about the sample."""
    with pytest.raises(ValueError, match="points of the readout"):
        _load(tmp_path, "zero", f"""
    [config.v6]
    treatment = ["prompt_set"]
    hypothesised_effect = 0.0
    readout = "EX"
{_MINIMAL}
        """)


def test_an_config_whose_treatment_is_the_corpus_release_must_name_one(tmp_path: Path) -> None:
    """The one place ``corpus_release`` is mandatory, and the reason it is conditional elsewhere.

    An config declaring ``treatment = ["corpus_release"]`` claims the corpus release *is* what
    changed. Not naming it leaves ``reconcile``'s release branch unentered, so the config's own
    treatment is the single thing about it that nothing checks. Configs whose treatment is something
    else keep the field optional: every artifact on disk predates the setting, and refusing them
    would strand all seven to gain nothing the digest does not already reconcile.
    """
    with pytest.raises(ValueError, match="treatment is corpus_release"):
        _load(tmp_path, "release_unnamed", f"""
    [config.v6]
    treatment = ["corpus_release"]
{_MINIMAL}
        """)


def test_the_shipped_configs_declare_no_hypothesis_and_that_is_the_committed_state() -> None:
    """**Read this before quoting the power check as being in force.** On this file it is not.

    All four configs in ``configs.toml`` declare no ``hypothesised_effect``, so ``config_power_refusal``
    abstains on every one of them. That is the honest outcome: all four were measured before the
    field existed, and an effect size written down after the measurement is not a hypothesis. What
    is *not* honest is leaving that inferable only from the absence of a key, which is how the
    dropped field went unnoticed. This asserts the inventory, so the first config to declare one has
    to come through here and say so.
    """
    declared = {
        name: (p.hypothesised_effect, p.readout)
        for name, p in load_config_profiles().items()
        if p.hypothesised_effect is not None or p.readout
    }
    assert declared == {}, (
        f"configs.toml now declares a hypothesis for {sorted(declared)}. That is fine and expected "
        "eventually -- update this test, and check the value was pre-registered rather than read "
        "off the config's own result"
    )
