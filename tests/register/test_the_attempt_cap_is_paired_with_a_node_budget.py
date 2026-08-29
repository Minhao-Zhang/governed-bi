"""The attempt-cap / statement-timeout pairing, which was a comment until 2026-08-26.

``statement_timeout_ms``'s register note has always said ``run_query_attempt_cap`` x it =
5 x 120 s = 600 s, half of ``agent_node_timeout_s``, "so five statements can each time out and
still leave the other half for model calls". Nothing checked it, and the failure it describes is
silent: at cap 10 the product is the *entire* node budget, so the five extra attempts cannot be
spent on recoveries — the node's own wall clock stamps ``crashed`` first, and the arm reads as
evidence that raising the cap does not help. Every row of that arm is a real, correctly-recorded
``crashed``, so no gate downstream can catch it.

**These tests do not re-implement the rule.** The invariant is asserted at import by
``register/knobs.py::_assert_the_attempt_cap_fits_inside_the_node_budget``, following
``measure/gates.py``, ``govern/functions.py`` and ``register/facets.py``; what is exercised here
is the pure function it calls and the assertion itself, driven with values that violate it.
"""

from __future__ import annotations

import pytest

from governed_bi.register.knobs import (
    KNOB_REGISTER,
    Role,
    attempt_cap_pairing_problem,
    knob_default,
)

#: The three names the pairing relates, read off the register rather than typed here: a test
#: holding its own copy of a declared default is the "instrument asserting a constant against
#: itself" shape this repository has paid for eight times.
CAP = knob_default("run_query_attempt_cap")
STATEMENT_MS = knob_default("statement_timeout_ms")
NODE_S = knob_default("agent_node_timeout_s")


def test_the_shipped_defaults_hold_the_pairing() -> None:
    """5 x 120 s = 600 s against a 1 200 s node budget: exactly half, which is the rule."""
    assert attempt_cap_pairing_problem(
        run_query_attempt_cap=CAP,
        statement_timeout_ms=STATEMENT_MS,
        agent_node_timeout_s=NODE_S,
    ) is None
    assert CAP * STATEMENT_MS * 2 == NODE_S * 1000, (
        "the shipped defaults no longer sit at exactly half the node budget. That is allowed by "
        "the check (which asks for 'at most half'), but the register note claims the equality, "
        "so one of the two has to move."
    )


def test_the_cap_the_operator_wants_is_refused_at_the_shipped_timeouts() -> None:
    """Cap 10 x 120 s = 1 200 s, which is the whole node budget rather than half of it.

    The message has to name all three knobs. A refusal that said only "too big" would send the
    reader to the cap, and the cap is the one value they deliberately changed.
    """
    problem = attempt_cap_pairing_problem(
        run_query_attempt_cap=10,
        statement_timeout_ms=STATEMENT_MS,
        agent_node_timeout_s=NODE_S,
    )

    assert problem is not None, "cap 10 at the shipped timeouts consumes the entire node budget"
    for knob in ("run_query_attempt_cap", "statement_timeout_ms", "agent_node_timeout_s"):
        assert knob in problem, f"the refusal does not name {knob}"
    assert "crashed" in problem, (
        "the refusal does not say what the extra attempts turn into, which is the whole reason "
        "the arm would be misread"
    )


def test_the_cap_is_allowed_once_the_node_budget_is_raised_with_it() -> None:
    """The arm ``arms.toml`` calls ``licensed_pre_budget_cap10``. Doubling both keeps the ratio,
    and the check must not stand in the way of the configuration it exists to demand."""
    assert attempt_cap_pairing_problem(
        run_query_attempt_cap=10,
        statement_timeout_ms=STATEMENT_MS,
        agent_node_timeout_s=NODE_S * 2,
    ) is None


def test_an_unbounded_statement_is_a_problem_and_not_a_vacuous_pass() -> None:
    """``datasource/postgres.py`` issues ``SET statement_timeout`` only for a positive value, so
    zero means a governed statement has no server-side bound at all. Multiplying it by the cap
    gives nil, which would clear every budget — the arithmetic reads as safe and the
    configuration is the least safe on offer."""
    problem = attempt_cap_pairing_problem(
        run_query_attempt_cap=CAP,
        statement_timeout_ms=0,
        agent_node_timeout_s=NODE_S,
    )
    assert problem is not None and "statement_timeout_ms" in problem


def test_the_import_time_assertion_is_the_thing_that_fires() -> None:
    """Not the pure function — the guard that runs on every import of the register.

    Driven through ``GOVERNED_BI_AGENT_NODE_TIMEOUT_S`` because that is the reachable half: the
    assertion takes ``agent_node_timeout_s`` env-first (``serve/graph.py`` does too), and
    ``run_query_attempt_cap`` has no ``env_var``, so the other half of the pairing can only be
    moved by editing the register. Lowering the node budget under a fixed cap is the same
    violation seen from the other end, and it proves the call site is wired.
    """
    from governed_bi.register import knobs

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GOVERNED_BI_AGENT_NODE_TIMEOUT_S", "400")
        with pytest.raises(AssertionError, match="run_query_attempt_cap"):
            knobs._assert_the_attempt_cap_fits_inside_the_node_budget()

    # And it is quiet on the configuration the register actually ships.
    knobs._assert_the_attempt_cap_fits_inside_the_node_budget()


def test_all_three_paired_knobs_are_comparability_roled() -> None:
    """Raising any one of them makes a new arm, which is what lets ``arms.toml`` name two of
    them as ``licensed_pre_budget_cap10``'s treatment. An operational role on any of the three
    would let a run move a paid arm's budget and hash identically to its control."""
    by_name = {k.name: k for k in KNOB_REGISTER}
    for name in ("run_query_attempt_cap", "statement_timeout_ms", "agent_node_timeout_s"):
        assert by_name[name].role is Role.comparability, f"{name} is no longer comparability"
