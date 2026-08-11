"""Pass-two re-retrieval inside selected schemas (ADR 0005 §2.5).

Re-runs facet queries against a ``UnifiedIndex`` restricted to the selected
schemas (global IDF via ``BM25.restrict_to``). Not a filter of pass-one.
Untagged pass-one hits are carried forward unconditionally before budgets.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Mapping, Sequence

from governed_bi.register.assets import AssetType
from governed_bi.register.facets import (
    FACET_CHANNELS,
    FACET_TARGETS,
    SCORING_CHANNELS,
    Channel,
    ChannelState,
)
from governed_bi.register.stages import Stage
from governed_bi.retrieve.budget import apply_budgets
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.serve.runtime import (
    ChannelScale,
    candidate_depth,
    channel_scale,
    combine_channels,
    vector_for_query,
)
from governed_bi.serve.runtime import facet_hits as hits_of
from governed_bi.serve.runtime import lexical_coverage as _lexical_coverage

__all__ = ["pass_two_retrieve"]


def pass_two_retrieve(
    *,
    state: Mapping[str, Any],
    index: UnifiedIndex,
    schemas: Sequence[Any],
    ranking: list[tuple[Any, float]],
    query_vector: Sequence[float] | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Re-search selected schemas per facet query; merge untagged pass-one hits.

    ``query_vector`` is the raw question's, from ``accept``. ``embedder`` is what lets a facet's
    *rewritten* query be scored against its own vector; without it this pass blends BM25 over one
    text with cosine over another. Optional, because fixtures and no-embedder configurations
    legitimately have none, and then ``query_vector`` is the only vector available.
    """
    schema_set = {str(s) for s in schemas}
    depth = candidate_depth(state)
    scale = channel_scale(state)
    question = str(state.get("question") or "")
    #: query text -> its vector, for this call. Two facets often rewrite to the same phrase.
    vectors: dict[str, Sequence[float] | None] = {}

    # Per-facet hit lists (dict payloads) after within-facet dedup.
    hits_by_facet: dict[str, list[dict[str, Any]]] = {}

    for facet_name, facet_result in (state.get("facets") or {}).items():
        name = str(facet_name)
        queries = _facet_queries(facet_result)
        targets = _facet_targets(name)
        candidate_ids = _candidate_ids(index, schema_set, targets)

        merged: dict[str, dict[str, Any]] = {}
        if candidate_ids and queries:
            # **Each channel retrieves; their union is scored** (ADR 0005 §2.4). Gating this
            # block on `_scores_lexical(name)` as a whole, and taking candidates from the
            # lexical ranking with the cosine read back by id, breaks two things silently:
            # `facet_example` declares only the semantic channel and is skipped entirely (its
            # pass-one hits are then dropped below, since the carry-forward keeps only
            # *untagged* hits and every few-shot carries `TagRule.own_schema`); and for the
            # other facets an asset with a strong cosine and no shared term cannot enter the
            # context at any depth. The `_scores_lexical` guard stays one level lower, because
            # `Anomaly.extra_channel` is real — a facet must not be scored on a channel
            # `register/facets.py` does not declare for it.
            restricted = (
                index.lexical.restrict_to(candidate_ids) if _scores_lexical(name) else None
            )
            # The channels this facet declares and this pass therefore consulted. `fuse`
            # renormalises over these, so `facet_example` (semantic only) is not diluted, and a
            # document one consulted channel missed scores 0.0 on it rather than being credited
            # as if the channel never ran.
            consulted = frozenset(
                {Channel.lexical.value} if _scores_lexical(name) else set()
            ) | frozenset({Channel.semantic.value} if _scores_semantic(name) else set())
            for query in queries:
                if not query:
                    continue
                lexical_scores: dict[str, float] = {}
                if restricted is not None:
                    lexical_scores = {
                        str(aid): float(sc)
                        for aid, sc in sorted(
                            restricted.search(query), key=lambda p: (-float(p[1]), str(p[0]))
                        )
                        if float(sc) > 0.0
                    }
                # **Inside the query loop, and scored against the vector of *this* query.**
                # Hoisted out, it scores the call-level `query_vector` — the raw question's —
                # while the lexical channel searches `queries`, the utility-model rewrite, and
                # the two are then blended. Memoised per call, so two facets producing the same
                # rewrite embed it once and the raw question needs no call at all.
                semantic_scores = (
                    _semantic_scores(
                        index,
                        candidate_ids,
                        _vector_for(query, question, query_vector, embedder, vectors),
                    )
                    if _scores_semantic(name)
                    else {}
                )
                # Positive only, matching `nodes/facets.py`. The two passes disagreeing about
                # what counts as a semantic score is how one asset ends up with two different
                # `score` values in one turn.
                semantic_scores = {
                    str(aid): float(sc) for aid, sc in semantic_scores.items() if float(sc) > 0.0
                }
                semantic_top = [
                    aid
                    for aid, _ in sorted(
                        semantic_scores.items(), key=lambda p: (-float(p[1]), str(p[0]))
                    )
                ][:depth]
                # `dict.fromkeys` unions the two candidate lists while keeping the lexical
                # order first and de-duplicating; the real ordering happens in `apply_budgets`.
                lexical_top = list(lexical_scores)[:depth]
                # The scale belongs to `combine_channels` and is a fixed ceiling, not a min-max
                # over this facet's scored population: pass one and pass two score different
                # candidate sets, so a population-dependent scale gave one asset two different
                # numbers in one turn and `apply_budgets` sorted them together (audit I1).
                for asset_id in dict.fromkeys([*lexical_top, *semantic_top]):
                    entry = index.entries.get(asset_id)
                    if entry is None:
                        continue
                    lexical = lexical_scores.get(asset_id)
                    semantic = semantic_scores.get(asset_id)
                    score = _hybrid(lexical, semantic, consulted=consulted, scale=scale)
                    payload = {
                        "facet": name,
                        "asset_id": asset_id,
                        "asset_type": (
                            entry.asset_type.value
                            if isinstance(entry.asset_type, AssetType)
                            else str(entry.asset_type)
                        ),
                        "lexical": lexical,
                        "semantic": semantic,
                        "queries": [query],
                        "score": score,
                        "schema_tag": entry.schema_tag,
                    }
                    _merge_within_facet(merged, payload)

        # Untagged pass-one hits for this facet — unconditional carry-forward.
        pass_one_consulted = _pass_one_consulted(facet_result)
        for hit in hits_of(facet_result):
            if _raw_schema_tag(hit) is not None:
                continue
            payload = _pass_one_payload(hit, name, pass_one_consulted, scale)
            if payload is None:
                continue
            _merge_within_facet(merged, payload)

        if merged:
            hits_by_facet[name] = list(merged.values())

    return _build_retrieved(hits_by_facet, ranking, state, index)


def _scores_lexical(facet_name: str) -> bool:
    """Whether this facet declares a lexical channel — read from ``FACET_CHANNELS``.

    Without the guard, pass two scores ``facet_example`` on ``lexical`` and a few-shot outranks
    an entity hit on a channel the same turn's record declares ``not_configured``
    (``Anomaly.extra_channel``). ADR 0005 §2: ``register/facets.py`` decides which channels a
    facet uses and ``retrieve/`` must never decide it locally. An unrecognised facet name has no
    declaration to consult, so it is scored on nothing.
    """
    try:
        stage = Stage(facet_name)
    except ValueError:
        return False
    return Channel.lexical in FACET_CHANNELS.get(stage, frozenset())


def _scores_semantic(facet_name: str) -> bool:
    """Whether this facet declares a semantic channel — the other half of :func:`_scores_lexical`.

    Two questions, not one: ``facet_example`` declares ``semantic`` alone, so answering "may I
    run the vector channel here" with the lexical declaration silences it completely.
    """
    try:
        stage = Stage(facet_name)
    except ValueError:
        return False
    return Channel.semantic in FACET_CHANNELS.get(stage, frozenset())


def _facet_targets(facet_name: str) -> frozenset[AssetType] | None:
    try:
        stage = Stage(facet_name)
    except ValueError:
        return None
    return FACET_TARGETS.get(stage)


def _candidate_ids(
    index: UnifiedIndex,
    schema_set: set[str],
    targets: frozenset[AssetType] | None,
) -> set[str]:
    out: set[str] = set()
    for eid, entry in index.entries.items():
        tag = entry.schema_tag
        if tag is None or str(tag) not in schema_set:
            continue
        if targets is not None and entry.asset_type not in targets:
            continue
        out.add(eid)
    return out


def _facet_queries(facet_result: Any) -> list[str]:
    if facet_result is None:
        return []
    if isinstance(facet_result, Mapping):
        raw = facet_result.get("queries") or ()
    else:
        raw = getattr(facet_result, "queries", None) or ()
    return [str(q) for q in raw if q is not None and str(q)]


def _raw_schema_tag(hit: Any) -> str | None:
    if isinstance(hit, Mapping):
        tag = hit.get("schema_tag")
    else:
        tag = getattr(hit, "schema_tag", None)
    if tag is None or tag == "":
        return None
    return str(tag)


def _semantic_scores(
    index: UnifiedIndex,
    candidate_ids: set[str],
    query_vector: Sequence[float] | None,
) -> dict[str, float]:
    """Every candidate's cosine against the query vector, in **one** store query.

    A point lookup per surviving lexical hit is a store round trip each; the whole candidate set
    is one query, read back by id.

    An asset absent from the result keeps its ``None``: "this channel did not score it" and "it
    scored zero" are different facts and the record publishes both.
    """
    store = index.vectors
    if store is None or query_vector is None or not candidate_ids:
        return {}
    return dict(store.search(query_vector, keys=candidate_ids))


def _vector_for(
    query: str,
    question: str,
    fallback: Sequence[float] | None,
    embedder: Any | None,
    memo: dict[str, Sequence[float] | None],
) -> Sequence[float] | None:
    """:func:`~governed_bi.serve.runtime.vector_for_query`, memoised over this call.

    The five facets frequently rewrite a question into overlapping phrases, and a facet whose
    query *is* the question resolves to ``fallback`` with no embedding call at all.

    **The state is dropped here and the vector is not substituted** (audit I7). Pass two derives
    ``consulted`` from ``register/facets.py``'s *declared* channels rather than from what ran, so
    it has nowhere to record an observed ``failed`` — that seam is pass one's. What it must not
    do is what it used to: score a rewrite's BM25 against the raw question's cosine. With
    ``None`` the semantic channel contributes 0.0 to every asset, which is a channel that found
    nothing rather than a channel that searched something else.
    """
    if query not in memo:
        vector, _state = vector_for_query(
            query, question=question, fallback=fallback, embedder=embedder
        )
        memo[query] = vector
    return memo[query]


def _hybrid(
    lexical: float | None,
    semantic: float | None,
    *,
    consulted: Collection[str],
    scale: ChannelScale,
) -> float | None:
    """Delegates to :func:`~governed_bi.serve.runtime.combine_channels`.

    This score is what reaches ``apply_budgets``, so it decides which tables survive the cap of 8
    — the largest attributable loss in the pipeline — and fusing raw BM25 saturation with raw cosine
    is the lexical score plus a small constant. ``scale`` carries the three fusion knobs **this turn
    resolved**, rather than the values ``serve.runtime`` read when it was imported (audit I10).

    ``consulted`` comes from the facet's declared channels (``_scores_lexical`` /
    ``_scores_semantic``), so a facet declaring one channel is not diluted and a document one
    channel missed is not credited as if that channel never ran.
    """
    return combine_channels(lexical, semantic, consulted=consulted, scale=scale)


def _merge_within_facet(
    merged: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    asset_id = str(payload["asset_id"])
    score = float(payload["score"])
    prev = merged.get(asset_id)
    if prev is None:
        merged[asset_id] = {
            **payload,
            "queries": list(payload.get("queries") or ()),
        }
        return
    queries = list(dict.fromkeys([*(prev.get("queries") or ()), *(payload.get("queries") or ())]))
    if score > float(prev.get("score") or 0.0):
        # Components from the max-scoring query (ADR §2.4).
        merged[asset_id] = {**payload, "queries": queries}
    else:
        prev["queries"] = queries


def _pass_one_consulted(facet_result: Any) -> frozenset[str]:
    """The scoring channels pass one **recorded as having run** for this facet.

    Read from the facet result's ``channels``, not from ``FACET_CHANNELS``: the declaration says
    which channels the facet uses, and a declared channel that failed reports ``failed`` there.
    Counting it as consulted would divide by a channel that never scored anything.
    """
    if isinstance(facet_result, Mapping):
        channels = facet_result.get("channels")
    else:
        channels = getattr(facet_result, "channels", None)
    if not isinstance(channels, Mapping):
        return frozenset()
    scoring = {c.value for c in SCORING_CHANNELS}
    return frozenset(
        str(name)
        for name, state in channels.items()
        if str(name) in scoring and str(state) == ChannelState.ran.value
    )


def _pass_one_payload(
    hit: Any, facet_name: str, consulted: Collection[str], scale: ChannelScale
) -> dict[str, Any] | None:
    if isinstance(hit, Mapping):
        asset_id = hit.get("asset_id")
        asset_type = hit.get("asset_type")
        lexical = hit.get("lexical")
        semantic = hit.get("semantic")
        score = hit.get("score")
        queries = list(hit.get("queries") or ())
        schema_tag = hit.get("schema_tag")
    else:
        asset_id = getattr(hit, "asset_id", None)
        asset_type = getattr(hit, "asset_type", None)
        lexical = getattr(hit, "lexical", None)
        semantic = getattr(hit, "semantic", None)
        score = getattr(hit, "score", None)
        queries = list(getattr(hit, "queries", None) or ())
        schema_tag = getattr(hit, "schema_tag", None)

    if asset_id is None or asset_type is None:
        return None
    if score is None:
        # A pass-one hit that arrived without a score — a `retrieve_hooks` hit or a hand-built
        # one. `consulted` is what pass one recorded, widened by the channels that scored *this*
        # hit: a channel holding a component for it demonstrably ran, whatever the record says.
        # Both empty means nothing is recorded about which channels ran, and then the components
        # present are the whole of what is known — the fallback `route_retrieve._hit_score` takes,
        # for the same reason. Never the facet's declaration, which would divide by a channel
        # that did not run.
        present = frozenset(
            channel
            for channel, value in (("lexical", lexical), ("semantic", semantic))
            if value is not None
        )
        score = _hybrid(
            float(lexical) if lexical is not None else None,
            float(semantic) if semantic is not None else None,
            consulted=frozenset(consulted) | present,
            scale=scale,
        )
    if score is None:
        return None
    return {
        "facet": facet_name,
        "asset_id": str(asset_id),
        "asset_type": str(asset_type.value if isinstance(asset_type, AssetType) else asset_type),
        "lexical": float(lexical) if lexical is not None else None,
        "semantic": float(semantic) if semantic is not None else None,
        "queries": [str(q) for q in queries],
        "score": float(score),
        "schema_tag": schema_tag,
    }


def _build_retrieved(
    facet_hits: Mapping[str, list[dict[str, Any]]],
    ranking: list[tuple[Any, float]],
    state: Mapping[str, Any],
    index: UnifiedIndex | None = None,
) -> dict[str, Any]:
    attributions: dict[str, list[dict[str, Any]]] = {}
    selected: dict[str, dict[str, Any]] = {}
    best_score: dict[str, float] = {}
    by_id: dict[str, tuple[str, AssetType, float]] = {}

    for _facet_name, hits in facet_hits.items():
        for payload in hits:
            asset_id = str(payload["asset_id"])
            asset_type_raw = payload["asset_type"]
            score = float(payload["score"])
            try:
                at = (
                    asset_type_raw
                    if isinstance(asset_type_raw, AssetType)
                    else AssetType(str(asset_type_raw))
                )
            except ValueError:
                continue

            attributions.setdefault(asset_id, []).append(payload)
            prev_best = best_score.get(asset_id)
            if prev_best is None or score > prev_best:
                best_score[asset_id] = score
                selected[asset_id] = payload

            prev = by_id.get(asset_id)
            if prev is None or score > prev[2]:
                by_id[asset_id] = (asset_id, at, score)

    if not by_id:
        return {
            "by_type": {},
            "selected": {},
            "attributions": {},
            "pulled_in": {},
            "schema_ranking": list(ranking),
            # **Measured on this path too, and it is the path that needs it most.** Zero hits
            # is exactly when "are the question's words in the corpus vocabulary at all" is
            # the question, and this early return is easy to miss — the first attempt at
            # wiring the live index reached only the branch below, and the test that caught it
            # happened to produce no hits.
            "lexical_coverage": _lexical_coverage(state, index),
        }

    budgeted = apply_budgets(list(by_id.values()), pulled_in=[])
    by_type: dict[str, list[str]] = {}
    kept_ids: set[str] = set()
    for asset_id, asset_type, _score in budgeted.hits:
        key = asset_type.value if isinstance(asset_type, AssetType) else str(asset_type)
        by_type.setdefault(key, []).append(asset_id)
        kept_ids.add(asset_id)

    out: dict[str, Any] = {
        "by_type": by_type,
        "selected": {k: v for k, v in selected.items() if k in kept_ids},
        "attributions": {k: v for k, v in attributions.items() if k in kept_ids},
        "pulled_in": {},
        "schema_ranking": list(ranking),
        # **The live index, not the test hook.** This read `state.get("lexical_coverage")`,
        # which nothing on the served path sets, so the field was null on every turn of every
        # arm -- a declared measurement with a dead producer. `BM25.coverage` is the derivation
        # and had no production caller at all; `route_retrieve._lexical_coverage` is the wrapper
        # and its one call site passes `index=None`, which is right there (the F1 no-index path)
        # and is why nothing ever reached the real thing. Imported rather than copied: a second
        # implementation of "which text is measured" is what `check_one_implementation.py`
        # exists to refuse, and the choice of the raw question over a facet rewrite is the part
        # that must not drift.
        "lexical_coverage": _lexical_coverage(state, index),
    }
    # **What the caps discarded, carried out rather than dropped on the floor.** The filter two
    # lines above deletes over-budget ids from `selected` and `attributions`, so without this a
    # 9th-ranked gold table does not exist to the turn and the miss reads as "retrieval never
    # found it". A correctly routed question can still have its gold table below the cap; the
    # offline arm that sized how often is retired (register/citations.py). Emitted only when
    # something was cut, so a turn under budget is byte-identical and `context_hash` holds.
    if budgeted.dropped:
        out["budget_dropped"] = dict(budgeted.dropped)
        out["budget_best_dropped_score"] = dict(budgeted.best_dropped_score)
    return out
