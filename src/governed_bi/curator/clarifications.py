"""The offline clarifications ledger (UtkuAI, ported): ``clarifications.jsonl``.

**Phases 1a-1c of restoring v1's offline Clarifications queue + Setup Wizard onto v2.** v1's
``curator/clarifications.py`` persists one admin-facing question per line in
``clarifications.jsonl`` and lets an admin answer it outside any live chat turn — see
``utku-ai-v2-porting-spec.md``. This module is that ledger's model and storage, ported.

**What this module is, and is not.** This is pure CRUD + persistence: a record's shape, a
full-file JSONL load/write, and functions to answer a record and mark it converted, each
writing the whole ledger back. ``ask_user`` (``serve/tools.py``) writing an unanswered
question into this ledger (Phase 1b) and folding an answered record into a corpus draft
(``curator/clarification.py::fold_ledger_answer_into_corpus``, Phase 1c) both live outside
this module and call into it — this module never calls out to either. ``category``/
``ui_modality``/Setup-Wizard-specific answer composition — Phase 2 — remain declared-only:
the two fields are declared below so the record shape does not need to change again to add
them, but nothing here reads or writes them.

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
    "unmet_prerequisites",
    "answer_clarification",
    "mark_converted_to_corpus",
    "append_if_new_scope",
]


class ClarificationRecordStatus(str, Enum):
    open = "open"
    answered = "answered"


#: Phase 2 Setup Wizard categories (fixed priority order A > C > E > B > D in v1). Declared
#: only — nothing in this phase generates or reads a category-tagged record.
ElicitationCategory = Literal["A", "B", "C", "D", "E"]

#: Phase 2 Setup Wizard UI widget for a category-tagged candidate. Declared only, same reason.
ElicitationUiModality = Literal["column_picker", "numeric", "checkbox", "checklist"]

#: Severity tier of the gap a candidate asks about — ``utku-ai-setup-wizard-gap-model.md``
#: § "Tier structure". The discriminator is **what happens if the question goes unanswered**,
#: not how much accuracy the category bought on a benchmark:
#:
#: - ``T1`` *Poison* — silently wrong answer that looks right, **and** the gap sits on an
#:   identity/join key, so it contaminates every question touching that table.
#: - ``T2`` *Silent-wrong, local* — also silently wrong, but scoped to one term/metric/column.
#:   T1 vs T2 is blast radius, not danger class; both are wrong answers nobody can spot.
#: - ``T3`` *Safe failure* — worst case is a refusal or a mid-turn ``ask_user``. Correctness is
#:   never at risk, so leaving one open is an acceptable steady state.
#: - ``T4`` *Polish* — retrieval quality or phrasing only.
#:
#: Stored as the doc's own ``"T1"``-style label rather than an int 1-4, matching
#: :data:`ElicitationCategory`/:data:`ElicitationUiModality`/``source``: every closed vocabulary
#: on this record is a string ``Literal`` that ``_to_json`` passes straight through, and a bare
#: ``2`` in ``clarifications.jsonl`` reads as a count rather than as a name. The label is also
#: what the doc and the wizard UI both call it, so an int would put the ``"T"`` prefix in a
#: Python format string and a TypeScript template with nothing keeping the two in agreement.
ElicitationSeverity = Literal["T1", "T2", "T3", "T4"]

#: Who can answer this candidate — ``utku-ai-setup-wizard-gap-model.md`` § decision 2. Orthogonal
#: to :data:`ElicitationCategory`: ``business`` is a non-technical domain owner (Kindling's
#: restaurant owner, who can say what "active customer" means but has never seen a column name);
#: ``data`` is a DBA (Power Kiosk's Peruz, who can say how two tables join but must guess at
#: business intent). Exactly two values, because a gap type that needs both audiences is
#: answered by **two records**, one per tab — never by one record with a third audience value
#: that no single person can act on.
ElicitationAudience = Literal["business", "data"]


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
    #: ``ask_user``'s own ``basis`` argument (``"data_definition"`` | ``"ranking_ambiguity"``),
    #: carried onto the ledger row so an offline answer gates identically to a live one
    #: (``curator/clarification.py::fold_ledger_answer_into_corpus``). ``None`` for a record
    #: that predates this field, or is not sourced from ``ask_user`` at all (e.g. a
    #: ``source="curator"`` row) — that gate treats a missing ``basis`` as
    #: ``data_definition``-eligible, not a third state silently skipped.
    basis: str | None = None
    category: ElicitationCategory | None = None
    ui_modality: ElicitationUiModality | None = None
    target_table: str | None = None
    target_column: str | None = None
    severity: ElicitationSeverity | None = None
    audience: ElicitationAudience | None = None
    #: Ids of the candidates that must be **answered** before this one may be presented.
    #: ``()`` — the default — means nothing blocks it, which is a real state rather than an
    #: unknown one, so this defaults like ``raised_by`` (empty tuple) and not like
    #: ``answer_choice_ids`` (``None``).
    #:
    #: A tuple rather than a single id because the measured shape needs more than one: the doc's
    #: hard constraint is that a near-duplicate-disagreement question on a column must be
    #: answered before any A/B/E question about that column, and one A question ranges over
    #: *every* column matching a term — ``beer_factory`` has four price-like columns of which two
    #: are decoys, so its ``price`` question waits on two separate cluster questions, not one.
    blocked_by: tuple[str, ...] = ()
    #: Which of :attr:`blocked_by` were still unanswered at the moment this record was answered.
    #: ``None`` = never answered; ``()`` = answered with every prerequisite behind it; non-empty
    #: = answered anyway, without the warrant those prerequisites would have given it.
    #:
    #: Recorded at answer time rather than derived on read because it is **not recoverable**
    #: afterwards: once the prerequisite is answered too, :func:`unmet_prerequisites` returns
    #: ``()`` for this record and the fact that its own answer arrived first is gone. Nothing in
    #: this phase acts on it — see :func:`answer_clarification` for what a later phase is meant
    #: to do with it.
    unmet_prerequisites_at_answer: tuple[str, ...] | None = None


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
        "basis": record.basis,
        "category": record.category,
        "ui_modality": record.ui_modality,
        "target_table": record.target_table,
        "target_column": record.target_column,
        "severity": record.severity,
        "audience": record.audience,
        "blocked_by": list(record.blocked_by),
        "unmet_prerequisites_at_answer": (
            list(record.unmet_prerequisites_at_answer)
            if record.unmet_prerequisites_at_answer is not None
            else None
        ),
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
    if data.get("blocked_by") is not None:
        data["blocked_by"] = tuple(data["blocked_by"])
    if data.get("unmet_prerequisites_at_answer") is not None:
        data["unmet_prerequisites_at_answer"] = tuple(data["unmet_prerequisites_at_answer"])
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

    Built for ``GET /clarifications``: a choice-only answer leaves the record's own ``answer``
    field ``None``, and this is what turns ``answer_choice_id`` back into readable text for a
    ledger view. ``curator/clarification.py::fold_ledger_answer_into_corpus`` (Phase 1c) reuses
    it for the same reason — the text it folds into the corpus must be the same text a caller
    would have rendered, not a second reduction of ``answer_choice_id``/``answer`` that could
    disagree with it.

    **Category-tagged bypass (Setup Wizard, Phase 2), ported from v1's own
    ``resolve_answer_text`` unchanged.** A ``category``-tagged record's ``answer`` is already a
    fully composed, self-contained sentence
    (``curator/elicitation.py::compose_elicitation_answer_text``, written into ``answer`` at
    answer time by ``api/curation_routes.py::answer_clarification_route``) — the label+freeform
    concatenation below is specifically what would corrupt it: a bare picked-choice label like
    ``"sales.total_amount"`` means nothing on its own, which is the whole reason the composed
    sentence exists, and gluing the label back onto it a second time (``"sales.total_amount —
    'revenue' maps to sales.total_amount."``) is the "choice-picked answer disappears into a
    duplicate" bug class this module's docstring already names for the opposite input shape.
    """
    if record.category is not None:
        return record.answer
    label: str | None = None
    if record.answer_choice_id and record.choices:
        for choice in record.choices:
            if choice.get("id") == record.answer_choice_id:
                label = choice.get("label")
                break
    if label and record.answer:
        return f"{label} — {record.answer}"
    return label or record.answer


def unmet_prerequisites(
    record: ClarificationRecord, records: Sequence[ClarificationRecord]
) -> tuple[str, ...]:
    """Which of ``record.blocked_by`` are not yet answered, given the whole ledger.

    Empty means the record is answerable now. One definition, two readers — the wizard's
    candidate listing (which renders a still-blocked question as not-yet-answerable rather than
    hiding it, so the admin can see *why* it is waiting) and
    :func:`answer_clarification` (which stamps the result onto the answer as its warrant) — so
    "presented as blocked" and "recorded as unwarranted" cannot come to disagree.

    **Fails closed on a prerequisite that is not in the ledger at all.** A dangling id is the one
    state this must not read as "all clear": the record claims something has to be settled first
    and the ledger cannot show that it was. Reporting it keeps the missing id visible to whoever
    has to fix it, where treating it as satisfied would silently license the exact answer the
    dependency exists to hold back (the doc's objection #4: nobody shown a value checklist can
    tell they are looking at a decoy column).
    """
    if not record.blocked_by:
        return ()
    answered = {
        r.id for r in records if r.status is ClarificationRecordStatus.answered
    }
    return tuple(pid for pid in record.blocked_by if pid not in answered)


def _replace_record(
    corpus_root: Path | str, clarification_id: str, **changes: Any
) -> ClarificationRecord:
    """Load the ledger, replace the one record matching ``clarification_id`` with
    ``dataclasses.replace(record, **changes)``, write the whole ledger back, and return the
    updated record.

    Shared by :func:`answer_clarification` and :func:`mark_converted_to_corpus` — both do
    exactly this load-mutate-write-the-whole-file round trip and differ only in which fields
    change. Raises :class:`ClarificationNotFound` on an unknown id.
    """
    records = load_clarifications(corpus_root)
    for i, record in enumerate(records):
        if record.id != clarification_id:
            continue
        updated = replace(record, **changes)
        records[i] = updated
        write_clarifications(corpus_root, records)
        return updated
    raise ClarificationNotFound(f"no clarification {clarification_id!r} under {corpus_root}")


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

    Does **not** itself fold the answer into the corpus — ``api/routes.py``'s own route calls
    ``curator/clarification.py::fold_ledger_answer_into_corpus`` right after this returns (Phase
    1c); keeping that a separate call keeps this function's contract to "the answer is now on
    the record" only. Does not validate ``choice_id`` against the record's declared ``choices``
    (v1 doesn't either — see :func:`resolve_answer_text`).

    **Also stamps the answer's warrant** (``unmet_prerequisites_at_answer``): whichever of the
    record's ``blocked_by`` prerequisites were still open right now. Answering anyway is not
    refused, deliberately — ``utku-ai-setup-wizard-gap-model.md`` requires a DBA with no business
    counterpart to be able to answer the engineering half of a hybrid gap standalone (Power Kiosk
    has no business-domain expert; Kindling has no DBA, and neither pilot can fill both tabs). It
    is the *warrant* that differs, not the availability: a later phase reads a non-empty stamp and
    lands the corpus write ``draft`` + ``reliability: suspect`` instead of ``certified``. Nothing
    reads it in this phase — the fold path is unchanged and still certifies either way.

    Computed here rather than by each caller, from a read of the ledger this function then reads
    again through :func:`_replace_record`. Two full-file reads of a JSONL ledger on an
    admin-triggered write is the same "no locking or append-in-place sophistication" trade this
    module already makes everywhere else, and it buys the invariant that *every* answer carries
    its warrant rather than only the answers whose caller remembered to pass one.

    Raises :class:`ClarificationNotFound` on an unknown id.
    """
    records = load_clarifications(corpus_root)
    # ``()`` when no record matches: :func:`_replace_record` below is the one place that decides
    # an unknown id is a :class:`ClarificationNotFound`, and it is about to.
    unmet = next(
        (unmet_prerequisites(r, records) for r in records if r.id == clarification_id), ()
    )
    return _replace_record(
        corpus_root,
        clarification_id,
        status=ClarificationRecordStatus.answered,
        answer=answer,
        answer_choice_id=choice_id,
        answer_choice_ids=tuple(choice_ids) if choice_ids is not None else None,
        answered_by=answered_by,
        unmet_prerequisites_at_answer=unmet,
    )


def mark_converted_to_corpus(corpus_root: Path | str, clarification_id: str) -> ClarificationRecord:
    """Flip ``converted_to_corpus`` to ``True`` and persist — the idempotency marker
    :func:`~governed_bi.curator.clarification.fold_ledger_answer_into_corpus` sets once it has
    actually folded a record's answer into the corpus, so a second fold attempt on the same
    record is a no-op (v1 had this exact field name/purpose).

    Raises :class:`ClarificationNotFound` on an unknown id.
    """
    return _replace_record(corpus_root, clarification_id, converted_to_corpus=True)


def append_if_new_scope(corpus_root: Path | str, record: ClarificationRecord) -> ClarificationRecord | None:
    """Append ``record`` to the ledger unless a record with the same ``scope`` already exists.

    Returns the appended record, or ``None`` when nothing was written. Idempotent-by-scope,
    matching ``serve/tools.py::_log_live_clarification``'s idempotent-by-id discipline for the
    live ledger write — the offline-mint equivalent for a record whose identity is its
    ``scope``, not a caller-supplied id: ``curator/elicitation.py::maybe_generate_join_followup``
    (Setup Wizard, Phase 2) mints a fresh D follow-up record from scratch on every A answer that
    triggers it, and this is what stops a second, differently-worded A answer that lands on the
    same table pair from duplicating the same join question.
    """
    records = load_clarifications(corpus_root)
    if any(r.scope == record.scope for r in records):
        return None
    records.append(record)
    write_clarifications(corpus_root, records)
    return record
