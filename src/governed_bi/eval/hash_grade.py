"""Score predicted SQL against precomputed BIRD gold result hashes (plan §5b).

Normalisation + SHA-256 are vendored from BIRD-Data-Obfuscation
``pipeline/_db.py`` (``normalise_result`` / ``hash_normalised_result{,_strict}``)
so scores match the reference grader byte-for-byte without importing that
module's ``psycopg2`` dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import numbers
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bird_loader import load_rename_map, manifest_path

if TYPE_CHECKING:
    from ..gateway import Gateway, Identity

logger = logging.getLogger("governed_bi.eval")


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


#: Exception type names that mean the *grader's* connection / timeout failed, not
#: that the model's SQL was wrong. Walked via ``__mro__`` so psycopg subclasses
#: (``QueryCanceled``, ``AdminShutdown``, …) match without listing every leaf.
#:
#: ``OperationalError`` is deliberately absent: sqlite wraps "no such column" in
#: it, and treating those as infrastructure would hide wrong answers as crashes.
_INFRA_EXC_NAMES: frozenset[str] = frozenset(
    {
        "QueryCanceled",
        "QueryCanceledError",
        "TimeoutError",
        "CancelledError",
        "InterfaceError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "BrokenPipeError",
    }
)

#: Message fragments that mark infrastructure failure even when the exception type
#: is the ambiguous ``OperationalError`` / ``DatabaseError``.
_INFRA_MSG_MARKERS: tuple[str, ...] = (
    "server closed the connection",
    "connection not open",
    "connection already closed",
    "could not connect",
    "connection timed out",
    "canceling statement due to statement timeout",
    "cancelling statement due to statement timeout",
    "statement timeout",
    "lock wait timeout",
    "ssl connection has been closed",
    "consuming input failed",
)


def is_infrastructure_error(err: BaseException) -> bool:
    """True when ``err`` is a harness / DB-infra failure, not a bad model statement.

    Infrastructure failures must not land in the accuracy denominator as ordinary
    wrong answers (audit E4): they are crashes that block quotability.
    """
    for cls in type(err).__mro__:
        if cls.__name__ in _INFRA_EXC_NAMES:
            return True
    msg = str(err).lower()
    return any(marker in msg for marker in _INFRA_MSG_MARKERS)


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


class TrapColumns(frozenset):
    """Decoy ``table.column`` refs for one db, plus whether a manifest existed.

    ``manifest_present is False`` is the fact a plain ``frozenset`` could not carry:
    a missing ``trap_manifest.json`` and a genuinely trap-free db both produced an
    empty set, so ``decoy_touch_rate`` printed a confident ``0.0`` either way.
    Subclassing keeps every existing set operation working — a union with the
    corpus-derived suspects still yields a plain ``frozenset`` — while a caller that
    wants to report "not measured" instead of "measured as zero" reads the attribute.
    """

    manifest_present: bool

    def __new__(cls, refs=(), *, manifest_present: bool) -> "TrapColumns":
        self = super().__new__(cls, refs)
        self.manifest_present = manifest_present
        return self


def load_trap_columns(bird_dir: Path | str, db_id: str) -> TrapColumns:
    """Physical ``table.column`` refs for decoy/trap columns (decoy-touch metric).

    Refs are schema-qualified only: a bare column name over-counts, because a
    legitimate column sharing a decoy's name in another table then reads as a decoy
    touch (C6). Each ref is emitted under **both** table spellings, because the
    manifest keys tables by their pre-rename BIRD name while the graded
    ``rename_decoy`` database serves the renamed one — matching the manifest's
    spelling alone would read decoy-touch as zero on every renamed db, which is the
    same silent-zero failure qualified matching is meant to remove.
    """
    bird_dir = Path(bird_dir)
    path = manifest_path(bird_dir, "trap_manifest.json")
    if path is None:
        logger.warning(
            "trap_manifest.json not found under %s (checked artifacts/ and "
            "eval_dataset/); decoy-touch is NOT MEASURED for db %r — read "
            "manifest_present, not the rate, before calling this db trap-free",
            bird_dir,
            db_id,
        )
        return TrapColumns(manifest_present=False)
    rename = load_rename_map(bird_dir, db_id)
    refs: set[str] = set()

    def _add(table: Any, col: Any, *, translate: bool) -> None:
        if not table or not col:
            return
        refs.add(f"{table}.{col}")
        if translate and rename.get(str(table)):
            refs.add(f"{rename[str(table)]}.{col}")

    for row in json.loads(path.read_text(encoding="utf-8")):
        if row.get("db") != db_id:
            continue
        names = row.get("names") or {}
        _add(
            row.get("table"),
            names.get("rename") or names.get("base") or row.get("source_column"),
            translate=True,
        )

    tpath = manifest_path(bird_dir, "trap_table_manifest.json")
    if tpath is not None:
        for row in json.loads(tpath.read_text(encoding="utf-8")):
            if row.get("db") != db_id:
                continue
            names = row.get("names") or {}
            # A decoy *table* names itself and its columns per variant, under
            # ``names.<variant>.{table,columns}``. The sibling ``columns`` list holds
            # the pre-decoy source columns and carries no physical name at all, so
            # reading it yields nothing — the whole decoy-table manifest used to
            # contribute zero refs. The variant's table name is already physical, so
            # it is not run through the rename map.
            variant = names.get("rename") or names.get("base") or {}
            table = variant.get("table") or row.get("source_table")
            for col in variant.get("columns") or []:
                _add(table, col, translate=False)
    return TrapColumns(refs, manifest_present=True)


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
        # Split infrastructure failures (timeout, connection death) from model SQL
        # faults (undefined column, bad cast). The former used to share the
        # ``exec_error:`` prefix, so ``classify_row`` treated them as answered-
        # and-wrong and ``crash_rate`` / ``quotable`` never saw them (audit E4).
        prefix = "infra_error" if is_infrastructure_error(err) else "exec_error"
        return {
            "correct": False,
            "correct_strict": False,
            # Prefixed so it is distinguishable downstream. ``exec_error:`` — the
            # model produced a statement that parses and then raises (type error,
            # unknown column): a wrong answer, not a harness crash. ``infra_error:``
            # — the grader could not finish the execution: a crash that must not
            # silently move the accuracy number.
            "error": f"{prefix}:{type(err).__name__}: {err}",
            "hash_lenient": None,
            "hash_strict": None,
        }
    # A truncated result is not a complete answer. Hashing the clipped rows as if
    # they were the full set can falsely match (or falsely miss) gold; either way
    # the comparison is meaningless. Same infra bucket as a timeout: counted,
    # blocks quotability, not a silent wrong answer.
    if getattr(result, "truncated", False):
        return {
            "correct": False,
            "correct_strict": False,
            "error": (
                f"infra_error:truncated: result exceeded row cap "
                f"({getattr(result, 'row_count', len(rows))} rows returned)"
            ),
            "hash_lenient": None,
            "hash_strict": None,
            "pred_nrows": len(rows),
            "pred_ncols": len(result.columns) if result.columns is not None else None,
            "gold_nrows": gold.nrows,
            "nrows_match": False,
        }
    h_lenient = hash_normalised_result(rows)
    h_strict = hash_normalised_result_strict(rows)
    # Result *shape* alongside the verdict. A prediction with the gold row count
    # but a different hash failed on projection / ordering / formatting; a
    # different row count means a genuinely different result set. Those two need
    # opposite responses (change the grading contract vs. fix the SQL), and the
    # scored booleans alone cannot tell them apart. Free here — the rows are
    # already in hand — whereas a true value-multiset tier would need the gold SQL
    # re-executed per question, and the gold artifact ships no such hash.
    return {
        "correct": h_lenient == gold.hash_lenient,
        "correct_strict": bool(gold.hash_strict) and h_strict == gold.hash_strict,
        "error": None,
        "hash_lenient": h_lenient,
        "hash_strict": h_strict,
        "pred_nrows": len(rows),
        "pred_ncols": len(result.columns) if result.columns is not None else None,
        "gold_nrows": gold.nrows,
        "nrows_match": (gold.nrows is not None and len(rows) == gold.nrows),
    }


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
    n_exec_errors = 0
    n_no_gold = 0
    n_unusable = 0
    errors: list[str] = []
    for item in items:
        qid = getattr(item, "question_id", None)
        if not qid or str(qid) not in gold_hashes:
            n_no_gold += 1
            continue
        gold = gold_hashes[str(qid)]
        if not gold.usable or not item.sql:
            n_unusable += 1
            continue
        try:
            result = gateway.execute(item.sql, identity)
            rows = list(result.rows)
        except Exception as err:
            # Counted, not merely appended to a truncated string list. An execution
            # error here reduced ``n_checked`` and never touched ``agree_rate``, so a
            # caller gating on ``agree_rate < 1.0`` passed on one agreeing row no
            # matter how many failed to run at all — and gold that cannot execute is
            # the single most likely thing to be wrong in this pre-flight, because it
            # is what a wrong DSN, an unloaded schema, a bad ``search_path`` or the
            # wrong ``gold_sql_field`` all look like.
            n_exec_errors += 1
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
        # Why the un-checked items were un-checked, kept apart because they mean
        # different things: an exec error is our configuration being wrong, whereas
        # missing or unusable gold is a property of the dataset that no run can fix.
        "n_exec_errors": n_exec_errors,
        "n_no_gold": n_no_gold,
        "n_unusable_gold": n_unusable,
        "errors": errors[:5],
    }


def free_pass_counts(
    rows: list[dict[str, Any]],
    *,
    gold: dict[str, str] | None = None,
    dialect: str = "postgres",
) -> dict[str, int]:
    """Count correct answers that are grading free passes (Audit E2).

    Every input is already on the scored row (or optionally ``gold`` SQL keyed by
    question id). These do not change ``correct`` — they only make free-pass mass
    visible in the summary so an arm that over-filters into empty-vs-empty matches
    cannot look identical to one that actually got the answer right.

    - ``n_correct_with_empty_gold``: ``correct`` and ``gold_nrows == 0``.
    - ``n_correct_and_pred_has_no_from``: ``correct`` and the prediction touches no
      tables (``SELECT 1``-style). Prefer an empty ``tables_used`` when the row
      recorded it; otherwise parse ``generated_sql``.
    - ``n_correct_and_zero_table_overlap``: ``correct``, both sides name at least one
      table, and the physical-name sets are disjoint. Uses ``oracle_gold_tables``
      when present, else ``gold`` SQL when supplied; pred tables always come from
      parsing ``generated_sql`` (``tables_used`` holds asset ids, not physical names).
      Skipped when neither gold source is available.
    """
    from .sql_diff import extract_features

    n_empty_gold = 0
    n_no_from = 0
    n_zero_overlap = 0

    for row in rows:
        if not row.get("correct"):
            continue
        if row.get("gold_nrows") == 0:
            n_empty_gold += 1

        sql = row.get("generated_sql")
        tables_used = row.get("tables_used")
        pred_no_from: bool | None = None
        if isinstance(tables_used, (list, tuple, set, frozenset)):
            pred_no_from = len(tables_used) == 0
        elif sql:
            pred_no_from = not extract_features(sql, dialect=dialect).tables
        if pred_no_from:
            n_no_from += 1

        gold_tables: frozenset[str] | None = None
        oracle_gold = row.get("oracle_gold_tables")
        if isinstance(oracle_gold, (list, tuple, set, frozenset)):
            gold_tables = frozenset(str(t).lower() for t in oracle_gold if t)
        elif gold is not None:
            qid = row.get("question_id") or row.get("request_id")
            gold_sql = gold.get(str(qid)) if qid is not None else None
            if gold_sql:
                gold_tables = extract_features(gold_sql, dialect=dialect).tables

        if gold_tables is None or not sql:
            continue
        pred_tables = extract_features(sql, dialect=dialect).tables
        if pred_tables and gold_tables and pred_tables.isdisjoint(gold_tables):
            n_zero_overlap += 1

    return {
        "n_correct_with_empty_gold": n_empty_gold,
        "n_correct_and_pred_has_no_from": n_no_from,
        "n_correct_and_zero_table_overlap": n_zero_overlap,
    }
