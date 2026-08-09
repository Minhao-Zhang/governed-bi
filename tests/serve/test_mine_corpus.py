"""``serve/nodes/mine_corpus.py`` -- unit tests, the node called directly.

Ported from ``tests/api/test_clarification_mining.py`` (Phase 2/3's tests of
``api/routes.py::_mine_clarification_draft``), the function this node replaced when mining
moved from the HTTP route layer into the compiled graph -- see the node's own module
docstring for why. Same scenarios and assertions; only the call shape changes, from
``_mine_clarification_draft(session, pending, reply, out)`` to
``mine_corpus_node(state, config)``: a graph node has no ``session`` object and reads
everything off ``state``/``configurable(config)`` instead.

Not through the full HTTP/graph stack: that needs a real interrupt-then-resume round trip,
which ``tests/serve/test_clarification_mining_transport.py`` exercises for both real
transports -- the thing this port's own gap-fixing loop found this file's predecessor could
not prove on its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governed_bi.serve.nodes.mine_corpus import mine_corpus_node


def _config(
    corpus_root: Path | None,
    *,
    agent_model: object | None = None,
    assets_by_id: dict | None = None,
) -> dict[str, Any]:
    conf: dict[str, Any] = {"assets_by_id": assets_by_id or {}}
    if corpus_root is not None:
        conf["corpus_root"] = corpus_root
    if agent_model is not None:
        conf["agent_model"] = agent_model
    return {"configurable": conf}


def _state(
    clarifications: list[dict[str, Any]],
    *,
    db_id: str = "olist",
    knobs_resolved: dict[str, Any] | None = None,
    already_mined: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "db_id": db_id,
        "knobs_resolved": (
            knobs_resolved
            if knobs_resolved is not None
            else {"enable_clarification_to_draft": True}
        ),
        "clarifications": clarifications,
        "clarifications_mined": already_mined or [],
    }


def _clarification(
    *,
    clarification_id: str = "c1",
    question: str = "what does active customer mean?",
    answer: str = "90 days",
    basis: str = "data_definition",
    declined: bool = False,
) -> dict[str, Any]:
    return {
        "clarification_id": clarification_id,
        "question": question,
        "why": "ambiguous",
        "answer": answer,
        "turn_id": "turn-1",
        "basis": basis,
        "declined": declined,
    }


def _certified_term(asset_id: str, summary: str) -> Any:
    from governed_bi.corpus.schema import (
        Audit,
        Provenance,
        ProvenanceSource,
        ProvenanceStatus,
        TermAsset,
    )

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


def test_mines_nothing_when_the_knob_is_off_by_default(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    update = mine_corpus_node(_state([_clarification()], knobs_resolved={}), _config(tmp_path))
    assert update == {}
    assets, _ = load(tmp_path)
    assert assets == []


def test_mines_a_draft_when_the_knob_is_on(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    update = mine_corpus_node(_state([_clarification()]), _config(tmp_path))
    assert update == {"clarifications_mined": ["c1"]}
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
    from governed_bi.corpus.store import load

    clar = _clarification(basis="ranking_ambiguity", answer="total lifetime spend")
    update = mine_corpus_node(_state([clar]), _config(tmp_path))
    # Still marked processed: a skip is not retried on a later turn either.
    assert update == {"clarifications_mined": ["c1"]}
    assets, _ = load(tmp_path)
    assert assets == []


def test_mines_nothing_on_a_decline_even_with_the_knob_on(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    clar = _clarification(declined=True, answer="The user declined to answer this clarification.")
    update = mine_corpus_node(_state([clar]), _config(tmp_path))
    assert update == {"clarifications_mined": ["c1"]}
    assets, _ = load(tmp_path)
    assert assets == []


def test_never_raises_when_the_corpus_root_is_missing() -> None:
    update = mine_corpus_node(_state([_clarification()]), _config(None))
    assert update == {}


def test_already_mined_clarifications_are_not_reprocessed(tmp_path: Path) -> None:
    """The guard ``api/routes.py``'s version never needed: ``ServeState.clarifications``
    accumulates across the whole thread under the checkpointer, so a node reading it sees
    every clarification the thread has ever answered, not just this turn's. Without
    ``clarifications_mined`` this would re-mine the whole history on every later turn --
    and ``corpus/store.py::write`` overwrites the same asset id silently, which would revert
    a since-approved/-certified draft back to ``proposed`` with no admin action involved.
    """
    from governed_bi.corpus.store import load

    state = _state([_clarification()], already_mined=["c1"])
    update = mine_corpus_node(state, _config(tmp_path))
    assert update == {}
    assets, _ = load(tmp_path)
    assert assets == []


def test_only_the_unmined_entry_is_processed_when_two_are_present(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    old = _clarification(clarification_id="c-old", question="old question?", answer="old answer")
    new = _clarification(clarification_id="c-new", question="new question?", answer="new answer")
    state = _state([old, new], already_mined=["c-old"])
    update = mine_corpus_node(state, _config(tmp_path))
    assert update == {"clarifications_mined": ["c-new"]}
    assets, _ = load(tmp_path)
    (draft,) = assets
    assert "new answer" in draft.summary


# ── Phase 3: Enhancer wired into the mining path ───────────────────────────────────────────
#
# ported intent of v1's test_round_c_conflicts.py: a data-definition answer must be checked
# against already-certified TermAssets before becoming its own independent draft.


def test_duplicate_of_a_certified_term_writes_no_new_file(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load, write

    existing = _certified_term(
        "clarification.olist.existing1", "active customer — placed an order in 90 days"
    )
    write(tmp_path, existing, namespace="olist")

    config = _config(
        tmp_path,
        agent_model=_scripted(f'{{"duplicate_of": "{existing.id}", "conflict_with": null}}'),
        assets_by_id={existing.id: existing},
    )
    clar = _clarification(answer="an active customer ordered in the last 90 days")
    mine_corpus_node(_state([clar]), config)

    assets, problems = load(tmp_path)
    assert not problems
    assert assets == [existing]  # nothing new was minted


def test_conflict_with_a_certified_term_writes_a_flagged_draft(tmp_path: Path) -> None:
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.existing2", "customer id means kunde_id")
    write(tmp_path, existing, namespace="olist")

    config = _config(
        tmp_path,
        agent_model=_scripted(f'{{"duplicate_of": null, "conflict_with": "{existing.id}"}}'),
        assets_by_id={existing.id: existing},
    )
    clar = _clarification(answer="customer id means transaktions_kunde_id")
    mine_corpus_node(_state([clar]), config)

    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    (draft,) = new_drafts
    assert draft.audit is not None
    assert draft.audit.provenance is not None
    assert draft.audit.provenance.status is ProvenanceStatus.proposed
    assert draft.audit.extra["conflict_with"] == existing.id


def test_novel_answer_writes_a_plain_draft_with_no_conflict_flag(tmp_path: Path) -> None:
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.unrelated", "region means the shipping region")
    write(tmp_path, existing, namespace="olist")

    config = _config(
        tmp_path,
        agent_model=_scripted('{"duplicate_of": null, "conflict_with": null}'),
        assets_by_id={existing.id: existing},
    )
    clar = _clarification(answer="90 days")
    mine_corpus_node(_state([clar]), config)

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
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.existing3", "some certified fact")
    write(tmp_path, existing, namespace="olist")

    config = _config(
        tmp_path,
        agent_model=_scripted("not json at all"),  # decide() raises EnhancerError
        assets_by_id={existing.id: existing},
    )
    clar = _clarification(answer="90 days")
    mine_corpus_node(_state([clar]), config)

    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    assert len(new_drafts) == 1  # mined anyway, despite the broken dedup check


def test_conflict_flagged_draft_is_excluded_from_for_analyst(tmp_path: Path) -> None:
    """Regression: ``for_analyst()`` already excludes non-certified assets purely on
    ``provenance_status`` -- verified explicitly here for this conflict-flagged code path
    rather than assumed, since that is what the SQL-writing agent actually sees.
    """
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.store import load, write

    existing = _certified_term("clarification.olist.existing4", "customer id means kunde_id")
    write(tmp_path, existing, namespace="olist")

    config = _config(
        tmp_path,
        agent_model=_scripted(f'{{"duplicate_of": null, "conflict_with": "{existing.id}"}}'),
        assets_by_id={existing.id: existing},
    )
    clar = _clarification(answer="customer id means transaktions_kunde_id")
    mine_corpus_node(_state([clar]), config)

    assets, problems = load(tmp_path)
    assert not problems
    (conflict_draft,) = [a for a in assets if a.id != existing.id]
    assert conflict_draft.audit.extra["conflict_with"] == existing.id

    corpus = for_analyst(assets)
    assert existing.id in corpus.by_id
    assert conflict_draft.id not in corpus.by_id
