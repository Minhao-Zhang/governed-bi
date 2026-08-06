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
    FACET_TARGETS,
    Channel,
    ChannelState,
    expected_channel_state,
)
from governed_bi.register.stages import Stage
from governed_bi.retrieve.fuse import scale_within_channel
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.retrieve.semantic import semantic_search
from governed_bi.serve.runtime import candidate_depth, combine_channels
from governed_bi.serve.runtime import configurable as runtime_config

__all__ = [
    "facet_schema_node",
    "facet_term_node",
    "facet_metric_node",
    "facet_entity_node",
    "facet_example_node",
]

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


def _hooked(state: Mapping[str, Any], stage: Stage) -> bool:
    """Whether a test hook is supplied for this facet.

    Separate from :func:`_hits_from_hook` because the caller has to *branch* on it: "a hook
    returned nothing" and "there is no hook" are different states, and only the second should
    fall through to the empty-handed path.
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

    **The two channels are scaled, then blended by the declared weights** —
    :func:`~governed_bi.retrieve.fuse.scale_within_channel` then
    :func:`~governed_bi.serve.runtime.combine_channels`. This used to read *"combined by ``max``,
    not by a weighted sum. A weight is a knob, a knob is a comparability field, and there is no
    measurement yet that would set one."* The reasoning was right and the premise expired: the
    measurement exists now, and it says the units were deciding rather than the evidence. Over
    32 244 documents that both channels scored, the semantic channel won **0 times**, because
    BM25-after-saturation starts roughly where cosine ends. ``max`` was not keeping the stronger
    evidence; it was keeping BM25. Both functions carry the numbers.

    The property this needed to preserve is preserved: ``fuse`` renormalises by *active* weight,
    so an asset found by one channel is not penalised for being missed by the other, and a facet
    declaring one channel does not score structurally below one declaring two.
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

    # **One scale, then one combiner — the same one pass two uses.** `scale_within_channel`
    # carries the measurement that forced the scaling; `combine_channels` is shared with
    # `nodes/pass_two.py` because an asset that carries two different scores in one turn is a
    # real defect: the untagged pass-one hits are carried into pass two verbatim and then
    # compete against pass-two hits in `apply_budgets`' single global sort. This used to be
    # `max()` here and `fuse()` there, so a table found by both channels scored 0.9 down one
    # path and 0.7 down the other, and untagged assets were systematically advantaged.
    lexical_scaled = scale_within_channel(lexical_scores)
    semantic_scaled = scale_within_channel(semantic_scores)

    def _combined(aid: str) -> float:
        return combine_channels(lexical_scaled.get(aid), semantic_scaled.get(aid)) or 0.0

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
                # `None` where a channel did not score this asset, and a float where it did.
                # Absence and zero are different facts: one means "not found by this channel",
                # the other means "found and scored zero", and the record reads both.
                # **Raw, deliberately** — attribution must keep the channel's own number, so
                # only `score` is rescaled and the record can still say what BM25 said.
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
) -> dict[str, Any]:
    # One query per facet, always. The `[:_MAX_QUERIES]` truncation that used to sit here
    # bounded a list built two lines above as `[question]`, so it could never fire, and the
    # `max_queries_per_facet` knob it duplicated described a per-facet fan-out that does not
    # exist. Both are gone; a real multi-query facet brings its own bound.
    queries = [question] if question else []
    return {
        "facet": stage.value,
        "queries": queries,
        "hits": list(hits) if hits is not None else [],
        "channels": _channels_for(stage, ran),
    }


def _rewritten_query(
    question: str,
    stage: Stage,
    config: RunnableConfig,
    *,
    ran: set[Channel],
    spent: list[dict],
    turn_index: Any,
) -> str:
    """The question, restated as search text for this facet. Falls back to the question.

    **``spent`` is an out-parameter and carries this call's cost.** These five rewrites are five
    model calls per turn that the engine's own ledger did not know about: ``usage`` was written
    only by ``agent_core``, so five of the seven calls a turn makes were priced at nothing. An
    out-parameter rather than a second return value because ``ran`` already works this way in
    this function and one convention is better than two.

    **Why every facet searches with different words now.** A user asks *"what is the average star
    rating for restaurants in this area"* and a schema summary reads *"stores basic information
    about restaurants"* — neither BM25 nor an embedder finds much between those two, and until
    now every facet searched with the raw question. Each facet is looking for a different kind of
    object, so each gets its own restatement.

    ``Channel.extraction`` is added to ``ran`` **only when a rewrite actually came back**. A model
    that errors, or returns nothing, falls back to the raw question and the channel reports
    ``failed`` — because a fallback that reports as a run is precisely how, per ADR 0005 §2.3, an
    arm quietly becomes v1's single-pass retrieval while every channel claims to be working.

    Every failure returns the question rather than raising: retrieval on the original wording is
    the behaviour this replaced, so the worst case is what we had yesterday, not a dead turn.
    """
    from governed_bi.register.prompts import FACET_QUERY_PROMPTS, prompt_text

    prompt_name = FACET_QUERY_PROMPTS.get(stage.value)
    model = runtime_config(config).get("utility_model")
    if not question or prompt_name is None or model is None:
        return question

    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.serve.usage import usage_row

    try:
        reply = model.invoke(
            [SystemMessage(prompt_text(prompt_name)), HumanMessage(question)],
            # Named after the registered prompt, so the five concurrent rewrites are five
            # distinguishable rows in LangSmith instead of five `ChatOpenAI`s that started in
            # the same second. See the same note in `guard._bi_scope`.
            config={"run_name": prompt_name},
        )
        rewritten = str(getattr(reply, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — a rewriter is an improvement, never a dependency
        return question
    # Recorded before the empty check: a model that answered with nothing still billed for the
    # attempt, and dropping the row would make a *failing* rewriter look like a free one.
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
) -> Sequence[float] | None:
    """The vector to score against — **of the rewritten query when there is one**.

    The cached ``query_vector`` is the *raw question's*, computed once per turn by ``accept``. It
    is the right thing to score with when no rewrite happened, and the wrong thing the moment one
    did: a facet that restates the question and then searches with the original question's vector
    has paid for the rewrite and thrown away the half that motivated it.

    So a rewrite is embedded here, per facet. That is five extra embedding calls on a turn, which
    is the cheap half of the cost — they are small, they run concurrently with the other facets,
    and the rewrite that produced them cost a model call already.

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
    cfg = runtime_config(config)
    if query and question is not None and query != question:
        embedder = cfg.get("embedder")
        if embedder is not None:
            try:
                return list(embedder.embed([query])[0])
            except Exception:  # noqa: BLE001 — fall back to the question's vector below
                pass
    vector = state.get("query_vector")
    if vector:
        return vector
    return cfg.get("query_vector") or None


def _run_facet(
    state: Mapping[str, Any],
    config: RunnableConfig,
    stage: Stage,
) -> dict[str, Any]:
    question = _effective_question(state)
    index = _index_from_config(config)
    ran: set[Channel] = set()
    # Filled by the rewriter, appended to the `usage` channel below. A list because that is
    # the channel's shape and `operator.add` merges the five concurrent facets for free.
    spent: list[dict] = []
    # `queries` is what the record publishes as "what this facet searched for", so it has to be
    # the text that actually went to the index. It stays the raw question on every path where no
    # rewrite happened, which is what makes the two cases distinguishable in a trace.
    query = question

    if index is not None:
        # The rewrite happens first, and both channels then search with it — a rewrite that
        # reached only BM25 would miss the point, since the whole reason to restate the question
        # in the vocabulary of the thing being searched is to move it *semantically* closer.
        query = _rewritten_query(
            question, stage, config, ran=ran, spent=spent, turn_index=state.get("turn_index", 1)
        )
        hits: list[Any] = _pass_one_hits(
            index,
            stage,
            query,
            depth=candidate_depth(state),
            ran=ran,
            query_vector=_query_vector(state, config, query=query, question=question),
        )
    elif _hooked(state, stage):
        # **Checked before the extraction branch, and that ordering is a repair.** This used to
        # be the `else`, reachable only for the two facets outside `FACET_EXTRACTS` — and ADR
        # 0011 put all five inside it, which made the branch unreachable and `retrieve_hooks`
        # dead without a single test failing, because nothing outside this module uses it. A
        # declared hook that no input can reach is the shape this repository keeps finding; an
        # explicit "is one supplied for this facet" restores it for every facet instead.
        hits = _hits_from_hook(state, stage, question)
    else:
        # No index, no hook: the queries would fall back to the raw question, which is precisely
        # the "the arm quietly IS v1's single-pass retrieval" case ADR 0005 §2.3 describes. `ran`
        # stays empty, so every channel this facet declares reports `failed` rather than the
        # fallback passing for a run.
        hits = []

    update: dict[str, Any] = {
        "facets": {
            stage.value: _facet_result(stage, query, hits=hits, ran=frozenset(ran))
        }
    }
    # Omitted when empty rather than written as `[]`: the reducer is `operator.add`, so an empty
    # list is a no-op either way, and leaving the key out keeps "this facet called no model"
    # readable in the node's update — which is what `rail_observation` reads.
    if spent:
        update["usage"] = spent
    return update


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
