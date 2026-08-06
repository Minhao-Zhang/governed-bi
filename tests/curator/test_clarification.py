"""curator/clarification.py: an answered clarification becomes a TermAsset draft."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def test_resolved_answer_text_is_none_on_decline() -> None:
    from governed_bi.curator.clarification import resolved_answer_text

    assert resolved_answer_text({"declined": True}) is None
    assert resolved_answer_text({"declined": True, "answer": "ignored"}) is None


def test_resolved_answer_text_reads_answer_then_choice_id_then_text() -> None:
    from governed_bi.curator.clarification import resolved_answer_text

    assert resolved_answer_text({"answer": "active means 90 days"}) == "active means 90 days"
    assert resolved_answer_text({"choice_id": "opt_a"}) == "opt_a"
    assert resolved_answer_text({}) is None


def test_draft_from_clarification_shape() -> None:
    from governed_bi.curator.clarification import draft_from_clarification

    draft = draft_from_clarification(
        "what does 'active customer' mean?", "made a purchase in the last 90 days", schema="olist",
    )
    assert draft.asset_type.value == "term"
    assert draft.name == "what does 'active customer' mean?"
    assert "active customer" in draft.summary
    assert "90 days" in draft.summary
    assert "Q: what does 'active customer' mean?" in draft.body
    assert "A: made a purchase in the last 90 days" in draft.body


def test_draft_id_is_deterministic_and_scoped_to_schema() -> None:
    from governed_bi.curator.clarification import draft_from_clarification

    a = draft_from_clarification("q", "a", schema="olist")
    b = draft_from_clarification("q", "a", schema="olist")
    c = draft_from_clarification("q", "a", schema="beer_factory")
    assert a.id == b.id
    assert a.id != c.id


def test_long_question_and_answer_are_truncated_in_summary_but_not_body() -> None:
    from governed_bi.curator.clarification import draft_from_clarification
    from governed_bi.register.knobs import knob_default

    question = "why " * 100
    draft = draft_from_clarification(question, "an answer", schema="s")
    assert len(draft.summary) <= int(knob_default("summary_max_chars"))
    assert question.strip() in draft.body


def test_draft_submits_and_is_invisible_until_approved(tmp_path: Path) -> None:
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.drafts import approve_draft, submit_draft
    from governed_bi.corpus.store import load
    from governed_bi.curator.clarification import draft_from_clarification

    draft = draft_from_clarification("what is a good-standing vendor?", "rating >= 3.5", schema="olist")
    submit_draft(tmp_path, draft, namespace="olist")

    assets, problems = load(tmp_path)
    assert not problems
    assert draft.id not in for_analyst(assets).by_id

    approve_draft(tmp_path, draft.id)
    assets_after, _ = load(tmp_path)
    assert draft.id in for_analyst(assets_after).by_id
