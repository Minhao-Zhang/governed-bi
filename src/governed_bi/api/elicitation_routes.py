"""Setup Wizard candidate generation: ``POST /elicitation/generate`` and
``GET /elicitation/candidates``.

**Not a curation concern, even though it used to live in ``curation_routes.py``.** These two
routes run the gap detectors and read the resulting candidate queue — they propose questions, they
do not review or fold an existing draft/conflict/clarification the way every other route in that
file does. They were parked there anyway because that module already had the one thing an
elicitation route needs (a ``make_..._router(session)`` factory mounted beside the others in
``routes.py``), and there was no reason yet to build a second file just to hold two routes.

**Split out now because the file it was parked in ran out of room.** ``curation_routes.py`` was
984 lines against ADR 0005 §6's hard 1000-line cap (``tools/check_file_length.py``) -- the split
is forced by that cap, not by a belief that these routes were badly placed before.
``settings_routes.py`` came out of the same file for the same reason, in the same commit series.
Both are factories over one ``session``, mirroring ``drafts_routes.py``'s own separate-
``APIRouter`` module and the reasoning ``curation_routes.py::make_curation_router`` gives for why
these are factories rather than a module-level ``router``.

**``_reload_assets`` and ``_clarification_row`` are imported from ``curation_routes.py``, not
duplicated.** Both routes below build on them (a fresh-off-disk read of the corpus, and the one
shared clarification-record-to-response-row shape every clarification/elicitation route uses).
``drafts_routes.py`` already imports ``_reload_assets`` the same way for the same reason —
moving either helper into a third shared module would touch every one of its other call sites in
``curation_routes.py`` for no gain ``tools/check_imports.py`` requires (it is AST-only and layers
by *package*, not by module, so an import between two files that are both in ``governed_bi.api``
carries no layer meaning at all).

No behaviour changed: both routes keep their exact path, method, request/response shape, and
docstring.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from governed_bi.api.curation_routes import _clarification_row, _reload_assets

__all__ = ["make_elicitation_router"]


def make_elicitation_router(session: Any) -> APIRouter:
    """The two elicitation routes this file declares, over one ``session``.

    A factory, not a module-level ``router`` -- see the module docstring, and
    ``browse_routes.make_router``'s identical reasoning for why.
    """
    router = APIRouter()

    @router.post("/elicitation/generate")
    def elicitation_generate(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run both candidate generators against this session's current tables and append any newly
        proposed candidates to the offline clarifications ledger.

        **Two generators, additive, not one replacing the other.** ``curator/gaps.py``'s structural
        detectors are here because the keyword generator returns an **empty list** on the German
        ``beer_factory`` corpus this backend actually serves — every one of its gates is an English
        substring match. But the keyword path finds real traps on English schemas (``app_store``'s
        ``price`` A-question is a genuine ambiguity between two real columns), so it keeps running:
        the two read different signals and neither subsumes the other.

        Order is forced, not chosen. ``detect_structural_gaps`` runs first because its near-duplicate
        output is what ``apply_cluster_dependencies`` gates the keyword candidates *with*: certifying
        a value mapping on a decoy column makes the wrong column authoritative and nobody shown a
        value checklist can tell (``utku-ai-setup-wizard-gap-model.md`` § "Presentation
        consequences"). ``blocked_by`` is stamped on the new records before they are written, so the
        dependency is persisted on the record rather than recomputed per read — which is what lets
        ``GET /elicitation/candidates`` derive ``blocked`` from the ledger alone.

        Gated the same way the ledger's own write route is (``session.corpus_root is not None``,
        no ``can_edit`` -- this is a read/propose action over the semantic layer, not the unrelated
        free-form corpus editor surface ``can_edit`` gates).

        Idempotent: a candidate whose ``scope`` already exists among prior
        ``source="elicitation_wizard"`` ledger records (open or answered) is never proposed twice.
        That filter now runs once, in ``curator/scan_report.diff_scan_against_ledger``, over both
        generators' output -- it used to run twice, inside the keyword generator for its own half and
        here for the structural half, which is two implementations of one rule over one ledger.

        **``report`` is the account a re-run owes, and it is why the filter moved.** The owner's third
        standing decision (``utku-ai-setup-wizard-gap-model.md`` § "Three owner decisions") is that a
        re-run diffs against already-confirmed content and *says so in words when nothing is new*.
        ``n_generated: 0`` does not say that: it is the same number a structurally blind detector
        returns, which is the defect ``coverage`` already answers for the other half of the sentence.
        So the response now carries ``new`` / ``still_open`` / ``settled`` / ``stranded`` counts, an
        explicit ``nothing_new`` boolean, and the sentence the wizard prints -- composed on this side
        rather than in the client, so ``curl`` and the UI read the same words. Producing it needs the
        *unfiltered* candidate set (a generator that pre-filters cannot say what it re-derived) and
        the records the corpus dedup dropped (``drop_already_answered`` returns both halves now), and
        that is the whole of the change to this route's pipeline.

        **Categories B and E read the live database, through the governed path.** Both are about a
        column's real value vocabulary, and both used to gate on ``ColumnAsset.sample_values``, which
        ``corpus/seed.py`` never populates -- so neither could fire on any live-seeded corpus.
        ``curator/elicitation.py::read_observed_values`` supplies the values instead, one
        ``serve/fetch.sample_rows`` call per keyword-gated column (bounded by ``MAX_VALUE_READS``),
        which is the same ``prepare()``-checked, ledgered executor path the live agent's own
        ``sample_rows`` tool takes. ``session.connector``/``.corpus``/``.policy`` are exactly what a
        served turn gets (``serve/session.py::Session.configurable`` hands the same three objects to
        every node), so nothing is constructed here that a turn would not also have.

        **The structural scan reads rows too**, through the same governed path: one
        ``serve/fetch.compare_column_pair`` per name-alike column pair (bounded by
        ``gaps.MAX_PAIR_COMPARISONS``), which is a row-wise ``IS DISTINCT FROM`` count and not a
        value-set read -- the two columns that made this detector necessary hold the *identical* 554
        distinct customer ids and disagree on 6 305 of 6 312 rows -- plus one
        ``serve/fetch.count_distinct_values`` per candidate join key (bounded by
        ``gaps.MAX_KEY_PROBES``), because whether a column identifies a row is the one thing the
        seeded corpus cannot say and ``pg_rename_decoy`` declares no constraint to read it from.

        ``ledger`` in the response is every attempt row from both -- named as ``GET /turns/{id}``
        already names the same thing (``routes.py``: ``"ledger": execution["attempts"]``). It is
        returned rather than appended to ``runs/serve/*.jsonl``, because that log holds *turn* records
        judged by ``register/record.py``'s required fields and a generate call is not a turn;
        synthesising the fields to make one fit would be the "field the engine does not observe"
        defect. Returning the rows keeps the property that matters -- a governed statement is never
        issued from here without its verdict being visible to the caller who caused it.

        ``coverage`` is ``GapScan.coverage``: one "ran / skipped, and why" line per structural
        detector. It exists because an empty result is otherwise indistinguishable from a
        structurally blind detector, which is exactly what this route returned on ``beer_factory``
        before -- ``n_generated: 0`` with no way to tell that every gate had been evaluated in the
        wrong language. Only the structural detectors report it: the keyword generator has no
        equivalent (its "considered" set is a word list, not a measured population), and inventing
        rows for it here would be a coverage claim nothing computed.

        **Reporting caps are absent by construction, not by ordering.** Neither generator drops a
        finding to fit a quota (``limit_per_category`` is gone), and no two detectors share a budget,
        so 93 undescribed columns cannot crowd out one disagreeing join key.
        """
        from fastapi import HTTPException

        from governed_bi.curator.candidate_rules import (
            drop_already_answered,
            enforce_audience_language,
        )
        from governed_bi.curator.clarifications import load_clarifications, write_clarifications
        from governed_bi.curator.elicitation import (
            generate_candidate_questions,
            read_observed_values,
        )
        from governed_bi.curator.elicitation_terms import read_term_cardinalities
        from governed_bi.curator.gaps import apply_cluster_dependencies, detect_structural_gaps
        from governed_bi.curator.scan_report import diff_scan_against_ledger, scan_report_payload

        if session.corpus_root is None:
            raise HTTPException(status_code=409, detail="this session has no corpus_root to write back to")

        tables = [a for a in session.assets_by_id.values() if a.asset_type.value == "table"]
        existing = load_clarifications(session.corpus_root)
        # The value read runs first because *both* halves consume it now: the keyword generator's B,
        # E and S6, and the structural scan's join detector, which asks whether two look-alike
        # columns of two tables draw on the same domain. One read per column, shared -- reading them
        # twice would double the governed statements for one fact.
        observed, value_ledger = read_observed_values(
            tables,
            session.assets_by_id,
            connector=session.connector,
            corpus=session.corpus,
            policy=session.policy,
        )
        # One governed ``count(*) / count(distinct c)`` per column whose *name* carries an ambiguous
        # business term -- 2 on ``app_store``, 0 on German ``beer_factory``. It is a second statement
        # per column rather than a wider value read because the capped distinct-value list can never
        # say how many rows a column has, and "one value per record" vs "42 values across 6 312
        # records" is the grain distinction the business half of A is built on.
        cardinalities, cardinality_ledger = read_term_cardinalities(
            tables,
            session.assets_by_id,
            connector=session.connector,
            corpus=session.corpus,
            policy=session.policy,
        )
        scan = detect_structural_gaps(
            tables,
            session.assets_by_id,
            connector=session.connector,
            corpus=session.corpus,
            policy=session.policy,
            # ``retrieve/structure.py``'s canonical, endpoint-reconciled edges -- the session already
            # holds them, and reconciling a join's ``left_table`` spelling to a table id a second
            # time here would bind an edge to the wrong table rather than merely lose it.
            join_edges=session.structure.join_edges,
            observed_values=observed,
        )
        keyword_records = generate_candidate_questions(
            tables,
            session.assets_by_id,
            observed_values=observed,
            cardinalities=cardinalities,
        )
        # Then the rules about the *presented set* rather than about any one candidate, in the order
        # they have to run: dependency stamping, then "is this already answered", then "can its
        # audience read it", then "have we asked this before". The corpus dedup runs *after* the
        # stamp because a prerequisite that is already answered is a prerequisite that is met, and
        # only the stamped list knows which records were waiting on the one being dropped --
        # `drop_already_answered` clears those edges as it goes (found live: without that,
        # suppressing an answered cluster question left two E cards permanently "Waiting" on an id in
        # no ledger).
        #
        # The scope filter runs *last*, inside the diff, and that ordering is what the report is made
        # of: everything upstream of it now sees the whole re-derived set, so "16 carried forward
        # from an earlier scan" is a measurement rather than an absence.
        #
        # `_reload_assets`, not `session.assets_by_id`: the frozen mapping is a run constant, and the
        # whole point of the dedup is that an answer folded a minute ago on this same server should
        # already have settled its question (`/corpus/conflicts` reloads for the same reason).
        kept, settled_by_corpus = drop_already_answered(
            apply_cluster_dependencies([*scan.records, *keyword_records], scan.gated_columns),
            {a.id: a for a in _reload_assets(session)},
            schema=session.db_id,
        )
        report = diff_scan_against_ledger(
            enforce_audience_language(kept), settled_by_corpus, existing
        )
        new_records = list(report.new)
        if new_records:
            write_clarifications(session.corpus_root, [*existing, *new_records])
        return {
            "generated": [_clarification_row(r) for r in new_records],
            "n_generated": len(new_records),
            "report": scan_report_payload(report),
            "ledger": [dict(row) for row in (*scan.ledger, *value_ledger, *cardinality_ledger)],
            "coverage": [
                {
                    "detector": c.detector,
                    "gap_type": c.gap_type,
                    "considered": c.considered,
                    "measured": c.measured,
                    "found": c.found,
                    "note": c.note,
                }
                for c in scan.coverage
            ],
        }


    @router.get("/elicitation/candidates")
    def elicitation_candidates() -> list[dict[str, Any]]:
        """Every Setup Wizard candidate (``source == "elicitation_wizard"``), open **and**
        answered -- the wizard needs both to show onboarding progress, unlike ``/clarifications``'s
        own optional ``status`` filter.

        ``session.corpus_root is None`` returns an empty list, matching ``/clarifications``'s own
        handling of "nothing to read here."

        **Adds a derived ``blocked``** on top of ``_clarification_row``'s persisted fields:
        ``curator/clarifications.py::unmet_prerequisites(record, records) != ()``, i.e. this
        candidate's ``blocked_by`` names a question that is not answered yet. Derived rather than
        stored for the reason ``answer_text`` beside it is — it is a fact about the ledger as a whole
        at read time, not about the row.

        Those edges are real as of the commit that wired ``curator/gaps.py`` into
        ``POST /elicitation/generate``: a near-duplicate-cluster question on a contested column is
        written with the A/B/E questions naming that column pointing at it, so ``blocked`` flips to
        ``false`` for them the moment the cluster question is answered through
        ``POST /clarifications/{id}/answer``. Before that they could only be hand-seeded.

        Computed here and not on ``_clarification_row``/``GET /clarifications`` because the
        dependency order is a constraint on the *wizard's* sequencing
        (``utku-ai-setup-wizard-gap-model.md`` § "Presentation consequences", point 2): no A/B/E
        question may be presented before the near-duplicate-cluster question that decides which of
        two look-alike columns is authoritative, or the admin is invited to certify a value mapping
        onto a decoy. ``/clarifications`` is the raw ledger view and has no ordered flow to gate.
        The client renders a blocked candidate as not-yet-answerable rather than hiding it, and
        resolves the ``blocked_by`` ids against this same list to say what it is waiting for.
        """
        from governed_bi.curator.clarifications import load_clarifications, unmet_prerequisites

        if session.corpus_root is None:
            return []
        records = load_clarifications(session.corpus_root)
        return [
            {**_clarification_row(r), "blocked": bool(unmet_prerequisites(r, records))}
            for r in records
            if r.source == "elicitation_wizard"
        ]

    return router
