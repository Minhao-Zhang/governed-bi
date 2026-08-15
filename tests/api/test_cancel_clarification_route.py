"""``POST /clarifications/{id}/cancel`` over the real app.

Separate from ``tests/curator/test_cancelling_depends_on_the_basis.py``, which exercises the
ledger function directly, because the two catch different things. That file cannot reach the
route's error paths, and this file exists partly because it nearly shipped broken: `HTTPException`
is imported per-handler in ``curation_routes.py``, the new handler raised it without importing it,
and every ledger test passed because the happy path never touches it. ``ruff`` caught the
undefined name; a 404 in production would have been a 500.

The route's contract, and the two things about it that are deliberate:

* **It takes no body.** Cancelling carries no information beyond "this one" — the basis rule reads
  the stored record, so there is nothing for a caller to send and nothing for one to get wrong.
* **It is not a resume.** ``ask_user``'s ``interrupt()`` payload and the resume shape
  (``answer | choice_id | declined | defer``) are upstream's wire contract and are untouched. The
  paused graph thread is simply never resumed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from contracts import needs

from governed_bi.curator.clarifications import (
    ClarificationRecord,
    ClarificationRecordStatus,
    load_clarifications,
    write_clarifications,
)

pytestmark = needs("D")


def _client(monkeypatch, corpus_root: Path) -> Any:
    from fastapi.testclient import TestClient

    from governed_bi.api import routes, trace_store

    return TestClient(routes.make_app(_session(corpus_root), None, trace_store))


def _session(corpus_root: Path) -> Any:
    """Same shape as ``test_clarifications_route.py``'s fixture — this file drives the same
    router, so it builds the session the same way rather than inventing a second recipe.
    """
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    return Session(
        index=None, structure=structure, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id="app_store", run_id="r",
        corpus_root=corpus_root,
    )


def _seed(root: Path, *, basis: str | None, status: str = "open", rid: str = "clar-01") -> None:
    write_clarifications(
        root,
        [
            ClarificationRecord(
                id=rid,
                scope=f"live_chat:{rid}",
                question="Which apps are best?",
                status=ClarificationRecordStatus(status),
                source="live_chat",
                basis=basis,
            )
        ],
    )


def test_cancelling_a_ranking_question_shortens_the_admin_queue(monkeypatch, tmp_path: Path) -> None:
    _seed(tmp_path, basis="ranking_ambiguity")
    client = _client(monkeypatch, tmp_path)
    assert len(client.get("/clarifications?status=open").json()) == 1

    response = client.post("/clarifications/clar-01/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert client.get("/clarifications?status=open").json() == []
    assert len(client.get("/clarifications?status=cancelled").json()) == 1


def test_cancelling_a_definition_question_leaves_the_queue_alone(monkeypatch, tmp_path: Path) -> None:
    _seed(tmp_path, basis="data_definition")
    client = _client(monkeypatch, tmp_path)

    response = client.post("/clarifications/clar-01/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "open", (
        "a definition question has one answer for everyone, so abandoning it does not un-ask it"
    )
    assert len(client.get("/clarifications?status=open").json()) == 1


def test_an_unknown_id_is_404_and_not_500(monkeypatch, tmp_path: Path) -> None:
    """The path ``ruff`` caught before this test existed: the handler raised `HTTPException`
    without importing it, so the error branch was a `NameError`.
    """
    _seed(tmp_path, basis="ranking_ambiguity")
    client = _client(monkeypatch, tmp_path)

    response = client.post("/clarifications/nope/cancel")

    assert response.status_code == 404, response.text
    assert "nope" in response.json()["detail"]


def test_cancelling_an_answered_record_is_409(monkeypatch, tmp_path: Path) -> None:
    """Its answer may already be a corpus asset under an id hashed from this question text."""
    _seed(tmp_path, basis="ranking_ambiguity", status="answered")
    client = _client(monkeypatch, tmp_path)

    response = client.post("/clarifications/clar-01/cancel")

    assert response.status_code == 409, response.text
    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk.status is ClarificationRecordStatus.answered


def test_the_route_takes_no_body(monkeypatch, tmp_path: Path) -> None:
    """A body would be a second place the basis rule could be decided. Sending one is ignored
    rather than rejected — FastAPI does not bind it — and the assertion here is that the outcome
    is the ledger's, not the caller's: a body claiming this is a definition question does not save
    a ranking one from being cancelled.
    """
    _seed(tmp_path, basis="ranking_ambiguity")
    client = _client(monkeypatch, tmp_path)

    response = client.post("/clarifications/clar-01/cancel", json={"basis": "data_definition"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
