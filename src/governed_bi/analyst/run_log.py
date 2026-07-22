"""Portable run logging + conversation checkpointer factory (ADR 0004 M2).

Metadata-only by default: the portable record never stores verbatim question,
SQL, or row previews. Ledger copies written here strip ``sql`` / ``result``.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..config import Settings, _repo_root
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

_LEDGER_LOG_KEYS = frozenset(
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


def make_conversation_checkpointer(settings: Settings) -> Any | None:
    """Build a durable (or memory) conversation checkpointer from Settings.

    DSN is read only from the environment (``conversation_checkpointer_dsn_env``).
    Returns ``None`` when kind is unrecognized. Caller owns the saver lifetime
    (Postgres uses a long-lived ``psycopg`` connection, not a short-lived CM).
    """
    kind = (settings.conversation_checkpointer_kind or "sqlite").lower()
    if kind == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    if kind == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = _resolve_path(settings.conversation_checkpointer_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        return saver
    if kind == "postgres":
        env_name = settings.conversation_checkpointer_dsn_env
        if not env_name:
            raise ValueError(
                "conversation_checkpointer_kind=postgres requires "
                "conversation_checkpointer_dsn_env"
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


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return _repo_root() / p


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


def strip_ledger_for_log(ledger: list | None) -> list[dict]:
    """Whitelist safe ledger keys for the portable record (ADR 0004 metadata-only).

    Drops ``sql``, ``result``, and free-text ``reason`` (execute errors and
    guardrail messages can echo question literals, SQL fragments, or PII).
    """
    out: list[dict] = []
    for entry in ledger or []:
        if not isinstance(entry, dict):
            continue
        out.append({k: entry[k] for k in _LEDGER_LOG_KEYS if k in entry})
    return out


def build_metadata_record(answer: Any, *, ctx: FinalizeCtx, provenance: dict) -> dict:
    """Curated metadata-only portable record (no question / SQL / rows)."""
    return {
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
        "governance_ledger": strip_ledger_for_log(provenance.get("governance_ledger")),
    }


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
    except Exception:
        logger.exception("run_log append failed (non-fatal)")


def _upsert_sqlite(path: Path, turn: str, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, sort_keys=True, default=str)
    ts = datetime.now(timezone.utc).isoformat()
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


def finalize_and_log(answer: Any, *, ctx: FinalizeCtx) -> Any:
    """Stamp identical metadata keys onto ``Answer.provenance`` and append the log.

    Safe to call from every terminal outcome. Idempotent portable append keyed by
    ``turn_id``.
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
    "build_metadata_record",
    "count_run_records",
    "estimate_cost_usd",
    "finalize_and_log",
    "load_run_record",
    "make_conversation_checkpointer",
    "new_run_id",
    "strip_ledger_for_log",
    "sum_token_usage",
    "turn_id",
]
