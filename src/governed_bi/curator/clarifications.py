"""Agent-authored clarifications ledger (``clarifications.jsonl``).

One self-contained JSONL record per line — the durable SME hand-off artifact
the Phase A deep agent maintains via ``FilesystemBackend`` file tools, and that
Phase B (plus the experiment SME fill helper) loads back.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Iterable, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ClarificationRecordStatus(str, Enum):
    open = "open"
    answered = "answered"


class ClarificationRecord(BaseModel):
    """One row in ``clarifications.jsonl``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    scope: str
    question: str
    status: ClarificationRecordStatus = ClarificationRecordStatus.open
    raised_by: list[str] = Field(default_factory=list)
    answer: str | None = None
    answered_by: str | None = None


CLARIFICATIONS_FILENAME = "clarifications.jsonl"


@runtime_checkable
class Responder(Protocol):
    """Seam a human SME or Simulated SME plugs into to answer a question."""

    def answer(self, question: str) -> str:
        """Return a free-text answer to a clarification question."""
        ...


class StaticResponder:
    """Scripted :class:`Responder` for offline runs and tests."""

    def __init__(self, answers: dict[str, str] | None = None, default: str = "") -> None:
        self._answers = dict(answers) if answers else {}
        self._default = default

    def answer(self, question: str) -> str:
        return self._answers.get(question, self._default)


def clarifications_path(run_dir: Path | str) -> Path:
    return Path(run_dir) / CLARIFICATIONS_FILENAME


def relocated_clarifications_path(run_dir: Path | str, schema: str) -> Path:
    """Canonical post-relocate ledger: ``<run_dir>/<schema>/_build/clarifications.jsonl``."""
    return Path(run_dir) / schema / "_build" / CLARIFICATIONS_FILENAME


def resolve_clarifications_path(run_dir: Path | str, schema: str) -> Path | None:
    """Live arm-root ledger first, then the relocated ``<schema>/_build`` path.

    Within one db build, sidecars still sit at the arm root until relocate runs.
    Across a resume, curated's ledger has already moved under ``<db>/_build/``; a
    reader that only checks the live root sees an empty ledger and folds nothing
    (or, under the no-model path's scaffolding, synthesises a misleading seed fold).
    """
    live = clarifications_path(run_dir)
    if live.exists():
        return live
    relocated = relocated_clarifications_path(run_dir, schema)
    if relocated.exists():
        return relocated
    return None


def parse_line(line: str) -> ClarificationRecord:
    """Parse and validate one JSONL line. Strict — the validating primitive."""
    return ClarificationRecord.model_validate_json(line)


#: Fields the model declares. Anything else the agent invents is dropped on repair
#: rather than rejected, because ``extra="forbid"`` turns one stray key into a lost
#: schema.
_KNOWN_FIELDS: frozenset[str] = frozenset(ClarificationRecord.model_fields)

#: The fields a record cannot be reconstructed without. Everything else has a
#: default, so a record holding these three is still a usable question.
_CORE_FIELDS: tuple[str, ...] = ("id", "scope", "question")


def repair_record(obj: object) -> tuple[ClarificationRecord | None, str | None]:
    """Best-effort recovery of one model-authored record. Never raises.

    Returns ``(record, note)`` — ``note`` is ``None`` when the object validated
    as-is, a short description of what was repaired otherwise, and ``record`` is
    ``None`` only when not even a question could be recovered.

    Repairs, in the order tried:

    1. A ``status`` outside the enum becomes ``open``. This is the fail-safe
       direction and it is not a new policy: ``open`` is exactly what
       :func:`quarantine_agent_answers` resets a forged answer to, so an
       unrecognised status is treated as what it is — not evidence a human
       answered. ``answer`` / ``answered_by`` are deliberately left in place, so a
       record like ``{"status": "resolved", "answer": "Verified: …",
       "answered_by": "curator_probe"}`` is still caught by that guard and still
       reported as forged.
    2. Keys the model does not declare are dropped (``extra="forbid"``).
    3. Failing both, the core three fields are kept and everything else reset to
       its default — a type slip in ``raised_by`` or ``answer`` costs that field,
       not the schema.
    """
    if not isinstance(obj, dict):
        return None, "not a JSON object"
    notes: list[str] = []
    data = dict(obj)

    raw_status = data.get("status")
    if raw_status is not None and raw_status not in tuple(
        s.value for s in ClarificationRecordStatus
    ):
        data["status"] = ClarificationRecordStatus.open.value
        notes.append(f"status={raw_status!r} is not in the enum, read as open")

    unknown = sorted(set(data) - _KNOWN_FIELDS)
    if unknown:
        for key in unknown:
            data.pop(key, None)
        notes.append("dropped undeclared key(s) " + ", ".join(repr(k) for k in unknown))

    try:
        return ClarificationRecord.model_validate(data), "; ".join(notes) or None
    except ValidationError:
        pass

    core = {k: data.get(k) for k in _CORE_FIELDS}
    if not all(isinstance(v, str) and v for v in core.values()):
        return None, "; ".join([*notes, "no recoverable id / scope / question"])
    try:
        record = ClarificationRecord.model_validate(core)
    except ValidationError as err:
        return None, "; ".join([*notes, f"unrepairable: {err}"])
    notes.append("kept id/scope/question only; other fields reset to defaults")
    return record, "; ".join(notes)


def load_clarifications_with_repairs(
    path: Path | str,
) -> tuple[list[ClarificationRecord], list[str]]:
    """Load all records, repairing what the agent got wrong. Never raises.

    The second element names every line that needed repair or was unusable —
    empty on a clean ledger. Callers that hold the durable artifact should record
    it and rewrite the file, so the repair happens once rather than on every read.

    This function used to be strict, and a single bad line raised ``ValueError``
    through :func:`~governed_bi.curator.pipeline.build_curated_corpus` and out to
    the eval driver, which recorded the whole schema as a build failure. On
    ``ice_hockey_draft`` that cost three perfectly good clarifications and the
    entire schema because the fourth said ``"status": "resolved"`` — a near-synonym
    for ``answered`` that any model will reach for eventually. Worse, the record it
    died on was a *self-answered* one, which the pipeline already has a guard for
    (:func:`quarantine_agent_answers`): strictness here meant the ValueError fired
    before the guard designed for exactly that case could run.

    The ledger is model-authored through ordinary file tools. Parsing it as if it
    were a trusted schema, and letting the parse failure decide whether a schema
    enters the experiment, is the defect — not the model's word choice.
    """
    p = Path(path)
    if not p.exists():
        return [], []
    records: list[ClarificationRecord] = []
    repairs: list[str] = []
    for i, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            records.append(parse_line(line))
            continue
        except ValidationError:
            pass
        try:
            obj = json.loads(line)
        except ValueError as err:
            repairs.append(f"line {i}: dropped, not valid JSON ({err})")
            continue
        record, note = repair_record(obj)
        if record is None:
            repairs.append(f"line {i}: dropped, {note}")
            continue
        records.append(record)
        repairs.append(f"line {i} ({record.id}): {note}")
    return records, repairs


def load_clarifications(path: Path | str) -> list[ClarificationRecord]:
    """Load all records from a JSONL file. Missing file → empty list.

    Repairs silently; see :func:`load_clarifications_with_repairs` for the report.
    """
    return load_clarifications_with_repairs(path)[0]


def write_clarifications(path: Path | str, records: Sequence[ClarificationRecord]) -> Path:
    """Overwrite ``path`` with one validated JSON object per line."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for rec in records:
            ClarificationRecord.model_validate(rec.model_dump())
            fh.write(rec.model_dump_json() + "\n")
    return p


def quarantine_agent_answers(
    records: Sequence[ClarificationRecord],
) -> tuple[list[ClarificationRecord], list[str]]:
    """Reset any answer the *agent* pre-filled, and report which ids were reset.

    The Phase A ledger is written by the deep agent through ordinary ``write_file`` /
    ``edit_file`` tools, so every field in it — including ``status`` and
    ``answered_by`` — is model-controlled. ``fill_clarifications_with_responder``
    only fills records still ``open``, and
    :meth:`AssetBag.apply_answered_clarifications` maps an answered record to
    ``source=human, status=certified``. Together those meant an agent could write

        {"id": "q001", ..., "status": "answered", "answered_by": "Jane Chen, Finance"}

    and mint a certified human fact with no human in the loop (AUDIT C6). Removing
    ``certified`` from the write-tool signatures closed the tool-argument path; this
    closes the file path, which is the one the agent actually owns.

    The prompt telling the agent to write ``"status": "open"`` is not a control. This
    is: an answer that did not come from the ``Responder`` seam does not survive the
    Phase A boundary, whatever the ledger says.
    """
    cleaned: list[ClarificationRecord] = []
    reset: list[str] = []
    for rec in records:
        forged = (
            rec.status is ClarificationRecordStatus.answered
            or rec.answer is not None
            or rec.answered_by is not None
        )
        if not forged:
            cleaned.append(rec)
            continue
        reset.append(rec.id)
        cleaned.append(
            rec.model_copy(
                update={
                    "status": ClarificationRecordStatus.open,
                    "answer": None,
                    "answered_by": None,
                }
            )
        )
    return cleaned, reset


def next_clarification_id(records: Sequence[ClarificationRecord]) -> str:
    """Allocate the next ``qNNN`` id."""
    max_n = 0
    for rec in records:
        if rec.id.startswith("q") and rec.id[1:].isdigit():
            max_n = max(max_n, int(rec.id[1:]))
    return f"q{max_n + 1:03d}"


def upsert_clarification_record(
    records: Sequence[ClarificationRecord],
    *,
    scope: str,
    question: str,
    raised_by: str,
) -> list[ClarificationRecord]:
    """Merge-by-scope discipline the Phase A prompt encodes.

    If an **open** record already covers ``scope``, keep the same ``id``, union
    ``raised_by``, and broaden ``question`` when the new text is not already
    contained. Otherwise append a new open record with the next ``qNNN`` id.
    Never duplicates an open scope.
    """
    out = [r.model_copy(deep=True) for r in records]
    for i, rec in enumerate(out):
        if rec.scope != scope or rec.status is not ClarificationRecordStatus.open:
            continue
        raised = list(dict.fromkeys([*rec.raised_by, raised_by]))
        broadened = rec.question
        q = question.strip()
        if q and q not in rec.question:
            broadened = f"{rec.question.rstrip(' ?')} — also: {q}"
        out[i] = rec.model_copy(update={"question": broadened, "raised_by": raised})
        return out
    out.append(
        ClarificationRecord(
            id=next_clarification_id(out),
            scope=scope,
            question=question,
            raised_by=[raised_by],
        )
    )
    return out


def seed_gap_clarifications(
    tables: Iterable,
    *,
    raised_by: str = "seed",
    confidence_threshold: float = 0.75,
    limit: int | None = 20,
) -> list[ClarificationRecord]:
    """Explicit offline scaffolding only (``seed_ledger_if_empty=True``).

    Not used on the default Phase B path — agent-authored ledgers are required
    unless the caller opts in (e.g. ``--oracle-only`` experiment runs, formerly
    ``--skip-agent``).
    """
    records: list[ClarificationRecord] = []
    n = 0
    for table in tables:
        if limit is not None and n >= limit:
            break
        tname = table.physical_name
        if table.description is None or (
            table.confidence is not None and float(table.confidence) < confidence_threshold
        ):
            n += 1
            records.append(
                ClarificationRecord(
                    id=f"q{n:03d}",
                    scope=f"table:{tname}",
                    question=f"What is the business meaning of table `{tname}`?",
                    raised_by=[raised_by],
                )
            )
            if limit is not None and n >= limit:
                break
        for col in table.columns:
            if limit is not None and n >= limit:
                break
            if col.description is None or (
                col.confidence is not None and float(col.confidence) < confidence_threshold
            ):
                n += 1
                records.append(
                    ClarificationRecord(
                        id=f"q{n:03d}",
                        scope=f"table:{tname}.{col.physical_name}",
                        question=(
                            f"What is the business meaning of `{tname}.{col.physical_name}`?"
                        ),
                        raised_by=[raised_by],
                    )
                )
    return records


def fill_clarifications_with_responder(
    records: Sequence[ClarificationRecord],
    responder: Responder,
    *,
    answered_by: str = "sme",
) -> list[ClarificationRecord]:
    """Answer every ``open`` record via a :class:`Responder`."""
    out: list[ClarificationRecord] = []
    for rec in records:
        if rec.status is not ClarificationRecordStatus.open:
            out.append(rec)
            continue
        answer = responder.answer(rec.question)
        out.append(
            rec.model_copy(
                update={
                    "status": ClarificationRecordStatus.answered,
                    "answer": answer,
                    "answered_by": answered_by,
                }
            )
        )
    return out


def parse_scope(scope: str) -> tuple[str, str | None]:
    """Parse ``table:Name`` or ``table:Name.col`` → ``(table, column|None)``."""
    if not scope.startswith("table:"):
        raise ValueError(f"unsupported clarification scope: {scope!r}")
    rest = scope[len("table:") :]
    if "." in rest:
        table, column = rest.split(".", 1)
        return table, column
    return rest, None
