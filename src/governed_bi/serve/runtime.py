"""Shared serve runtime knobs (config + candidate depth + fuse weights).

One home so facet / pass-two / route / assemble do not each redefine the same
helpers (ADR 0005 §6 one-implementation gate).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from governed_bi.retrieve.structure import CorpusStructure, build_structure

__all__ = [
    "DEFAULT_CANDIDATE_DEPTH",
    "DEFAULT_CONTEXT_BUDGET",
    "FUSE_WEIGHTS",
    "assets_by_id",
    "candidate_depth",
    "configurable",
    "corpus_structure",
    "facet_hits",
]

DEFAULT_CANDIDATE_DEPTH = 50
DEFAULT_CONTEXT_BUDGET = 80_000
FUSE_WEIGHTS: Mapping[str, float] = {"lexical": 0.5, "semantic": 0.5}

#: ``id(asset container) -> (that container, its projection)``. Insertion-ordered and
#: capped, so a driver that builds a fresh corpus per question cannot grow it without
#: bound. Deliberately **not** a weak cache: ``dict`` does not support weak references,
#: which is why the container is held and identity-checked on read.
_STRUCTURE_CACHE: dict[int | None, tuple[Any, "CorpusStructure"]] = {}
_STRUCTURE_CACHE_MAX = 8


def configurable(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """``config[\"configurable\"]`` when present; else empty mapping."""
    if not config:
        return {}
    raw = config.get("configurable") if isinstance(config, Mapping) else None
    return raw if isinstance(raw, Mapping) else {}


def candidate_depth(state: Mapping[str, Any]) -> int:
    """Pass-one / pass-two candidate pool size (state, then knobs, else default)."""
    raw = state.get("candidate_depth")
    if raw is None:
        knobs = state.get("knobs_resolved") or {}
        if isinstance(knobs, Mapping):
            raw = knobs.get("candidate_depth")
    try:
        return int(raw) if raw is not None else DEFAULT_CANDIDATE_DEPTH
    except (TypeError, ValueError):
        return DEFAULT_CANDIDATE_DEPTH


def facet_hits(facet_result: Any) -> list[Any]:
    """Hits list from a FacetResult dict or object."""
    if facet_result is None:
        return []
    if isinstance(facet_result, Mapping):
        return list(facet_result.get("hits") or ())
    return list(getattr(facet_result, "hits", None) or ())


def corpus_structure(config: Mapping[str, Any] | None) -> CorpusStructure:
    """This turn's corpus structure projection (ADR 0005 §2.8.2).

    ``configurable["structure"]`` is the **declared** wiring: the projection is built
    beside the index, once, from the same asset set, and passed in. That is where its
    ``problems`` have a reader -- an unresolvable join endpoint is a curation defect and
    §2.8.2 says it must surface where the corpus is built, not as a decline three
    layers away.

    When it is absent this **derives it from the assets already on ``configurable``**
    rather than returning an empty projection, and the distinction matters: an empty
    projection is not a degradation, it is the defect §2.8.2 was written about --
    ``connect`` on an empty edge set declines ``missing_join_path`` for every turn
    licensing two tables, and single-table turns answer, so nothing looks broken. The
    derivation is a pure function of the asset set, so two turns given the same assets
    cannot disagree; what the fallback genuinely loses is the ``problems`` list, which
    has no reader at serve time. Nothing in ``src/`` builds the index either, so this
    path is the one the in-repo callers take today.

    The fallback is memoised on the identity of the asset container the caller supplied,
    so ``route``, ``resolve`` and ``connect`` share one object instead of building three.
    That is the *other* half of §2.2's "computed at build, not query time": three
    projections per turn of a pooled corpus is three rounds of few-shot SQL parsing, and
    a driver whose per-turn cost depends on how many nodes read the corpus is a driver
    whose latency numbers mean something different from the ones before it.
    """
    cfg = configurable(config)
    ready = cfg.get("structure")
    if isinstance(ready, CorpusStructure):
        return ready

    source = cfg.get("assets_by_id")
    if source is None:
        source = cfg.get("corpus")
    key = id(source) if source is not None else None
    cached = _STRUCTURE_CACHE.get(key)
    if cached is not None and cached[0] is source:
        return cached[1]

    structure, _problems = build_structure(assets_by_id(cfg).values())
    if key is not None:
        if len(_STRUCTURE_CACHE) >= _STRUCTURE_CACHE_MAX:
            _STRUCTURE_CACHE.pop(next(iter(_STRUCTURE_CACHE)))
        # The source object is held alongside the value, so a recycled ``id()`` cannot
        # return another corpus's projection: the identity check above rejects it.
        _STRUCTURE_CACHE[key] = (source, structure)
    return structure


def assets_by_id(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve ``assets_by_id`` or build it from ``corpus`` (list / dict / AnalystCorpus).

    One implementation, here rather than in ``nodes/assemble.py``, because the
    structure projection needs the same asset set the render does. Two resolvers would
    be two answers to "which assets does this turn have", and they would disagree
    exactly where one of the four accepted shapes was handled in only one of them.
    """
    direct = cfg.get("assets_by_id")
    if isinstance(direct, Mapping) and direct:
        return {str(k): v for k, v in direct.items()}

    corpus = cfg.get("corpus")
    if corpus is None:
        return {}

    by_id = getattr(corpus, "by_id", None)
    if isinstance(by_id, Mapping):
        return {str(k): v for k, v in by_id.items()}

    if isinstance(corpus, Mapping):
        # id → asset
        values = list(corpus.values())
        if values and _looks_like_asset(values[0]):
            return {str(k): v for k, v in corpus.items()}
        # type → sequence of assets
        out: dict[str, Any] = {}
        for value in values:
            _ingest_assets(out, value)
        return out

    if isinstance(corpus, Sequence) and not isinstance(corpus, (str, bytes)):
        out = {}
        _ingest_assets(out, corpus)
        return out

    return {}


def _ingest_assets(out: dict[str, Any], value: Any) -> None:
    if isinstance(value, Mapping) and _looks_like_asset(value):
        aid = value.get("id")
        if aid is not None:
            out[str(aid)] = value
        return
    if hasattr(value, "id") and hasattr(value, "asset_type"):
        out[str(value.id)] = value
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _ingest_assets(out, item)


def _looks_like_asset(obj: Any) -> bool:
    if isinstance(obj, Mapping):
        return "id" in obj and ("asset_type" in obj or "summary" in obj)
    return hasattr(obj, "id") and (
        hasattr(obj, "asset_type") or hasattr(obj, "summary")
    )
