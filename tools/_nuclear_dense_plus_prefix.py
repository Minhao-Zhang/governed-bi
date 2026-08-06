"""Nuclear revise: start from dense summaries, ADD discriminating prefixes only.

Does not delete densify's content words or identifier lists — only prepends
sibling-discriminating nouns and refits to 250 without mid-word truncation.
Preserves authored `grain` fields.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from governed_bi.register.knobs import knob_default  # noqa: E402

DENSE = REPO / "corpora" / "_variant-dense-20260805"
AUTH = REPO / "corpora" / "_variant-authored-20260805"
CAP = int(knob_default("summary_max_chars"))

# Additive prefixes only — densify body stays
PREFIX: dict[str, str] = {
    "ice_hockey_draft": "draft-prospects scouting ELITEID",
    "hockey": "NHL-career Stanley-Cup HOF",
    "soccer_2016": "cricket IPL batsman bowling",
    "european_football_2": "FIFA club-football bookmaker",
    "professional_basketball": "NBA ABA NBL basketball",
    "movie_platform": "Mubi lists subscriber trialist",
    "movies_4": "TMDB production cast crew",
    "movielens": "MovieLens audience-ratings",
    "disney": "Walt-Disney animated voice-actors",
    "food_inspection": "inspection-score violation risk-category",
    "food_inspection_2": "sanitarian fines inspection-point",
    "restaurant": "California cuisine directory rating",
    "menu": "historical menu-page dish-price",
    "beer_factory": "rootbeer brewery brand review",
    "car_retails": "classicmodels scale-model productlines",
    "regional_sales": "sales-team store-location net-profit",
    "sales": "bicycle-parts free-gift",
    "superstore": "four-region Central East South West",
    "retails": "TPC-H partsupp lineitem",
    "retail_complains": "finance-complaints call-center",
    "law_episode": "Law-and-Order Primetime-Emmy",
    "simpson_episodes": "Simpsons season-20",
    "address": "ZIP-code zip_data demographics",
    "student_loan": "student-loan disability bankruptcy",
    "synthea": "synthetic-EHR patients encounters",
    "works_cycles": "AdventureWorks BOM workorders",
    "toxicology": "molecule atom bond toxicity",
}


def fit(text: str) -> str:
    text = text.replace("…", " ").replace("...", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= CAP:
        return text
    parts = text.split(" ")
    while parts and len(" ".join(parts)) > CAP:
        parts.pop()
    return " ".join(parts).rstrip(" .,-;:")


def main() -> None:
    # 1) Restore ALL schema+table summaries from dense (keep grain)
    for schema_dir in sorted(AUTH.iterdir()):
        if not schema_dir.is_dir() or schema_dir.name.startswith("_"):
            continue
        name = schema_dir.name
        # schema file
        sp = DENSE / name / f"{name}.yaml"
        dp = schema_dir / f"{name}.yaml"
        if sp.exists() and dp.exists():
            sdoc = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
            ddoc = yaml.safe_load(dp.read_text(encoding="utf-8")) or {}
            summ = fit(sdoc.get("summary") or ddoc.get("summary") or "")
            if name in PREFIX:
                # densify starts with "name: ..."; inject prefix after name:
                rest = summ.split(":", 1)[1].strip() if ":" in summ else summ
                summ = fit(f"{name}: {PREFIX[name]} {rest}")
            ddoc["summary"] = summ
            dp.write_text(
                yaml.dump(
                    ddoc,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                    width=1000,
                ),
                encoding="utf-8",
            )

        # tables
        src_t = DENSE / name / "tables"
        dst_t = schema_dir / "tables"
        if src_t.exists() and dst_t.exists():
            for tp in src_t.glob("*.yaml"):
                dp = dst_t / tp.name
                sdoc = yaml.safe_load(tp.read_text(encoding="utf-8")) or {}
                ddoc = yaml.safe_load(dp.read_text(encoding="utf-8")) if dp.exists() else {}
                grain = ddoc.get("grain")
                summ = sdoc.get("summary") or ""
                if summ.endswith("…") or summ.endswith("..."):
                    summ = fit(summ)
                ddoc["summary"] = summ
                if grain:
                    ddoc["grain"] = grain
                dp.write_text(
                    yaml.dump(
                        ddoc,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False,
                        width=1000,
                    ),
                    encoding="utf-8",
                )
        print("ok", name)

    print("done")


if __name__ == "__main__":
    main()
