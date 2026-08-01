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

# Text-and-pure-functions only, no settings import of its own, so this cannot
# cycle back — the property the registry is written to preserve.
from .prompts import resolve as resolve_prompt_variants


class Environment(str, Enum):
    """Dev/test runs on BIRD; prod runs at enterprise scale. See Architecture §9."""

    dev = "dev"  # BIRD: auto-accept corpus, single all-access identity, files + SQLite
    prod = "prod"  # enterprise: PR + owner + CI, real user + RLS, service fleet


@dataclass(frozen=True)
class ModelConfig:
    """Which models the LLM and embedding seams call, and where the key lives.

    Provider-agnostic by shape, OpenAI by default (the current project decision).
    ``provider`` selects the concrete client the ``governed_bi.llm`` seam builds:
    ``"openai"`` (default) or ``"bedrock"`` (AWS Bedrock via the ``bedrock`` extra).
    This record only names what they should use, so swapping a model — or a whole
    provider — is a config edit. ``api_key_env`` names an environment variable -
    the key itself is **never** stored here or in the config file. For Bedrock,
    boto3 resolves credentials from the standard chain (env / profile / role);
    point ``api_key_env`` at whichever variable must be present to go live (e.g.
    ``AWS_BEARER_TOKEN_BEDROCK`` or ``AWS_ACCESS_KEY_ID``).
    """

    provider: str = "openai"  # openai | bedrock
    llm_model: str = "gpt-5.6-luna"  # project default; swap in governed_bi.toml
    llm_reasoning_effort: str = "low"  # none | low | medium | high | xhigh | max (provider-specific)
    llm_max_output_tokens: int | None = None  # None = provider default
    # Recorded on every run even when None. Decoding temperature is the largest
    # single source of run-to-run variance in a text-to-SQL score, and it was neither
    # pinned nor recorded anywhere (AUDIT E5): two runs of "the same" configuration
    # could differ because a provider changed its default. None = provider default,
    # which is now at least a stated, comparable fact rather than an unknown.
    llm_temperature: float | None = None
    # A stalled model connection must not hang a curator step or serve turn
    # forever: recursion_limit bounds steps, not wall-clock. None = provider default.
    request_timeout_s: float | None = 60.0
    max_retries: int = 2
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int | None = None  # None = model default (1536 for -3-small)
    api_key_env: str = "OPENAI_API_KEY"
    region: str | None = None  # Bedrock only: AWS region; None = boto3 default (AWS_REGION)
    # Bedrock only: extra model-specific fields merged into the Converse request's
    # ``additionalModelRequestFields``. The escape hatch for anything the engine does
    # not translate, and the override for what it does: keys set here win over the
    # ``llm_reasoning_effort`` translation in ``llm.langchain_client``. Left None,
    # only the translated reasoning field is sent.
    bedrock_request_fields: dict[str, Any] | None = None

    def api_key(self) -> str | None:
        """Read the API key from the configured environment variable, or None."""
        return os.environ.get(self.api_key_env)

    def has_credentials(self) -> bool:
        """Whether this deployment can actually authenticate to the provider.

        For OpenAI this is exactly "the named env var is set". For Bedrock it is
        **not**: boto3 resolves credentials from a chain — env vars, a shared
        ``~/.aws/credentials`` profile, the SSO cache, an instance/task role — so
        gating on one env var refuses to boot a perfectly authenticated deployment
        whose keys live in the documented default place. ``api_key_env`` still wins
        when it is set, so an operator who wants a hard env-var gate keeps one.

        Note for the no-credentials case: botocore's chain ends at EC2 instance
        metadata, so a machine with no credentials at all pays a short probe timeout
        here before returning False. That is once per stack build, not per turn.
        """
        if self.api_key() is not None:
            return True
        if self.provider != "bedrock":
            return False
        try:
            import botocore.session  # noqa: PLC0415 (lazy: bedrock extra only)
        except ModuleNotFoundError:
            return False  # provider=bedrock without the extra: nothing can authenticate
        try:
            return botocore.session.get_session().get_credentials() is not None
        except Exception:
            return False

    def credentials_hint(self) -> str:
        """Human-facing description of what to set, for fail-closed error messages."""
        if self.provider == "bedrock":
            return (
                f"AWS credentials — env {self.api_key_env}, an ~/.aws/credentials "
                "profile (optionally AWS_PROFILE), or an instance/task role"
            )
        return self.api_key_env


@dataclass(frozen=True)
class DataSourceConfig:
    """Which database the engine and curator read (the ``[datasource]`` table).

    SECURITY: a Postgres/Redshift DSN carries a password, so it is **not** stored
    here. Set ``dsn_env`` to the name of an environment variable holding the full
    libpq DSN (read at call time), exactly as the API key is handled. ``dsn`` is an
    inline fallback for local, secret-free DSNs only.

    ``db`` is the lake identity for future ``db:`` note-scope sentinels (ADR 0003),
    not the SQL schema pin — that is ``corpus_pin`` / ``schema``.
    """

    kind: str = "sqlite"  # sqlite | postgres | redshift
    corpus_pin: str = "beer_factory"  # default corpus schema subtree / BIRD db_id
    db: str = "main"  # lake identity for db: scope sentinels (≠ corpus_pin)
    sqlite_path: str = "data/bird/beer_factory.sqlite"  # kind=sqlite; repo-root-relative
    dsn: str | None = None  # kind=postgres/redshift: inline DSN (local, secret-free only)
    dsn_env: str | None = None  # ...or the env var holding the DSN (preferred)
    schema: str | None = None  # optional designated default for bare-ref L4 resolution

    def serving_schema(self) -> str | None:
        """The schema tables are qualified under — the always-on multi-schema model.

        Every deployment serves schema-qualified ``schema.table`` (D15). This
        returns the schema a *bare* reference resolves to (``default_schema`` in
        the guardrails / analyst), or ``None`` when the source spans every schema
        with no single default (a bare reference then fails closed).

        - SQLite has no native schema level, so the connector ATTACHes the file
          under a fake schema alias (``corpus_pin``); that alias is the serving
          schema, and ``schema.table`` resolves against the attachment.
        - Postgres / Redshift: the pinned ``schema`` (e.g. a single ``db_id`` for
          the eval harness), or ``None`` to span every user schema in the database.
        """
        if self.kind.lower() == "sqlite":
            return self.schema or self.corpus_pin
        return self.schema

    def resolve_dsn(self) -> str | None:
        """The DSN to dial: inline ``dsn`` if set, else ``$dsn_env``, else None."""
        if self.dsn:
            return self.dsn
        if self.dsn_env:
            return os.environ.get(self.dsn_env)
        return None


@dataclass(frozen=True)
class NoteGovernance:
    """The five note-delivery knobs as ONE :meth:`Settings.for_env` argument.

    Not a field on :class:`Settings` — the five live flat there, and
    ``provenance.serve_config_hash``, ``retrieval.triggers`` and
    ``analyst.context`` all read them flat. This is a parameter object: a carrier
    that ``for_env`` expands, so a caller that wants note governance says it in one
    argument instead of five, and the group is named in one place instead of a
    fourth hand-maintained list of the same five strings.

    It exists because ``for_env`` was the ONLY way the eval drivers built Settings
    and it could not express any of these, so ADR 0003's trigger channel was
    unreachable from a run: :func:`load_settings` read a ``[notes]`` table, the
    drivers threw that Settings away and kept only ``.models``, and
    ``fire_triggers`` returned ``[]`` on every question of every graded run while
    the corpus carried triggers authored for exactly that path.

    Every field is ``X | None`` where ``None`` means "leave the ``Settings``
    default" — the same convention every other ``for_env`` keyword uses, one level
    down. Absence is therefore not expressible as a value here; that is what
    :meth:`overrides` is for.
    """

    always_note_global_max: int | None = None
    always_note_char_max: int | None = None
    pin_triggers_enabled: bool | None = None
    pin_require_certified: bool | None = None
    pin_max: int | None = None

    def overrides(self) -> dict[str, Any]:
        """Only the knobs this carrier actually states, as ``Settings`` kwargs.

        Keys are OMITTED rather than set to ``None``: every one of the five is
        non-optional on :class:`Settings`, so passing ``None`` through would replace
        an int with ``None`` and break ``apply_always_budget`` arithmetic at serve
        time instead of leaving the default in place.
        """
        return {
            f.name: value
            for f in fields(self)
            if (value := getattr(self, f.name)) is not None
        }

    @classmethod
    def from_settings(
        cls, settings: "Settings", *, pin_triggers: bool = False
    ) -> "NoteGovernance":
        """Carry an existing Settings' note knobs across a ``for_env`` rebuild.

        Both eval drivers read :func:`load_settings` for its ``models`` and then build
        a FRESH Settings through ``for_env``, which drops everything else the TOML
        said. This carries the ``[notes]`` half back over, so an operator can configure
        note governance for a run by file and not only by flag.

        ``pin_triggers`` can only turn PIN **on**. The drivers expose it as a
        ``store_true`` and OFF is the baseline arm, so "the flag was not passed" must
        mean "no opinion", not a silent override of a TOML that asked for pinning.
        """
        return cls(
            always_note_global_max=settings.always_note_global_max,
            always_note_char_max=settings.always_note_char_max,
            pin_triggers_enabled=pin_triggers or settings.pin_triggers_enabled,
            pin_require_certified=settings.pin_require_certified,
            pin_max=settings.pin_max,
        )


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Construct via ``Settings.for_env(...)`` or
    :func:`load_settings`."""

    environment: Environment

    # ── D6 human gate ──
    auto_accept_corpus: bool  # dev: True (adversary is sole reviewer); prod: False (PR + owner + CI)

    # ── D7 identity / RLS ──
    single_all_access_identity: bool  # dev: True; prod: False (real user + gateway RLS)

    # ── Analyst: suspect-column enforcement (Analyst §"three points" #1) ──
    hard_block_suspect_columns: bool  # dev/BIRD: True; prod/enterprise: soft-warn + drop reliability tier

    # ── D5: deliver-and-grade semantic failures ──
    # When True, coverage / L3–L5 repair-exhaustion / execution-exhaustion
    # return the last generated SQL with ``semantic_assurance=unverified``
    # instead of a hard refusal. L2 policy + curated refuse-gate stay hard.
    grade_semantic_failures: bool = False

    # ── Schema routing (D15; multi-schema / data-lake serve) ──
    # Candidate shortlist size for the router (embedding similarity, BM25 fallback).
    # Wider raises schema recall at the cost of more per-question context. Only used
    # when the corpus spans >1 schema.
    schema_route_top_k: int = 3
    # When True, an LLM picks exactly ONE schema from the shortlist
    # (D15) and cross-schema join-expansion is skipped — the
    # single-schema-answer regime (e.g. the BIRD data-lake, where every question
    # targets one db_id). When False (default), keep the shortlist and expand along
    # curated cross-schema joins (cross-schema answering).
    schema_route_llm_pick: bool = False
    # Column names per table shown to the LLM picker. Column vocabulary is what
    # separates same-topic sibling schemas whose table descriptions read alike;
    # 0 restores the names-only summary. Capped per table so one wide table cannot
    # dominate the picker context across every candidate.
    schema_pick_max_columns: int = 12

    # ── Analyst prompt shaping (see [analyst] in governed_bi.toml) ──
    # Columns per table in the ANALYST system prompt. Same argument as
    # ``schema_pick_max_columns`` one field up — a wide table would otherwise dominate
    # the context — applied to the prompt that actually writes the SQL, where nothing
    # capped it: ``european_football_2.partido`` contributes 118 column lines today.
    #
    # ``0`` (the default) means NO CAP, i.e. exactly the behaviour every recorded run
    # measured. The default is deliberately not the router's 12: whether capping helps
    # is unsettled. Pooled BIRD rows show EX falling with gold-table width (70.7% under
    # 15 columns -> 44.3% at 40+), but a within-schema median split does not reach
    # significance (17/29 schemas, one-sided sign test p = 0.23), so most of that curve
    # is schema difficulty. This knob exists to run the intervention that would settle
    # it, and a non-zero default would confound the comparison it was added to make.
    # Selection is by relevance with keys and SUSPECT columns never dropped
    # (``analyst/context.py::_select_columns``).
    # TOML: ``[analyst] max_table_columns``.
    analyst_max_table_columns: int = 0
    # When True the ``## Reliability caveats`` block lists DO-NOT-USE identifiers only.
    # Each caveat is currently rendered twice — inline on the column line and again
    # here with the same note text — and on the widest committed schemas the pair is
    # ~half the context block. Off by default for the same comparability reason.
    # TOML: ``[analyst] compact_suspect_caveats``.
    analyst_compact_suspect_caveats: bool = False

    # ── Model seam (see [models] in governed_bi.toml) ──
    models: ModelConfig = field(default_factory=ModelConfig)

    # ── Prompt variants (see [prompts]; governed_bi.prompts is the registry) ──
    # ``stage -> variant id``, e.g. ``{"schema_pick": "v2"}``. Empty (the default)
    # means every stage sends ``v1``, byte-identical to the pre-registry text.
    # Unlike the eval-only routing knobs above this is TOML-visible: a deployment
    # that wants a non-default prompt has no other way to say so, since
    # ``api.stack.build_stack`` reads ``load_settings()`` and nothing else. The
    # eval CLIs layer ``--prompt stage=variant`` on top for one-off experiments.
    prompt_variants: dict[str, str] = field(default_factory=dict)

    # ── Data source (see [datasource]) ──
    datasource: DataSourceConfig = field(default_factory=DataSourceConfig)

    # ── Paths (see [paths]) ──
    corpus_root: str = "corpus"  # repo-root-relative or absolute (D9/D13)

    # ── Serve / API (see [serve]) ──
    can_stream: bool = False  # True when a streaming chat graph is fronted
    # Corpus file-write via POST /corpus/edit. Safe default is False; for_env(dev)
    # opts in to True for local demo when the TOML does not override. Committed
    # governed_bi.toml sets false — enable in governed_bi.local.toml for edits.
    allow_edit: bool = False
    # Env-var *name* holding the shared secret for mutating HTTP routes
    # (X-API-Key or Authorization: Bearer). None / unset = demo mode: reads stay
    # open, writes still require allow_edit. Same secret-name-only pattern as
    # models.api_key_env / datasource.dsn_env.
    serve_api_key_env: str | None = None
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    # ── Conversation checkpointer + portable run log (ADR 0004; see [logging]) ──
    conversation_checkpointer_kind: str = "sqlite"  # sqlite | postgres | memory
    conversation_checkpointer_path: str = "data/checkpoints/conversations.sqlite"
    conversation_checkpointer_dsn_env: str | None = None  # env var name; never inline DSN
    run_log_kind: str = "sqlite"  # sqlite | jsonl | off
    run_log_path: str = "data/logs/runs.sqlite"

    # ── Always-note prompt budget (ADR 0003 H1; see [notes] in TOML) ──
    always_note_global_max: int = 8
    always_note_char_max: int = 2000

    # ── PIN trigger authority (ADR 0003 H2; R7/R8) ──
    # When True, keyword PINs can affect schema shortlist / selected (prod needs
    # certified-only graduation — see pin_require_certified).
    pin_triggers_enabled: bool = False
    pin_require_certified: bool = True  # prod default; dev may set False
    pin_max: int = 3

    # ── Full-content run log (ADR 0004 H11; M5) ──
    log_full_content: bool = False
    log_full_content_ack: bool = False  # required True in prod when log_full_content
    log_row_previews: bool = False  # Tier C; needs log_full_content too
    log_full_content_ttl_days: int = 30

    # ── Eval harness concurrency (see [eval]; docs/measurement.md) ──
    # Serve-loop worker threads for the BIRD eval drivers. 1 = fully serial and
    # byte-identical to the pre-concurrency behaviour (the non-negotiable default).
    # ``eval_serve_workers`` overrides ``eval_workers`` for the per-question serve
    # loop; ``eval_build_workers`` does the same for the per-DB corpus build loop.
    # They are separate because they exhaust different resources — a build worker
    # holds a Postgres connection AND a deep-agent conversation.
    eval_workers: int = 1
    eval_serve_workers: int | None = None
    eval_build_workers: int | None = None

    def serve_worker_count(self) -> int:
        """Effective serve-loop worker count: the ``[eval] serve_workers`` split
        override when set, else the single ``workers`` knob."""
        return (
            self.eval_serve_workers
            if self.eval_serve_workers is not None
            else self.eval_workers
        )

    def build_worker_count(self) -> int:
        """Effective corpus-build worker count.

        Same split-override shape as :meth:`serve_worker_count`. The two are separate
        knobs because they exhaust different resources: the serve loop is bounded by
        Postgres ``max_connections``, while a build worker holds a connection *and* a
        deep-agent conversation, so the sensible ceilings differ.
        """
        return (
            self.eval_build_workers
            if self.eval_build_workers is not None
            else self.eval_workers
        )

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
        serve_api_key_env: str | None = None,
        cors_origins: tuple[str, ...] | None = None,
        conversation_checkpointer_kind: str | None = None,
        conversation_checkpointer_path: str | None = None,
        conversation_checkpointer_dsn_env: str | None = None,
        run_log_kind: str | None = None,
        run_log_path: str | None = None,
        # One carrier rather than five more scalars (see :class:`NoteGovernance`).
        notes: NoteGovernance | None = None,
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
        if serve_api_key_env is not None:
            base["serve_api_key_env"] = serve_api_key_env
        if cors_origins is not None:
            base["cors_origins"] = cors_origins
        if conversation_checkpointer_kind is not None:
            base["conversation_checkpointer_kind"] = conversation_checkpointer_kind
        if conversation_checkpointer_path is not None:
            base["conversation_checkpointer_path"] = conversation_checkpointer_path
        if conversation_checkpointer_dsn_env is not None:
            base["conversation_checkpointer_dsn_env"] = conversation_checkpointer_dsn_env
        if run_log_kind is not None:
            base["run_log_kind"] = run_log_kind
        if run_log_path is not None:
            base["run_log_path"] = run_log_path
        if notes is not None:
            base.update(notes.overrides())
        if env is Environment.dev:
            # Local demo: file-write edits on unless TOML / caller opts out.
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

    def serve_api_key(self) -> str | None:
        """Shared secret for mutating HTTP routes, or None when auth is off.

        Reads ``os.environ[serve_api_key_env]`` when the env-var name is set.
        Empty / missing values are treated as unset (auth not enforced).
        """
        if not self.serve_api_key_env:
            return None
        value = os.environ.get(self.serve_api_key_env)
        return value if value else None


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
    # [serve].api_key_env names the env var; never the secret itself.
    raw_serve_key_env = serve.get("api_key_env")
    if raw_serve_key_env is None:
        serve_api_key_env = None
    else:
        serve_api_key_env = str(raw_serve_key_env).strip() or None
    cors_origins = (
        _cors_origins_from(serve["cors_origins"]) if "cors_origins" in serve else None
    )

    # Optional [logging] table (ADR 0004 checkpointer + portable run log).
    logging_tbl = data.get("logging", {})
    ckpt_kind = logging_tbl.get("conversation_checkpointer_kind")
    ckpt_path = logging_tbl.get("conversation_checkpointer_path")
    ckpt_dsn_env = logging_tbl.get("conversation_checkpointer_dsn_env")
    run_log_kind = logging_tbl.get("run_log_kind")
    run_log_path = logging_tbl.get("run_log_path")

    # Optional [notes] table (ADR 0003 always-note budget + PIN trigger authority).
    # Passed through ``for_env`` rather than a post-hoc ``replace``, because
    # ``for_env`` is what every non-TOML caller uses — the eval drivers included —
    # and a knob only ``load_settings`` can set is a knob no measured run can vary.
    notes = NoteGovernance(**_known_kwargs(NoteGovernance, data.get("notes", {})))

    settings = Settings.for_env(
        env,
        models=models,
        datasource=datasource,
        corpus_root=str(corpus_root),
        can_stream=can_stream,
        allow_edit=allow_edit,
        serve_api_key_env=serve_api_key_env,
        cors_origins=cors_origins,
        conversation_checkpointer_kind=str(ckpt_kind) if ckpt_kind is not None else None,
        conversation_checkpointer_path=str(ckpt_path) if ckpt_path is not None else None,
        conversation_checkpointer_dsn_env=(
            str(ckpt_dsn_env) if ckpt_dsn_env is not None else None
        ),
        run_log_kind=str(run_log_kind) if run_log_kind is not None else None,
        run_log_path=str(run_log_path) if run_log_path is not None else None,
        notes=notes,
    )

    knob_overrides: dict[str, Any] = {}
    for k in (
        "log_full_content",
        "log_full_content_ack",
        "log_row_previews",
        "log_full_content_ttl_days",
    ):
        if k in logging_tbl:
            knob_overrides[k] = logging_tbl[k]

    # Optional [routing] table (D15 data-lake schema routing). These three were
    # reachable only through the eval CLI, so the pooled benchmark ran shortlist@10
    # WITH the LLM pick while every deployment ran the dataclass defaults —
    # shortlist@3, no pick — with no way to configure otherwise. A benchmark result
    # then described a configuration no deployment could run, which makes "this
    # improves the end result" unfalsifiable in the direction that matters: the
    # improvement was measured under routing the product does not have.
    routing_tbl = data.get("routing", {})
    for toml_key, field_name in (
        ("top_k", "schema_route_top_k"),
        ("llm_pick", "schema_route_llm_pick"),
        ("pick_max_columns", "schema_pick_max_columns"),
    ):
        if toml_key in routing_tbl:
            value = routing_tbl[toml_key]
            knob_overrides[field_name] = (
                bool(value) if field_name == "schema_route_llm_pick" else int(value)
            )

    # Optional [analyst] table: prompt-shaping knobs for the SQL-writing prompt.
    # Same reachability lesson as [routing] above — a knob only an eval CLI can set
    # is a knob no deployment can run, so the benchmark ends up describing a
    # configuration the product does not have.
    analyst_tbl = data.get("analyst", {})
    for toml_key, field_name in (
        ("max_table_columns", "analyst_max_table_columns"),
        ("compact_suspect_caveats", "analyst_compact_suspect_caveats"),
    ):
        if toml_key in analyst_tbl:
            value = analyst_tbl[toml_key]
            knob_overrides[field_name] = (
                bool(value)
                if field_name == "analyst_compact_suspect_caveats"
                else int(value)
            )

    # Optional [eval] table (docs/measurement.md). TOML keys are
    # short (``workers`` / ``serve_workers`` / ``build_workers``); they map onto the
    # ``eval_*`` fields on Settings.
    eval_tbl = data.get("eval", {})
    for toml_key, field_name in (
        ("workers", "eval_workers"),
        ("serve_workers", "eval_serve_workers"),
        ("build_workers", "eval_build_workers"),
    ):
        if toml_key in eval_tbl:
            knob_overrides[field_name] = int(eval_tbl[toml_key])

    # Optional [prompts] table: stage = "variant" per registered stage. Validated
    # HERE, through the registry, so a typo fails at startup instead of silently
    # serving v1 while every artifact claims the variant that was asked for.
    prompts_tbl = data.get("prompts", {})
    if prompts_tbl:
        prompt_overrides = {str(k): str(v) for k, v in prompts_tbl.items()}
        try:
            resolve_prompt_variants(prompt_overrides)
        except KeyError as err:
            raise ValueError(
                f"[prompts] in {resolved}: {err.args[0] if err.args else err}"
            ) from err
        knob_overrides["prompt_variants"] = prompt_overrides

    if knob_overrides:
        settings = replace(settings, **knob_overrides)

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
    except (OSError, ValueError):
        # UnicodeDecodeError is a ValueError subclass, not an OSError: a .env
        # saved as cp1252/latin-1 (or with one non-ASCII byte) must stay a no-op
        # per the docstring, not crash `import governed_bi` at import time.
        return {}
    applied: dict[str, str] = {}
    for key, value in parsed.items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
