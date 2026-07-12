"""Tests for the D12 clarification protocol: the ``Clarification`` block on the
Audit tier and the ``accept_answer`` write primitive.

Covers the happy-path answer round trip, input immutability, the error guards,
and the loader contract (an open question never reaches the server view because
it lives on the never-served Audit tier)."""

from __future__ import annotations

import pytest

from governed_bi.corpus import (
    Clarification,
    ClarificationStatus,
    Column,
    Corpus,
    TableAsset,
    accept_answer,
)
from governed_bi.corpus.schemas import (
    Audit,
    ColumnRole,
    LogicalType,
    Provenance,
    ProvenanceSource,
    ProvenanceStatus,
    Reliability,
    ReliabilityStatus,
)


def _column_with_open_question() -> Column:
    """A column the curator could not confidently describe: a low-confidence
    provisional guess on the Inference tier, plus an open clarification on Audit."""
    return Column(
        physical_name="c_1",
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=False,
        is_unique=True,
        # provisional guess (D12: Inference tier + low confidence + suspect caveat)
        description="unknown id-looking column?",
        confidence=0.2,
        reliability=Reliability(status=ReliabilityStatus.suspect, note="UNRELIABLE guess"),
        audit=Audit(
            provenance=Provenance(
                source=ProvenanceSource.curator,
                status=ProvenanceStatus.proposed,
                model="gpt-x",
            ),
            clarification=Clarification(
                question="What does c_1 identify?", asked_by="curator"
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# Happy path: answer round trip
# --------------------------------------------------------------------------- #


def test_accept_answer_flips_clarification_and_applies_edits():
    col = _column_with_open_question()
    answered = accept_answer(
        col,
        by="alice@sme",
        answer="It is the customer id.",
        edits={"description": "customer id", "role": ColumnRole.key, "confidence": 0.95},
        reason="SME confirmed",
        at="2026-07-11T12:00:00Z",
    )

    # clarification is now answered, with the answer recorded
    clar = answered.audit.clarification
    assert clar.status is ClarificationStatus.answered
    assert clar.answer == "It is the customer id."
    assert clar.answered_by == "alice@sme"
    assert clar.at == "2026-07-11T12:00:00Z"

    # edits landed on the Inference tier
    assert answered.description == "customer id"
    assert answered.role is ColumnRole.key
    assert answered.confidence == 0.95

    # provenance re-stamped as a human sign-off, keeping the prior model field
    prov = answered.audit.provenance
    assert prov.source is ProvenanceSource.human
    assert prov.status is ProvenanceStatus.certified  # default
    assert prov.by == "alice@sme"
    assert prov.reason == "SME confirmed"
    assert prov.at == "2026-07-11T12:00:00Z"
    assert prov.model == "gpt-x"  # untouched


def test_accept_answer_status_override():
    col = _column_with_open_question()
    answered = accept_answer(
        col, by="bob", answer="ok", status=ProvenanceStatus.draft
    )
    assert answered.audit.provenance.status is ProvenanceStatus.draft
    assert answered.audit.provenance.source is ProvenanceSource.human


# --------------------------------------------------------------------------- #
# Immutability: the input is never mutated
# --------------------------------------------------------------------------- #


def test_accept_answer_does_not_mutate_input():
    col = _column_with_open_question()
    _ = accept_answer(
        col, by="alice", answer="the customer id", edits={"description": "customer id"}
    )

    assert col.audit.clarification.status is ClarificationStatus.open
    assert col.audit.clarification.answer is None
    assert col.description == "unknown id-looking column?"
    assert col.confidence == 0.2
    assert col.audit.provenance.source is ProvenanceSource.curator
    assert col.audit.provenance.status is ProvenanceStatus.proposed
    assert getattr(col.audit.provenance, "by", None) is None


# --------------------------------------------------------------------------- #
# Error guards
# --------------------------------------------------------------------------- #


def test_accept_answer_requires_audit():
    col = Column(
        physical_name="c_1",
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=False,
        is_unique=True,
    )  # audit is None
    with pytest.raises(ValueError):
        accept_answer(col, by="alice", answer="x")


def test_accept_answer_requires_a_clarification():
    col = Column(
        physical_name="c_1",
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=False,
        is_unique=True,
        audit=Audit(
            provenance=Provenance(
                source=ProvenanceSource.curator, status=ProvenanceStatus.proposed
            )
        ),  # no clarification
    )
    with pytest.raises(ValueError):
        accept_answer(col, by="alice", answer="x")


def test_accept_answer_rejects_already_answered():
    col = _column_with_open_question()
    col.audit.clarification.status = ClarificationStatus.answered
    with pytest.raises(ValueError):
        accept_answer(col, by="alice", answer="x")


def test_accept_answer_rejects_unknown_edit_field():
    col = _column_with_open_question()
    with pytest.raises(ValueError):
        accept_answer(
            col, by="alice", answer="x", edits={"not_a_real_field": True}
        )


# --------------------------------------------------------------------------- #
# Loader contract: an open question is never served
# --------------------------------------------------------------------------- #


def test_clarification_never_reaches_server_view():
    table = TableAsset(
        id="tbl_demo_orders",
        schema="demo",
        physical_name="orders",
        columns=[_column_with_open_question()],
        audit=Audit(
            provenance=Provenance(
                source=ProvenanceSource.curator, status=ProvenanceStatus.proposed
            ),
            clarification=Clarification(question="What grain is this table?"),
        ),
    )
    server_view = Corpus(assets=[table]).for_server()
    tbl_view = server_view.by_id("tbl_demo_orders")

    # Audit (hence every clarification) is stripped from the server view.
    assert tbl_view.audit is None
    assert all(c.audit is None for c in tbl_view.columns)
