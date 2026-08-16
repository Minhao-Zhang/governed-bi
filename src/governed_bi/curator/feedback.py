"""The reader-reported-wrong-answer ledger (UtkuAI trust loop, task H): ``feedback.jsonl``.

**H-b, restated for this module.** A clarification (``curator/clarifications.py``) is the
*engine* asking a question it cannot answer on its own; a report is the *reader* objecting to an
answer the engine already gave. Different lifecycles -- a clarification can be ``deferred``, a
report cannot, because nothing asked the reader anything they might defer -- and different
meanings, so this is a second record type and a second file, never a row appended to
``clarifications.jsonl``. Merging the two would make every downstream count ambiguous (how many
of "answered clarifications" are actually corrections to a wrong answer?), which is exactly the
cost ``~/Antigravity/experiments/009_failure-attribution/SUMMARY.md`` spent a day pricing.

**Mirrors ``clarifications.py``'s shape, not its file split.** That module is pure CRUD/
persistence, with the fold (``curator/clarification.py``) factored into a sibling file because it
has *two* entry points -- a live turn's own resume, and the offline
``POST /clarifications/{id}/answer`` route -- that must reach byte-identical behaviour. A report
has exactly one entry point (``POST /feedback/{id}/answer``; there is no live-turn concept of
"resuming" a filed report), so there is nothing a second file would keep in sync that this one
file does not already keep in sync by being one file. The record shape, the ``open`` /
``answered`` / ``dismissed`` lifecycle, and the JSONL round trip below are the mirror;
:func:`fold_report_into_corpus` at the bottom is this module's whole reason not to also mirror
the file split.

**``dismissed``, not ``cancelled``.** ``ClarificationRecordStatus.cancelled`` is the *reader*
abandoning a question nobody can answer for them
(``curator/clarifications.py::cancel_clarification``, gated on ``basis``). Here the actor is the
opposite: an *admin* deciding a filed report needs no corpus change. Same surface-level effect
("this row stops asking for anything"), different act, different actor -- the 2026-08-15
``basis``-dependent cancel rule is the precedent this project already has for keeping such
distinctions apart rather than collapsing them into one status name.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "FeedbackRecordStatus",
    "FeedbackRecord",
    "FeedbackNotFound",
    "feedback_path",
    "load_feedback",
    "write_feedback",
    "file_report",
    "answer_report",
    "dismiss_report",
    "fold_report_into_corpus",
]


class FeedbackRecordStatus(str, Enum):
    open = "open"
    answered = "answered"
    #: The admin looked at this report and decided the corpus needs no change (the answer was
    #: right, or the fix belongs somewhere this ledger cannot express). Not ``cancelled`` -- see
    #: the module docstring.
    dismissed = "dismissed"


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    """One row in ``feedback.jsonl``: a reader saying one turn's answer was wrong.

    ``turn_id`` + ``question`` + ``answer_text`` are what :func:`file_report` derives this
    record's id from, so they are required, not optional -- there is no shape for "a report about
    nothing in particular". ``reason`` is the one genuinely optional field (H-3's "on click, an
    optional one-line reason"), and ``correction``/``answered_by`` stay unset until
    :func:`answer_report` writes them, the same way ``ClarificationRecord.answer``/``answered_by``
    sit unset on an ``open`` clarification.
    """

    id: str
    turn_id: str
    question: str
    #: The answer the reader is objecting to, exactly as the card showed it
    #: (``lib/answer-delivery.ts::displayText``) -- not re-derived later, because the corpus or the
    #: model may have moved on by the time an admin looks at this row, and what is being disputed
    #: is what the reader actually saw, not what the engine would say today.
    answer_text: str
    status: FeedbackRecordStatus = FeedbackRecordStatus.open
    reason: str | None = None
    #: When this report was filed, ``datetime.now(timezone.utc).isoformat(timespec="seconds")`` --
    #: matching ``api/trace_store.py``'s own timestamp format, this project's one other place that
    #: stamps wall-clock time onto a durable record.
    reported_at: str | None = None
    #: The admin's corrected answer -- what :func:`fold_report_into_corpus` folds into the corpus
    #: draft. ``None`` until :func:`answer_report` sets it.
    correction: str | None = None
    answered_by: str | None = None
    #: Idempotency marker for :func:`fold_report_into_corpus`, mirroring
    #: ``ClarificationRecord.converted_to_corpus`` by name on purpose: same job, same reader
    #: expectation for what the field means on sight.
    converted_to_corpus: bool = False


class FeedbackNotFound(LookupError):
    """No record with this id exists in the ledger."""


def feedback_path(corpus_root: Path | str) -> Path:
    """Where the ledger lives for one corpus root: ``<corpus_root>/feedback.jsonl`` -- a sibling
    of ``clarifications_path``'s ``clarifications.jsonl``, never the same file (H-b)."""
    return Path(corpus_root) / "feedback.jsonl"


def _feedback_to_json(record: FeedbackRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "turn_id": record.turn_id,
        "question": record.question,
        "answer_text": record.answer_text,
        "status": record.status.value,
        "reason": record.reason,
        "reported_at": record.reported_at,
        "correction": record.correction,
        "answered_by": record.answered_by,
        "converted_to_corpus": record.converted_to_corpus,
    }


def _feedback_from_json(raw: Mapping[str, Any], *, where: str) -> FeedbackRecord:
    """One parsed JSON object into a :class:`FeedbackRecord`.

    Unknown keys are rejected, matching ``curator/clarifications.py::_from_json``'s own
    ``extra="forbid"`` port: a mistyped field name that parses is a field nobody writes and
    nothing reads.
    """
    known = {f.name for f in fields(FeedbackRecord)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {unknown}")
    data = dict(raw)
    if "status" in data:
        data["status"] = FeedbackRecordStatus(data["status"])
    try:
        return FeedbackRecord(**data)
    except TypeError as err:
        raise ValueError(f"{where}: {err}") from err


def load_feedback(corpus_root: Path | str) -> list[FeedbackRecord]:
    """Every report in the ledger, in file order. No ledger file -> empty list."""
    path = feedback_path(corpus_root)
    if not path.exists():
        return []
    records: list[FeedbackRecord] = []
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
        records.append(_feedback_from_json(parsed, where=f"{path}: line {i}"))
    return records


def write_feedback(corpus_root: Path | str, records: Sequence[FeedbackRecord]) -> Path:
    """Overwrite the ledger with ``records``, one JSON object per line -- full-file
    load-mutate-write, matching ``clarifications.py``'s own simplicity."""
    path = feedback_path(corpus_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_feedback_to_json(record)) + "\n")
    return path


def _replace_feedback_record(corpus_root: Path | str, feedback_id: str, **changes: Any) -> FeedbackRecord:
    """Load the ledger, replace the one record matching ``feedback_id``, write the whole ledger
    back, and return the updated record. Raises :class:`FeedbackNotFound` on an unknown id."""
    records = load_feedback(corpus_root)
    for i, record in enumerate(records):
        if record.id != feedback_id:
            continue
        updated = replace(record, **changes)
        records[i] = updated
        write_feedback(corpus_root, records)
        return updated
    raise FeedbackNotFound(f"no feedback report {feedback_id!r} under {corpus_root}")


def file_report(
    corpus_root: Path | str,
    *,
    turn_id: str,
    question: str,
    answer_text: str,
    reason: str | None = None,
) -> FeedbackRecord:
    """A reader says ``turn_id``'s answer is wrong. Appends an ``open`` record and returns it.

    **Idempotent by content, not by call.** The id is a hash of ``turn_id`` + ``question`` +
    ``answer_text`` -- the same reasoning ``api/curation_routes.py::
    clarification_from_refusal_route`` already gives for its own ``refusal-{digest}`` id: a
    network retry of the same click must not double the admin's queue, and there is no graph
    interrupt here to guard against it the way a live ``ask_user`` question has. Filing the exact
    same report twice returns the existing record unchanged rather than a second row; a different
    ``reason`` for the same turn does **not** mint a second row either (unlike
    ``append_if_new_scope``'s per-question dedup for clarifications) -- one turn has one wrong
    answer, and a second reason for the same complaint is more context on the same report, not a
    second complaint. Nothing about this record shape lets a caller *add* a reason to an existing
    open report; if that turns out to matter, it is a real, separate follow-up.
    """
    digest = hashlib.sha256(f"{turn_id}\x1f{question}\x1f{answer_text}".encode()).hexdigest()[:16]
    record_id = f"feedback-{digest}"
    records = load_feedback(corpus_root)
    existing = next((r for r in records if r.id == record_id), None)
    if existing is not None:
        return existing
    record = FeedbackRecord(
        id=record_id,
        turn_id=turn_id,
        question=question,
        answer_text=answer_text,
        reason=reason,
        reported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    records.append(record)
    write_feedback(corpus_root, records)
    return record


def answer_report(
    corpus_root: Path | str, feedback_id: str, *, correction: str, answered_by: str = "admin"
) -> FeedbackRecord:
    """Record one admin correction to ``feedback_id`` and persist the whole ledger.

    Sets ``status -> answered`` plus ``correction``/``answered_by``. Does **not** itself fold the
    correction into the corpus -- ``api/feedback_routes.py``'s own route calls
    :func:`fold_report_into_corpus` right after this returns, the same split
    ``curator/clarifications.py::answer_clarification`` keeps from its own fold.

    Raises :class:`FeedbackNotFound` on an unknown id.
    """
    return _replace_feedback_record(
        corpus_root,
        feedback_id,
        status=FeedbackRecordStatus.answered,
        correction=correction,
        answered_by=answered_by,
    )


def dismiss_report(corpus_root: Path | str, feedback_id: str) -> FeedbackRecord:
    """The admin decided this report needs no corpus change. Lands ``dismissed`` unconditionally
    -- unlike ``cancel_clarification``, there is no ``basis``-shaped reason this might instead
    leave the row untouched, because the admin (not a ``basis`` value) is the one deciding.

    Refuses an already-``answered`` record, same reasoning as
    ``curator/clarifications.py::cancel_clarification``'s own refusal: its correction may already
    be folded into the corpus under an id hashed from this report's own text, and dismissing it now
    would strand that fact behind a ledger no longer claiming the report was ever answered.

    Raises :class:`FeedbackNotFound` on an unknown id.
    """
    record = next((r for r in load_feedback(corpus_root) if r.id == feedback_id), None)
    if record is None:
        raise FeedbackNotFound(f"no feedback report {feedback_id!r} under {corpus_root}")
    if record.status is FeedbackRecordStatus.answered:
        raise ValueError(
            f"feedback report {feedback_id!r} is already answered, so it cannot be dismissed: "
            "its correction may already be folded into the corpus under an id hashed from this "
            "report's own text, and the asset would outlive the ledger's claim that it was ever "
            "answered."
        )
    return _replace_feedback_record(corpus_root, feedback_id, status=FeedbackRecordStatus.dismissed)


def _report_draft(question: str, answer: str, *, schema: str):
    """One admin correction as a :class:`~governed_bi.corpus.schema.TermAsset` draft.

    **Deliberately not ``curator/clarification.py::draft_from_clarification``**, despite building
    an identical shape from the same two strings. That function hardcodes the id prefix
    ``clarification.<schema>.<hash>`` -- which is exactly the string
    ``api/curation_routes.py::_is_clarification_derived`` keys on to decide whether an asset
    belongs in ``GET /corpus/assumptions``. Reusing it here would make a reported wrong answer's
    correction silently show up in "Agreed Assumptions" the moment an admin certifies it, which
    is the exact conflation H-b rules out at the record-type level: a report is not a
    clarification, so its corpus footprint should not read as one either.  ``feedback.<schema>.
    <hash>`` keeps the two producers structurally apart -- with **no change to
    ``curation_routes.py`` at all**: that route's existing prefix check continues to mean exactly
    what its own docstring already says, and simply never matches this producer's output.

    The draft still shows up in ``GET /corpus/drafts`` (task D's queue, which is prefix-agnostic --
    every ``proposed`` asset, regardless of id) and, once approved, behaves like any other
    certified ``TermAsset`` for retrieval. Only the one read route's categorisation changes.
    """
    from governed_bi.corpus.schema import TermAsset
    from governed_bi.curator.clarification import _qa_summary, _truncated

    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    return TermAsset(
        id=f"feedback.{schema}.{digest}",
        name=_truncated(question),
        summary=_qa_summary(question, answer),
        body=f"Q: {question}\nA: {answer}",
    )


def fold_report_into_corpus(
    record: FeedbackRecord,
    *,
    agent_model: Any,
    corpus_root: Path | str,
    schema: str | None,
    known_assets: Iterable[Any],
    write_model: str | None = None,
) -> FeedbackRecord:
    """Fold one *answered* :class:`FeedbackRecord`'s correction into the corpus via the same
    Enhancer dedup/conflict path ``curator/clarification.py::fold_answered_clarification`` uses
    for a clarification answer, then mark it ``converted_to_corpus`` so a second call on the same
    record is a no-op (mirrors ``fold_ledger_answer_into_corpus``'s own idempotency).

    **The ``source`` decision (task H-4).** Stamped ``"feedback"`` into ``audit.extra["source"]``
    -- a fourth value alongside ``ClarificationRecord.source``'s ``curator`` / ``live_chat`` /
    ``elicitation_wizard`` / ``refusal``, but **not added to that ``Literal``**: no
    ``ClarificationRecord`` ever carries ``source="feedback"`` (H-b's whole point is that a report
    is not a clarification), so there is nothing of that type for the Literal to describe. What
    grows instead is the *asset-level* ``audit.extra["source"]`` string this function stamps --
    already a plain ``str`` on ``fold_answered_clarification``'s own signature, not bound to
    ``ClarificationRecord.source`` at all, because it already had two independent producers before
    this one (a live turn's own ``fold_answered_clarification`` call passes literal
    ``"live_chat"``, never a ``ClarificationRecord``). This is the third. Distinct from
    ``"refusal"`` and ``"live_chat"`` because task C's third counter -- "how many refusals became
    approved rules" -- is unanswerable if a reported wrong answer's correction is
    indistinguishable from a refusal's clarification at the provenance level; the same argument
    for a fourth, symmetric counter ("how many reports became approved rules") requires this value
    to be its own thing, never folded into either existing one.

    Not gated on anything resembling ``ClarificationRecord.basis`` -- there is no ambiguity-kind
    concept on this record shape, so every answered report folds, unconditionally, at
    ``ProvenanceStatus.proposed``: an admin's correction is always a claim a second admin should
    still approve before it is retrievable as certified, never a claim strong enough to skip that
    gate (there is no unwarranted-answer shape here the way a clarification's unmet prerequisite
    is one).

    Best-effort, matching ``fold_answered_clarification``: any failure building the draft, or any
    :class:`~governed_bi.curator.enhancer.EnhancerError` from a broken dedup/conflict model call,
    degrades to an unconditional plain write rather than dropping a real admin correction.
    """
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.curator import enhancer
    from governed_bi.curator.clarification import _is_certified

    if record.converted_to_corpus or not record.correction:
        return record
    try:
        draft = _report_draft(record.question, record.correction, schema=schema)
        existing = [
            asset
            for asset in known_assets
            if asset.asset_type.value == "term" and _is_certified(asset)
        ]
        try:
            enhancer.apply(
                agent_model,
                corpus_root,
                draft,
                existing=existing,
                namespace=schema,
                write_model=write_model,
                status=ProvenanceStatus.proposed,
                extra={"source": "feedback"},
            )
        except enhancer.EnhancerError:
            submit_draft(
                corpus_root,
                draft,
                namespace=schema,
                status=ProvenanceStatus.proposed,
                extra={"source": "feedback"},
            )
    except Exception:  # noqa: BLE001 -- mining is best-effort, never fatal to the caller
        pass
    return _replace_feedback_record(corpus_root, record.id, converted_to_corpus=True)
