"""GET /corpus/assumptions, GET /corpus/conflicts, POST /corpus/conflicts/{id}/resolve.

Phase 4 of restoring v1 admin corpus curation onto v2: the read/admin side of what Phase 3
(``curator/enhancer.py`` wired into live clarification mining) already writes. Fixtures write
assets directly through the same primitives Phase 3's live path uses
(``corpus.drafts.submit_draft``, ``corpus.store.write``) rather than driving a real model call
-- deterministic, same idiom ``tests/api/test_clarification_mining.py`` already uses.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")

_DB_ID = "olist"


def _session_with_corpus_root(tmp_path: Path, db_id: str = _DB_ID) -> Any:
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
        prompt_set_hash="p", knobs_resolved={}, db_id=db_id, run_id="r",
        corpus_root=tmp_path,
    )


def _client(monkeypatch, tmp_path: Path, db_id: str = _DB_ID):
    from fastapi.testclient import TestClient

    from governed_bi.api import routes, trace_store

    session = _session_with_corpus_root(tmp_path, db_id)
    # `routes.app` reached a process-global session that no longer exists: upstream
    # removed `_session` at the 2026-08-11 restructure in favour of this constructor.
    return TestClient(routes.make_app(session, None, trace_store))


def _write_certified_term(tmp_path: Path, asset_id: str, summary: str, *, db_id: str = _DB_ID) -> Any:
    from governed_bi.corpus.schema import Audit, Provenance, ProvenanceSource, ProvenanceStatus, TermAsset
    from governed_bi.corpus.store import write

    asset = TermAsset(
        id=asset_id, name=asset_id, summary=summary,
        audit=Audit(provenance=Provenance(source=ProvenanceSource.human, status=ProvenanceStatus.certified)),
    )
    write(tmp_path, asset, namespace=db_id)
    return asset


def _write_settled_draft(
    tmp_path: Path, asset_id: str, question: str, answer: str, *, db_id: str = _DB_ID
) -> Any:
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import TermAsset

    draft = TermAsset(
        id=asset_id, name=question, summary=f"{question} — {answer}", body=f"Q: {question}\nA: {answer}",
    )
    submit_draft(tmp_path, draft, namespace=db_id)
    return draft


def _write_conflict_candidate(
    tmp_path: Path, asset_id: str, existing_id: str, question: str, answer: str, *, db_id: str = _DB_ID
) -> Any:
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import TermAsset

    draft = TermAsset(
        id=asset_id, name=question, summary=f"{question} — {answer}", body=f"Q: {question}\nA: {answer}",
    )
    submit_draft(tmp_path, draft, namespace=db_id, extra={"conflict_with": existing_id})
    return draft


# ── GET /corpus/conflicts ──────────────────────────────────────────────────────────────────


def test_conflicts_lists_a_seeded_conflict_with_every_field_populated(monkeypatch, tmp_path: Path) -> None:
    existing = _write_certified_term(tmp_path, "clarification.olist.revenue", "revenue means net_revenue")
    _write_conflict_candidate(
        tmp_path,
        "clarification.olist.conflict1",
        existing.id,
        "what does revenue mean?",
        "gross sales including tax",
    )
    client = _client(monkeypatch, tmp_path)

    response = client.get("/corpus/conflicts")
    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert row["id"] == "clarification.olist.conflict1"
    assert row["status"] == "unresolved"
    assert row["existing_asset_id"] == existing.id
    assert row["existing_asset_type"] == "term"
    assert row["existing_text"] == "revenue means net_revenue"
    assert row["existing_question"] is None  # existing's body has no Q/A shape
    assert row["new_question"] == "what does revenue mean?"
    assert "gross sales including tax" in row["new_text"]
    assert row["source"] == "live_chat"
    assert row["answered_by"] is None
    assert row["created_at"] is None


def test_conflicts_status_filter_narrows_the_list(monkeypatch, tmp_path: Path) -> None:
    existing = _write_certified_term(tmp_path, "clarification.olist.revenue", "revenue means net_revenue")
    _write_conflict_candidate(
        tmp_path, "clarification.olist.conflict1", existing.id, "what does revenue mean?", "gross sales",
    )
    client = _client(monkeypatch, tmp_path)

    assert len(client.get("/corpus/conflicts?status=unresolved").json()) == 1
    assert client.get("/corpus/conflicts?status=resolved_kept_existing").json() == []
    assert client.get("/corpus/conflicts?status=resolved_replaced").json() == []


# ── GET /corpus/assumptions ─────────────────────────────────────────────────────────────────


def test_assumptions_includes_settled_and_excludes_conflicts_and_non_clarification_assets(
    monkeypatch, tmp_path: Path
) -> None:
    _write_settled_draft(
        tmp_path, "clarification.olist.settled1", "what counts as active?", "ordered in the last 90 days",
    )
    existing = _write_certified_term(tmp_path, "clarification.olist.revenue", "revenue means net_revenue")
    _write_conflict_candidate(
        tmp_path, "clarification.olist.conflict1", existing.id, "what does revenue mean?", "gross sales",
    )
    # (b) a non-clarification asset going through the same proposed/certified machinery.
    _write_certified_term(tmp_path, "term.manual_definition", "manual definition of something else")

    client = _client(monkeypatch, tmp_path)
    response = client.get("/corpus/assumptions")
    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert row["id"] == "clarification.olist.settled1"
    assert row["question"] == "what counts as active?"
    assert row["answer"] == "ordered in the last 90 days"
    assert row["answered_by"] is None
    assert row["answered_at"] is None
    assert row["source"] == "live_chat"


def test_assumptions_excludes_a_mistake_memory_style_draft(monkeypatch, tmp_path: Path) -> None:
    """Problem 1's discriminator must hold even for another curator-authored, ``proposed``
    asset going through the exact same ``submit_draft`` machinery -- ``curator/mistake_memory.py``
    always writes a ``FewShotAsset``, simulated directly here rather than driving that module.
    """
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import FewShotAsset

    submit_draft(
        tmp_path,
        FewShotAsset(id="mistake.olist.abc123", schema=_DB_ID, sql="SELECT 1", summary="a mined mistake fix"),
        namespace=_DB_ID,
    )
    client = _client(monkeypatch, tmp_path)
    assert client.get("/corpus/assumptions").json() == []


def test_assumptions_excludes_a_settled_asset_later_superseded_by_a_replace_resolution(
    monkeypatch, tmp_path: Path
) -> None:
    """Found live (2026-08-08): a "replace" resolution excludes the asset it superseded
    (``governance.excluded=True``) but does not touch that asset's own ``audit.extra`` -- so
    without a governance check, a definition a later conflict overturned kept reporting as a
    currently-agreed assumption.
    """
    settled = _write_settled_draft(tmp_path, "clarification.olist.settled3", "what is active?", "90 days")
    candidate = _write_conflict_candidate(
        tmp_path, "clarification.olist.conflict6", settled.id, "what is active?", "30 days instead",
    )
    client = _client(monkeypatch, tmp_path)
    assert client.post(f"/corpus/conflicts/{candidate.id}/resolve", json={"resolution": "replace"}).status_code == 200

    ids = {row["id"] for row in client.get("/corpus/assumptions").json()}
    assert settled.id not in ids  # superseded -- no longer a currently-agreed assumption
    assert candidate.id not in ids  # conflict-flagged -- belongs to /corpus/conflicts, not here


# ── POST /corpus/conflicts/{id}/resolve ─────────────────────────────────────────────────────


def test_resolve_keep_existing_flips_status_and_leaves_candidate_proposed(
    monkeypatch, tmp_path: Path
) -> None:
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load

    existing = _write_certified_term(tmp_path, "clarification.olist.revenue", "revenue means net_revenue")
    candidate = _write_conflict_candidate(
        tmp_path, "clarification.olist.conflict1", existing.id, "what does revenue mean?", "gross sales",
    )
    client = _client(monkeypatch, tmp_path)

    response = client.post(f"/corpus/conflicts/{candidate.id}/resolve", json={"resolution": "keep_existing"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "resolved": True,
        "conflict_id": candidate.id,
        "status": "resolved_kept_existing",
        "detail": f"resolved {candidate.id} (keep_existing)",
    }

    assert client.get("/corpus/conflicts?status=unresolved").json() == []
    (row,) = client.get("/corpus/conflicts").json()
    assert row["status"] == "resolved_kept_existing"

    (on_disk,) = [a for a in load(tmp_path)[0] if a.id == candidate.id]
    assert on_disk.audit.provenance.status is ProvenanceStatus.proposed  # never certified
    assert on_disk.audit.extra["conflict_resolution"] == "kept_existing"
    (existing_on_disk,) = [a for a in load(tmp_path)[0] if a.id == existing.id]
    assert existing_on_disk.governance.excluded is False  # untouched


def test_resolve_replace_certifies_candidate_and_excludes_existing(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load

    existing = _write_certified_term(tmp_path, "clarification.olist.revenue2", "revenue means net_revenue")
    candidate = _write_conflict_candidate(
        tmp_path, "clarification.olist.conflict2", existing.id, "what does revenue mean?", "gross sales",
    )
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        f"/corpus/conflicts/{candidate.id}/resolve", json={"resolution": "replace", "answered_by": "admin"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "resolved_replaced"

    (on_disk,) = [a for a in load(tmp_path)[0] if a.id == candidate.id]
    assert on_disk.audit.provenance.status is ProvenanceStatus.certified
    assert on_disk.audit.extra["conflict_resolution"] == "replaced"
    assert on_disk.audit.extra["approved_by"] == "admin"

    (existing_on_disk,) = [a for a in load(tmp_path)[0] if a.id == existing.id]
    assert existing_on_disk.governance.excluded is True
    assert existing_on_disk.governance.reason == f"superseded by {candidate.id}"
    assert existing_on_disk.governance.by == "admin"

    (row,) = client.get("/corpus/conflicts").json()
    assert row["status"] == "resolved_replaced"


def test_resolve_a_second_time_is_409(monkeypatch, tmp_path: Path) -> None:
    existing = _write_certified_term(tmp_path, "clarification.olist.revenue3", "revenue means net_revenue")
    candidate = _write_conflict_candidate(
        tmp_path, "clarification.olist.conflict3", existing.id, "what does revenue mean?", "gross sales",
    )
    client = _client(monkeypatch, tmp_path)

    body = {"resolution": "keep_existing"}
    assert client.post(f"/corpus/conflicts/{candidate.id}/resolve", json=body).status_code == 200
    assert client.post(f"/corpus/conflicts/{candidate.id}/resolve", json=body).status_code == 409


def test_resolve_unknown_id_is_404(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.post("/corpus/conflicts/nope/resolve", json={"resolution": "keep_existing"})
    assert response.status_code == 404


def test_resolve_a_non_conflict_asset_is_404(monkeypatch, tmp_path: Path) -> None:
    settled = _write_settled_draft(tmp_path, "clarification.olist.settled2", "q?", "a")
    client = _client(monkeypatch, tmp_path)
    response = client.post(f"/corpus/conflicts/{settled.id}/resolve", json={"resolution": "keep_existing"})
    assert response.status_code == 404


def test_resolve_unknown_resolution_string_is_422(monkeypatch, tmp_path: Path) -> None:
    existing = _write_certified_term(tmp_path, "clarification.olist.revenue4", "revenue means net_revenue")
    candidate = _write_conflict_candidate(
        tmp_path, "clarification.olist.conflict4", existing.id, "what does revenue mean?", "gross sales",
    )
    client = _client(monkeypatch, tmp_path)
    response = client.post(f"/corpus/conflicts/{candidate.id}/resolve", json={"resolution": "discard"})
    assert response.status_code == 422


def test_resolve_succeeds_with_the_session_default_can_edit_false(monkeypatch, tmp_path: Path) -> None:
    """The session fixture never sets a ``can_edit``-shaped field (there is none on
    ``Session`` -- ``can_edit`` is a hard-coded ``/capabilities`` report, not a gate), and this
    route must not require one, unlike the free-form corpus editor it is not part of.
    """
    existing = _write_certified_term(tmp_path, "clarification.olist.revenue5", "revenue means net_revenue")
    candidate = _write_conflict_candidate(
        tmp_path, "clarification.olist.conflict5", existing.id, "what does revenue mean?", "gross sales",
    )
    client = _client(monkeypatch, tmp_path)
    response = client.post(f"/corpus/conflicts/{candidate.id}/resolve", json={"resolution": "replace"})
    assert response.status_code == 200
