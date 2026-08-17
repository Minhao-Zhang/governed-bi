"""``GET /threads/{thread_id}/raised`` and ``GET /trust-loop/metrics`` (utku-ai-trust-loop-plan.md,
tasks B-1 and C).

Two read models, one file. C's own count -- "how many became approved rules" -- is computed the
same way B-1 asks "did what this thread raised become a certified asset": reload the corpus,
recompute the expected asset id, read its current provenance. C adds "for everyone, not one
thread" and "and how many of those were later retrieved" on top; nothing about the underlying
mechanics changes, so this stays the one file that owns "read both ledgers plus the corpus plus
the turn log" rather than a second module restating the same reads. See :func:`make_raised_router`
for B-1's own reasoning about why this dependency set earns its own file at all (not
``curation_routes.py``, 984/1000 against ADR 0005 §6's hard cap; not ``feedback_routes.py``, whose
own docstring is about a *record type*, not a cross-ledger read).

**The question this answers, and only this question.** "Given a thread, what did it raise, and
what became of it?" -- read-only, over ledgers that already exist (``feedback.jsonl``,
``clarifications.jsonl``) and the corpus assets those ledgers fold into. No new write path, and
no new engine field: everything this route reports was already durable before task B touched
anything.

**Why "thread" and not "user".** This engine has no identity concept (``api/routes.py::
_identity`` falls back to the thread id when the caller supplies none, and the UI supplies
none) -- see the plan's own note on this. So "the reader who raised something" is, operationally,
the thread that raised it, and this route is keyed on ``thread_id`` rather than any notion of an
account.

**Why a new file, not ``feedback_routes.py``.** This route reads *both* ledgers -- the report
ledger (task H) and the refusal-clarification slice of the clarification ledger (task A) -- plus
the turn log, plus the corpus. Folding it into ``feedback_routes.py`` would misname the concern
the same way reusing ``draft_from_clarification`` for a report's own draft would have misnamed
provenance (see that module's own docstring): H-b's argument that a report is a different record
type from a clarification, so it gets a different module, applies just as much to a route that is
about *neither* record type specifically but about what a thread did across both. Not added to
``curation_routes.py`` either -- that file is 968/1000 lines against ADR 0005 §6's hard cap, with
no margin left for a route this size.

**How a raised item is traced back to a thread.** Neither ledger stores ``thread_id`` directly.
Both store ``turn_id`` -- ``curator/feedback.py::FeedbackRecord.turn_id`` (required, always
present) and ``curator/clarifications.py::ClarificationRecord.turn_id`` (task B-0, optional,
present only on a refusal-clarification filed after B-0 shipped) -- and the turn log
(``api/trace_store.py``) is the one place that already maps a ``turn_id`` to the ``thread_id``
it was served on. So this route reads both ledgers, looks each candidate row's ``turn_id`` up in
the turn log, and keeps only the rows whose turn belongs to the requested thread. A
refusal-clarification with no ``turn_id`` (it predates B-0) is silently excluded -- not a
different failure mode than "raised on a different thread", because this route has no way to
tell the two apart, and reporting a guess would be exactly the "field the engine does not
observe" defect this project's own docstrings (``/corpus/assumptions``, most recently) keep
naming and refusing to commit.

**"Became of it" means "is a certified asset now", nothing softer.** ``certified``, never
``proposed`` -- the plan's own words: a ``proposed`` draft is not yet a rule an admin stands
behind, and telling a reader their report changed something when it has not been approved is the
kind of claim that costs trust rather than building it. Computed by re-deriving the exact asset
id the fold path would have written (see :func:`_expected_asset_id` below) and reading its
*current* ``audit.provenance.status`` fresh off disk (:func:`~governed_bi.api.curation_routes.
_reload_assets`, the same reload every other admin-facing route in this family already uses, for
the same reason: an approval that happened moments ago in this same process must be visible here
without a restart).

**Silence on "dismissed" and "still open", by choice, not by omission.** This route's own
response *does* carry every raised item's ``status`` (open/answered/dismissed for a report;
always ``answered`` for a refusal-clarification, since ``POST /clarifications/from-refusal``
never leaves one open) -- the read model tells the whole truth. What the reader-facing surface
built on top of this (``ui/components/chat/raised-history.tsx``, task B-2) chooses to *render* is
narrower: only the ``certified`` case. A dismissed report carries no reason field explaining why
(``curator/feedback.py::dismiss_report`` takes none), so surfacing a bare "an admin dismissed
this" would read as an unexplained rejection -- worse than the silence H-3's own "an admin will
see this" already left the reader with. And "still open" adds nothing beyond that same
same-turn acknowledgment. Both are real, inspectable states this route reports; neither is a
state B-2 turns into reader-facing copy.

**No fabricated date.** The plan's own minimum phrasing is "an admin defined it on <date>", and
this route does not produce a ``<date>`` for that half of the sentence: `corpus/drafts.py::
approve_draft`` stamps no timestamp anywhere (``Provenance.built_at`` is declared and never
populated -- confirmed by reading, not assumed), so there is no *observed* certification date to
report. ``raised_at`` on a response row is ``FeedbackRecord.reported_at`` (when the reader filed
it) -- the one honest timestamp either ledger carries -- and is ``None`` for a clarification,
which has no timestamp field at all. Inventing either would be the exact defect
``/corpus/assumptions``'s own docstring already refuses for ``answered_at``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from governed_bi.api.curation_routes import _reload_assets
from governed_bi.curator.clarification import asset_digest

__all__ = ["make_raised_router", "make_trust_loop_metrics_router"]

#: How many turns ``GET /trust-loop/metrics`` scans by default, overridable via
#: ``?turn_scan_limit=``. 10,000 against this repo's own turn log at 221 turns (2026-08-16) is a
#: wide margin, not a tight fit -- picked so the default answer is "everything", not "everything
#: so far, probably". Always echoed back on the response (``scan_bound``) alongside
#: ``turns_scanned``/``possibly_truncated``, because a bounded scan that does not say so reads as
#: a complete one -- exactly the silent-truncation defect the plan's own brief for this task
#: forbids.
DEFAULT_TURN_SCAN_LIMIT = 10_000


def _expected_asset_id(prefix: str, question: str, schema: str | None) -> str:
    """The asset id the fold path would have written for ``question``, under ``prefix``.

    **Must match the two minting formulas exactly, and now shares one function with them.**
    ``curator/clarification.py::draft_from_clarification`` mints
    ``f"clarification.{schema}.{digest}"`` and ``curator/feedback.py::_report_draft`` mints
    ``f"feedback.{schema}.{digest}"``; all three read the digest from
    :func:`~governed_bi.curator.clarification.asset_digest`.

    This originally restated the hash inline, on the reasoning that one two-line expression was
    cheaper than a new export. The cost it accepted was the wrong one to accept: a drifted
    formula makes this route report ``certified: false`` for everything, forever, with no error
    -- which is *precisely* the failure the trust loop exists to prevent, a reader being told
    nothing came of what they raised when something did. Silent false negatives in the feedback
    channel are worse than loud breakage, so the shared function makes the drift impossible
    rather than merely documented.
    """
    return f"{prefix}.{schema}.{asset_digest(question)}"


def make_raised_router(session: Any, turn_log: Any) -> APIRouter:
    """The one route this file declares, over one ``session`` and the ``turn_log`` that maps a
    turn to the thread it was served on.

    **Takes ``turn_log`` too, unlike every sibling ``make_..._router`` in this package.** Every
    other curation-family router needs only the session; this is the first to also need the turn
    log ``api/routes.py::_build_app`` already threads through as its own third dependency
    (``turn_log.get_turn``/``.list_turns``, the same seam ``/audit/turns`` reads). Importing
    ``governed_bi.api.trace_store`` directly here would have worked too -- its module-level
    ``TURN_LOG_DIR`` is read fresh on every call, so a test's ``monkeypatch.setattr(trace_store,
    "TURN_LOG_DIR", ...)`` would still take effect even without this parameter -- but it would
    quietly drop the swappable-``turn_log`` seam ``make_app``'s own docstring describes ("anything
    exposing ``append_turn``, ``list_turns``, ``get_turn``..."). Taking it as a parameter, the way
    every other reader of the turn log in this codebase already does, keeps that seam real rather
    than theoretical for exactly one more caller.
    """
    router = APIRouter()

    @router.get("/threads/{thread_id}/raised")
    def raised_by_thread(thread_id: str) -> list[dict[str, Any]]:
        """Every report or refusal-clarification traceable to ``thread_id``, and whether each one
        is now a certified asset. See the module docstring for the full argument; this is the
        shape of one row:

        ``{"kind": "feedback" | "clarification", "id", "question", "status", "raised_at",
        "certified"}``.

        ``session.corpus_root is None`` returns an empty list, matching every sibling read route
        in this project's handling of "nothing to read here" (``/clarifications``,
        ``/feedback``, ``/corpus/assumptions``).
        """
        from governed_bi.api.routes import _provenance_status
        from governed_bi.curator.clarifications import load_clarifications
        from governed_bi.curator.feedback import load_feedback

        if session.corpus_root is None:
            return []

        assets_by_id = {a.id: a for a in _reload_assets(session)}

        def _certified(asset_id: str) -> bool:
            asset = assets_by_id.get(asset_id)
            return asset is not None and _provenance_status(asset) == "certified"

        rows: list[dict[str, Any]] = []

        for record in load_feedback(session.corpus_root):
            turn = turn_log.get_turn(record.turn_id)
            if turn is None or (turn.get("record") or {}).get("thread_id") != thread_id:
                continue
            rows.append(
                {
                    "kind": "feedback",
                    "id": record.id,
                    "question": record.question,
                    "status": record.status.value,
                    "raised_at": record.reported_at,
                    "certified": _certified(
                        _expected_asset_id("feedback", record.question, session.db_id)
                    ),
                }
            )

        for record in load_clarifications(session.corpus_root):
            # Only a refusal-clarification: the one kind of clarification row this reader raised
            # themselves (task A). Every other source (curator/live_chat/elicitation_wizard) was
            # raised by an admin or by the agent, never by the reader, so it is not "what this
            # thread raised" no matter whose turn it happens to reference.
            if record.source != "refusal" or not record.turn_id:
                continue
            turn = turn_log.get_turn(record.turn_id)
            if turn is None or (turn.get("record") or {}).get("thread_id") != thread_id:
                continue
            rows.append(
                {
                    "kind": "clarification",
                    "id": record.id,
                    "question": record.question,
                    "status": record.status.value,
                    "raised_at": None,
                    "certified": _certified(
                        _expected_asset_id("clarification", record.question, session.db_id)
                    ),
                }
            )

        return sorted(rows, key=lambda r: (r["raised_at"] or "", r["id"]))

    return router


# ── task C: count whether the loop turns ──────────────────────────────────────────────────────


def _refusal_counts(turn_log: Any, *, db_id: str, limit: int) -> dict[str, Any]:
    """Turn-log counter 1: refusals, by reason -- ``outcome == "refused"`` rows, grouped on
    ``terminal_reason``. Cheap: both fields (plus ``db_id``) are in ``trace_store.
    SUMMARY_FIELDS``, so this reads only ``list_turns``, never ``get_turn``.

    **Scoped to ``db_id``.** ``runs/serve/`` is one process-wide log shared across every session
    this repo has ever run a server against -- confirmed live: 156 ``beer_factory`` turns, 61
    ``app_store``, a handful of others, all in the one log this route reads. Counting every
    turn regardless of ``db_id`` would answer "how many refusals has this *log file* ever seen",
    not "how many has *this session's loop* seen" -- a different question, and the wrong one for
    a route whose other three counters (the ledgers, the corpus) are already scoped to this one
    session's ``corpus_root``. ``session.db_id`` has no default and is never optional
    (``serve/session.py::Session.db_id: str``), so there is no "no scope configured" case to
    special-case here.

    ``capped``/``crashed``/``clarification`` outcomes are excluded on purpose -- ``Outcome``
    (``register/stages.py``) keeps a refusal (the product declining on purpose) apart from a cap
    (the attempt limit, not a decision) and a crash (our bug wearing a refusal's terminal_reason
    on ``model_error``/``guardrail_error``). Counting either into "refusals" would answer a
    different question than the one this route is for.
    """
    summaries = turn_log.list_turns(limit=limit)
    by_reason: dict[str, int] = {}
    total = 0
    for summary in summaries:
        if summary.get("db_id") != db_id:
            continue
        if summary.get("outcome") != "refused":
            continue
        total += 1
        reason = summary.get("terminal_reason") or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "total": total,
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
        "turns_scanned": len(summaries),
        "scan_bound": limit,
        "possibly_truncated": len(summaries) >= limit,
    }


def _reader_entrance_counts(corpus_root: Any) -> dict[str, Any] | None:
    """Counter 2: how many refusals became reader clarifications, plus H's other entrance.

    ``None`` when this session has no ``corpus_root`` -- there is no ledger to read, which is a
    different fact from "read the ledger and found zero rows" and this route keeps the two
    distinguishable rather than collapsing both into ``0``.

    Combines two disjoint populations, not one: ``refusal_clarifications`` is task A's entrance
    (``clarifications.jsonl`` rows with ``source == "refusal"``), ``reports`` is task H's
    (every row of ``feedback.jsonl``, which has no ``source`` field at all -- H-b's whole point
    is that a report is not a clarification). Neither can double-count the other: disjoint files,
    disjoint id prefixes (``refusal-``/``elicit.``/... vs ``feedback-``).
    """
    if corpus_root is None:
        return None
    from governed_bi.curator.clarifications import load_clarifications
    from governed_bi.curator.feedback import load_feedback

    refusal_clarifications = sum(
        1 for r in load_clarifications(corpus_root) if r.source == "refusal"
    )
    reports = len(load_feedback(corpus_root))
    return {
        "refusal_clarifications": refusal_clarifications,
        "reports": reports,
        "total": refusal_clarifications + reports,
    }


#: The two id prefixes ``draft_from_clarification``/``_report_draft`` mint unconditionally --
#: ``_is_clarification_derived``'s own reasoning (``curation_routes.py``), extended to the
#: feedback producer, which the assumptions route never needed to recognise.
_READER_CHANNEL_ID_PREFIXES = ("clarification.", "feedback.")


def _is_reader_channel_asset(asset: Any) -> bool:
    """True for a ``TermAsset`` minted by ``draft_from_clarification`` or ``_report_draft`` --
    the two producers a reader's own words can reach, as opposed to ``curator``/
    ``elicitation_wizard``-sourced terms an admin wrote unprompted."""
    return asset.asset_type.value == "term" and asset.id.startswith(_READER_CHANNEL_ID_PREFIXES)


def _approved_rule_counts(session: Any) -> tuple[dict[str, Any], list[str]] | tuple[None, None]:
    """Counter 3: how many became approved rules, by ``audit.extra["source"]`` -- the field
    task C-0 (``005e66c``) exists so this counter can read at all (before it, the fold destroyed
    ``source`` and every certified reader-channel asset would have landed unstamped, reporting
    zero forever with no error).

    Returns ``(counts, reader_initiated_ids)``, or ``(None, None)`` with no ``corpus_root``.

    **Deliberately does not apply ``/corpus/assumptions``'s own ``"live_chat"`` fallback.** That
    route's docstring justifies the default for *its own*, narrower population (clarification-
    derived terms only, and only because every unstamped row there provably predates task A, when
    live_chat was the only producer able to write one). This counter's population is wider
    (clarification- **and** feedback-derived) and its job is different: a full breakdown by
    source, where mislabelling an unknown row ``"live_chat"`` would overstate that one bucket with
    no way for a reader to tell the claim from a real observation. An unstamped certified asset is
    reported under the literal string ``"unstamped"`` instead -- honest about not knowing, which
    is the plan brief's own instruction for this exact fallback.
    """
    if session.corpus_root is None:
        return None, None
    from governed_bi.api.routes import _provenance_status

    by_source: dict[str, int] = {}
    ids_by_source: dict[str, list[str]] = {}
    for asset in _reload_assets(session):
        if not _is_reader_channel_asset(asset):
            continue
        if _provenance_status(asset) != "certified":
            continue
        extra = asset.audit.extra if asset.audit is not None else {}
        source = extra.get("source") or "unstamped"
        by_source[source] = by_source.get(source, 0) + 1
        ids_by_source.setdefault(source, []).append(asset.id)

    reader_initiated_ids = ids_by_source.get("refusal", []) + ids_by_source.get("feedback", [])
    counts = {
        "by_source": dict(sorted(by_source.items())),
        "reader_initiated_total": len(reader_initiated_ids),
        "reader_initiated_ids": sorted(reader_initiated_ids),
    }
    return counts, reader_initiated_ids


def _retrieval_counts(turn_log: Any, rule_ids: list[str], *, limit: int) -> dict[str, Any]:
    """Counter 4: how many of the certified reader-initiated rules were later retrieved on a
    real turn -- the one the plan's brief names as expensive, and the one whose named field
    (``licensed``) turned out not to answer it. Both are addressed below rather than glossed over.

    **``licensed`` cannot do this.** It is govern's table allowlist
    (``serve/nodes/route_retrieve.py``: "Joins are ``pulled_in`` and never enter ``licensed``:
    that field is govern's table allowlist... and a join id in it would be a table key naming no
    table"). A ``TermAsset`` -- every clarification- and feedback-derived rule -- is never a
    table, so it can never appear there: not a data-sparsity gap but a structural one, confirmed
    both by reading ``connect_node``/``resolve_node`` and empirically, by grepping every turn this
    repo's own ``runs/serve/*.jsonl`` holds (zero occurrences of a ``clarification.``/``feedback.``
    id inside any ``licensed`` array, across 221 turns spanning 2026-08-07..2026-08-17). Building
    this counter on ``licensed`` as written would not report a small number; it would report a
    guaranteed, permanent zero -- the exact "comfortable number that is worse than no counter" the
    brief warns against.

    **What is used instead.** ``facet_hits.facet_term.hits[].asset_id`` -- ``register/record.py``
    declares ``facet_hits`` (``Stage.route``) as a real, already-persisted field, and it is the
    term-facet retrieval channel's per-turn ranked candidates, term assets only. This is a
    materially weaker claim than "licensed": a facet-term hit is a retrieval *candidate* for that
    turn's question, not confirmation the fact was rendered into the model's prompt or read by it
    (nothing in the persisted record captures final render selection for a term asset the way
    ``licensed`` does for a table). Reported as ``retrieved`` here because it is the closest
    signal this record actually carries, and every response row using it says so in ``method``
    rather than letting the field name imply more.

    **"Later" is not enforced.** ``corpus/drafts.py::approve_draft`` stamps no certification
    timestamp (``Provenance.built_at`` is declared, never populated -- confirmed by reading, the
    same fact ``make_raised_router``'s own docstring already established for B-1). So this counts
    a candidate hit on *any* scanned turn, before or after the rule was certified, because there
    is no observed timestamp to order the two by.

    Cost, stated rather than hidden: one ``get_turn`` per scanned turn (each a fresh linear scan of
    every log file, per ``get_turn``'s own docstring), because ``facet_hits`` is not in
    ``SUMMARY_FIELDS`` and ``list_turns`` alone cannot see it. ``limit`` bounds the scan and is
    always echoed back (``scan_bound``/``turns_scanned``/``possibly_truncated``) rather than
    applied silently.
    """
    wanted = set(rule_ids)
    summaries = turn_log.list_turns(limit=limit)
    seen: set[str] = set()
    if wanted:
        for summary in summaries:
            turn_id = summary.get("turn_id")
            if not turn_id:
                continue
            full = turn_log.get_turn(turn_id)
            if full is None:
                continue
            record = full.get("record") or {}
            facet_hits = record.get("facet_hits") or {}
            term_hits = (facet_hits.get("facet_term") or {}).get("hits") or ()
            for hit in term_hits:
                asset_id = hit.get("asset_id") if isinstance(hit, dict) else None
                if asset_id in wanted:
                    seen.add(str(asset_id))
    return {
        "n_retrieved": len(seen),
        "retrieved_rule_ids": sorted(seen),
        "method": (
            "facet_hits.facet_term candidate hits per scanned turn -- a retrieval-candidate "
            "signal, not confirmation the fact was delivered to the model. `licensed` (govern's "
            "table allowlist) cannot answer this at all: a TermAsset id never appears there."
        ),
        "turns_scanned": len(summaries),
        "scan_bound": limit,
        "possibly_truncated": len(summaries) >= limit,
    }


def make_trust_loop_metrics_router(session: Any, turn_log: Any) -> APIRouter:
    """``GET /trust-loop/metrics`` (task C): does the loop -- refusal/wrong-answer → reader
    entrance → approved rule → retrieved again -- actually turn, and where does it stop.

    **Read-only over ledgers that already exist.** No new write path, no new engine field: every
    number below is a projection of ``trace_store``'s turn log, ``clarifications.jsonl``,
    ``feedback.jsonl`` and the corpus's own ``audit.extra["source"]`` (task C-0). If a future
    change to this route ever needs to add a field to the served record to answer a question, that
    is the sign the audit behind this task was wrong, and the route should stop rather than grow
    one.

    **The funnel, and why it is one field and not four scattered ones.** ``funnel`` is
    ``[refusals, entrances, approved_rules, retrieved]`` in that order -- the same four numbers
    the plan's own brief asks for, arranged so a reader sees the drop-off without doing the
    arithmetic themselves (40/3/1/0 reads as a stalled loop; 40/38/35/30 does not, and the whole
    point of this task is that the two must not look alike). An entry is ``None``, never a
    fabricated ``0``, wherever the underlying section could not be measured (no ``corpus_root``)
    -- see each helper's own docstring for which counter that is and why.

    **Admin/engineer instrument, gated server-side the same way every sibling curation route
    already is** -- nothing here is reader-facing, and ``ui/lib/capabilities.ts::
    tierShowsTrustLoopMetrics`` is this route's own UI-side gate, named rather than an inline tier
    comparison for the same reason every other tier predicate in that file is.
    """
    router = APIRouter()

    @router.get("/trust-loop/metrics")
    def trust_loop_metrics(turn_scan_limit: int = DEFAULT_TURN_SCAN_LIMIT) -> dict[str, Any]:
        refusals = _refusal_counts(turn_log, db_id=session.db_id, limit=turn_scan_limit)
        entrances = _reader_entrance_counts(session.corpus_root)
        approved, reader_initiated_ids = _approved_rule_counts(session)
        retrieved = (
            None
            if reader_initiated_ids is None
            else _retrieval_counts(turn_log, reader_initiated_ids, limit=turn_scan_limit)
        )

        return {
            "refusals": refusals,
            "entrances": entrances,
            "approved_rules": approved,
            "retrieved": retrieved,
            "funnel": [
                refusals["total"],
                entrances["total"] if entrances is not None else None,
                approved["reader_initiated_total"] if approved is not None else None,
                retrieved["n_retrieved"] if retrieved is not None else None,
            ],
            "notes": [
                "`entrances`, `approved_rules` and `retrieved` are `null`, not `0`, when this "
                "session has no corpus_root to read a ledger from -- unmeasured, not measured "
                "and zero."
                if session.corpus_root is None
                else "Every ledger this route reads was reachable; see `retrieved.method` for "
                "the one counter whose signal is weaker than its name suggests.",
            ],
        }

    return router
