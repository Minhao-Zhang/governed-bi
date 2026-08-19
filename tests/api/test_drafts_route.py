"""GET /corpus/drafts (fix round, task D): the approval queue reads fresh off disk.

Original bug: the queue's first data source, GET /corpus/assets, reads
`session.assets_by_id`, a run constant frozen at session-build time (ADR 0005) -- so within
one server process it never observes a draft written, or a draft approved, after the session
was built. This module's fixtures write directly through `corpus.drafts.submit_draft` /
`corpus.store.write` -- the same primitives Phase 3's live path uses, and the same idiom
`tests/api/test_corpus_conflicts_route.py` and `tests/api/test_draft_approve_route.py` already
use -- deterministic, no model call.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")

_DB_ID = "beer"


def _session_with_corpus_root(tmp_path: Path | None, db_id: str = _DB_ID) -> Any:
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


def _client(monkeypatch, tmp_path: Path | None, db_id: str = _DB_ID):
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    session = _session_with_corpus_root(tmp_path, db_id)
    # `routes.app` reached a process-global session that no longer exists: upstream removed
    # `_session` at the 2026-08-11 restructure in favour of this constructor.
    return TestClient(routes.make_app(session, None))


def test_drafts_reports_a_draft_written_after_the_session_was_built(
    monkeypatch, tmp_path: Path
) -> None:
    """The exact regression this route fixes.

    The session fixture always builds with `assets_by_id={}` -- as if this draft did not
    exist when the process started. `GET /corpus/assets` reads that frozen mapping and must
    stay blind to a write made after it (that staleness is the accepted, out-of-scope
    tradeoff for the asset browser). `GET /corpus/drafts` reads the corpus root fresh on this
    same request, in this same process, and must see it -- with its body, not only a
    (possibly truncated) summary.
    """
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import TermAsset

    draft = TermAsset(
        id="clarification.beer.abv",
        name="abv",
        summary="what does abv mean? — alcohol by volume",
        body="Q: what does abv mean?\nA: alcohol by volume",
    )
    submit_draft(tmp_path, draft, namespace=_DB_ID)
    client = _client(monkeypatch, tmp_path)

    # The stale surface: still reports nothing, because `assets_by_id` was frozen empty.
    assert client.get("/corpus/assets").json() == []

    # The fresh surface: reads the same on-disk state within the same request cycle.
    response = client.get("/corpus/drafts")
    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert row == {
        "id": "clarification.beer.abv",
        "asset_type": "term",
        "summary": "what does abv mean? — alcohol by volume",
        "body": "Q: what does abv mean?\nA: alcohol by volume",
        "provenance_status": "proposed",
    }


def test_drafts_disappears_from_the_queue_once_approved_in_the_same_process(
    monkeypatch, tmp_path: Path
) -> None:
    """The other half of the same bug: after `POST .../approve`, a refetch of the queue in
    this same process must not still list the just-approved draft."""
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import FewShotAsset

    submit_draft(
        tmp_path, FewShotAsset(id="fs.queue1", schema=_DB_ID, sql="SELECT 1", summary="q")
    )
    client = _client(monkeypatch, tmp_path)

    assert len(client.get("/corpus/drafts").json()) == 1
    assert client.post("/corpus/drafts/fs.queue1/approve").status_code == 200
    assert client.get("/corpus/drafts").json() == []


def test_drafts_excludes_an_already_certified_asset(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.schema import (
        Audit,
        Provenance,
        ProvenanceSource,
        ProvenanceStatus,
        TermAsset,
    )
    from governed_bi.corpus.store import write

    write(
        tmp_path,
        TermAsset(
            id="term.certified_one",
            name="x",
            summary="already certified",
            audit=Audit(
                provenance=Provenance(source=ProvenanceSource.human, status=ProvenanceStatus.certified)
            ),
        ),
        namespace=_DB_ID,
    )
    client = _client(monkeypatch, tmp_path)
    assert client.get("/corpus/drafts").json() == []


def test_drafts_with_no_corpus_root_is_an_empty_list(monkeypatch) -> None:
    client = _client(monkeypatch, None)
    assert client.get("/corpus/drafts").json() == []
