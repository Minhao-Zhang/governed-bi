"""``mine_mistakes`` -- a self-corrected turn's ledger becomes a mistake-memory draft
(UtkuAI, ported).

**Relocated from ``scripts/mine_mistakes_v2.py``, on the same reasoning as
``mine_corpus.py``'s own relocation (commit ``d20832a``) -- read its module docstring first.**
That script's only caller is a human running it by hand; nothing in the live serve path ever
calls :func:`~governed_bi.curator.mistake_memory.mine_mistake_from_execution`, confirmed via
``grep -rln "mistake_memory" src/`` returning only the module that declares it and this one
docstring. So a turn that hits a governance or execution failure, retries within the same
turn, and self-corrects produces **no durable memory of the fix** until an admin remembers to
run the offline script -- the next question that hits the identical mistake gets no benefit.

``graph.invoke()``/``graph.astream()`` on the compiled graph is the one thing every transport
actually calls, exactly as ``mine_corpus.py`` argues for clarifications. Putting the mining
trigger in a node here, rather than behind the offline script's own manual invocation, is what
makes it fire on every live turn regardless of who is asking or which transport carries it.

**Sits right after ``mine_corpus``, before ``narrate``.** Both are "durable fact extraction
after a turn" steps that read disjoint state -- this one reads ``state["execution"]``
(``agent_core``'s ledger of every ``run_query`` attempt this turn), ``mine_corpus`` reads
``state["clarifications"]`` -- and neither depends on the other's output, so the order between
them is a wiring choice, not a correctness one, exactly as ``serve/graph.py``'s own comment
says about ``mine_corpus`` and ``narrate``.

**Needs no ``*_mined`` dedup channel, unlike ``mine_corpus``.** ``clarifications`` accumulates
across the whole thread under the checkpointer (``operator.add``), so a node reading it sees
every clarification the thread has ever answered -- that is exactly what
``clarifications_mined`` (``serve/state.py``) exists to stop being re-processed on every later
turn. ``execution`` carries no such history: it is cleared every turn
(``serve/state.py::PER_TURN_RESET``), so this node only ever sees *this* turn's own,
already-complete ledger, once. There is nothing thread-wide to re-mine.

**The extraction algorithm itself is untouched.**
:func:`~governed_bi.curator.mistake_memory.mine_mistake_from_execution` decides whether a turn
has anything to learn (a ``run_query`` attempt failed governance/execution and a *later*
attempt in the same turn passed) exactly as it does for the offline script -- this module
relocates only the trigger. Writing a mined draft goes through
:func:`~governed_bi.curator.enhancer.apply` against this schema's already-certified
``few_shot`` assets, using the turn's own ``agent_model`` (the live analogue of the offline
script's optional ``--enhancer-model``; a live turn already has a model configured for
exactly this class of small extra call), falling back to a plain
:func:`~governed_bi.corpus.drafts.submit_draft` on any :class:`~governed_bi.curator.enhancer.
EnhancerError` -- byte-identical fallback shape to ``mine_corpus_node``'s own
``fold_answered_clarification`` call, so a broken dedup/conflict call degrades rather than
losing a real correction.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from governed_bi.serve.runtime import bool_knob, configurable

__all__ = ["mine_mistakes_node"]


def mine_mistakes_node(state: dict, config: RunnableConfig) -> dict:
    """Mine this turn's ``execution`` ledger into a mistake-memory draft, if it self-corrected.

    Gated exactly like ``mine_corpus_node``: ``enable_mistake_memory_mining`` (off by default,
    read off ``state`` the same way every other knob is) and ``corpus_root is not None`` (via
    :func:`~governed_bi.serve.runtime.configurable`).

    Best-effort, matching every other write in this class: any failure building or writing the
    draft is swallowed rather than turning a successfully self-corrected answer into a failed
    turn. Returns ``{}`` unconditionally -- there is no dedup channel to report, see the module
    docstring for why one is not needed here.
    """
    cfg = configurable(config)
    corpus_root = cfg.get("corpus_root")
    if corpus_root is None or not bool_knob(state, "enable_mistake_memory_mining"):
        return {}

    question = str(state.get("question") or "")
    schema = state.get("db_id")
    if not question or not schema:
        return {}

    from governed_bi.curator.mistake_memory import mine_mistake_from_execution

    draft = mine_mistake_from_execution(question, str(schema), state.get("execution") or {})
    if draft is None:
        return {}

    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.curator import enhancer

    agent_model = cfg.get("agent_model")
    assets_by_id: dict[str, Any] = cfg.get("assets_by_id") or {}
    write_model = (state.get("knobs_resolved") or {}).get("llm_model")
    # Same inline filter `scripts/mine_mistakes_v2.py` itself uses ahead of its own
    # `enhancer.apply` call: certified `few_shot` assets are the only ones a dedup/conflict
    # decision may compare a mined mistake against.
    existing = [
        asset
        for asset in assets_by_id.values()
        if asset.asset_type.value == "few_shot"
        and getattr(asset, "audit", None) is not None
        and getattr(asset.audit, "provenance", None) is not None
        and asset.audit.provenance.status.value == "certified"
    ]
    try:
        try:
            enhancer.apply(
                agent_model,
                corpus_root,
                draft,
                existing=existing,
                namespace=str(schema),
                write_model=write_model,
            )
        except enhancer.EnhancerError:
            submit_draft(corpus_root, draft, namespace=str(schema), model=write_model)
    except Exception:  # noqa: BLE001 -- mining is best-effort, never fatal to the caller
        pass
    return {}
