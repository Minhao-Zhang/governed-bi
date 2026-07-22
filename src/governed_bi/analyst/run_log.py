"""Portable run logging + durable checkpointer factory (ADR 0004 M2/M5).

**H11 tiers.** Tier A metadata is always written. Tier B (question / SQL /
answer text) requires ``Settings.log_full_content``. Tier C (ledger row
previews) requires both ``log_full_content`` and ``log_row_previews``.

**Store permissions.** After each successful write, POSIX sets the log file to
``0o600`` and its parent directory to ``0o700``. On win32, ``os.chmod`` cannot
restrict group/other the same way — treat the store as a single-operator local
artifact and do not pretend the bits enforce multi-user isolation.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from ..config import Environment, Settings, _repo_root
from ..provenance import (
    DataSplit,
    Producer,
    corpus_release_hash,
    export_allow,
    new_run_id,
    serve_config_hash,
    turn_id,
)

logger = logging.getLogger("governed_bi.run_log")

# Serialize JSONL read-modify-rewrite so concurrent finalizes cannot drop rows.
_JSONL_LOCK = threading.Lock()

# Provenance keys every terminal Answer must carry after finalize_and_log (L5).
METADATA_PROVENANCE_KEYS: tuple[str, ...] = (
    "turn_id",
    "run_id",
    "thread_id",
    "producer",
    "data_split",
    "export_allow",
    "corpus_release_hash",
    "corpus_pin",
    "serve_config_hash",
    "token_usage",
    "token_sum",
    "cost_est_usd",
    "latency_ms",
    "outcome",
    "model",
    "serve_path",
)

# USD per 1M tokens (input, output). Unknown models → cost_est_usd=None.
_PRICE_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-5.6-luna": (2.0, 8.0),
    "gpt-5.5": (1.25, 10.0),
    "gpt-5.5-mini": (0.25, 2.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "text-embedding-3-small": (0.02, 0.0),
}

_LEDGER_META_KEYS = frozenset(
    {
        "action",
        "verdict",
        "layer",
        "duration_ms",
        "ts",
        "table_id",
        "allowed",
        "licensed_ids",
    }
)

# Tier B/C keys removed by prune_full_content (Tier A kept).
_TIER_BC_TOP_KEYS = frozenset({"question", "sql", "answer", "answer_text", "result"})

# Producer `extra` keys that are safe Tier A metadata and may be merged even when
# full-content is off. This is a WHITELIST: any key not listed is dropped when
# gated, so a new verbatim-carrying `extra` key can never silently leak. Add
# known-safe scalar-metadata keys here as producers need them.
_TIER_A_EXTRA_KEYS = frozenset(
    {"schema", "arm", "batch", "n_tool_calls", "n_steps", "n_questions", "n_assets", "n_errors"}
)


@dataclass(frozen=True)
class FinalizeCtx:
    """Context threaded into :func:`finalize_and_log` for one terminal outcome."""

    settings: Settings
    run_id: str
    thread_id: str
    n_human: int = 1
    producer: Producer = Producer.serve
    data_split: DataSplit = DataSplit.dev
    model: str | None = None
    serve_path: str = "agent"
    token_usage: list | None = None
    t0: float | None = None  # perf_counter at turn start; latency_ms derived
    outcome: str | None = None  # override; else inferred from answer.tier
    append: bool = True
    question: str | None = None  # Tier B when log_full_content


def assert_full_content_policy(settings: Settings) -> None:
    """Fail loud when prod enables full-content without explicit ack (H11).

    Mirrors the single-access identity guard in ``build_stack``.
    """
    if (
        settings.environment is Environment.prod
        and settings.log_full_content
        and not settings.log_full_content_ack
    ):
        raise RuntimeError(
            "log_full_content=True in environment=prod requires "
            "log_full_content_ack=True (ADR 0004 H11). Refuse to enable "
            "verbatim question/SQL/answer logging without an explicit ack."
        )


def make_durable_checkpointer(
    settings: Settings,
    *,
    kind: str | None = None,
    path: str | None = None,
    path_override: str | None = None,
    dsn_env: str | None = None,
) -> Any | None:
    """Build a durable (or memory) checkpointer.

    Reuses the conversation-checkpointer Settings fields unless ``kind`` /
    ``path`` / ``dsn_env`` overrides are passed (clarify / curator use a
    distinct path). ``path_override`` is an alias for ``path``. Returns
    ``None`` when kind is unrecognized. Caller owns the saver lifetime
    (Postgres uses a long-lived ``psycopg`` connection).
    """
    resolved_kind = (kind or settings.conversation_checkpointer_kind or "sqlite").lower()
    if resolved_kind == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    if resolved_kind == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        raw = (
            path
            if path is not None
            else path_override
            if path_override is not None
            else settings.conversation_checkpointer_path
        )
        ckpt_path = _resolve_path(raw)
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        _secure_store_perms(ckpt_path, file_must_exist=False)
        conn = sqlite3.connect(str(ckpt_path), check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        _secure_store_perms(ckpt_path)
        return saver
    if resolved_kind == "postgres":
        env_name = dsn_env if dsn_env is not None else settings.conversation_checkpointer_dsn_env
        if not env_name:
            raise ValueError(
                "conversation_checkpointer_kind=postgres requires "
                "conversation_checkpointer_dsn_env (or dsn_env=...)"
            )
        dsn = os.environ.get(env_name)
        if not dsn:
            raise ValueError(f"checkpoint DSN env var {env_name!r} is unset")
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver

        # Long-lived connection: do NOT use from_conn_string()'s context manager
        # (exiting/GC-finalizing it closes the connection).
        conn = psycopg.connect(dsn, autocommit=True)
        saver = PostgresSaver(conn)
        saver.setup()
        return saver
    return None


def make_conversation_checkpointer(settings: Settings) -> Any | None:
    """Build the outer-chat conversation checkpointer from Settings."""
    return make_durable_checkpointer(settings)


def make_clarify_checkpointer(settings: Settings) -> Any | None:
    """Inner HITL clarify saver (H10): distinct path, or InMemory when kind=memory."""
    kind = (settings.conversation_checkpointer_kind or "sqlite").lower()
    if kind == "memory":
        return make_durable_checkpointer(settings, kind="memory")
    base = Path(settings.conversation_checkpointer_path)
    clarify_path = str(base.with_name(f"clarify{base.suffix or '.sqlite'}"))
    return make_durable_checkpointer(settings, path=clarify_path)


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return _repo_root() / p


def _secure_store_perms(path: Path, *, file_must_exist: bool = True) -> None:
    """POSIX: parent ``0o700``, file ``0o600``. Win32: no-op (documented caveat).

    On Windows, ``os.chmod`` only toggles the read-only bit and cannot restrict
    group/other — single-operator local store only; do not pretend otherwise.
    """
    if os.name == "nt":
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        if path.exists() or not file_must_exist:
            if path.exists():
                os.chmod(path, 0o600)
    except OSError:
        logger.debug("run_log chmod skipped for %s", path, exc_info=True)


def estimate_cost_usd(model: str | None, token_sum: Mapping[str, int] | None) -> float | None:
    """Estimate USD cost from a crude price table; None when unknown."""
    if not model or not token_sum:
        return None
    prices = _PRICE_PER_1M.get(model)
    if prices is None:
        # Prefix match (e.g. dated model ids).
        for key, val in _PRICE_PER_1M.items():
            if model.startswith(key):
                prices = val
                break
    if prices is None:
        return None
    pin, pout = prices
    inp = int(token_sum.get("input_tokens") or token_sum.get("prompt_tokens") or 0)
    out = int(token_sum.get("output_tokens") or token_sum.get("completion_tokens") or 0)
    return round((inp * pin + out * pout) / 1_000_000.0, 8)


def sum_token_usage(entries: list | None) -> dict[str, int]:
    """Sum input/output/total tokens across usage snapshots."""
    inp = out = total = 0
    for entry in entries or []:
        usage = entry.get("usage_metadata") if isinstance(entry, dict) else None
        if not isinstance(usage, Mapping):
            continue
        i = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        o = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        t = int(usage.get("total_tokens") or (i + o))
        inp += i
        out += o
        total += t
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total}


def usage_callback_entries(usage_cb: Any, *, source: str = "deep_agent") -> list[dict]:
    """Convert a ``UsageMetadataCallbackHandler`` into token_usage entries."""
    meta = getattr(usage_cb, "usage_metadata", None) or {}
    out: list[dict] = []
    for model_name, usage in meta.items():
        entry: dict[str, Any] = {"source": source, "usage_metadata": dict(usage)}
        if model_name:
            entry["model"] = model_name
        out.append(entry)
    return out


def strip_ledger_for_log(
    ledger: list | None,
    *,
    full_content: bool = False,
    row_previews: bool = False,
) -> list[dict]:
    """Strip ledger entries for the portable record (ADR 0004 H11).

    When ``full_content`` is False (default): whitelist Tier A keys only —
    drops ``sql``, ``result``, and free-text ``reason`` (execute errors and
    guardrail messages can echo question literals, SQL fragments, or PII).

    When ``full_content`` is True: keep ``sql``; keep ``result`` only when
    ``row_previews`` is also True (Tier C). Still drops ``reason``.
    """
    out: list[dict] = []
    for entry in ledger or []:
        if not isinstance(entry, dict):
            continue
        cleaned = {k: entry[k] for k in _LEDGER_META_KEYS if k in entry}
        if full_content and "sql" in entry:
            cleaned["sql"] = entry["sql"]
        if full_content and row_previews and "result" in entry:
            cleaned["result"] = entry["result"]
        out.append(cleaned)
    return out


def build_metadata_record(answer: Any, *, ctx: FinalizeCtx, provenance: dict) -> dict:
    """Build the portable record; Tier B/C only when Settings gates allow."""
    settings = ctx.settings
    full = bool(settings.log_full_content)
    previews = bool(settings.log_row_previews) and full
    rec: dict[str, Any] = {
        "turn_id": provenance.get("turn_id"),
        "run_id": provenance.get("run_id"),
        "thread_id": provenance.get("thread_id"),
        "producer": provenance.get("producer"),
        "data_split": provenance.get("data_split"),
        "export_allow": provenance.get("export_allow"),
        "corpus_release_hash": provenance.get("corpus_release_hash"),
        "corpus_pin": provenance.get("corpus_pin"),
        "serve_config_hash": provenance.get("serve_config_hash"),
        "token_usage": provenance.get("token_usage") or [],
        "token_sum": provenance.get("token_sum"),
        "cost_est_usd": provenance.get("cost_est_usd"),
        "latency_ms": provenance.get("latency_ms"),
        "outcome": provenance.get("outcome"),
        "model": provenance.get("model"),
        "serve_path": provenance.get("serve_path"),
        "tier": getattr(getattr(answer, "tier", None), "value", None),
        "semantic_assurance": getattr(
            getattr(answer, "semantic_assurance", None), "value", None
        ),
        "safety_clearance": getattr(answer, "safety_clearance", None),
        "tables_used": provenance.get("tables_used"),
        "routed_schemas": provenance.get("routed_schemas"),
        "governance_ledger": strip_ledger_for_log(
            provenance.get("governance_ledger"),
            full_content=full,
            row_previews=previews,
        ),
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    if full:
        # Tier B
        q = ctx.question if ctx.question is not None else provenance.get("question")
        if q is not None:
            rec["question"] = q
        sql = getattr(answer, "sql", None)
        if sql is None:
            sql = provenance.get("sql")
        if sql is not None:
            rec["sql"] = sql
        text = getattr(answer, "text", None)
        if text is None:
            text = provenance.get("answer") or provenance.get("answer_text")
        if text is not None:
            rec["answer"] = text
    return rec


def append_run_record(record: Mapping[str, Any], settings: Settings) -> None:
    """At-least-once idempotent append keyed by ``turn_id``. Never raises."""
    kind = (settings.run_log_kind or "sqlite").lower()
    if kind == "off":
        return
    turn = record.get("turn_id")
    if not turn:
        logger.warning("run_log skip: missing turn_id")
        return
    try:
        path = _resolve_path(settings.run_log_path)
        if kind == "jsonl":
            _upsert_jsonl(path, str(turn), dict(record))
        else:
            _upsert_sqlite(path, str(turn), dict(record))
        _secure_store_perms(path)
    except Exception:
        logger.exception("run_log append failed (non-fatal)")


def _upsert_sqlite(path: Path, turn: str, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _secure_store_perms(path, file_must_exist=False)
    payload = json.dumps(record, sort_keys=True, default=str)
    ts = record.get("logged_at") or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_log (
                turn_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO run_log (turn_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (turn, payload, ts),
        )
        conn.commit()


def _upsert_jsonl(path: Path, turn: str, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _secure_store_perms(path, file_must_exist=False)
    with _JSONL_LOCK:
        rows: dict[str, dict] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = obj.get("turn_id")
                if tid:
                    rows[str(tid)] = obj
        rows[turn] = record
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for tid in sorted(rows):
                fh.write(json.dumps(rows[tid], sort_keys=True, default=str) + "\n")
        tmp.replace(path)


def _null_tier_bc(record: dict) -> dict:
    """Drop Tier B/C keys from a portable record; strip ledger sql/result."""
    cleaned = {k: v for k, v in record.items() if k not in _TIER_BC_TOP_KEYS}
    ledger = cleaned.get("governance_ledger")
    if isinstance(ledger, list):
        cleaned["governance_ledger"] = strip_ledger_for_log(
            ledger, full_content=False, row_previews=False
        )
    return cleaned


def _parse_logged_at(record: Mapping[str, Any], *, fallback: str | None = None) -> datetime | None:
    raw = record.get("logged_at") or fallback
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def prune_full_content(
    settings: Settings,
    *,
    ttl_days: int | None = None,
) -> int:
    """Null Tier B/C content older than TTL; keep Tier A metadata. Returns pruned count."""
    days = settings.log_full_content_ttl_days if ttl_days is None else ttl_days
    if days < 0:
        return 0
    kind = (settings.run_log_kind or "sqlite").lower()
    if kind == "off":
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    path = _resolve_path(settings.run_log_path)
    if kind == "jsonl":
        return _prune_jsonl(path, cutoff)
    return _prune_sqlite(path, cutoff)


def _prune_sqlite(path: Path, cutoff: datetime) -> int:
    if not path.is_file():
        return 0
    pruned = 0
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_log (
                turn_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        rows = list(conn.execute("SELECT turn_id, payload, updated_at FROM run_log"))
        for turn, payload, updated_at in rows:
            try:
                record = json.loads(payload)
            except json.JSONDecodeError:
                continue
            logged = _parse_logged_at(record, fallback=updated_at)
            if logged is None or logged >= cutoff:
                continue
            if not any(k in record for k in _TIER_BC_TOP_KEYS) and not any(
                isinstance(e, dict) and ("sql" in e or "result" in e)
                for e in (record.get("governance_ledger") or [])
            ):
                continue
            cleaned = _null_tier_bc(record)
            cleaned["logged_at"] = record.get("logged_at") or updated_at
            conn.execute(
                "UPDATE run_log SET payload = ?, updated_at = ? WHERE turn_id = ?",
                (
                    json.dumps(cleaned, sort_keys=True, default=str),
                    cleaned["logged_at"],
                    turn,
                ),
            )
            pruned += 1
        conn.commit()
    if pruned:
        _secure_store_perms(path)
    return pruned


def _prune_jsonl(path: Path, cutoff: datetime) -> int:
    if not path.is_file():
        return 0
    pruned = 0
    with _JSONL_LOCK:
        rows: dict[str, dict] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = obj.get("turn_id")
            if not tid:
                continue
            logged = _parse_logged_at(obj)
            if logged is not None and logged < cutoff and (
                any(k in obj for k in _TIER_BC_TOP_KEYS)
                or any(
                    isinstance(e, dict) and ("sql" in e or "result" in e)
                    for e in (obj.get("governance_ledger") or [])
                )
            ):
                obj = _null_tier_bc(obj)
                pruned += 1
            rows[str(tid)] = obj
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for tid in sorted(rows):
                fh.write(json.dumps(rows[tid], sort_keys=True, default=str) + "\n")
        tmp.replace(path)
    if pruned:
        _secure_store_perms(path)
    return pruned


def load_run_record(turn: str, settings: Settings) -> dict | None:
    """Read one portable record by turn_id (test helper)."""
    kind = (settings.run_log_kind or "sqlite").lower()
    if kind == "off":
        return None
    path = _resolve_path(settings.run_log_path)
    if kind == "jsonl":
        if not path.is_file():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("turn_id") == turn:
                return obj
        return None
    if not path.is_file():
        return None
    with sqlite3.connect(str(path)) as conn:
        row = conn.execute(
            "SELECT payload FROM run_log WHERE turn_id = ?", (turn,)
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def count_run_records(settings: Settings) -> int:
    """Row count in the portable log (test helper)."""
    kind = (settings.run_log_kind or "sqlite").lower()
    if kind == "off":
        return 0
    path = _resolve_path(settings.run_log_path)
    if kind == "jsonl":
        if not path.is_file():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if not path.is_file():
        return 0
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_log (
                turn_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        (n,) = conn.execute("SELECT COUNT(*) FROM run_log").fetchone()
    return int(n)


def emit_run_record(
    *,
    settings: Settings,
    producer: Producer | str,
    run_id: str,
    thread_id: str,
    outcome: str,
    n_human: int = 1,
    model: str | None = None,
    token_usage: list | None = None,
    t0: float | None = None,
    question: str | None = None,
    sql: str | None = None,
    answer_text: str | None = None,
    error: str | None = None,
    data_split: DataSplit | str = DataSplit.dev,
    serve_path: str = "deep_agent",
    extra: Mapping[str, Any] | None = None,
) -> dict:
    """Shared portable-record emit for serve / curator / sme. Never raises.

    One mechanism, three producers. Tier B fields are included only when
    ``settings.log_full_content`` is True. ``error`` is Tier A (always kept).

    Fails loud (before the non-raising body) when prod enables full content
    without an explicit ack, so curator / SME producers honor the same H11
    guard as the serve ``build_stack`` path.
    """
    assert_full_content_policy(settings)
    try:
        return _emit_run_record_inner(
            settings=settings,
            producer=producer,
            run_id=run_id,
            thread_id=thread_id,
            outcome=outcome,
            n_human=n_human,
            model=model,
            token_usage=token_usage,
            t0=t0,
            question=question,
            sql=sql,
            answer_text=answer_text,
            error=error,
            data_split=data_split,
            serve_path=serve_path,
            extra=extra,
        )
    except Exception:
        logger.exception("emit_run_record failed (non-fatal)")
        return {
            "turn_id": turn_id(thread_id, n_human),
            "run_id": run_id,
            "thread_id": thread_id,
            "producer": (
                producer.value if isinstance(producer, Producer) else str(producer)
            ),
            "outcome": outcome,
        }


def _emit_run_record_inner(
    *,
    settings: Settings,
    producer: Producer | str,
    run_id: str,
    thread_id: str,
    outcome: str,
    n_human: int,
    model: str | None,
    token_usage: list | None,
    t0: float | None,
    question: str | None,
    sql: str | None,
    answer_text: str | None,
    error: str | None,
    data_split: DataSplit | str,
    serve_path: str,
    extra: Mapping[str, Any] | None,
) -> dict:
    prod = producer.value if isinstance(producer, Producer) else str(producer)
    split = data_split if isinstance(data_split, DataSplit) else DataSplit(data_split)
    usage = list(token_usage or [])
    # Normalize a bare usage_metadata dict (curator sometimes packs the whole map).
    normalized: list[dict] = []
    for entry in usage:
        if not isinstance(entry, dict):
            continue
        um = entry.get("usage_metadata")
        if isinstance(um, dict) and um and not any(
            k in um for k in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens")
        ):
            # usage_cb.usage_metadata is {model: UsageMetadata} — expand.
            for model_name, model_usage in um.items():
                if isinstance(model_usage, Mapping):
                    normalized.append(
                        {
                            "source": entry.get("source", "deep_agent"),
                            "model": model_name,
                            "usage_metadata": dict(model_usage),
                        }
                    )
            continue
        normalized.append(entry)
    usage = normalized
    token_sum = sum_token_usage(usage)
    model_name = model or settings.models.llm_model
    latency_ms = (
        int((time.perf_counter() - t0) * 1000) if t0 is not None else None
    )
    tid = turn_id(thread_id, n_human)
    rec: dict[str, Any] = {
        "turn_id": tid,
        "run_id": run_id or new_run_id(),
        "thread_id": thread_id,
        "producer": prod,
        "data_split": split.value,
        "export_allow": export_allow(split),
        "corpus_release_hash": corpus_release_hash(),
        "corpus_pin": settings.datasource.corpus_pin,
        "serve_config_hash": serve_config_hash(settings),
        "token_usage": usage,
        "token_sum": token_sum,
        "cost_est_usd": estimate_cost_usd(model_name, token_sum),
        "latency_ms": latency_ms,
        "outcome": outcome,
        "model": model_name,
        "serve_path": serve_path,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    if error is not None:
        if settings.log_full_content:
            # Tier B: full message + traceback (truncate huge tracebacks).
            rec["error"] = error if len(error) <= 4000 else error[:4000] + "…[truncated]"
        else:
            # Metadata-only: the exception message / traceback can echo the
            # question, generated SQL, or row values (the same risk the ledger
            # `reason` strip guards against), so keep only the exception type
            # prefix. Full text requires log_full_content.
            etype = error.split("\n", 1)[0].split(":", 1)[0].strip()
            rec["error"] = etype[:120] or "error"
    if settings.log_full_content:
        if question is not None:
            rec["question"] = question
        if sql is not None:
            rec["sql"] = sql
        if answer_text is not None:
            rec["answer"] = answer_text
    if extra:
        # WHITELIST (not denylist): when gated off, merge only known-safe Tier A
        # keys so an arbitrary verbatim-carrying `extra` key can never leak.
        for k, v in extra.items():
            if settings.log_full_content or k in _TIER_A_EXTRA_KEYS:
                rec[k] = v
    append_run_record(rec, settings)
    return rec


def finalize_and_log(answer: Any, *, ctx: FinalizeCtx) -> Any:
    """Stamp identical metadata keys onto ``Answer.provenance`` and append the log.

    Safe to call from every terminal outcome. Idempotent portable append keyed by
    ``turn_id``. Ledger strip / Tier B gates applied at append time.
    """
    from .answer import ReliabilityTier  # local: avoid import cycle at module load

    settings = ctx.settings
    tid = turn_id(ctx.thread_id, ctx.n_human)
    producer = ctx.producer
    data_split = ctx.data_split
    usage = list(ctx.token_usage or [])
    token_sum = sum_token_usage(usage)
    cost = estimate_cost_usd(ctx.model, token_sum)
    latency_ms = (
        int((time.perf_counter() - ctx.t0) * 1000) if ctx.t0 is not None else None
    )
    if ctx.outcome is not None:
        outcome = ctx.outcome
    elif getattr(answer, "tier", None) is ReliabilityTier.refused:
        outcome = "refuse"
    else:
        outcome = "finalize"

    base = dict(getattr(answer, "provenance", None) or {})
    meta = {
        "turn_id": tid,
        "run_id": ctx.run_id or base.get("run_id") or new_run_id(),
        "thread_id": ctx.thread_id,
        "producer": producer.value if isinstance(producer, Producer) else producer,
        "data_split": data_split.value if isinstance(data_split, DataSplit) else data_split,
        "export_allow": export_allow(data_split),
        "corpus_release_hash": corpus_release_hash(),
        "corpus_pin": settings.datasource.corpus_pin,
        "serve_config_hash": serve_config_hash(settings),
        "token_usage": usage,
        "token_sum": token_sum,
        "cost_est_usd": cost,
        "latency_ms": latency_ms,
        "outcome": outcome,
        "model": ctx.model or settings.models.llm_model,
        "serve_path": ctx.serve_path,
    }
    base.update(meta)

    stamped = replace(answer, provenance=base)
    if ctx.append:
        append_run_record(build_metadata_record(stamped, ctx=ctx, provenance=base), settings)
    return stamped


def amend_run_tokens(
    answer: Any,
    *,
    settings: Settings,
    extra_usage: list | None,
    model: str | None = None,
) -> Any:
    """Fold narrator (or other post-final) usage into provenance and re-UPSERT."""
    if not extra_usage:
        return answer
    prov = dict(getattr(answer, "provenance", None) or {})
    usage = list(prov.get("token_usage") or []) + list(extra_usage)
    token_sum = sum_token_usage(usage)
    model_name = model or prov.get("model")
    prov["token_usage"] = usage
    prov["token_sum"] = token_sum
    prov["cost_est_usd"] = estimate_cost_usd(model_name, token_sum)
    stamped = replace(answer, provenance=prov)
    # Rebuild a minimal ctx for append from already-stamped keys.
    ctx = FinalizeCtx(
        settings=settings,
        run_id=str(prov.get("run_id") or new_run_id()),
        thread_id=str(prov.get("thread_id") or "default"),
        n_human=_n_human_from_turn(prov.get("turn_id")),
        model=model_name,
        serve_path=str(prov.get("serve_path") or "agent"),
        token_usage=usage,
        outcome=prov.get("outcome"),
        append=True,
        question=prov.get("question"),
    )
    # Force the already-known turn_id by keeping provenance keys.
    append_run_record(build_metadata_record(stamped, ctx=ctx, provenance=prov), settings)
    return stamped


def _n_human_from_turn(turn: Any) -> int:
    if not isinstance(turn, str) or ":" not in turn:
        return 1
    try:
        return int(turn.rsplit(":", 1)[-1])
    except ValueError:
        return 1


# Re-export helpers callers often need alongside finalize.
__all__ = [
    "FinalizeCtx",
    "METADATA_PROVENANCE_KEYS",
    "amend_run_tokens",
    "append_run_record",
    "assert_full_content_policy",
    "build_metadata_record",
    "count_run_records",
    "emit_run_record",
    "estimate_cost_usd",
    "finalize_and_log",
    "load_run_record",
    "make_clarify_checkpointer",
    "make_conversation_checkpointer",
    "make_durable_checkpointer",
    "new_run_id",
    "prune_full_content",
    "strip_ledger_for_log",
    "sum_token_usage",
    "turn_id",
    "usage_callback_entries",
]
