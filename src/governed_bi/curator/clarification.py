"""Turn an answered clarification into a corpus candidate (UtkuAI, ported).

**What v2 already has, and what it does not.** ``serve/tools.py``'s ``ask_user`` +
``serve/resume.py``'s identity-bound resume + ``POST /chat/resume`` are the full
pause/resume mechanics, built and tested on this branch already — see
``utku-ai-v2-porting-spec.md``. What has no home yet is the other half: turning an answered
question into a corpus fact, so the next question that hits the same ambiguity does not have
to ask again. This module is exactly that missing half, and nothing else — it does not touch
how a turn is served, paused, or declined.

**Decline/defer behavior is deliberately untouched.** v2 fails closed on a decline (the turn
refuses rather than guessing); UtkuAI v1 fell back to a heuristic-tagged guess. That is a
serve-behavior product decision the v2 authors already made on purpose, not a gap this port is
scoped to fill — :func:`resolved_answer_text` returns ``None`` on a decline so a caller mines
nothing, and the turn's own refusal is untouched.

**Two entry points, one fold (Phase 1c, this initiative).** :func:`fold_answered_clarification`
is the Enhancer dedup/conflict pipeline itself, factored out of
``serve/nodes/mine_corpus.py`` so a live turn's own resume and the offline
``POST /clarifications/{id}/answer`` route (via :func:`fold_ledger_answer_into_corpus`) reach
byte-identical behavior rather than two implementations of the same decision.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from governed_bi.corpus.schema import ProvenanceStatus, TermAsset
from governed_bi.register.knobs import knob_default

__all__ = [
    "resolved_answer_text",
    "draft_from_clarification",
    "fold_answered_clarification",
    "fold_ledger_answer_into_corpus",
]


def resolved_answer_text(body: Mapping[str, Any]) -> str | None:
    """The client's structured resume payload (``{answer}`` / ``{choice_id}`` / ``{declined}``)
    reduced to answer text, or ``None`` on a decline -- distinct from
    ``serve/tools.py::_clarification_answer``, which turns the same payload into what the
    *model* sees mid-turn (a sentence, even on decline, since the agent needs to know a
    disambiguation was refused). This is "is there anything to mine", not "what does the model
    read", and the two must not collapse into one string a caller then has to pattern-match.
    """
    if body.get("declined"):
        return None
    for key in ("answer", "choice_id", "text"):
        value = body.get(key)
        if value:
            return str(value)
    return None


def _truncated(text: str, cap: int | None = None) -> str:
    cap = int(knob_default("summary_max_chars")) if cap is None else cap
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def _qa_summary(question: str, answer: str) -> str:
    """``question — answer``, trimmed to ``summary_max_chars`` **from the question end**.

    **The answer is the fact; the question is context.** Only ``summary`` is indexed (ADR 0005
    I1), so whatever is dropped here is dropped out of retrieval entirely -- and truncating
    ``f"{question} — {answer}"`` from the right drops the answer, which is the only part a later
    turn needs. Found live on a real ``POST /clarifications/{id}/answer``: a Setup Wizard
    question is a sentence or two of context, so it consumed the whole 250-character budget on
    its own and the corpus fact that came back said nothing but the question. The record read
    ``converted_to_corpus: true`` and there was nothing retrievable behind it.

    An answer that does not fit on its own is truncated in its own right rather than dropping
    the question first and then overflowing anyway -- the same direction, applied once more.
    """
    tail = f" — {answer}"
    cap = int(knob_default("summary_max_chars"))
    if len(tail) >= cap:
        return _truncated(answer)
    return _truncated(question, cap - len(tail)) + tail


def draft_from_clarification(question: str, answer: str, *, schema: str) -> TermAsset:
    """One clarification Q&A as a :class:`TermAsset` draft.

    ``TermAsset`` over the other seven types because it is the one asset whose contract
    ("a phrase, and what it refers to") does not presuppose a formula, a join, or a bound
    column -- a live clarification answer can be any of those, and guessing which without a
    model call would misfile more often than a generic term captures correctly. The admin
    reviewing the drafts queue (corpus/drafts.py::approve_draft) is exactly where a
    misclassified draft gets corrected before it ever serves.

    **One type for every Setup Wizard category too, checked rather than assumed.** A wizard
    answer can be a value grouping, a default exclusion, a join key or a free-text description of
    a table, and this repo has no ``NoteAsset`` -- the eight types are schema/table/column/join/
    metric/term/few-shot/negative-example (``corpus/schema.py``). ``TermAsset.body`` is free text
    and ``summary`` is what retrieval sees, so a table description is carried perfectly well by
    this shape; what it needs is a *composed sentence* that names the object it is about, which
    is ``curator/elicitation_answers.py``'s job and not this function's.
    """
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    return TermAsset(
        id=f"clarification.{schema}.{digest}",
        name=_truncated(question),
        summary=_qa_summary(question, answer),
        body=f"Q: {question}\nA: {answer}",
    )


def _is_certified(asset: Any) -> bool:
    """Same read as ``corpus/analyst.py``'s: absence of provenance is not "certified".

    Moved here from ``serve/nodes/mine_corpus.py`` (Phase 1c, this initiative) alongside the
    one function that filters on it -- the two had no reason to live apart once a second
    caller (the offline route) needed the identical filter.
    """
    provenance = getattr(asset.audit, "provenance", None) if asset.audit is not None else None
    return provenance is not None and provenance.status is ProvenanceStatus.certified


def fold_answered_clarification(
    agent_model: Any,
    corpus_root: Path | str,
    question: str,
    answer: str,
    *,
    schema: str | None,
    known_assets: Iterable[Any],
    write_model: str | None = None,
    status: ProvenanceStatus = ProvenanceStatus.proposed,
    source: str | None = None,
) -> None:
    """Build a :class:`TermAsset` draft from one answered clarification and write it through
    the Enhancer dedup/conflict path.

    **Factored out of ``serve/nodes/mine_corpus.py`` (Phase 1c, this initiative)** so a second
    caller -- ``POST /clarifications/{id}/answer`` (``api/routes.py``, via
    :func:`fold_ledger_answer_into_corpus`) -- reaches byte-identical behavior on an offline
    answer rather than a parallel reimplementation. ``known_assets`` is filtered down to
    already-**certified** ``TermAsset``s the same way ``mine_corpus_node`` always did: a
    reworded restatement does not mint a second, unlinked draft, and a contradicting answer is
    flagged rather than silently producing a second, disagreeing certified fact once approved.
    Callers decide what ``known_assets`` means for them -- a live turn's own frozen
    ``assets_by_id`` (unchanged from before this refactor), or a fresh disk reload for a caller
    that needs same-request visibility of a fact certified moments earlier.

    Best-effort, matching the node this was extracted from: any failure building the draft, or
    any :class:`~governed_bi.curator.enhancer.EnhancerError` from a broken dedup/conflict model
    call, degrades to an unconditional plain write rather than dropping a real user answer --
    the caller is not expected to inspect what happened past "did not raise".

    ``status`` is the provenance the write lands at, forwarded to both paths so the
    Enhancer-degraded one cannot quietly write a stronger warrant than the ordinary one. It is
    ``proposed`` for every caller but :func:`fold_ledger_answer_into_corpus`'s unwarranted case.

    ``source`` (task C-0) is the caller's own :attr:`~governed_bi.curator.clarifications.
    ClarificationRecord.source` string, stamped into ``audit.extra["source"]`` on write --
    ``None`` writes nothing, matching every call site that predates this parameter. Provenance,
    not ambiguity kind: this is *who raised* the clarification, never merged into ``basis``
    (*what kind* of ambiguity it was) -- the two were ruled orthogonal at task A and merging them
    would make every count `/corpus/assumptions` and step C take ambiguous.
    """
    from governed_bi.corpus.drafts import submit_draft
    from governed_bi.curator import enhancer

    extra = {"source": source} if source else None
    try:
        draft = draft_from_clarification(question, answer, schema=schema)
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
                status=status,
                extra=extra,
            )
        except enhancer.EnhancerError:
            submit_draft(corpus_root, draft, namespace=schema, status=status, extra=extra)
    except Exception:  # noqa: BLE001 -- mining is best-effort, never fatal to the caller
        pass


def fold_ledger_answer_into_corpus(
    record: Any,
    *,
    agent_model: Any,
    corpus_root: Path | str,
    schema: str | None,
    known_assets: Iterable[Any],
    write_model: str | None = None,
) -> Any:
    """Fold one *offline-answered* :class:`~governed_bi.curator.clarifications.ClarificationRecord`
    into the corpus via :func:`fold_answered_clarification` -- the offline ledger's own entry
    point into the same Enhancer path a live turn's resume takes -- then mark it
    ``converted_to_corpus`` so a second call on the same record is a no-op. Returns the
    (possibly updated) record.

    **Basis gate, mirrored exactly from ``serve/nodes/mine_corpus.py``'s live-turn gate.**
    ``basis == "ranking_ambiguity"`` folds nothing -- a per-turn judgment call, never a durable
    schema fact, whether the answer arrives live or offline. ``None``/missing ``basis`` (a
    record that predates this field, or is not sourced from ``ask_user`` at all -- e.g. a
    hypothetical future ``curator``-sourced record with no ``basis`` concept) is treated as
    ``data_definition``-eligible rather than silently skipped: the safest default, matching
    this session's own "don't silently drop a real answer" principle from the ``basis``
    field's own gap fix. There is no ``declined``/``deferred`` concept on this record shape at
    all (those live only on a live turn's own ``state["clarifications"]`` entries) -- an
    offline answer is, by construction, always a real answer.

    **Idempotent on ``record.converted_to_corpus``, folded synchronously, no poll step.** v1's
    ``apply_answered_clarifications_to_corpus`` polled the ledger for ``answered`` records not
    yet ``converted_to_corpus`` because multiple writers -- a human admin's
    ``POST /clarifications/{id}/answer`` *or* a live-chat answer -- could flip a record to
    ``answered`` outside any one call's own control flow. That is no longer true here:
    ``curator/clarifications.py::answer_clarification`` is this repo's only writer of
    ``status -> answered``, and ``POST /clarifications/{id}/answer`` is its only caller -- a
    live turn's resume never touches the ledger's ``status`` at all (Phase 1b only logs the
    *open* record, before ``interrupt()``). With exactly one route driving exactly one writer,
    there is nothing for a separate poll step to catch that this function's own call, made
    once right after ``answer_clarification`` returns, would miss -- so this folds
    synchronously inside that same route rather than porting v1's poll mechanism.

    **The answer's warrant, finally read.** ``ClarificationRecord.unmet_prerequisites_at_answer``
    records which of a record's ``blocked_by`` questions were still unanswered at the moment it
    was answered (``curator/clarifications.py::answer_clarification``). ``703a442`` made that
    expressible and left it unenforced; this is where it costs something.
    ``utku-ai-setup-wizard-gap-model.md`` § "Which gap types produce two audience-specific
    questions" requires it: A-eng answered with no A-biz behind it "must not land ``certified``
    … it should land ``draft`` … noting 'picked without a business definition'". Power Kiosk has
    a DBA and no business-domain expert, so this is the ordinary case there, not an edge one --
    the answer is taken, and it is taken as weaker evidence.

    Two things change and neither is a refusal:

    * **the status.** ``draft`` rather than ``proposed``, which
      ``corpus/drafts.py::approve_draft`` refuses to certify (it accepts ``proposed`` only). An
      admin cannot promote a fact whose warrant is missing by clicking Approve -- the doc's
      objection #4, that "a human is accountable only for facts they can actually verify from
      what the question showed them", made mechanical.
    * **the text.** The caveat rides in the folded sentence itself, which is where every reader
      of the fact will meet it -- ``summary`` is the one indexed field (ADR 0005 I1) and
      ``_qa_summary`` spends its budget from the question end, so an appended clause survives.
      ``ProvenanceStatus`` is a machine's answer to "may this be certified"; the sentence is the
      human's. The doc asks for ``reliability: suspect`` and that field **does not exist on the
      asset this writes**: ``Reliability`` is declared on ``ColumnAsset`` only, and
      ``draft_from_clarification`` hard-codes ``TermAsset`` for every category. So the caveat
      goes where a ``TermAsset`` can carry it rather than into a field invented to match a doc.

    A record with no ``blocked_by`` at all stamps ``()`` and is untouched by any of this, which
    is every question the wizard asks outside a hybrid pair or a contested column.
    """
    from governed_bi.curator.clarifications import mark_converted_to_corpus, resolve_answer_text

    if record.converted_to_corpus or record.basis == "ranking_ambiguity":
        return record
    answer_text = resolve_answer_text(record)
    if not answer_text or not record.question:
        return record
    unwarranted = bool(record.unmet_prerequisites_at_answer)
    if unwarranted:
        answer_text = f"{answer_text} {_UNWARRANTED_CAVEAT}"
    fold_answered_clarification(
        agent_model,
        corpus_root,
        record.question,
        answer_text,
        schema=schema,
        known_assets=known_assets,
        write_model=write_model,
        status=ProvenanceStatus.draft if unwarranted else ProvenanceStatus.proposed,
        source=record.source,
    )
    return mark_converted_to_corpus(corpus_root, record.id)


#: What an unwarranted answer's folded sentence says about itself.
#:
#: Short on purpose: ``summary`` is capped at ``summary_max_chars`` and this clause has to fit
#: beside the answer it qualifies, not displace it. It names the *shape* of what is missing
#: rather than the specific prerequisite, because the mechanism is general — an A-eng answer with
#: no business definition and a value mapping answered before its near-duplicate cluster question
#: are the same defect, and the ledger holds the ids either way.
_UNWARRANTED_CAVEAT = "(Unverified: answered before the question it depends on.)"
