"""``serve/nodes/mine_mistakes.py`` -- unit tests, the node called directly.

Mirrors ``tests/serve/test_mine_corpus.py``'s own discipline: these are the direct-call
scenarios (knob gating, Enhancer dedup/conflict/fallback), and
``tests/serve/test_mine_mistakes_transport.py`` is the full-``compile_graph()`` round trip that
proves the node actually fires for a real, agent-driven correction sequence -- not just that
``mine_mistake_from_execution`` returns the right dataclass when called directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governed_bi.serve.nodes.mine_mistakes import mine_mistakes_node


def _attempt(*, passed: bool, executed_sql: str | None, verdict_layer: str | None = None,
             reason_code: str = "ok") -> dict[str, Any]:
    return {
        "verdict_layer": verdict_layer,
        "passed": passed,
        "reason_code": reason_code,
        "path": "agent",
        "executed_sql": executed_sql,
    }


#: Refused, then a corrected attempt passes -- the one pattern that is mineable.
_CORRECTED_EXECUTION = {
    "attempts": [
        _attempt(passed=False, executed_sql=None, verdict_layer="r_table_not_licensed",
                  reason_code="r_table_not_licensed"),
        _attempt(passed=True, executed_sql="SELECT count(*) FROM sales.orders"),
    ],
    "terminal": "answered",
    "guardrail_errors": 0,
}

#: The first (and only) attempt already passed -- nothing to learn.
_CLEAN_EXECUTION = {
    "attempts": [_attempt(passed=True, executed_sql="SELECT count(*) FROM sales.orders")],
    "terminal": "answered",
    "guardrail_errors": 0,
}


def _config(
    corpus_root: Path | None, *, agent_model: object | None = None, assets_by_id: dict | None = None,
) -> dict[str, Any]:
    conf: dict[str, Any] = {"assets_by_id": assets_by_id or {}}
    if corpus_root is not None:
        conf["corpus_root"] = corpus_root
    if agent_model is not None:
        conf["agent_model"] = agent_model
    return {"configurable": conf}


def _state(execution: dict[str, Any], *, db_id: str = "sales",
           knobs_resolved: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "question": "how many orders were placed",
        "db_id": db_id,
        "knobs_resolved": (
            knobs_resolved if knobs_resolved is not None
            else {"enable_mistake_memory_mining": True}
        ),
        "execution": execution,
    }


def _certified_few_shot(asset_id: str, summary: str, sql: str = "SELECT 1") -> Any:
    from governed_bi.corpus.schema import Audit, FewShotAsset, Provenance, ProvenanceSource, ProvenanceStatus

    return FewShotAsset(
        id=asset_id,
        schema="sales",
        sql=sql,
        summary=summary,
        audit=Audit(provenance=Provenance(source=ProvenanceSource.human, status=ProvenanceStatus.certified)),
    )


def _scripted(response_json: str) -> Any:
    from langchain_core.messages import AIMessage

    from governed_bi.serve.scripted_model import ScriptedChatModel

    return ScriptedChatModel(responses=[AIMessage(content=response_json)])


def test_mines_nothing_when_the_knob_is_off_by_default(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    update = mine_mistakes_node(_state(_CORRECTED_EXECUTION, knobs_resolved={}), _config(tmp_path))
    assert update == {}
    assets, _ = load(tmp_path)
    assert assets == []


def test_never_raises_when_the_corpus_root_is_missing() -> None:
    update = mine_mistakes_node(_state(_CORRECTED_EXECUTION), _config(None))
    assert update == {}


def test_mines_a_draft_when_the_knob_is_on_and_the_turn_self_corrected(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    update = mine_mistakes_node(_state(_CORRECTED_EXECUTION), _config(tmp_path))
    assert update == {}
    assets, problems = load(tmp_path)
    assert not problems
    (draft,) = assets
    assert draft.asset_type.value == "few_shot"
    assert draft.sql == "SELECT count(*) FROM sales.orders"
    assert "r_table_not_licensed" in (draft.body or "")


def test_mines_nothing_when_the_first_attempt_already_passed(tmp_path: Path) -> None:
    """Nothing to learn: no earlier failure, so the offline algorithm this node reuses
    (``mine_mistake_from_execution``) returns ``None`` and the node writes nothing."""
    from governed_bi.corpus.store import load

    update = mine_mistakes_node(_state(_CLEAN_EXECUTION), _config(tmp_path))
    assert update == {}
    assets, _ = load(tmp_path)
    assert assets == []


def test_mines_nothing_when_no_attempt_ever_passed(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    execution = {
        "attempts": [_attempt(passed=False, executed_sql=None, verdict_layer="r_table_not_licensed")],
        "terminal": "refused",
        "guardrail_errors": 0,
    }
    update = mine_mistakes_node(_state(execution), _config(tmp_path))
    assert update == {}
    assets, _ = load(tmp_path)
    assert assets == []


def test_duplicate_of_a_certified_few_shot_writes_no_new_file(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load, write

    existing = _certified_few_shot("mistake.sales.existing1", "how many orders were placed")
    write(tmp_path, existing, namespace="sales")

    config = _config(
        tmp_path,
        agent_model=_scripted(f'{{"duplicate_of": "{existing.id}", "conflict_with": null}}'),
        assets_by_id={existing.id: existing},
    )
    mine_mistakes_node(_state(_CORRECTED_EXECUTION), config)

    assets, problems = load(tmp_path)
    assert not problems
    assert assets == [existing]  # nothing new was minted


def test_conflict_with_a_certified_few_shot_writes_a_flagged_draft(tmp_path: Path) -> None:
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load, write

    existing = _certified_few_shot("mistake.sales.existing2", "orders placed, a different join")
    write(tmp_path, existing, namespace="sales")

    config = _config(
        tmp_path,
        agent_model=_scripted(f'{{"duplicate_of": null, "conflict_with": "{existing.id}"}}'),
        assets_by_id={existing.id: existing},
    )
    mine_mistakes_node(_state(_CORRECTED_EXECUTION), config)

    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    (draft,) = new_drafts
    assert draft.audit is not None and draft.audit.provenance is not None
    assert draft.audit.provenance.status is ProvenanceStatus.proposed
    assert draft.audit.extra["conflict_with"] == existing.id


def test_enhancer_error_falls_back_to_the_old_unconditional_write(tmp_path: Path) -> None:
    """A broken dedup/conflict model call must not drop a real self-correction -- it degrades
    to a plain proposed draft rather than losing the mined fix."""
    from governed_bi.corpus.store import load, write

    existing = _certified_few_shot("mistake.sales.existing3", "some certified fact")
    write(tmp_path, existing, namespace="sales")

    config = _config(
        tmp_path,
        agent_model=_scripted("not json at all"),  # decide() raises EnhancerError
        assets_by_id={existing.id: existing},
    )
    mine_mistakes_node(_state(_CORRECTED_EXECUTION), config)

    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    assert len(new_drafts) == 1  # mined anyway, despite the broken dedup check
