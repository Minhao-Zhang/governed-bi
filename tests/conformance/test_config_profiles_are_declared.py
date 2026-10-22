"""``configs.toml`` is the committed half of a config's identity, so it has to be checkable.

Before it existed, ``runs/eval/`` named a config and nothing a reader could fetch said what the
name meant — the config's configuration lived in a gitignored ``.env`` on one machine. That is the
"versioned is not rebuildable" problem the corpus has, one level up.

The file is rather than documentation because of audit D9: ``settings_comparable``
refuses a pair that cannot name its setting under test, and this is where the name comes from.
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

#: Every fixture declares a question set. ``reconcile`` refuses a profile without one, and a
#: fixture is where an unreconcilable profile gets invented.
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
        assert profile.under_test <= setting_names(), (
            f"[config.{name}] names a treatment that is not a comparability setting"
        )


def test_v4s_declared_under_test_is_the_prompt() -> None:
    """If this set went empty, the headline comparison would look like missing data."""
    assert config_profile("v4").under_test == frozenset({"prompt_set"})


def test_a_under_test_that_is_not_a_affects_results_setting_is_refused(tmp_path: Path) -> None:
    """A typo is refused at load. Downstream it would look like missing data."""
    path = _write(tmp_path, '''
        [config.typo]
        under_test = ["prompt_sett"]
    ''')
    with pytest.raises(ValueError, match="prompt_sett"):
        load_config_profiles(path)


def test_git_sha_is_a_declared_setting_and_a_legal_under_test(tmp_path: Path) -> None:
    """Roles are gone. A difference in ``git_sha`` fails a comparison unless it is the setting under test."""
    path = _write(tmp_path, '''
        [config.wrong]
        under_test = ["git_sha"]
        corpus_content_hash = "86ed1dbf"
        question_subset = "1351:423a3f4b65fb"
    ''')
    profiles = load_config_profiles(path)
    assert profiles["wrong"].under_test == frozenset({"git_sha"})


def test_an_unknown_config_raises_rather_than_returning_an_empty_under_test() -> None:
    """The dangerous default. An empty profile for a misspelled config name would degrade a real
    comparison to ``cannot_evaluate`` and say nothing about why."""
    with pytest.raises(KeyError, match="no_such_config"):
        config_profile("no_such_config")


def test_reconcile_catches_an_artifact_labelled_with_the_wrong_corpus() -> None:
    """The point of writing a claim down is that it can be checked against what ran."""
    profile = ConfigProfile(
        name="x",
        description="",
        under_test=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )

    assert reconcile(profile, {"corpus_content_hash": "86ed1dbf"}) == ()

    problems = reconcile(profile, {"corpus_content_hash": "deadbeef"})
    assert problems and "deadbeef" in problems[0]


def test_reconcile_reads_the_row_and_not_the_setting_mapping() -> None:
    """The hash is on the row, not in ``settings_resolved``. Reading the mapping agrees with every artifact."""
    profile = ConfigProfile(
        name="x",
        description="",
        under_test=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )

    row = {"corpus_content_hash": "deadbeef", "settings_resolved": {"corpus_content_hash": "86ed1dbf"}}
    assert reconcile(profile, row), "the setting mapping was read instead of the row"

    setting_only = {"settings_resolved": {"corpus_content_hash": "deadbeef"}}
    assert reconcile(profile, setting_only) == (), (
        "a row that does not name a corpus cannot contradict the profile"
    )


def test_reconcile_compares_the_digest_and_never_the_git_ref() -> None:
    """A git ref and a content digest are different namespaces. Comparing them never matches a real row."""
    git_only = ConfigProfile(
        name="x",
        description="",
        under_test=frozenset(),
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
        under_test=frozenset(),
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
        under_test=frozenset(),
        corpus_content_hash="86ed1dbfef8b325e",
        question_subset=SUBSET,
    )
    assert reconcile(profile, {"corpus_content_hash": "86ed1dbf"})


def test_the_shipped_profiles_name_the_digest_their_artifacts_carry() -> None:
    """Pinned as a literal. The artifact is gitignored, so a rebuilt corpus would otherwise relabel in silence."""
    digest = "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5"
    for name in ("v4", "v5"):
        assert config_profile(name).corpus_content_hash == digest
        assert config_profile(name).corpus == "30872d3", "the git ref is kept, and is not the digest"


def test_a_profile_that_cannot_be_reconciled_says_so_instead_of_agreeing() -> None:
    """Silence is not a pass. A profile with no digest must not report agreement."""
    profile = ConfigProfile(name="x", description="", under_test=frozenset())

    problems = reconcile(profile, {"corpus_content_hash": "anything"})
    assert problems, "an unreconcilable profile must not report agreement"
    assert "no corpus_content_hash" in problems[0]


def test_the_loader_refuses_an_config_that_declares_no_digest(tmp_path: Path) -> None:
    """A new config with no digest fails to load. A git ref does not stand in for the digest."""
    path = _write(tmp_path, '''
        [config.undeclared]
        under_test = ["prompt_set"]
        corpus = "30872d3"
    ''')
    with pytest.raises(ValueError, match="no corpus_content_hash"):
        load_config_profiles(path)


def test_every_shipped_config_can_be_reconciled() -> None:
    """Each config is reconciled against its own digest. One shared constant assumes every config uses one corpus."""
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
        under_test=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )
    assert reconcile(profile, {"corpus_content_hash": None, "outcome": "clarification"}) == ()


# ── the question set: the same rule, one field over ───────────────────────────


def test_the_loader_refuses_an_config_that_declares_no_question_set(tmp_path: Path) -> None:
    """Same n and same corpus digest can hide a replaced question set. A git ref does not name that set."""
    path = _write(tmp_path, '''
        [config.no_questions]
        under_test = ["prompt_set"]
        corpus = "30872d3"
        corpus_content_hash = "86ed1dbf"
        dataset = "22fe2a6"
    ''')
    with pytest.raises(ValueError, match="no question_subset"):
        load_config_profiles(path)


def test_reconcile_catches_an_config_rerun_on_a_replaced_dataset() -> None:
    """Same corpus and same n with a different question set must not look like a replicate."""
    profile = ConfigProfile(
        name="v4",
        description="",
        under_test=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )
    same_corpus_other_questions = {
        "corpus_content_hash": "86ed1dbf",
        "settings_resolved": {"question_subset": "1351:0000deadbeef"},
    }

    problems = reconcile(profile, same_corpus_other_questions)

    assert problems and "1351:0000deadbeef" in problems[0]
    assert "question_subset" in problems[0]


def test_reconcile_reads_the_question_set_out_of_the_setting_mapping_where_it_lives() -> None:
    """``question_subset`` lives in ``settings_resolved``. Reading it from the row, as the corpus hash is read, misses
    it."""
    profile = ConfigProfile(
        name="x",
        description="",
        under_test=frozenset(),
        corpus_content_hash="86ed1dbf",
        question_subset=SUBSET,
    )
    row = {"corpus_content_hash": "86ed1dbf", "settings_resolved": {"question_subset": "9:aaaa"}}
    assert reconcile(profile, row), "the setting mapping was not read"

    agreeing = {"corpus_content_hash": "86ed1dbf", "settings_resolved": {"question_subset": SUBSET}}
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
        name="x", description="", under_test=frozenset(), corpus_content_hash="86ed1dbf"
    )

    problems = reconcile(profile, {"corpus_content_hash": "86ed1dbf"})

    assert problems and "no question_subset" in problems[0]


def test_the_shipped_profiles_name_the_question_set_their_artifacts_carry() -> None:
    """Pinned as a literal. The artifacts are gitignored, so a silent relabel would not show up in review."""
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
            profile, {"settings_resolved": {"question_subset": "1351:0000deadbeef"}}
        ), f"[config.{name}] accepts a question set it did not run"


def test_the_declared_question_set_is_the_dataset_commit_it_names() -> None:
    """``question_subset`` and ``dataset`` are two claims about one split. Skipped when the dataset checkout is absent.
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


# The loader must copy hypothesised_effect, readout, and corpus_release. A test that
# builds ConfigProfile directly cannot see a loader that drops a field, which is how
# the power check stayed silent.


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
    """A loader that drops the effect makes every config look like it claims nothing."""
    profiles = _load(tmp_path, "effect", f"""
    [config.v6]
    under_test = ["prompt_set"]
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
    field makes that block unreachable and the one setting that affects results that names the corpus as a
    setting under test reconciles against nothing."""
    profiles = _load(tmp_path, "release", f"""
    [config.v6]
    under_test = ["corpus_release"]
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
    under_test = ["corpus_release"]
    corpus_release = "v2026.08.20"
{_MINIMAL}
    """)["v6"]

    agreeing = {
        "corpus_content_hash": "86ed1dbf",
        "settings_resolved": {"question_subset": SUBSET, "corpus_release": "v2026.08.20"},
    }
    assert reconcile(profile, agreeing) == ()

    other = {
        "corpus_content_hash": "86ed1dbf",
        "settings_resolved": {"question_subset": SUBSET, "corpus_release": "v2026.08.13"},
    }
    problems = reconcile(profile, other)
    assert problems and "v2026.08.13" in problems[0], (
        "a config whose declared treatment is the corpus release accepted a different release"
    )


def test_an_unknown_key_in_an_config_table_is_refused(tmp_path: Path) -> None:
    """An unknown key is refused. Ignoring extra keys is how a misspelled field parsed and did nothing."""
    with pytest.raises(ValueError, match="hypothesized_effect"):
        _load(tmp_path, "typo", f"""
    [config.v6]
    under_test = ["prompt_set"]
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
    under_test = ["prompt_set"]
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
    under_test = ["prompt_set"]
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
    under_test = ["prompt_set"]
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
    under_test = ["prompt_set"]
    hypothesised_effect = 0.0
    readout = "EX"
{_MINIMAL}
        """)


def test_an_config_whose_under_test_is_the_corpus_release_must_name_one(tmp_path: Path) -> None:
    """Mandatory only when it is the setting under test. Elsewhere the digest already reconciles the corpus."""
    with pytest.raises(ValueError, match="treatment is corpus_release"):
        _load(tmp_path, "release_unnamed", f"""
    [config.v6]
    under_test = ["corpus_release"]
{_MINIMAL}
        """)


def test_the_shipped_configs_declare_no_hypothesis_and_that_is_the_committed_state() -> None:
    """No shipped config declares an effect, so the power check abstains. The first one to declare one fails here until
    this inventory moves."""
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
