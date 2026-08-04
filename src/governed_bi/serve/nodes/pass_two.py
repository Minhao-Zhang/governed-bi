"""Pass-two re-retrieval inside selected schemas (ADR 0005 §2.5).

Re-runs facet queries against a ``UnifiedIndex`` restricted to the selected
schemas (global IDF via ``BM25.restrict_to``). Not a filter of pass-one.
Untagged pass-one hits are carried forward unconditionally before budgets.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from governed_bi.register.assets import AssetType
from governed_bi.register.facets import FACET_CHANNELS, FACET_TARGETS, Channel
from governed_bi.register.stages import Stage
from governed_bi.retrieve.budget import apply_budgets
from governed_bi.retrieve.fuse import fuse
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.retrieve.semantic import cosine
from governed_bi.serve.runtime import FUSE_WEIGHTS, candidate_depth
from governed_bi.serve.runtime import facet_hits as hits_of

__all__ = ["pass_two_retrieve"]


def pass_two_retrieve(
    *,
    state: Mapping[str, Any],
    index: UnifiedIndex,
    schemas: Sequence[Any],
    ranking: list[tuple[Any, float]],
    query_vector: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Re-search selected schemas per facet query; merge untagged pass-one hits."""
    schema_set = {str(s) for s in schemas}
    depth = candidate_depth(state)

    # Per-facet hit lists (dict payloads) after within-facet dedup.
    hits_by_facet: dict[str, list[dict[str, Any]]] = {}

    for facet_name, facet_result in (state.get("facets") or {}).items():
        name = str(facet_name)
        queries = _facet_queries(facet_result)
        targets = _facet_targets(name)
        candidate_ids = _candidate_ids(index, schema_set, targets)

        merged: dict[str, dict[str, Any]] = {}
        if candidate_ids and queries and _scores_lexical(name):
            restricted = index.lexical.restrict_to(candidate_ids)
            for query in queries:
                if not query:
                    continue
                scored = restricted.search(query)
                top = sorted(scored, key=lambda p: (-float(p[1]), str(p[0])))
                kept = [(aid, sc) for aid, sc in top if float(sc) > 0.0][:depth]
                for asset_id, lex_score in kept:
                    entry = index.entries.get(asset_id)
                    if entry is None:
                        continue
                    lexical = float(lex_score)
                    semantic = _semantic_for(entry.summary, index, query_vector)
                    score = _hybrid(lexical, semantic)
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
        for hit in hits_of(facet_result):
            if _raw_schema_tag(hit) is not None:
                continue
            payload = _pass_one_payload(hit, name)
            if payload is None:
                continue
            _merge_within_facet(merged, payload)

        if merged:
            hits_by_facet[name] = list(merged.values())

    return _build_retrieved(hits_by_facet, ranking, state)


def _scores_lexical(facet_name: str) -> bool:
    """Whether this facet declares a lexical channel — read from ``FACET_CHANNELS``.

    Pass two had no such guard, so it scored ``facet_example`` on ``lexical`` and a
    few-shot outranked an entity hit on a channel the same turn's record declared
    ``not_configured``. That is ``Anomaly.extra_channel``, and it went undetected because
    nothing compared observation to declaration. ADR 0005 §2 is explicit that
    ``register/facets.py`` decides which channels a facet uses and ``retrieve/`` must never
    decide it locally; ``nodes/facets.py`` already asks the same question of the same table.

    An unrecognised facet name has no declaration to consult, so it is not scored on a
    channel nobody declared for it.
    """
    try:
        stage = Stage(facet_name)
    except ValueError:
        return False
    return Channel.lexical in FACET_CHANNELS.get(stage, frozenset())


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


def _semantic_for(
    summary: str,
    index: UnifiedIndex,
    query_vector: Sequence[float] | None,
) -> float | None:
    if query_vector is None:
        return None
    doc_vec = index.vectors.get(summary)
    if doc_vec is None:
        return None
    return float(cosine(query_vector, doc_vec))


def _hybrid(lexical: float | None, semantic: float | None) -> float | None:
    scores: dict[str, float] = {}
    if lexical is not None:
        scores["lexical"] = float(lexical)
    if semantic is not None:
        scores["semantic"] = float(semantic)
    if not scores:
        return None
    return float(fuse(scores, FUSE_WEIGHTS))


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


def _pass_one_payload(hit: Any, facet_name: str) -> dict[str, Any] | None:
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
        score = _hybrid(
            float(lexical) if lexical is not None else None,
            float(semantic) if semantic is not None else None,
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
            "lexical_coverage": float(state.get("lexical_coverage") or 0.0),
        }

    budgeted = apply_budgets(list(by_id.values()), pulled_in=[])
    by_type: dict[str, list[str]] = {}
    kept_ids: set[str] = set()
    for asset_id, asset_type, _score in budgeted.hits:
        key = asset_type.value if isinstance(asset_type, AssetType) else str(asset_type)
        by_type.setdefault(key, []).append(asset_id)
        kept_ids.add(asset_id)

    return {
        "by_type": by_type,
        "selected": {k: v for k, v in selected.items() if k in kept_ids},
        "attributions": {k: v for k, v in attributions.items() if k in kept_ids},
        "pulled_in": {},
        "schema_ranking": list(ranking),
        "lexical_coverage": float(state.get("lexical_coverage") or 0.0),
    }
