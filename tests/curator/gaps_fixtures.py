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
#:
#: One pair per line is the point: the eye scans this as a table, and a reader checking a
#: detector against it reads down a column. Two of forty rows wrapping to satisfy a column
#: limit costs more than the two long lines do, so those two carry ``noqa: E501``.
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
    ("transaktion", "wurzelbier_id", "transaktions_wurzelbier_id"): (6312, 6312, 6312, 6312),  # decoy pair (trap manifest)  # noqa: E501
    ("wurzelbier", "marke_id", "marken_nummer"): (6430, 6043, 23, 23),  # decoy pair (trap manifest)
    ("wurzelbier", "standort_id", "zugehoeriger_standort_id"): (6430, 3234, 2, 2),  # decoy pair (trap manifest)
    ("wurzelbier", "wurzelbier_id", "wurzelbier_nummer"): (6430, 6430, 6430, 6430),  # decoy pair (trap manifest)
    ("wurzelbier_bewertung", "kunde_id", "bewertender_kunde_id"): (713, 712, 362, 362),  # decoy pair (trap manifest)
    ("wurzelbier_bewertung", "marke_id", "bewertete_marke_id"): (713, 666, 22, 22),  # decoy pair (trap manifest)
    ("wurzelbiermarke", "aktueller_einzelhandelspreis", "einzelhandel_preis_aktuell"): (24, 2, 3, 5),  # decoy pair (trap manifest)  # noqa: E501
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


#: ``(table, column) -> (rows, distinct values)``, exactly what the live database answers.
#:
#: The fact the corpus cannot supply and the join detector now measures instead of inferring
#: from a name: ``Session.from_live_schema`` writes no ``is_unique`` and ``pg_rename_decoy``
#: declares zero table constraints, so "does this column identify a row" has to be counted.
#: Note ``standort`` and ``geoposition``: three rows each, so nearly every column there is
#: trivially unique -- which is exactly why uniqueness alone is not enough and
#: :data:`BEER_FACTORY_OBSERVED` carries the second half.
BEER_FACTORY_CARDINALITY: dict[tuple[str, str], tuple[int, int]] = {
    ("betriebsstandorte", "betrieb_id"): (3, 3),
    ("betriebsstandorte", "bezeichnung"): (3, 3),
    ("betriebsstandorte", "strassenadresse"): (3, 1),
    ("betriebsstandorte", "ort"): (3, 1),
    ("betriebsstandorte", "bundesland_name"): (3, 1),
    ("betriebsstandorte", "plz"): (3, 1),
    ("geoposition", "standort_id"): (3, 3),
    ("geoposition", "breitengrad"): (3, 3),
    ("geoposition", "l_ngengrad"): (3, 3),
    ("geoposition", "geo_standort_id"): (3, 3),
    ("geoposition", "breitenkoordinate"): (3, 3),
    ("geoposition", "laengengrad"): (3, 3),
    ("kunden", "kunde_id"): (554, 554),
    ("kunden", "vorname"): (554, 359),
    ("kunden", "nachname"): (554, 478),
    ("kunden", "stra_enadresse"): (554, 554),
    ("kunden", "stadt"): (554, 22),
    ("kunden", "bundesland"): (554, 1),
    ("kunden", "postleitzahl"): (554, 133),
    ("kunden", "email"): (554, 554),
    ("kunden", "telefonnummer"): (554, 554),
    ("kunden", "erstes_kaufdatum"): (554, 307),
    ("kunden", "newsletter_abonniert"): (554, 2),
    ("kunden", "geschlecht"): (554, 2),
    ("kunden", "kunde_nummer"): (554, 554),
    ("kunden", "ort"): (554, 22),
    ("kunden", "email_adresse"): (554, 554),
    ("standort", "standort_id"): (3, 3),
    ("standort", "standortname"): (3, 3),
    ("standort", "stra_enadresse"): (3, 1),
    ("standort", "stadt"): (3, 1),
    ("standort", "bundesland"): (3, 1),
    ("standort", "postleitzahl"): (3, 1),
    ("standort", "standort_nummer"): (3, 3),
    ("standort", "ortschaft"): (3, 1),
    ("standort", "standort_bezeichnung"): (3, 3),
    ("transaktion", "transaktions_id"): (6312, 6312),
    ("transaktion", "kreditkartennummer"): (6312, 554),
    ("transaktion", "kunde_id"): (6312, 554),
    ("transaktion", "transaktionsdatum"): (6312, 727),
    ("transaktion", "kreditkartentyp"): (6312, 4),
    ("transaktion", "standort_id"): (6312, 2),
    ("transaktion", "wurzelbier_id"): (6312, 6312),
    ("transaktion", "kaufpreis"): (6312, 2),
    ("transaktion", "transaktions_kunde_id"): (6312, 554),
    ("transaktion", "transaktions_standort_id"): (6312, 2),
    ("transaktion", "transaktions_wurzelbier_id"): (6312, 6312),
    ("wurzelbier", "wurzelbier_id"): (6430, 6430),
    ("wurzelbier", "marke_id"): (6430, 23),
    ("wurzelbier", "beh_ltertyp"): (6430, 2),
    ("wurzelbier", "standort_id"): (6430, 2),
    ("wurzelbier", "kaufdatum"): (6430, 807),
    ("wurzelbier", "marken_nummer"): (6430, 23),
    ("wurzelbier", "zugehoeriger_standort_id"): (6430, 2),
    ("wurzelbier", "wurzelbier_nummer"): (6430, 6430),
    ("wurzelbier_bewertung", "kunde_id"): (713, 362),
    ("wurzelbier_bewertung", "marke_id"): (713, 22),
    ("wurzelbier_bewertung", "sternbewertung"): (713, 5),
    ("wurzelbier_bewertung", "bewertungsdatum"): (713, 421),
    ("wurzelbier_bewertung", "bewertung"): (713, 16),
    ("wurzelbier_bewertung", "bewertete_marke_id"): (713, 22),
    ("wurzelbier_bewertung", "bewertender_kunde_id"): (713, 362),
    ("wurzelbier_bewertung", "rezension_text"): (713, 15),
    ("wurzelbier_feedback", "kunde_nr"): (713, 362),
    ("wurzelbier_feedback", "marke_nr"): (713, 22),
    ("wurzelbier_feedback", "sterne"): (713, 5),
    ("wurzelbier_feedback", "bewertungs_datum"): (713, 421),
    ("wurzelbier_feedback", "kommentar"): (713, 16),
    ("wurzelbiermarke", "marke_id"): (24, 24),
    ("wurzelbiermarke", "markenname"): (24, 24),
    ("wurzelbiermarke", "erstes_braujaht"): (24, 19),
    ("wurzelbiermarke", "brauerei_name"): (24, 23),
    ("wurzelbiermarke", "stadt"): (24, 19),
    ("wurzelbiermarke", "bundesland"): (24, 14),
    ("wurzelbiermarke", "land"): (24, 2),
    ("wurzelbiermarke", "beschreibung"): (24, 24),
    ("wurzelbiermarke", "rohrzucker"): (24, 2),
    ("wurzelbiermarke", "maissirup"): (24, 2),
    ("wurzelbiermarke", "honig"): (24, 2),
    ("wurzelbiermarke", "k_nstlicher_s_stoff"): (24, 2),
    ("wurzelbiermarke", "koffeinhaltig"): (24, 2),
    ("wurzelbiermarke", "alkoholhaltig"): (24, 1),
    ("wurzelbiermarke", "in_dosen_erh_ltlich"): (24, 2),
    ("wurzelbiermarke", "in_flaschen_erh_ltlich"): (24, 2),
    ("wurzelbiermarke", "in_f_ssern_erh_ltlich"): (24, 2),
    ("wurzelbiermarke", "website"): (24, 22),
    ("wurzelbiermarke", "facebook_seite"): (24, 6),
    ("wurzelbiermarke", "twitter"): (24, 2),
    ("wurzelbiermarke", "gro_handelspreis"): (24, 17),
    ("wurzelbiermarke", "aktueller_einzelhandelspreis"): (24, 3),
    ("wurzelbiermarke", "marke_ref_id"): (24, 24),
    ("wurzelbiermarke", "einzelhandel_preis_aktuell"): (24, 5),
    ("wurzelbiermarke", "grosshandelspreis"): (24, 17),
}


#: ``column id -> the first 20 distinct values``, as ``read_observed_values`` really returns them.
#:
#: Only the 59 columns of the 93 that some other table's column reads like, because those are the
#: only ones the join detector asks about. This is the half of "is this a join" that names cannot
#: supply: a foreign key's values live in the referenced key's domain, so two columns over one
#: domain share their smallest values and two columns over different domains share none.
#: ``wurzelbiermarke.maissirup`` and ``kunden.email`` are the case that matters -- they match on
#: the three characters ``mai`` and have not one value in common.
BEER_FACTORY_OBSERVED: dict[str, tuple[str, ...]] = {
    "beer_factory.betriebsstandorte.bezeichnung": (
        'LOST', 'Sac State American River Courtyard', 'Sac State Union',
    ),
    "beer_factory.betriebsstandorte.strassenadresse": (
        '6000 J St',
    ),
    "beer_factory.betriebsstandorte.ort": (
        'Sacramento',
    ),
    "beer_factory.betriebsstandorte.bundesland_name": (
        'CA',
    ),
    "beer_factory.geoposition.standort_id": (
        '0', '1', '2',
    ),
    "beer_factory.geoposition.geo_standort_id": (
        '0', '1', '2',
    ),
    "beer_factory.kunden.kunde_id": (
        '101811', '103508', '104939', '105549', '105771', '108708', '109251', '110363',
        '111486', '111991', '112690', '116690', '116934', '119406', '121639', '122818',
        '127472', '128131', '132454', '134457',
    ),
    "beer_factory.kunden.vorname": (
        'Aaron', 'Ada', 'Adam', 'Adrian', 'Adrienne', 'Alex', 'Alice', 'Alyssa', 'Amanda',
        'Amber', 'Amobi', 'Andy', 'Angela', 'Ann', 'Anna', 'Anne', 'Annette', 'Annie',
        'Anthony', 'April',
    ),
    "beer_factory.kunden.stra_enadresse": (
        '1003 Heather Tree Dr', '1014 Viking Dr', '1031 Shadow Creek Dr', '1046 Auburn Blvd',
        '1063 Wintermist Ct', '1070 Bandon Way', '1130 Deer Lake Dr', '1148 Cool Water Ct',
        '114 Parkside Ct', '1151 Yellowtail Way', '1182 Dornajo Way', '1187 Outer Banks Pl',
        '1204 Chamonix Way', '1215 Rodeo Dr', '1227 McClatchy Way', '1232 Chambord Ct',
        '1281 Poinsettia Ct', '1291 Chesterton Way', '1297 Goethe Rd', '1309 Clothier Way',
    ),
    "beer_factory.kunden.stadt": (
        'Antelope', 'Carmichael', 'Citrus Heights', 'Courtland', 'Elk Grove', 'Elverta',
        'Fair Oaks', 'Folsom', 'Galt', 'Herald', 'Isleton', 'Mather', 'McClellan',
        'North Highlands', 'Orangevale', 'Rancho Cordova', 'Rancho Murieta', 'Rio Linda',
        'Ryde', 'Sacramento',
    ),
    "beer_factory.kunden.bundesland": (
        'CA',
    ),
    "beer_factory.kunden.postleitzahl": (
        '94203', '94204', '94205', '94206', '94207', '94208', '94209', '94211', '94229',
        '94230', '94232', '94234', '94235', '94236', '94237', '94239', '94240', '94244',
        '94245', '94246',
    ),
    "beer_factory.kunden.email": (
        'aalyssa20@fastmail.com', 'aaron.g@zoho.com', 'a_boehmer@fastmail.com',
        'a_brandi@hotmail.com', 'adam.j@gmail.com', 'adrian_r@yahoo.com', 'adrienne_b@aol.com',
        'afox@mail.com', 'agray@gmail.com', 'a_haley@zoho.com', 'a_kennedy17@mail.com',
        'a.lane@zoho.com', 'alazraquic@icloud.com', 'alcorn.e@outlook.com',
        'alcorn_j@fastmail.com', 'a_leibowitz@outlook.com', 'alex_h@zoho.com',
        'alynn78@zoho.com', 'amberd69@hotmail.com', 'a_mullins@yahoo.com',
    ),
    "beer_factory.kunden.erstes_kaufdatum": (
        '2012-07-01', '2012-07-02', '2012-07-03', '2012-07-04', '2012-07-07', '2012-07-08',
        '2012-07-09', '2012-07-10', '2012-07-11', '2012-07-14', '2012-07-15', '2012-07-16',
        '2012-07-18', '2012-07-19', '2012-07-21', '2012-07-22', '2012-07-23', '2012-07-24',
        '2012-07-26', '2012-07-27',
    ),
    "beer_factory.kunden.newsletter_abonniert": (
        'FALSE', 'TRUE',
    ),
    "beer_factory.kunden.kunde_nummer": (
        '101811', '103508', '104939', '105549', '105771', '108708', '109251', '110363',
        '111486', '111991', '112690', '116690', '116934', '119406', '121639', '122818',
        '127472', '128131', '132454', '134457',
    ),
    "beer_factory.kunden.ort": (
        'Antelope', 'Carmichael', 'Citrus Heights', 'Courtland', 'Elk Grove', 'Elverta',
        'Fair Oaks', 'Folsom', 'Galt', 'Herald', 'Isleton', 'Mather', 'McClellan',
        'North Highlands', 'Orangevale', 'Rancho Cordova', 'Rancho Murieta', 'Rio Linda',
        'Ryde', 'Sacramento',
    ),
    "beer_factory.standort.standort_id": (
        '0', '1', '2',
    ),
    "beer_factory.standort.standortname": (
        'LOST', 'Sac State American River Courtyard', 'Sac State Union',
    ),
    "beer_factory.standort.stra_enadresse": (
        '6000 J St',
    ),
    "beer_factory.standort.stadt": (
        'Sacramento',
    ),
    "beer_factory.standort.bundesland": (
        'CA',
    ),
    "beer_factory.standort.postleitzahl": (
        '95819',
    ),
    "beer_factory.standort.standort_nummer": (
        '0', '1', '2',
    ),
    "beer_factory.standort.ortschaft": (
        'Sacramento',
    ),
    "beer_factory.standort.standort_bezeichnung": (
        'LOST', 'Sac State American River Courtyard', 'Sac State Union',
    ),
    "beer_factory.transaktion.kreditkartennummer": (
        '340348517399962', '340371753376429', '340497628282391', '340696558569673',
        '340737645115300', '340942459953867', '340942518103652', '340988300416984',
        '341286314383533', '341539631695512', '341683510551907', '342013788180503',
        '342476006542863', '342523749860472', '342545986621978', '342759627792243',
        '342776192610527', '342781333348557', '343025452901674', '343130887791987',
    ),
    "beer_factory.transaktion.kunde_id": (
        '101811', '103508', '104939', '105549', '105771', '108708', '109251', '110363',
        '111486', '111991', '112690', '116690', '116934', '119406', '121639', '122818',
        '127472', '128131', '132454', '134457',
    ),
    "beer_factory.transaktion.kreditkartentyp": (
        'American Express', 'Discover', 'MasterCard', 'Visa',
    ),
    "beer_factory.transaktion.standort_id": (
        '1', '2',
    ),
    "beer_factory.transaktion.wurzelbier_id": (
        '100000', '100001', '100002', '100003', '100004', '100005', '100006', '100007',
        '100008', '100009', '100010', '100011', '100012', '100013', '100014', '100015',
        '100016', '100017', '100018', '100019',
    ),
    "beer_factory.transaktion.transaktions_kunde_id": (
        '101811', '103508', '104939', '105549', '105771', '108708', '109251', '110363',
        '111486', '111991', '112690', '116690', '116934', '119406', '121639', '122818',
        '127472', '128131', '132454', '134457',
    ),
    "beer_factory.transaktion.transaktions_standort_id": (
        '1', '2',
    ),
    "beer_factory.transaktion.transaktions_wurzelbier_id": (
        '100000', '100001', '100002', '100003', '100004', '100005', '100006', '100007',
        '100008', '100009', '100010', '100011', '100012', '100013', '100014', '100015',
        '100016', '100017', '100018', '100019',
    ),
    "beer_factory.wurzelbier.wurzelbier_id": (
        '100000', '100001', '100002', '100003', '100004', '100005', '100006', '100007',
        '100008', '100009', '100010', '100011', '100012', '100013', '100014', '100015',
        '100016', '100017', '100018', '100019',
    ),
    "beer_factory.wurzelbier.marke_id": (
        '10001', '10002', '10003', '10004', '10005', '10006', '10007', '10008', '10009',
        '10010', '10011', '10012', '10013', '10014', '10015', '10016', '10017', '10018',
        '10019', '10020',
    ),
    "beer_factory.wurzelbier.beh_ltertyp": (
        'Bottle', 'Can',
    ),
    "beer_factory.wurzelbier.standort_id": (
        '1', '2',
    ),
    "beer_factory.wurzelbier.kaufdatum": (
        '2014-05-04', '2014-05-15', '2014-05-23', '2014-05-24', '2014-06-08', '2014-06-10',
        '2014-06-11', '2014-06-20', '2014-06-22', '2014-06-24', '2014-06-27', '2014-06-28',
        '2014-07-01', '2014-07-02', '2014-07-03', '2014-07-07', '2014-07-08', '2014-07-12',
        '2014-07-13', '2014-07-14',
    ),
    "beer_factory.wurzelbier.marken_nummer": (
        '10001', '10002', '10003', '10004', '10005', '10006', '10007', '10008', '10009',
        '10010', '10011', '10012', '10013', '10014', '10015', '10016', '10017', '10018',
        '10019', '10020',
    ),
    "beer_factory.wurzelbier.zugehoeriger_standort_id": (
        '1', '2',
    ),
    "beer_factory.wurzelbier.wurzelbier_nummer": (
        '100000', '100001', '100002', '100003', '100004', '100005', '100006', '100007',
        '100008', '100009', '100010', '100011', '100012', '100013', '100014', '100015',
        '100016', '100017', '100018', '100019',
    ),
    "beer_factory.wurzelbier_bewertung.kunde_id": (
        '101811', '105549', '105771', '108708', '109251', '110363', '111486', '111991',
        '116690', '116934', '119406', '121639', '122818', '127472', '132454', '134457',
        '137696', '143878', '145929', '147216',
    ),
    "beer_factory.wurzelbier_bewertung.marke_id": (
        '10001', '10002', '10003', '10004', '10005', '10006', '10007', '10008', '10009',
        '10010', '10011', '10012', '10013', '10014', '10015', '10016', '10017', '10018',
        '10019', '10020',
    ),
    "beer_factory.wurzelbier_bewertung.sternbewertung": (
        '1', '2', '3', '4', '5',
    ),
    "beer_factory.wurzelbier_bewertung.bewertungsdatum": (
        '2012-09-03', '2012-09-05', '2012-09-11', '2012-09-12', '2012-09-15', '2012-09-19',
        '2012-09-24', '2012-10-16', '2012-10-19', '2012-10-20', '2012-10-26', '2012-11-04',
        '2012-11-06', '2012-11-08', '2012-11-11', '2012-11-14', '2012-11-17', '2012-11-18',
        '2012-11-20', '2012-11-22',
    ),
    "beer_factory.wurzelbier_bewertung.bewertung": (
        'Awww, doggies!', 'Cans? Not a chance.', "Didn't try it, but the label was ugly.",
        'Does Wisconsin proud.', 'Fantastic!', "Love stopping by Fitz's in St. Louis.",
        "Only root beer I've ever seen.", 'Tastes like Australia.',
        'That is the coolest bottle and the foulest root beer.',
        'The drink is fine, but the crest on the label is pretentious.',
        'The quintessential dessert root beer. No ice cream required.',
        'This is the best root beer ever!', 'Too much bite, not enough barq.', 'Too Spicy!',
        'You could have done better Sactown.', 'Yuk, more like licorice soda.',
    ),
    "beer_factory.wurzelbier_bewertung.bewertete_marke_id": (
        '10001', '10002', '10003', '10004', '10005', '10006', '10007', '10008', '10009',
        '10010', '10011', '10012', '10013', '10014', '10015', '10016', '10017', '10018',
        '10019', '10020',
    ),
    "beer_factory.wurzelbier_bewertung.bewertender_kunde_id": (
        '101811', '105549', '105771', '108708', '109251', '110363', '111486', '111991',
        '116690', '116934', '119406', '121639', '122818', '127472', '132454', '134457',
        '137696', '143878', '145929', '147216',
    ),
    "beer_factory.wurzelbier_feedback.kunde_nr": (
        '101811', '105549', '105771', '108708', '109251', '110363', '111486', '111991',
        '116690', '116934', '119406', '121639', '122818', '127472', '132454', '134457',
        '137696', '143878', '145929', '147216',
    ),
    "beer_factory.wurzelbier_feedback.marke_nr": (
        '10001', '10002', '10003', '10004', '10005', '10006', '10007', '10008', '10009',
        '10010', '10011', '10012', '10013', '10014', '10015', '10016', '10017', '10018',
        '10019', '10020',
    ),
    "beer_factory.wurzelbier_feedback.sterne": (
        '1', '2', '3', '4', '5',
    ),
    "beer_factory.wurzelbier_feedback.bewertungs_datum": (
        '2012-09-03', '2012-09-05', '2012-09-11', '2012-09-12', '2012-09-15', '2012-09-19',
        '2012-09-24', '2012-10-16', '2012-10-19', '2012-10-20', '2012-10-26', '2012-11-04',
        '2012-11-06', '2012-11-08', '2012-11-11', '2012-11-14', '2012-11-17', '2012-11-18',
        '2012-11-20', '2012-11-22',
    ),
    "beer_factory.wurzelbiermarke.marke_id": (
        '10001', '10002', '10003', '10004', '10005', '10006', '10007', '10008', '10009',
        '10010', '10011', '10012', '10013', '10014', '10015', '10016', '10017', '10018',
        '10019', '10020',
    ),
    "beer_factory.wurzelbiermarke.stadt": (
        'Bainbridge Island', 'Bundaberg', 'Chicago', 'Covington', 'Detroit', 'Dover',
        'Fall River', 'Fresno', 'Glendale', 'Lodi', 'Mansfield', 'New Orleans', 'New Ulm',
        'Pacific Grove', 'Port Angeles', 'Portland', 'Sacramento', 'San Francisco', 'St. Louis',
    ),
    "beer_factory.wurzelbiermarke.bundesland": (
        'CA', 'DE', 'IL', 'LA', 'MA', 'ME', 'MI', 'MN', 'MO', 'OH', 'OR', 'QLD', 'WA', 'WI',
    ),
    "beer_factory.wurzelbiermarke.land": (
        'Australia', 'United States',
    ),
    "beer_factory.wurzelbiermarke.maissirup": (
        'FALSE', 'TRUE',
    ),
    "beer_factory.wurzelbiermarke.marke_ref_id": (
        '10001', '10002', '10003', '10004', '10005', '10006', '10007', '10008', '10009',
        '10010', '10011', '10012', '10013', '10014', '10015', '10016', '10017', '10018',
        '10019', '10020',
    ),
}

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
    """Answers a governed statement with what the live database really answered.

    Keyed on the quoted column identifiers the statement carries, which is the repo's existing
    scripted-connector idiom (``tests/serve/test_agent_tools_hitl.py``). Two statement shapes now:

    * a **pair comparison** (``IS DISTINCT FROM``). A pair nobody measured returns "identical
      everywhere", so an unmeasured pair can never be mistaken for evidence of disagreement.
    * a **cardinality read** (``n_distinct``), which the join detector issues to ask whether a
      column identifies a row. A column nobody measured comes back *not* unique, for the same
      reason in the same direction: the unmeasured case must never look like evidence.
    """

    dialect = "postgres"

    def __init__(
        self,
        counts: dict[tuple[str, str, str], tuple[int, int, int, int]] | None = None,
        cardinality: dict[tuple[str, str], tuple[int, int]] | None = None,
    ):
        self.counts = BEER_FACTORY_PAIR_COUNTS if counts is None else counts
        self.cardinality = BEER_FACTORY_CARDINALITY if cardinality is None else cardinality
        self.statements: list[str] = []

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        self.statements.append(sql)
        if "n_distinct" in sql and "n_differing" not in sql:
            for (table, column), counts in self.cardinality.items():
                if f'"{table}"' in sql and f'"{column}"' in sql:
                    return (["n_rows", "n_distinct"], [counts], False)
            return (["n_rows", "n_distinct"], [(2, 1)], False)
        header = ["n_rows", "n_differing", "n_distinct_left", "n_distinct_right"]
        for (table, left, right), counts in self.counts.items():
            if f'"{table}"' in sql and f'"{left}"' in sql and f'"{right}"' in sql:
                return (header, [counts], False)
        if "n_differing" in sql or "IS DISTINCT FROM" in sql:
            return (header, [(1, 0, 1, 1)], False)
        return (["value"], [], False)
