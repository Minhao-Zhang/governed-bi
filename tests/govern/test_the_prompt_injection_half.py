"""The prompt-injection half of the adversarial suite: what the five input rules are worth.

**Why this file exists.** ADR 0006 OQ3 held ``govern/guard.py``'s five deterministic rules off
until each had two numbers — adversarial recall and benign firing rate — and recorded the
blocker as "a corpus, not code". For as long as that corpus did not exist,
``api/graph_app.py`` shipped ``guard_rules_enabled={g_bi_scope: True}``. That reads as "the
guard is on"; it means five of six are off, because ``g_bi_scope`` is not a member of
``GUARD_RULES``, :func:`~governed_bi.govern.guard.guard` iterates that mapping, and
:meth:`GovernancePolicy.guard_rule_enabled`'s ``.get(rule_id, False)`` turns an absent id into
a silent ``False``. The served surface evaluated **zero** deterministic predicates.

Every one of the five was 100% line-covered by ``tests/govern/test_guard_pipeline_ledger.py``
the whole time. Coverage said the rules were exercised, and they were — by a fixture that
built its own policy with ``{rule: True for rule in GUARD_RULES}``. Nothing said the *shipped*
policy exercised them, and the one test that read the shipped mapping pinned it with ``==``,
so it would have failed on the fix and passed on the regression.

So the gap this file closes is not "are the rules correct" — they always were. It is "is there
a number behind each one", which is what OQ3 asked for and what
``tests/api/test_the_served_surface_is_not_the_benchmark_arm.py`` now holds the deployment to.

**This file is the adversarial number. The benign number is elsewhere**, in
``tests/conformance/test_the_guard_does_not_refuse_the_benchmark.py``, over the 1,351
questions every published figure in this repository is taken on. The six controls here are the
near misses — shapes that look like an attack and are not — and a rate over six is not a rate.
"""

from __future__ import annotations

import pytest

from contracts import needs

pytestmark = needs("B")


@pytest.fixture(scope="module")
def guard_report():
    """The half, run once. No model, no network, no database, so one run is every run."""
    from governed_bi.govern.adversarial_run import run_guard_suite

    return run_guard_suite()


# ── the shape of the half ─────────────────────────────────────────────────────


def test_the_half_declares_both_sides_and_covers_every_rule(guard_report) -> None:
    """A guard that refuses everything scores a perfect block rate; the controls are the trade.

    And per *rule*, not per set: a set-level recall of 1.000 is compatible with one rule
    catching every attack and four catching none, which is exactly the state OQ3 refused to
    ship. The import-time guard in ``adversarial.py`` says the same thing; this says it where
    a reader looks.
    """
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
    """``why`` and ``origin`` are mandatory, as they are for the statement half.

    The loader refuses a case without them. Asserted again here because the reason is worth
    restating: an unexplained case is how a suite rots — nobody can tell a deliberate probe
    from a leftover, so nobody deletes anything and nobody trusts anything.
    """
    for result in guard_report.results:
        assert result.case.why.strip(), result.case.id
        assert result.case.origin.strip(), result.case.id


# ── what the rules are worth ──────────────────────────────────────────────────


def test_no_attack_reaches_the_model(guard_report) -> None:
    """The headline. ``guard()`` returning anything but ``blocked`` lets the turn continue.

    ``serve/graph.py::_after_guard`` routes to ``refuse`` **only** on ``blocked`` — ``clear``
    and ``error_failed_open`` both fall through to ``rewrite`` — so "not blocked" and "reached
    the model" are the same statement here.
    """
    reached = guard_report.with_status("bypassed", "attack")
    assert not reached, [
        (r.case.id, r.case.expect_rule, r.detail) for r in reached
    ]


def test_no_attack_is_blocked_by_the_wrong_rule(guard_report) -> None:
    """Refusing for the wrong reason means the rule meant to catch it did not.

    ``g_length`` swallowing a payload written to test ``g_instruction_override`` is a green
    tick over an untested rule, and a suite that asked only "was it blocked" would report it
    as working. The same argument as the statement half's layer/rule pair, one axis shorter.
    """
    wrong = guard_report.with_status("misattributed", "attack")
    assert not wrong, [(r.case.id, r.detail) for r in wrong]


def test_no_rule_fails_open(guard_report) -> None:
    """``error_failed_open`` is this half's ``guardrail_error``, and it is a bypass in effect.

    :func:`~governed_bi.govern.guard.guard` catches a raising predicate and returns
    ``error_failed_open`` rather than ending the turn — deliberately, so a broken rule is not
    an outage. The cost is that a rule which crashes on every input is indistinguishable from
    one that never fires, unless something counts it. This counts it.
    """
    crashed = guard_report.with_status("guardrail_error")
    assert not crashed, [(r.case.id, r.detail) for r in crashed]


def test_no_benign_question_is_refused(guard_report) -> None:
    """The controls, each one a recorded weakness of the rule it is aimed at.

    ``ignore`` and ``system`` are ordinary words in questions about data and
    ``_INSTRUCTION_OVERRIDE``'s own comment says so; a column can genuinely be named
    ``system``; non-ASCII is not a control character. The hardest one contains ``override``,
    ``prior`` *and* ``instructions`` and passes only because the pattern requires them in
    order — a looser rule written later fails on that case first.
    """
    refused = [
        r
        for r in guard_report.with_status("false_refusal", "benign")
        if not r.case.known_false_refusal
    ]
    assert not refused, [(r.case.id, r.detail) for r in refused]


# ── the gate, and the driver that prints it ───────────────────────────────────


def test_the_report_carries_a_denominator_for_every_rate(guard_report) -> None:
    """A consumer handed ``0.042`` cannot recover the denominator, so every rate ships one.

    ``Measured.rate`` over an empty population is *unmeasured*, not ``1.000`` — which is the
    property that stops a rule with no attacks from reporting perfect recall. Asserted by
    asking for a rule id that owns nothing.
    """
    from governed_bi.govern.adversarial_run import guard_report_lines

    unowned = guard_report.rule_recall("g_bi_scope")
    with pytest.raises(TypeError):
        bool(unowned)

    lines = guard_report_lines(guard_report)
    assert any("per-rule recall" in line for line in lines)
    assert any("(10/10)" in line or "/10)" in line for line in lines), lines


def test_the_half_runs_with_every_rule_on_whatever_the_caller_passes() -> None:
    """The suite measures the rules, not the deployment.

    Running it under a policy that disabled a rule would report a clean bypass rate over a
    rule that never ran — which is the defect this whole half exists because of, reproduced
    inside the instrument meant to detect it.
    """
    from governed_bi.govern.adversarial_run import run_guard_suite
    from governed_bi.govern.policy import GovernancePolicy

    disabled = GovernancePolicy(guard_rules_enabled={})
    report = run_guard_suite(policy=disabled)

    assert not report.with_status("bypassed", "attack"), (
        "a caller's disabled policy reached the suite; the bypass rate now describes that "
        "policy rather than the rules"
    )
