"""Cancelling a clarification, and why the ledger's answer depends on ``basis``.

A user who abandons a question they no longer want asked is not declining to answer it and is not
handing it to an admin. Before this existed there was no third option at all: ``ask_user`` locks
the composer, ``conversation.tsx`` removes Stop while a clarification is pending, and this fork
replaced upstream's Decline button with Defer and then hid Defer for ``ranking_ambiguity`` — so
the one basis with no admin-answerable question was also the one with no way out but answering.

**The rule under test is that the two bases end differently.**

* ``ranking_ambiguity`` — "which metric does 'best' mean" is a per-user judgment call, and this
  fork's whole reason for the ``basis`` field is that such a question must never reach an admin.
  An abandoned one is noise in their queue, so cancelling drops it: ``cancelled``.
* ``data_definition`` — "how do you count an active app" has one answer for everyone. It is worth
  answering whether or not the user who triggered it waited, so cancelling leaves it ``open``.

The rule is read from the record's own ``basis`` rather than passed in by the caller, and
:func:`test_the_caller_cannot_override_the_basis_rule` is what keeps it that way: a second caller
that thought it knew better would be a second rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.curator.clarifications import (
    ClarificationNotFound,
    ClarificationRecord,
    ClarificationRecordStatus,
    cancel_clarification,
    load_clarifications,
    write_clarifications,
)

HOMEWORK = (ClarificationRecordStatus.open, ClarificationRecordStatus.deferred)


def _seed(root: Path, *, basis: str | None, status: str = "open") -> ClarificationRecord:
    record = ClarificationRecord(
        id=f"clar-{basis or 'none'}-01",
        scope=f"live_chat:clar-{basis or 'none'}-01",
        question="Which apps are best?",
        status=ClarificationRecordStatus(status),
        source="live_chat",
        basis=basis,
    )
    write_clarifications(root, [record])
    return record


def test_cancelling_a_ranking_question_drops_it_from_the_admin_queue(tmp_path: Path) -> None:
    record = _seed(tmp_path, basis="ranking_ambiguity")

    updated = cancel_clarification(tmp_path, record.id)

    assert updated is not None
    assert updated.status is ClarificationRecordStatus.cancelled
    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk.status is ClarificationRecordStatus.cancelled
    assert on_disk.status not in HOMEWORK, "a cancelled ranking question is not admin homework"


def test_cancelling_a_definition_question_leaves_it_for_the_admin(tmp_path: Path) -> None:
    """The asymmetry, stated from the admin's side: this one stays on their list."""
    record = _seed(tmp_path, basis="data_definition")

    updated = cancel_clarification(tmp_path, record.id)

    assert updated is not None
    assert updated.status is ClarificationRecordStatus.open
    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk.status in HOMEWORK


def test_a_record_with_no_basis_is_treated_as_a_definition_question(tmp_path: Path) -> None:
    """Missing ``basis`` fails toward keeping the question, matching every other gate in this
    fork that reads the field (``mine_corpus``'s and ``fold_ledger_answer_into_corpus``'s both
    treat a missing basis as ``data_definition``-eligible). Dropping an admin's homework on the
    strength of an absent field is the expensive direction to be wrong in.
    """
    record = _seed(tmp_path, basis=None)

    updated = cancel_clarification(tmp_path, record.id)

    assert updated is not None
    assert updated.status is ClarificationRecordStatus.open


def test_the_caller_cannot_override_the_basis_rule(tmp_path: Path) -> None:
    """There is no ``basis=`` argument. The rule reads the stored record, so one function is the
    only place that decides, and a route cannot grow a second opinion.
    """
    import inspect

    params = set(inspect.signature(cancel_clarification).parameters)
    assert "basis" not in params, (
        "cancel_clarification grew a basis argument, which lets a caller decide what the ledger "
        "does with an abandoned question; the record already carries the answer"
    )


def test_cancelling_an_answered_record_is_refused(tmp_path: Path) -> None:
    """An answer already folded into the corpus cannot be un-asked. Cancelling would strand the
    asset under an id hashed from a question the ledger no longer claims was asked.
    """
    record = _seed(tmp_path, basis="ranking_ambiguity", status="answered")

    with pytest.raises(ValueError, match="answered"):
        cancel_clarification(tmp_path, record.id)

    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk.status is ClarificationRecordStatus.answered


def test_cancelling_an_unknown_id_raises(tmp_path: Path) -> None:
    """Unlike ``close_live_clarification``, which tolerates a missing row because it runs inside a
    turn that has already produced an answer, this one is a deliberate user action on a row the
    UI just rendered. A silent no-op would leave the prompt on screen with nothing explaining it.
    """
    _seed(tmp_path, basis="ranking_ambiguity")

    with pytest.raises(ClarificationNotFound):
        cancel_clarification(tmp_path, "clar-does-not-exist")
