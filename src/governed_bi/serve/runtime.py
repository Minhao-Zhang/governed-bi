"""One implementation of the serve settings readers (ADR 0005 §6)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Mapping

from governed_bi.registry.channels import ChannelState
from governed_bi.registry.settings import Unset, setting_default
from governed_bi.retrieve.fuse import fuse, scale_to_ceiling
from governed_bi.retrieve.structure import CorpusStructure, build_structure

__all__ = [
    "ChannelScale",
    "DEFAULT_CONTEXT_BUDGET",
    "channel_scale",
    "assets_by_id",
    "bool_setting",
    "candidate_depth",
    "combine_channels",
    "configurable",
    "corpus_structure",
    "channel_hits",
    "channel_weights",
    "vector_for_query",
    "float_setting",
    "int_setting",
    "model_id",
    "trust",
    "trusted",
]

DEFAULT_CONTEXT_BUDGET = 80_000


@dataclass(frozen=True, slots=True)
class ChannelScale:
    """Recorded fusion settings. A value that did not move the fuse would be a false record."""

    lexical: float
    semantic: float
    semantic_ceiling: float

    @property
    def weights(self) -> Mapping[str, float]:
        return {"lexical": self.lexical, "semantic": self.semantic}


def channel_scale(state: Mapping[str, Any]) -> ChannelScale:
    return ChannelScale(
        lexical=float_setting(state, "w_lexical"),
        semantic=float_setting(state, "w_semantic"),
        semantic_ceiling=float_setting(state, "semantic_scale_ceiling"),
    )


def combine_channels(
    lexical: float | None,
    semantic: float | None,
    *,
    consulted: Collection[str],
    scale: ChannelScale,
) -> float | None:
    """``None`` if neither channel scored. A per-pass normaliser would score one asset twice."""
    scores: dict[str, float] = {}
    if lexical is not None:
        scores["lexical"] = float(lexical)
    if semantic is not None:
        scores["semantic"] = scale_to_ceiling(
            float(semantic), ceiling=scale.semantic_ceiling
        )
    if not scores:
        return None
    return float(fuse(scores, scale.weights, consulted=consulted))


def lexical_coverage(state: Mapping[str, Any], index: Any) -> float | None:
    """Coverage of the raw question. Scoring the rewrite would report the rewriter as the corpus."""
    # A home on either node would import-cycle: ``route_retrieve`` imports ``pass_two``.
    hooked = state.get("lexical_coverage")
    if isinstance(hooked, (int, float)) and not isinstance(hooked, bool):
        return float(hooked)
    lexical = getattr(index, "lexical", None)
    coverage = getattr(lexical, "coverage", None)
    if coverage is None:
        return None
    try:
        return coverage(str(state.get("question") or ""))
    except Exception:  # noqa: BLE001 — a degraded signal must not fail the turn
        return None


def vector_for_query(
    query: str | None,
    *,
    question: str | None,
    fallback: Sequence[float] | None,
    embedder: Any | None,
) -> tuple[Sequence[float] | None, ChannelState]:
    """The searched text's vector, or nothing. A raw-question fallback would still report ``ran``."""
    if fallback is not None and question is not None and query == question:
        # No rewrite, so ``fallback`` is this text's vector.
        return list(fallback), ChannelState.ran
    if not query or embedder is None:
        return None, ChannelState.not_configured
    try:
        return list(embedder.embed([query])[0]), ChannelState.ran
    except Exception:  # noqa: BLE001 — a degraded channel, not a failed turn
        return None, ChannelState.failed


#: Capped identity cache. ``dict`` cannot hold a weak reference, so the container is held.
_STRUCTURE_CACHE: dict[int | None, tuple[Any, "CorpusStructure"]] = {}
_STRUCTURE_CACHE_MAX = 8


#: Run constants a request cannot name. See :func:`trust` and ADR 0007.
_TRUSTED: dict[str, Any] = {}


def trust(constants: Mapping[str, Any] | None = None) -> None:
    """Run constants a request must not override. Calling with nothing clears them."""
    _TRUSTED.clear()
    _TRUSTED.update(constants or {})


def trusted() -> Mapping[str, Any]:
    return dict(_TRUSTED)


def configurable(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not config:
        return dict(_TRUSTED)
    raw = config.get("configurable") if isinstance(config, Mapping) else None
    if not isinstance(raw, Mapping):
        return dict(_TRUSTED)
    return {**raw, **_TRUSTED} if _TRUSTED else raw


def prompt_variants(config: Mapping[str, Any] | None) -> dict[str, str]:
    """This run's variant map. A second reader can send the default under the override's hash."""
    raw = configurable(config).get("prompt_variants")
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def model_id(model: Any) -> str | None:
    """Provider model id, or ``None``. Some clients expose it only as ``model_id``, so that name is last."""
    for attr in ("model_name", "model", "deployment_name", "model_id"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def int_setting(state: Mapping[str, Any], name: str) -> int:
    raw = state.get(name)
    if raw is None:
        settings = state.get("settings_resolved") or {}
        if isinstance(settings, Mapping):
            raw = settings.get(name)
    if raw is None:
        raw = setting_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"setting {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"setting {name!r} is {raw!r}, which is not an integer. Falling back to the "
            "register default would make the record report a value this turn did not use."
        ) from err


def bool_setting(state: Mapping[str, Any], name: str) -> bool:
    """Only real booleans and ``"true"`` / ``"false"``. ``bool("false")`` is ``True``."""
    raw = state.get(name)
    if raw is None:
        settings = state.get("settings_resolved") or {}
        if isinstance(settings, Mapping):
            raw = settings.get(name)
    if raw is None:
        raw = setting_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"setting {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
        return raw.strip().lower() == "true"
    raise ValueError(
        f"setting {name!r} is {raw!r}, which is not a boolean. Coercing it would read "
        "the string 'false' as True and record the opposite of what ran."
    )


def float_setting(state: Mapping[str, Any], name: str) -> float:
    raw = state.get(name)
    if raw is None:
        settings = state.get("settings_resolved") or {}
        if isinstance(settings, Mapping):
            raw = settings.get(name)
    if raw is None:
        raw = setting_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"setting {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    try:
        return float(raw)
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"setting {name!r} is {raw!r}, which is not a number. Falling back to the "
            "register default would make the record report a value this turn did not use."
        ) from err


def channel_weights(state: Mapping[str, Any]) -> Mapping[str, float]:
    """Schema has its own multiplier. Changing either must change the ranking."""
    from governed_bi.registry.stages import CHANNEL_STAGES, Stage

    other = float_setting(state, "channel_weight_other")
    return {
        stage.value: (
            float_setting(state, "channel_weight_schema") if stage is Stage.channel_schema else other
        )
        for stage in CHANNEL_STAGES
    }


def candidate_depth(state: Mapping[str, Any]) -> int:
    return int_setting(state, "candidate_depth")


def channel_hits(channel_result: Any) -> list[Any]:
    if channel_result is None:
        return []
    if isinstance(channel_result, Mapping):
        return list(channel_result.get("hits") or ())
    return list(getattr(channel_result, "hits", None) or ())


def corpus_structure(config: Mapping[str, Any] | None) -> CorpusStructure:
    """Corpus structure for this config (ADR 0005 §2.8.2). Assets never yield an empty stand-in."""
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
        # Held beside the value, so a recycled ``id()`` cannot return another corpus.
        _STRUCTURE_CACHE[key] = (source, structure)
    return structure


def assets_by_id(cfg: Mapping[str, Any]) -> dict[str, Any]:
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
        values = list(corpus.values())
        if values and _looks_like_asset(values[0]):
            return {str(k): v for k, v in corpus.items()}
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
