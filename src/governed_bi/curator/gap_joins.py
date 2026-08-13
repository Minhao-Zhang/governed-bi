"""S2 / D: the join paths nobody declared, and which of them are dangerous.

Split out of ``curator/gaps.py`` at 956/1000 lines (ADR 0005 §6), and along the seam the other
three detectors do not have: this one is the only one that decides *which* governed statements
to issue on the strength of a second measurement, so it owns a budget
(:data:`MAX_KEY_PROBES`), a threshold (:data:`UNIQUE_ENOUGH`) and a read
(``serve/fetch.count_distinct_values``) that nothing else in ``gaps.py`` touches. S3 reads one
statement per name-alike pair; S1 and S4 read none. Putting all four in one file made the
near-duplicate detector's constants and the join detector's sit in one block where a reader has
to check which belongs to which.

**Why the detector was rebuilt rather than tuned.** Its candidate-key test used to be "a key is
named after what it identifies" -- ``kunde_id`` for ``kunden``. Measured live, that convention
holds for nothing on ``restaurant``, whose key is ``lokal_id`` on a table called
``allgemeine_informationen``: 5 tables, 0 declared joins, 10 unjoined pairs, **0** questions
emitted, on the schema with the worst join gap in the fixture set. And it fired on a
coincidence in the other direction, reading the four characters ``unde`` shared by
``bundesland`` and ``kunden`` as "``bundesland`` identifies a customer" and emitting a T1 about
two German state columns.

So the name now supplies only what a name can (``gap_signals.name_matched_keys``), and the two
things it was standing in for are measured:

* **does the target identify a row** -- one ``count_distinct_values`` per candidate, because
  ``Session.from_live_schema`` writes no ``is_unique`` and ``pg_rename_decoy`` declares zero
  table constraints, so there is nothing to read it off.
* **do the two columns share a domain** -- ``gap_signals.values_overlap`` over the capped value
  reads the caller already made, so it costs no round trip at all.

Measured effect, live against real Postgres: ``restaurant`` 0 -> 11 findings (3 of them T1
ambiguities on real decoy pairs), ``beer_factory`` 17 -> 6 with every junk finding gone,
``app_store`` 1 -> 3.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from governed_bi.curator.clarifications import ClarificationRecord
from governed_bi.curator.elicitation import ELICITATION_SOURCE, _record_id
from governed_bi.curator.gap_signals import name_matched_keys, unjoined_pairs, values_overlap

__all__ = ["MAX_KEY_PROBES", "UNIQUE_ENOUGH", "key_matches", "measure_keys", "join_records"]

#: Ceiling on governed *cardinality* statements per scan.
#:
#: A separate budget from ``gaps.MAX_PAIR_COMPARISONS`` rather than a shared one, for the reason
#: no detector shares a reporting quota either: a wide table full of look-alike column pairs must
#: not be able to starve the join detector of the one measurement it needs.
#:
#: The probed set is already narrow by construction -- only a column that some *other* table's
#: column both reads like and shares a value with is worth asking "does this identify a row",
#: which is two filters applied before a statement is built. Measured: 38 probes on
#: ``beer_factory`` (93 columns), 21 on ``restaurant`` (30), 25 on ``app_store`` (31). 200 is
#: the same order as the pair budget and roughly five times the widest observed need; like it,
#: the probe list is sorted deterministically so a truncated scan truncates the same way twice.
MAX_KEY_PROBES = 200

#: How close to unique a column must be to count as something that identifies a row.
#:
#: **Not 1.0, and the exception is measured.** Exact uniqueness is the clean statement and it is
#: right for every dimension key in the fixture set, but ``playstore.App`` holds 9 659 distinct
#: values over 10 840 rows -- the table has duplicate app rows -- and it is the join key
#: ``user_reviews`` references. At 1.0 that join is lost, and it is one the *retired* name
#: convention actually caught, so 1.0 would have traded a real finding for the fix.
#:
#: 0.85 sits in the gap the three schemas leave: the nearest column above it is
#: ``playstore.App`` at 0.891 (a real key, admitted) and the nearest below is
#: ``restaurant.allgemeine_informationen.bezeichnung`` at 0.793 (a restaurant *name*, rejected).
#: That band is narrow and this is a threshold fitted to three schemas -- said plainly, because
#: the honest description of the risk is that a near-unique non-key such as ``kunden.nachname``
#: (0.863) would qualify if any other table held a name-alike column whose values overlapped it.
#: On these schemas none does; on a fourth one might.
UNIQUE_ENOUGH = 0.85


def key_matches(
    live: Sequence[Any],
    columns_by_table: Mapping[str, list[Any]],
    join_edges: frozenset[tuple[str, str]],
    observed: Mapping[str, tuple[str, ...]],
) -> list[tuple[Any, Any, Any, list[Any]]]:
    """``(source table, target table, target column, source columns)`` worth measuring.

    The **name** half from ``name_matched_keys``, narrowed by the **value** half: a source column
    stays only if it shares a value with the target. Both readings come free of a new round trip
    — the values were read on the way in — and together they are what the retired
    "column is named after its own table" convention was standing in for.

    Measured on real ``beer_factory``, the value half alone removes every junk finding the
    uniqueness half admits: ``wurzelbiermarke.maissirup`` as a key into ``kunden.email`` (matched
    on the three characters ``mai``), ``kunden.{stadt,ort}`` and ``wurzelbiermarke.{stadt,land}``
    as keys into ``standort.standortname``, and ``betriebsstandorte.strassenadresse`` into
    ``kunden.stra_enadresse``. On ``restaurant`` it removes nothing at all, which is the property
    that matters: it is a filter on coincidence, not on text columns.
    """
    out: list[tuple[Any, Any, Any, list[Any]]] = []
    by_id = {c.id: c for cols in columns_by_table.values() for c in cols}
    for left_table, right_table in unjoined_pairs(live, join_edges):
        for source_table in (left_table, right_table):
            other = right_table if source_table is left_table else left_table
            for target_id, sources in name_matched_keys(
                source_table, other, columns_by_table
            ).items():
                target = by_id.get(target_id)
                if target is None:
                    continue
                shared = [
                    source
                    for source in sources
                    if values_overlap(observed.get(source.id) or (), observed.get(target_id) or ())
                ]
                if shared:
                    out.append((source_table, other, target, shared))
    return out


def measure_keys(
    column_ids: Sequence[str],
    *,
    assets_by_id: dict[str, Any],
    connector: Any,
    corpus: Any,
    policy: Any,
) -> tuple[set[str], list[Any], int]:
    """``(ids that identify a row, ledger rows, statements issued)``.

    One governed ``count_distinct_values`` per candidate target column — the cheapest honest
    answer to "does this column identify a row", given that the seed path writes no ``is_unique``
    and ``pg_rename_decoy`` declares no constraints. Refusals skip the column and still hand back
    their row.
    """
    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.serve.fetch import count_distinct_values

    unique: set[str] = set()
    ledger: list[Any] = []
    issued = 0
    for column_id in column_ids:
        column = assets_by_id.get(column_id)
        table_id = str(getattr(column, "parent_table", "") or "")
        if not table_id:
            continue
        cardinality, attempt = count_distinct_values(
            column_id,
            bounds=ToolBounds(licensed=frozenset({table_id})),
            assets=assets_by_id,
            connector=connector,
            corpus=corpus,
            policy=policy,
        )
        if attempt is not None:
            ledger.append(attempt)
            issued += 1
        if cardinality is not None and cardinality.n_rows and (
            cardinality.n_distinct / cardinality.n_rows >= UNIQUE_ENOUGH
        ):
            unique.add(column_id)
    return unique, ledger, issued


def join_records(
    live: Sequence[Any],
    matches: Sequence[tuple[Any, Any, Any, list[Any]]],
    keys: set[str],
    join_edges: frozenset[tuple[str, str]],
    agreements: Mapping[tuple[str, str], Any],
    key_probes: int,
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

    That unit is enforced by keying on the record's own ``scope``, and the reason is a defect
    found live rather than a precaution: the match map is keyed by *target* column, and a
    target table with two columns that both identify its rows (``standort.standort_id`` and
    ``standort_nummer``) matched ``kunden.ort`` twice — so six T3 records reached the ledger with
    the same scope and the same id, and the wizard rendered duplicate React keys. One source
    column joining to one table is one decision no matter how many of the target's columns it
    resembles.

    **A target has to have been measured to identify a row.** ``keys`` is the set that came back
    with as many distinct values as rows. Everything else — including every column of a
    three-row lookup table that is trivially unique but shares no value with the source, which
    ``key_matches`` has already dropped — is not a key and produces nothing.
    """
    records: list[ClarificationRecord] = []
    ambiguous_pairs = 0
    by_pair: dict[tuple[str, str], list[tuple[Any, Any, Any, list[Any]]]] = {}
    for source_table, target_table, target, sources in matches:
        if target.id not in keys:
            continue
        by_pair.setdefault(
            tuple(sorted((source_table.id, target_table.id))),  # type: ignore[arg-type]
            [],
        ).append((source_table, target_table, target, sources))
    for left_table, right_table in unjoined_pairs(live, join_edges):
        pair_records: dict[str, ClarificationRecord] = {}
        ambiguous: list[tuple[Any, Any, list[Any], Any]] = []
        for source_table, target_table, _target, sources in by_pair.get(
            tuple(sorted((left_table.id, right_table.id))), []
        ):
            conflict = _disagreeing_pair(sources, agreements)
            if conflict is not None:
                ambiguous.append((source_table, target_table, sources, conflict))
                continue
            for source in sources:
                record = _single_key_record(source_table, target_table, source)
                pair_records.setdefault(record.scope, record)
        if ambiguous:
            ambiguous_pairs += 1
            records.append(_ambiguous_key_record(left_table, right_table, ambiguous))
            continue
        records.extend(pair_records.values())
    unjoined = len(unjoined_pairs(live, join_edges))
    note = (
        f"{unjoined} table pairs have no declared join. {len(matches)} cross-table column "
        f"matches read alike *and* share a value; {key_probes} of their target columns were "
        f"measured for uniqueness and {len(keys)} identify a row. Of the pairs, "
        f"{ambiguous_pairs} carry two or more candidate keys whose values disagree (T1, a wrong "
        f"answer), and {unjoined - len(by_pair)} have no candidate key at all (T3, a refusal — "
        "no question emitted, because there is nothing grounded to offer as a choice). The rest "
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
