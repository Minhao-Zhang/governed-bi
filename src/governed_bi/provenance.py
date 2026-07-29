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
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .config import _repo_root
from .prompts import prompt_set_hash

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

    ``prompt_set_hash`` folds in the prompt *text* every stage will send, not the
    variant ids alone, so editing a prompt in place changes this digest. A fixed
    field list is exactly how prompt identity went unhashed in the first place.

    ``routing_knobs`` values must be JSON-native (str/int/float/bool/None/list/dict).
    Non-JSON types raise ``TypeError`` so the digest never depends on ``repr``.
    """
    payload: dict[str, Any] = {
        "environment": settings.environment.value,
        "prompt_set_hash": prompt_set_hash(settings.prompt_variants),
        "auto_accept_corpus": settings.auto_accept_corpus,
        "schema_route_top_k": settings.schema_route_top_k,
        "schema_route_llm_pick": settings.schema_route_llm_pick,
        "schema_pick_max_columns": settings.schema_pick_max_columns,
        "hard_block_suspect_columns": settings.hard_block_suspect_columns,
        "grade_semantic_failures": settings.grade_semantic_failures,
        # Note governance (ADR 0003): these decide which notes reach the model and
        # under what authority, so two runs that differ here are two configurations.
        # They were absent while eight dead memory/cache knobs were hashed, which
        # made flipping note-pinning produce an identical digest.
        "pin_triggers_enabled": settings.pin_triggers_enabled,
        "pin_require_certified": settings.pin_require_certified,
        "pin_max": settings.pin_max,
        "always_note_global_max": settings.always_note_global_max,
        "always_note_char_max": settings.always_note_char_max,
    }
    if routing_knobs:
        payload["routing_knobs"] = dict(routing_knobs)
    # No default=str: non-JSON-native knobs must fail loudly, not hash via repr.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_SEP = bytes([0])  # path/payload separator so two files cannot concatenate ambiguously

#: What :func:`corpus_content_hash` returns when there was no corpus to hash. Named so
#: the comparability gate can refuse it instead of matching it against itself.
CORPUS_HASH_UNKNOWN = "unknown"


def corpus_content_hash(roots: "Sequence[Path | str]") -> str:
    """Content digest of one or more corpus trees — the treatment's actual identity.

    ``corpus_release_hash`` reads ``.git/HEAD`` of the **code** repo, so it moves when
    the engine changes and stays put when the corpus does. The corpus is the
    independent variable of every experiment in this repo, and nothing hashed it
    (AUDIT E5): two runs over different curator draws compared as if comparable.

    Path-relative and sorted, so an absolute staging path cannot leak into the digest
    and two byte-identical trees in different directories hash the same. Returns
    ``"unknown"`` for an absent tree rather than raising — a missing corpus is a fact
    for the ledger to record, not an exception at manifest time.

    ``"unknown"`` is NOT a hash and must never be compared as one. It equals itself, so
    two runs over two *different* missing corpora read as identical on the one field
    whose whole job is being the treatment's identity (AUDIT E5) — ``index.comparable``
    therefore treats the sentinel as un-comparable rather than as a match.

    A file that exists and cannot be READ is a third case, distinct from both: skipping
    it silently made a corpus with unreadable content hash identically to one that was
    never written. Those files are named in the digest without their bytes, so the
    digest still differs from a clean tree and the caller is told on stderr.
    """
    h = hashlib.sha256()
    seen_any = False
    unreadable: list[str] = []
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.yaml")) + sorted(base.rglob("*.md")):
            rel = str(path.relative_to(base)).replace("\\", "/")
            try:
                payload = path.read_bytes()
            except OSError as err:
                unreadable.append(f"{rel}: {err}")
                seen_any = True
                h.update(rel.encode())
                h.update(_SEP)
                h.update(b"<unreadable>")
                continue
            seen_any = True
            h.update(rel.encode())
            h.update(_SEP)
            h.update(payload)
    if unreadable:
        print(
            f"*** WARNING: corpus_content_hash could not read {len(unreadable)} file(s); "
            f"they are counted by name only: {', '.join(unreadable[:5])} ***"
        )
    return h.hexdigest()[:16] if seen_any else CORPUS_HASH_UNKNOWN


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
