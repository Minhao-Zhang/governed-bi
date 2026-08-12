"""What the identifiers and the corpus shape say: every gap signal that costs no database read.

Split out of ``curator/gaps.py`` at 929/1000 lines (ADR 0005 §6). **Not a line-count split.**
``gaps.py``'s own docstring states that every signal it computes comes from one of exactly two
sources — the character structure of identifiers, or rows in the database — and those two sources
have completely different dependencies:

* This module is the **first** source. Pure functions of names and of the corpus's declared
  shape. No connector, no ``prepare()``, no ledger row, no ``ClarificationRecord``, no severity
  tier: nothing here can refuse, cost a round trip, or decide what to ask. That is why it is
  testable by passing two strings, and why the sweep that chose
  :data:`NEAR_DUPLICATE_SIMILARITY` could be run offline over a decoy manifest.
* ``gaps.py`` is the **second** source, and everything that depends on it: the governed
  comparison, the severity/audience classification, the records, the dependency ordering.

The boundary is "does this need the database", and the cheap gates below are on this side of it
precisely because they decide *which* pairs are worth paying for. Enumerating candidates and
measuring them are separate steps for that reason (:data:`MAX_PAIR_COMPARISONS` truncates
between them), so the seam already existed in the call graph — this file only gives it a name.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

__all__ = [
    "NEAR_DUPLICATE_SIMILARITY",
    "name_similarity",
    "type_class",
    "comparable_type",
    "identifies_rows",
    "comparison_candidates",
    "unjoined_pairs",
    "candidate_keys",
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


def type_class(column: Any) -> str:
    """Which coarse class of :data:`_TYPE_CLASS_MARKERS` a column's engine type falls in.

    Also read for the column checklist's labels (``gaps.py``'s S1 detector), which is why it is
    a public name rather than the comparison gate's private helper: a reader deciding whether a
    cryptic column is worth describing wants the same coarse class the gate uses, not a second
    rendering of the raw engine spelling.
    """
    physical = str(getattr(column, "physical_type", None) or "").casefold()
    for markers, name in _TYPE_CLASS_MARKERS:
        if any(marker in physical for marker in markers):
            return name
    return physical or "unknown"


def comparable_type(left: Any, right: Any) -> bool:
    """Whether a row-wise comparison of these two columns can even execute."""
    return type_class(left) == type_class(right)


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


def identifies_rows(column: Any, table: Any) -> bool:
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


def comparison_candidates(
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

    Sorted by similarity descending, which is what makes ``gaps.MAX_PAIR_COMPARISONS`` degrade
    recall gracefully.
    """
    out: list[tuple[Any, Any, Any, float]] = []
    for table in live:
        columns = columns_by_table.get(table.id) or []
        for index, left in enumerate(columns):
            for right in columns[index + 1 :]:
                if not comparable_type(left, right):
                    continue
                similarity = name_similarity(left.physical_name, right.physical_name)
                if similarity < NEAR_DUPLICATE_SIMILARITY:
                    continue
                out.append((table, left, right, similarity))
    return sorted(out, key=lambda row: (-row[3], row[0].id, row[1].id, row[2].id))


def unjoined_pairs(
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


def candidate_keys(
    source_table: Any, target_table: Any, columns_by_table: Mapping[str, list[Any]]
) -> dict[str, list[Any]]:
    """``{target column id: source columns that could join to it}``.

    A column of ``source_table`` is a candidate key into ``target_table`` when it reads like a
    column of ``target_table`` that identifies ``target_table``'s rows. Two or more candidates
    for one target column is the ambiguity the doc's T1 ``D`` row is about.
    """
    out: dict[str, list[Any]] = {}
    for target in columns_by_table.get(target_table.id) or []:
        if not identifies_rows(target, target_table):
            continue
        matches = [
            source
            for source in columns_by_table.get(source_table.id) or []
            if comparable_type(source, target)
            and name_similarity(source.physical_name, target.physical_name)
            >= NEAR_DUPLICATE_SIMILARITY
        ]
        if matches:
            out[target.id] = matches
    return out
