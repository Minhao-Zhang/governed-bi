"""corpus/drafts.py: submit -> proposed, invisible; approve -> certified, visible."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _few_shot(asset_id: str) -> "object":
    from governed_bi.corpus.schema import FewShotAsset

    return FewShotAsset(id=asset_id, schema="s", sql="SELECT 1", summary="example question")


def test_submit_then_approve_round_trips_through_disk(tmp_path: Path) -> None:
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.drafts import approve_draft, submit_draft
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load

    path = submit_draft(tmp_path, _few_shot("fs.mined"), model="test-model")
    assert path.exists()

    assets, problems = load(tmp_path)
    assert not problems
    (written,) = [a for a in assets if a.id == "fs.mined"]
    assert written.audit.provenance.status is ProvenanceStatus.proposed
    assert "fs.mined" not in for_analyst(assets).by_id

    certified = approve_draft(tmp_path, "fs.mined", by="admin@example.com")
    assert certified.audit.provenance.status is ProvenanceStatus.certified
    assert certified.audit.extra["approved_by"] == "admin@example.com"

    assets_after, problems_after = load(tmp_path)
    assert not problems_after
    assert for_analyst(assets_after).by_id["fs.mined"].id == "fs.mined"


def test_submit_strips_a_forged_governance_block(tmp_path: Path) -> None:
    """A model cannot mint its own exclusion/certification by constructing the dataclass
    directly -- restamp_model_authored (called by submit_draft) strips it regardless."""
    from dataclasses import replace

    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import Audit, Governance, Provenance, ProvenanceSource, ProvenanceStatus
    from governed_bi.corpus.store import load

    forged = replace(
        _few_shot("fs.forged"),
        governance=Governance(excluded=True, by="human"),
        audit=Audit(provenance=Provenance(source=ProvenanceSource.human, status=ProvenanceStatus.certified)),
    )
    submit_draft(tmp_path, forged)
    (written,) = [a for a in load(tmp_path)[0] if a.id == "fs.forged"]
    assert written.governance.excluded is False
    assert written.audit.provenance.status is ProvenanceStatus.proposed


def test_approve_refuses_an_asset_that_is_already_certified(tmp_path: Path) -> None:
    from governed_bi.corpus.drafts import DraftNotPending, approve_draft, submit_draft

    submit_draft(tmp_path, _few_shot("fs.twice"))
    approve_draft(tmp_path, "fs.twice")
    with pytest.raises(DraftNotPending):
        approve_draft(tmp_path, "fs.twice")


def test_approve_refuses_an_unknown_id(tmp_path: Path) -> None:
    from governed_bi.corpus.drafts import DraftNotFound, approve_draft

    with pytest.raises(DraftNotFound):
        approve_draft(tmp_path, "fs.never-written")


def test_submit_extra_survives_restamp_and_is_visible_after_approval(tmp_path: Path) -> None:
    """The Enhancer conflict-flag hook: restamp_model_authored rebuilds audit from scratch,
    so `extra` has to be merged back in *after*, or a conflict flag would be silently
    dropped on write -- the same silent-loss shape this whole feature exists to avoid."""
    from governed_bi.corpus.drafts import approve_draft, submit_draft
    from governed_bi.corpus.store import load

    submit_draft(tmp_path, _few_shot("fs.flagged"), extra={"conflict_with": "metric.other"})
    (written,) = [a for a in load(tmp_path)[0] if a.id == "fs.flagged"]
    assert written.audit.extra["conflict_with"] == "metric.other"

    certified = approve_draft(tmp_path, "fs.flagged")
    assert certified.audit.extra["conflict_with"] == "metric.other"  # not clobbered by approval
