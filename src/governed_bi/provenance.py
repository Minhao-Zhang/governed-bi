"""Stable turn/run ids, config hashes, and producer enums for run logging.

Dependency-free shared foundation (ADR 0003 + ADR 0004 X1): stdlib +
:mod:`governed_bi.config` only. Call sites wire this in later milestones;
this module must stay importable from analyst / corpus / curator without cycles.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .config import _repo_root

if TYPE_CHECKING:
    from .config import Settings


class Producer(str, Enum):
    """Who emitted a portable run / turn record."""

    serve = "serve"
    curator = "curator"
    sme = "sme"
    eval = "eval"


class DataSplit(str, Enum):
    """Eval / deployment split stamp on a portable record."""

    train = "train"
    dev = "dev"
    test = "test"
    holdout = "holdout"
    prod = "prod"


def turn_id(thread_id: str, n_human: int) -> str:
    """Stable per-turn id: ``{thread_id}:{n_human}``.

    Matches the serve chat graph's ``clarify_thread`` formula
    (``api/graph_app.py``), so resume and logging share one key.
    """
    return f"{thread_id}:{n_human}"


def new_run_id() -> str:
    """Fresh opaque id for one invoke / graph run."""
    return uuid.uuid4().hex


def export_allow(data_split: DataSplit | str) -> bool:
    """Whether a record from this split may leave the operator boundary.

    Holdout is never exportable (simple policy stub for later portable records).
    Accepts the enum or a plain string so a JSON-reloaded ``\"holdout\"`` still
    fails closed (``!=`` not ``is not``).
    """
    return data_split != DataSplit.holdout


def serve_config_hash(
    settings: Settings,
    routing_knobs: Mapping[str, Any] | None = None,
) -> str:
    """SHA-256 of the curated serve knobs that change governance / routing / memory.

    Hashes the Settings fields listed below (plus optional ``routing_knobs``).
    Two runs that differ only on those fields get different digests; fields not
    in this set (e.g. model names, paths, CORS) are intentionally out of scope.

    ``routing_knobs`` values must be JSON-native (str/int/float/bool/None/list/dict).
    Non-JSON types raise ``TypeError`` so the digest never depends on ``repr``.
    """
    payload: dict[str, Any] = {
        "environment": settings.environment.value,
        "auto_accept_corpus": settings.auto_accept_corpus,
        "working_memory": settings.working_memory,
        "episodic_memory": settings.episodic_memory,
        "correction_memory": settings.correction_memory,
        "schema_route_top_k": settings.schema_route_top_k,
        "schema_route_llm_pick": settings.schema_route_llm_pick,
        "hard_block_suspect_columns": settings.hard_block_suspect_columns,
        "grade_semantic_failures": settings.grade_semantic_failures,
        "cache_hit_cosine_gate": settings.cache_hit_cosine_gate,
        "few_shot_recall_cosine_gate": settings.few_shot_recall_cosine_gate,
        "few_shot_recall_confidence_gate": settings.few_shot_recall_confidence_gate,
        "few_shot_recall_max_fail_count": settings.few_shot_recall_max_fail_count,
        "sql_cache_ttl_minutes": settings.sql_cache_ttl_minutes,
    }
    if routing_knobs:
        payload["routing_knobs"] = dict(routing_knobs)
    # No default=str: non-JSON-native knobs must fail loudly, not hash via repr.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def corpus_release_hash(*, repo_root: Path | None = None) -> str:
    """Interim corpus-release identity: git HEAD SHA (D11 deferred).

    Reads ``.git/HEAD`` (and a loose/packed ref) under ``repo_root`` without
    ``subprocess``. Returns ``\"unknown\"`` when git metadata is missing or
    unreadable — never raises.
    """
    root = repo_root if repo_root is not None else _repo_root()
    try:
        head_path = root / ".git" / "HEAD"
        if not head_path.is_file():
            return "unknown"
        head = head_path.read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head[len("ref:") :].strip()
            ref_path = root / ".git" / ref
            if ref_path.is_file():
                return ref_path.read_text(encoding="utf-8").strip() or "unknown"
            # Packed refs fallback.
            packed = root / ".git" / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("^"):
                        continue
                    sha, _, name = line.partition(" ")
                    if name.strip() == ref and len(sha) >= 40:
                        return sha.strip()
            return "unknown"
        # Detached HEAD: bare SHA.
        return head if len(head) >= 40 else "unknown"
    except (OSError, ValueError):
        # UnicodeDecodeError is a ValueError subclass — corrupt/binary refs.
        return "unknown"
