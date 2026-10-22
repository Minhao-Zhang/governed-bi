"""A missing key is not a recorded ``None``. Hash differences are retrieval noise, not the declared change."""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.eval.report import comparison_quotable, config_population, settings_comparable
from governed_bi.measure.checks import Result
from governed_bi.registry.settings import UNSET, defaults

#: The setting under test in the fixtures below. A real one: v3-fold → v4 moved the prompt and nothing
#: else, and ``prompt_set`` is ``a recorded setting`` — which is exactly why the setting under test has
#: to be declared rather than assumed to be whichever settings happen to differ.
_UNDER_TEST = frozenset({"prompt_set"})


def _recorded(**overrides: Any) -> dict[str, Any]:
    """Built from the register so a later setting is covered. ``UNSET`` flattens to ``None``."""
    base = {k: (None if v is UNSET else v) for k, v in defaults().items()}
    base["prompt_set"] = "prompts-a"
    return {**base, **overrides}


def _rows(n: int, *, settings: dict[str, Any], hash_prefix: str) -> list[dict[str, Any]]:
    """The extra fields keep unrelated checks from failing the comparison for an unnamed reason."""
    return [
        {
            "question_id": f"q{i}",
            "correct": i % 2 == 0,
            "context_hash": f"{hash_prefix}-{i}",
            "settings_resolved": dict(settings),
            "corpus_content_hash": "corpus-abc",
            "crashed": False,
            "guardrail_error": False,
            "negative_failed_open": False,
            "channel_scoring": {"entity": "ran"},
            "channel_degraded": 0,
        }
        for i in range(n)
    ]


def _pair(settings_a: dict[str, Any], settings_b: dict[str, Any], n: int = 40) -> tuple[Any, Any]:
    return (
        config_population(_rows(n, settings=settings_a, hash_prefix="a"), label="a"),
        config_population(_rows(n, settings=settings_b, hash_prefix="b"), label="b"),
    )


def _treated() -> tuple[Any, Any]:
    """A legitimate config pair: the prompt moved, nothing else did."""
    return _pair(_recorded(), _recorded(prompt_set="prompts-b"))


# ── confounders ──────────────────────────────────────────────────────────────


def test_a_model_swap_is_not_a_comparison() -> None:
    """The defect, in the form that would have shipped a wrong number.

    Drop the ``settings_comparable`` call from ``comparison_quotable`` and this goes red.
    """
    a, b = _pair(_recorded(), _recorded(prompt_set="prompts-b", chat_model="another-model"))
    ok, _ra, _rb, _ctx, settings = comparison_quotable(a, b, under_test=_UNDER_TEST)

    assert settings.result is Result.failed
    assert not ok, "two configs on different models were published as a comparison"
    assert "chat_model" in settings.detail


def test_a_real_config_pair_still_compares() -> None:
    """The fence. A check refusing every pair would satisfy the test above and be useless.

    This is the shape of v3-fold → v4: one setting that affects results moved, on purpose, and it is the
    declared setting under test.
    """
    ok, _ra, _rb, _ctx, settings = comparison_quotable(*_treated(), under_test=_UNDER_TEST)

    assert settings.result is Result.passed, settings.detail
    assert ok


def test_an_operational_or_scope_difference_is_not_fatal() -> None:
    """Only settings that affect results may block. Moving a non-setting left the wider name set green."""
    a, b = _pair(
        _recorded(),
        _recorded(
            prompt_set="prompts-b",
            git_sha="beefbeef",          # operational
            working_tree_dirty=True,     # operational
            split="dev",                 # scope
        ),
    )
    _ok, _ra, _rb, _ctx, settings = comparison_quotable(a, b, under_test=_UNDER_TEST)

    assert settings.result is Result.failed, settings.detail
    assert "git_sha" in settings.detail


def test_a_setting_missing_on_one_side_cannot_evaluate() -> None:
    """Absent on one side is a difference in what is *known*, not a difference in value."""
    thin = _recorded(prompt_set="prompts-b")
    del thin["embedding_model"]
    ok, _ra, _rb, _ctx, settings = comparison_quotable(
        *_pair(_recorded(), thin), under_test=_UNDER_TEST
    )

    assert settings.result is Result.cannot_evaluate
    assert not ok
    assert "embedding_model" in settings.detail


def test_a_setting_absent_from_both_configs_is_not_silently_equal() -> None:
    """Both sides omitting a key is not agreement; ``get`` would return ``None`` and call it a match."""
    a_settings, b_settings = _recorded(), _recorded(prompt_set="prompts-b")
    del a_settings["sqlglot_version"]
    del b_settings["sqlglot_version"]
    result = settings_comparable(*_pair(a_settings, b_settings), under_test=_UNDER_TEST)

    assert result.result is Result.cannot_evaluate
    assert "sqlglot_version" in result.detail


def test_none_on_both_sides_is_agreement_not_ignorance() -> None:
    """Recorded ``None`` on both sides is agreement. Treating it as unmeasured would fail every real pair."""
    assert _recorded()["permitted_functions"] is None, "fixture no longer represents a real config"

    assert settings_comparable(*_treated(), under_test=_UNDER_TEST).result is Result.passed


# ── the setting under test itself (audit D9) ──────────────────────────────────────────


def test_a_pair_whose_declared_under_test_did_not_move_is_a_replicate() -> None:
    """Identical result-affecting settings are a replicate. Distinct hashes are retrieval noise."""
    result = settings_comparable(*_pair(_recorded(), _recorded()), under_test=_UNDER_TEST)

    assert result.result is Result.failed
    assert "replicate" in result.detail
    assert "prompt_set" in result.detail


def test_a_pair_with_no_declared_under_test_cannot_be_evaluated() -> None:
    """Nobody naming the change is not a pass, and it is not the same as nothing having changed."""
    result = settings_comparable(*_pair(_recorded(), _recorded()))

    assert result.result is Result.cannot_evaluate
    assert "no treatment declared" in result.detail


def test_a_under_test_that_is_not_a_affects_results_setting_is_refused() -> None:
    """A category error, refused rather than ignored.

    Silently dropping an unknown name would let a typo — ``prompt_sett`` — read as "no setting under test
    declared" on one path and as a passing comparison on another.
    """
    result = settings_comparable(*_treated(), under_test=frozenset({"prompt_sett"}))

    assert result.result is Result.cannot_evaluate
    assert "prompt_sett" in result.detail


def test_a_malformed_configs_file_is_a_loud_failure_and_not_an_undeclared_config(
    tmp_path, monkeypatch
) -> None:
    """A malformed configs file must raise. Swallowing it would report every comparison as unevaluable data."""
    from governed_bi.eval.report import summarise
    from governed_bi.registry import config_profiles

    broken = tmp_path / "configs.toml"
    broken.write_text('[config.v4]\nunder_test = ["prompt_sett"]\n', encoding="utf-8")
    monkeypatch.setattr(config_profiles, "CONFIGS_FILE", broken)
    config_profiles.load_config_profiles.cache_clear()
    try:
        with pytest.raises(ValueError, match="prompt_sett"):
            summarise(
                {
                    "v3_fold": _rows(2, settings=_recorded(), hash_prefix="a"),
                    "v4": _rows(2, settings=_recorded(prompt_set="p-b"), hash_prefix="b"),
                },
                pair=("v3_fold", "v4"),
            )
    finally:
        config_profiles.load_config_profiles.cache_clear()


def test_an_config_with_no_profile_still_reads_as_nobody_said_what_changed(
    tmp_path, monkeypatch
) -> None:
    """A well-formed file with no entry for this config is "nobody wrote a profile", not a malformed file."""
    from governed_bi.eval.report import summarise
    from governed_bi.registry import config_profiles

    fine = tmp_path / "configs.toml"
    fine.write_text(
        '[config.somebody_else]\nunder_test = ["prompt_set"]\n'
        'corpus_content_hash = "abc123"\nquestion_subset = "2:abc123abc123"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config_profiles, "CONFIGS_FILE", fine)
    config_profiles.load_config_profiles.cache_clear()
    try:
        summary = summarise(
            {
                "v3_fold": _rows(2, settings=_recorded(), hash_prefix="a"),
                "v4": _rows(2, settings=_recorded(prompt_set="p-b"), hash_prefix="b"),
            },
            pair=("v3_fold", "v4"),
        )
    finally:
        config_profiles.load_config_profiles.cache_clear()

    assert summary["comparison"]["treatment"] == []
    assert summary["comparison"]["quotable"] is False
