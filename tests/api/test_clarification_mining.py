"""api/routes.py::_mine_clarification_draft -- the resume-time wiring, tested directly.

Not through the full HTTP/graph stack: that needs a real interrupt-then-resume round trip,
which tests/serve/test_agent_tools_hitl.py already exercises for the resume mechanics
themselves. This is about the one thing this port adds on top of a successful resume.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


class _Session:
    def __init__(
        self,
        corpus_root: Path | None,
        db_id: str = "olist",
        *,
        agent_model: object | None = None,
        assets_by_id: dict | None = None,
        knobs_resolved: dict | None = None,
    ) -> None:
        self.corpus_root = corpus_root
        self.db_id = db_id
        self.agent_model = agent_model
        self.assets_by_id = assets_by_id or {}
        self.knobs_resolved = knobs_resolved or {}


def _pending(question: str = "what does active customer mean?") -> dict:
    return {"kind": "clarification", "clarification_id": "c1", "question": question, "why": "ambiguous"}


def _certified_term(asset_id: str, summary: str) -> Any:
    from governed_bi.corpus.schema import Audit, Provenance, ProvenanceSource, ProvenanceStatus, TermAsset

    return TermAsset(
        id=asset_id,
        name=asset_id,
        summary=summary,
        audit=Audit(provenance=Provenance(source=ProvenanceSource.human, status=ProvenanceStatus.certified)),
    )


def _scripted(response_json: str) -> Any:
    from langchain_core.messages import AIMessage

    from governed_bi.serve.scripted_model import ScriptedChatModel

    return ScriptedChatModel(responses=[AIMessage(content=response_json)])


def _mining_out() -> dict:
    return {
        "knobs_resolved": {"enable_clarification_to_draft": True},
        "clarifications": [{"clarification_id": "c1", "basis": "data_definition"}],
    }


def test_mines_nothing_when_the_knob_is_off_by_default(tmp_path: Path) -> None:
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.store import load

    session = _Session(tmp_path)
    _mine_clarification_draft(session, _pending(), {"answer": "90 days"}, out={})
    assets, _ = load(tmp_path)
    assert assets == []


def test_mines_a_draft_when_the_knob_is_on(tmp_path: Path) -> None:
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.store import load

    session = _Session(tmp_path)
    out = {
        "knobs_resolved": {"enable_clarification_to_draft": True},
        "clarifications": [{"clarification_id": "c1", "basis": "data_definition"}],
    }
    _mine_clarification_draft(session, _pending(), {"answer": "90 days"}, out=out)
    assets, problems = load(tmp_path)
    assert not problems
    (draft,) = assets
    assert draft.asset_type.value == "term"
    assert "90 days" in draft.summary


def test_mines_nothing_when_basis_is_ranking_ambiguity_even_with_the_knob_on(tmp_path: Path) -> None:
    """Phase 2: a ranking/superlative answer ("best" means X) is a judgment call for the one
    question that asked it, not a durable schema fact -- it must never reach the shared
    corpus, regardless of ``enable_clarification_to_draft``.
    """
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.store import load

    session = _Session(tmp_path)
    out = {
        "knobs_resolved": {"enable_clarification_to_draft": True},
        "clarifications": [{"clarification_id": "c1", "basis": "ranking_ambiguity"}],
    }
    _mine_clarification_draft(session, _pending(), {"answer": "total lifetime spend"}, out=out)
    assets, _ = load(tmp_path)
    assert assets == []


def test_mines_nothing_on_a_decline_even_with_the_knob_on(tmp_path: Path) -> None:
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.store import load

    session = _Session(tmp_path)
    out = {"knobs_resolved": {"enable_clarification_to_draft": True}}
    _mine_clarification_draft(session, _pending(), {"declined": True}, out=out)
    assets, _ = load(tmp_path)
    assert assets == []


def test_never_raises_when_the_corpus_root_is_missing() -> None:
    from governed_bi.api.routes import _mine_clarification_draft

    session = _Session(corpus_root=None)
    out = {"knobs_resolved": {"enable_clarification_to_draft": True}}
    _mine_clarification_draft(session, _pending(), {"answer": "90 days"}, out=out)  # no raise


# ── Phase 3: Enhancer wired into the mining path ───────────────────────────────────────────
#
# ported intent of v1's test_round_c_conflicts.py: a data-definition answer must be checked
# against already-certified TermAssets before becoming its own independent draft.


def test_duplicate_of_a_certified_term_writes_no_new_file(tmp_path: Path) -> None:
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.existing1", "active customer — placed an order in 90 days")
    write(tmp_path, existing, namespace="olist")

    session = _Session(
        tmp_path,
        agent_model=_scripted(f'{{"duplicate_of": "{existing.id}", "conflict_with": null}}'),
        assets_by_id={existing.id: existing},
    )
    _mine_clarification_draft(
        session, _pending(), {"answer": "an active customer ordered in the last 90 days"}, out=_mining_out()
    )

    assets, problems = load(tmp_path)
    assert not problems
    assert assets == [existing]  # nothing new was minted


def test_conflict_with_a_certified_term_writes_a_flagged_draft(tmp_path: Path) -> None:
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.existing2", "customer id means kunde_id")
    write(tmp_path, existing, namespace="olist")

    session = _Session(
        tmp_path,
        agent_model=_scripted(f'{{"duplicate_of": null, "conflict_with": "{existing.id}"}}'),
        assets_by_id={existing.id: existing},
    )
    _mine_clarification_draft(
        session, _pending(), {"answer": "customer id means transaktions_kunde_id"}, out=_mining_out()
    )

    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    (draft,) = new_drafts
    assert draft.audit is not None
    assert draft.audit.provenance is not None
    assert draft.audit.provenance.status is ProvenanceStatus.proposed
    assert draft.audit.extra["conflict_with"] == existing.id


def test_novel_answer_writes_a_plain_draft_with_no_conflict_flag(tmp_path: Path) -> None:
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.unrelated", "region means the shipping region")
    write(tmp_path, existing, namespace="olist")

    session = _Session(
        tmp_path,
        agent_model=_scripted('{"duplicate_of": null, "conflict_with": null}'),
        assets_by_id={existing.id: existing},
    )
    _mine_clarification_draft(session, _pending(), {"answer": "90 days"}, out=_mining_out())

    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    (draft,) = new_drafts
    assert draft.audit.provenance.status is ProvenanceStatus.proposed
    assert "conflict_with" not in draft.audit.extra


def test_enhancer_error_falls_back_to_the_old_unconditional_write(tmp_path: Path) -> None:
    """A broken dedup/conflict model call must not drop a real user answer -- it degrades to
    the pre-Phase-3 behavior (plain proposed draft, no dedup check) rather than losing the
    clarification or breaking the resumed turn.
    """
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.existing3", "some certified fact")
    write(tmp_path, existing, namespace="olist")

    session = _Session(
        tmp_path,
        agent_model=_scripted("not json at all"),  # decide() raises EnhancerError
        assets_by_id={existing.id: existing},
    )
    _mine_clarification_draft(session, _pending(), {"answer": "90 days"}, out=_mining_out())

    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    assert len(new_drafts) == 1  # mined anyway, despite the broken dedup check


def test_conflict_flagged_draft_is_excluded_from_for_analyst(tmp_path: Path) -> None:
    """Regression: ``for_analyst()`` already excludes non-certified assets purely on
    ``provenance_status`` (2026-08-06 porting work) -- verified explicitly here for this new
    conflict-flagged code path rather than assumed, since that is what the SQL-writing agent
    actually sees.
    """
    from governed_bi.api.routes import _mine_clarification_draft
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.existing4", "customer id means kunde_id")
    write(tmp_path, existing, namespace="olist")

    session = _Session(
        tmp_path,
        agent_model=_scripted(f'{{"duplicate_of": null, "conflict_with": "{existing.id}"}}'),
        assets_by_id={existing.id: existing},
    )
    _mine_clarification_draft(
        session, _pending(), {"answer": "customer id means transaktions_kunde_id"}, out=_mining_out()
    )

    assets, problems = load(tmp_path)
    assert not problems
    (conflict_draft,) = [a for a in assets if a.id != existing.id]
    assert conflict_draft.audit.extra["conflict_with"] == existing.id

    corpus = for_analyst(assets)
    assert existing.id in corpus.by_id
    assert conflict_draft.id not in corpus.by_id
