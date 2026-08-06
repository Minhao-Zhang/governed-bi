"""Apply bindings for the 27 unbound terms (Item 3)."""

from __future__ import annotations

import pathlib
import sys

import yaml

AUTH = pathlib.Path("corpora/_variant-authored-20260805")

BINDINGS: dict[str, dict[str, str]] = {
    "authors/terms/term_authors_full_name.yaml": {
        "target_type": "column",
        "target_id": "authors.Conference.FullName",
    },
    "authors/terms/term_authors_homepage.yaml": {
        "target_type": "column",
        "target_id": "authors.Conference.HomePage",
    },
    "authors/terms/term_authors_short_name.yaml": {
        "target_type": "column",
        "target_id": "authors.Conference.ShortName",
    },
    "card_games/terms/term_card_games_powerful_card.yaml": {
        "target_type": "column",
        "target_id": "card_games.cards.cardKingdomId",
    },
    "food_inspection/terms/term_food_inspection_high_risk_violation.yaml": {
        "target_type": "column",
        "target_id": "food_inspection.verstaesse.risikokategorie",
    },
    "food_inspection/terms/term_food_inspection_low_risk_violation.yaml": {
        "target_type": "column",
        "target_id": "food_inspection.verstaesse.risikokategorie",
    },
    "food_inspection/terms/term_food_inspection_moderate_risk_violation.yaml": {
        "target_type": "column",
        "target_id": "food_inspection.verstaesse.risikokategorie",
    },
    "food_inspection/terms/term_food_inspection_met_all_requirements.yaml": {
        "target_type": "column",
        "target_id": "food_inspection.inspektionen.bewertung",
    },
    "food_inspection/terms/term_food_inspection_reinspection.yaml": {
        "target_type": "column",
        "target_id": "food_inspection.inspektionen.typ",
    },
    "food_inspection/terms/term_food_inspection_routine_inspection.yaml": {
        "target_type": "column",
        "target_id": "food_inspection.inspektionen.typ",
    },
    "food_inspection/terms/term_food_inspection_structural_inspection.yaml": {
        "target_type": "column",
        "target_id": "food_inspection.inspektionen.typ",
    },
    "mondial_geo/terms/term_mondial_geo_gdp_per_capita.yaml": {
        "target_type": "column",
        "target_id": "mondial_geo.jing_ji.gdp",
    },
    "mondial_geo/terms/term_mondial_geo_percentage.yaml": {
        "target_type": "column",
        "target_id": "mondial_geo.min_zu_zu.bai_fen_bi",
    },
    "mondial_geo/terms/term_mondial_geo_population_density.yaml": {
        "target_type": "column",
        "target_id": "mondial_geo.guo_jia.ren_kou",
    },
    "movie_3/terms/term_movie_3_full_name.yaml": {
        "target_type": "column",
        "target_id": "movie_3.actor.first_name",
    },
    "movielens/terms/term_movielens_box_office_success_paradox.yaml": {
        "target_type": "table",
        "target_id": "movielens.directores",
    },
    "movielens/terms/term_movielens_negative_critical_reception.yaml": {
        "target_type": "table",
        "target_id": "movielens.calificaciones",
    },
    "olympics/terms/term_olympics_bmi.yaml": {
        "target_type": "column",
        "target_id": "olympics.ren_wu.ti_zhong",
    },
    "olympics/terms/term_olympics_gold_medal.yaml": {
        "target_type": "column",
        "target_id": "olympics.jiang_pai.jiang_pai_ming",
    },
    "restaurant/terms/term_restaurant_address.yaml": {
        "target_type": "table",
        "target_id": "restaurant.restaurant_adresse",
    },
    "retail_complains/terms/term_retail_complains_date_of_birth.yaml": {
        "target_type": "column",
        "target_id": "retail_complains.client.annee",
    },
    "retail_complains/terms/term_retail_complains_delay.yaml": {
        "target_type": "table",
        "target_id": "retail_complains.evenements",
    },
    "retail_complains/terms/term_retail_complains_full_name.yaml": {
        "target_type": "column",
        "target_id": "retail_complains.client.prenom",
    },
    "retails/terms/term_retails_delivery_time.yaml": {
        "target_type": "column",
        "target_id": "retails.detalle_pedido.fecha_recepcion",
    },
    "sales/terms/term_sales_full_name.yaml": {
        "target_type": "column",
        "target_id": "sales.kunden.vorname",
    },
    "soccer_2016/terms/term_soccer_2016_win_rate.yaml": {
        "target_type": "table",
        "target_id": "soccer_2016.spiel",
    },
    "student_loan/terms/term_student_loan_female_student.yaml": {
        "target_type": "table",
        "target_id": "student_loan.nan_xing",
    },
}


def main() -> int:
    missing = []
    for rel, binding in BINDINGS.items():
        path = AUTH / rel
        if not path.exists():
            missing.append(rel)
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        doc["binding"] = binding
        path.write_text(
            yaml.dump(
                doc,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=1000,
            ),
            encoding="utf-8",
        )
        print("bound", rel, "->", binding["target_id"])
    if missing:
        print("MISSING", missing, file=sys.stderr)
        return 1
    print(f"applied {len(BINDINGS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
