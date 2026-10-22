"""One node per channel stage (ADR 0005 §2–3).

With neither an index nor a hook, hits stay empty and declared channels report
``failed``: ``ran`` with no index made the degradation check inert (ADR 0005 §2.3)."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping, Sequence

from langchain_core.runnables import RunnableConfig

from governed_bi.registry.channels import (
    CHANNEL_TARGETS,
    DECLARED_CHANNELS,
    SCORING_CHANNELS,
    Channel,
    ChannelState,
    expected_channel_state,
)
from governed_bi.registry.stages import Stage
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
    "channel_schema_node",
    "channel_term_node",
    "channel_metric_node",
    "channel_entity_node",
    "channel_example_node",
]

def _effective_question(state: Mapping[str, Any]) -> str:
    rewrite = state.get("rewrite")
    if (
        isinstance(rewrite, Mapping)
        and rewrite.get("outcome") == "rewritten"
        and rewrite.get("after") is not None
    ):
        return str(rewrite["after"])
    return str(state["question"])


def _channels_for(
    stage: Stage,
    ran: frozenset[Channel],
    observed: Mapping[Channel, ChannelState],
) -> dict[str, str]:
    """Observed state. Undeclared is ``not_configured`` from the table (ADR 0005 §2.3)."""
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
    return stage.value.removeprefix("channel_")


def _hooked(state: Mapping[str, Any], stage: Stage) -> bool:
    """Whether a hook is supplied. An empty result is a different state."""
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
    """The index via ``runtime.configurable``. A second reader is a second answer."""
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
    """``ran`` is recorded where the channel is consulted. A per-set scale made every favourite 1.0."""
    declared = DECLARED_CHANNELS[stage]
    if not question:
        return []

    targets = CHANNEL_TARGETS[stage]
    candidate_ids = {
        entry_id
        for entry_id, entry in index.entries.items()
        if entry.asset_type in targets
    }

    lexical_scores: dict[str, float] = {}
    if Channel.lexical in declared:
        # An empty candidate set was consulted: nothing of these types is indexed.
        ran.add(Channel.lexical)
        if candidate_ids:
            for asset_id, score in index.lexical.restrict_to(candidate_ids).search(question):
                if float(score) > 0.0:
                    lexical_scores[str(asset_id)] = float(score)

    semantic_scores: dict[str, float] = {}
    if Channel.semantic in declared:
        if query_vector_state is ChannelState.failed:
            # Embedding failed. Do not score another text's vector or record ``ran``.
            observed[Channel.semantic] = ChannelState.failed
        else:
            # ``ran`` only when ``semantic_search`` says so.
            ranked, state = semantic_search(index, query_vector, candidates=candidate_ids or None)
            if state is ChannelState.ran:
                ran.add(Channel.semantic)
                for asset_id, score in ranked:
                    if float(score) > 0.0:
                        semantic_scores[str(asset_id)] = float(score)
            else:
                # Keep its state. Do not flatten every non-run to ``failed``.
                observed[Channel.semantic] = state

    if not candidate_ids:
        return []

    # Shared with pass two, so one asset is not scored on two scales.
    # ``extraction`` is in ``ran`` and has no fusion weight; a missing channel is not an empty score.
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
        # Tie-break on id so two runs over one index agree.
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
                # ``None`` where that channel did not score the asset.
                "lexical": lexical,
                "semantic": semantic,
                "score": _combined(asset_id),
                "schema_tag": entry.schema_tag,
                "queries": list(queries),
            }
        )
    return hits


def _channel_result(
    stage: Stage,
    question: str,
    *,
    hits: list[Any] | None,
    ran: frozenset[Channel],
    observed: Mapping[Channel, ChannelState],
) -> dict[str, Any]:
    # One query per channel. There is no per-channel query fan-out.
    queries = [question] if question else []
    return {
        "channel": stage.value,
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
    """The question, or a rewrite. ``extraction`` is recorded only when a rewrite came back (ADR 0005 §2.3)."""
    from governed_bi.registry.prompts import CHANNEL_QUERY_PROMPTS, prompt_text

    prompt_name = CHANNEL_QUERY_PROMPTS.get(stage.value)
    model = runtime_config(config).get("utility_model")
    if not question or prompt_name is None or model is None:
        return question

    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.serve.usage import usage_row

    try:
        reply = await model.ainvoke(
            [SystemMessage(prompt_text(prompt_name, prompt_variants(config))), HumanMessage(question)],
            # Named for the prompt, so concurrent rewrites are distinct traces.
            config={"run_name": prompt_name},
        )
        rewritten = str(getattr(reply, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — a rewriter is an improvement, never a dependency
        return question
    # Billed even when the text is empty. An empty answer is not a free call.
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
    """State before config: the streamed graph binds config once, with no question."""
    cfg = runtime_config(config)
    fallback = state.get("query_vector") or cfg.get("query_vector") or None
    return vector_for_query(
        query, question=question, fallback=fallback, embedder=cfg.get("embedder")
    )


async def _run_channel(
    state: Mapping[str, Any],
    config: RunnableConfig,
    stage: Stage,
) -> dict[str, Any]:
    question = _effective_question(state)
    index = _index_from_config(config)
    ran: set[Channel] = set()
    #: Why a declared channel did not run. See :func:`_channels_for`.
    observed: dict[Channel, ChannelState] = {}
    spent: list[dict] = []
    # The text sent to the index, which is what the record publishes as searched.
    query = question

    if index is not None:
        # Rewrite first. Both channels search with it.
        query = await _rewritten_query(
            question, stage, config, ran=ran, spent=spent, turn_index=state.get("turn_index", 1)
        )
        # Off the loop: a blocking embed inside ``async def`` is reported as a failed semantic channel.
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
        # Branch on the hook being present, not on which stages rewrite.
        hits = _hits_from_hook(state, stage, question)
    else:
        # No index and no hook: ``ran`` stays empty, so declared channels report ``failed``.
        hits = []

    update: dict[str, Any] = {
        "channels": {
            stage.value: _channel_result(
                stage, query, hits=hits, ran=frozenset(ran), observed=observed
            )
        }
    }
    # Omit an empty ``usage``. A missing key says this channel called no model.
    if spent:
        update["usage"] = spent
    return update


async def channel_schema_node(state: dict, config: RunnableConfig) -> dict:
    return await _run_channel(state, config, Stage.channel_schema)


async def channel_term_node(state: dict, config: RunnableConfig) -> dict:
    return await _run_channel(state, config, Stage.channel_term)


async def channel_metric_node(state: dict, config: RunnableConfig) -> dict:
    return await _run_channel(state, config, Stage.channel_metric)


async def channel_entity_node(state: dict, config: RunnableConfig) -> dict:
    return await _run_channel(state, config, Stage.channel_entity)


async def channel_example_node(state: dict, config: RunnableConfig) -> dict:
    """Declares the semantic channel only, so pass one must score it here."""
    return await _run_channel(state, config, Stage.channel_example)
