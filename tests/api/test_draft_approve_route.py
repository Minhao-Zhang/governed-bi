"""POST /corpus/drafts/{id}/approve — the admin half of UtkuAI's draft write path on v2."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

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


def _client(monkeypatch, tmp_path: Path):
    from fastapi.testclient import TestClient

    from governed_bi.api import routes, trace_store

    session = _session_with_corpus_root(tmp_path)
    # `routes.app` reached a process-global session that no longer exists: upstream
    # removed `_session` at the 2026-08-11 restructure in favour of this constructor.
    return TestClient(routes.make_app(session, None, trace_store))


def test_approve_certifies_a_submitted_draft_end_to_end(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import FewShotAsset
    from governed_bi.corpus.store import load

    submit_draft(tmp_path, FewShotAsset(id="fs.e2e", schema="s", sql="SELECT 1", summary="q"))
    client = _client(monkeypatch, tmp_path)

    response = client.post("/corpus/drafts/fs.e2e/approve", json={"by": "admin@example.com"})
    assert response.status_code == 200, response.text
    assert response.json() == {"id": "fs.e2e", "asset_type": "few_shot", "provenance_status": "certified"}

    (written,) = [a for a in load(tmp_path)[0] if a.id == "fs.e2e"]
    assert written.audit.provenance.status.value == "certified"


def test_approve_404s_on_an_unknown_id(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.post("/corpus/drafts/nope/approve")
    assert response.status_code == 404


def test_approve_409s_on_an_already_certified_asset(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import FewShotAsset

    submit_draft(tmp_path, FewShotAsset(id="fs.twice", schema="s", sql="SELECT 1", summary="q"))
    client = _client(monkeypatch, tmp_path)
    assert client.post("/corpus/drafts/fs.twice/approve").status_code == 200
    assert client.post("/corpus/drafts/fs.twice/approve").status_code == 409
