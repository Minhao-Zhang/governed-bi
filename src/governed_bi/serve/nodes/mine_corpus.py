"""``mine_corpus`` -- an answered ``ask_user`` clarification becomes a corpus draft.

See also ``serve/nodes/mine_mistakes.py``, wired in right after this node on the identical
reasoning: a self-corrected ``run_query`` ledger is a second class of "durable fact extraction
after a turn" fact and needed the same relocation from an offline-only script.

**Relocated from ``api/routes.py``'s ``_mine_clarification_draft`` (UtkuAI, ported).** That
function's only call site was ``POST /chat/resume``, whose own docstring calls it a
"degradation path -- streaming is the primary transport". The real ``governed-bi-ui`` never
calls it: it resumes a paused ``ask_user`` interrupt through LangGraph Server's own
``/threads/{id}/runs/stream``, which invokes the compiled graph directly and never passes
through ``routes.py``'s FastAPI app at all. Net effect, confirmed live: no real end-user
interaction through the shipped product could ever populate a corpus draft.

``graph.invoke()``/``graph.astream()`` on the compiled graph is the one thing every transport
actually calls -- ``/chat/resume`` (via ``resume_clarification``), LangGraph Server's native
resume, the CLI, and ``eval/``. Putting the mining logic in a node here, rather than behind any
one HTTP route, is what makes it unskippable by a transport that has never been written yet.

This module relocates *where mining runs*, from a route to a graph node that runs after
``agent_core`` on every turn. The ``basis``/decline/defer gate and per-turn dedup
(``clarifications_mined``) below are specific to reading ``state["clarifications"]`` and stay
here; the actual "build a draft, run it through Enhancer, write accordingly" logic moved on to
``curator/clarification.py::fold_answered_clarification`` (Phase 1c, this initiative), shared
with the offline ``POST /clarifications/{id}/answer`` route (via ``curator/clarification.py::
fold_ledger_answer_into_corpus``) rather than duplicated for it.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from governed_bi.serve.runtime import bool_knob, configurable

__all__ = ["mine_corpus_node"]


def mine_corpus_node(state: dict, config: RunnableConfig) -> dict:
    """Mine every newly-resolved clarification in ``state["clarifications"]`` into a draft.

    Gated exactly as before: ``enable_clarification_to_draft`` (off by default, read off
    ``state`` the same way every other knob is -- ``state`` -> ``knobs_resolved`` -> register
    default) and ``corpus_root is not None`` (via :func:`~governed_bi.serve.runtime.configurable`,
    the shared reader every node uses for session-scoped resources -- see
    ``tests/serve/test_trusted_constants.py::test_every_node_reads_config_through_the_shared_reader``).

    **Why a dedup guard is needed here and was not needed in ``routes.py``.**
    ``ServeState.clarifications`` accumulates across the whole thread under the checkpointer
    (``operator.add``), so a node reading it sees every clarification ever answered on this
    thread, not just this turn's. ``routes.py``'s version never had this problem: it ran
    exactly once per resume, called directly by ``/chat/resume`` with the one clarification
    that resume just answered. A graph node runs on every turn, so without
    ``clarifications_mined`` it would re-mine the whole history on every later turn --
    harmless for an unreviewed draft (``corpus/store.py::write`` overwrites the same asset id
    cleanly), but not for one an admin has since approved or certified: a re-mine would
    silently revert it back to ``proposed`` with no admin action involved.
    """
    cfg = configurable(config)
    corpus_root = cfg.get("corpus_root")
    if corpus_root is None or not bool_knob(state, "enable_clarification_to_draft"):
        return {}

    already_mined = set(state.get("clarifications_mined") or ())
    pending = [
        record
        for record in (state.get("clarifications") or ())
        if isinstance(record, dict) and record.get("clarification_id") not in already_mined
    ]
    if not pending:
        return {}

    from governed_bi.curator.clarification import fold_answered_clarification

    schema = state.get("db_id")
    agent_model = cfg.get("agent_model")
    assets_by_id = cfg.get("assets_by_id") or {}
    write_model = (state.get("knobs_resolved") or {}).get("llm_model")

    #: Ids processed this call, regardless of outcome -- a skip (ranking ambiguity, decline,
    #: no answer) or a caught mining failure is not retried on a later turn either, matching
    #: the old one-shot-per-resume behaviour exactly.
    mined_ids: list[str] = []
    for record in pending:
        clarification_id = record.get("clarification_id")
        mined_ids.append(str(clarification_id))

        # Phase 2 gate: a ranking/superlative reading is a judgment call for the one question
        # that asked it, not a durable schema fact. A decline carries no answer to mine, and
        # neither does a defer (Phase 1b, this initiative) -- its `answer` is the agent's own
        # best-guess instruction text (`serve/tools.py::_CLARIFY_DEFERRED_TEXT`), not the
        # user's, and mining it would write that instruction into the corpus as a fact.
        if (
            record.get("basis") == "ranking_ambiguity"
            or record.get("declined")
            or record.get("deferred")
        ):
            continue
        answer_text = str(record.get("answer") or "")
        question = str(record.get("question") or "")
        if not answer_text or not question:
            continue

        # Phase 3's Enhancer dedup/conflict wiring lives in `fold_answered_clarification` now
        # (Phase 1c, this initiative) -- shared with the offline `/clarifications/{id}/answer`
        # route -- rather than inline here. Best-effort internally; never raises.
        fold_answered_clarification(
            agent_model,
            corpus_root,
            question,
            answer_text,
            schema=schema,
            known_assets=assets_by_id.values(),
            write_model=write_model,
            source="live_chat",
        )
    return {"clarifications_mined": mined_ids}
