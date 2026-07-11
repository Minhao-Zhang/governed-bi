"""Load real BIRD-Obfuscation gold items into :class:`EvalItem` (D14).

Each line of ``<split>_final.jsonl`` is one JSON object carrying both the
obfuscated (``sql_rename`` / ``sql_base``) and the un-obfuscated (``sql_sqlite``)
gold SQL, keyed by ``db_id`` / ``question`` / ``question_id`` / ``difficulty`` /
``evidence``. For the **beer_factory-first** pass (D14) the arms run against the
vendored un-obfuscated database, so we map ``sql_sqlite`` -- the gold that
executes against those live identifiers -- into ``EvalItem.sql``.

The dataset directory is a **parameter**, never a hardcoded sibling-repo path:
the real files live outside this repo and are pointed at by the caller, while
tests feed a tmp fixture. Nothing is read at import time.
"""

from __future__ import annotations

import json
from pathlib import Path

from .dataset import EvalItem

_SPLITS = ("test", "train")


def _rows_path(dataset_dir: Path | str, split: str) -> Path:
    """Resolve ``<dataset_dir>/<split>_final.jsonl``, validating ``split``."""
    if split not in _SPLITS:
        raise ValueError(f"split must be one of {_SPLITS}, got {split!r}")
    return Path(dataset_dir) / f"{split}_final.jsonl"


def _iter_rows(dataset_dir: Path | str, split: str):
    """Yield parsed JSON objects from the split file, skipping blank lines."""
    path = _rows_path(dataset_dir, split)
    if not path.exists():
        raise FileNotFoundError(f"BIRD split file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            yield json.loads(line)


def load_bird_items(
    dataset_dir: Path | str, db_id: str, *, split: str = "test"
) -> list[EvalItem]:
    """Load the BIRD rows for one ``db_id`` as :class:`EvalItem` gold (D14).

    Reads ``<dataset_dir>/<split>_final.jsonl``, keeps rows whose ``db_id``
    matches, and maps ``question`` + ``sql_sqlite`` (the un-obfuscated gold used
    for the beer_factory-first pass) into an :class:`EvalItem`.

    Raises ``ValueError`` for an unknown ``split``, ``FileNotFoundError`` if the
    split file is missing, and ``ValueError`` (naming the ``question_id``) if a
    matching row lacks ``question`` or ``sql_sqlite``.
    """
    items: list[EvalItem] = []
    for row in _iter_rows(dataset_dir, split):
        if row.get("db_id") != db_id:
            continue
        try:
            question = row["question"]
            sql = row["sql_sqlite"]
        except KeyError as exc:
            qid = row.get("question_id", "<unknown>")
            raise ValueError(
                f"BIRD row question_id={qid} (db_id={db_id}) is missing {exc.args[0]!r}"
            ) from exc
        items.append(EvalItem(question=question, sql=sql))
    return items


def available_dbs(dataset_dir: Path | str, split: str = "test") -> set[str]:
    """Return the distinct ``db_id``s in a split (a harness convenience)."""
    return {
        db_id
        for row in _iter_rows(dataset_dir, split)
        if (db_id := row.get("db_id")) is not None
    }
