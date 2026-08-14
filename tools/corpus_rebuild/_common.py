"""Shared plumbing for the one-shot corpus rebuild scripts.

These scripts are BIRD-specific and not portable. They deliberately sit outside
``src/governed_bi`` and nothing in the package imports them.

The main exception to "no engine imports" is ``governed_bi.corpus.identity`` (01, 02): ids must
have exactly one spelling, and when they did not, ``airline."Air Carriers"`` ended up with no
table asset at all while 24 few-shots cited it. Re-deriving that logic here would be a second
spelling waiting to drift. ``_set_asset_fields.py`` imports ``register.knobs.knob_default`` for
the same reason — a second copy of the summary cap is a second threshold.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

REPO = Path(__file__).resolve().parents[2]
BUILD = Path(__file__).resolve().parent / "_build"
DEFAULT_CORPUS = REPO.parent / "BIRD-corpus"
DATASET = REPO.parent / "BIRD-Data-Obfuscation"
EVAL_DATASET = DATASET / "eval_dataset"

#: What the scaffold writes into ``summary``. ``check_corpus_conformance`` rejects it (V2), so
#: an asset nobody finished cannot reach a run. Deliberately not a plausible-looking template:
#: the corpus being replaced is what happens when a writer finds one already in place.
SENTINEL = "TODO"

#: Files no rebuild script may read. The trap manifests are *not* here — the database under
#: test is the decoy instance and a steward would know which of its columns are junk. What may
#: be written about them is constrained instead (the brief's three hard rules, §5).
FORBIDDEN = ("test_final.jsonl", "gold_result_hashes", "question_paraphrases.jsonl")

_PG_TO_LOGICAL = {
    "smallint": "integer", "integer": "integer", "bigint": "integer",
    "numeric": "decimal", "real": "decimal", "double precision": "decimal", "money": "decimal",
    "boolean": "boolean",
    "date": "date",
    "timestamp without time zone": "datetime", "timestamp with time zone": "datetime",
    "time without time zone": "datetime", "time with time zone": "datetime",
}


def logical_type(physical: str) -> str:
    """A dialect-independent type for ``LogicalType``. Unknown catalog spellings are strings —
    the field is a retrieval and rendering hint, and guessing ``decimal`` for an unrecognised
    numeric would be a claim the catalog did not make."""
    return _PG_TO_LOGICAL.get(physical.strip().lower(), "string")


def guard(path: Path) -> Path:
    """Refuse a read of the held-out split. Cheap, and the one mistake that voids a run."""
    if any(bad in str(path) for bad in FORBIDDEN):
        raise SystemExit(f"refusing to read {path}: held-out data (see the brief, N1)")
    return path


def dsn() -> str:
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    value = os.environ.get("PG_RENAME_DECOY_DSN")
    if not value:
        raise SystemExit("PG_RENAME_DECOY_DSN is not set; scripts 01 and 06 need the live schema")
    return value


def evaluated_schemas() -> list[str]:
    """The 57 the benchmark asks about. The instance carries 71; the extra 14 are distractors
    for routing and must not enter the corpus, or they compete as candidates for nothing."""
    raw = json.loads((EVAL_DATASET / "evaluated_dbs.json").read_text(encoding="utf-8"))
    return sorted(d if isinstance(d, str) else d["db_id"] for d in raw)


def rename_map() -> dict[str, dict[str, str]]:
    return json.loads((EVAL_DATASET / "schema_rename_map.json").read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with guard(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def train_rows() -> list[dict[str, Any]]:
    return list(read_jsonl(EVAL_DATASET / "train_final.jsonl"))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def write_asset(root: Path, schema: str, subdir: str, name: str, mapping: dict[str, Any]) -> Path:
    """One asset file, written so two runs over the same inputs are byte-identical.

    ``newline="\\n"`` because the corpus repository sets ``* -text``: a CRLF checkout on Windows
    would otherwise move ``corpus_content_hash`` without changing a single fact. 1,327 files
    differed between the last two corpora for exactly that reason.

    ``sort_keys=False`` because the mapping is already in reading order, and a corpus a person
    has to review should read top-down.
    """
    path = root / schema / subdir / f"{name}.yaml" if subdir else root / schema / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        mapping, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def provenance(source: str, version: str, refs: list[str], evidence: str | None = None) -> dict[str, Any]:
    """An ``audit`` block. ``source_refs`` is what makes a later re-run auditable: the agent
    pass is not reproducible, so the inputs have to be recorded instead."""
    block: dict[str, Any] = {
        "provenance": {"source": source, "status": "draft", "version": version, "source_refs": refs}
    }
    if evidence:
        block["evidence"] = evidence
    return block


#: Split an evidence string into clauses. Semicolons always separate; a comma only does when it
#: sits between a finished phrase and a new capitalised or quoted one, which keeps
#: ``'a, b and c'`` value lists intact.
CLAUSE_SPLIT = re.compile(r";|(?<=[a-z0-9)'\"])\s*,\s*(?=[A-Z'\"])")


def clauses(evidence: str) -> list[str]:
    return [c.strip() for c in CLAUSE_SPLIT.split(evidence or "") if c.strip()]
