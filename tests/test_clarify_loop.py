"""Tests for the curator clarification loop (WS3, Increment 2 of D12).

Deterministic and offline. ``emit_clarifications`` attaches an open question to
every gap; ``StaticResponder`` + ``default_parse`` answer it; and
``resolve_clarifications`` folds each answer back in via ``accept_answer``. The
final case checks the resolved assets still round-trip through
``write_corpus`` / ``load_corpus``.
"""

from __future__ import annotations

from governed_bi.corpus import (
    load_corpus,
    write_corpus,
)
from governed_bi.corpus.schemas import (
    ClarificationStatus,
    Column,
    LogicalType,
    ProvenanceSource,
    ProvenanceStatus,
    TableAsset,
)
from governed_bi.curator import (
    Responder,
    StaticResponder,
    default_parse,
    emit_clarifications,
    resolve_clarifications,
)

_COLUMN_QUESTION = (
    "What does column `orders.c_1` represent, and is it reliable for analysis?"
)
_TABLE_QUESTION = "What does table `orders` represent?"


def _gap_column() -> Column:
    """A Facts-only column with no description: a gap needing a clarification."""
    return Column(
        physical_name="c_1",
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=False,
        is_unique=True,
    )


def _described_column() -> Column:
    """A confidently-described column: not a gap, gets no clarification."""
    return Column(
        physical_name="c_ok",
        physical_type="TEXT",
        logical_type=LogicalType.string,
        nullable=True,
        is_unique=False,
        description="a well understood label",
        confidence=0.9,
    )


def _gap_table(columns: list[Column]) -> TableAsset:
    """A Facts-only table (no description/confidence): a table-level gap too."""
    return TableAsset(
        id="tbl_demo_orders",
        db="demo",
        physical_name="orders",
        columns=columns,
    )


# --------------------------------------------------------------------------- #
# emit_clarifications
# --------------------------------------------------------------------------- #


def test_emit_attaches_open_question_to_gap_column_and_table():
    [table] = emit_clarifications([_gap_table([_gap_column()])])

    # table-level gap -> templated table question, OPEN, asked_by default
    tbl_clar = table.audit.clarification
    assert tbl_clar.status is ClarificationStatus.open
    assert tbl_clar.question == _TABLE_QUESTION
    assert tbl_clar.asked_by == "curator"
    # a fresh curator/proposed stamp was created to hang the question on
    assert table.audit.provenance.source is ProvenanceSource.curator
    assert table.audit.provenance.status is ProvenanceStatus.proposed

    # column-level gap -> templated column question, OPEN
    col_clar = table.columns[0].audit.clarification
    assert col_clar.status is ClarificationStatus.open
    assert col_clar.question == _COLUMN_QUESTION
    assert col_clar.asked_by == "curator"


def test_emit_skips_non_gap_node():
    table = _gap_table([_described_column()])
    [emitted] = emit_clarifications([table])
    # the described column is above threshold and stays clarification-free
    assert emitted.columns[0].audit is None


def test_emit_respects_confidence_threshold():
    # a described column that is a gap ONLY because its confidence is low
    weak = _described_column().model_copy(update={"confidence": 0.5})
    weak_question = (
        "What does column `orders.c_ok` represent, and is it reliable for analysis?"
    )
    [table] = emit_clarifications([_gap_table([weak])])
    # 0.5 < 0.75 default -> a gap
    assert table.columns[0].audit.clarification.question == weak_question
    # a custom, lower threshold reclassifies it as not-a-gap
    [table2] = emit_clarifications([_gap_table([weak])], confidence_threshold=0.4)
    assert table2.columns[0].audit is None


def test_emit_custom_asked_by():
    [table] = emit_clarifications([_gap_table([_gap_column()])], asked_by="curator-v2")
    assert table.audit.clarification.asked_by == "curator-v2"
    assert table.columns[0].audit.clarification.asked_by == "curator-v2"


def test_emit_is_idempotent_and_does_not_overwrite():
    once = emit_clarifications([_gap_table([_gap_column()])])
    twice = emit_clarifications(once)
    # question text is unchanged, still open, still exactly one clarification block
    assert twice[0].audit.clarification.question == _TABLE_QUESTION
    assert twice[0].audit.clarification.status is ClarificationStatus.open
    assert twice[0].columns[0].audit.clarification.question == _COLUMN_QUESTION


def test_emit_does_not_mutate_input():
    table = _gap_table([_gap_column()])
    _ = emit_clarifications([table])
    assert table.audit is None
    assert table.columns[0].audit is None


# --------------------------------------------------------------------------- #
# StaticResponder / default_parse
# --------------------------------------------------------------------------- #


def test_static_responder_maps_and_defaults():
    responder = StaticResponder({"q?": "a"}, default="dunno")
    assert isinstance(responder, Responder)
    assert responder.answer("q?") == "a"
    assert responder.answer("unknown?") == "dunno"
    # default default is the empty string
    assert StaticResponder().answer("anything") == ""


def test_default_parse_returns_description_edit():
    assert default_parse("the customer identifier", _gap_column()) == {
        "description": "the customer identifier"
    }


# --------------------------------------------------------------------------- #
# Round trip: emit -> resolve
# --------------------------------------------------------------------------- #


def test_resolve_answers_column_clarification_end_to_end():
    emitted = emit_clarifications([_gap_table([_gap_column()])])
    responder = StaticResponder(
        {
            _COLUMN_QUESTION: "the customer identifier",
            _TABLE_QUESTION: "one row per order",
        }
    )
    [resolved] = resolve_clarifications(emitted, responder)

    col = resolved.columns[0]
    assert col.audit.clarification.status is ClarificationStatus.answered
    assert col.audit.clarification.answer == "the customer identifier"
    assert col.description == "the customer identifier"
    assert col.audit.provenance.source is ProvenanceSource.human

    # the table-level clarification is resolved too, carrying the updated column
    assert resolved.audit.clarification.status is ClarificationStatus.answered
    assert resolved.description == "one row per order"
    assert resolved.audit.provenance.source is ProvenanceSource.human


def test_resolve_passes_through_nodes_without_open_clarification():
    # a table whose only column is already fully described: nothing to resolve
    table = _gap_table([_described_column()]).model_copy(
        update={
            "description": "orders fact",
            "confidence": 0.9,
        }
    )
    emitted = emit_clarifications([table])  # no gaps -> no clarifications
    [resolved] = resolve_clarifications(emitted, StaticResponder())
    assert resolved.audit is None
    assert resolved.columns[0].audit is None
    assert resolved.description == "orders fact"


def test_resolve_does_not_mutate_input():
    emitted = emit_clarifications([_gap_table([_gap_column()])])
    _ = resolve_clarifications(
        emitted, StaticResponder({_COLUMN_QUESTION: "x", _TABLE_QUESTION: "y"})
    )
    # the emitted (input) clarifications are still open, unanswered
    assert emitted[0].audit.clarification.status is ClarificationStatus.open
    assert emitted[0].columns[0].audit.clarification.status is ClarificationStatus.open
    assert emitted[0].columns[0].description is None


# --------------------------------------------------------------------------- #
# Integration: resolved assets still serialize + reload
# --------------------------------------------------------------------------- #


def test_resolved_assets_round_trip_through_corpus(tmp_path):
    emitted = emit_clarifications([_gap_table([_gap_column()])])
    responder = StaticResponder(
        {_COLUMN_QUESTION: "the customer identifier", _TABLE_QUESTION: "one row per order"}
    )
    resolved = resolve_clarifications(emitted, responder)

    write_corpus(tmp_path, "demo", resolved)
    back = load_corpus(tmp_path, db="demo")

    [table] = back.tables()
    assert table.id == "tbl_demo_orders"
    assert table.description == "one row per order"
    assert table.columns[0].description == "the customer identifier"
    # the answered clarification survives the git+YAML round trip on the Audit tier
    assert table.columns[0].audit.clarification.status is ClarificationStatus.answered
