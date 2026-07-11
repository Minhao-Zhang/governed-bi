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

As a local-run convenience, :func:`load_dotenv` also reads a git-ignored ``.env``
at the repo root and fills in any variables it defines that are **not already
set** - so a real environment variable always wins and ``.env`` is a fallback,
never an override. It runs once automatically when the package is imported.
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
    llm_model: str = "gpt-5.5"  # GA flagship; gpt-5.6-sol is limited-preview (404s without access)
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
    """Which database the engine and curator read (the ``[datasource]`` table in
    ``governed_bi.toml``). One source of truth, so pointing at a different DB - the
    vendored SQLite fixture, or a BIRD-Obfuscation Postgres instance - is a config
    edit, not a code change. Built into a ``Connector`` by
    ``governed_bi.gateway.build_connector``.

    SECURITY: a Postgres/Redshift DSN carries a password, so it is **not** stored
    here. Set ``dsn_env`` to the name of an environment variable holding the full
    libpq DSN (read at call time), exactly as the API key is handled. ``dsn`` is an
    inline fallback for local, secret-free DSNs only.
    """

    kind: str = "sqlite"  # sqlite | postgres | redshift
    db: str = "beer_factory"  # db_id / corpus namespace
    sqlite_path: str = "data/bird/beer_factory.sqlite"  # kind=sqlite; repo-root-relative
    dsn: str | None = None  # kind=postgres/redshift: inline DSN (local, secret-free only)
    dsn_env: str | None = None  # ...or the env var holding the DSN (preferred)
    schema: str | None = None  # postgres/redshift schema; None -> the connector default

    def resolve_dsn(self) -> str | None:
        """The DSN to dial: inline ``dsn`` if set, else ``$dsn_env``, else None."""
        if self.dsn:
            return self.dsn
        if self.dsn_env:
            return os.environ.get(self.dsn_env)
        return None


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

    # ── Data source: which DB the engine/curator read (see [datasource]) ──
    datasource: DataSourceConfig = field(default_factory=DataSourceConfig)

    @classmethod
    def for_env(
        cls,
        environment: Environment | str,
        *,
        models: ModelConfig | None = None,
        datasource: DataSourceConfig | None = None,
    ) -> "Settings":
        env = Environment(environment)
        base: dict = {}
        if models is not None:
            base["models"] = models
        if datasource is not None:
            base["datasource"] = datasource
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


def _repo_root() -> Path:
    """The repo root: the nearest ancestor of this file that holds
    ``governed_bi.toml`` or ``pyproject.toml``. Falls back to the package's
    grandparent (``src/governed_bi/`` -> repo root) if neither is found."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / _CONFIG_FILENAME).is_file() or (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[2]


_CORPUS_ROOT_ENV = "GOVERNED_BI_CORPUS"
_DEFAULT_CORPUS_ROOT = "corpus"


def resolve_corpus_root(value: str | Path | None = None) -> Path:
    """Resolve the corpus root (D9/D13) to an absolute path.

    Precedence: the explicit ``value`` argument, else ``$GOVERNED_BI_CORPUS``,
    else ``corpus`` (the vendored beer_factory fixture). An absolute path is used
    as-is; a **relative** path resolves against the repo root, *not* the process
    CWD - so the separate corpus repo (D13) is reachable as a sibling checkout via
    ``GOVERNED_BI_CORPUS=../BIRD-corpus`` regardless of where the process runs.
    """
    raw = value if value is not None else os.environ.get(_CORPUS_ROOT_ENV, _DEFAULT_CORPUS_ROOT)
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (_repo_root() / p).resolve()


def _model_config_from_table(table: dict) -> ModelConfig:
    """Build a :class:`ModelConfig` from a ``[models]`` TOML table, ignoring keys
    it does not recognise so a forward-compatible file never crashes an old build.
    """
    known = {f for f in ModelConfig.__dataclass_fields__}
    kwargs = {k: v for k, v in table.items() if k in known}
    return ModelConfig(**kwargs)


def _datasource_from_table(table: dict) -> DataSourceConfig:
    """Build a :class:`DataSourceConfig` from a ``[datasource]`` TOML table,
    ignoring unrecognised keys so a forward-compatible file never crashes an old
    build (same tolerance as :func:`_model_config_from_table`)."""
    known = {f for f in DataSourceConfig.__dataclass_fields__}
    kwargs = {k: v for k, v in table.items() if k in known}
    return DataSourceConfig(**kwargs)


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
    datasource = _datasource_from_table(data.get("datasource", {}))
    settings = Settings.for_env(env, models=models, datasource=datasource)

    # Optional [runtime] overrides for the environment toggles, so a deployment
    # can, e.g., soft-warn on suspect columns without switching the whole env.
    overrides = {
        k: runtime[k]
        for k in ("auto_accept_corpus", "single_all_access_identity", "hard_block_suspect_columns")
        if k in runtime
    }
    return replace(settings, **overrides) if overrides else settings


# --------------------------------------------------------------------------- #
# .env loading (local-run convenience)
# --------------------------------------------------------------------------- #
#
# The API key (and any other secret) is read from the process environment. For
# local runs we also read a ``.env`` at the repo root and populate any variables
# it defines that are not already set - so a real environment variable always
# wins and ``.env`` is a fallback, never an override. ``.env`` is git-ignored;
# never commit a real key. Location is overridable via ``GOVERNED_BI_DOTENV``.

_DOTENV_FILENAME = ".env"


def _find_dotenv() -> Path | None:
    """Locate a ``.env``: the ``GOVERNED_BI_DOTENV`` override, else the first
    ancestor of this file that contains one, else the current directory."""
    override = os.environ.get("GOVERNED_BI_DOTENV")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _DOTENV_FILENAME
        if candidate.is_file():
            return candidate
    cwd_candidate = Path.cwd() / _DOTENV_FILENAME
    return cwd_candidate if cwd_candidate.is_file() else None


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

    A real environment variable wins by default (``setdefault`` semantics): the
    file only fills in variables that are unset, so exporting a key in the shell
    always takes precedence over ``.env``. Pass ``override=True`` to let the file
    replace already-set variables. A missing or unreadable file is a no-op, so
    this is always safe to call.
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
