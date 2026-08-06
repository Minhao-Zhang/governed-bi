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
from governed_bi.retrieve.fuse import scale_within_channel
from governed_bi.retrieve.index import UnifiedIndex
from governed_bi.serve.runtime import candidate_depth, combine_channels
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
        if candidate_ids and queries:
            # **Each channel retrieves; their union is scored** (ADR 0005 §2.4). This block
            # used to be gated on `_scores_lexical(name)` as a whole, and inside it the
            # candidate list came from the lexical ranking alone with the cosine read back
            # by id. Two consequences, both silent:
            #
            # * `facet_example` declares **only** the semantic channel, so the gate skipped
            #   the entire facet. Its pass-one hits are then dropped below, because the
            #   carry-forward keeps only *untagged* hits and every few-shot carries
            #   `TagRule.own_schema`. Net: no past SQL example has ever reached `retrieved`,
            #   the declared `few_shot` budget of 3 was unreachable, and context.py's
            #   `## Few-shots` section could not render — 5 000 assets, 36% of the corpus,
            #   voting on routing and delivering nothing.
            # * For the other facets, an asset with a strong cosine and no shared term could
            #   not enter the context at any depth. `retrieve/semantic.py` names that exact
            #   shape: "A caller that ranks lexically and then attaches a cosine to the
            #   survivors has no semantic channel at all for that facet."
            #
            # The `_scores_lexical` guard itself is kept, one level lower, because the
            # `Anomaly.extra_channel` it was added for is real: a facet must not be scored
            # on a channel `register/facets.py` does not declare for it.
            semantic_scores = (
                _semantic_scores(index, candidate_ids, query_vector)
                if _scores_semantic(name)
                else {}
            )
            # Positive only, matching `nodes/facets.py`. The two passes disagreeing about
            # what counts as a semantic score is how one asset ends up with two different
            # `score` values in one turn.
            semantic_scores = {
                str(aid): float(sc) for aid, sc in semantic_scores.items() if float(sc) > 0.0
            }
            # Query-independent — `pass_two_retrieve` takes one vector for the whole call —
            # so the semantic candidates are ranked once rather than per query.
            semantic_top = [
                aid
                for aid, _ in sorted(
                    semantic_scores.items(), key=lambda p: (-float(p[1]), str(p[0]))
                )
            ][:depth]
            restricted = (
                index.lexical.restrict_to(candidate_ids) if _scores_lexical(name) else None
            )
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
                # `dict.fromkeys` unions the two candidate lists while keeping the lexical
                # order first and de-duplicating; the real ordering happens in `apply_budgets`.
                lexical_top = list(lexical_scores)[:depth]
                # **Both channels onto one scale before they are blended.** `_hybrid` fused the
                # *raw* values at 0.5/0.5, and a 0.5/0.5 blend of BM25 saturation (0.60-0.97)
                # against cosine (0.00-0.635) is not a blend — it is the lexical score plus a
                # small constant. It matters more here than in pass one: this score is what
                # reaches `apply_budgets`, so it decides which tables survive the cap of 8, and
                # that cut is the largest attributable loss in the pipeline. Scaled over each
                # channel's own scored population within this facet, exactly as pass one does.
                lexical_scaled = scale_within_channel(lexical_scores)
                semantic_scaled = scale_within_channel(semantic_scores)
                for asset_id in dict.fromkeys([*lexical_top, *semantic_top]):
                    entry = index.entries.get(asset_id)
                    if entry is None:
                        continue
                    lexical = lexical_scores.get(asset_id)
                    semantic = semantic_scores.get(asset_id)
                    score = _hybrid(
                        lexical_scaled.get(asset_id), semantic_scaled.get(asset_id)
                    )
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


def _scores_semantic(facet_name: str) -> bool:
    """The other half of :func:`_scores_lexical`, and the half that was missing.

    Asking only whether a facet declares ``lexical`` answers "may I run BM25 here", which
    is not the same question as "may I run the vector channel here" — and the code used the
    first answer for both. ``facet_example`` declares ``semantic`` alone, so the conflation
    silenced it completely.
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
    """Every candidate's cosine against the turn's query vector, in one store query.

    This was one point lookup and one :func:`~governed_bi.retrieve.semantic.cosine` **per
    surviving lexical hit**, inside the per-query loop — up to ``candidate_depth`` × the
    number of facet queries of them, all against the same query vector. Against a dict that
    was free; against a store it is a round trip each. The scores do not depend on the
    query text (``pass_two_retrieve`` takes one ``query_vector`` for the whole call), so
    they are computed once for the candidate set and read back by id.

    An asset absent from the result keeps its ``None``: **"this channel did not score it"
    and "it scored zero" are different facts** and the record publishes both.
    """
    store = index.vectors
    if store is None or query_vector is None or not candidate_ids:
        return {}
    return dict(store.search(query_vector, keys=candidate_ids))


def _hybrid(lexical: float | None, semantic: float | None) -> float | None:
    """Delegates to :func:`~governed_bi.serve.runtime.combine_channels`.

    **Its inputs are now scaled and they were not before.** This fused *raw* BM25 saturation
    with *raw* cosine at 0.5/0.5 — and a 0.5/0.5 blend of two quantities whose ranges barely
    overlap is not a blend, it is the lexical score plus a small constant. That mattered more
    here than anywhere else: this score is what reaches ``apply_budgets``, so it decided which
    tables survived the cap of 8, which is the largest attributable loss in the pipeline.
    """
    return combine_channels(lexical, semantic)


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

    out: dict[str, Any] = {
        "by_type": by_type,
        "selected": {k: v for k, v in selected.items() if k in kept_ids},
        "attributions": {k: v for k, v in attributions.items() if k in kept_ids},
        "pulled_in": {},
        "schema_ranking": list(ranking),
        "lexical_coverage": float(state.get("lexical_coverage") or 0.0),
    }
    # **What the caps discarded, carried out rather than dropped on the floor.** The filter two
    # lines above deletes over-budget ids from `selected` and `attributions` and nothing counted
    # them, so a 9th-ranked gold table did not exist to the turn and the resulting miss read as
    # "retrieval never found it". Measured offline: 44% of questions whose schema was routed
    # correctly have a gold table outside the 8-table cap, median worst rank 9 — one position
    # past the budget. Only emitted when something was actually cut, so a turn under budget is
    # byte-identical and its `context_hash` does not move.
    if budgeted.dropped:
        out["budget_dropped"] = dict(budgeted.dropped)
        out["budget_best_dropped_score"] = dict(budgeted.best_dropped_score)
    return out
