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

1. **Character structure of identifiers** — :func:`name_similarity`, a maximum of two
   complementary ratios over the case-folded alphanumeric run. No tokenisation into words, no
   vocabulary, no stemmer: ``stadt``/``stadtname`` and ``city``/``city_name`` score identically.
2. **Rows in the database**, read through ``serve/fetch.compare_column_pair`` — the same
   ``prepare()``-checked, ledgered path the live agent's own tools take.

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
(:data:`MAX_PAIR_COMPARISONS`), which is a different quantity with a different justification.

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
from governed_bi.curator.elicitation import ELICITATION_SOURCE, _columns_of, _live_tables, _record_id

__all__ = [
    "NEAR_DUPLICATE_SIMILARITY",
    "CARDINALITY_COMPARABILITY",
    "MAX_PAIR_COMPARISONS",
    "SEVERITY_ORDER",
    "DetectorCoverage",
    "GapScan",
    "name_similarity",
    "detect_structural_gaps",
    "apply_cluster_dependencies",
]

#: How alike two identifiers must read before their values are worth comparing.
#:
#: **Chosen by measurement, not by feel.** Swept against BIRD-Obfuscation's
#: ``trap_manifest.json`` — which names, for each injected decoy column, the ``source_column`` it
#: mimics — over ``beer_factory`` (21 pairs), ``restaurant`` (9) and ``app_store`` (6). At 0.6 the
#: detector reports 26 of the 34 manifest pairs with 4 non-manifest pairs; at 0.5 it gains 2 more
#: manifest pairs and 8 more non-manifest ones. 0.6 is where the trade stops being worth it.
#:
#: The same constant gates three different readings of "these two identifiers name the same
#: thing", which is why it is one constant: two columns of one table (a duplicate), a column of
#: one table against a column of another (a join key), and a column against its own table's name
#: (whether that column identifies rows at all).
NEAR_DUPLICATE_SIMILARITY = 0.6

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

#: Coarse physical-type classes, matched as substrings of the raw engine type.
#:
#: Two purposes, and the first is **correctness rather than precision**: Postgres raises
#: ``operator does not exist: bigint = text`` for a cross-class comparison, so an ungated pair
#: spends a governed round trip to learn nothing. Substrings because the engine's spelling varies
#: (``bigint``, ``integer``, ``double precision``, ``character varying``, ``timestamp with time
#: zone``) and enumerating dialect spellings is the kind of list that goes stale silently. Order
#: matters: ``timestamp with time zone`` must reach ``time`` before anything else claims it.
_TYPE_CLASS_MARKERS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("bool",), "boolean"),
    (("date", "time", "interval"), "temporal"),
    (("int", "numeric", "decimal", "real", "double", "float", "money", "serial"), "numeric"),
    (("char", "text", "string", "clob", "uuid", "enum"), "text"),
)


def _alphanumeric_run(name: str) -> str:
    """``name`` case-folded with every separator dropped: ``"Content Rating"`` -> ``contentrating``.

    Deliberately *not* a tokenisation. Splitting on ``_`` is what made the design phase's
    throwaway detector miss ``region``/``regionname`` and ``stadt``/``stadtname``, and a
    word-boundary rule is a claim about a language.
    """
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _trigram_dice(left: str, right: str) -> float:
    """Dice coefficient over character trigrams. Sees **reordering**.

    ``aktueller_einzelhandelspreis`` and ``einzelhandel_preis_aktuell`` are the same words in a
    different arrangement, which no containment measure notices and this one scores 0.78.
    """
    a, b = _trigrams(_alphanumeric_run(left)), _trigrams(_alphanumeric_run(right))
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _trigrams(text: str) -> frozenset[str]:
    if len(text) < 3:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[i : i + 3] for i in range(len(text) - 2))


def _longest_common_run(left: str, right: str) -> int:
    """Length of the longest character run the two names share."""
    a, b = _alphanumeric_run(left), _alphanumeric_run(right)
    best = 0
    previous = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        current = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def _longest_common_run_ratio(left: str, right: str) -> float:
    """Longest shared character run, over the shorter name's length. Sees **containment**.

    ``email`` inside ``email_adresse`` scores 1.0 where trigram overlap scores 0.46, because the
    longer name is mostly *other* characters. This is the affixing shape a migration produces —
    a column added beside another with a qualifier bolted on.

    Normalising by the *shorter* name is what buys that, and it is also this measure's weakness:
    any short name contained in a long one scores 1.0, so ``app`` inside ``app_name`` (a real
    decoy pair) and ``ort`` inside ``betriebsstandorte`` (a coincidence) are indistinguishable
    here. Telling them apart needs context this function does not have, so it is not attempted
    here — see :data:`_MIN_KEY_NAME_RUN` for the one caller where the coincidence was measured
    doing damage.
    """
    a, b = _alphanumeric_run(left), _alphanumeric_run(right)
    if not a or not b:
        return 0.0
    return _longest_common_run(left, right) / min(len(a), len(b))


def name_similarity(left: str, right: str) -> float:
    """How alike two identifiers read, in ``[0, 1]``. Language-independent by construction.

    The **maximum** of two measures because they see different shapes and neither subsumes the
    other: containment (:func:`_longest_common_run_ratio`) reaches ``email``/``email_adresse``
    and misses reordering; trigram overlap (:func:`_trigram_dice`) reaches
    ``aktueller_einzelhandelspreis``/``einzelhandel_preis_aktuell`` and misses containment. Each
    was measured against the decoy manifest below :data:`NEAR_DUPLICATE_SIMILARITY`'s threshold
    and each recovers pairs the other does not.

    A third candidate — shared ``_``-delimited tokens — was measured and **dropped**: it reached
    nothing the other two missed and it alone flagged ``in_dosen_erh_ltlich`` against
    ``in_flaschen_erh_ltlich`` (available in cans vs in bottles), because a shared naming frame
    is exactly what a family of parallel columns has.
    """
    return max(_trigram_dice(left, right), _longest_common_run_ratio(left, right))


def _type_class(column: Any) -> str:
    physical = str(getattr(column, "physical_type", None) or "").casefold()
    for markers, name in _TYPE_CLASS_MARKERS:
        if any(marker in physical for marker in markers):
            return name
    return physical or "unknown"


def _comparable_type(left: Any, right: Any) -> bool:
    return _type_class(left) == _type_class(right)


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
    max_comparisons: int = MAX_PAIR_COMPARISONS,
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
    """
    live = _live_tables(tables)
    columns_by_table = {
        table.id: [c for c in _columns_of(table, assets_by_id) if not _excluded(c)]
        for table in live
    }

    candidates = _comparison_candidates(live, columns_by_table)
    agreements, ledger, refused = _measure_pairs(
        candidates[:max_comparisons],
        assets_by_id=assets_by_id,
        connector=connector,
        corpus=corpus,
        policy=policy,
    )

    duplicates, gated = _duplicate_records(candidates[:max_comparisons], agreements, assets_by_id)
    joins, join_note = _join_records(live, columns_by_table, join_edges, agreements)
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
                measured=0,
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


def _comparison_candidates(
    live: Sequence[Any], columns_by_table: Mapping[str, list[Any]]
) -> list[tuple[Any, Any, Any, float]]:
    """``(table, left, right, similarity)`` for every pair worth a governed comparison.

    **The name gate is the only way in, and both detectors read the same measurements.** Two
    competing candidate join keys are two columns of one table, so their disagreement is this
    same comparison seen from the join side — which is why the join detector reads
    ``agreements`` rather than issuing its own statements, and why nothing bypasses this gate to
    get a pair measured.

    That invariant has a cost worth naming: the join detector can only report a T1 ambiguity
    whose competing keys are themselves name-alike. In practice they are, because both had to
    match the *same* target column's name to become candidates — but two candidates with no
    shared run between them (``acct_id`` and ``customer_ref`` for one target) would be reported
    as two T3s rather than one T1. Letting them in was tried and measured: it put a pair with
    *zero* name similarity into the comparison budget, because one three-character coincidence
    upstream (see :data:`_MIN_KEY_NAME_RUN`) is enough to make an arbitrary column a candidate.

    Sorted by similarity descending, which is what makes :data:`MAX_PAIR_COMPARISONS` degrade
    recall gracefully.
    """
    out: list[tuple[Any, Any, Any, float]] = []
    for table in live:
        columns = columns_by_table.get(table.id) or []
        for index, left in enumerate(columns):
            for right in columns[index + 1 :]:
                if not _comparable_type(left, right):
                    continue
                similarity = name_similarity(left.physical_name, right.physical_name)
                if similarity < NEAR_DUPLICATE_SIMILARITY:
                    continue
                out.append((table, left, right, similarity))
    return sorted(out, key=lambda row: (-row[3], row[0].id, row[1].id, row[2].id))


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


def _duplicate_records(
    candidates: Sequence[tuple[Any, Any, Any, float]],
    agreements: Mapping[tuple[str, str], Any],
    assets_by_id: dict[str, Any],
) -> tuple[list[ClarificationRecord], dict[str, str]]:
    """Records for measured pairs, and the columns a T1 record gates.

    Three outcomes, and the middle one is the finding this phase exists for:

    * **vocabularies not comparable** — not two versions of one fact. No record.
    * **values disagree** — T1. Both columns are type-valid and non-empty, so nothing at answer
      time can tell them apart; picking the decoy attaches data to the wrong entity for every
      question that traverses it.
    * **values agree** — T4. Redundant, not dangerous: pick either, record which.
    """
    del assets_by_id  # names come off the assets already in hand
    records: list[ClarificationRecord] = []
    gated: dict[str, str] = {}
    for table, left, right, _similarity in candidates:
        agreement = agreements.get((left.id, right.id))
        if agreement is None:
            continue
        widest = max(agreement.n_distinct_left, agreement.n_distinct_right)
        narrowest = min(agreement.n_distinct_left, agreement.n_distinct_right)
        if widest and narrowest / widest < CARDINALITY_COMPARABILITY:
            continue
        names = sorted((left.physical_name, right.physical_name))
        scope = f"elicitation:duplicate:{table.physical_name}.{names[0]}|{names[1]}"
        disagrees = agreement.n_differing > 0
        qualified = [f"{table.physical_name}.{name}" for name in names]
        record = ClarificationRecord(
            id=_record_id(scope),
            scope=scope,
            question=(
                f"`{qualified[0]}` and `{qualified[1]}` hold different values on "
                f"{agreement.n_differing} of {agreement.n_rows} rows, and read as two names for "
                "one thing. Which one is authoritative? Is the other a legacy copy, an import "
                "artefact, or a different field entirely?"
                if disagrees
                else f"`{qualified[0]}` and `{qualified[1]}` hold different values on "
                f"{agreement.n_differing} of {agreement.n_rows} rows — they agree everywhere. "
                "One of them is redundant: which should the semantic layer treat as the real "
                "one?"
            ),
            # D, not a sixth letter: the doc's D row is "join path where >=2 candidate keys
            # exist and disagree", and a disagreeing identity-ish pair within one table is
            # exactly that seen from the column side. Reusing the category keeps
            # ``compose_elicitation_answer_text``'s freeform D branch as the fold path.
            category="D",
            ui_modality="column_picker",
            severity="T1" if disagrees else "T4",
            audience="data",
            choices=(
                *({"id": name, "label": f"{name} is authoritative"} for name in qualified),
                {"id": "different_fields", "label": "They are different fields, both correct"},
            ),
            allow_freeform=True,
            target_table=table.physical_name,
            raised_by=("elicitation_wizard",),
            source=ELICITATION_SOURCE,
        )
        records.append(record)
        if disagrees:
            for name in qualified:
                gated[name] = record.id
    return records, gated


# ── S2 / D: join paths, proactively ─────────────────────────────────────────────────────────


def _unjoined_pairs(
    live: Sequence[Any], join_edges: frozenset[tuple[str, str]]
) -> list[tuple[Any, Any]]:
    """Table pairs with no declared join, in a fixed order."""
    out: list[tuple[Any, Any]] = []
    for index, left in enumerate(live):
        for right in live[index + 1 :]:
            if tuple(sorted((left.id, right.id))) in join_edges:
                continue
            out.append((left, right))
    return out


#: Shortest shared run that counts as "this column is named after this table".
#:
#: Added because the flaw was **observed, not anticipated**: on ``beer_factory``,
#: ``betriebsstandorte.ort`` scored 1.0 against its own table name, because
#: :func:`_longest_common_run_ratio` normalises by the shorter name and ``ort`` is three
#: characters that happen to sit inside ``betriebsstandorte``. That one coincidence made every
#: text column of ``standort`` a candidate key into ``betriebsstandorte`` and put a pair with
#: *zero* name similarity (``bezeichnung`` / ``ort``) into the comparison budget.
#:
#: Four, and scoped to this one predicate. A floor inside :func:`name_similarity` itself would
#: cost real findings — ``playstore.App`` / ``app_name`` is a measured decoy pair whose whole
#: shared run is three characters — and the difference is that a column-to-column match has the
#: row-level evidence behind it while a column-to-table match has nothing but the name.
_MIN_KEY_NAME_RUN = 4


def _identifies_rows(column: Any, table: Any) -> bool:
    """Whether ``column`` reads like something that identifies a row of ``table``.

    The corpus cannot answer this directly: ``is_unique``, ``role`` and ``references`` are all
    unset by the live-schema seed path, and ``nullable`` is ``true`` on every column. So the
    available signal is that a key is conventionally named after the thing it identifies —
    ``kunde_id`` for ``kunden``, ``standort_id`` for ``standort`` — which is the same
    character-level test :func:`name_similarity` already is, applied to the table's own name.
    Conventional, therefore fallible; it is a *candidate* gate whose findings are confirmed by
    row-level evidence before anything is called T1.
    """
    if _longest_common_run(column.physical_name, table.physical_name) < _MIN_KEY_NAME_RUN:
        return False
    return name_similarity(column.physical_name, table.physical_name) >= NEAR_DUPLICATE_SIMILARITY


def _candidate_keys(
    source_table: Any, target_table: Any, columns_by_table: Mapping[str, list[Any]]
) -> dict[str, list[Any]]:
    """``{target column id: source columns that could join to it}``.

    A column of ``source_table`` is a candidate key into ``target_table`` when it reads like a
    column of ``target_table`` that identifies ``target_table``'s rows. Two or more candidates
    for one target column is the ambiguity the doc's T1 ``D`` row is about.
    """
    out: dict[str, list[Any]] = {}
    for target in columns_by_table.get(target_table.id) or []:
        if not _identifies_rows(target, target_table):
            continue
        matches = [
            source
            for source in columns_by_table.get(source_table.id) or []
            if _comparable_type(source, target)
            and name_similarity(source.physical_name, target.physical_name)
            >= NEAR_DUPLICATE_SIMILARITY
        ]
        if matches:
            out[target.id] = matches
    return out


def _join_records(
    live: Sequence[Any],
    columns_by_table: Mapping[str, list[Any]],
    join_edges: frozenset[tuple[str, str]],
    agreements: Mapping[tuple[str, str], Any],
) -> tuple[list[ClarificationRecord], str]:
    """One record per ambiguous key set (T1) or per candidate key column (T3).

    **The two shapes are never collapsed, and the difference is what happens if nobody answers.**
    Two candidate keys that disagree means the engine picks one and silently attaches data to the
    wrong entity — ``transaktion.kunde_id`` against ``transaktions_kunde_id`` disagree on 6 305
    of 6 312 rows, so every per-customer answer in the schema is wrong for one of the choices.
    One candidate, or none, means the engine cannot traverse and refuses, which costs an answer
    and never corrupts one.

    **The emission unit is the column, not the pair**, following the design doc against the
    arithmetic: 28 unjoined pairs on ``beer_factory`` versus 16 FK-looking columns, and "pairs
    are combinatorial noise, columns are the actual decision". A pair with no candidate key on
    either side therefore produces no question — there is nothing grounded to ask, and inventing
    choices for it is what the grounded-choices discipline forbids. Those pairs are **counted in
    the coverage note** instead, so the distinction stays visible rather than being dropped
    silently.
    """
    records: list[ClarificationRecord] = []
    without_candidates = 0
    ambiguous_pairs = 0
    for left_table, right_table in _unjoined_pairs(live, join_edges):
        pair_records: list[ClarificationRecord] = []
        ambiguous: list[tuple[Any, Any, list[Any], Any]] = []
        for source_table in (left_table, right_table):
            other = right_table if source_table is left_table else left_table
            for target_id, sources in _candidate_keys(
                source_table, other, columns_by_table
            ).items():
                del target_id
                conflict = _disagreeing_pair(sources, agreements)
                if conflict is not None:
                    ambiguous.append((source_table, other, sources, conflict))
                    continue
                pair_records.extend(
                    _single_key_record(source_table, other, source) for source in sources
                )
        if ambiguous:
            ambiguous_pairs += 1
            records.append(_ambiguous_key_record(left_table, right_table, ambiguous))
            continue
        if not pair_records:
            without_candidates += 1
            continue
        records.extend(pair_records)
    note = (
        f"{len(_unjoined_pairs(live, join_edges))} table pairs have no declared join: "
        f"{ambiguous_pairs} carry two or more candidate keys whose values disagree (T1, a wrong "
        f"answer), and {without_candidates} have no candidate key at all (T3, a refusal — no "
        "question emitted, because there is nothing grounded to offer as a choice). The rest "
        "are asked per candidate column, not per pair."
    )
    return records, note


def _disagreeing_pair(
    sources: Sequence[Any], agreements: Mapping[tuple[str, str], Any]
) -> Any | None:
    """The first measured pair of competing keys that actually disagrees, if any.

    Reads the near-duplicate detector's measurements rather than issuing its own: competing keys
    for one target are columns of one table, so their disagreement is the same governed
    comparison, and asking twice would be two answers to one question.
    """
    for index, left in enumerate(sources):
        for right in sources[index + 1 :]:
            agreement = agreements.get((left.id, right.id)) or agreements.get((right.id, left.id))
            if agreement is not None and agreement.n_differing > 0:
                return agreement
    return None


def _ambiguous_key_record(
    left_table: Any, right_table: Any, ambiguous: Sequence[tuple[Any, Any, list[Any], Any]]
) -> ClarificationRecord:
    source_table, target_table, sources, agreement = ambiguous[0]
    ids = sorted((left_table.id, right_table.id))
    scope = f"elicitation:joinkeys:{ids[0]}|{ids[1]}"
    names = sorted(f"{source_table.physical_name}.{c.physical_name}" for c in sources)
    return ClarificationRecord(
        id=_record_id(scope),
        scope=scope,
        question=(
            f"`{left_table.physical_name}` and `{right_table.physical_name}` have no declared "
            f"join, and `{source_table.physical_name}` offers {len(sources)} columns that could "
            f"be the key into `{target_table.physical_name}`: {', '.join(names)}. They disagree "
            f"on {agreement.n_differing} of {agreement.n_rows} rows, so the wrong one attaches "
            "every row to the wrong record. Which column joins these tables?"
        ),
        category="D",
        ui_modality="column_picker",
        severity="T1",
        audience="data",
        choices=tuple({"id": name, "label": name} for name in names),
        allow_freeform=True,
        target_table=source_table.physical_name,
        raised_by=("elicitation_wizard",),
        source=ELICITATION_SOURCE,
    )


def _single_key_record(
    source_table: Any, target_table: Any, source: Any
) -> ClarificationRecord:
    scope = (
        f"elicitation:joinkey:{source_table.physical_name}.{source.physical_name}"
        f":{target_table.physical_name}"
    )
    return ClarificationRecord(
        id=_record_id(scope),
        scope=scope,
        question=(
            f"No declared join uses `{source_table.physical_name}.{source.physical_name}`, which "
            f"reads like a key into `{target_table.physical_name}`. How do "
            f"`{source_table.physical_name}` and `{target_table.physical_name}` join, and is "
            "this the column that does it?"
        ),
        category="D",
        ui_modality=None,
        severity="T3",
        audience="data",
        choices=None,
        allow_freeform=True,
        target_table=source_table.physical_name,
        target_column=source.physical_name,
        raised_by=("elicitation_wizard",),
        source=ELICITATION_SOURCE,
    )


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
                        f"Nothing in the semantic layer says what `{table.physical_name}` is. In "
                        "one line, what does one row of it represent?"
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
                    "Describe the ones whose meaning is not obvious from the name; leave the "
                    "rest."
                ),
                category="A",
                ui_modality="checklist",
                severity="T4",
                audience="data",
                choices=tuple(
                    {"id": c.physical_name, "label": f"{c.physical_name} ({_type_class(c)})"}
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
        out.append(
            replace(candidate, blocked_by=tuple(sorted(blockers))) if blockers else candidate
        )
    return out


def _column_keys(record: ClarificationRecord) -> Iterable[str]:
    if record.target_table and record.target_column:
        yield f"{record.target_table}.{record.target_column}"
    for choice in record.choices or ():
        identifier = str(choice.get("id") or "")
        if identifier.count(".") == 1:
            yield identifier


def _severity_sort_key(record: ClarificationRecord) -> tuple[int, float, str]:
    """Worst tier first, then strongest row-level evidence, then a stable tiebreak.

    Stratification is what makes "list ALL gaps" usable: an admin reads top-down and stops when
    they run out of time, and the doc's within-tier rule is that a pair disagreeing on 6 305 of
    6 312 rows outranks one disagreeing on 6 of 24.
    """
    tier = record.severity or "T4"
    index = SEVERITY_ORDER.index(tier) if tier in SEVERITY_ORDER else len(SEVERITY_ORDER)
    return (index, -_evidence_share(record.question), record.scope)


def _evidence_share(question: str) -> float:
    """``differing / rows`` recovered from a record's own evidence sentence, else 0.

    Read back out of the text rather than carried on the record because
    ``ClarificationRecord`` has no field for it and inventing one for a sort order would be a
    field nothing else reads. Anything without the sentence sorts last within its tier.
    """
    import re

    found = re.search(r"on (\d+) of (\d+) rows", question)
    if found is None:
        return 0.0
    rows = int(found.group(2))
    return int(found.group(1)) / rows if rows else 0.0
