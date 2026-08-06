"""curator/enhancer.py: dedup/conflict decision against existing certified assets."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


class _Existing:
    def __init__(self, id: str, summary: str) -> None:
        self.id = id
        self.summary = summary


def _scripted(response_json: str):
    from langchain_core.messages import AIMessage

    from governed_bi.serve.scripted_model import ScriptedChatModel

    return ScriptedChatModel(responses=[AIMessage(content=response_json)])


def test_no_existing_assets_is_always_novel() -> None:
    from governed_bi.curator.enhancer import decide

    model = _scripted('{"duplicate_of": null, "conflict_with": null}')
    decision = decide(model, "some candidate", existing=[])
    assert decision.duplicate_of is None
    assert decision.conflict_with is None
    assert model.prompts_seen == []  # never called -- nothing to compare against


def test_duplicate_is_recognized_and_the_id_is_validated() -> None:
    from governed_bi.curator.enhancer import decide

    existing = [_Existing("fs.rev", "total revenue by month")]
    model = _scripted('{"duplicate_of": "fs.rev", "conflict_with": null}')
    decision = decide(model, "monthly revenue total", existing=existing)
    assert decision.duplicate_of == "fs.rev"
    assert decision.conflict_with is None


def test_conflict_is_recognized() -> None:
    from governed_bi.curator.enhancer import decide

    existing = [_Existing("metric.net_rev", "net revenue = gross - discounts")]
    model = _scripted('{"duplicate_of": null, "conflict_with": "metric.net_rev"}')
    decision = decide(model, "net revenue = gross - discounts - returns", existing=existing)
    assert decision.conflict_with == "metric.net_rev"


def test_a_novel_candidate_sets_neither() -> None:
    from governed_bi.curator.enhancer import decide

    existing = [_Existing("fs.unrelated", "customer churn rate")]
    model = _scripted('{"duplicate_of": null, "conflict_with": null}')
    decision = decide(model, "average order value", existing=existing)
    assert decision.duplicate_of is None
    assert decision.conflict_with is None


def test_an_invented_id_raises_rather_than_being_trusted() -> None:
    """Rule 1/2: the model may reference only ids it was given."""
    from governed_bi.curator.enhancer import EnhancerError, decide

    existing = [_Existing("fs.rev", "total revenue by month")]
    model = _scripted('{"duplicate_of": "fs.made-up-id", "conflict_with": null}')
    with pytest.raises(EnhancerError, match="not one of the ids"):
        decide(model, "candidate", existing=existing)


def test_both_set_raises_rather_than_being_silently_narrowed() -> None:
    from governed_bi.curator.enhancer import EnhancerError, decide

    existing = [_Existing("a", "x"), _Existing("b", "y")]
    model = _scripted('{"duplicate_of": "a", "conflict_with": "b"}')
    with pytest.raises(EnhancerError, match="both"):
        decide(model, "candidate", existing=existing)


def test_unparseable_response_raises() -> None:
    from governed_bi.curator.enhancer import EnhancerError, decide

    existing = [_Existing("a", "x")]
    model = _scripted("not json at all")
    with pytest.raises(EnhancerError, match="could not parse"):
        decide(model, "candidate", existing=existing)


def test_a_fenced_json_response_is_still_parsed() -> None:
    from governed_bi.curator.enhancer import decide

    existing = [_Existing("a", "x")]
    model = _scripted('```json\n{"duplicate_of": "a", "conflict_with": null}\n```')
    decision = decide(model, "candidate", existing=existing)
    assert decision.duplicate_of == "a"


def _few_shot(asset_id: str, summary: str):
    from governed_bi.corpus.schema import FewShotAsset

    return FewShotAsset(id=asset_id, schema="s", sql="SELECT 1", summary=summary)


def test_apply_skips_the_write_on_a_duplicate(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.enhancer import apply

    existing = [_Existing("fs.rev", "total revenue by month")]
    model = _scripted('{"duplicate_of": "fs.rev", "conflict_with": null}')
    path, decision = apply(model, tmp_path, _few_shot("fs.new", "monthly revenue total"), existing=existing)
    assert path is None
    assert decision.duplicate_of == "fs.rev"
    assets, _ = load(tmp_path)
    assert assets == []  # nothing was minted


def test_apply_writes_a_conflict_flagged_draft(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.enhancer import apply

    existing = [_Existing("metric.net_rev", "net revenue = gross - discounts")]
    model = _scripted('{"duplicate_of": null, "conflict_with": "metric.net_rev"}')
    path, decision = apply(
        model, tmp_path, _few_shot("fs.new", "net revenue = gross - discounts - returns"), existing=existing,
    )
    assert path is not None
    assert decision.conflict_with == "metric.net_rev"
    (written,) = load(tmp_path)[0]
    assert written.audit.extra["conflict_with"] == "metric.net_rev"


def test_apply_writes_a_plain_draft_when_novel(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.enhancer import apply

    existing = [_Existing("fs.unrelated", "customer churn rate")]
    model = _scripted('{"duplicate_of": null, "conflict_with": null}')
    path, decision = apply(model, tmp_path, _few_shot("fs.new", "average order value"), existing=existing)
    assert path is not None
    assert decision.duplicate_of is None and decision.conflict_with is None
    (written,) = load(tmp_path)[0]
    assert "conflict_with" not in written.audit.extra
