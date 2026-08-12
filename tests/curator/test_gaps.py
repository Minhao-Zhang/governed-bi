"""Structural gap detectors: language-independent, evidence-gated.

**What these replace.** Every gate in ``curator/elicitation.py`` is an English keyword substring
match, and the whole German ``beer_factory`` schema -- 93 columns -- hits **zero** of them
(``kreditkartentyp`` contains ``typ``, not ``type``). Measured on the same schema, the
structural gap surface is 93/93 undescribed columns, 28 of 36 table pairs with no declared join,
and 21 injected near-duplicate column pairs of which 19 disagree row-wise. Detected by the
shipped generator: **0 of any of them.**

So these detectors key on corpus *shape* and real *data*, never on a word list, and each is
tested three ways: a unit test of the signal, a test against
``tests/curator/gaps_fixtures.py``'s literal reproduction of the real German schema and the real
numbers its database answers with, and (for the near-duplicate detector) a recall bar stated
against BIRD-Obfuscation's own decoy manifest rather than against the detector's own output.
"""

from __future__ import annotations

from typing import Any

from gaps_fixtures import (  # noqa: E402 - sibling fixture module, as tests/serve/ does
    BEER_FACTORY_AGREEING_DECOYS,
    BEER_FACTORY_DECOY_PAIRS,
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


# ── the name signal: language-independent by construction ────────────────────────────────────


def test_similarity_is_computed_on_characters_not_words() -> None:
    """The root cause this fixes: a word list is a language. Both measures read the case-folded
    alphanumeric character run, so ``stadt``/``stadtname`` and ``city``/``city_name`` score
    identically and neither is privileged."""
    from governed_bi.curator.gaps import name_similarity

    assert name_similarity("stadt", "stadtname") == name_similarity("city", "city_name")
    assert name_similarity("email", "email_adresse") == name_similarity("email", "email_address")


def test_the_two_measures_cover_the_two_shapes_a_duplicate_takes() -> None:
    """Neither measure alone reaches both, which is why the signal is their maximum.

    Containment (``email`` inside ``email_adresse``) is what a longest-common-substring ratio
    sees and a trigram overlap barely registers; reordering
    (``aktueller_einzelhandelspreis`` vs ``einzelhandel_preis_aktuell``) is the reverse.
    """
    from governed_bi.curator.gaps import (
        NEAR_DUPLICATE_SIMILARITY,
        _longest_common_run_ratio,
        _trigram_dice,
        name_similarity,
    )

    assert _trigram_dice("email", "email_adresse") < NEAR_DUPLICATE_SIMILARITY
    assert _longest_common_run_ratio("email", "email_adresse") == 1.0

    reordered = ("aktueller_einzelhandelspreis", "einzelhandel_preis_aktuell")
    assert _longest_common_run_ratio(*reordered) < NEAR_DUPLICATE_SIMILARITY
    assert _trigram_dice(*reordered) >= NEAR_DUPLICATE_SIMILARITY

    for pair in (("email", "email_adresse"), reordered):
        assert name_similarity(*pair) >= NEAR_DUPLICATE_SIMILARITY, pair


def test_unrelated_names_score_below_the_gate() -> None:
    from governed_bi.curator.gaps import NEAR_DUPLICATE_SIMILARITY, name_similarity

    for a, b in (("kunde_id", "kreditkartennummer"), ("vorname", "telefonnummer"),
                 ("order_id", "shipped_at")):
        assert name_similarity(a, b) < NEAR_DUPLICATE_SIMILARITY, (a, b)


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
    """4 non-manifest pairs come back T1, and they are one recognisable class: parallel columns
    that share a naming frame and are genuinely different facts -- ``in_dosen_erh_ltlich`` vs
    ``in_flaschen_erh_ltlich`` (available in cans / in bottles), ``breitengrad`` vs
    ``l_ngengrad`` (latitude / longitude), and a primary key beside a foreign key that shares
    its prefix. This is the ``created_at``/``updated_at`` class and it is not solved; it is
    counted, so a change that trades precision away shows up here.
    """
    scan = _scan(beer_factory_assets())
    unexpected = _pairs(scan.records, "T1") - BEER_FACTORY_DECOY_PAIRS
    assert unexpected == {
        ("wurzelbiermarke", "in_dosen_erh_ltlich", "in_f_ssern_erh_ltlich"),
        ("wurzelbiermarke", "in_dosen_erh_ltlich", "in_flaschen_erh_ltlich"),
        ("geoposition", "breitengrad", "l_ngengrad"),
        ("transaktion", "transaktions_id", "transaktions_wurzelbier_id"),
    }, sorted(unexpected)


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
    assert len(connector.statements) == 5
    assert len(_pairs(scan.records)) <= 5
    assert ("kunden", "email", "email_adresse") in _pairs(scan.records, "T1")


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
    scan = _scan(beer_factory_assets())
    duplicates = [
        r for r in scan.records
        if r.scope.startswith("elicitation:duplicate:") and r.severity == "T1"
    ]
    shares = [_differing_share(r.question) for r in duplicates]
    assert shares == sorted(shares, reverse=True), shares


def test_nothing_is_dropped_for_a_quota() -> None:
    """The cap decision: cost is bounded (how many governed statements one click issues), and
    findings are not. 93 undescribed columns must never be able to crowd out one disagreeing
    join key, and the only way to guarantee that is for no detector to share a budget with
    another."""
    scan = _scan(beer_factory_assets())
    assert len(_pairs(scan.records, "T1")) == 20
    assert len([r for r in scan.records if r.severity == "T4"]) >= 18


# ── helpers ─────────────────────────────────────────────────────────────────────────────────


def _differing_share(question: str) -> float:
    """``(differing, rows)`` back out of the question text, for the ordering assertion."""
    import re

    differing, rows = (int(n.replace(" ", "")) for n in re.findall(r"([\d ]+\d)", question)[:2])
    return differing / rows


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
