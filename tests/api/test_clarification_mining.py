"""api/routes.py::_mine_clarification_draft -- the resume-time wiring, tested directly.

Not through the full HTTP/graph stack: that needs a real interrupt-then-resume round trip,
which tests/serve/test_agent_tools_hitl.py already exercises for the resume mechanics
themselves. This is about the one thing this port adds on top of a successful resume.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


class _Session:
    def __init__(self, corpus_root: Path | None, db_id: str = "olist") -> None:
        self.corpus_root = corpus_root
        self.db_id = db_id


def _pending(question: str = "what does active customer mean?") -> dict:
    return {"kind": "clarification", "clarification_id": "c1", "question": question, "why": "ambiguous"}


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
