"""Shared serve runtime knobs (config + candidate depth + fuse weights).

One home so facet / pass-two / route / assemble do not each redefine the same
helpers (ADR 0005 §6 one-implementation gate).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Mapping

from governed_bi.register.facets import ChannelState
from governed_bi.register.knobs import Unset, knob_default
from governed_bi.retrieve.fuse import fuse, scale_to_ceiling
from governed_bi.retrieve.structure import CorpusStructure, build_structure

__all__ = [
    "ChannelScale",
    "DEFAULT_CONTEXT_BUDGET",
    "channel_scale",
    "assets_by_id",
    "bool_knob",
    "candidate_depth",
    "combine_channels",
    "configurable",
    "corpus_structure",
    "facet_hits",
    "facet_weights",
    "vector_for_query",
    "float_knob",
    "int_knob",
    "model_id",
    "trust",
    "trusted",
]

DEFAULT_CONTEXT_BUDGET = 80_000


@dataclass(frozen=True, slots=True)
class ChannelScale:
    """The three fusion knobs **this turn** resolved, carried instead of read at import.

    ``w_lexical``, ``w_semantic`` and ``semantic_scale_ceiling`` were module constants built from
    ``knob_default`` when ``serve.runtime`` was first imported, so no request could move them
    (audit I10). That is worse than a missing feature: all three are declared
    ``Role.comparability``, they enter ``config_hash_keys()`` and ``knobs_resolved``, so a run
    could publish ``w_semantic: 0.9``, move its config hash, and behave exactly like the default —
    which is the inverse of the defect ``register/knobs.py`` opens by describing.

    A frozen value object rather than three parameters, because the three are read together and a
    call site that resolved two of them from state and one from a constant would be the same
    defect in a smaller place.
    """

    lexical: float
    semantic: float
    semantic_ceiling: float

    @property
    def weights(self) -> Mapping[str, float]:
        """The pair :func:`~governed_bi.retrieve.fuse.fuse` takes."""
        return {"lexical": self.lexical, "semantic": self.semantic}


def channel_scale(state: Mapping[str, Any]) -> ChannelScale:
    """Resolve the three fusion knobs for this turn. **The one reader.**

    Through :func:`float_knob`, so the precedence is the same as every other knob — state, then
    ``knobs_resolved``, then the register — and an ``UNSET`` raises rather than being guessed.
    """
    return ChannelScale(
        lexical=float_knob(state, "w_lexical"),
        semantic=float_knob(state, "w_semantic"),
        semantic_ceiling=float_knob(state, "semantic_scale_ceiling"),
    )


def combine_channels(
    lexical: float | None,
    semantic: float | None,
    *,
    consulted: Collection[str],
    scale: ChannelScale,
) -> float | None:
    """Weighted fuse of **raw** lexical + semantic scores (shared by pass one and pass two).

    ``None`` when neither channel scored this document.

    ``scale`` is required and has no default, deliberately: a default would let a call site keep
    reading the register while the turn ran on something else, which is the defect I10 names.

    **Takes raw scores and does the scaling itself** (audit I1). Both nodes used to min-max each
    channel over its own scored population and pass the result in, which made the score relative
    to the query — see :func:`~governed_bi.retrieve.fuse.scale_to_ceiling` for why that cannot be
    summed across facets. Doing it here rather than at two call sites is also the structural
    point: the two passes score different candidate sets, so any normaliser needing a population
    gave one asset two different scores in one turn, and ``apply_budgets`` then sorts them
    together in a single global sort.

    The lexical channel is passed through: ``raw/(raw+k)`` is already in ``[0, 1)``, which is the
    absolute scale the sealed scoring contract prescribes. Only cosine needs a ceiling, because
    its practical range is a property of the embedder rather than of the score's definition.

    ``consulted`` names the channels that ran for this query, and it is not derivable from the
    two arguments: ``semantic=None`` means either "the channel did not run for this facet" or
    "it ran and did not return this document", and :func:`~governed_bi.retrieve.fuse.fuse` must
    tell those apart or additional evidence lowers the score.
    """
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
    """Share of the question's terms the corpus vocabulary has, or ``None``.

    ``BM25.coverage`` is the measurement; this decides *which text* is measured and honours the
    ``lexical_coverage`` test hook. The **raw question**, not a facet rewrite: a rewrite is the
    utility model restating the question *into* the corpus's vocabulary, so measuring it would
    report the rewriter's success as the corpus's. ``None`` and never ``0.0`` when unavailable
    — the register declares the field ``Absence.not_measured``.
    """
    # Lives here rather than in a node because both `route_retrieve` (the F1 no-index path,
    # which passes None on purpose) and `pass_two` (the real indexed path) need it, and
    # `route_retrieve` already imports `pass_two` -- so a node-level home makes it a cycle.
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
    """The vector of the text that was **actually searched**, with the channel's own verdict.

    Shared by both retrieval passes because only one of them had it: pass one embedded each
    facet's rewritten query, while pass two took the raw question's call-level vector and
    blended BM25 over the rewrite against cosine over the question — two texts, one score. Pass
    two is the pass whose output becomes the analyst's context and decides which tables survive
    the budget.

    **The returned vector is ``query``'s vector or nothing** (audit I7). It used to fall back to
    ``fallback`` — the *raw question's* vector — whenever the embed raised, so a rate-limited
    rewrite embed produced a facet that searched BM25 over the rewrite, cosine over the
    question, blended the two into one ``score``, and recorded ``semantic: ran``. Nothing
    anywhere said the two channels had searched different text. A degraded channel that reports
    itself is a measurement; one that substitutes another text's vector is a fabricated one.

    Hence the second element, and hence three states rather than a bare ``None``: ``failed`` is
    "should have run and did not", ``not_configured`` is "there is nothing to embed, or nothing
    to embed with". Collapsing them would say a rate limit and an unwired embedder are the same
    event, which is the distinction ``ChannelState`` exists to carry.

    ``fallback`` is trusted **only when it is provably this query's vector**, i.e. when
    ``query == question``. With ``question`` unknown there is no such proof, so the query is
    embedded rather than assumed: that path is the harness's, and it is how ``facet_schema``
    — the one facet that never rewrites — came to score against ``None`` and report
    ``semantic: failed`` on every turn of a 1,351-question run.
    """
    if fallback is not None and question is not None and query == question:
        # No rewrite: `fallback` *is* this text's vector, so the embed is a cache hit.
        return list(fallback), ChannelState.ran
    if not query or embedder is None:
        return None, ChannelState.not_configured
    try:
        return list(embedder.embed([query])[0]), ChannelState.ran
    except Exception:  # noqa: BLE001 — a degraded channel, not a failed turn
        return None, ChannelState.failed


#: ``id(asset container) -> (that container, its projection)``. Insertion-ordered and
#: capped, so a driver that builds a fresh corpus per question cannot grow it without
#: bound. Deliberately **not** a weak cache: ``dict`` does not support weak references,
#: which is why the container is held and identity-checked on read.
_STRUCTURE_CACHE: dict[int | None, tuple[Any, "CorpusStructure"]] = {}
_STRUCTURE_CACHE_MAX = 8


#: Run constants no request may name. Empty in-process by default; registered once by
#: ``api/graph_app.make_graph`` at server start. See :func:`trust`.
_TRUSTED: dict[str, Any] = {}


def trust(constants: Mapping[str, Any] | None = None) -> None:
    """Declare run constants a request must not override (policy, corpus, …).

    Call with nothing to clear (tests, multi-session processes).
    """
    _TRUSTED.clear()
    _TRUSTED.update(constants or {})


def trusted() -> Mapping[str, Any]:
    """What :func:`trust` registered, for a caller that needs to check."""
    return dict(_TRUSTED)


def configurable(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """``config["configurable"]`` with :func:`trust`-ed constants forced over it."""
    if not config:
        return dict(_TRUSTED)
    raw = config.get("configurable") if isinstance(config, Mapping) else None
    if not isinstance(raw, Mapping):
        return dict(_TRUSTED)
    return {**raw, **_TRUSTED} if _TRUSTED else raw


def prompt_variants(config: Mapping[str, Any] | None) -> dict[str, str]:
    """``{prompt name -> variant}`` this run selected, empty when it selected none.

    One reader, because a prompt sent at the wrong variant fails **silently**: the turn records
    the overriding ``prompt_set_hash`` and the model receives the default. Every ``prompt_text``
    call site in ``serve/`` passes this; ``tests/conformance`` refuses a call site that does not.
    """
    raw = configurable(config).get("prompt_variants")
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def model_id(model: Any) -> str | None:
    """Provider model id, or ``None``. Prefer ``model_name`` / ``model`` over ``_llm_type``.

    ``model_id`` is in the tuple because ``ChatBedrockConverse`` spells it that way and spells
    none of the others, so every Bedrock turn fell through to ``_model_name``'s ``_llm_type``
    branch and recorded the **class** name ``amazon_bedrock_converse_chat`` as its model. That
    is not a display bug: ``chat_model`` and ``llm_utility_model`` are ``Role.comparability``
    knobs, so two Bedrock arms serving *different* Anthropic models published the same value
    and ``measure/gates.py``'s drift gate compared them equal. Measured on
    ``runs/serve/2026-08-18.jsonl``: both turns recorded ``amazon_bedrock_converse_chat`` while
    actually running ``us.anthropic.claude-sonnet-5``.

    Last in the tuple, not first: it is the fallback spelling, and a client that offers both
    ``model_name`` and ``model_id`` should keep the meaning the earlier arms recorded.
    """
    for attr in ("model_name", "model", "deployment_name", "model_id"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def int_knob(state: Mapping[str, Any], name: str) -> int:
    """Integer knob: ``state`` → ``knobs_resolved`` → register. ``UNSET`` / bad values raise."""
    raw = state.get(name)
    if raw is None:
        knobs = state.get("knobs_resolved") or {}
        if isinstance(knobs, Mapping):
            raw = knobs.get(name)
    if raw is None:
        raw = knob_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"knob {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"knob {name!r} is {raw!r}, which is not an integer. Falling back to the "
            "register default would make the record report a value this turn did not use."
        ) from err


def bool_knob(state: Mapping[str, Any], name: str) -> bool:
    """Boolean knob with the same precedence as :func:`int_knob`.

    A separate reader rather than one generic function, because the coercion is where the danger
    is and it differs per type: ``int("false")`` raises, but ``bool("false")`` is ``True``, so a
    knob that arrived from JSON as ``"false"`` would switch a feature **on** and be recorded as
    off. Only real booleans and the two JSON spellings are accepted.
    """
    raw = state.get(name)
    if raw is None:
        knobs = state.get("knobs_resolved") or {}
        if isinstance(knobs, Mapping):
            raw = knobs.get(name)
    if raw is None:
        raw = knob_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"knob {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
        return raw.strip().lower() == "true"
    raise ValueError(
        f"knob {name!r} is {raw!r}, which is not a boolean. Coercing it would read "
        "the string 'false' as True and record the opposite of what ran."
    )


def float_knob(state: Mapping[str, Any], name: str) -> float:
    """Float knob with the same precedence as :func:`int_knob`."""
    raw = state.get(name)
    if raw is None:
        knobs = state.get("knobs_resolved") or {}
        if isinstance(knobs, Mapping):
            raw = knobs.get(name)
    if raw is None:
        raw = knob_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"knob {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    try:
        return float(raw)
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"knob {name!r} is {raw!r}, which is not a number. Falling back to the "
            "register default would make the record report a value this turn did not use."
        ) from err


def facet_weights(state: Mapping[str, Any]) -> Mapping[str, float]:
    """Per-facet vote multipliers for :func:`~governed_bi.retrieve.route.route`.

    ``facet_weight_schema`` applies to ``facet_schema``, ``facet_weight_other`` to the rest.
    Both ship 1.0, so this is behaviour-preserving; the point is that moving either now moves
    the result, which was not true while ``route`` took no weights at all.
    """
    from governed_bi.register.stages import FACET_STAGES, Stage

    other = float_knob(state, "facet_weight_other")
    return {
        stage.value: (
            float_knob(state, "facet_weight_schema") if stage is Stage.facet_schema else other
        )
        for stage in FACET_STAGES
    }


def candidate_depth(state: Mapping[str, Any]) -> int:
    """Pass-one / pass-two candidate pool size. One knob, read through :func:`int_knob`."""
    return int_knob(state, "candidate_depth")


def facet_hits(facet_result: Any) -> list[Any]:
    """Hits list from a FacetResult dict or object."""
    if facet_result is None:
        return []
    if isinstance(facet_result, Mapping):
        return list(facet_result.get("hits") or ())
    return list(getattr(facet_result, "hits", None) or ())


def corpus_structure(config: Mapping[str, Any] | None) -> CorpusStructure:
    """Corpus structure projection (ADR 0005 §2.8.2).

    Prefers ``configurable["structure"]``; otherwise derives from assets (memoised).
    Never returns an empty stand-in when assets are present.
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
    """Resolve ``assets_by_id`` or build it from ``corpus`` (list / dict / AnalystCorpus)."""
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
