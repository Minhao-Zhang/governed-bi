"""A seed-only pair must not be quotable. Distinct context hashes are retrieval noise."""

from __future__ import annotations

import json
import pathlib

import pytest

RUNS = pathlib.Path(__file__).resolve().parents[2] / "runs" / "eval"
NULL_PAIR = (
    RUNS / "proxy_full_opus_high_corpus30872d3.jsonl",
    RUNS / "proxy_full_opus_high_corpus30872d3_run2.jsonl",
)

#: ``runs/`` is gitignored, so CI has no artifacts. A missing file is skipped: an xfail would look like a defect.
_ARTIFACTS = pytest.mark.skipif(
    not all(p.exists() for p in NULL_PAIR),
    reason=(
        "the designated null replicate is not on this machine; `runs/` is gitignored, so this control "
        "runs where the artifacts are and is silent where they are not"
    ),
)


def _population(label: str, path: pathlib.Path):
    from governed_bi.measure.population import Population

    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return Population.of(
        label,
        [
            {
                # No `str()` around a possibly-absent id: `Population.of` refuses a row it cannot
                # identify, and `str(None)` would hand it the string "None" and defeat that.
                "question_id": r["question_id"],
                "context_hash": r.get("context_hash"),
                "corpus_content_hash": r.get("corpus_content_hash"),
                "settings_resolved": r.get("settings_resolved"),
                "correct": r.get("correct"),
                "outcome": r.get("outcome"),
            }
            for r in rows
        ],
    )


def _pair():
    # Distinct labels: both stems share their first 24 characters, so truncating them made the
    # control's failure message name one config twice — in a test whose only product is that message.
    return _population("run1", NULL_PAIR[0]), _population("run2", NULL_PAIR[1])


@_ARTIFACTS
def test_the_cross_config_judgement_refuses_a_seed_only_pair() -> None:
    """The judgement is the declared setting, not the hash. A seed-only pair must not pass."""
    from governed_bi.eval.report import Result, settings_comparable

    check = settings_comparable(*_pair())
    assert check.result is not Result.passed, (
        f"the cross-config judgement passed on two configs that differ only by a random seed: "
        f"{check.render()}"
    )


@_ARTIFACTS
def test_context_hash_no_longer_claims_to_detect_a_under_test() -> None:
    """The hash check may pass on a null pair. It must not be read as "the setting changed"."""
    from governed_bi.eval.report import Result, context_hashes_distinct, settings_comparable

    a, b = _pair()
    assert context_hashes_distinct(a, b).result is Result.passed
    assert settings_comparable(a, b).result is not Result.passed


@_ARTIFACTS
def test_a_seed_only_pair_is_not_quotable_today_and_must_stay_that_way() -> None:
    """These artifacts predate the corpus hash, so the pair is refused for that, and must stay unquotable."""
    from governed_bi.eval.report import comparison_quotable

    ok, _results_a, _results_b, _ctx, _settings = comparison_quotable(*_pair())
    assert ok is False, "a seed-only null pair became quotable"


@_ARTIFACTS
def test_the_null_pair_is_still_a_null() -> None:
    """One row is not the config. A change past the first row has to fail the null check too."""
    a, b = _pair()
    shared = a.units & b.units
    assert len(shared) == 1351, f"the configs share {len(shared)} questions, not the full set"

    by_a, by_b = a.by_unit(), b.by_unit()
    differing = [
        qid for qid in sorted(shared) if by_a[qid].get("settings_resolved") != by_b[qid].get("settings_resolved")
    ]
    assert not differing, (
        f"{len(differing)} row(s) record different settings, so this is no longer a null replicate and "
        f"the control above is measuring something else: {differing[:5]}"
    )


# The artifact pair short-circuits on missing keys, so it never reaches the replicate check.
# Synthetic rows are what can express that judgement.

_ALL_SETTINGS_AGREE = None  # populated per test from the register, so a new setting cannot be forgotten


def _synthetic(label: str, settings: dict):
    from governed_bi.measure.population import Population

    return Population.of(
        label,
        [
            {"question_id": f"q{i}", "outcome": "answered", "correct": True, "settings_resolved": settings}
            for i in range(3)
        ],
    )


def _every_affects_results_setting() -> dict:
    """A complete, well-formed `settings_resolved`, so nothing short-circuits on absence."""
    from governed_bi.registry.settings import Unset, setting_default, setting_names

    out = {}
    for key in setting_names():
        value = setting_default(key)
        out[key] = None if isinstance(value, Unset) else value
    return out


def test_two_configs_with_every_setting_identical_are_a_replicate_not_a_comparison() -> None:
    """The judgement D9 exists to make, reached rather than short-circuited.

    Deleting the replicate check leaves every artifact-backed control above green; it does not leave
    this one green. That is the whole difference between a check and a check with a control.
    """
    from governed_bi.eval.report import Result, settings_comparable

    settings = _every_affects_results_setting()
    check = settings_comparable(_synthetic("a", settings), _synthetic("b", dict(settings)))
    assert check.result is not Result.passed, (
        f"two configs with every comparability setting identical were certified as a comparison: "
        f"{check.render()}"
    )
    assert "absent" not in check.render(), (
        "the result came from a missing setting rather than from the judgement, which is the defect "
        f"this test exists to exclude: {check.render()}"
    )

    # With no setting under test the check exits before the replicate check.
    # The first assertion alone stayed green with that check deleted.
    key = "route_top_n" if "route_top_n" in settings else sorted(settings)[0]
    declared = settings_comparable(
        _synthetic("a", settings), _synthetic("b", dict(settings)), under_test=frozenset({key})
    )
    assert declared.result is Result.failed, (
        f"a declared treatment identical on both configs is a replicate, not a comparison: "
        f"{declared.render()}"
    )
    assert "replicate" in declared.render()


def test_one_moved_setting_outside_the_declared_under_test_is_a_confounder() -> None:
    """The paired positive: the check must still be able to *pass*, and to refuse a confounder.

    Without this, a `settings_comparable` that refuses everything satisfies the test above.
    """
    from governed_bi.eval.report import Result, settings_comparable

    settings = _every_affects_results_setting()
    moved = dict(settings)
    key = "route_top_n" if "route_top_n" in settings else sorted(settings)[0]
    moved[key] = (settings[key] or 0) + 7 if isinstance(settings[key], (int, float)) else "moved"

    undeclared = settings_comparable(_synthetic("a", settings), _synthetic("b", moved))
    assert undeclared.result is Result.failed, (
        f"a setting moved outside any declared treatment is a confounder, not a comparison: "
        f"{undeclared.render()}"
    )
    declared = settings_comparable(
        _synthetic("a", settings), _synthetic("b", moved), under_test=frozenset({key})
    )
    assert declared.result is Result.passed, (
        f"the check cannot pass at all, so refusing a replicate proves nothing: {declared.render()}"
    )
