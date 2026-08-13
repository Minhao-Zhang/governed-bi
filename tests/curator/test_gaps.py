"""Structural gap detectors: language-independent, evidence-gated.

**What these replace.** Every gate in ``curator/elicitation.py`` is an English keyword substring
match, and the whole German ``beer_factory`` schema -- 93 columns -- hits **zero** of them
(``kreditkartentyp`` contains ``typ``, not ``type``). Measured on the same schema, the
structural gap surface is 93/93 undescribed columns, 28 of 36 table pairs with no declared join,
and 21 injected near-duplicate column pairs of which 19 disagree row-wise. Detected by the
shipped generator: **0 of any of them.**

So these detectors key on corpus *shape* and real *data*, never on a word list, and each is
tested three ways: a unit test of the signal (``tests/curator/test_gap_signals.py``, split out
with the module), a test against ``tests/curator/gaps_fixtures.py``'s literal reproduction of the
real German schema and the real numbers its database answers with, and (for the near-duplicate
detector) a recall bar stated against BIRD-Obfuscation's own decoy manifest rather than against
the detector's own output.
"""

from __future__ import annotations

from typing import Any

from gaps_fixtures import (  # noqa: E402 - sibling fixture module, as tests/serve/ does
    BEER_FACTORY_AGREEING_DECOYS,
    BEER_FACTORY_DECOY_PAIRS,
    BEER_FACTORY_OBSERVED,
    BEER_FACTORY_SYNONYM_DECOYS,
    MeasuredConnector,
    beer_factory_assets,
)


def _scan(assets: dict[str, Any], *, connector: Any = None, **kwargs: Any) -> Any:
    """``detect_structural_gaps`` over a real ``AnalystCorpus`` and a real ``CorpusStructure``,
    as ``POST /elicitation/generate`` calls it."""
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.curator.gaps import detect_structural_gaps
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import build_structure

    structure, _problems = build_structure(list(assets.values()))
    tables = [a for a in assets.values() if a.asset_type.value == "table"]
    kwargs.setdefault("observed_values", BEER_FACTORY_OBSERVED)
    return detect_structural_gaps(
        tables,
        assets,
        connector=MeasuredConnector() if connector is None else connector,
        corpus=for_analyst(list(assets.values())),
        policy=GovernancePolicy(),
        join_edges=structure.join_edges,
        **kwargs,
    )


def _pairs(records: Any, severity: str | None = None) -> set[tuple[str, str, str]]:
    """``{(table, col_a, col_b)}`` for the duplicate-cluster records, names sorted."""
    out = set()
    for rec in records:
        if not rec.scope.startswith("elicitation:duplicate:"):
            continue
        if severity is not None and rec.severity != severity:
            continue
        target, _, names = rec.scope.rpartition(":")[2].partition(".")
        out.add((target, *sorted(names.split("|"))))
    return out


# ── near-duplicate columns whose values disagree: the T1 "poison" class ──────────────────────


def test_both_halves_are_required_a_similar_name_alone_is_not_a_finding() -> None:
    """``created_at``/``updated_at`` is the cautionary pair: near-duplicate by name and
    legitimately different. What makes a cluster *dangerous* is that two columns which look
    interchangeable are not, so the row-level evidence is not a ranking input -- it is the
    second half of the predicate."""
    assets = _identical_pair_schema(differing=0)
    scan = _scan(assets, connector=_pair_connector(n_rows=100, n_differing=0, card=(9, 9)))
    (rec,) = [r for r in scan.records if r.scope.startswith("elicitation:duplicate:")]
    assert rec.severity == "T4", "values agree, so redundant rather than poisonous"


def test_a_disagreeing_near_duplicate_pair_is_t1_for_the_data_audience() -> None:
    assets = _identical_pair_schema(differing=97)
    scan = _scan(assets, connector=_pair_connector(n_rows=100, n_differing=97, card=(9, 9)))
    (rec,) = [r for r in scan.records if r.scope.startswith("elicitation:duplicate:")]
    assert (rec.severity, rec.audience) == ("T1", "data")
    assert rec.category == "D", "a disagreeing identity-ish pair is the doc's T1 D row"
    assert "97" in rec.question and "100" in rec.question, rec.question
    assert {c["id"] for c in rec.choices or ()} >= {"orders.acct_id", "orders.acct_uid"}


def test_a_pair_with_incomparable_value_vocabularies_is_not_a_cluster() -> None:
    """The precision co-signal, and it is a *necessary* condition rather than a heuristic: two
    columns cannot be two copies of one fact if one holds 554 distinct values and the other 2.
    Measured on ``beer_factory``, this removes 12 of 17 candidate false positives and costs
    nothing in recall."""
    assets = _identical_pair_schema(differing=100)
    scan = _scan(assets, connector=_pair_connector(n_rows=100, n_differing=100, card=(100, 2)))
    assert not [r for r in scan.records if r.scope.startswith("elicitation:duplicate:")]


def test_a_type_incompatible_pair_is_never_compared() -> None:
    """Not a filter, a correctness requirement: ``bigint IS DISTINCT FROM text`` raises
    ``operator does not exist`` at the engine, so an ungated pair would spend a governed round
    trip to learn nothing."""
    connector = _pair_connector(n_rows=100, n_differing=100, card=(9, 9))
    _scan(_identical_pair_schema(differing=100, right_type="text"), connector=connector)
    assert not connector.statements


def test_columns_on_two_different_tables_are_not_a_row_wise_pair() -> None:
    """``kunden.postleitzahl`` and ``standort.postleitzahl`` share a name and are not
    duplicates -- comparing them needs a join key, which is the join detector's question."""
    connector = MeasuredConnector()
    scan = _scan(beer_factory_assets(), connector=connector)
    for table, left, right in _pairs(scan.records):
        assert table and left != right
    # Parsed, not scanned: ``IS DISTINCT FROM`` is an operator whose spelling contains ``FROM``.
    import sqlglot

    for sql in connector.statements:
        tree = sqlglot.parse_one(sql, dialect="postgres")
        assert len(list(tree.find_all(sqlglot.exp.Table))) == 1, sql
        assert not list(tree.find_all(sqlglot.exp.Join)), sql


# ── recall against BIRD-Obfuscation's own decoy manifest, on the real German schema ──────────


def test_recall_against_the_measured_decoy_pairs_on_the_real_schema() -> None:
    """The bar is the manifest, not the detector's own output.

    21 decoy pairs are declared in ``trap_manifest.json``; 2 of them agree row-wise in the
    loaded data and 3 are pure synonyms no character measure reaches. So 16 are both dangerous
    and reachable, and all 16 must come back as T1.
    """
    scan = _scan(beer_factory_assets())
    reachable = BEER_FACTORY_DECOY_PAIRS - BEER_FACTORY_SYNONYM_DECOYS - BEER_FACTORY_AGREEING_DECOYS
    assert len(reachable) == 16
    found_t1 = _pairs(scan.records, "T1")
    assert reachable <= found_t1, sorted(reachable - found_t1)

    # Named individually, because these are the four the design phase measured by hand.
    assert ("transaktion", "kunde_id", "transaktions_kunde_id") in found_t1
    assert ("kunden", "email", "email_adresse") in found_t1
    assert ("kunden", "kunde_id", "kunde_nummer") in found_t1
    assert ("wurzelbier", "standort_id", "zugehoeriger_standort_id") in found_t1


def test_the_agreeing_decoys_are_reported_as_redundant_not_as_poison() -> None:
    scan = _scan(beer_factory_assets())
    assert BEER_FACTORY_AGREEING_DECOYS <= _pairs(scan.records, "T4")
    assert not (BEER_FACTORY_AGREEING_DECOYS & _pairs(scan.records, "T1"))


def test_the_synonym_decoys_are_the_stated_ceiling_and_are_not_claimed() -> None:
    """Pinned so the detector's limit stays visible: ``stadt``/``ort`` share no character run,
    and no name-similarity measure in any language reaches it. Closing this needs a value-overlap
    signal, which is not built."""
    scan = _scan(beer_factory_assets())
    assert not (BEER_FACTORY_SYNONYM_DECOYS & _pairs(scan.records))


def test_precision_on_the_real_schema_is_pinned_so_a_regression_is_visible() -> None:
    """One non-manifest pair still comes back T1, down from four, and it is the one the
    parallel-frame rule cannot reach: ``transaktions_id`` is a primary key beside
    ``transaktions_wurzelbier_id``, its three ``transaktions_*_id`` siblings hold 554, 2 and
    6 312 distinct values against its 6 312, and only one of those is a comparable vocabulary —
    so the family is not confirmed and the pair keeps the louder label. Pinned, so a change that
    trades precision away shows up here.
    """
    scan = _scan(beer_factory_assets())
    unexpected = _pairs(scan.records, "T1") - BEER_FACTORY_DECOY_PAIRS
    assert unexpected == {
        ("transaktion", "transaktions_id", "transaktions_wurzelbier_id"),
    }, sorted(unexpected)


def test_a_family_of_parallel_columns_is_demoted_rather_than_claimed_as_a_duplicate() -> None:
    """**Three of the six wrong T1 cards an admin saw on real ``beer_factory``**, and all three
    are the class ``name_similarity``'s own docstring admits it cannot solve: cans / bottles /
    kegs, and latitude / longitude / a second longitude. Each pair disagrees row-wise exactly as
    a poisoned duplicate does, and T1's copy — "will make every answer touching this table
    wrong" — is simply false about them.

    Demoted to T2, **not dropped**: the owner's standing decision is to list all gaps, so a
    shakier finding gets a quieter label. The question text moves with the label, because a card
    that still asked "which one is authoritative?" would now be asking something the detector no
    longer believes.
    """
    scan = _scan(beer_factory_assets())
    demoted = _pairs(scan.records, "T2")
    assert demoted == {
        ("geoposition", "breitengrad", "l_ngengrad"),
        ("wurzelbiermarke", "in_dosen_erh_ltlich", "in_f_ssern_erh_ltlich"),
        ("wurzelbiermarke", "in_dosen_erh_ltlich", "in_flaschen_erh_ltlich"),
    }, sorted(demoted)
    rec = next(
        r
        for r in scan.records
        if r.scope == "elicitation:duplicate:geoposition.breitengrad|l_ngengrad"
    )
    assert "laengengrad" in rec.question, rec.question
    assert "authoritative" not in rec.question, rec.question


def test_a_demoted_pair_stops_gating_the_questions_about_its_columns() -> None:
    """A parallel frame means neither column is a decoy of the other, so a value mapping
    certified on either is not certified on a decoy — and blocking a whole tab on it would be
    the cost of the dependency gate without its reason."""
    scan = _scan(beer_factory_assets())
    demoted_ids = {r.id for r in scan.records if r.severity == "T2"}
    assert demoted_ids, "the fixture stopped producing a demoted pair"
    assert not (demoted_ids & set(scan.gated_columns.values()))
    # ``in_dosen_erh_ltlich`` is in two demoted pairs and no T1, so nothing gates it at all.
    # ``breitengrad`` is in a demoted pair *and* in the real ``breitenkoordinate`` cluster, and
    # stays gated by that one -- the gate follows the finding, not the column.
    assert "wurzelbiermarke.in_dosen_erh_ltlich" not in scan.gated_columns
    assert scan.gated_columns["geoposition.breitengrad"] == next(
        r.id
        for r in scan.records
        if r.scope == "elicitation:duplicate:geoposition.breitengrad|breitenkoordinate"
    )
    assert "transaktion.kunde_id" in scan.gated_columns


def test_the_frame_rule_needs_a_measured_vocabulary_and_not_only_a_matching_name() -> None:
    """**The recall half, and it is what the rule turns on.** ``playstore`` holds ``App``,
    ``app_name`` and ``app_category``; all three wear the ``app`` frame, so on names alone the
    ``App``/``app_name`` manifest decoy pair is indistinguishable from cans/bottles/kegs. What
    separates them is that ``app_category`` holds 33 values against ``App``'s 9 659.

    Stated on the real ``app_store`` shape and its real counts, because a fixture with two
    columns cannot show a rule about a third being rejected.
    """
    from governed_bi.corpus.schema import ColumnAsset, TableAsset

    columns = [
        ColumnAsset(
            id=f"app_store.playstore.{name}", schema="app_store",
            parent_table="app_store.playstore", physical_name=name, summary=name,
            physical_type="text", body="described",
        )
        for name in ("App", "app_name", "app_category")
    ]
    table = TableAsset(
        id="app_store.playstore", schema="app_store", physical_name="playstore",
        summary="playstore", body="Apps.", grain="one row per app",
        columns=tuple(c.id for c in columns),
    )
    assets = {a.id: a for a in [table, *columns]}
    connector = MeasuredConnector(
        {
            ("playstore", "App", "app_name"): (10840, 10840, 9659, 9659),
            ("playstore", "App", "app_category"): (10840, 10840, 9659, 33),
        }
    )
    scan = _scan(assets, connector=connector)
    assert ("playstore", "App", "app_name") in _pairs(scan.records, "T1")
    assert not _pairs(scan.records, "T2")


def test_every_governed_comparison_gets_its_own_ledger_row() -> None:
    connector = MeasuredConnector()
    scan = _scan(beer_factory_assets(), connector=connector)
    assert len(scan.ledger) == len(connector.statements)
    assert all(row["path"] == "sample" for row in scan.ledger)
    assert all(row["executed_sql"] for row in scan.ledger)


def test_the_number_of_governed_comparisons_is_capped_and_the_cap_keeps_the_best_evidence() -> None:
    """A cost bound, not a reporting bound. Pairs are measured in descending name-similarity
    order, so a truncated scan is a scan of the strongest candidates rather than of whichever
    table sorted first."""
    connector = MeasuredConnector()
    scan = _scan(beer_factory_assets(), connector=connector, max_comparisons=5)
    comparisons = [s for s in connector.statements if "IS DISTINCT FROM" in s]
    assert len(comparisons) == 5
    assert len(_pairs(scan.records)) <= 5
    assert ("kunden", "email", "email_adresse") in _pairs(scan.records, "T1")


def test_the_two_governed_budgets_are_separate_so_neither_can_starve_the_other() -> None:
    """A wide table full of look-alike pairs must not be able to spend the join detector's one
    measurement. Two bounds, two statement kinds, and truncating either leaves the other whole."""
    connector = MeasuredConnector()
    _scan(beer_factory_assets(), connector=connector, max_key_probes=3)
    kinds = [("IS DISTINCT FROM" in s, "n_distinct" in s and "n_differing" not in s)
             for s in connector.statements]
    assert sum(1 for pair, _card in kinds if pair) == 33, "every name-alike pair still measured"
    assert sum(1 for _pair, card in kinds if card) == 3, "and only three key probes"


def test_a_refused_comparison_skips_the_pair_and_keeps_its_row() -> None:
    """A refusal is never routed around. ``hard_block_suspect`` refuses at COLUMNS, the pair
    gets no record, and the refusal still gets its ledger row."""
    import dataclasses

    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.schema import Reliability, ReliabilityStatus
    from governed_bi.curator.gaps import detect_structural_gaps
    from governed_bi.govern.policy import GovernancePolicy

    assets = _identical_pair_schema(differing=97)
    assets["shop.orders.acct_uid"] = dataclasses.replace(
        assets["shop.orders.acct_uid"], reliability=Reliability(status=ReliabilityStatus.suspect)
    )
    connector = _pair_connector(n_rows=100, n_differing=97, card=(9, 9))
    scan = detect_structural_gaps(
        [a for a in assets.values() if a.asset_type.value == "table"],
        assets,
        connector=connector,
        corpus=for_analyst(list(assets.values())),
        policy=GovernancePolicy(hard_block_suspect=True),
    )
    assert not [r for r in scan.records if r.scope.startswith("elicitation:duplicate:")]
    assert [row["reason_code"] for row in scan.ledger] == ["r_column_suspect"]
    assert not connector.statements


# ── join paths: proactively proposed, and split T1 from T3 ───────────────────────────────────


def test_a_key_not_named_after_its_table_is_still_found() -> None:
    """**The complete miss this replaced**, on the shape that caused it. ``restaurant`` has 5
    tables, 0 declared joins, 10 unjoined pairs and its key is called ``lokal_id`` on a table
    called ``allgemeine_informationen`` -- so "a key is named after what it identifies" holds for
    nothing in the schema, and the detector emitted **0** questions on the worst join gap in the
    fixture set.

    Measured uniqueness has no such blind spot: ``lokal_id`` holds one value per row on both
    sides, and the two columns share their smallest values, which is what says they are the same
    domain rather than two unrelated integers.
    """
    assets = _two_tables_joined_by_an_unconventional_key()
    scan = _scan(
        assets,
        connector=MeasuredConnector(
            {}, {("allgemeine_informationen", "lokal_id"): (9590, 9590),
                 ("betrieb_informationen", "lokal_id"): (9590, 9590)}
        ),
        observed_values={
            "r.allgemeine_informationen.lokal_id": ("1", "2", "3"),
            "r.betrieb_informationen.lokal_id": ("1", "2", "3"),
        },
    )
    joins = [r for r in scan.records if r.scope.startswith("elicitation:joinkey:")]
    assert {r.scope for r in joins} == {
        "elicitation:joinkey:allgemeine_informationen.lokal_id:betrieb_informationen",
        "elicitation:joinkey:betrieb_informationen.lokal_id:allgemeine_informationen",
    }, sorted(r.scope for r in joins)
    assert {r.severity for r in joins} == {"T3"}


def test_a_name_match_with_no_shared_value_is_not_a_join_key() -> None:
    """**The junk finding this removes**, named: ``wurzelbiermarke.bundesland`` and ``land`` were
    reported as competing keys into ``kunden`` at T1, on the strength of the four characters
    ``unde`` appearing in both ``bundesland`` and ``kunden``. Two German state columns, and a
    card telling the admin that picking wrong would attach every row to the wrong customer.

    Both halves of the fix are visible here: no column of ``kunden`` identifies a row *and* is
    named like a state, and the state columns share no value with anything ``kunden`` keys on.
    """
    scan = _scan(beer_factory_assets(with_joins=False))
    scopes = {r.scope for r in scan.records if r.scope.startswith("elicitation:joinkey")}
    assert "elicitation:joinkeys:beer_factory.kunden|beer_factory.wurzelbiermarke" not in scopes
    assert not [s for s in scopes if "maissirup" in s or "bundesland" in s or ".land:" in s], scopes


def test_whether_a_column_identifies_a_row_costs_a_governed_statement() -> None:
    """The one fact the corpus cannot supply: the seed path writes no ``is_unique`` and
    ``pg_rename_decoy`` declares zero constraints, so it is counted -- on the same
    ``prepare()``-checked path, with its own ledger row, and bounded separately from the pair
    budget so neither detector can starve the other."""
    connector = MeasuredConnector()
    scan = _scan(beer_factory_assets(), connector=connector)
    probes = [s for s in connector.statements if "n_distinct" in s and "n_differing" not in s]
    assert probes, "no cardinality statement was issued at all"
    assert all("COUNT(DISTINCT" in s for s in probes), probes
    assert len(scan.ledger) == len(connector.statements)
    assert all(row["path"] == "sample" for row in scan.ledger)


def test_two_competing_candidate_keys_that_disagree_are_t1() -> None:
    """The doc's T1 ``D`` row, which nothing proposed proactively before: ``transaktion`` offers
    both ``kunde_id`` and ``transaktions_kunde_id`` as a key into ``kunden``, and they disagree
    on 6 305 of 6 312 rows. Picking the wrong one attaches every transaction to the wrong
    customer, silently."""
    assets = beer_factory_assets(with_joins=False)
    scan = _scan(assets)
    joins = {
        r.scope: r for r in scan.records if r.scope.startswith("elicitation:joinkeys:")
    }
    assert "elicitation:joinkeys:beer_factory.kunden|beer_factory.transaktion" in joins, sorted(joins)
    rec = joins["elicitation:joinkeys:beer_factory.kunden|beer_factory.transaktion"]
    assert (rec.severity, rec.audience, rec.category) == ("T1", "data", "D")
    assert {c["id"] for c in rec.choices or ()} >= {
        "transaktion.kunde_id", "transaktion.transaktions_kunde_id"
    }


def test_a_single_candidate_key_is_a_safe_failure_not_a_poison() -> None:
    """The doc's ``D'`` row. One obvious candidate and no declared join means the engine cannot
    traverse, so the cost of leaving it open is a refusal -- never a wrong number."""
    scan = _scan(beer_factory_assets(with_joins=False))
    single = [
        r for r in scan.records
        if r.scope.startswith("elicitation:joinkey:") and r.severity == "T3"
    ]
    assert single, "no per-column join question was proposed at all"
    assert {r.audience for r in single} == {"data"}
    assert all(r.category == "D" for r in single)


def test_the_t1_and_t3_join_shapes_are_never_collapsed() -> None:
    scan = _scan(beer_factory_assets(with_joins=False))
    (coverage,) = [c for c in scan.coverage if c.gap_type == "S2"]
    assert coverage.found == len(
        [r for r in scan.records if r.scope.startswith("elicitation:joinkey")]
    )
    tiers = {
        r.severity for r in scan.records if r.scope.startswith("elicitation:joinkey")
    }
    assert tiers == {"T1", "T3"}, tiers
    assert "no candidate key" in coverage.note, coverage.note


def test_a_declared_join_is_not_proposed_again() -> None:
    """``transaktion``/``kunden`` is one of the six joins hand-curated on 2026-08-10, so the
    join detector must be silent about that pair even though its keys disagree -- the
    near-duplicate detector still reports the pair, which is the right division."""
    with_joins = _scan(beer_factory_assets(with_joins=True))
    without = _scan(beer_factory_assets(with_joins=False))
    scopes = {r.scope for r in with_joins.records}
    assert "elicitation:joinkeys:beer_factory.kunden|beer_factory.transaktion" not in scopes
    assert ("transaktion", "kunde_id", "transaktions_kunde_id") in _pairs(with_joins.records, "T1")
    assert len([r for r in with_joins.records if r.scope.startswith("elicitation:joinkey")]) < len(
        [r for r in without.records if r.scope.startswith("elicitation:joinkey")]
    )


# ── coverage: tables and columns nothing in the semantic layer describes ─────────────────────


def test_uncovered_columns_are_batched_per_table_never_one_question_each() -> None:
    """93 of 93 columns on this schema carry no description. Ninety-three T4 questions would
    drown the nine that matter, so the emission unit is the table and the columns are its
    payload."""
    scan = _scan(beer_factory_assets())
    column_sweeps = [r for r in scan.records if r.scope.startswith("elicitation:describecolumns:")]
    assert len(column_sweeps) == 9, "one per table, not one per column"
    assert sum(len(r.choices or ()) for r in column_sweeps) == 93
    assert {(r.severity, r.audience) for r in column_sweeps} == {("T4", "data")}


def test_uncovered_tables_ask_the_business_audience_for_one_line_each() -> None:
    scan = _scan(beer_factory_assets())
    tables = [r for r in scan.records if r.scope.startswith("elicitation:describetable:")]
    assert len(tables) == 9
    assert {(r.severity, r.audience) for r in tables} == {("T4", "business")}


def test_a_described_table_and_column_are_not_asked_about() -> None:
    import dataclasses

    assets = beer_factory_assets()
    assets["beer_factory.kunden"] = dataclasses.replace(
        assets["beer_factory.kunden"], body="Customers.", grain="one row per customer"
    )
    assets["beer_factory.kunden.email"] = dataclasses.replace(
        assets["beer_factory.kunden.email"], body="Primary contact address."
    )
    scan = _scan(assets)
    scopes = {r.scope for r in scan.records}
    assert "elicitation:describetable:beer_factory.kunden" not in scopes
    sweep = next(r for r in scan.records if r.scope == "elicitation:describecolumns:beer_factory.kunden")
    assert "email" not in {c["id"] for c in sweep.choices or ()}


# ── low-confidence assets: honestly vacuous on any seeded corpus ─────────────────────────────


def test_the_low_confidence_detector_reports_that_it_cannot_fire_yet() -> None:
    """Nothing in the seed path writes ``reliability.status`` or ``confidence``, so this gap type
    has 0 instances on every schema measured. Reported as "ran and found nothing, and here is
    why", which is the opposite of silence -- an empty result that reads as a clean bill of
    health is the failure mode the design doc names."""
    scan = _scan(beer_factory_assets())
    (coverage,) = [c for c in scan.coverage if c.gap_type == "S4"]
    assert coverage.found == 0
    assert "reliability" in coverage.note, coverage.note
    assert not [r for r in scan.records if r.scope.startswith("elicitation:reliability:")]


def test_the_low_confidence_detector_does_fire_when_something_writes_the_field() -> None:
    import dataclasses

    from governed_bi.corpus.schema import Reliability, ReliabilityStatus

    assets = beer_factory_assets()
    assets["beer_factory.kunden.email"] = dataclasses.replace(
        assets["beer_factory.kunden.email"],
        reliability=Reliability(status=ReliabilityStatus.suspect),
    )
    scan = _scan(assets)
    (rec,) = [r for r in scan.records if r.scope.startswith("elicitation:reliability:")]
    assert (rec.severity, rec.audience) == ("T3", "data")


# ── dependency ordering: a cluster question gates every question about its columns ───────────


def test_a_value_question_on_a_cluster_column_is_blocked_until_the_cluster_is_settled() -> None:
    """The doc's hard constraint, not a ranking preference: certifying a value mapping on a
    decoy makes the wrong column authoritative, and nobody looking at a value checklist can tell
    it is a decoy."""
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.gaps import apply_cluster_dependencies

    scan = _scan(beer_factory_assets())
    candidate = ClarificationRecord(
        id="q_valuemap",
        scope="elicitation:valuemap:kunden.email_adresse",
        question="Which values count together?",
        category="B",
        severity="T2",
        audience="business",
        target_table="kunden",
        target_column="email_adresse",
        source="elicitation_wizard",
    )
    (gated,) = apply_cluster_dependencies([candidate], scan.gated_columns)
    assert gated.blocked_by, "a B question on a decoy column must wait for its cluster question"
    cluster = next(
        r for r in scan.records if r.scope == "elicitation:duplicate:kunden.email|email_adresse"
    )
    assert gated.blocked_by == (cluster.id,)


def test_a_term_question_waits_on_every_cluster_its_choices_touch() -> None:
    """One A question ranges over every column matching a term, so it can wait on more than one
    cluster -- which is why ``blocked_by`` is a tuple."""
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.gaps import apply_cluster_dependencies

    scan = _scan(beer_factory_assets())
    candidate = ClarificationRecord(
        id="q_term",
        scope="elicitation:term:preis",
        question="Which column is 'preis'?",
        category="A",
        severity="T2",
        audience="data",
        choices=(
            {"id": "wurzelbiermarke.aktueller_einzelhandelspreis", "label": "a"},
            {"id": "wurzelbiermarke.einzelhandel_preis_aktuell", "label": "b"},
            {"id": "transaktion.kaufpreis", "label": "c"},
        ),
        source="elicitation_wizard",
    )
    (gated,) = apply_cluster_dependencies([candidate], scan.gated_columns)
    assert len(gated.blocked_by) == 1, gated.blocked_by
    cluster = next(
        r
        for r in scan.records
        if r.scope
        == "elicitation:duplicate:wurzelbiermarke.aktueller_einzelhandelspreis|einzelhandel_preis_aktuell"
    )
    assert gated.blocked_by == (cluster.id,)


def test_a_question_about_an_uncontested_column_is_not_blocked() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.gaps import apply_cluster_dependencies

    scan = _scan(beer_factory_assets())
    candidate = ClarificationRecord(
        id="q_free",
        scope="elicitation:valuemap:transaktion.kreditkartentyp",
        question="?",
        category="B",
        target_table="transaktion",
        target_column="kreditkartentyp",
        source="elicitation_wizard",
    )
    (gated,) = apply_cluster_dependencies([candidate], scan.gated_columns)
    assert gated.blocked_by == ()
    assert gated is candidate, "an unblocked record is returned untouched, not rebuilt"


def test_only_a_t1_cluster_gates_other_questions() -> None:
    """An agreeing cluster is T4: either column may be used, so nothing downstream has to wait
    on it. Blocking on it would stall a whole tab for a cosmetic finding."""
    scan = _scan(beer_factory_assets())
    t4_columns = {
        f"{table}.{name}"
        for table, left, right in _pairs(scan.records, "T4")
        for name in (left, right)
    }
    assert t4_columns, "the fixture stopped producing an agreeing cluster"
    assert not (t4_columns & set(scan.gated_columns))


# ── ordering and severity stratification ────────────────────────────────────────────────────


def test_records_come_back_severity_first_so_an_admin_can_stop_at_any_tier() -> None:
    from governed_bi.curator.gaps import SEVERITY_ORDER

    scan = _scan(beer_factory_assets())
    tiers = [SEVERITY_ORDER.index(r.severity or "T4") for r in scan.records]
    assert tiers == sorted(tiers), [r.severity for r in scan.records]


def test_within_t1_the_strongest_row_level_evidence_comes_first() -> None:
    from governed_bi.curator.gap_signals import evidence_strength

    scan = _scan(beer_factory_assets())
    duplicates = [
        r for r in scan.records
        if r.scope.startswith("elicitation:duplicate:") and r.severity == "T1"
    ]
    keys = [
        (-evidence_strength(*_differing_counts(r.question)),
         -_differing_counts(r.question)[0])
        for r in duplicates
    ]
    assert keys == sorted(keys), keys


def test_a_three_row_table_does_not_outrank_the_schemas_worst_join_key() -> None:
    """**Found by looking at the real output through the admin UI.** Ranking T1 on
    ``differing / rows`` put the top three cards on ``geoposition`` -- 3 of 3 rows, a perfect
    1.0 on a *three-row* table -- and sorted ``transaktion.kunde_id`` against
    ``transaktions_kunde_id`` (6 305 of 6 312, the case the whole design doc is written around)
    to #13. An admin who reads top-down and stops is then answering the wrong questions first,
    which is the entire premise of stratifying by severity.

    Both halves are asserted, because either alone can be satisfied by the wrong fix: the
    headline case has to come first, *and* the three-row pairs have to leave the top of the tier
    rather than merely swap places among themselves.
    """
    scan = _scan(beer_factory_assets())
    ranked = [
        r.scope.rpartition(":")[2]
        for r in scan.records
        if r.scope.startswith("elicitation:duplicate:") and r.severity == "T1"
    ]
    headline = "transaktion.kunde_id|transaktions_kunde_id"
    assert headline in ranked, ranked
    assert ranked.index(headline) < 5, ranked[:6]
    assert not [s for s in ranked[:5] if s.startswith("geoposition.")], ranked[:5]


def test_equally_certain_findings_are_ordered_by_how_many_rows_are_wrong() -> None:
    """The second term, and the reason it is not a tiebreak: three ``beer_factory`` pairs
    disagree on 100% of their rows with the small-sample discount saturated, so evidence
    strength cannot separate them -- and among those, the pair that puts 6 430 rows on the wrong
    entity is worse than the one that puts 6 312 there."""
    scan = _scan(beer_factory_assets())
    top = [
        _differing_counts(r.question)
        for r in scan.records
        if r.scope.startswith("elicitation:duplicate:") and r.severity == "T1"
    ][:3]
    assert [d for d, _ in top] == [6430, 6312, 6312], top


def test_nothing_is_dropped_for_a_quota() -> None:
    """The cap decision: cost is bounded (how many governed statements one click issues), and
    findings are not. 93 undescribed columns must never be able to crowd out one disagreeing
    join key, and the only way to guarantee that is for no detector to share a budget with
    another."""
    scan = _scan(beer_factory_assets())
    assert len(_pairs(scan.records, "T1")) == 17
    assert len(_pairs(scan.records, "T2")) == 3, "demoted, and still reported"
    assert len([r for r in scan.records if r.severity == "T4"]) >= 18


def test_one_scope_and_one_id_per_finding_across_a_whole_scan() -> None:
    """**Found live**, on real ``beer_factory`` through ``POST /elicitation/generate``: six T3
    join-key records reached the ledger *twice*, same scope and same id, and the wizard rendered
    duplicate React keys for them.

    The cause was the join detector's emission unit. Its docstring says the unit is the candidate
    *column* ("pairs are combinatorial noise, columns are the actual decision"), but it emitted
    once per ``(target column, source column)`` match — and ``standort`` has two columns that
    both read as its key (``standort_id``, ``standort_nummer``), so ``kunden.ort`` matched both
    and was asked about twice. One source column joining to one table is one decision.

    Asserted over the whole scan rather than inside the join detector, because ``_record_id`` is
    a hash of ``scope``: any two records sharing a scope are one record proposed twice, whichever
    detector made them, and the ledger's idempotency is keyed on exactly that.
    """
    scan = _scan(beer_factory_assets())
    scopes = [r.scope for r in scan.records]
    ids = [r.id for r in scan.records]
    assert len(scopes) == len(set(scopes)), sorted(
        s for s in scopes if scopes.count(s) > 1
    )
    assert len(ids) == len(set(ids))


# ── helpers ─────────────────────────────────────────────────────────────────────────────────


def _differing_counts(question: str) -> tuple[int, int]:
    """``(differing, rows)`` back out of the question text, for the ordering assertions."""
    import re

    found = re.search(r"on (\d+) of (\d+) rows", question)
    assert found is not None, question
    return int(found.group(1)), int(found.group(2))


def _two_tables_joined_by_an_unconventional_key() -> dict[str, Any]:
    """``restaurant``'s shape, minimally: the key is ``lokal_id`` on a table called
    ``allgemeine_informationen``, so nothing in the schema is named after what it identifies."""
    from governed_bi.corpus.schema import ColumnAsset, TableAsset

    assets: list[Any] = []
    for name in ("allgemeine_informationen", "betrieb_informationen"):
        column = ColumnAsset(
            id=f"r.{name}.lokal_id", schema="r", parent_table=f"r.{name}",
            physical_name="lokal_id", summary="lokal_id", physical_type="bigint",
            body="described",
        )
        assets += [
            column,
            TableAsset(
                id=f"r.{name}", schema="r", physical_name=name, summary=name,
                body="Restaurants.", grain="one row per restaurant", columns=(column.id,),
            ),
        ]
    return {a.id: a for a in assets}


def _identical_pair_schema(*, differing: int, right_type: str = "bigint") -> dict[str, Any]:
    """One table, two near-duplicate identity columns. ``differing`` documents the intent; the
    connector is what actually answers."""
    from governed_bi.corpus.schema import ColumnAsset, TableAsset

    del differing
    columns = [
        ColumnAsset(
            id=f"shop.orders.{name}", schema="shop", parent_table="shop.orders",
            physical_name=name, summary=name, physical_type=physical_type, body="described",
        )
        for name, physical_type in (
            ("order_id", "bigint"), ("acct_id", "bigint"), ("acct_uid", right_type),
        )
    ]
    table = TableAsset(
        id="shop.orders", schema="shop", physical_name="orders", summary="orders",
        body="Orders.", grain="one row per order", columns=tuple(c.id for c in columns),
    )
    return {a.id: a for a in [table, *columns]}


def _pair_connector(*, n_rows: int, n_differing: int, card: tuple[int, int]) -> Any:
    return MeasuredConnector({("orders", "acct_id", "acct_uid"): (n_rows, n_differing, *card)})
