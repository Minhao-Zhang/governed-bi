"""Structural gap detectors for the Setup Wizard: corpus shape and real data, never a word list.

``utku-ai-setup-wizard-gap-model.md`` § "Recall against the real gap surface" is what this module
answers. Measured across four schemas and 264 columns, the shipped keyword generator
(``curator/elicitation.py``) produces **six** candidates, of which two are genuinely data-derived
and both are on English schemas. On the German ``beer_factory`` corpus the running backend
actually serves, its admin-facing output is an **empty list** — while that same schema has 93 of
93 columns with no description, 28 of 36 table pairs with no declared join, and 21 injected
near-duplicate column pairs of which 19 disagree row-wise. Detected: 0 of any of them.

**The root cause is measured, not assumed.** Every gate in ``elicitation.py`` is an English
substring match (``revenue|cost|total|price|country|region|status|rating|state|…``), and all 93
German column names were matched against those lists and hit **zero** — ``kreditkartentyp``
contains ``typ``, not ``type``. A word list is a language. So every signal here is computed from
one of two sources only:

1. **Character structure of identifiers** — ``curator/gap_signals.py``, which is exactly this
   first source split into its own module: :func:`~governed_bi.curator.gap_signals.name_similarity`
   and the cheap gates that decide which pairs are worth paying for, with no connector and no
   record in sight.
2. **Rows in the database**, read through ``serve/fetch.compare_column_pair`` and
   ``serve/fetch.count_distinct_values`` — the same ``prepare()``-checked, ledgered path the
   live agent's own tools take. That is this module, and ``curator/gap_joins.py``, which is the
   one detector whose reads are chosen by a second measurement and which therefore carries its
   own budget and threshold rather than sharing this file's.

**Evidence, not suspicion.** The headline detector requires *both* halves: a near-duplicate name
**and** a measured row-level disagreement. Either alone is a false-positive machine —
``created_at``/``updated_at`` are near-duplicates by name and legitimately differ, and two
columns disagreeing is unremarkable unless they looked interchangeable. A third, weaker signal
(comparable distinct-value counts) is a *precision* filter whose effect was measured rather than
argued: on ``beer_factory`` it removes 12 of 17 candidate false positives and costs nothing in
recall, because two columns cannot be two copies of one fact if one holds 554 distinct values
and the other 2.

**Caps bound cost, never findings.** The owner's 2026-08-12 decision is "list ALL gaps, don't
truncate; stratify by severity so the admin can stop at any tier", so no detector here drops a
finding to fit a quota and no two detectors share a budget — the arrangement that would let 93
undescribed columns crowd out one disagreeing join key is structurally absent, not merely
avoided by ordering. What *is* bounded is how many governed statements one admin click issues
(:data:`MAX_PAIR_COMPARISONS` here, ``gap_joins.MAX_KEY_PROBES`` there — two budgets, so that a
wide table full of look-alike pairs cannot spend the join detector's measurements), which is a
different quantity with a different justification.

**Ordering is a constraint, not a preference.** :func:`apply_cluster_dependencies` writes
``blocked_by`` from the near-duplicate detector's output onto every A/B/E question about a
contested column. Certifying a value mapping on a decoy makes the wrong column authoritative,
and nobody shown a value checklist can tell they are looking at a decoy — so this is the doc's
"hard constraint" and the near-duplicate detector has to run first, which is why it does.

**What this module does not do.** Question *phrasing* is a later phase: the text below is
serviceable and deliberately unpolished, with no audience-differentiated wording and no
business/engineering question pair. It carries the right ``severity``, ``audience``,
``blocked_by`` and evidence so that phrasing work has something correct to phrase.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from governed_bi.curator.clarifications import ClarificationRecord

# One implementation of "which tables does a scan look at, and what are their columns", shared
# with the keyword generator rather than re-derived here (ADR 0005 §6): a second answer to
# "excluded tables are skipped, in id order" is a second answer to what the wizard scans.
from governed_bi.curator.elicitation import (
    ELICITATION_SOURCE,
    _columns_of,
    _live_tables,
    _record_id,
    plain_name,
)
from governed_bi.curator.gap_joins import MAX_KEY_PROBES, join_records, key_matches, measure_keys
from governed_bi.curator.gap_signals import (
    comparison_candidates,
    evidence_strength,
    frame_siblings,
    type_class,
)

__all__ = [
    "CARDINALITY_COMPARABILITY",
    "MAX_KEY_PROBES",
    "MAX_PAIR_COMPARISONS",
    "SEVERITY_ORDER",
    "DetectorCoverage",
    "GapScan",
    "detect_structural_gaps",
    "apply_cluster_dependencies",
]

#: How comparable two columns' value vocabularies must be to be candidate copies of one fact.
#:
#: ``min(distinct_left, distinct_right) / max(...)``. A **necessary condition** rather than a
#: heuristic: whatever else is true of two spellings of one fact, they range over comparable sets
#: of values, and a pair holding 554 and 2 distinct values is two different facts that share a
#: name stem. Measured effect on ``beer_factory``: 12 of 17 candidate false positives removed,
#: 0 of 16 true findings lost. A ratio rather than equality because a partially-backfilled
#: legacy copy is a real shape and would fail an equality test.
CARDINALITY_COMPARABILITY = 0.5

#: Ceiling on governed comparison statements per :func:`detect_structural_gaps` call.
#:
#: A **cost** bound, and the distinction from a reporting cap is the whole of this module's
#: answer to ``limit_per_category``: an admin is entitled to every gap that was found, and is not
#: entitled to an unbounded number of round trips for one click. Column pairs within a table are
#: quadratic, so a 200-column table offers 19 900 of them; the name gate cuts that to a handful
#: on every schema measured (33 on ``beer_factory``), but "a handful on the schemas I measured"
#: is not a bound.
#:
#: Pairs are measured in **descending name-similarity order**, so a truncated scan is a scan of
#: the strongest candidates rather than of whichever table sorted first — the property that makes
#: truncation degrade recall gracefully instead of arbitrarily.
MAX_PAIR_COMPARISONS = 200

#: Severity tiers worst-first, so ``index()`` is a sort key. ``utku-ai-setup-wizard-gap-model.md``
#: § "Tier structure"; the strings are ``ElicitationSeverity``'s own vocabulary.
SEVERITY_ORDER: tuple[str, ...] = ("T1", "T2", "T3", "T4")


def _excluded(asset: Any) -> bool:
    return bool(getattr(getattr(asset, "governance", None), "excluded", False))


@dataclass(frozen=True, slots=True)
class DetectorCoverage:
    """One detector's "ran / skipped, and why" line.

    ``utku-ai-setup-wizard-gap-model.md`` § "The honest-report contract": an empty result is
    indistinguishable from a structurally blind detector, and on ``beer_factory`` and
    ``restaurant`` an empty result is precisely what the shipped generator returned. Silence
    about a blind detector reads as a clean bill of health, so a detector reports what it looked
    at even — especially — when it found nothing.
    """

    detector: str
    #: The gap-type row in the design doc's table (``S1``–``S6``), so a reader can look it up.
    gap_type: str
    #: Candidates the detector's cheap gate admitted.
    considered: int
    #: Of those, how many cost a governed statement.
    measured: int
    #: Records emitted.
    found: int
    note: str


@dataclass(frozen=True, slots=True)
class GapScan:
    """Everything one structural scan produced.

    ``ledger`` is separate from ``records`` for the reason ``read_observed_values`` gives: a
    governed statement is never issued without its verdict reaching the caller who caused it,
    including refusals, which have no record to carry them.
    """

    records: tuple[ClarificationRecord, ...]
    ledger: tuple[Any, ...]
    coverage: tuple[DetectorCoverage, ...]
    #: ``"{table}.{column}" -> id of the T1 cluster record that must be answered first.``
    #: Only T1 clusters appear: an agreeing cluster (T4) means either column will do, so nothing
    #: downstream has to wait on it, and blocking on one would stall a tab for a cosmetic
    #: finding.
    gated_columns: Mapping[str, str]


def detect_structural_gaps(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    *,
    connector: Any,
    corpus: Any,
    policy: Any,
    join_edges: frozenset[tuple[str, str]] = frozenset(),
    observed_values: Mapping[str, tuple[str, ...]] | None = None,
    max_comparisons: int = MAX_PAIR_COMPARISONS,
    max_key_probes: int = MAX_KEY_PROBES,
) -> GapScan:
    """Run every structural detector, near-duplicate clusters first.

    The order is forced rather than chosen: the near-duplicate detector's output is what gates
    the others (see :func:`apply_cluster_dependencies`), and the join detector reads its
    row-level measurements rather than re-issuing them — two candidate join keys of one table
    disagreeing *is* a near-duplicate finding seen from the join side, so measuring it twice
    would be two governed answers to one question.

    ``join_edges`` is ``retrieve/structure.CorpusStructure.join_edges`` — canonical, undirected,
    endpoint-reconciled table-id pairs. Passed in rather than derived from the ``JoinAsset``\\ s
    here, because reconciling a join's ``left_table`` spelling to a table id is
    ``structure.py``'s single implementation and a second one would bind an edge to the wrong
    table rather than merely losing it (ADR 0005 §2.8.2). Empty means "no joins declared", which
    is the honest reading for a caller that has no structure to offer.

    ``observed_values`` is ``curator/elicitation.read_observed_values``'s mapping — the capped
    distinct values already read for every column on the way here. The join detector needs them
    to ask whether two look-alike columns draw on the same domain, and taking them from the
    caller rather than re-reading is the difference between one value read per column and two.
    Omitted means the caller has no values to offer, and the join detector then has no evidence
    that a name match is a real reference: it emits nothing and says so in its coverage note,
    which is the honest result rather than a fallback to the name convention this replaced.
    """
    live = _live_tables(tables)
    columns_by_table = {
        table.id: [c for c in _columns_of(table, assets_by_id) if not _excluded(c)]
        for table in live
    }

    candidates = comparison_candidates(live, columns_by_table)
    agreements, ledger, refused = _measure_pairs(
        candidates[:max_comparisons],
        assets_by_id=assets_by_id,
        connector=connector,
        corpus=corpus,
        policy=policy,
    )

    duplicates, gated = _duplicate_records(
        candidates[:max_comparisons], agreements, columns_by_table
    )
    matches = key_matches(live, columns_by_table, join_edges, observed_values or {})
    keys, key_ledger, key_probes = measure_keys(
        sorted({target.id for _s, _t, target, _sources in matches})[:max_key_probes],
        assets_by_id=assets_by_id,
        connector=connector,
        corpus=corpus,
        policy=policy,
    )
    ledger.extend(key_ledger)
    joins, join_note = join_records(live, matches, keys, join_edges, agreements, key_probes)
    coverage_records, uncovered_columns = _coverage_records(live, columns_by_table)
    reliability = _reliability_records(live, columns_by_table)

    records = [*duplicates, *joins, *coverage_records, *reliability]
    return GapScan(
        records=tuple(sorted(records, key=_severity_sort_key)),
        ledger=tuple(ledger),
        coverage=(
            DetectorCoverage(
                detector="near_duplicate_disagreement",
                gap_type="S3",
                considered=len(candidates),
                measured=len(agreements) + refused,
                found=len(duplicates),
                note=(
                    f"{len(candidates)} type-compatible column pairs read alike within one "
                    f"table; {refused} comparisons refused by governance and skipped. Pure "
                    "synonyms with no shared character run are not reachable by any name "
                    "measure and are not claimed."
                ),
            ),
            DetectorCoverage(
                detector="join_path",
                gap_type="S2",
                considered=len(live) * (len(live) - 1) // 2,
                measured=key_probes,
                found=len(joins),
                note=join_note,
            ),
            DetectorCoverage(
                detector="semantic_coverage",
                gap_type="S1",
                considered=sum(len(c) for c in columns_by_table.values()) + len(live),
                measured=0,
                found=len(coverage_records),
                note=(
                    f"{uncovered_columns} columns and "
                    f"{sum(1 for t in live if not _described(t))} tables carry no description. "
                    "Emitted one batched question per table, never one per column: the volume "
                    "would drown every tier above it."
                ),
            ),
            DetectorCoverage(
                detector="low_confidence_asset",
                gap_type="S4",
                considered=sum(len(c) for c in columns_by_table.values()),
                measured=0,
                found=len(reliability),
                note=(
                    "Nothing in the seed path writes reliability.status or confidence, so this "
                    "gap type has 0 instances on any freshly-seeded corpus. It carries signal "
                    "only once the Enhancer or a structural check writes those fields."
                ),
            ),
        ),
        gated_columns=gated,
    )


# ── S3: near-duplicate columns whose values disagree ────────────────────────────────────────


def _measure_pairs(
    candidates: Sequence[tuple[Any, Any, Any, float]],
    *,
    assets_by_id: dict[str, Any],
    connector: Any,
    corpus: Any,
    policy: Any,
) -> tuple[dict[tuple[str, str], Any], list[Any], int]:
    """One governed ``compare_column_pair`` per candidate. Refusals skip the pair, never route
    around it — and still hand back their ledger row, because a refused attempt is a governance
    decision the audit trail is owed."""
    if connector is None:
        # See ``gap_joins.measure_keys``: ``compare_column_pair`` raises on a missing connector
        # rather than manufacturing a verdict, so the caller that has no database gets no
        # row-level findings instead of a crash or a fabricated zero.
        return {}, [], 0

    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.serve.fetch import compare_column_pair

    agreements: dict[tuple[str, str], Any] = {}
    ledger: list[Any] = []
    refused = 0
    for table, left, right, _similarity in candidates:
        agreement, attempt = compare_column_pair(
            left.id,
            right.id,
            # Exactly the one table both columns belong to. There is no retrieval to derive a
            # licensed set from -- an admin asked for a scan, not a turn -- and a single-table
            # scope also keeps ``spellings_for`` from folding ``name``/``id``/``code`` into an
            # ambiguity that would refuse almost everything (``read_observed_values``' reason).
            bounds=ToolBounds(licensed=frozenset({table.id})),
            assets=assets_by_id,
            connector=connector,
            corpus=corpus,
            policy=policy,
        )
        if attempt is not None:
            ledger.append(attempt)
        if agreement is None:
            refused += 1
            continue
        agreements[(left.id, right.id)] = agreement
    return agreements, ledger, refused


def _comparable_vocabularies(left_distinct: int, right_distinct: int) -> bool:
    """Whether two columns range over comparable numbers of values.

    :data:`CARDINALITY_COMPARABILITY`'s predicate, named once because it now has two readers:
    the near-duplicate gate itself, and :func:`_parallel_frame` confirming that a look-alike
    sibling really is drawn from the same vocabulary as the pair.
    """
    widest = max(left_distinct, right_distinct)
    return not widest or min(left_distinct, right_distinct) / widest >= CARDINALITY_COMPARABILITY


def _distinct_counts(agreements: Mapping[tuple[str, str], Any]) -> dict[str, int]:
    """``{column id: distinct values}``, read off the comparisons already paid for.

    Every governed comparison reports both columns' distinct counts in the same statement (that
    is why they are in it), so the vocabulary of any column that took part in one is already
    known and :func:`_parallel_frame` costs no additional round trip.
    """
    counts: dict[str, int] = {}
    for (left_id, right_id), agreement in agreements.items():
        counts[left_id] = agreement.n_distinct_left
        counts[right_id] = agreement.n_distinct_right
    return counts


def _parallel_frame(
    columns: Sequence[Any], left: Any, right: Any, similarity: float, distinct: Mapping[str, int]
) -> Any | None:
    """The sibling that makes this pair a family of parallel facts rather than a duplicate, if any.

    Two halves, and neither works alone. ``frame_siblings`` supplies the *name* half — a third
    type-compatible column of the same table wearing the pair's shared run at least as well as
    the pair wears it. This function supplies the *evidence* half: that sibling must be drawn
    from a comparable vocabulary, or it is not a member of the same family.

    **The evidence half is what keeps recall.** ``playstore`` holds ``App``, ``app_name`` and
    ``app_category``: all three wear ``app``, so on names alone the ``App``/``app_name`` decoy
    pair is indistinguishable from a parallel frame — and the names are all there is to look at
    until you notice ``app_category`` holds 33 values against ``App``'s 9 659. Same shape on
    ``user_reviews``, where ``Sentiment_Polarity`` (6 492 values) wears the ``sentiment`` frame
    of the ``Sentiment``/``sentiment_label`` decoy pair (4 values each). Without this half both
    manifest pairs are demoted; with it neither is, on any of the three schemas measured.

    A sibling nobody measured is **not** treated as confirmation. The counts come from
    comparisons the scan already ran, so an unmeasured sibling leaves the pair at T1 — the
    status quo, which is the safe direction for a rule that can only soften a finding.
    """
    for sibling in frame_siblings(columns, left, right, similarity):
        if sibling.id not in distinct:
            continue
        if any(
            _comparable_vocabularies(distinct[sibling.id], distinct[member.id])
            for member in (left, right)
            if member.id in distinct
        ):
            return sibling
    return None


#: The choice every branch offers for "neither of these is a copy of the other".
_BOTH_CORRECT = "different_fields"


def _duplicate_wording(
    table: Any,
    qualified: Sequence[str],
    agreement: Any,
    *,
    sibling: Any | None,
    disagrees: bool,
) -> tuple[str, tuple[Mapping[str, str], ...]]:
    """The three tiers' question text and options. **What each one claims is what was measured.**

    * **T1 — disagree, no sibling.** The pair really does read as two names for one thing, so
      "which is authoritative" is the question, and the count is the evidence for it.
    * **T2 — disagree, but a third column wears the same frame.** The demoted card must stop
      asking which is authoritative: the detector no longer believes either is a decoy, so
      offering "X is authoritative" first invites an answer the evidence does not support. It
      also must not claim the sibling was measured — it was **not** compared against anything.
      ``frame_siblings`` found it by name and ``_parallel_frame`` confirmed only that its
      vocabulary is comparably sized, so the text says exactly that and no more. The previous
      wording read "…hold different values on N of M rows, and so does ``breitengrad``", which
      asserted a row-wise comparison that was never issued.
    * **T4 — agree.** The old text opened with the disagreement sentence and then contradicted
      itself eleven words later ("hold different values on 0 of N rows — they agree everywhere").
      A branch reusing another branch's frame is the same defect class as v1's worked examples,
      one template in rather than one column over.

    ``on N of M rows`` survives verbatim in the two disagreeing branches because
    :func:`_severity_sort_key` reads the ranking's evidence back out of it. T4's does not need
    it — with ``differing == 0`` both sort terms are zero either way.
    """
    left, right = qualified[0], qualified[1]
    both_correct = {"id": _BOTH_CORRECT, "label": "They are different fields, both correct"}
    if sibling is not None:
        question = (
            f"`{left}` and `{right}` hold different values on {agreement.n_differing} of "
            f"{agreement.n_rows} rows. `{table.physical_name}.{sibling.physical_name}` is named "
            "the same way and holds a comparable range of values, so the three read as parallel "
            "fields rather than as one field stored twice. Is that right, or is one of them a "
            "copy of another?"
        )
        return question, (
            both_correct,
            {"id": left, "label": f"No — `{left}` is the real one and `{right}` copies it"},
            {"id": right, "label": f"No — `{right}` is the real one and `{left}` copies it"},
        )
    authoritative = tuple(
        {"id": name, "label": f"{name} is authoritative"} for name in qualified
    )
    if disagrees:
        question = (
            f"`{left}` and `{right}` hold different values on {agreement.n_differing} of "
            f"{agreement.n_rows} rows, and read as two names for one thing. Which one is "
            "authoritative? Is the other a legacy copy, an import artefact, or a different "
            "field entirely?"
        )
        return question, (*authoritative, both_correct)
    question = (
        f"`{left}` and `{right}` agree on every one of {agreement.n_rows} rows, so one of them "
        "is redundant. Which should the semantic layer treat as the real one?"
    )
    return question, (*authoritative, both_correct)


def _duplicate_records(
    candidates: Sequence[tuple[Any, Any, Any, float]],
    agreements: Mapping[tuple[str, str], Any],
    columns_by_table: Mapping[str, list[Any]],
) -> tuple[list[ClarificationRecord], dict[str, str]]:
    """Records for measured pairs, and the columns a T1 record gates.

    Four outcomes now, and the new one is a *demotion* rather than a drop:

    * **vocabularies not comparable** — not two versions of one fact. No record.
    * **values disagree, and no third column wears the pair's naming frame** — T1. Both columns
      are type-valid and non-empty, so nothing at answer time can tell them apart; picking the
      decoy attaches data to the wrong entity for every question that traverses it.
    * **values disagree, but the pair is one of ≥3 columns wearing that frame** — T2
      (:func:`_parallel_frame`). Cans/bottles/kegs and latitude/longitude disagree row-wise
      exactly as a poisoned duplicate does, and T1's claim — that this makes *every* answer
      touching the table wrong — is simply false for them. **Demoted, never dropped**: the
      owner's "list ALL gaps" decision means a shakier finding gets a quieter label, not
      silence, and the question is re-worded to ask what it now means.
    * **values agree** — T4. Redundant, not dangerous: pick either, record which.
    """
    records: list[ClarificationRecord] = []
    gated: dict[str, str] = {}
    distinct = _distinct_counts(agreements)
    for table, left, right, similarity in candidates:
        agreement = agreements.get((left.id, right.id))
        if agreement is None:
            continue
        if not _comparable_vocabularies(agreement.n_distinct_left, agreement.n_distinct_right):
            continue
        names = sorted((left.physical_name, right.physical_name))
        scope = f"elicitation:duplicate:{table.physical_name}.{names[0]}|{names[1]}"
        disagrees = agreement.n_differing > 0
        sibling = (
            _parallel_frame(
                columns_by_table.get(table.id) or [], left, right, similarity, distinct
            )
            if disagrees
            else None
        )
        qualified = [f"{table.physical_name}.{name}" for name in names]
        question, choices = _duplicate_wording(
            table, qualified, agreement, sibling=sibling, disagrees=disagrees
        )
        record = ClarificationRecord(
            id=_record_id(scope),
            scope=scope,
            question=question,
            # D, not a sixth letter: the doc's D row is "join path where >=2 candidate keys
            # exist and disagree", and a disagreeing identity-ish pair within one table is
            # exactly that seen from the column side. Reusing the category keeps
            # ``curator/elicitation_answers.py``'s D fold path.
            category="D",
            ui_modality="column_picker",
            severity=("T2" if sibling is not None else "T1") if disagrees else "T4",
            audience="data",
            choices=choices,
            allow_freeform=True,
            target_table=table.physical_name,
            raised_by=("elicitation_wizard",),
            source=ELICITATION_SOURCE,
        )
        records.append(record)
        # Only a T1 gates: a parallel frame means neither column is a decoy of the other, so a
        # value mapping certified on either is not certified on a decoy and nothing downstream
        # has to wait. Blocking a whole tab on a T2 would be the cost without the reason.
        if disagrees and sibling is None:
            for name in qualified:
                gated[name] = record.id
    return records, gated


# ── S1: objects the semantic layer does not describe ────────────────────────────────────────


def _described(asset: Any) -> bool:
    """Whether anything beyond the seed stub says what this object is.

    ``summary`` is excluded on purpose: ``corpus/seed.py`` writes one for every asset
    mechanically (``"kunden (15 columns: kunde_id, …)"``), so a summary-based test would report
    full coverage on a corpus nobody has described at all.
    """
    return bool(getattr(asset, "body", None) or getattr(asset, "grain", None))


def _coverage_records(
    live: Sequence[Any], columns_by_table: Mapping[str, list[Any]]
) -> tuple[list[ClarificationRecord], int]:
    """T4 records, **batched per table**, plus how many columns were uncovered.

    93 of 93 columns on ``beer_factory`` carry no description, and 93 individual questions would
    push every T1 finding off the first several screens. So a table's uncovered columns are one
    question with the column list as its payload, which is the doc's "one line per table" step
    rather than a column sweep. The split by audience is by *object type*, not two questions
    about one object: a business owner can say what a table is for, and a DBA is who can say
    what a cryptic column holds.
    """
    records: list[ClarificationRecord] = []
    uncovered_total = 0
    for table in live:
        if not _described(table):
            scope = f"elicitation:describetable:{table.id}"
            records.append(
                ClarificationRecord(
                    id=_record_id(scope),
                    scope=scope,
                    question=(
                        f"Nothing on file says what your {plain_name(table.physical_name)} "
                        "records are. In one line, what does a single entry in it represent?"
                    ),
                    category="A",
                    ui_modality=None,
                    severity="T4",
                    audience="business",
                    allow_freeform=True,
                    target_table=table.physical_name,
                    raised_by=("elicitation_wizard",),
                    source=ELICITATION_SOURCE,
                )
            )
        undescribed = [c for c in columns_by_table.get(table.id) or [] if not _described(c)]
        uncovered_total += len(undescribed)
        if not undescribed:
            continue
        scope = f"elicitation:describecolumns:{table.id}"
        records.append(
            ClarificationRecord(
                id=_record_id(scope),
                scope=scope,
                question=(
                    f"{len(undescribed)} columns of `{table.physical_name}` have no description. "
                    "Check the ones whose meaning is not obvious from the name, and say what "
                    "they hold."
                ),
                category="A",
                ui_modality="checklist",
                severity="T4",
                audience="data",
                choices=tuple(
                    {"id": c.physical_name, "label": f"{c.physical_name} ({type_class(c)})"}
                    for c in undescribed
                ),
                allow_freeform=True,
                target_table=table.physical_name,
                raised_by=("elicitation_wizard",),
                source=ELICITATION_SOURCE,
            )
        )
    return records, uncovered_total


# ── S4: assets something already flagged as unreliable ──────────────────────────────────────


def _reliability_records(
    live: Sequence[Any], columns_by_table: Mapping[str, list[Any]]
) -> list[ClarificationRecord]:
    """T3 records for columns carrying a ``suspect`` reliability status.

    **This cannot fire on a freshly-seeded corpus, and that is worth saying out loud rather than
    reporting as a working detector.** ``corpus/seed.py`` never sets ``reliability.status`` or
    ``confidence``, so the design phase measured 0 instances across all four schemas — confirmed
    here. It is implemented anyway because the check is four lines and because the field does get
    written once the Enhancer or a structural check runs, at which point a gap type that exists
    and is never looked at would be worse than one that reports zero.
    """
    records: list[ClarificationRecord] = []
    for table in live:
        for column in columns_by_table.get(table.id) or []:
            status = getattr(getattr(column, "reliability", None), "status", None)
            if str(getattr(status, "value", status) or "") != "suspect":
                continue
            scope = f"elicitation:reliability:{column.id}"
            records.append(
                ClarificationRecord(
                    id=_record_id(scope),
                    scope=scope,
                    question=(
                        f"`{table.physical_name}.{column.physical_name}` is flagged unreliable, "
                        "so the engine hedges on every answer that uses it. What is wrong with "
                        "it, and can it be trusted?"
                    ),
                    category="A",
                    ui_modality=None,
                    severity="T3",
                    audience="data",
                    allow_freeform=True,
                    target_table=table.physical_name,
                    target_column=column.physical_name,
                    raised_by=("elicitation_wizard",),
                    source=ELICITATION_SOURCE,
                )
            )
    return records


# ── dependency ordering ─────────────────────────────────────────────────────────────────────

#: Categories whose answer certifies something *about a column*, and which a contested column
#: therefore has to wait on. ``utku-ai-setup-wizard-gap-model.md`` § "Presentation consequences",
#: point 2, names exactly these: an A mapping, a B value list and an E/S6 exclusion all become
#: authoritative statements about whichever column they name, and a decoy is invisible from
#: inside any of them. ``D`` is absent because the cluster question *is* a D question.
_GATED_CATEGORIES: frozenset[str] = frozenset({"A", "B", "E"})


def apply_cluster_dependencies(
    candidates: Sequence[ClarificationRecord], gated_columns: Mapping[str, str]
) -> list[ClarificationRecord]:
    """Stamp ``blocked_by`` on every candidate that names a contested column.

    The doc's hard constraint, and the reason it is a constraint rather than a ranking
    preference: the design phase's own counterfactual produced a B question targeting
    ``restaurant.geografisch.regionname`` — the decoy of ``region``, 76 of 168 rows disagreeing —
    and a certified value mapping on a decoy is strictly worse than none, because it makes the
    wrong column authoritative. Nobody shown a value checklist can tell.

    A record is matched on both ``target_table``/``target_column`` and on its ``choices``,
    because the two shapes differ: B and E name one column, and an A question ranges over every
    column matching a term, so it can wait on several clusters at once — which is why
    ``blocked_by`` is a tuple. An unblocked record is returned **as-is**, not rebuilt, so
    identity survives for callers that compare records.

    **Edges are added, never replaced.** A candidate can arrive already blocked by something this
    function knows nothing about: A-eng waits on its own A-biz half
    (``curator/elicitation_terms.py``), stamped by the generator that mints the pair, and that
    edge is the whole warrant mechanism. Overwriting ``blocked_by`` here — which is what this did
    until the pair landed — would silently delete it for exactly the A questions that also name a
    contested column, i.e. the ones with the most reason to wait.
    """
    out: list[ClarificationRecord] = []
    for candidate in candidates:
        if candidate.category not in _GATED_CATEGORIES:
            out.append(candidate)
            continue
        blockers = {
            gated_columns[key]
            for key in _column_keys(candidate)
            if key in gated_columns and gated_columns[key] != candidate.id
        }
        added = blockers - set(candidate.blocked_by)
        out.append(
            replace(candidate, blocked_by=tuple(sorted(set(candidate.blocked_by) | blockers)))
            if added
            else candidate
        )
    return out


def _column_keys(record: ClarificationRecord) -> Iterable[str]:
    if record.target_table and record.target_column:
        yield f"{record.target_table}.{record.target_column}"
    for choice in record.choices or ():
        identifier = str(choice.get("id") or "")
        if identifier.count(".") == 1:
            yield identifier


def _severity_sort_key(record: ClarificationRecord) -> tuple[int, float, int, str]:
    """Worst tier first, then strongest evidence, then widest blast radius, then a stable tiebreak.

    **Two terms, because the two questions are different, and ranking on either alone was
    measured going wrong.** ``differing / rows`` alone — what this sorted by until it was looked
    at on real data — put ``beer_factory``'s top three T1 cards on ``geoposition``, a *three-row*
    table where "3 of 3" is a perfect score, and pushed the design doc's headline case
    (``transaktion.kunde_id`` vs ``transaktions_kunde_id``, 6 305 of 6 312) to #13.
    :func:`~governed_bi.curator.gap_signals.evidence_strength` is that share discounted by how
    little evidence supports it, which fixes the ordering at the root: on three rows two
    unrelated columns disagree on all three too, so the share is barely a measurement there.

    Absolute ``differing`` is the second term and it is *not* a tiebreak dressed up — it is the
    other half of "most severe". Evidence strength says how sure the finding is; the count says
    how many rows carry a wrong value if the admin picks wrong. Three pairs on this schema
    disagree on 100% of their rows with the discount saturated, and among those the 6 430-row
    table genuinely outranks the 6 312-row one. Multiplying the two into one number was
    considered and rejected: the product answers neither question and cannot be read off a card.
    """
    tier = record.severity or "T4"
    index = SEVERITY_ORDER.index(tier) if tier in SEVERITY_ORDER else len(SEVERITY_ORDER)
    differing, rows = _evidence_counts(record.question)
    return (index, -evidence_strength(differing, rows), -differing, record.scope)


def _evidence_counts(question: str) -> tuple[int, int]:
    """``(differing, rows)`` recovered from a record's own evidence sentence, else ``(0, 0)``.

    Read back out of the text rather than carried on the record because
    ``ClarificationRecord`` has no field for it and inventing one for a sort order would be a
    field nothing else reads. Anything without the sentence sorts last within its tier.
    """
    import re

    found = re.search(r"on (\d+) of (\d+) rows", question)
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)
