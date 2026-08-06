"""Revise authored miss schemas: dense floor + sibling-discriminating lead nouns.

Copies dense schema/table summaries for selected schemas, then rewrites schema
summaries to lead with discriminating tokens while keeping densify's identifier
tail. Never truncates mid-word; drops whole terms to fit 250.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from governed_bi.register.knobs import knob_default  # noqa: E402

DENSE = REPO / "corpora" / "_variant-dense-20260805"
AUTH = REPO / "corpora" / "_variant-authored-20260805"
CAP = int(knob_default("summary_max_chars"))

# schema -> lead discriminating phrase (no function words)
LEADS: dict[str, str] = {
    "ice_hockey_draft": "scouting draft prospects ELITEID height weight CSS junior leagues NOT career HOF Stanley",
    "hockey": "NHL WHA career scoring goalies Stanley Cup HOF standings NOT draft prospects",
    "soccer_2016": "cricket IPL ball-by-ball batsman bowling wickets toss umpire NEVER football soccer",
    "european_football_2": "European club football soccer FIFA attributes leagues bookmaker odds NOT cricket basketball",
    "professional_basketball": "NBA ABA NBL basketball all-star rebounds coaches draft NOT hockey football cricket",
    "movie_platform": "Mubi social lists critic reviews subscriber trialist rating-score NOT MovieLens production rental",
    "movies_4": "TMDB production cast crew keywords companies box-office budget NOT user-ratings Mubi rental",
    "movielens": "MovieLens audience ratings actors directors occupation release-year NOT Mubi TMDB Disney",
    "disney": "Walt Disney animated voice-actors heroes villains songs segment-revenue NOT MovieLens TMDB",
    "food_inspection": "food-safety inspection score violation risk businesses owner San-Francisco NOT cuisine directory",
    "food_inspection_2": "municipal food-safety sanitarian employee inspection-point fines license taverns NOT SF-score directory",
    "restaurant": "California restaurant cuisine food-type review-rating city county region directory NOT inspection violation",
    "menu": "historical menu-page dish price venue sponsor event appearance-history NOT live restaurant inspection",
    "beer_factory": "root-beer brand brewery store customer transaction star-review geolocation NOT finance complaints",
    "car_retails": "classicmodels scale-model productlines offices payments MSRP buyPrice Sales-Rep NOT AdventureWorks bike-parts",
    "regional_sales": "US regional sales-team store-location warehouse channel net-profit discount orders NOT TPC-H complaints",
    "sales": "bicycle-parts retailer product quantity employee customer free-gift NOT classicmodels Superstore",
    "superstore": "Superstore four-region Central East South West order-lines profit discount NOT classicmodels TPC-H",
    "retails": "TPC-H wholesale nation region partsupp lineitem account-balance NOT classicmodels Superstore",
    "retail_complains": "consumer-finance complaints call-center reviews credit mortgage deposits NOT product sales orders",
    "law_episode": "Law-and-Order episode credits awards Primetime star-votes id-keyed NOT Simpsons",
    "simpson_episodes": "Simpsons season-20 character awards name-keyed credits star-votes NOT Law-and-Order",
    "address": "ZIP-code demographics households income housing elevation metro congressional area-codes",
    "student_loan": "student-loan disability bankruptcy unemployment enlistment enrollment absence payment-due name-lists",
    "synthea": "synthetic EHR patients encounters conditions medications allergies immunizations claims prevalence",
    "works_cycles": "AdventureWorks manufacturing sales cycles workorders BOM specialoffers NOT classicmodels scale-model",
}


def fit(text: str, cap: int = CAP) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # strip leftover truncation markers from densify
    text = text.replace("…", " ").replace("...", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= cap:
        return text
    parts = text.split(" ")
    while parts and len(" ".join(parts)) > cap:
        parts.pop()
    out = " ".join(parts).rstrip(" .,-;:")
    if len(out) > cap:
        raise SystemExit(f"cannot fit: {len(out)} {out!r}")
    return out


def identifier_tail(dense_summary: str, name: str) -> str:
    """Keep densify's table/identifier list portion when present."""
    # densify format: "{name}: content words. N tables — id1, id2, ..."
    m = re.search(r"\d+\s+tables\s+[—\-�]+\s*(.*)$", dense_summary)
    if m:
        return m.group(1).strip().rstrip("….").strip()
    # fallback: everything after first period
    if ". " in dense_summary:
        return dense_summary.split(". ", 1)[1].strip()
    return ""


MISS_SCHEMAS = {
    "address",
    "beer_factory",
    "car_retails",
    "european_football_2",
    "food_inspection",
    "ice_hockey_draft",
    "law_episode",
    "movie_platform",
    "movies_4",
    "professional_basketball",
    "regional_sales",
    "restaurant",
    "student_loan",
    "synthea",
    # siblings that stole shortlist slots
    "hockey",
    "soccer_2016",
    "movielens",
    "disney",
    "menu",
    "food_inspection_2",
    "works_cycles",
}


def main() -> None:
    for schema, lead in LEADS.items():
        # Restore dense table summaries only for miss/sibling schemas (identifier density)
        if schema in MISS_SCHEMAS:
            src_tables = DENSE / schema / "tables"
            dst_tables = AUTH / schema / "tables"
            if src_tables.exists() and dst_tables.exists():
                for sp in src_tables.glob("*.yaml"):
                    dp = dst_tables / sp.name
                    sdoc = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
                    ddoc = yaml.safe_load(dp.read_text(encoding="utf-8")) if dp.exists() else {}
                    grain = ddoc.get("grain")  # preserve authored grain
                    if sdoc.get("summary"):
                        summ = sdoc["summary"]
                        if summ.endswith("…") or summ.endswith("..."):
                            summ = fit(summ.rstrip(".…"))
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

        # schema summary = lead + identifier tail from dense
        sp = DENSE / schema / f"{schema}.yaml"
        dp = AUTH / schema / f"{schema}.yaml"
        sdoc = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        ddoc = yaml.safe_load(dp.read_text(encoding="utf-8")) or {}
        tail = identifier_tail(sdoc.get("summary") or "", schema)
        # Prefer densify's english table meanings if tail is short
        if len(tail) < 20:
            tail = sdoc.get("summary", "").split(": ", 1)[-1]
        new = fit(f"{schema}: {lead}. {tail}")
        # ensure schema name present
        if schema.casefold() not in new.casefold():
            new = fit(f"{schema}: {new}")
        ddoc["summary"] = new
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
        print(f"{schema}: {len(new)} | {new}")


if __name__ == "__main__":
    main()
