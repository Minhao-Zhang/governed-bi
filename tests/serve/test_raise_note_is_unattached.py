"""The unattached ``raise_note`` node is an ``aupdate_state`` target, not a turn step."""

from __future__ import annotations

from langgraph.graph import START

from governed_bi.serve.graph import build_graph, compile_graph
from governed_bi.serve.raised import raised_row


def _edge_ends(edge: object) -> tuple[object, object]:
    if isinstance(edge, (tuple, list)) and len(edge) >= 2:
        return edge[0], edge[1]
    src = getattr(edge, "source", getattr(edge, "start", None))
    dst = getattr(edge, "target", getattr(edge, "end", None))
    return src, dst


def test_raise_note_exists_and_has_no_incoming_edge() -> None:
    builder = build_graph()
    assert "raise_note" in builder.nodes
    incoming = [src for src, dst in map(_edge_ends, builder.edges) if dst == "raise_note"]
    assert incoming == [], incoming
    assert START not in incoming


def test_aupdate_state_appends_raised_through_raise_note() -> None:
    graph = compile_graph()
    config = {"configurable": {"thread_id": "t-raise"}}
    graph.invoke(
        {
            "question": "q",
            "thread_id": "t-raise",
            "turn_index": 1,
            "turn_id": "turn-raise",
            "run_id": "r",
            "question_id": "q",
            "db_id": "d",
            "attempt_id": "a",
            "corpus_content_hash": "c",
            "prompt_set_hash": "p",
            "knobs_resolved": {},
            "n_re_served": 0,
            "messages": [],
            "usage": [],
            "identity": {"token": "op"},
        },
        config,
    )
    row = raised_row(
        kind="wrong_answer",
        turn_id="turn-raise",
        thread_id="t-raise",
        note="the total is inverted",
        report_id="rpt-turn-raise-0123456789ab",
    )
    graph.update_state(config, {"raised": [row]}, as_node="raise_note")
    snapshot = graph.get_state(config)
    values = snapshot.values if hasattr(snapshot, "values") else snapshot
    raised = list(values.get("raised") or [])
    assert any(r.get("report_id") == row["report_id"] for r in raised), raised
    assert any(r.get("kind") == "wrong_answer" for r in raised)
