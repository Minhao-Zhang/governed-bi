"""The served surface runs production's suspect policy; the benchmark keeps its own.

``hard_block_suspect`` ships ``True`` and its ``why`` says that is "development and the
benchmark", with production soft-warning instead. Until 2026-08-19 ``api/graph_app.py``
constructed its ``GovernancePolicy`` without the field, so the *served* surface — a real
deployment answering real questions — silently ran the benchmark's arm.

**What it cost, on the live corpus.** ``plant_run_hour_readings.tag_description`` is marked
``suspect``, and the same column's body states it is the only bridge from a historian tag to
an asset. Asked which chillers ran the most hours, the agent reached for ``sample_rows`` on
that column — the right tool for the question — and was refused at COLUMNS. It fell back to
reading bare tag names (``BOP_HMI_DFP4_TOT_RT``, ``BOP_HMI_OPI_B1_GAS_RT``) out of which no
equipment type can be recovered, spent all five ``run_query`` attempts probing, and hit the
cap with no answer. The descriptions settle the question outright: 81 tags, all boilers and
pumps across two steam plants, not one chiller. The turn read as a model that could not
budget its attempts; it was a model denied the one column that carried the meaning.

**These tests pin the difference, not just the value.** Asserting only that serve is ``False``
would pass if someone "fixed" the knob default and made the benchmark soft-warn too — which
would let a measured number rest on a column the corpus says not to trust. The benchmark half
is the load-bearing half.
"""

from __future__ import annotations

from pathlib import Path

from governed_bi.corpus.analyst import analyst_corpus_from_keys
from governed_bi.govern.check import check
from governed_bi.govern.guard import BI_SCOPE_RULE_ID
from governed_bi.govern.policy import GovernancePolicy

_LICENSED = frozenset({"main.readings"})
_SUSPECT = frozenset({"main.readings.tag_description"})


def _verdict(policy: GovernancePolicy) -> dict:
    """One statement reading one suspect column, judged under ``policy``."""
    return check(
        "SELECT r.tag_description FROM main.readings r",
        licensed=_LICENSED,
        corpus=analyst_corpus_from_keys(allowed=_SUSPECT, suspect=_SUSPECT),
        policy=policy,
    )


def test_the_served_policy_soft_warns_on_a_suspect_column() -> None:
    """Production semantics, reached through the seam the server actually uses."""
    from governed_bi.api.graph_app import serve_policy

    policy = serve_policy(Path("."))

    assert policy.hard_block_suspect is False, (
        "the served surface hard-blocks suspect columns, which is the benchmark's arm — a "
        "deployment then refuses the column its own corpus calls the only bridge to an asset"
    )
    assert _verdict(policy)["passed"] is True


def test_the_benchmark_still_hard_blocks_a_suspect_column() -> None:
    """The other half. ``eval/arms.py`` and the ``tools/`` drivers build their own policy and
    pass no ``hard_block_suspect``, so they inherit the ``True`` default — and must keep it.
    A measured number resting on a column the corpus marks unreliable is the thing this knob
    exists to prevent, so relaxing the *default* to fix the server would be the wrong repair.
    """
    verdict = _verdict(GovernancePolicy())

    assert verdict["passed"] is False, (
        "the default policy no longer refuses a suspect column; if this was relaxed to fix "
        "the served surface, revert it — serve_policy() passes the flag explicitly"
    )
    assert verdict["reason_code"] == "r_column_suspect"


def test_the_served_policy_keeps_the_scope_gate_and_changes_nothing_else() -> None:
    """The suspect flag is the only intended difference. ``run_query_attempt_cap`` in
    particular is asserted at its default: the capped turn above was diagnosed as a policy
    problem, not a budget one, and raising the cap was considered and declined.
    """
    from governed_bi.api.graph_app import serve_policy

    policy = serve_policy(Path("."))

    assert policy.guard_rules_enabled == {BI_SCOPE_RULE_ID: True}
    assert policy.run_query_attempt_cap == GovernancePolicy().run_query_attempt_cap
