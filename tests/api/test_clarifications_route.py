"""GET /clarifications, POST /clarifications/{id}/answer — v1's offline Clarifications queue,
restored onto v2. Phase 1a built pure CRUD + persistence; Phase 1c (this file's fold tests,
below the CRUD ones) wires the answer route through the same Enhancer/mining pipeline
``serve/nodes/mine_corpus.py`` uses for a live answer (``curator/clarification.py::
fold_ledger_answer_into_corpus``).
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

    from governed_bi.api import routes, trace_store

    # `routes.app` reached a process-global session that no longer exists: upstream
    # removed `_session` at the 2026-08-11 restructure in favour of this constructor.
    return TestClient(routes.make_app(session, None, trace_store))


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


# ── POST /clarifications/{id}/answer folds into the corpus (Phase 1c) ──────────────────────
#
# The route delegates to `curator/clarification.py::fold_ledger_answer_into_corpus` -- the
# same Enhancer/mining pipeline `serve/nodes/mine_corpus.py` uses for a live answer. Novel/
# duplicate/conflict decisions themselves are exercised directly against that shared function
# in `tests/curator/test_clarification.py`; these tests are about the *route* reaching it with
# the right basis gate, schema, and idempotency -- not re-proving the Enhancer's own decisions.


def test_answer_with_ranking_ambiguity_basis_mines_nothing(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.clarifications import ClarificationRecord

    _seed(
        tmp_path,
        ClarificationRecord(
            id="q001", scope="s", question="best-selling by what measure?",
            basis="ranking_ambiguity",
        ),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post("/clarifications/q001/answer", json={"answer": "total revenue"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["converted_to_corpus"] is False

    assets, _ = load(tmp_path)
    assert assets == [], f"a ranking_ambiguity answer was mined through the offline route: {assets}"


def test_answer_with_data_definition_basis_folds_into_the_corpus(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.clarifications import ClarificationRecord

    _seed(
        tmp_path,
        ClarificationRecord(
            id="q001", scope="s", question="what does active mean?", basis="data_definition",
        ),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post("/clarifications/q001/answer", json={"answer": "90 days"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["converted_to_corpus"] is True

    assets, problems = load(tmp_path)
    assert not problems
    (draft,) = assets
    assert draft.asset_type.value == "term"
    assert "90 days" in draft.summary


def test_answer_with_no_basis_at_all_still_folds(monkeypatch, tmp_path: Path) -> None:
    """A record predating the ``basis`` field (or not sourced from ``ask_user`` at all) has
    ``basis is None`` -- treated as ``data_definition``-eligible, not silently skipped.
    """
    from governed_bi.corpus.store import load
    from governed_bi.curator.clarifications import ClarificationRecord

    _seed(tmp_path, ClarificationRecord(id="q001", scope="s", question="what does active mean?"))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post("/clarifications/q001/answer", json={"answer": "90 days"})
    assert response.json()["converted_to_corpus"] is True
    assets, _ = load(tmp_path)
    assert len(assets) == 1


def test_answering_the_same_record_twice_via_the_route_does_not_double_write(
    monkeypatch, tmp_path: Path
) -> None:
    """A real second ``POST`` to the same id (there is no re-answer flow in the product, but
    nothing stops a second call at the HTTP layer either) must not fold a second time.
    """
    from governed_bi.corpus.store import load
    from governed_bi.curator.clarifications import ClarificationRecord

    _seed(
        tmp_path,
        ClarificationRecord(
            id="q001", scope="s", question="what does active mean?", basis="data_definition",
        ),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    first = client.post("/clarifications/q001/answer", json={"answer": "90 days"})
    assert first.json()["converted_to_corpus"] is True
    assets_after_first, _ = load(tmp_path)
    (draft,) = assets_after_first
    written_at = (tmp_path / "beer" / f"{draft.id}.yaml").stat().st_mtime

    second = client.post("/clarifications/q001/answer", json={"answer": "still 90 days"})
    assert second.status_code == 200, second.text
    assert second.json()["converted_to_corpus"] is True

    assets_after_second, _ = load(tmp_path)
    assert len(assets_after_second) == 1, f"the fold ran twice: {[a.id for a in assets_after_second]}"
    assert (
        tmp_path / "beer" / f"{assets_after_second[0].id}.yaml"
    ).stat().st_mtime == written_at, "the draft file was rewritten on the second answer"


# ── POST /clarifications/from-refusal (task A) ──────────────────────────────────────────────
#
# The one reader-initiated entrance to this ledger: a refusal fires before `ask_user` can ever
# reach it, so the reader who asked the original question files it directly rather than through
# a resumed graph turn.


def test_filing_a_refusal_clarification_with_no_corpus_root_is_409(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    response = client.post(
        "/clarifications/from-refusal",
        json={"question": "Which apps are popular?", "answer": "Highest download count."},
    )
    assert response.status_code == 409


def test_filing_a_refusal_clarification_with_no_answer_is_422(monkeypatch, tmp_path: Path) -> None:
    """An explanation with nothing in it is not homework for an admin -- it is nothing at all,
    so this must reject it rather than write an empty record to the ledger."""
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/clarifications/from-refusal", json={"question": "Which apps are popular?"}
    )
    assert response.status_code == 422


def test_filing_a_refusal_clarification_with_no_question_is_422(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/clarifications/from-refusal", json={"answer": "Highest download count."}
    )
    assert response.status_code == 422


def test_filing_a_refusal_clarification_creates_an_answered_record_sourced_from_the_reader(
    monkeypatch, tmp_path: Path
) -> None:
    """The reader's explanation lands as the record's own answer, not a freeform pre-fill left
    for an admin to separately confirm (see the route's own docstring for the argument)."""
    from governed_bi.curator.clarifications import load_clarifications

    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/clarifications/from-refusal",
        json={"question": "Which apps are popular?", "answer": "Highest download count."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"] == "Which apps are popular?"
    assert body["answer"] == "Highest download count."
    assert body["status"] == "answered"
    assert body["source"] == "refusal"
    assert body["basis"] == "data_definition"
    assert body["answered_by"] == "user"
    assert body["converted_to_corpus"] is True

    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk.source == "refusal"
    assert on_disk.status.value == "answered"


def test_filing_a_refusal_clarification_folds_into_a_proposed_corpus_draft(
    monkeypatch, tmp_path: Path
) -> None:
    """Reaches the corpus through the exact same fold every admin answer route already uses --
    a `proposed` draft a human must still approve before it is `certified` and visible to a
    live turn, never a certified fact from one reader's unreviewed say-so."""
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load

    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/clarifications/from-refusal",
        json={"question": "Which apps are popular?", "answer": "Highest download count."},
    )
    assert response.status_code == 200, response.text

    assets, problems = load(tmp_path)
    assert not problems
    (draft,) = assets
    assert draft.asset_type.value == "term"
    assert "Highest download count." in draft.summary
    assert draft.audit is not None and draft.audit.provenance is not None
    assert draft.audit.provenance.status is ProvenanceStatus.proposed


def test_filing_the_same_question_and_explanation_twice_does_not_duplicate_the_ledger_row(
    monkeypatch, tmp_path: Path
) -> None:
    """An accidental double-submit of the identical text (no graph interrupt to guard against
    here, unlike a live `ask_user` question) must not double the ledger row."""
    from governed_bi.curator.clarifications import load_clarifications

    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    body = {"question": "Which apps are popular?", "answer": "Highest download count."}

    first = client.post("/clarifications/from-refusal", json=body)
    second = client.post("/clarifications/from-refusal", json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    assert len(load_clarifications(tmp_path)) == 1


def test_filing_a_refusal_clarification_forwards_the_turn_id(monkeypatch, tmp_path: Path) -> None:
    """task B-0: an optional `turn_id` in the request lands on the record unchanged, so task B's
    read model can later trace this row back to the thread that raised it."""
    from governed_bi.curator.clarifications import load_clarifications

    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/clarifications/from-refusal",
        json={
            "question": "Which apps are popular?",
            "answer": "Highest download count.",
            "turn_id": "turn-123",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["turn_id"] == "turn-123"

    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk.turn_id == "turn-123"


def test_filing_a_refusal_clarification_with_no_turn_id_leaves_it_none(
    monkeypatch, tmp_path: Path
) -> None:
    """Additive: a caller that predates B-0 (or simply has no turn id yet) keeps working."""
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/clarifications/from-refusal",
        json={"question": "Which apps are popular?", "answer": "Highest download count."},
    )
    assert response.status_code == 200, response.text
    assert response.json()["turn_id"] is None


def test_filing_two_different_explanations_for_the_same_question_keeps_both(
    monkeypatch, tmp_path: Path
) -> None:
    """A second reader's own words are not a duplicate of the first reader's -- both are kept as
    separate records, deliberately, per the route's own docstring."""
    from governed_bi.curator.clarifications import load_clarifications

    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    question = "Which apps are popular?"

    first = client.post(
        "/clarifications/from-refusal", json={"question": question, "answer": "Highest downloads."}
    )
    second = client.post(
        "/clarifications/from-refusal", json={"question": question, "answer": "Highest rating."}
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] != second.json()["id"]

    assert len(load_clarifications(tmp_path)) == 2
