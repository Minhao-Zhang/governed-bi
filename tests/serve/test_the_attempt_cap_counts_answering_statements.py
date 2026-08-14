"""Which ledger rows the attempt cap charges for.

Split out of ``test_agent_tools_hitl.py`` at the 2026-08-14 upstream merge: keeping both sides'
content put that file at 1 034 lines, over ADR 0005 §6's 1 000-line hard cap. The subject here is
``serve/agent_state.py``'s ``_chargeable`` / :class:`~governed_bi.serve.agent_state.AttemptBook`,
not the HITL tool surface, so its own file is the better home either way.

The behaviour was this fork's fix (``e61bd14``) and is now upstream's, in a stricter form:
``_chargeable`` drops introspection rows *and* the cap row itself. This test survived the merge
unchanged and now covers upstream's implementation, which is the useful outcome -- the fork's
patch was superseded, the property it asserted was not.
"""

from __future__ import annotations


def test_sample_rows_attempts_do_not_count_against_the_run_query_cap() -> None:
    """The cap is about answering statements, not every governed call the turn made.

    ``sample_rows`` writes a real ledger row per call (``path="sample"``) so the audit
    surface shows it happened — but a model that spends three calls checking which of two
    similarly-named columns (a decoy-column table, say) is the right join key before
    writing SQL has answered nothing yet. Before this fix, those three ledger rows landed
    in the same ``attempts_by_call`` mapping ``run_query``'s cap counts over, so the first
    *actual* ``run_query`` call — the model's first real attempt to answer — was refused
    with "attempt limit reached" without ever running. Live-observed on "Who are our best
    customers?" against a table with both ``kunde_id`` and ``transaktions_kunde_id``.
    """
    from governed_bi.serve.agent_state import AttemptBook

    committed = {
        "sample-0": {"path": "sample", "passed": True, "reason_code": "passed"},
        "sample-1": {"path": "sample", "passed": True, "reason_code": "passed"},
        "sample-2": {"path": "sample", "passed": True, "reason_code": "passed"},
    }
    book = AttemptBook(3)
    assert book.admit(committed, "rq-0") is True, (
        "three introspection attempts must not exhaust a cap meant for answering statements"
    )

    # An actual answering attempt still counts, and the cap still bites once three of
    # *those* have run — the cap is narrowed to the right population, not disabled.
    committed_with_one_real_attempt = {
        **committed,
        "rq-0": {"path": "agent", "passed": False, "reason_code": "blocked"},
    }
    book2 = AttemptBook(1)
    assert book2.admit(committed_with_one_real_attempt, "rq-1") is False, (
        "a cap of 1 must still refuse a second answering attempt"
    )
