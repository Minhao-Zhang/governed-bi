"""Two entry points, one mechanism: a live-deferred clarification, answered offline.

Ported intent of v1's ``test_live_defer_continues_to_answer_and_lands_in_offline_queue``
(``governed-bi-v1-demo/tests/test_serve_clarify_live.py``) -- the "same mechanism, two entry
points" proof, driven here by ``compile_graph()`` + ``ScriptedChatModel`` rather than a real
model, the same substitution ``tests/serve/test_clarification_mining_transport.py`` makes for
the sibling *live* fold.

1. A real ``ask_user`` interrupt fires mid-turn (``basis="data_definition"``).
2. Resuming with ``{"defer": True}`` continues the turn (Phase 1b: defer, unlike decline, does
   not fail the turn closed) and leaves the ledger record ``open`` -- Phase 1b only writes that
   record *before* ``interrupt()``; nothing after a resume touches it.
3. That same ``open`` record, answered through ``POST /clarifications/{id}/answer`` -- the
   offline admin route, with **no call anywhere in this test into the compiled graph** for the
   answer half -- folds into the corpus via ``curator/clarification.py::
   fold_ledger_answer_into_corpus``, the identical Enhancer path ``serve/nodes/mine_corpus.py``
   uses for a live-chat answer, not a parallel one.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _ask_user_then_defer_answer(*, basis: str) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {"question": "what does active customer mean?", "basis": basis},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Assuming 90 days (unconfirmed), the answer proceeds on that basis."),
        ]
    )


def _live_config(model: Any, corpus_root: Path, thread_id: str) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
            "corpus_root": corpus_root,
        }
    }


def _turn(*, thread_id: str, turn_id: str, token: str, db_id: str) -> dict[str, Any]:
    return {
        "question": "who counts as an active customer?",
        "thread_id": thread_id,
        "turn_index": 1,
        "turn_id": turn_id,
        "run_id": "r",
        "question_id": "q",
        "db_id": db_id,
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "knobs_resolved": {},
        "n_re_served": 0,
        "facet_route_hits": [("facet_schema", db_id, 1.0)],
        "messages": [],
        "usage": [],
        "identity": {"token": token},
        "clarifications": [],
    }


def _offline_session(tmp_path: Path, db_id: str) -> Any:
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    return Session(
        index=None, structure=structure, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id=db_id, run_id="r",
        corpus_root=tmp_path,
    )


def _offline_client(monkeypatch, session: Any) -> Any:
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    monkeypatch.setattr(routes, "_session", lambda: session)
    return TestClient(routes.app)


def test_a_live_deferred_clarification_survives_open_then_folds_through_the_offline_route(
    monkeypatch, tmp_path: Path
) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.clarifications import load_clarifications

    db_id = "olist"

    # ── 1 & 2: a real ask_user interrupt fires, defer continues the turn, record stays open ──
    model = _ask_user_then_defer_answer(basis="data_definition")
    graph = compile_graph()
    token = "identity-live-defer-offline-fold"
    config = _live_config(model, tmp_path, "t-live-defer-offline-fold")

    paused = graph.invoke(
        _turn(thread_id="t-live-defer-offline-fold", turn_id="turn-1", token=token, db_id=db_id),
        config,
    )
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"

    done = resume_clarification(graph, config=config, identity={"token": token}, answer={"defer": True})
    assert done["answer"]["outcome"] in {"answered", "clarification"}, done["answer"]
    assert done["answer"]["reliability"] is not None, "defer must downgrade this turn's reliability"

    records = load_clarifications(tmp_path)
    (record,) = records
    assert record.status.value == "open", "Phase 1b: a resume never itself answers the ledger row"
    assert record.source == "live_chat"
    assert record.basis == "data_definition"

    # ── 3: the SAME open record, answered offline -- no graph call from here on ──
    session = _offline_session(tmp_path, db_id)
    client = _offline_client(monkeypatch, session)
    answer_text = "An active customer placed an order in the last 90 days."
    response = client.post(f"/clarifications/{record.id}/answer", json={"answer": answer_text})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert body["answer"] == answer_text
    assert body["converted_to_corpus"] is True, (
        "answering via the offline route must actually fold into the corpus "
        "(fold_ledger_answer_into_corpus), not just flip the ledger status"
    )

    assets, problems = load(tmp_path, schemas=[db_id])
    assert not problems, problems
    (draft,) = assets
    assert draft.asset_type.value == "term"
    assert "90 days" in draft.summary


def test_a_live_deferred_ranking_ambiguity_still_mines_nothing_through_the_offline_route(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression, mirrored from ``test_clarification_mining_transport.py``'s own pairing:
    the offline route must not widen what a live turn's own basis gate already excludes.
    """
    from governed_bi.corpus.store import load
    from governed_bi.curator.clarifications import load_clarifications

    db_id = "olist"
    model = _ask_user_then_defer_answer(basis="ranking_ambiguity")
    graph = compile_graph()
    token = "identity-live-defer-ranking"
    config = _live_config(model, tmp_path, "t-live-defer-ranking")

    paused = graph.invoke(
        _turn(thread_id="t-live-defer-ranking", turn_id="turn-1", token=token, db_id=db_id),
        config,
    )
    assert paused.get("__interrupt__")
    resume_clarification(graph, config=config, identity={"token": token}, answer={"defer": True})

    (record,) = load_clarifications(tmp_path)
    assert record.basis == "ranking_ambiguity"

    session = _offline_session(tmp_path, db_id)
    client = _offline_client(monkeypatch, session)
    response = client.post(
        f"/clarifications/{record.id}/answer", json={"answer": "total lifetime spend"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["converted_to_corpus"] is False

    assets, _ = load(tmp_path, schemas=[db_id])
    assert assets == [], f"a ranking_ambiguity answer was mined offline: {[a.id for a in assets]}"
