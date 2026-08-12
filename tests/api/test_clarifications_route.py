"""GET /clarifications, POST /clarifications/{id}/answer — v1's offline Clarifications queue,
restored onto v2 (Phase 1a: pure CRUD + persistence, no ask_user wiring, no corpus fold).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _session_with_corpus_root(tmp_path: Path) -> Any:
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
        prompt_set_hash="p", knobs_resolved={}, db_id="beer", run_id="r",
        corpus_root=tmp_path,
    )


def _session_without_corpus_root() -> Any:
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
        prompt_set_hash="p", knobs_resolved={}, db_id="beer", run_id="r",
        corpus_root=None,
    )


def _client(monkeypatch, session):
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    monkeypatch.setattr(routes, "_session", lambda: session)
    return TestClient(routes.app)


def _seed(tmp_path: Path, *records) -> None:
    from governed_bi.curator.clarifications import write_clarifications

    write_clarifications(tmp_path, list(records))


# ── GET /clarifications ─────────────────────────────────────────────────────────────────────


def test_get_returns_empty_list_with_no_corpus_root(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    response = client.get("/clarifications")
    assert response.status_code == 200
    assert response.json() == []


def test_get_returns_empty_list_with_no_ledger_file(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.get("/clarifications")
    assert response.status_code == 200
    assert response.json() == []


def test_get_lists_every_record_with_full_shape(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import ClarificationRecord

    _seed(
        tmp_path,
        ClarificationRecord(
            id="q001", scope="table:orders", question="what counts as active?",
            choices=({"id": "opt_a", "label": "90 days"},),
        ),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.get("/clarifications")
    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert row["id"] == "q001"
    assert row["scope"] == "table:orders"
    assert row["question"] == "what counts as active?"
    assert row["status"] == "open"
    assert row["choices"] == [{"id": "opt_a", "label": "90 days"}]
    assert row["allow_freeform"] is True
    assert row["answer"] is None
    assert row["answer_text"] is None
    assert row["source"] == "curator"


def test_get_with_status_filter_narrows_the_list(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import ClarificationRecord, ClarificationRecordStatus

    _seed(
        tmp_path,
        ClarificationRecord(id="q001", scope="s1", question="q1?"),
        ClarificationRecord(
            id="q002", scope="s2", question="q2?", status=ClarificationRecordStatus.answered,
            answer="an answer",
        ),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    open_rows = client.get("/clarifications?status=open").json()
    assert [r["id"] for r in open_rows] == ["q001"]

    answered_rows = client.get("/clarifications?status=answered").json()
    assert [r["id"] for r in answered_rows] == ["q002"]

    assert len(client.get("/clarifications").json()) == 2


# ── POST /clarifications/{id}/answer ────────────────────────────────────────────────────────


def test_answer_with_a_choice_id_sets_status_and_resolves_answer_text(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import ClarificationRecord, load_clarifications

    _seed(
        tmp_path,
        ClarificationRecord(
            id="q001", scope="table:orders", question="what counts as active?",
            choices=({"id": "opt_a", "label": "90 days"},),
        ),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post("/clarifications/q001/answer", json={"choice_id": "opt_a", "answered_by": "admin"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert body["answer_choice_id"] == "opt_a"
    assert body["answer"] is None
    assert body["answer_text"] == "90 days"
    assert body["answered_by"] == "admin"

    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk.status.value == "answered"
    assert on_disk.answer_choice_id == "opt_a"


def test_answer_freeform_sets_answer_and_answer_text(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import ClarificationRecord

    _seed(tmp_path, ClarificationRecord(id="q001", scope="s", question="q?"))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post("/clarifications/q001/answer", json={"answer": "90 days"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == "90 days"
    assert body["answer_text"] == "90 days"
    assert body["answered_by"] == "admin"  # default


def test_answer_unknown_id_is_404(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post("/clarifications/nope/answer", json={"answer": "x"})
    assert response.status_code == 404


def test_answer_with_none_of_choice_id_choice_ids_answer_is_422(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import ClarificationRecord

    _seed(tmp_path, ClarificationRecord(id="q001", scope="s", question="q?"))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post("/clarifications/q001/answer", json={})
    assert response.status_code == 422


def test_answer_with_no_corpus_root_is_409(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    response = client.post("/clarifications/q001/answer", json={"answer": "x"})
    assert response.status_code == 409


def test_answer_succeeds_with_the_session_default_can_edit_false(monkeypatch, tmp_path: Path) -> None:
    """The session fixture never sets a ``can_edit``-shaped field (there is none on
    ``Session`` -- ``can_edit`` is a hard-coded ``/capabilities`` report, not a gate), and this
    route must not require one, mirroring ``/corpus/conflicts/{id}/resolve``'s own test.
    """
    from governed_bi.curator.clarifications import ClarificationRecord

    _seed(tmp_path, ClarificationRecord(id="q001", scope="s", question="q?"))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post("/clarifications/q001/answer", json={"answer": "x"})
    assert response.status_code == 200
