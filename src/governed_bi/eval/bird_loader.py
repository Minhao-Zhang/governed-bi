"""Load real BIRD-Obfuscation gold items into :class:`EvalItem` (D14).

Each line of ``<split>_final.jsonl`` is one JSON object carrying both the
obfuscated (``sql_rename`` / ``sql_base``) and the un-obfuscated (``sql_sqlite``)
gold SQL, keyed by ``db_id`` / ``question`` / ``question_id`` / ``difficulty`` /
``evidence``. For the **beer_factory-first** pass (D14) the arms run against the
vendored un-obfuscated database, so the default ``gold_sql_field`` is
``sql_sqlite``. The eval-ladder experiment on ``pg_rename_decoy`` passes
``gold_sql_field="sql_rename"`` instead.

The dataset directory is a **parameter**, never a hardcoded sibling-repo path:
the real files live outside this repo and are pointed at by the caller, while
tests feed a tmp fixture. Nothing is read at import time.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .dataset import EvalItem

logger = logging.getLogger("governed_bi.eval")

_SPLITS = ("test", "train")
_DEFAULT_GOLD_SQL_FIELD = "sql_sqlite"

#: Sidecar artifact that rewrites the handful of gold statements containing a
#: ``SELECT *`` projection into an explicit column list.
_STAR_EXPANDED_FILE = "gold_star_expanded.jsonl"

#: ``gold_sql_field -> the field in gold_star_expanded.jsonl that supersedes it``.
#:
#: Only the two Postgres fields are listed. ``sql_sqlite`` runs against the
#: un-obfuscated vendored database, which carries no decoy columns, so expanding
#: its star would be a no-op with a chance of being wrong.
_STAR_EXPANDED_FIELD = {
    "sql_base": "sql_base_expanded",
    "sql_rename": "sql_rename_expanded",
}


def _rows_path(dataset_dir: Path | str, split: str) -> Path:
    """Resolve ``<dataset_dir>/<split>_final.jsonl``, validating ``split``."""
    if split not in _SPLITS:
        raise ValueError(f"split must be one of {_SPLITS}, got {split!r}")
    return Path(dataset_dir) / f"{split}_final.jsonl"


def _parse_rows(path: Path) -> list[dict]:
    """Parse every JSON object in a split file, skipping blank lines."""
    out: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                out.append(json.loads(line))
    except UnicodeDecodeError as err:
        # Fail loud with the path rather than an opaque decode error mid-iteration.
        raise ValueError(
            f"BIRD split file is not valid UTF-8: {path} ({err})"
        ) from err
    return out


#: ``(resolved path, mtime_ns, size) -> {db_id: [row, ...]}``, in file order.
#:
#: Every public function here needs the same parse, and a pooled run calls them
#: dozens of times: once per db per split for the gold items, again for the
#: train/test disjointness assertion, again for ``available_dbs``. The obfuscated
#: BIRD splits are ~9 MB (test) and ~34 MB (train), so that was tens of seconds of
#: pure JSON parsing per run — and it was paid again on every ``--resume-from`` even
#: when nothing needed rebuilding.
#:
#: Keyed on identity AND (mtime, size) so regenerating a split invalidates the entry
#: instead of silently serving the previous dataset — which on this project would be
#: a *scoring* bug, not a caching one. Grouped by ``db_id`` because that is the access
#: pattern; rows with no ``db_id`` are dropped, which every caller already did.
_ROWS_CACHE: dict[tuple[str, int, int], dict[str, list[dict]]] = {}

#: Same keying discipline as ``_ROWS_CACHE``, for the star-expansion sidecar.
_STAR_CACHE: dict[tuple[str, int, int], dict[str, dict]] = {}


def clear_split_cache() -> None:
    """Drop the parsed-split cache. For tests that rewrite a fixture in place within
    the same mtime granularity; production invalidates on (mtime, size)."""
    _ROWS_CACHE.clear()
    _STAR_CACHE.clear()


def _rows_by_db(dataset_dir: Path | str, split: str) -> dict[str, list[dict]]:
    """``{db_id: rows}`` for one split, parsed at most once per file version."""
    path = _rows_path(dataset_dir, split)
    if not path.exists():
        raise FileNotFoundError(f"BIRD split file not found: {path}")
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    grouped = _ROWS_CACHE.get(key)
    if grouped is None:
        grouped = {}
        for row in _parse_rows(path):
            db_id = row.get("db_id")
            if db_id is None:
                continue
            grouped.setdefault(str(db_id), []).append(row)
        _ROWS_CACHE[key] = grouped
    return grouped


def _iter_rows(dataset_dir: Path | str, split: str):
    """Yield every row of a split, in file order. Kept for callers that want the
    whole split rather than one db's slice."""
    for rows in _rows_by_db(dataset_dir, split).values():
        yield from rows


def load_star_expanded_gold(dataset_dir: Path | str) -> dict[str, dict]:
    """``{question_id: row}`` from ``gold_star_expanded.jsonl``, or ``{}`` if absent.

    Keyed on ``question_id`` alone, which the artifact itself is: it carries no
    ``db_id``, and question ids are unique across the whole split pool (6,743 ids,
    no duplicates, none spanning two dbs), so the id identifies a question.
    """
    path = Path(dataset_dir) / _STAR_EXPANDED_FILE
    if not path.exists():
        # Absent is normal: test fixtures ship splits without the sidecar, and a
        # dataset whose gold contains no star projection needs no such file.
        return {}
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _STAR_CACHE.get(key)
    if cached is None:
        cached = {}
        for row in _parse_rows(path):
            qid = row.get("question_id")
            if qid is None:
                continue
            cached[str(qid)] = row
        _STAR_CACHE[key] = cached
    return cached


def load_bird_items(
    dataset_dir: Path | str,
    db_id: str,
    *,
    split: str = "test",
    gold_sql_field: str = _DEFAULT_GOLD_SQL_FIELD,
) -> list[EvalItem]:
    """Load the BIRD rows for one ``db_id`` as :class:`EvalItem` gold (D14).

    Reads ``<dataset_dir>/<split>_final.jsonl``, keeps rows whose ``db_id``
    matches, and maps ``question`` + the chosen gold SQL field into an
    :class:`EvalItem`. Also preserves ``question_id``, ``difficulty``, and
    ``evidence`` when present.

    **A ``SELECT *`` gold statement is replaced by its star-expanded twin** from
    ``gold_star_expanded.jsonl`` when the sidecar has one for that question and the
    requested field is ``sql_base`` / ``sql_rename``. This is not a convenience: the
    precomputed ``gold_result_hashes_*.jsonl`` were computed from the *expanded*
    statement, and their ``sql_sha256`` proves it — over the 6,743 gold rows,
    ``sql_sha256`` equals ``sha256(sql_rename)`` for 6,740 and ``sha256`` of the
    expanded twin for the remaining 3, with nothing else unexplained. On the
    ``rename_decoy`` databases the two are *not* the same query: ``SELECT *`` also
    returns the injected decoy columns, so submitting the unexpanded gold produces
    a result the gold hash cannot match and the grader marks the answer key wrong
    against itself (``mondial_geo`` / ``train_8505``: 16 rows either way, 7 columns
    submitted vs. the 4 gold was hashed from).

    Raises ``ValueError`` for an unknown ``split``, ``FileNotFoundError`` if the
    split file is missing, and ``ValueError`` (naming the ``question_id``) if a
    matching row lacks ``question`` or the chosen gold SQL field.
    """
    expanded_field = _STAR_EXPANDED_FIELD.get(gold_sql_field)
    expanded = load_star_expanded_gold(dataset_dir) if expanded_field else {}
    items: list[EvalItem] = []
    for row in _rows_by_db(dataset_dir, split).get(str(db_id), ()):
        qid = row.get("question_id", "<unknown>")
        try:
            question = row["question"]
            sql = row[gold_sql_field]
        except KeyError as exc:
            raise ValueError(
                f"BIRD row question_id={qid} (db_id={db_id}) is missing {exc.args[0]!r}"
            ) from exc
        if expanded_field:
            override = (expanded.get(str(qid)) or {}).get(expanded_field)
            if override:
                logger.debug(
                    "question_id=%s: using %s (star-expanded gold) instead of %s",
                    qid,
                    expanded_field,
                    gold_sql_field,
                )
                sql = override
        items.append(
            EvalItem(
                question=question,
                sql=sql,
                question_id=None if qid == "<unknown>" else str(qid),
                difficulty=row.get("difficulty"),
                evidence=row.get("evidence"),
            )
        )
    return items


def manifest_path(bird_dir: Path | str, filename: str) -> Path | None:
    """``filename`` under ``artifacts/`` or ``eval_dataset/``, or ``None`` if neither."""
    bird_dir = Path(bird_dir)
    for parent in ("artifacts", "eval_dataset"):
        candidate = bird_dir / parent / filename
        if candidate.exists():
            return candidate
    return None


def load_rename_map(bird_dir: Path | str, db_id: str) -> dict[str, str]:
    """BIRD identifier -> ``rename_decoy`` identifier, for one db.

    ``schema_rename_map.json`` is one *flat* identifier map per db: table names and
    column names share the namespace, so a column name that collides with a table
    name (or with a same-named column in another table) has exactly one entry. That
    is fine for both callers — grading translates table names, the SME brief
    translates whatever identifier it is addressing — but it means the map cannot
    express a per-table column rename, and never could.

    An absent file yields an empty map rather than an error, because the
    identity-rename dbs need no translation at all.
    """
    path = manifest_path(bird_dir, "schema_rename_map.json")
    if path is None:
        logger.warning(
            "schema_rename_map.json not found under %s (checked artifacts/ and "
            "eval_dataset/); db %r will NOT be translated to its physical "
            "identifiers. Callers that address the obfuscated schema — the SME "
            "brief above all — will name identifiers that do not exist there.",
            bird_dir,
            db_id,
        )
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = data.get(db_id)
    if not mapping:
        # Identity-rename dbs still carry a full map of name -> same name, so an
        # absent entry is a missing db, never "this db needs no translation".
        logger.warning(
            "db %r has no entry in %s; it will NOT be translated to its physical "
            "identifiers",
            db_id,
            path,
        )
        return {}
    return {str(k): str(v) for k, v in mapping.items() if isinstance(v, str)}


def description_dir(bird_dir: Path | str, db_id: str) -> Path | None:
    """The BIRD ``database_description/`` directory for ``db_id``, or ``None``.

    BIRD splits its schemas across two trees — ``data/train/train_databases/`` and
    ``data/dev/dev_databases/`` — and the 69-schema pool draws from both. Callers
    that hardcoded the train tree silently found no CSVs for the 11 dev-tree
    schemas (california_schools, financial, formula_1, superhero, ...), which
    left their SME arm's brief silently empty without failing.
    """
    bird_dir = Path(bird_dir)
    for split, tree in (("train", "train_databases"), ("dev", "dev_databases")):
        candidate = bird_dir / "data" / split / tree / db_id / "database_description"
        if candidate.is_dir():
            return candidate
    return None


def available_dbs(dataset_dir: Path | str, split: str = "test") -> set[str]:
    """Return the distinct ``db_id``s in a split (a harness convenience)."""
    return set(_rows_by_db(dataset_dir, split))


def load_cross_db_unanswerable(
    dataset_dir: Path | str, db_id: str, *, k: int = 20, split: str = "test"
) -> list[str]:
    """Questions drawn from *other* ``db_id``s — a model-free negative set for the
    refuse-gate eval (Architecture section 8, "cross-DB cases").

    A question written against a different schema/domain is, by construction,
    unanswerable for ``db_id``: the Analyst should refuse it. Round-robins across
    the other DBs (deterministic order — no RNG) so the ``k`` sampled questions
    span domains rather than all coming from whichever DB is first in the file.
    """
    by_db: dict[str, list[str]] = {}
    for other, rows in _rows_by_db(dataset_dir, split).items():
        if other == db_id:
            continue
        questions = [q for row in rows if (q := row.get("question"))]
        if questions:
            by_db[other] = questions
    out: list[str] = []
    cursors = {d: 0 for d in by_db}
    while len(out) < k and any(cursors[d] < len(by_db[d]) for d in by_db):
        for d in sorted(by_db):
            if len(out) >= k:
                break
            if cursors[d] < len(by_db[d]):
                out.append(by_db[d][cursors[d]])
                cursors[d] += 1
    return out
