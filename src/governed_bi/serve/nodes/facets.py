"""Facet fan-out nodes (ADR 0005 §2–3).

Five nodes, one per :data:`~governed_bi.register.stages.FACET_STAGES` member.
Each writes only ``{"facets": {stage_value: FacetResult}}`` so the graph's
``merge_facets`` reducer can replace by key under concurrent fan-out.

With a :class:`~governed_bi.retrieve.index.UnifiedIndex` on ``config["configurable"]["index"]``,
pass one searches each facet's declared channels within
:data:`~governed_bi.register.facets.FACET_TARGETS` types (top ``candidate_depth``, default 50).
Without an index hits stay empty so F1 tests injecting ``facet_route_hits`` keep working — but
``channels`` then says ``failed``, because reporting ``ran`` for a channel that consulted no
index is what made the degradation gate inert (ADR 0005 §2.3).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping, Sequence

from langchain_core.runnables import RunnableConfig

from governed_bi.register.facets import (
    FACET_CHANNELS,
    FACET_TARGETS,
    SCORING_CHANNELS,
    Channel,
    ChannelState,
    expected_channel_state,
)
from governed_bi.register.stages import Stage
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.retrieve.semantic import semantic_search
from governed_bi.serve.runtime import (
    ChannelScale,
    candidate_depth,
    channel_scale,
    combine_channels,
    prompt_variants,
    vector_for_query,
)
from governed_bi.serve.runtime import configurable as runtime_config

__all__ = [
    "facet_schema_node",
    "facet_term_node",
    "facet_metric_node",
    "facet_entity_node",
    "facet_example_node",
]

def _effective_question(state: Mapping[str, Any]) -> str:
    """The question the facets search: ``state["question"]``, and nothing else since 2026-08-26.

    It used to prefer ``rewrite.after``. The ``rewrite`` node and its state channel are both
    gone (``serve/graph.py::_after_guard`` records why), so that branch could only ever read a
    key ``ServeState`` no longer declares — a reader whose writer is deleted, which returns the
    fallback forever while reading like a live feature. Kept as a named function because
    ``_run_facet`` asks a real question of the state and :func:`_rewritten_query` below rewrites
    the *facet's* query, one string later and for a different purpose; collapsing the two names
    is how those two rewrites come to be read as one.
    """
    return str(state["question"])


def _channels_for(
    stage: Stage,
    ran: frozenset[Channel],
    observed: Mapping[Channel, ChannelState],
) -> dict[str, str]:
    """What each channel **did** for this facet, against what the table declares.

    Never :func:`expected_channel_state` verbatim — that reports the configuration, which is
    the degradation gate's only input. The declaration decides one thing and the observation
    the other: a channel this facet does not declare is ``not_configured``, **taken from the
    table** and never from the producer (ADR 0005 §2.3). ``ran`` is collected where the call
    happens, so it cannot claim a channel the code did not reach.

    ``observed`` is how a *declared* channel says why it did not run. Without it every such
    channel read ``failed``, so "this arm wired no embedder" and "the embedder died" were one
    word, and ``Anomaly.unconfigured`` — declared in ``register/facets.py`` for exactly this —
    was unreachable from the only producer of channel state. Both remain degradation under
    ``is_degraded``; the distinction is diagnostic, and ``pass_two`` still consults only ``ran``.
    """
    out: dict[str, str] = {}
    for ch in Channel:
        expected = expected_channel_state(stage, ch)
        if expected is ChannelState.not_configured:
            out[ch.value] = expected.value
            continue
        if ch in ran:
            out[ch.value] = ChannelState.ran.value
            continue
        out[ch.value] = observed.get(ch, ChannelState.failed).value
    return out


def _hook_name(stage: Stage) -> str:
    """``facet_schema`` → ``schema`` for ``retrieve_hooks`` lookup."""
    return stage.value.removeprefix("facet_")


def _hooked(state: Mapping[str, Any], stage: Stage) -> bool:
    """Whether a test hook is supplied for this facet.

    Separate from :func:`_hits_from_hook` because the caller must *branch* on it: "a hook
    returned nothing" and "there is no hook" are different states.
    """
    hooks = state.get("retrieve_hooks") or {}
    return isinstance(hooks, Mapping) and hooks.get(_hook_name(stage)) is not None


def _hits_from_hook(state: Mapping[str, Any], stage: Stage, question: str) -> list[Any]:
    hooks = state.get("retrieve_hooks") or {}
    if not isinstance(hooks, Mapping):
        return []
    hook: Callable[..., Any] | None = hooks.get(_hook_name(stage))
    if hook is None:
        return []
    return list(hook(question))


def _index_from_config(config: RunnableConfig | None) -> UnifiedIndex | None:
    """The index, through the shared reader — never by subscripting ``config`` here.

    A second reader of ``config["configurable"]`` is a second answer to "may a request name
    this key"; ``runtime.configurable`` answers it once.
    """
    index = runtime_config(config).get("index")
    return index if isinstance(index, UnifiedIndex) else None


def _pass_one_hits(
    index: UnifiedIndex,
    stage: Stage,
    question: str,
    *,
    depth: int,
    ran: set[Channel],
    observed: dict[Channel, ChannelState],
    scale: ChannelScale,
    query_vector: Sequence[float] | None = None,
    query_vector_state: ChannelState = ChannelState.ran,
) -> list[dict[str, Any]]:
    """Top-``depth`` over this facet's target types, on every channel it declares.

    ``ran`` is an out-parameter: every channel this function consults adds itself at the line
    where the consultation happens, which is what lets ``_channels_for`` report the truth rather
    than the configuration.

    The semantic channel must be scored here and not only in pass two: ``facet_example``
    declares **only** the semantic channel, so without it the past-SQL-example facet retrieves
    nothing at all.

    **The two channels are blended by :func:`~governed_bi.serve.runtime.combine_channels`,
    which owns the scale**, and never by ``max``: BM25-after-saturation starts roughly where
    cosine ends, so ``max`` is a lexical-only rule by construction. The audit that caught it is
    retired (register/citations.py). ``fuse`` renormalises by *active* weight, so an asset missed
    by one channel is not penalised and a facet declaring one channel does not score structurally
    below one declaring two.

    Raw scores go in. Each pass used to min-max its channels over its own scored population
    first, which made every facet's favourite score exactly 1.0 however weak it was — see
    :func:`~governed_bi.retrieve.fuse.scale_to_ceiling` (audit I1).
    """
    declared = FACET_CHANNELS[stage]
    if not question:
        return []

    targets = FACET_TARGETS[stage]
    candidate_ids = {
        entry_id
        for entry_id, entry in index.entries.items()
        if entry.asset_type in targets
    }

    lexical_scores: dict[str, float] = {}
    if Channel.lexical in declared:
        # An empty candidate set is a measurement ("nothing of these types is indexed"), not a
        # channel failure, so the channel counts as consulted.
        ran.add(Channel.lexical)
        if candidate_ids:
            for asset_id, score in index.lexical.restrict_to(candidate_ids).search(question):
                if float(score) > 0.0:
                    lexical_scores[str(asset_id)] = float(score)

    semantic_scores: dict[str, float] = {}
    if Channel.semantic in declared:
        if query_vector_state is ChannelState.failed:
            # **The query could not be embedded, so this channel does not run** (audit I7).
            # `vector_for_query` used to hand back the raw question's vector here, which scored
            # cosine over one text against BM25 over another, blended them into one `score`,
            # and recorded `ran`. Skipped rather than scored, and the failure is what the record
            # carries — `semantic_search` cannot tell this apart, because from its side a
            # substituted vector is just a vector.
            observed[Channel.semantic] = ChannelState.failed
        else:
            # Marked `ran` from `semantic_search`'s own verdict (it returns `not_configured` when
            # the index holds no vectors or the query has none), not from this branch having been
            # entered.
            ranked, state = semantic_search(index, query_vector, candidates=candidate_ids or None)
            if state is ChannelState.ran:
                ran.add(Channel.semantic)
                for asset_id, score in ranked:
                    if float(score) > 0.0:
                        semantic_scores[str(asset_id)] = float(score)
            else:
                # Its own verdict, carried to the record rather than flattened to `failed`.
                observed[Channel.semantic] = state

    if not candidate_ids:
        return []

    # **One combiner, and it owns the scale.** `combine_channels` must be shared with
    # `nodes/pass_two.py`: untagged pass-one hits are carried into pass two verbatim and then
    # compete against pass-two hits in `apply_budgets`' single global sort, so two combiners means
    # one asset carrying two different scores in one turn. Each pass min-maxing its channels over
    # its own scored population was that, quietly: the two passes score different candidate sets,
    # so the same raw pair scaled to two different numbers (audit I1).

    # `fuse` needs this to tell "this facet has no semantic channel" from "the semantic channel
    # returned nothing for this asset"; conflating them scores an asset found by both channels
    # below one found by only one. Restricted to `SCORING_CHANNELS` because `ran` also carries
    # `extraction` — the rewriter call — which has no weight to fuse with.
    consulted = frozenset(c.value for c in ran if c in SCORING_CHANNELS)

    def _combined(aid: str) -> float:
        return (
            combine_channels(
                lexical_scores.get(aid),
                semantic_scores.get(aid),
                consulted=consulted,
                scale=scale,
            )
            or 0.0
        )

    merged = sorted(
        set(lexical_scores) | set(semantic_scores),
        # Ties broken by id so two runs over one index cannot disagree — the same rule
        # `semantic_search` follows for the same reason.
        key=lambda aid: (-_combined(aid), aid),
    )

    queries = [question]
    hits: list[dict[str, Any]] = []
    for asset_id in merged[:depth]:
        entry = index.entries[asset_id]
        asset_type = entry.asset_type
        lexical = lexical_scores.get(asset_id)
        semantic = semantic_scores.get(asset_id)
        hits.append(
            {
                "asset_id": asset_id,
                "asset_type": (
                    asset_type.value if hasattr(asset_type, "value") else str(asset_type)
                ),
                # `None` where a channel did not score this asset, a float where it did — the
                # record reads both, and they are the same raw numbers `_combined` was given.
                "lexical": lexical,
                "semantic": semantic,
                "score": _combined(asset_id),
                "schema_tag": entry.schema_tag,
                "queries": list(queries),
            }
        )
    return hits


def _facet_result(
    stage: Stage,
    question: str,
    *,
    hits: list[Any] | None,
    ran: frozenset[Channel],
    observed: Mapping[Channel, ChannelState],
) -> dict[str, Any]:
    # One query per facet, always. There is no `max_queries_per_facet` bound because there is
    # no per-facet fan-out; a real multi-query facet brings its own.
    queries = [question] if question else []
    return {
        "facet": stage.value,
        "queries": queries,
        "hits": list(hits) if hits is not None else [],
        "channels": _channels_for(stage, ran, observed),
    }


async def _rewritten_query(
    question: str,
    stage: Stage,
    config: RunnableConfig,
    *,
    ran: set[Channel],
    spent: list[dict],
    turn_index: Any,
) -> str:
    """The question, restated as search text for this facet. Falls back to the question.

    Each facet looks for a different kind of object, so each gets its own restatement: a
    question's wording and a schema summary's wording often share nothing for either channel to
    match on.

    ``spent`` is an out-parameter carrying this call's cost — four rewrites are four model calls
    a turn (``FACET_QUERY_PROMPTS`` has four entries; ``facet_schema`` is the fifth facet node
    and returns early above), and a call the ledger does not know about is a turn priced below
    what it spent.

    ``Channel.extraction`` is added to ``ran`` **only when a rewrite actually came back**. A
    model that errors or returns nothing falls back to the raw question and the channel reports
    ``failed``: a fallback that reports as a run is how an arm quietly becomes v1's single-pass
    retrieval while every channel claims to be working (ADR 0005 §2.3). Every failure returns
    the question rather than raising.
    """
    from governed_bi.register.prompts import FACET_QUERY_PROMPTS, prompt_text

    prompt_name = FACET_QUERY_PROMPTS.get(stage.value)
    model = runtime_config(config).get("utility_model")
    if not question or prompt_name is None or model is None:
        return question

    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.serve.usage import usage_row

    try:
        reply = await model.ainvoke(
            [SystemMessage(prompt_text(prompt_name, prompt_variants(config))), HumanMessage(question)],
            # Named after the registered prompt, so the four concurrent rewrites are four
            # distinguishable rows in LangSmith rather than four `ChatOpenAI`s.
            config={"run_name": prompt_name},
        )
        rewritten = str(getattr(reply, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — a rewriter is an improvement, never a dependency
        return question
    # Recorded before the empty check: a model that answered with nothing still billed, and
    # dropping the row would make a *failing* rewriter look like a free one.
    spent.append(usage_row(stage=stage.value, model=model, messages=reply, turn_index=turn_index))
    if not rewritten:
        return question
    ran.add(Channel.extraction)
    return rewritten


def _query_vector(
    state: Mapping[str, Any],
    config: RunnableConfig,
    *,
    query: str | None = None,
    question: str | None = None,
) -> tuple[Sequence[float] | None, ChannelState]:
    """The vector to score against — **of the rewritten query when there is one**.

    The cached ``query_vector`` is the *raw question's*, so a facet that restates the question
    and then scores the original's vector has paid for the rewrite and discarded half of it.
    :func:`~governed_bi.serve.runtime.vector_for_query` embeds the rewrite; this function is the
    state/config lookup producing its fallback.

    **State first, config second.** ``Session.configurable(question=...)`` puts a
    ``query_vector`` on the config, which works for a caller building one config per question
    (``eval/harness.py``, the CLI) but not on the streamed path, where
    ``graph_app.make_graph`` binds run constants once at load time with no question — the key is
    simply absent and the semantic channel reports ``failed`` however many vectors the index
    holds. ``accept`` writes it into state per turn, so state must win.

    Returns the channel's verdict beside the vector: a rewrite whose embed raised must reach
    :func:`_pass_one_hits` as ``failed``, not as some other text's vector (audit I7).
    """
    cfg = runtime_config(config)
    fallback = state.get("query_vector") or cfg.get("query_vector") or None
    return vector_for_query(
        query, question=question, fallback=fallback, embedder=cfg.get("embedder")
    )


async def _run_facet(
    state: Mapping[str, Any],
    config: RunnableConfig,
    stage: Stage,
) -> dict[str, Any]:
    question = _effective_question(state)
    index = _index_from_config(config)
    ran: set[Channel] = set()
    #: Declared channels that did not run, with the reason they gave. See `_channels_for`.
    observed: dict[Channel, ChannelState] = {}
    # Filled by the rewriter, appended to the `usage` channel below. A list because that is
    # the channel's shape and `operator.add` merges the five concurrent facets for free.
    spent: list[dict] = []
    # `queries` is what the record publishes as "what this facet searched for", so it must be
    # the text that actually went to the index.
    query = question

    if index is not None:
        # The rewrite happens first and **both** channels then search with it: the point of
        # restating the question in the vocabulary of the thing being searched is to move it
        # semantically closer, so a rewrite reaching only BM25 misses it.
        query = await _rewritten_query(
            question, stage, config, ran=ran, spent=spent, turn_index=state.get("turn_index", 1)
        )
        # **Off the event loop.** This node is `async def`, and `_query_vector` embeds the
        # rewritten query with the *synchronous* OpenAI client, so calling it directly ran a
        # TLS handshake on the loop. Under `langgraph dev` blockbuster raises `BlockingError`
        # on `socket.connect`, the SDK retries, its `time.sleep` raises too, and
        # `vector_for_query` swallows the lot as `semantic: failed` — four of five facets
        # degraded on every turn, silently, because a degraded channel is not a failed turn.
        #
        # The sync nodes never showed this: LangGraph runs a `def` node in a worker thread
        # where blockbuster is not armed. Only the `async def` facets touch the loop, and only
        # `facet_schema` escaped, because it is the one facet that does not rewrite and so
        # reuses the call-level vector instead of embedding.
        query_vector, query_vector_state = await asyncio.to_thread(
            _query_vector, state, config, query=query, question=question
        )
        hits: list[Any] = _pass_one_hits(
            index,
            stage,
            query,
            depth=candidate_depth(state),
            scale=channel_scale(state),
            ran=ran,
            observed=observed,
            query_vector=query_vector,
            query_vector_state=query_vector_state,
        )
    elif _hooked(state, stage):
        # An explicit "is a hook supplied for this facet", never a branch keyed on which facets
        # rewrite. This line's predecessor keyed on `FACET_EXTRACTS` membership, and when ADR
        # 0011 put every facet inside that set the branch went unreachable and `retrieve_hooks`
        # died with no test failing. `facet_schema` has since left the set again, so it holds
        # four — the same drift arriving from the other direction.
        hits = _hits_from_hook(state, stage, question)
    else:
        # No index, no hook. `ran` stays empty, so every channel this facet declares reports
        # `failed` rather than the raw-question fallback passing for a run (ADR 0005 §2.3).
        hits = []

    update: dict[str, Any] = {
        "facets": {
            stage.value: _facet_result(
                stage, query, hits=hits, ran=frozenset(ran), observed=observed
            )
        }
    }
    # Omitted when empty rather than written as `[]`: the reducer is `operator.add`, so both are
    # no-ops, but leaving the key out keeps "this facet called no model" readable to
    # `rail_observation`, which reads the node's update.
    if spent:
        update["usage"] = spent
    return update


async def facet_schema_node(state: dict, config: RunnableConfig) -> dict:
    """Schema facet: pass one over schema assets when an index is configured."""
    return await _run_facet(state, config, Stage.facet_schema)


async def facet_term_node(state: dict, config: RunnableConfig) -> dict:
    """Term facet: rewritten query + type-scoped pass one when indexed."""
    return await _run_facet(state, config, Stage.facet_term)


async def facet_metric_node(state: dict, config: RunnableConfig) -> dict:
    """Metric facet: rewritten query + type-scoped pass one when indexed."""
    return await _run_facet(state, config, Stage.facet_metric)


async def facet_entity_node(state: dict, config: RunnableConfig) -> dict:
    """Entity facet: rewritten query + table/column/join pass one when indexed."""
    return await _run_facet(state, config, Stage.facet_entity)


async def facet_example_node(state: dict, config: RunnableConfig) -> dict:
    """Example facet: semantic channel only — it declares no lexical channel."""
    return await _run_facet(state, config, Stage.facet_example)
