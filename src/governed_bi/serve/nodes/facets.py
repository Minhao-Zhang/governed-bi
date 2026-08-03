"""Facet fan-out nodes (ADR 0005 §2–3).

Five nodes, one per :data:`~governed_bi.register.stages.FACET_STAGES` member.
Each writes only ``{"facets": {stage_value: FacetResult}}`` so the graph's
``merge_facets`` reducer can replace by key under concurrent fan-out.

When ``config["configurable"]["index"]`` is a :class:`~governed_bi.retrieve.index.UnifiedIndex`,
pass-one searches the lexical channel within :data:`~governed_bi.register.facets.FACET_TARGETS`
types (top ``candidate_depth``, default 50). Without an index, hits stay empty so
F1 tests that inject ``facet_route_hits`` keep working — but the ``channels`` map then says
``failed``, because a facet that could not consult an index did not run its lexical channel
and reporting ``ran`` there is what made the degradation gate inert (ADR 0005 §2.3).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from langchain_core.runnables import RunnableConfig

from governed_bi.register.facets import (
    FACET_CHANNELS,
    FACET_EXTRACTS,
    FACET_TARGETS,
    Channel,
    ChannelState,
    expected_channel_state,
)
from governed_bi.register.stages import Stage
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.serve.runtime import candidate_depth

__all__ = [
    "facet_schema_node",
    "facet_term_node",
    "facet_metric_node",
    "facet_entity_node",
    "facet_example_node",
]

_MAX_QUERIES = 8


def _effective_question(state: Mapping[str, Any]) -> str:
    """``rewrite.after`` when rewriting succeeded, else ``question``."""
    rewrite = state.get("rewrite")
    if (
        isinstance(rewrite, Mapping)
        and rewrite.get("outcome") == "rewritten"
        and rewrite.get("after") is not None
    ):
        return str(rewrite["after"])
    return str(state["question"])


def _channels_for(stage: Stage, ran: frozenset[Channel]) -> dict[str, str]:
    """What each channel **did** for this facet, against what the table declares.

    This returned :func:`expected_channel_state` verbatim, so a facet run with no index
    and no model reported ``{'lexical': 'ran', 'semantic': 'ran'}`` — the field's entire
    purpose inverted, and the degradation gate's only input reporting the configuration.

    The declaration decides one thing and the observation the other:

    * a channel this facet does not declare is ``not_configured``, **taken from the
      table** and never from the producer (ADR 0005 §2.3: a channel that silently stops
      being wired up must not be able to excuse itself);
    * a channel it does declare and did not consult is ``failed`` — "should have run and
      did not" is what the third value exists for.

    ``ran`` is collected where the call happens, so it cannot claim a channel the code
    did not reach.
    """
    out: dict[str, str] = {}
    for ch in Channel:
        expected = expected_channel_state(stage, ch)
        if expected is ChannelState.not_configured:
            out[ch.value] = expected.value
            continue
        out[ch.value] = (ChannelState.ran if ch in ran else ChannelState.failed).value
    return out


def _hook_name(stage: Stage) -> str:
    """``facet_schema`` → ``schema`` for ``retrieve_hooks`` lookup."""
    return stage.value.removeprefix("facet_")


def _hits_from_hook(state: Mapping[str, Any], stage: Stage, question: str) -> list[Any]:
    hooks = state.get("retrieve_hooks") or {}
    if not isinstance(hooks, Mapping):
        return []
    hook: Callable[..., Any] | None = hooks.get(_hook_name(stage))
    if hook is None:
        return []
    return list(hook(question))


def _index_from_config(config: RunnableConfig | None) -> UnifiedIndex | None:
    if not config:
        return None
    configurable = config.get("configurable") or {}
    if not isinstance(configurable, Mapping):
        return None
    index = configurable.get("index")
    return index if isinstance(index, UnifiedIndex) else None


def _pass_one_hits(
    index: UnifiedIndex,
    stage: Stage,
    question: str,
    *,
    depth: int,
    ran: set[Channel],
) -> list[dict[str, Any]]:
    """Lexical top-``depth`` within this facet's target types (global IDF).

    ``ran`` is an out-parameter: every channel this function actually consults adds
    itself, at the line where the consultation happens. Nothing adds
    :attr:`Channel.semantic` — no vector is scored here and there is no
    :class:`~governed_bi.ports.Embedder` adapter in ``src/`` to produce one — so that
    channel reports ``failed``, which is the truth and is what the degradation gate is
    for.
    """
    if not question or Channel.lexical not in FACET_CHANNELS[stage]:
        return []

    targets = FACET_TARGETS[stage]
    candidate_ids = {
        entry_id
        for entry_id, entry in index.entries.items()
        if entry.asset_type in targets
    }
    # The index has been consulted for this facet's types. An empty candidate set is a
    # measurement ("nothing of these types is indexed"), not a channel failure.
    ran.add(Channel.lexical)
    if not candidate_ids:
        return []

    scored = index.lexical.restrict_to(candidate_ids).search(question)
    scored.sort(key=lambda pair: (-float(pair[1]), str(pair[0])))

    queries = [question]
    hits: list[dict[str, Any]] = []
    for asset_id, lexical in scored:
        if float(lexical) <= 0.0:
            continue
        entry = index.entries[asset_id]
        asset_type = entry.asset_type
        hits.append(
            {
                "asset_id": asset_id,
                "asset_type": (
                    asset_type.value if hasattr(asset_type, "value") else str(asset_type)
                ),
                "lexical": float(lexical),
                "semantic": None,
                "score": float(lexical),
                "schema_tag": entry.schema_tag,
                "queries": list(queries),
            }
        )
        if len(hits) >= depth:
            break
    return hits


def _facet_result(
    stage: Stage,
    question: str,
    *,
    hits: list[Any] | None,
    ran: frozenset[Channel],
) -> dict[str, Any]:
    queries = [question] if question else []
    if len(queries) > _MAX_QUERIES:
        queries = queries[:_MAX_QUERIES]
    return {
        "facet": stage.value,
        "queries": queries,
        "hits": list(hits) if hits is not None else [],
        "channels": _channels_for(stage, ran),
    }


def _run_facet(
    state: Mapping[str, Any],
    config: RunnableConfig,
    stage: Stage,
) -> dict[str, Any]:
    question = _effective_question(state)
    index = _index_from_config(config)
    ran: set[Channel] = set()

    if index is not None:
        hits: list[Any] = _pass_one_hits(
            index,
            stage,
            question,
            depth=candidate_depth(state),
            ran=ran,
        )
    elif stage in FACET_EXTRACTS:
        # No index and no extraction model: the queries fall back to the raw question,
        # which is precisely the "the arm quietly IS v1's single-pass retrieval" case
        # ADR 0005 §2.3 describes. `ran` stays empty, so every channel this facet
        # declares reports `failed` rather than the fallback passing for a run.
        hits = []
    else:
        hits = _hits_from_hook(state, stage, question)

    return {
        "facets": {
            stage.value: _facet_result(stage, question, hits=hits, ran=frozenset(ran))
        }
    }


def facet_schema_node(state: dict, config: RunnableConfig) -> dict:
    """Schema facet: pass-one lexical over schema assets when an index is configured."""
    return _run_facet(state, config, Stage.facet_schema)


def facet_term_node(state: dict, config: RunnableConfig) -> dict:
    """Term facet: stub extraction query + type-scoped lexical when indexed."""
    return _run_facet(state, config, Stage.facet_term)


def facet_metric_node(state: dict, config: RunnableConfig) -> dict:
    """Metric facet: stub extraction query + type-scoped lexical when indexed."""
    return _run_facet(state, config, Stage.facet_metric)


def facet_entity_node(state: dict, config: RunnableConfig) -> dict:
    """Entity facet: stub extraction query + table/column/join lexical when indexed."""
    return _run_facet(state, config, Stage.facet_entity)


def facet_example_node(state: dict, config: RunnableConfig) -> dict:
    """Example facet: no lexical channel; empty hits until a semantic index is wired."""
    return _run_facet(state, config, Stage.facet_example)
