"""curator/clarification.py: an answered clarification becomes a TermAsset draft."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

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


# ── fold_answered_clarification (Phase 1c): the Enhancer path shared with mine_corpus_node ──
#
# Ported intent of tests/serve/test_mine_corpus.py's Phase 3 scenarios (novel/duplicate/
# conflict/EnhancerError-fallback) -- this is the exact logic `mine_corpus_node` now delegates
# to, so a second caller (the offline `/clarifications/{id}/answer` route) reaches the same
# behavior rather than a parallel reimplementation.


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


def test_fold_answered_clarification_writes_a_novel_draft_with_no_existing_assets(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.clarification import fold_answered_clarification

    fold_answered_clarification(
        None, tmp_path, "what does active mean?", "90 days", schema="olist", known_assets=(),
    )
    assets, problems = load(tmp_path)
    assert not problems
    (draft,) = assets
    assert draft.asset_type.value == "term"
    assert "90 days" in draft.summary


def test_fold_answered_clarification_duplicate_of_a_certified_term_writes_no_new_file(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load, write
    from governed_bi.curator.clarification import fold_answered_clarification

    existing = _certified_term("clarification.olist.existing1", "active customer — placed an order in 90 days")
    write(tmp_path, existing, namespace="olist")

    fold_answered_clarification(
        _scripted(f'{{"duplicate_of": "{existing.id}", "conflict_with": null}}'),
        tmp_path,
        "what does active mean?",
        "an active customer ordered in the last 90 days",
        schema="olist",
        known_assets=[existing],
    )
    assets, problems = load(tmp_path)
    assert not problems
    assert assets == [existing]  # nothing new was minted


def test_fold_answered_clarification_conflict_with_a_certified_term_writes_a_flagged_draft(tmp_path: Path) -> None:
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load, write
    from governed_bi.curator.clarification import fold_answered_clarification

    existing = _certified_term("clarification.olist.existing2", "customer id means kunde_id")
    write(tmp_path, existing, namespace="olist")

    fold_answered_clarification(
        _scripted(f'{{"duplicate_of": null, "conflict_with": "{existing.id}"}}'),
        tmp_path,
        "what does customer id mean?",
        "customer id means transaktions_kunde_id",
        schema="olist",
        known_assets=[existing],
    )
    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    (draft,) = new_drafts
    assert draft.audit.provenance.status is ProvenanceStatus.proposed
    assert draft.audit.extra["conflict_with"] == existing.id


def test_fold_answered_clarification_only_compares_against_certified_terms(tmp_path: Path) -> None:
    """A ``proposed`` (not yet certified) term must not enter the comparison set -- matching
    ``mine_corpus_node``'s own ``_is_certified`` filter, now shared rather than duplicated.
    """
    from governed_bi.corpus.schema import Audit, Provenance, ProvenanceSource, ProvenanceStatus, TermAsset
    from governed_bi.corpus.store import load, write
    from governed_bi.curator.clarification import fold_answered_clarification

    proposed = TermAsset(
        id="clarification.olist.proposed1",
        name="proposed1",
        summary="not yet certified",
        audit=Audit(provenance=Provenance(source=ProvenanceSource.human, status=ProvenanceStatus.proposed)),
    )
    write(tmp_path, proposed, namespace="olist")

    # A model that would raise if it were ever asked to compare (no ids offered) -- proves
    # `existing` was empty, not merely that the model happened to say "novel".
    fold_answered_clarification(
        None, tmp_path, "q", "a", schema="olist", known_assets=[proposed],
    )
    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != proposed.id]
    assert len(new_drafts) == 1


def test_fold_answered_clarification_enhancer_error_falls_back_to_unconditional_write(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load, write
    from governed_bi.curator.clarification import fold_answered_clarification

    existing = _certified_term("clarification.olist.existing3", "some certified fact")
    write(tmp_path, existing, namespace="olist")

    fold_answered_clarification(
        _scripted("not json at all"),  # decide() raises EnhancerError
        tmp_path,
        "q",
        "90 days",
        schema="olist",
        known_assets=[existing],
    )
    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    assert len(new_drafts) == 1  # mined anyway, despite the broken dedup check


def test_fold_answered_clarification_never_raises_on_a_broken_draft(tmp_path: Path) -> None:
    """Best-effort, matching the node this was extracted from: a failure building or writing
    the draft must not propagate to the caller (mining/folding is never fatal to whatever
    answered the question).
    """
    from governed_bi.curator.clarification import fold_answered_clarification

    fold_answered_clarification(None, "/nonexistent/not/writable", "q", "a", schema="olist", known_assets=())


# ── fold_ledger_answer_into_corpus (Phase 1c): the offline ledger's own entry point ──────────


def _record(**overrides: Any) -> Any:
    from governed_bi.curator.clarifications import ClarificationRecord

    defaults: dict[str, Any] = dict(id="q001", scope="s", question="what does active mean?", answer="90 days")
    defaults.update(overrides)
    return ClarificationRecord(**defaults)


def test_fold_ledger_answer_skips_ranking_ambiguity(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import write_clarifications
    from governed_bi.curator.clarification import fold_ledger_answer_into_corpus
    from governed_bi.corpus.store import load

    record = _record(basis="ranking_ambiguity")
    write_clarifications(tmp_path, [record])

    updated = fold_ledger_answer_into_corpus(
        record, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    assert updated.converted_to_corpus is False
    assets, _ = load(tmp_path)
    assert assets == []


def test_fold_ledger_answer_treats_missing_basis_as_data_definition_eligible(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import write_clarifications
    from governed_bi.curator.clarification import fold_ledger_answer_into_corpus
    from governed_bi.corpus.store import load

    record = _record(basis=None)
    write_clarifications(tmp_path, [record])

    updated = fold_ledger_answer_into_corpus(
        record, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    assert updated.converted_to_corpus is True
    assets, _ = load(tmp_path)
    (draft,) = assets
    assert "90 days" in draft.summary


def test_fold_ledger_answer_folds_data_definition_and_marks_converted(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import write_clarifications
    from governed_bi.curator.clarification import fold_ledger_answer_into_corpus
    from governed_bi.corpus.store import load

    record = _record(basis="data_definition")
    write_clarifications(tmp_path, [record])

    updated = fold_ledger_answer_into_corpus(
        record, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    assert updated.converted_to_corpus is True
    assets, _ = load(tmp_path)
    assert len(assets) == 1


def test_fold_ledger_answer_twice_does_not_double_write(tmp_path: Path) -> None:
    """Idempotency: a record already ``converted_to_corpus`` must not fold a second time --
    reachable in production only if this ever gets called twice for one record (there is no
    re-answer flow today), exercised here directly since that is the only way to reach it.
    """
    from governed_bi.curator.clarifications import load_clarifications, write_clarifications
    from governed_bi.curator.clarification import fold_ledger_answer_into_corpus
    from governed_bi.corpus.store import load

    record = _record(basis="data_definition")
    write_clarifications(tmp_path, [record])

    once = fold_ledger_answer_into_corpus(
        record, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    assert once.converted_to_corpus is True
    assets_after_first, _ = load(tmp_path)
    (draft,) = assets_after_first
    written_at = (tmp_path / "olist" / f"{draft.id}.yaml").stat().st_mtime

    (persisted,) = load_clarifications(tmp_path)
    twice = fold_ledger_answer_into_corpus(
        persisted, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    assert twice.converted_to_corpus is True
    assets_after, _ = load(tmp_path)
    assert len(assets_after) == 1, f"the fold ran twice: {[a.id for a in assets_after]}"
    assert (
        tmp_path / "olist" / f"{assets_after[0].id}.yaml"
    ).stat().st_mtime == written_at, "the draft file was rewritten on the second call"


def test_fold_ledger_answer_with_no_answer_text_is_a_no_op(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import write_clarifications
    from governed_bi.curator.clarification import fold_ledger_answer_into_corpus
    from governed_bi.corpus.store import load

    record = _record(answer=None, basis="data_definition")
    write_clarifications(tmp_path, [record])

    updated = fold_ledger_answer_into_corpus(
        record, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    assert updated.converted_to_corpus is False
    assets, _ = load(tmp_path)
    assert assets == []
