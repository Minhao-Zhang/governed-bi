"""Environment toggles + reusable numbers + model configuration.

Environments are **toggles, not architecture forks** (Architecture §9). The same
code runs in both; only these switches differ. Bake the abstractions in now so
prod is a config flip, not a rewrite.

The numeric defaults are the "reusable numbers" starting points (Architecture
§7); tune against the BIRD-Obfuscation eval before trusting them.

Model choices (provider, LLM, embedding) live in a project-level config file
(``governed_bi.toml`` at the repo root) parsed by :func:`load_settings`, so the
whole system reads one source of truth and a model swap is a config edit, not a
code change. **The API key is never stored in the file** - it is read from an
environment variable named by ``ModelConfig.api_key_env`` (default
``OPENAI_API_KEY``) at call time. This keeps secrets out of git.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path


class Environment(str, Enum):
    """Dev/test runs on BIRD; prod runs at enterprise scale. See Architecture §9."""

    dev = "dev"  # BIRD: auto-accept corpus, single all-access identity, files + SQLite
    prod = "prod"  # enterprise: PR + owner + CI, real user + RLS, service fleet


@dataclass(frozen=True)
class MemoryBudget:
    """Per-route memory injection budget (Profile / Episodic / Correction)."""

    profile: int
    episodic: int
    correction: int


# Architecture §7 route memory budgets.
ROUTE_MEMORY_BUDGETS: dict[str, MemoryBudget] = {
    "nl2sql": MemoryBudget(5, 2, 5),
    "kpi_lookup": MemoryBudget(2, 0, 1),
    "knowledge_qa": MemoryBudget(3, 1, 1),
    "deep_analysis": MemoryBudget(8, 8, 4),
}


@dataclass(frozen=True)
class ModelConfig:
    """Which models the LLM and embedding seams call, and where the key lives.

    Provider-agnostic by shape, OpenAI by default (the current project decision).
    The concrete clients live in ``governed_bi.llm``; this record only names what
    they should use, so swapping a model is a config edit. ``api_key_env`` names
    an environment variable - the key itself is **never** stored here or in the
    config file.
    """

    provider: str = "openai"
    llm_model: str = "gpt-5.5"
    llm_reasoning_effort: str = "low"  # low | medium | high (provider-specific)
    llm_max_output_tokens: int | None = None  # None = provider default
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int | None = None  # None = model default (1536 for -3-small)
    api_key_env: str = "OPENAI_API_KEY"

    def api_key(self) -> str | None:
        """Read the API key from the configured environment variable, or None."""
        return os.environ.get(self.api_key_env)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Construct via ``Settings.for_env(...)`` or load a
    project config file with :func:`load_settings`."""

    environment: Environment

    # ── D6 human gate ──
    auto_accept_corpus: bool  # dev: True (adversary is sole reviewer); prod: False (PR + owner + CI)

    # ── D7 identity / RLS ──
    single_all_access_identity: bool  # dev: True; prod: False (real user + gateway RLS)

    # ── Server: suspect-column enforcement (Server §"three points" #1) ──
    hard_block_suspect_columns: bool  # dev/BIRD: True; prod/enterprise: soft-warn + drop reliability tier

    # ── Memory (D8) — working always on; episodic/correction off until eval earns it ──
    working_memory: bool = True
    episodic_memory: bool = False
    correction_memory: bool = False

    # ── Reusable numbers (Architecture §7; tune on BIRD first) ──
    profile_ttl_days: int = 365
    episodic_ttl_days: int = 90
    episodic_decay_per_day: float = 0.02
    correction_ttl_days: int = 180
    sql_cache_ttl_minutes: int = 15
    cache_hit_cosine_gate: float = 0.92
    few_shot_recall_cosine_gate: float = 0.95
    few_shot_recall_confidence_gate: float = 0.90
    few_shot_recall_max_fail_count: int = 3

    route_memory_budgets: dict[str, MemoryBudget] = field(
        default_factory=lambda: dict(ROUTE_MEMORY_BUDGETS)
    )

    # ── Model seam configuration (see load_settings / governed_bi.toml) ──
    models: ModelConfig = field(default_factory=ModelConfig)

    @classmethod
    def for_env(
        cls, environment: Environment | str, *, models: ModelConfig | None = None
    ) -> "Settings":
        env = Environment(environment)
        base = dict(models=models) if models is not None else {}
        if env is Environment.dev:
            return cls(
                environment=env,
                auto_accept_corpus=True,
                single_all_access_identity=True,
                hard_block_suspect_columns=True,
                **base,
            )
        return cls(
            environment=env,
            auto_accept_corpus=False,
            single_all_access_identity=False,
            hard_block_suspect_columns=False,
            **base,
        )


# The project config file lives at the repo root. Kept here so callers do not
# hard-code the path; overridable via the ``GOVERNED_BI_CONFIG`` env var (useful
# for tests and alternate deployments).
_CONFIG_FILENAME = "governed_bi.toml"


def _default_config_path() -> Path | None:
    """Locate ``governed_bi.toml``: the env override, else walk up from here to
    the first ancestor that contains it. Returns None when no file is found (so
    callers fall back to built-in defaults)."""
    override = os.environ.get("GOVERNED_BI_CONFIG")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _model_config_from_table(table: dict) -> ModelConfig:
    """Build a :class:`ModelConfig` from a ``[models]`` TOML table, ignoring keys
    it does not recognise so a forward-compatible file never crashes an old build.
    """
    known = {f for f in ModelConfig.__dataclass_fields__}
    kwargs = {k: v for k, v in table.items() if k in known}
    return ModelConfig(**kwargs)


def load_settings(path: str | Path | None = None) -> Settings:
    """Load :class:`Settings` from the project config file.

    Reads ``[runtime].environment`` (default ``dev``) and the ``[models]`` table
    from ``governed_bi.toml``. Missing file or missing tables fall back to the
    built-in defaults, so this is always safe to call. The API key is **not** read
    here - :meth:`ModelConfig.api_key` reads it from the environment on demand.
    """
    resolved = Path(path) if path is not None else _default_config_path()
    if resolved is None or not resolved.is_file():
        return Settings.for_env(Environment.dev)

    data = tomllib.loads(resolved.read_text(encoding="utf-8"))
    runtime = data.get("runtime", {})
    env = runtime.get("environment", Environment.dev.value)
    models = _model_config_from_table(data.get("models", {}))
    settings = Settings.for_env(env, models=models)

    # Optional [runtime] overrides for the environment toggles, so a deployment
    # can, e.g., soft-warn on suspect columns without switching the whole env.
    overrides = {
        k: runtime[k]
        for k in ("auto_accept_corpus", "single_all_access_identity", "hard_block_suspect_columns")
        if k in runtime
    }
    return replace(settings, **overrides) if overrides else settings
