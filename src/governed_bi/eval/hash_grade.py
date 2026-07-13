"""Score predicted SQL against precomputed BIRD gold result hashes (plan §5b).

Normalisation + SHA-256 are vendored from BIRD-Data-Obfuscation
``pipeline/_db.py`` (``normalise_result`` / ``hash_normalised_result{,_strict}``)
so scores match the reference grader byte-for-byte without importing that
module's ``psycopg2`` dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import numbers
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..gateway import Gateway, Identity


@dataclass(frozen=True)
class GoldHash:
    question_id: str
    hash_lenient: str | None
    hash_strict: str | None
    nrows: int | None = None
    error: str | None = None
    sql_sha256: str | None = None

    @property
    def usable(self) -> bool:
        """False when the precomputed gold hash itself failed / is stale."""
        return self.error is None and bool(self.hash_lenient)


# --------------------------------------------------------------------------- #
# Vendored from BIRD-Data-Obfuscation/pipeline/_db.py (keep in sync)
# --------------------------------------------------------------------------- #


def normalise_result(rows) -> list:
    if rows is None:
        return []

    def coerce(v):
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v).strip().lower()
        if math.isnan(f):
            return "\x00nan"
        if math.isinf(f):
            return "\x00inf" if f > 0 else "\x00-inf"
        return f

    def cell_key(v):
        if v is None:
            return (0, 0.0, "")
        if isinstance(v, float):
            return (1, v, "")
        return (2, 0.0, v)

    normalised = [tuple(coerce(c) for c in row) for row in rows]
    return sorted(normalised, key=lambda row: tuple(cell_key(c) for c in row))


def normalise_result_strict(rows) -> list:
    if rows is None:
        return []

    def scoerce(v):
        if v is None:
            return (0, 0.0, "")
        if isinstance(v, bool):
            return (1, 1.0 if v else 0.0, "")
        if isinstance(v, numbers.Number):
            f = float(v)
            if math.isnan(f):
                return (2, 0.0, "\x00nan")
            if math.isinf(f):
                return (2, 0.0, "\x00inf" if f > 0 else "\x00-inf")
            return (2, f, "")
        return (3, 0.0, str(v).strip())

    return sorted(tuple(scoerce(c) for c in row) for row in rows)


def _canonical_json(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def hash_normalised_result(rows) -> str:
    normalised = normalise_result(rows)
    payload = _canonical_json([list(row) for row in normalised])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_normalised_result_strict(rows) -> str:
    normalised = normalise_result_strict(rows)
    payload = _canonical_json([list(row) for row in normalised])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Loaders + score
# --------------------------------------------------------------------------- #


def load_gold_hashes(
    bird_dir: Path | str,
    *,
    db_id: str,
    dsn_key: str = "rename_decoy",
    split: str = "test",
) -> dict[str, GoldHash]:
    """Load ``gold_result_hashes_rename_decoy.jsonl`` filtered to one db/split."""
    bird_dir = Path(bird_dir)
    path = bird_dir / "eval_dataset" / "gold_result_hashes_rename_decoy.jsonl"
    if not path.exists():
        alt = bird_dir / "artifacts" / "gold_result_hashes_rename_decoy.jsonl"
        path = alt if alt.exists() else path
    if not path.exists():
        raise FileNotFoundError(f"gold hash file not found under {bird_dir}")
    out: dict[str, GoldHash] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("db_id") != db_id:
                continue
            if row.get("dsn_key") and row.get("dsn_key") != dsn_key:
                continue
            if row.get("split") and row.get("split") != split:
                continue
            qid = str(row["question_id"])
            out[qid] = GoldHash(
                question_id=qid,
                hash_lenient=row.get("hash_lenient"),
                hash_strict=row.get("hash_strict"),
                nrows=row.get("nrows"),
                error=row.get("error"),
                sql_sha256=row.get("sql_sha256"),
            )
    return out


def load_trap_columns(bird_dir: Path | str, db_id: str) -> frozenset[str]:
    """Physical ``table.column`` refs for decoy/trap columns (decoy-touch metric)."""
    bird_dir = Path(bird_dir)
    path = bird_dir / "artifacts" / "trap_manifest.json"
    if not path.exists():
        path = bird_dir / "eval_dataset" / "trap_manifest.json"
    if not path.exists():
        return frozenset()
    data = json.loads(path.read_text(encoding="utf-8"))
    refs: set[str] = set()
    for row in data:
        if row.get("db") != db_id:
            continue
        table = row.get("table")
        names = row.get("names") or {}
        col = names.get("rename") or names.get("base") or row.get("source_column")
        if table and col:
            refs.add(f"{table}.{col}")
            refs.add(col)
    tpath = bird_dir / "artifacts" / "trap_table_manifest.json"
    if not tpath.exists():
        tpath = bird_dir / "eval_dataset" / "trap_table_manifest.json"
    if tpath.exists():
        for row in json.loads(tpath.read_text(encoding="utf-8")):
            if row.get("db") != db_id:
                continue
            names = row.get("names") or {}
            table = names.get("rename") or names.get("base") or row.get("source_table")
            for col in row.get("columns") or []:
                if isinstance(col, dict):
                    cname = (col.get("names") or {}).get("rename") or col.get("name")
                else:
                    cname = col
                if table and cname:
                    refs.add(f"{table}.{cname}")
                    refs.add(str(cname))
    return frozenset(refs)


def score_sql_hashes(
    sql: str | None,
    gold: GoldHash | None,
    gateway: "Gateway",
    identity: "Identity",
    bird_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute ``sql`` and compare result hashes to ``gold``. Refusal → both False."""
    del bird_dir  # call-site compatibility; hashing is local
    if not sql:
        return {
            "correct": False,
            "correct_strict": False,
            "error": "refusal",
            "hash_lenient": None,
            "hash_strict": None,
        }
    if gold is None:
        return {
            "correct": False,
            "correct_strict": False,
            "error": "missing_gold_hash",
            "hash_lenient": None,
            "hash_strict": None,
        }
    if not gold.usable:
        return {
            "correct": False,
            "correct_strict": False,
            "error": f"gold_unusable:{gold.error or 'missing_hash'}",
            "hash_lenient": None,
            "hash_strict": None,
        }
    try:
        result = gateway.execute(sql, identity)
        rows = list(result.rows)
    except Exception as err:
        return {
            "correct": False,
            "correct_strict": False,
            "error": str(err),
            "hash_lenient": None,
            "hash_strict": None,
        }
    h_lenient = hash_normalised_result(rows)
    h_strict = hash_normalised_result_strict(rows)
    return {
        "correct": h_lenient == gold.hash_lenient,
        "correct_strict": bool(gold.hash_strict) and h_strict == gold.hash_strict,
        "error": None,
        "hash_lenient": h_lenient,
        "hash_strict": h_strict,
    }


def crosscheck_execution_match(
    pred_sql: str | None,
    gold_sql: str | None,
    gateway: "Gateway",
) -> bool | None:
    """Secondary EX via in-repo ``execution_match`` (gold SQL re-exec).

    Returns ``None`` when either side is missing (not scorable); otherwise the
    set-equality result. Used to sanity-check ``hash_grade`` on the same run.
    """
    if not pred_sql or not gold_sql:
        return None
    from .ex import execution_match

    return execution_match(pred_sql, gold_sql, gateway)


def validate_gold_hashes_live(
    items: list,
    gold_hashes: dict[str, GoldHash],
    gateway: "Gateway",
    identity: "Identity",
    *,
    sample: int = 5,
) -> dict[str, Any]:
    """Re-exec gold SQL for a sample of items; confirm hashes match the file.

    This is the practical stand-in for a full ``grade_offline_eval.py`` handoff:
    it proves our vendored normalizer + live ``pg_rename_decoy`` agree with the
    precomputed ``gold_result_hashes_*.jsonl`` before any arm is scored.
    """
    checked = 0
    matched = 0
    errors: list[str] = []
    for item in items:
        qid = getattr(item, "question_id", None)
        if not qid or str(qid) not in gold_hashes:
            continue
        gold = gold_hashes[str(qid)]
        if not gold.usable or not item.sql:
            continue
        try:
            result = gateway.execute(item.sql, identity)
            rows = list(result.rows)
        except Exception as err:
            errors.append(f"{qid}: exec {err}")
            continue
        h = hash_normalised_result(rows)
        checked += 1
        if h == gold.hash_lenient:
            matched += 1
        else:
            errors.append(f"{qid}: hash mismatch")
        if checked >= sample:
            break
    return {
        "n_checked": checked,
        "n_matched": matched,
        "agree_rate": (matched / checked) if checked else None,
        "errors": errors[:5],
    }
