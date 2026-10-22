"""Pass-two retrieval inside the selected schemas (ADR 0005 §2.5).

A fresh search, not a filter of pass one. Untagged pass-one hits are carried forward."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Mapping, Sequence

from governed_bi.registry.assets import AssetType
from governed_bi.registry.channels import (
    CHANNEL_TARGETS,
    DECLARED_CHANNELS,
    SCORING_CHANNELS,
    Channel,
    ChannelState,
)
from governed_bi.registry.stages import Stage
from governed_bi.retrieve.budget import apply_budgets
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.serve.runtime import (
    ChannelScale,
    candidate_depth,
    channel_scale,
    combine_channels,
    vector_for_query,
)
from governed_bi.serve.runtime import channel_hits as hits_of
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
    """``query_vector`` is the raw question. ``embedder`` scores a rewrite against its own vector."""
    schema_set = {str(s) for s in schemas}
    depth = candidate_depth(state)
    scale = channel_scale(state)
    question = str(state.get("question") or "")
    #: This call's query-to-vector map. Channels often share a phrase.
    vectors: dict[str, Sequence[float] | None] = {}

    hits_by_channel: dict[str, list[dict[str, Any]]] = {}

    for channel_name, channel_result in (state.get("channels") or {}).items():
        name = str(channel_name)
        queries = _channel_queries(channel_result)
        targets = _channel_targets(name)
        candidate_ids = _candidate_ids(index, schema_set, targets)

        merged: dict[str, dict[str, Any]] = {}
        if candidate_ids and queries:
            # Score each declared channel (ADR 0005 §2.4). Skipping this when
            # lexical is absent drops cosine-only assets.
            restricted = (
                index.lexical.restrict_to(candidate_ids) if _scores_lexical(name) else None
            )
            # Channels this pass consulted. A miss is not a channel that never ran.
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
                # This query's vector. The call-level vector is the raw question.
                semantic_scores = (
                    _semantic_scores(
                        index,
                        candidate_ids,
                        _vector_for(query, question, query_vector, embedder, vectors),
                    )
                    if _scores_semantic(name)
                    else {}
                )
                # Positive scores only, as in pass one, so one asset has one score.
                semantic_scores = {
                    str(aid): float(sc) for aid, sc in semantic_scores.items() if float(sc) > 0.0
                }
                semantic_top = [
                    aid
                    for aid, _ in sorted(
                        semantic_scores.items(), key=lambda p: (-float(p[1]), str(p[0]))
                    )
                ][:depth]
                # Lexical ids first. ``apply_budgets`` does the ranking.
                lexical_top = list(lexical_scores)[:depth]
                # Fixed ceiling, not a min-max over this candidate set. The two passes see different sets.
                for asset_id in dict.fromkeys([*lexical_top, *semantic_top]):
                    entry = index.entries.get(asset_id)
                    if entry is None:
                        continue
                    lexical = lexical_scores.get(asset_id)
                    semantic = semantic_scores.get(asset_id)
                    score = _hybrid(lexical, semantic, consulted=consulted, scale=scale)
                    payload = {
                        "channel": name,
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
                    _merge_within_channel(merged, payload)

        # Untagged hits have no schema to fall outside the selection.
        pass_one_consulted = _pass_one_consulted(channel_result)
        for hit in hits_of(channel_result):
            if _raw_schema_tag(hit) is not None:
                continue
            carried = _pass_one_payload(hit, name, pass_one_consulted, scale)
            if carried is None:
                continue
            _merge_within_channel(merged, carried)

        if merged:
            hits_by_channel[name] = list(merged.values())

    return _build_retrieved(hits_by_channel, ranking, state, index)


def _scores_lexical(channel_name: str) -> bool:
    """Lexical only when ``DECLARED_CHANNELS`` says so. An unknown name scores nothing (ADR 0005)."""
    try:
        stage = Stage(channel_name)
    except ValueError:
        return False
    return Channel.lexical in DECLARED_CHANNELS.get(stage, frozenset())


def _scores_semantic(channel_name: str) -> bool:
    """Semantic only when declared. The lexical declaration does not answer this."""
    try:
        stage = Stage(channel_name)
    except ValueError:
        return False
    return Channel.semantic in DECLARED_CHANNELS.get(stage, frozenset())


def _channel_targets(channel_name: str) -> frozenset[AssetType] | None:
    try:
        stage = Stage(channel_name)
    except ValueError:
        return None
    return CHANNEL_TARGETS.get(stage)


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


def _channel_queries(channel_result: Any) -> list[str]:
    if channel_result is None:
        return []
    if isinstance(channel_result, Mapping):
        raw = channel_result.get("queries") or ()
    else:
        raw = getattr(channel_result, "queries", None) or ()
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
    """One store query. A missing key stays absent, not zero."""
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
    """A failed embed stays ``None``. This pass must not substitute the raw question's vector."""
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
    return combine_channels(lexical, semantic, consulted=consulted, scale=scale)


def _merge_within_channel(
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
        # Components from the higher-scoring query (ADR 0005 §2.4).
        merged[asset_id] = {**payload, "queries": queries}
    else:
        prev["queries"] = queries


def _pass_one_consulted(channel_result: Any) -> frozenset[str]:
    """What pass one recorded as having run. The declaration would count a channel that never scored."""
    if isinstance(channel_result, Mapping):
        channels = channel_result.get("channels")
    else:
        channels = getattr(channel_result, "channels", None)
    if not isinstance(channels, Mapping):
        return frozenset()
    scoring = {c.value for c in SCORING_CHANNELS}
    return frozenset(
        str(name)
        for name, state in channels.items()
        if str(name) in scoring and str(state) == ChannelState.ran.value
    )


def _pass_one_payload(
    hit: Any, channel_name: str, consulted: Collection[str], scale: ChannelScale
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
        # No score on the hit. Widen ``consulted`` by channels that scored it, not by the declaration.
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
        "channel": channel_name,
        "asset_id": str(asset_id),
        "asset_type": str(asset_type.value if isinstance(asset_type, AssetType) else asset_type),
        "lexical": float(lexical) if lexical is not None else None,
        "semantic": float(semantic) if semantic is not None else None,
        "queries": [str(q) for q in queries],
        "score": float(score),
        "schema_tag": schema_tag,
    }


def _build_retrieved(
    channel_hits: Mapping[str, list[dict[str, Any]]],
    ranking: list[tuple[Any, float]],
    state: Mapping[str, Any],
    index: UnifiedIndex | None = None,
) -> dict[str, Any]:
    attributions: dict[str, list[dict[str, Any]]] = {}
    selected: dict[str, dict[str, Any]] = {}
    best_score: dict[str, float] = {}
    by_id: dict[str, tuple[str, AssetType, float]] = {}

    for _channel_name, hits in channel_hits.items():
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
            # Measured on the empty return too: zero hits is when coverage is the question.
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
        # Raw question, not a channel rewrite.
        "lexical_coverage": _lexical_coverage(state, index),
    }
    # Record a real cut: omitting it makes the table look never retrieved, and an empty cut would change the hash.
    if budgeted.dropped:
        out["budget_dropped"] = dict(budgeted.dropped)
        out["budget_best_dropped_score"] = dict(budgeted.best_dropped_score)
    return out
