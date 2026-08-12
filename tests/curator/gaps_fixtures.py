"""The real `beer_factory` schema and the real numbers its database answers with.

**Why a fixture this literal.** The discipline this project keeps relearning is that a detector
tested only against hand-invented fixtures is tested against the shapes its author already had
in mind. So this is not a plausible-looking schema: it is `beer_factory` exactly as
`pg_rename_decoy` holds it -- 9 tables, 93 German column names, real physical types -- and
:data:`BEER_FACTORY_PAIR_COUNTS` is what a live `SELECT COUNT(*) ... IS DISTINCT FROM` really
returned for every pair the name gate admits (measured 2026-08-12 against
`host=127.0.0.1 port=5435 dbname=bird`).

The pairs marked `decoy pair` are ground truth from BIRD-Obfuscation's own
`eval_dataset/trap_manifest.json`, which records for each injected decoy column the
`source_column` it mimics: 21 such pairs in this schema. That manifest -- not a detector's own
output -- is what recall is measured against.

Three facts about those 21 that the tests assert and that no detector could invent:

- **2 of them genuinely agree** row-wise in the loaded data (`standortname` /
  `standort_bezeichnung` on 0 of 3 rows, `gro_handelspreis` / `grosshandelspreis` on 0 of 24).
  Both were injected by a *sparse* operator on a tiny table, so the perturbation touched no row.
  A detector must call these redundant-not-dangerous, which is the correct answer, not a miss.
- **3 of them are pure synonyms** with no shared character run -- `stadt`/`ort`,
  `stadt`/`ortschaft`, `bewertung`/`rezension_text`. No name-similarity measure reaches them,
  in any language. That is this detector's stated ceiling, pinned here so it cannot silently
  become a claim of full recall.
- The remaining **16 disagree and are name-reachable**, which is the number the tests hold to.
"""

from __future__ import annotations

from typing import Any

#: The real `beer_factory` schema: 9 tables, 93 columns, German identifiers.
BEER_FACTORY_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "betriebsstandorte": (
        ("betrieb_id", "bigint"),
        ("bezeichnung", "text"),
        ("strassenadresse", "text"),
        ("ort", "text"),
        ("bundesland_name", "text"),
        ("plz", "bigint"),
    ),
    "geoposition": (
        ("standort_id", "bigint"),
        ("breitengrad", "real"),
        ("l_ngengrad", "real"),
        ("geo_standort_id", "bigint"),
        ("breitenkoordinate", "real"),
        ("laengengrad", "real"),
    ),
    "kunden": (
        ("kunde_id", "bigint"),
        ("vorname", "text"),
        ("nachname", "text"),
        ("stra_enadresse", "text"),
        ("stadt", "text"),
        ("bundesland", "text"),
        ("postleitzahl", "bigint"),
        ("email", "text"),
        ("telefonnummer", "text"),
        ("erstes_kaufdatum", "date"),
        ("newsletter_abonniert", "text"),
        ("geschlecht", "text"),
        ("kunde_nummer", "bigint"),
        ("ort", "text"),
        ("email_adresse", "text"),
    ),
    "standort": (
        ("standort_id", "bigint"),
        ("standortname", "text"),
        ("stra_enadresse", "text"),
        ("stadt", "text"),
        ("bundesland", "text"),
        ("postleitzahl", "bigint"),
        ("standort_nummer", "bigint"),
        ("ortschaft", "text"),
        ("standort_bezeichnung", "text"),
    ),
    "transaktion": (
        ("transaktions_id", "bigint"),
        ("kreditkartennummer", "bigint"),
        ("kunde_id", "bigint"),
        ("transaktionsdatum", "date"),
        ("kreditkartentyp", "text"),
        ("standort_id", "bigint"),
        ("wurzelbier_id", "bigint"),
        ("kaufpreis", "real"),
        ("transaktions_kunde_id", "bigint"),
        ("transaktions_standort_id", "bigint"),
        ("transaktions_wurzelbier_id", "bigint"),
    ),
    "wurzelbier": (
        ("wurzelbier_id", "bigint"),
        ("marke_id", "bigint"),
        ("beh_ltertyp", "text"),
        ("standort_id", "bigint"),
        ("kaufdatum", "date"),
        ("marken_nummer", "bigint"),
        ("zugehoeriger_standort_id", "bigint"),
        ("wurzelbier_nummer", "bigint"),
    ),
    "wurzelbier_bewertung": (
        ("kunde_id", "bigint"),
        ("marke_id", "bigint"),
        ("sternbewertung", "bigint"),
        ("bewertungsdatum", "date"),
        ("bewertung", "text"),
        ("bewertete_marke_id", "bigint"),
        ("bewertender_kunde_id", "bigint"),
        ("rezension_text", "text"),
    ),
    "wurzelbier_feedback": (
        ("kunde_nr", "bigint"),
        ("marke_nr", "bigint"),
        ("sterne", "bigint"),
        ("bewertungs_datum", "date"),
        ("kommentar", "text"),
    ),
    "wurzelbiermarke": (
        ("marke_id", "bigint"),
        ("markenname", "text"),
        ("erstes_braujaht", "bigint"),
        ("brauerei_name", "text"),
        ("stadt", "text"),
        ("bundesland", "text"),
        ("land", "text"),
        ("beschreibung", "text"),
        ("rohrzucker", "text"),
        ("maissirup", "text"),
        ("honig", "text"),
        ("k_nstlicher_s_stoff", "text"),
        ("koffeinhaltig", "text"),
        ("alkoholhaltig", "text"),
        ("in_dosen_erh_ltlich", "text"),
        ("in_flaschen_erh_ltlich", "text"),
        ("in_f_ssern_erh_ltlich", "text"),
        ("website", "text"),
        ("facebook_seite", "text"),
        ("twitter", "text"),
        ("gro_handelspreis", "real"),
        ("aktueller_einzelhandelspreis", "real"),
        ("marke_ref_id", "bigint"),
        ("einzelhandel_preis_aktuell", "real"),
        ("grosshandelspreis", "real"),
    ),
}

#: What the live database really answers for every pair the name gate admits.
BEER_FACTORY_PAIR_COUNTS: dict[tuple[str, str, str], tuple[int, int, int, int]] = {
    ("geoposition", "breitengrad", "breitenkoordinate"): (3, 3, 3, 3),  # decoy pair (trap manifest)
    ("geoposition", "breitengrad", "l_ngengrad"): (3, 2, 3, 3),
    ("geoposition", "l_ngengrad", "laengengrad"): (3, 3, 3, 3),  # decoy pair (trap manifest)
    ("geoposition", "standort_id", "geo_standort_id"): (3, 3, 3, 3),  # decoy pair (trap manifest)
    ("kunden", "email", "email_adresse"): (554, 554, 554, 554),  # decoy pair (trap manifest)
    ("kunden", "kunde_id", "kunde_nummer"): (554, 554, 554, 554),  # decoy pair (trap manifest)
    ("kunden", "newsletter_abonniert", "ort"): (554, 554, 2, 22),
    ("kunden", "vorname", "ort"): (554, 554, 359, 22),
    ("standort", "stadt", "standort_bezeichnung"): (3, 3, 1, 3),
    ("standort", "standort_id", "standort_nummer"): (3, 3, 3, 3),  # decoy pair (trap manifest)
    ("standort", "standortname", "stadt"): (3, 3, 3, 1),
    ("standort", "standortname", "standort_bezeichnung"): (3, 0, 3, 3),  # decoy pair (trap manifest)
    ("transaktion", "kunde_id", "transaktions_kunde_id"): (6312, 6305, 554, 554),  # decoy pair (trap manifest)
    ("transaktion", "standort_id", "transaktions_standort_id"): (6312, 3194, 2, 2),  # decoy pair (trap manifest)
    ("transaktion", "transaktions_id", "transaktions_kunde_id"): (6312, 6312, 6312, 554),
    ("transaktion", "transaktions_id", "transaktions_standort_id"): (6312, 6312, 6312, 2),
    ("transaktion", "transaktions_id", "transaktions_wurzelbier_id"): (6312, 6312, 6312, 6312),
    ("transaktion", "transaktions_kunde_id", "transaktions_standort_id"): (6312, 6312, 554, 2),
    ("transaktion", "transaktions_kunde_id", "transaktions_wurzelbier_id"): (6312, 6312, 554, 6312),
    ("transaktion", "wurzelbier_id", "transaktions_wurzelbier_id"): (6312, 6312, 6312, 6312),  # decoy pair (trap manifest)
    ("wurzelbier", "marke_id", "marken_nummer"): (6430, 6043, 23, 23),  # decoy pair (trap manifest)
    ("wurzelbier", "standort_id", "zugehoeriger_standort_id"): (6430, 3234, 2, 2),  # decoy pair (trap manifest)
    ("wurzelbier", "wurzelbier_id", "wurzelbier_nummer"): (6430, 6430, 6430, 6430),  # decoy pair (trap manifest)
    ("wurzelbier_bewertung", "kunde_id", "bewertender_kunde_id"): (713, 712, 362, 362),  # decoy pair (trap manifest)
    ("wurzelbier_bewertung", "marke_id", "bewertete_marke_id"): (713, 666, 22, 22),  # decoy pair (trap manifest)
    ("wurzelbiermarke", "aktueller_einzelhandelspreis", "einzelhandel_preis_aktuell"): (24, 2, 3, 5),  # decoy pair (trap manifest)
    ("wurzelbiermarke", "aktueller_einzelhandelspreis", "grosshandelspreis"): (24, 24, 3, 17),
    ("wurzelbiermarke", "bundesland", "land"): (24, 24, 14, 2),
    ("wurzelbiermarke", "gro_handelspreis", "aktueller_einzelhandelspreis"): (24, 24, 17, 3),
    ("wurzelbiermarke", "gro_handelspreis", "grosshandelspreis"): (24, 0, 17, 17),  # decoy pair (trap manifest)
    ("wurzelbiermarke", "in_dosen_erh_ltlich", "in_f_ssern_erh_ltlich"): (24, 6, 2, 2),
    ("wurzelbiermarke", "in_dosen_erh_ltlich", "in_flaschen_erh_ltlich"): (24, 23, 2, 2),
    ("wurzelbiermarke", "marke_id", "marke_ref_id"): (24, 24, 24, 24),  # decoy pair (trap manifest)
}

#: The 21 injected decoy pairs, from BIRD-Obfuscation's ``trap_manifest.json``. Ground truth.
BEER_FACTORY_DECOY_PAIRS: frozenset[tuple[str, str, str]] = frozenset(
    (table, *sorted(pair))  # type: ignore[misc]
    for table, pair in (
        ("geoposition", ("standort_id", "geo_standort_id")),
        ("geoposition", ("breitengrad", "breitenkoordinate")),
        ("geoposition", ("l_ngengrad", "laengengrad")),
        ("kunden", ("kunde_id", "kunde_nummer")),
        ("kunden", ("stadt", "ort")),
        ("kunden", ("email", "email_adresse")),
        ("standort", ("standort_id", "standort_nummer")),
        ("standort", ("stadt", "ortschaft")),
        ("standort", ("standortname", "standort_bezeichnung")),
        ("wurzelbier", ("marke_id", "marken_nummer")),
        ("wurzelbier", ("standort_id", "zugehoeriger_standort_id")),
        ("wurzelbier", ("wurzelbier_id", "wurzelbier_nummer")),
        ("wurzelbiermarke", ("marke_id", "marke_ref_id")),
        ("wurzelbiermarke", ("aktueller_einzelhandelspreis", "einzelhandel_preis_aktuell")),
        ("wurzelbiermarke", ("gro_handelspreis", "grosshandelspreis")),
        ("wurzelbier_bewertung", ("marke_id", "bewertete_marke_id")),
        ("wurzelbier_bewertung", ("kunde_id", "bewertender_kunde_id")),
        ("wurzelbier_bewertung", ("bewertung", "rezension_text")),
        ("transaktion", ("kunde_id", "transaktions_kunde_id")),
        ("transaktion", ("standort_id", "transaktions_standort_id")),
        ("transaktion", ("wurzelbier_id", "transaktions_wurzelbier_id")),
    )
)

#: The 3 decoy pairs no name-similarity measure can reach (no shared character run).
BEER_FACTORY_SYNONYM_DECOYS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("kunden", "ort", "stadt"),
        ("standort", "ortschaft", "stadt"),
        ("wurzelbier_bewertung", "bewertung", "rezension_text"),
    }
)

#: The 2 decoy pairs whose values agree in the loaded data -- redundant, not dangerous.
BEER_FACTORY_AGREEING_DECOYS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("standort", "standort_bezeichnung", "standortname"),
        ("wurzelbiermarke", "gro_handelspreis", "grosshandelspreis"),
    }
)

#: The 6 joins hand-curated on 2026-08-10 and present in the served corpus, as
#: ``(left physical name, right physical name, ON clause)``.
BEER_FACTORY_JOINS: tuple[tuple[str, str, str], ...] = (
    ("geoposition", "standort", "geoposition.standort_id = standort.standort_id"),
    ("transaktion", "kunden", "transaktion.kunde_id = kunden.kunde_id"),
    ("transaktion", "standort", "transaktion.standort_id = standort.standort_id"),
    ("transaktion", "wurzelbier", "transaktion.wurzelbier_id = wurzelbier.wurzelbier_id"),
    ("wurzelbier_bewertung", "kunden", "wurzelbier_bewertung.kunde_id = kunden.kunde_id"),
    (
        "wurzelbier_bewertung",
        "wurzelbiermarke",
        "wurzelbier_bewertung.marke_id = wurzelbiermarke.marke_id",
    ),
)


def beer_factory_assets(*, with_joins: bool = True) -> dict[str, Any]:
    """The seeded corpus for ``beer_factory``, built the way ``corpus/seed.py`` builds it.

    Nothing carries a ``body``, a ``grain``, a ``role``, a ``references`` or a ``reliability``
    status, because the live-schema seed path sets none of those -- which is the fact the
    coverage and low-confidence detectors are measured against, not an omission in the fixture.
    """
    from governed_bi.corpus.schema import ColumnAsset, JoinAsset, SchemaAsset, TableAsset

    assets: list[Any] = [
        SchemaAsset(id="beer_factory", name="beer_factory", summary="beer_factory — 9 tables")
    ]
    for table, columns in BEER_FACTORY_COLUMNS.items():
        table_id = f"beer_factory.{table}"
        for name, physical_type in columns:
            assets.append(
                ColumnAsset(
                    id=f"{table_id}.{name}",
                    schema="beer_factory",
                    parent_table=table_id,
                    physical_name=name,
                    summary=f"{table}.{name} ({physical_type})",
                    physical_type=physical_type,
                    nullable=True,
                )
            )
        assets.append(
            TableAsset(
                id=table_id,
                schema="beer_factory",
                physical_name=table,
                summary=f"{table} ({len(columns)} columns)",
                columns=tuple(f"{table_id}.{name}" for name, _ in columns),
            )
        )
    if with_joins:
        from governed_bi.corpus.identity import join_id

        for left, right, on in BEER_FACTORY_JOINS:
            assets.append(
                JoinAsset(
                    id=join_id("beer_factory", left, right, on),
                    left_table=left,
                    right_table=right,
                    on=on,
                    summary=f"{left} joins {right}",
                )
            )
    return {a.id: a for a in assets}


class MeasuredConnector:
    """Answers a pair-comparison statement with what the live database really answered.

    Keyed on the two quoted column identifiers the governed statement carries, which is the
    repo's existing scripted-connector idiom (``tests/serve/test_agent_tools_hitl.py``). A pair
    nobody measured returns "identical everywhere", so an unmeasured pair can never be mistaken
    for evidence of disagreement.
    """

    dialect = "postgres"

    def __init__(self, counts: dict[tuple[str, str, str], tuple[int, int, int, int]] | None = None):
        self.counts = BEER_FACTORY_PAIR_COUNTS if counts is None else counts
        self.statements: list[str] = []

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        self.statements.append(sql)
        header = ["n_rows", "n_differing", "n_distinct_left", "n_distinct_right"]
        for (table, left, right), counts in self.counts.items():
            if f'"{table}"' in sql and f'"{left}"' in sql and f'"{right}"' in sql:
                return (header, [counts], False)
        if "n_differing" in sql or "IS DISTINCT FROM" in sql:
            return (header, [(1, 0, 1, 1)], False)
        return (["value"], [], False)
