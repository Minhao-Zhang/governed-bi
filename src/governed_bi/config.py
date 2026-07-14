"""Project policy (TOML) + secrets (environment).

**Policy** — environment toggles, models, datasource shape, corpus path, serve
flags — lives in ``governed_bi.toml``, optionally overlaid by a git-ignored
``governed_bi.local.toml`` beside it. Parsed by :func:`load_settings`.

**Secrets** — API keys, DSN passwords — live only in the process environment
(or a git-ignored ``.env`` loaded as a fallback). TOML never stores secret
values; it only names the env var (``api_key_env``, ``dsn_env``).

Precedence: code defaults → ``governed_bi.toml`` → ``governed_bi.local.toml`` →
secret values read from the environment at call time.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any


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
    llm_model: str = "gpt-5.6-sol"  # project default; swap in governed_bi.toml
    llm_reasoning_effort: str = "low"  # none | low | medium | high | xhigh | max (provider-specific)
    llm_max_output_tokens: int | None = None  # None = provider default
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int | None = None  # None = model default (1536 for -3-small)
    api_key_env: str = "OPENAI_API_KEY"

    def api_key(self) -> str | None:
        """Read the API key from the configured environment variable, or None."""
        return os.environ.get(self.api_key_env)


@dataclass(frozen=True)
class DataSourceConfig:
    """Which database the engine and curator read (the ``[datasource]`` table).

    SECURITY: a Postgres/Redshift DSN carries a password, so it is **not** stored
    here. Set ``dsn_env`` to the name of an environment variable holding the full
    libpq DSN (read at call time), exactly as the API key is handled. ``dsn`` is an
    inline fallback for local, secret-free DSNs only.
    """

    kind: str = "sqlite"  # sqlite | postgres | redshift
    db: str = "beer_factory"  # default corpus schema subtree / BIRD db_id
    sqlite_path: str = "data/bird/beer_factory.sqlite"  # kind=sqlite; repo-root-relative
    dsn: str | None = None  # kind=postgres/redshift: inline DSN (local, secret-free only)
    dsn_env: str | None = None  # ...or the env var holding the DSN (preferred)
    schema: str | None = None  # optional designated default for bare-ref L4 resolution
    multi_schema: bool = True  # postgres/redshift default: span ALL user schemas (D15)

    def is_multi_schema(self) -> bool:
        """Whether this data source spans every user schema in one database.

        True for Postgres/Redshift unless ``multi_schema`` is explicitly opted
        out (``False``). The connector then enumerates and introspects all user
        schemas (D15); SQL and guardrails use fully-qualified ``schema.table``.
        SQLite is always single-schema regardless of this flag (BIRD graded path).
        Opt out with ``multi_schema=False`` plus a pinned ``schema`` when a
        deployment must stay single-schema on Postgres.
        """
        return self.multi_schema and self.kind.lower() in ("postgres", "redshift")

    def resolve_dsn(self) -> str | None:
        """The DSN to dial: inline ``dsn`` if set, else ``$dsn_env``, else None."""
        if self.dsn:
            return self.dsn
        if self.dsn_env:
            return os.environ.get(self.dsn_env)
        return None


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Construct via ``Settings.for_env(...)`` or
    :func:`load_settings`."""

    environment: Environment

    # ── D6 human gate ──
    auto_accept_corpus: bool  # dev: True (adversary is sole reviewer); prod: False (PR + owner + CI)

    # ── D7 identity / RLS ──
    single_all_access_identity: bool  # dev: True; prod: False (real user + gateway RLS)

    # ── Server: suspect-column enforcement (Server §"three points" #1) ──
    hard_block_suspect_columns: bool  # dev/BIRD: True; prod/enterprise: soft-warn + drop reliability tier

    # ── pipeline-design §6: deliver-and-grade semantic failures ──
    # When True, coverage / L3–L5 repair-exhaustion / execution-exhaustion
    # return the last generated SQL with ``semantic_assurance=unverified``
    # instead of a hard refusal. L2 policy + curated refuse-gate stay hard.
    grade_semantic_failures: bool = False

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

    # ── Model seam (see [models] in governed_bi.toml) ──
    models: ModelConfig = field(default_factory=ModelConfig)

    # ── Data source (see [datasource]) ──
    datasource: DataSourceConfig = field(default_factory=DataSourceConfig)

    # ── Paths (see [paths]) ──
    corpus_root: str = "corpus"  # repo-root-relative or absolute (D9/D13)

    # ── Serve / API (see [serve]) ──
    can_stream: bool = False  # True when a streaming chat graph is fronted
    allow_edit: bool = True  # corpus file-write; for_env sets False in prod
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    @classmethod
    def for_env(
        cls,
        environment: Environment | str,
        *,
        models: ModelConfig | None = None,
        datasource: DataSourceConfig | None = None,
        corpus_root: str | None = None,
        can_stream: bool | None = None,
        allow_edit: bool | None = None,
        cors_origins: tuple[str, ...] | None = None,
    ) -> "Settings":
        env = Environment(environment)
        base: dict[str, Any] = {}
        if models is not None:
            base["models"] = models
        if datasource is not None:
            base["datasource"] = datasource
        if corpus_root is not None:
            base["corpus_root"] = corpus_root
        if can_stream is not None:
            base["can_stream"] = can_stream
        if allow_edit is not None:
            base["allow_edit"] = allow_edit
        if cors_origins is not None:
            base["cors_origins"] = cors_origins
        if env is Environment.dev:
            return cls(
                environment=env,
                auto_accept_corpus=True,
                single_all_access_identity=True,
                hard_block_suspect_columns=True,
                allow_edit=base.pop("allow_edit", True),
                **base,
            )
        return cls(
            environment=env,
            auto_accept_corpus=False,
            single_all_access_identity=False,
            hard_block_suspect_columns=False,
            allow_edit=base.pop("allow_edit", False),
            **base,
        )


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

_CONFIG_FILENAME = "governed_bi.toml"
_LOCAL_CONFIG_FILENAME = "governed_bi.local.toml"
_DOTENV_FILENAME = ".env"
_DEFAULT_CORPUS_ROOT = "corpus"

# When False, :func:`load_settings` skips ``governed_bi.local.toml``. Tests set
# this False so a developer's local Postgres/corpus overlay cannot leak into the
# hermetic suite. Production and local runs leave it True.
APPLY_LOCAL_OVERLAY = True


def _abspath(path: Path | str) -> Path:
    """Normalize an already-absolute path without ``Path.resolve`` / ``os.getcwd``.

    ``Path.resolve()`` calls ``os.path.realpath``, which uses ``getcwd`` and trips
    LangGraph's ASGI blockbuster when path helpers run on the event loop. Repo
    joins and ``__file__`` are already absolute; ``normpath`` collapses ``..``.
    """
    p = Path(path)
    if not p.is_absolute():
        raise ValueError(f"expected an absolute path, got {p!r}")
    return Path(os.path.normpath(p))


def _package_file() -> Path:
    """Absolute path to this module, without ``Path.resolve()``."""
    p = Path(__file__)
    # ``__file__`` is absolute for normal package imports; keep a rare relative
    # fallback for frozen/zip loaders (may use CWD - only at import time).
    return _abspath(p if p.is_absolute() else os.path.abspath(p))


def _default_config_path() -> Path | None:
    """Locate ``governed_bi.toml``: walk up from this package to the first
    ancestor that contains it. Returns None when no file is found."""
    for parent in _package_file().parents:
        candidate = parent / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _compute_repo_root() -> Path:
    """The repo root: the nearest ancestor of this file that holds
    ``governed_bi.toml`` or ``pyproject.toml``. Falls back to the package's
    grandparent (``src/governed_bi/`` -> repo root) if neither is found."""
    here = _package_file()
    for parent in here.parents:
        if (parent / _CONFIG_FILENAME).is_file() or (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[2]


# Resolved once at import (outside request handlers) so later callers never need
# filesystem walks that block under LangGraph's ASGI detector.
_REPO_ROOT = _compute_repo_root()


def _repo_root() -> Path:
    """The repo root (see :func:`_compute_repo_root`). Cached at import."""
    return _REPO_ROOT


def resolve_corpus_root(value: str | Path | None = None) -> Path:
    """Resolve a corpus root path to an absolute path.

    ``None`` uses the default ``corpus`` fixture path. An absolute path is used
    as-is; a **relative** path resolves against the repo root, *not* the process
    CWD - so a sibling checkout is reachable as ``../BIRD-corpus`` regardless of
    where the process runs. Pass ``settings.corpus_root`` from :func:`load_settings`
    for the configured value.
    """
    raw = _DEFAULT_CORPUS_ROOT if value is None else value
    p = Path(raw)
    return _abspath(p if p.is_absolute() else _repo_root() / p)


# --------------------------------------------------------------------------- #
# TOML loading
# --------------------------------------------------------------------------- #


def _merge_tables(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge TOML tables; overlay wins on conflicts. Non-table values replace."""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_tables(out[key], value)
        else:
            out[key] = value
    return out


def _known_kwargs(cls: type, table: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys that are fields on ``cls`` (forward-compatible TOML)."""
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in table.items() if k in known}


def _model_config_from_table(table: dict[str, Any]) -> ModelConfig:
    return ModelConfig(**_known_kwargs(ModelConfig, table))


def _datasource_from_table(table: dict[str, Any]) -> DataSourceConfig:
    return DataSourceConfig(**_known_kwargs(DataSourceConfig, table))


def _cors_origins_from(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(o.strip() for o in value.split(",") if o.strip())
    if isinstance(value, list):
        return tuple(str(o).strip() for o in value if str(o).strip())
    raise TypeError(f"cors_origins must be a string or list, got {type(value).__name__}")


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_settings(
    path: str | Path | None = None,
    *,
    apply_local: bool | None = None,
) -> Settings:
    """Load :class:`Settings` from the project config file (+ optional local overlay).

    Reads ``governed_bi.toml`` (or ``path``). When ``apply_local`` is true (the
    default unless :data:`APPLY_LOCAL_OVERLAY` is False), also merges
    ``governed_bi.local.toml`` from the same directory if it exists — local wins.
    Missing file or missing tables fall back to built-in defaults. Secret values
    are **not** read here; :meth:`ModelConfig.api_key` and
    :meth:`DataSourceConfig.resolve_dsn` read them on demand.
    """
    resolved = Path(path) if path is not None else _default_config_path()
    if resolved is None or not resolved.is_file():
        return Settings.for_env(Environment.dev)

    data = _load_toml(resolved)
    use_local = APPLY_LOCAL_OVERLAY if apply_local is None else apply_local
    if use_local:
        local_path = resolved.parent / _LOCAL_CONFIG_FILENAME
        if local_path.is_file():
            data = _merge_tables(data, _load_toml(local_path))

    runtime = data.get("runtime", {})
    env = runtime.get("environment", Environment.dev.value)
    models = _model_config_from_table(data.get("models", {}))
    datasource = _datasource_from_table(data.get("datasource", {}))

    paths = data.get("paths", {})
    corpus_root = paths.get("corpus_root", _DEFAULT_CORPUS_ROOT)

    serve = data.get("serve", {})
    can_stream = bool(serve["can_stream"]) if "can_stream" in serve else None
    allow_edit = bool(serve["allow_edit"]) if "allow_edit" in serve else None
    cors_origins = (
        _cors_origins_from(serve["cors_origins"]) if "cors_origins" in serve else None
    )

    settings = Settings.for_env(
        env,
        models=models,
        datasource=datasource,
        corpus_root=str(corpus_root),
        can_stream=can_stream,
        allow_edit=allow_edit,
        cors_origins=cors_origins,
    )

    # Optional [runtime] overrides for the environment toggles, so a deployment
    # can soft-warn on suspect columns without switching the whole env.
    overrides = {
        k: runtime[k]
        for k in (
            "auto_accept_corpus",
            "single_all_access_identity",
            "hard_block_suspect_columns",
            "grade_semantic_failures",
        )
        if k in runtime
    }
    return replace(settings, **overrides) if overrides else settings


# --------------------------------------------------------------------------- #
# .env loading (secrets only — local-run convenience)
# --------------------------------------------------------------------------- #


def _find_dotenv() -> Path | None:
    """Locate a ``.env`` next to the project config / repo root, else CWD."""
    config = _default_config_path()
    if config is not None:
        candidate = config.parent / _DOTENV_FILENAME
        if candidate.is_file():
            return candidate
    repo_candidate = _repo_root() / _DOTENV_FILENAME
    if repo_candidate.is_file():
        return repo_candidate
    # CWD fallback is a local convenience; skip it under a running event loop
    # (``Path.cwd`` -> ``getcwd`` trips LangGraph's ASGI blockbuster).
    try:
        import asyncio

        asyncio.get_running_loop()
    except RuntimeError:
        cwd_candidate = Path.cwd() / _DOTENV_FILENAME
        return cwd_candidate if cwd_candidate.is_file() else None
    return None


def _parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines into a dict. Blank lines and ``#`` comments are
    skipped; a leading ``export`` is tolerated; surrounding single/double quotes
    are stripped; an unquoted trailing `` # comment`` is dropped. Deliberately
    small - not a full shell parser."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]  # quoted: take verbatim
        else:
            hash_at = value.find(" #")  # unquoted: drop an inline comment
            if hash_at != -1:
                value = value[:hash_at].rstrip()
        out[key] = value
    return out


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Populate ``os.environ`` from a ``.env`` file; return what was applied.

    Intended for **secrets only** (API keys, DSN values). Policy belongs in TOML.

    A real environment variable wins by default (``setdefault`` semantics): the
    file only fills in variables that are unset, so exporting a key in the shell
    always takes precedence over ``.env``. Pass ``override=True`` to let the file
    replace already-set variables. A missing or unreadable file is a no-op.
    """
    resolved = Path(path) if path is not None else _find_dotenv()
    if resolved is None or not resolved.is_file():
        return {}
    try:
        parsed = _parse_dotenv(resolved.read_text(encoding="utf-8"))
    except OSError:
        return {}
    applied: dict[str, str] = {}
    for key, value in parsed.items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
