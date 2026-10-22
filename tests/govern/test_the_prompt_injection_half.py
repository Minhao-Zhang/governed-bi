"""Adversarial recall for the five deterministic rules. See ADR 0006. The benign rate is the benchmark conformance test.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def guard_report():
    """The half, run once. No model, no network, no database, so one run is every run."""
    from governed_bi.govern.adversarial_run import run_guard_suite

    return run_guard_suite()


# ── the shape of the half ─────────────────────────────────────────────────────


def test_the_half_declares_both_sides_and_covers_every_rule(guard_report) -> None:
    """Per rule, not per set: one rule can catch everything while four catch nothing. See ADR 0006."""
    from governed_bi.govern.policy import GUARD_RULE_IDS

    attacks, benign = guard_report.of_kind("attack"), guard_report.of_kind("benign")
    assert attacks and benign

    aimed = {r.case.expect_rule for r in attacks}
    assert aimed == GUARD_RULE_IDS, (
        f"attacks are aimed at {sorted(aimed)} but the rules are {sorted(GUARD_RULE_IDS)}. "
        "A rule enabled on the served surface with no attack behind it is the state ADR 0006 "
        "OQ3 held all five out of"
    )


def test_every_case_explains_itself(guard_report) -> None:
    """An unexplained case cannot be told from a leftover, so the loader refuses it."""
    for result in guard_report.results:
        assert result.case.why.strip(), result.case.id
        assert result.case.origin.strip(), result.case.id


# ── what the rules are worth ──────────────────────────────────────────────────


def test_no_attack_reaches_the_model(guard_report) -> None:
    """Anything but ``blocked`` is routed onward, so a clear result reached the model."""
    reached = guard_report.with_status("bypassed", "attack")
    assert not reached, [
        (r.case.id, r.case.expect_rule, r.detail) for r in reached
    ]


def test_no_attack_is_blocked_by_the_wrong_rule(guard_report) -> None:
    """Blocked by the wrong rule leaves the rule written for that payload untested."""
    wrong = guard_report.with_status("misattributed", "attack")
    assert not wrong, [(r.case.id, r.detail) for r in wrong]


def test_no_rule_fails_open(guard_report) -> None:
    """A raised predicate is routed onward, so it is counted apart from a block. See ADR 0006."""
    crashed = guard_report.with_status("guardrail_error")
    assert not crashed, [(r.case.id, r.detail) for r in crashed]


def test_no_benign_question_is_refused(guard_report) -> None:
    """Ordinary words that the patterns must not treat as attacks, including ``override`` and ``instructions`` out of
    order."""
    refused = [
        r
        for r in guard_report.with_status("false_refusal", "benign")
        if not r.case.known_false_refusal
    ]
    assert not refused, [(r.case.id, r.detail) for r in refused]


# ── the check, and the driver that prints it ───────────────────────────────────


def test_the_report_carries_a_denominator_for_every_rate(guard_report) -> None:
    """An empty population is unmeasured, not a perfect score."""
    from governed_bi.govern.adversarial_run import guard_report_lines

    unowned = guard_report.rule_recall("g_bi_scope")
    with pytest.raises(TypeError):
        bool(unowned)

    lines = guard_report_lines(guard_report)
    assert any("per-rule recall" in line for line in lines)
    assert any("(10/10)" in line or "/10)" in line for line in lines), lines


def test_the_half_runs_with_every_rule_on_whatever_the_caller_passes() -> None:
    """Every rule is on, whatever policy the caller passes. A disabled rule would be reported as a bypass."""
    from governed_bi.govern.adversarial_run import run_guard_suite
    from governed_bi.govern.policy import GovernancePolicy

    disabled = GovernancePolicy(guard_rules_enabled={})
    report = run_guard_suite(policy=disabled)

    assert not report.with_status("bypassed", "attack"), (
        "a caller's disabled policy reached the suite; the bypass rate now describes that "
        "policy rather than the rules"
    )
