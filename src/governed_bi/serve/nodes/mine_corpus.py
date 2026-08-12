"""``mine_corpus`` -- an answered ``ask_user`` clarification becomes a corpus draft.

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

The mining logic itself (the ``basis`` gate, the Enhancer dedup/conflict wiring) is
unchanged -- this module only relocates *where it runs*, from a route to a graph node that
runs after ``agent_core`` on every turn.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from governed_bi.corpus.schema import ProvenanceStatus
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

    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.curator import enhancer
    from governed_bi.curator.clarification import draft_from_clarification

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

        try:
            draft = draft_from_clarification(question, answer_text, schema=schema)
            try:
                # Phase 3: compared against every already-**certified** TermAsset this
                # session has loaded, so a reworded restatement does not mint a second,
                # unlinked draft and a contradicting answer is flagged rather than silently
                # producing a second, disagreeing certified fact once approved.
                existing = [
                    asset
                    for asset in assets_by_id.values()
                    if asset.asset_type.value == "term" and _is_certified(asset)
                ]
                enhancer.apply(
                    agent_model,
                    corpus_root,
                    draft,
                    existing=existing,
                    namespace=schema,
                    write_model=write_model,
                )
            except enhancer.EnhancerError:
                # A broken dedup/conflict call must not drop a real user answer -- degrade to
                # the pre-Phase-3 unconditional write.
                submit_draft(corpus_root, draft, namespace=schema)
        except Exception:  # noqa: BLE001 -- mining is best-effort, never fatal to the turn
            pass
    return {"clarifications_mined": mined_ids}


def _is_certified(asset: Any) -> bool:
    """Same read as ``corpus/analyst.py``'s: absence of provenance is not "certified"."""
    provenance = getattr(asset.audit, "provenance", None) if asset.audit is not None else None
    return provenance is not None and provenance.status is ProvenanceStatus.certified
