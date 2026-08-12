"""The offline clarifications ledger (UtkuAI, ported): ``clarifications.jsonl``.

**Phase 1a of restoring v1's offline Clarifications queue + Setup Wizard onto v2.** v1's
``curator/clarifications.py`` persists one admin-facing question per line in
``clarifications.jsonl`` and lets an admin answer it outside any live chat turn — see
``utku-ai-v2-porting-spec.md``. This module is that ledger's model and storage, ported.

**What this phase is, and is not.** This is pure CRUD + persistence: a record's shape, a
full-file JSONL load/write, and one function to answer a record and write it back. Three
things a later phase wires in on purpose are *not* here yet:

* ``ask_user`` (``serve/tools.py``) writing an unanswered question into this ledger — Phase 1b.
* Folding an answered record into a corpus draft (the ``curator/clarification.py`` +
  ``curator/enhancer.py`` pipeline this session already built for *live* clarifications) —
  Phase 1c. This ledger and that pipeline are unconnected until then.
* ``category``/``ui_modality``/Setup-Wizard-specific answer composition — Phase 2. The two
  fields are declared below so the record shape does not need to change again to add them,
  but nothing here reads or writes them.

Frozen dataclass, not Pydantic, matching :mod:`governed_bi.corpus.schema`'s ``Asset``
subclasses — this repo's own idiom for a persisted domain record — rather than v1's
``pydantic.BaseModel``. ``api/routes.py`` mounts no Pydantic request/response models anywhere,
so a JSONL-shaped record has no HTTP-model convention to match here beyond "not Pydantic."
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

__all__ = [
    "ClarificationRecordStatus",
    "ClarificationRecord",
    "ClarificationNotFound",
    "clarifications_path",
    "load_clarifications",
    "write_clarifications",
    "resolve_answer_text",
    "answer_clarification",
]


class ClarificationRecordStatus(str, Enum):
    open = "open"
    answered = "answered"


#: Phase 2 Setup Wizard categories (fixed priority order A > C > E > B > D in v1). Declared
#: only — nothing in this phase generates or reads a category-tagged record.
ElicitationCategory = Literal["A", "B", "C", "D", "E"]

#: Phase 2 Setup Wizard UI widget for a category-tagged candidate. Declared only, same reason.
ElicitationUiModality = Literal["column_picker", "numeric", "checkbox", "checklist"]


@dataclass(frozen=True, slots=True)
class ClarificationRecord:
    """One row in ``clarifications.jsonl``.

    Sequence fields are tuples, not lists — matching ``corpus/schema.py``'s Asset
    subclasses (this repo's own frozen-dataclass idiom), not v1's ``list[...]``. JSONL is
    the wire format either way; the boundary functions below convert at the JSON edge.
    """

    id: str
    scope: str
    question: str
    status: ClarificationRecordStatus = ClarificationRecordStatus.open
    raised_by: tuple[str, ...] = ()
    #: Each choice: ``{"id": ..., "label": ...}``.
    choices: tuple[Mapping[str, str], ...] | None = None
    allow_freeform: bool = True
    answer: str | None = None
    answer_choice_id: str | None = None
    #: Multi-select audit trail (Phase 2's checklist modality). Not read by
    #: :func:`resolve_answer_text` today — declared so a future caller has somewhere to put it.
    answer_choice_ids: tuple[str, ...] | None = None
    answered_by: str | None = None
    converted_to_corpus: bool = False
    source: Literal["curator", "live_chat", "elicitation_wizard"] = "curator"
    category: ElicitationCategory | None = None
    ui_modality: ElicitationUiModality | None = None
    target_table: str | None = None
    target_column: str | None = None


class ClarificationNotFound(LookupError):
    """No record with this id exists in the ledger."""


def clarifications_path(corpus_root: Path | str) -> Path:
    """Where the ledger lives for one corpus root: ``<corpus_root>/clarifications.jsonl``."""
    return Path(corpus_root) / "clarifications.jsonl"


def _to_json(record: ClarificationRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "scope": record.scope,
        "question": record.question,
        "status": record.status.value,
        "raised_by": list(record.raised_by),
        "choices": [dict(c) for c in record.choices] if record.choices is not None else None,
        "allow_freeform": record.allow_freeform,
        "answer": record.answer,
        "answer_choice_id": record.answer_choice_id,
        "answer_choice_ids": (
            list(record.answer_choice_ids) if record.answer_choice_ids is not None else None
        ),
        "answered_by": record.answered_by,
        "converted_to_corpus": record.converted_to_corpus,
        "source": record.source,
        "category": record.category,
        "ui_modality": record.ui_modality,
        "target_table": record.target_table,
        "target_column": record.target_column,
    }


def _from_json(raw: Mapping[str, Any], *, where: str) -> ClarificationRecord:
    """One parsed JSON object into a :class:`ClarificationRecord`.

    Unknown keys are rejected — v1's ``ConfigDict(extra="forbid")``, ported: a mistyped field
    name that parses is a field nobody writes and nothing reads.
    """
    known = {f.name for f in fields(ClarificationRecord)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {unknown}")
    data = dict(raw)
    if "status" in data:
        data["status"] = ClarificationRecordStatus(data["status"])
    if data.get("raised_by") is not None:
        data["raised_by"] = tuple(data["raised_by"])
    if data.get("choices") is not None:
        data["choices"] = tuple(dict(c) for c in data["choices"])
    if data.get("answer_choice_ids") is not None:
        data["answer_choice_ids"] = tuple(data["answer_choice_ids"])
    try:
        return ClarificationRecord(**data)
    except TypeError as err:
        raise ValueError(f"{where}: {err}") from err


def load_clarifications(corpus_root: Path | str) -> list[ClarificationRecord]:
    """Every record in the ledger, in file order. No ledger file → empty list.

    Full-file read, matching v1's own simplicity — no locking or append-in-place
    sophistication v1 didn't have either.
    """
    path = clarifications_path(corpus_root)
    if not path.exists():
        return []
    records: list[ClarificationRecord] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as err:
            raise ValueError(f"{path}: line {i}: not valid JSON: {err}") from err
        if not isinstance(parsed, Mapping):
            raise ValueError(f"{path}: line {i}: expected a JSON object, got {type(parsed).__name__}")
        records.append(_from_json(parsed, where=f"{path}: line {i}"))
    return records


def write_clarifications(corpus_root: Path | str, records: Sequence[ClarificationRecord]) -> Path:
    """Overwrite the ledger with ``records``, one JSON object per line."""
    path = clarifications_path(corpus_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_to_json(record)) + "\n")
    return path


def resolve_answer_text(record: ClarificationRecord) -> str | None:
    """The answer text a caller renders for ``record`` — ported from v1 unchanged.

    A picked choice's ``label`` is the primary text; a freeform ``answer`` set alongside it
    (picked a choice *and* added freeform context) is appended for context. With no choice
    picked, the freeform ``answer`` is used as-is. A ``answer_choice_id`` that does not match
    any of ``choices`` is not an error — it silently falls through to the freeform ``answer``,
    same as v1: this function is not where an id gets validated.

    Not called by anything in this phase (folding an answer into the corpus is Phase 1c) —
    built now because ``GET /clarifications`` needs it: a choice-only answer leaves the
    record's own ``answer`` field ``None``, and this is what turns ``answer_choice_id`` back
    into readable text for a ledger view.
    """
    label: str | None = None
    if record.answer_choice_id and record.choices:
        for choice in record.choices:
            if choice.get("id") == record.answer_choice_id:
                label = choice.get("label")
                break
    if label and record.answer:
        return f"{label} — {record.answer}"
    return label or record.answer


def answer_clarification(
    corpus_root: Path | str,
    clarification_id: str,
    *,
    choice_id: str | None = None,
    choice_ids: Sequence[str] | None = None,
    answer: str | None = None,
    answered_by: str = "admin",
) -> ClarificationRecord:
    """Record one admin answer to ``clarification_id`` and persist the whole ledger.

    Sets ``status -> answered`` plus ``answer``/``answer_choice_id``/``answer_choice_ids``/
    ``answered_by`` from the caller's arguments. Nothing else reads or writes this ledger
    concurrently in this phase, so a load-mutate-write-the-whole-file round trip (matching
    v1's own ``app.py`` handler) needs no locking.

    Does **not** fold the answer into the corpus (Phase 1c, deliberately deferred) and does
    not validate ``choice_id`` against the record's declared ``choices`` (v1 doesn't either —
    see :func:`resolve_answer_text`).

    Raises :class:`ClarificationNotFound` on an unknown id.
    """
    records = load_clarifications(corpus_root)
    for i, record in enumerate(records):
        if record.id != clarification_id:
            continue
        updated = replace(
            record,
            status=ClarificationRecordStatus.answered,
            answer=answer,
            answer_choice_id=choice_id,
            answer_choice_ids=tuple(choice_ids) if choice_ids is not None else None,
            answered_by=answered_by,
        )
        records[i] = updated
        write_clarifications(corpus_root, records)
        return updated
    raise ClarificationNotFound(f"no clarification {clarification_id!r} under {corpus_root}")
