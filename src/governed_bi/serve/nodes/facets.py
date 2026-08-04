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

from typing import Any, Callable, Mapping, Sequence

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
from governed_bi.retrieve.semantic import semantic_search
from governed_bi.serve.runtime import candidate_depth
from governed_bi.serve.runtime import configurable as runtime_config

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
    """The index, through the shared reader — never by subscripting ``config`` here.

    A second reader of ``config["configurable"]`` is a second answer to "may a request name
    this key", and ``runtime.configurable`` is where that question is answered once.
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
    query_vector: Sequence[float] | None = None,
) -> list[dict[str, Any]]:
    """Top-``depth`` over this facet's target types, on every channel it declares.

    ``ran`` is an out-parameter: every channel this function actually consults adds itself, at
    the line where the consultation happens. That is the whole reason ``_channels_for`` can
    report the truth rather than the configuration.

    **The semantic channel used to be unreachable here, and the comment that said so has been
    wrong twice.** It read *"no vector is scored here and there is no ``Embedder`` adapter in
    ``src/`` to produce one"* — the adapter exists (``model/openai_embedder.py``), and the deeper
    problem was that pass one had no vector-scoring code at all, only pass two did. So four
    facets ran at half strength and, more seriously, ``facet_example`` declares **only** the
    semantic channel, which means the past-SQL-example facet retrieved *nothing, ever*. That is
    the facet the maintainer singled out: *"providing past SQL example to answer the more current
    question is very helpful, and in this case the embedding model would be able to retrieve
    those much better than BM25."* It could not retrieve them at all.

    **The two channels are combined by ``max``, not by a weighted sum.** A weight is a knob, a
    knob is a comparability field, and there is no measurement yet that would set one — so a
    tuned blend here would be a number invented at the call site, which is what
    ``register/knobs.py`` exists to prevent. ``max`` also has the property the fan-in needs: a
    facet whose channels disagree keeps the *stronger* evidence rather than diluting it, and an
    asset found by one channel is not penalised for being missed by the other. When a weight is
    warranted it becomes a declared knob and this line is where it lands.
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
        # The index has been consulted for this facet's types. An empty candidate set is a
        # measurement ("nothing of these types is indexed"), not a channel failure.
        ran.add(Channel.lexical)
        if candidate_ids:
            for asset_id, score in index.lexical.restrict_to(candidate_ids).search(question):
                if float(score) > 0.0:
                    lexical_scores[str(asset_id)] = float(score)

    semantic_scores: dict[str, float] = {}
    if Channel.semantic in declared:
        # `semantic_search` reports its own observed state — it returns `failed` when the index
        # holds no vectors or the query has none — so the channel is marked `ran` from *its*
        # verdict rather than from the fact that this branch was entered. A branch that ran and
        # found nothing to score has not consulted the channel.
        ranked, state = semantic_search(index, query_vector, candidates=candidate_ids or None)
        if state is ChannelState.ran:
            ran.add(Channel.semantic)
            for asset_id, score in ranked:
                if float(score) > 0.0:
                    semantic_scores[str(asset_id)] = float(score)

    if not candidate_ids:
        return []

    merged = sorted(
        set(lexical_scores) | set(semantic_scores),
        # Ties broken by id so two runs over one index cannot disagree — the same rule
        # `semantic_search` follows for the same reason.
        key=lambda aid: (-max(lexical_scores.get(aid, 0.0), semantic_scores.get(aid, 0.0)), aid),
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
                # `None` where a channel did not score this asset, and a float where it did.
                # Absence and zero are different facts: one means "not found by this channel",
                # the other means "found and scored zero", and the record reads both.
                "lexical": lexical,
                "semantic": semantic,
                "score": max(lexical or 0.0, semantic or 0.0),
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


def _query_vector(state: Mapping[str, Any], config: RunnableConfig) -> Sequence[float] | None:
    """This turn's question vector — from **state** first, then config.

    **State first, and that ordering is the fix.** ``Session.configurable(question=...)`` puts a
    ``query_vector`` on the config, and that works for a caller who builds one config per
    question — ``eval/harness.py`` and ``POST /chat``. It cannot work on the streamed path, which
    is now the only real one: ``graph_app.make_graph`` binds the run constants **once at load
    time**, with no question, because a query vector is per-turn and the config there is a run
    constant. So on the server path the key was simply never present, and the semantic channel
    would have reported ``failed`` however many vectors the index held.

    ``accept`` is the per-turn server-side node, so it computes the vector into state and this
    reads it. Config remains the fallback so the two existing callers keep working unchanged —
    and reading state first means a per-turn value always wins over a run-constant one, which is
    the direction that cannot be wrong.
    """
    vector = state.get("query_vector")
    if vector:
        return vector
    from_config = runtime_config(config).get("query_vector")
    return from_config or None


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
            query_vector=_query_vector(state, config),
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
