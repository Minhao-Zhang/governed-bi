"""Settings: a name, a default, and the ``GOVERNED_BI_*`` variable that overrides it.

The default lives in this module. A process environment variable overrides one
setting for a run and enters the config hash. ``.env`` is not a source of
settings. ``UNSET`` is not a default: reading one raises.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Final, Mapping

from .assets import ASSET_REGISTER

__all__ = [
    "UNSET",
    "Unset",
    "Knob",
    "KNOB_REGISTER",
    "knob_names",
    "defaults",
    "knob_default",
    "config_hash",
    "effective_settings",
    "bind_settings",
    "reset_settings",
    "env_overrides",
    "env_override",
]


class Unset:
    """A setting shipped uncalibrated. Distinct from every value, including ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UNSET"

    def __bool__(self) -> bool:
        raise TypeError(
            "UNSET has no truth value. A setting that ships uncalibrated must be "
            "handled explicitly, not defaulted through a boolean test."
        )


UNSET: Final[Unset] = Unset()

_MISSING: Final[object] = object()
_BOUND: dict[str, Any] | None = None

_ENV_NAMES: Final[Mapping[str, str]] = {
    "chat_model": "GOVERNED_BI_MODEL",
    "llm_reasoning_effort": "GOVERNED_BI_MODEL_EFFORT",
    "llm_utility_model": "GOVERNED_BI_UTILITY_MODEL",
    "llm_utility_reasoning_effort": "GOVERNED_BI_UTILITY_MODEL_EFFORT",
    "llm_provider": "GOVERNED_BI_MODEL_PROVIDER",
    "llm_utility_provider": "GOVERNED_BI_UTILITY_PROVIDER",
    "embedding_provider": "GOVERNED_BI_EMBEDDING_PROVIDER",
    "llm_max_retries": "GOVERNED_BI_LLM_MAX_RETRIES",
    "llm_timeout_s": "GOVERNED_BI_LLM_TIMEOUT_S",
    "llm_utility_timeout_s": "GOVERNED_BI_UTILITY_TIMEOUT_S",
    "embedding_model": "GOVERNED_BI_EMBEDDING_MODEL",
    "rail_node_timeout_s": "GOVERNED_BI_RAIL_NODE_TIMEOUT_S",
    "agent_node_timeout_s": "GOVERNED_BI_AGENT_NODE_TIMEOUT_S",
    "agent_recursion_limit": "GOVERNED_BI_AGENT_RECURSION_LIMIT",
    "corpus_release": "GOVERNED_BI_CORPUS_RELEASE",
}

_PROVIDER_KNOBS: Final[frozenset[str]] = frozenset(
    {"llm_provider", "llm_utility_provider", "embedding_provider"}
)
_PROVIDER_FALLBACK: Final[tuple[tuple[str, str], ...]] = (
    ("llm_provider", "GOVERNED_BI_MODEL_PROVIDER"),
    ("llm_utility_provider", "GOVERNED_BI_UTILITY_PROVIDER"),
    ("embedding_provider", "GOVERNED_BI_EMBEDDING_PROVIDER"),
)


@dataclass(frozen=True, slots=True)
class Knob:
    """One setting: the name the record uses, its default, and its variable."""

    name: str
    default: Any
    env_var: str


def _k(name: str, default: Any, env_var: str | None = None) -> Knob:
    return Knob(name, default, env_var or _ENV_NAMES.get(name) or f"GOVERNED_BI_{name.upper()}")


_ASSET_BUDGETS: Final[tuple[tuple[str, Any], ...]] = tuple(
    sorted((t.value, p.budget) for t, p in ASSET_REGISTER.items())
)


KNOB_REGISTER: tuple[Knob, ...] = (
    _k("summary_max_chars", 250),
    _k("summary_min_chars", 1),
    _k("asset_budgets", _ASSET_BUDGETS),
    _k("corpus_release", ""),
    _k("corpus_dir", "../BIRD-corpus"),
    _k("schema", ""),
    _k("seed_dir", ""),
    _k("candidate_depth", 50),
    _k("route_top_n", 3),
    _k("facet_weight_schema", 1.0),
    _k("facet_weight_other", 1.0),
    _k("w_lexical", 0.5),
    _k("w_semantic", 0.5),
    _k("semantic_scale_ceiling", 0.6),
    _k("lexical_saturation_k", 1.2),
    _k("expand_hops", 0),
    _k("max_steiner_points", 5),
    _k("max_crossings", 2),
    _k("max_resolve_additions", 400),
    _k("context_budget_chars", 80_000),
    _k("read_body_max_tokens", 20_000),
    _k("chat_model", "gpt-6-luna"),
    _k("facet_model", None),
    _k("rewrite_model", None),
    _k("llm_reasoning_effort", "xhigh"),
    _k("llm_utility_model", "gpt-6-luna"),
    _k("llm_utility_reasoning_effort", "medium"),
    _k("llm_provider", "openai"),
    _k("llm_utility_provider", "openai"),
    _k("embedding_provider", "openai"),
    _k("llm_max_retries", 3),
    _k("llm_max_output_tokens", 32_000),
    _k("llm_timeout_s", 300.0),
    _k("llm_utility_timeout_s", 30.0),
    _k("rail_node_timeout_s", 120.0),
    _k("agent_node_timeout_s", 1200.0),
    _k("statement_timeout_ms", 120_000),
    _k("agent_recursion_limit", 40),
    _k("embedding_model", "text-embedding-3-large"),
    _k("embedding_dimensions", None),
    _k("prompt_set", None),
    _k("permitted_functions", UNSET),
    _k("sqlglot_version", UNSET),
    _k("guard_rules_enabled", UNSET),
    _k("hard_block_suspect", True),
    _k("graded_delivery_enabled", True),
    _k("run_query_attempt_cap", 5),
    _k("max_rows", 200_000),
    _k("g_length_max_chars", 8_000),
    _k("cache_cost_reduction_target", 0.30),
    _k("abstention_policy_enabled", False),
    _k("enable_structured_percentage_check", True),
    _k("git_sha", None),
    _k("git_main_sha", None),
    _k("working_tree_dirty", None),
    _k("diff_sha256", None),
    _k("serve_workers", None),
    _k("build_workers", None),
    _k("arms", None),
    _k("schemas_under_test", None),
    _k("split", None),
    _k("question_subset", None),
    _k("conversation_db", "runs/conversations.sqlite"),
    _k("harness_db", "runs/harness-checkpoints.sqlite"),
    _k("vector_cache", "runs/vectors"),
    _k("aws_region", ""),
    _k("proxy_secret", ""),
    _k("proxy_region", ""),
    _k("proxy_ca_bundle", ""),
)


def knob_names() -> frozenset[str]:
    return frozenset(k.name for k in KNOB_REGISTER)


def defaults() -> Mapping[str, Any]:
    """Every setting at its declared default. ``UNSET`` values remain ``UNSET``."""
    return {k.name: k.default for k in KNOB_REGISTER}


def knob_default(name: str) -> Any:
    """One setting's declared default. ``UNSET`` stays ``UNSET``. Raises if undeclared."""
    for knob in KNOB_REGISTER:
        if knob.name == name:
            return knob.default
    raise KeyError(f"{name!r} is not a declared setting")


def env_overrides() -> Mapping[str, str]:
    """``{setting name -> environment variable}`` for every setting."""
    return {k.name: k.env_var for k in KNOB_REGISTER}


def _coerce(knob: Knob, raw: str) -> Any:
    """Parse ``raw`` as the type of ``knob``'s default. Blank is not a value."""
    text = str(raw).strip()
    if text == "":
        return _MISSING
    default = knob.default
    if knob.name in _PROVIDER_KNOBS:
        return text.lower()
    if isinstance(default, bool):
        lowered = text.lower()
        if lowered not in ("true", "false"):
            raise ValueError(
                f"{knob.env_var}={raw!r} is not a boolean; setting {knob.name!r} declares {default!r}"
            )
        return lowered == "true"
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(text)
        except ValueError as err:
            raise ValueError(
                f"{knob.env_var}={raw!r} cannot be read as the int that setting {knob.name!r} declares"
            ) from err
    if isinstance(default, float):
        try:
            return float(text)
        except ValueError as err:
            raise ValueError(
                f"{knob.env_var}={raw!r} cannot be read as the float that setting {knob.name!r} declares"
            ) from err
    if default is UNSET or default is None:
        if knob.name == "embedding_dimensions":
            try:
                return int(text)
            except ValueError as err:
                raise ValueError(
                    f"{knob.env_var}={raw!r} cannot be read as the int that setting {knob.name!r} declares"
                ) from err
        return text
    return text


def env_override(name: str, environ: Mapping[str, str] | None = None) -> Any | None:
    """What ``name``'s variable says, or ``None`` when it is unset or blank."""
    env = os.environ if environ is None else environ
    for knob in KNOB_REGISTER:
        if knob.name != name:
            continue
        raw = env.get(knob.env_var)
        if raw is None:
            return None
        value = _coerce(knob, str(raw))
        return None if value is _MISSING else value
    raise KeyError(f"{name!r} is not a declared setting")


def _jsonable(value: Any) -> Any:
    if value is UNSET:
        return None
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def config_hash(values: Mapping[str, Any]) -> str:
    """SHA-256 of the effective settings. ``config_hash`` itself is not an input."""
    body = {key: _jsonable(value) for key, value in values.items() if key != "config_hash"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _recorded_default(knob: Knob) -> Any:
    return None if knob.default is UNSET else knob.default


def resolve_settings(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Defaults overlaid with ``GOVERNED_BI_*`` from ``environ``. Does not read ``.env``."""
    env = os.environ if environ is None else environ
    resolved: dict[str, Any] = {}
    for knob in KNOB_REGISTER:
        raw = env.get(knob.env_var)
        if raw is None or str(raw).strip() == "":
            resolved[knob.name] = _recorded_default(knob)
            continue
        value = _coerce(knob, str(raw))
        resolved[knob.name] = _recorded_default(knob) if value is _MISSING else value
    shared = (env.get("GOVERNED_BI_PROVIDER") or "").strip()
    if shared:
        for name, var in _PROVIDER_FALLBACK:
            if not (env.get(var) or "").strip():
                resolved[name] = shared.lower()
    if not (env.get("GOVERNED_BI_AWS_REGION") or "").strip():
        for var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
            raw = (env.get(var) or "").strip()
            if raw:
                resolved["aws_region"] = raw
                break
    resolved["config_hash"] = config_hash(resolved)
    return resolved


def bind_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Remember one resolution for the rest of this process, until :func:`reset_settings`."""
    global _BOUND
    bound = dict(settings)
    _BOUND = bound
    return bound


def reset_settings() -> None:
    """Drop the bound resolution so the next read sees the process environment again."""
    global _BOUND
    _BOUND = None


def effective_settings() -> dict[str, Any]:
    """The bound resolution, or a fresh one from the process environment."""
    if _BOUND is not None:
        return dict(_BOUND)
    return resolve_settings(os.environ)


def _assert_knobs_are_coherent() -> None:
    names = [k.name for k in KNOB_REGISTER]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:  # pragma: no cover - import-time guard
        raise AssertionError(f"duplicate settings: {dupes}")
    variables = [k.env_var for k in KNOB_REGISTER]
    dup_vars = sorted({n for n in variables if variables.count(n) > 1})
    if dup_vars:  # pragma: no cover - import-time guard
        raise AssertionError(f"two settings share one variable: {dup_vars}")


_assert_knobs_are_coherent()
