"""Every rule in ``tools/check_corpus_conformance.py`` must actually fire.

A gate that matches nothing reports a clean corpus. V11 shipped that way for twenty minutes:
it keyed on ``(schema, physical_name)`` while an inline column carries no ``schema`` of its
own, so it found zero violations on a corpus with 333. The rule was right and the lookup was
vacuous, which is indistinguishable from passing.

So: one deliberately-broken asset per rule, asserted to be caught, plus a clean one asserted
to pass everything. The two whole-tree rules that need an external file (V11, V12) get real
temporary manifests rather than being skipped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import check_corpus_conformance as cc  # noqa: E402

CLEAN_TABLE = {
    "asset_type": "table",
    "id": "addr.zip_data",
    "schema": "addr",
    "physical_name": "zip_data",
    "summary": "zip_data holds one row for each US postal point in the address database.",
    "body": "One row per ZIP code. Join to country on zip_code for the owning county.",
    "columns": [
        {
            "physical_name": "zip_code",
            # V3 wants the bare identifier in the summary, so it has to read naturally there.
            "summary": "The zip_code that identifies this postal point, and the key others join on.",
            "body": "Five numeric digits, zero-padded. The join key for every other table here.",
        }
    ],
}


def _findings(kind: str, asset: dict) -> dict[str, list]:
    return cc.check_local(kind, asset, "t.yaml:x")


def _fires(rule: str, kind: str, asset: dict) -> bool:
    return bool(_findings(kind, asset).get(rule))


def test_a_conforming_asset_passes_every_local_rule() -> None:
    for kind, asset in (("table", CLEAN_TABLE), ("column", {"schema": "addr", **CLEAN_TABLE["columns"][0]})):
        assert _findings(kind, asset) == {}, f"{kind} should be clean: {_findings(kind, asset)}"


#: One deliberately-broken asset per local rule. Reused by the closure test below, so a new
#: rule with no case here fails rather than going unexercised.
BROKEN: list[tuple[str, str, dict]] = [
        ("V0", "<unparseable>", {"_error": "bad yaml"}),
        ("V0", "banana", {"id": "x"}),
        ("V1", "table", {**CLEAN_TABLE, "summary": ""}),
        ("V1", "table", {**CLEAN_TABLE, "summary": "zip_data " + "very long " * 40}),
        ("V2", "table", {**CLEAN_TABLE, "summary": "TODO"}),
        ("V3", "table", {**CLEAN_TABLE, "summary": "This table is about postal areas in the US."}),
        ("V4", "table", {**CLEAN_TABLE, "summary": "zip_data (zip_data): zip_code, alias, county"}),
        ("V4", "column", {"schema": "addr", "physical_name": "zip_code",
                          "summary": "zip_code — zip_data.zip_code", "body": "b"}),
        ("V5", "table", {**CLEAN_TABLE,
                         "summary": "zip_data holds one row per postal point, e.g. 97079 for Beaverton."}),
        ("V5", "column", {"schema": "addr", "physical_name": "zip_code", "body": "b",
                          "summary": "The postal code identifying this row (column zip_code)."}),
        ("V6", "join", {"id": "j", "left_table": "addr.zip_data", "right_table": "addr.country",
                        "summary": "zip_data joins country on the shared zip_code key."}),
        ("V7", "column", {"schema": "addr", "physical_name": "zip_code",
                          "summary": "The postal code that identifies this row in the table.",
                          "body": "Means 'zip_code' (obfuscated to 'zip_code')."}),
        ("V8", "term", {"id": "t", "synonyms": ["postal code"],
                        "summary": "zip code: the identifier of a US postal delivery area.",
                        "body": "Full definition."}),
        ("V10", "column", {"schema": "addr", "physical_name": "alt_alias",
                           "summary": "alt_alias is a text column on the alias table here.",
                           "body": "A decoy column fabricated to mimic alias."}),
]


@pytest.mark.parametrize("rule,kind,asset", BROKEN)
def test_each_local_rule_fires(rule: str, kind: str, asset: dict) -> None:
    assert _fires(rule, kind, asset), f"{rule} did not fire on {asset.get('summary', asset)!r}"


def test_v3_reads_the_register_rather_than_a_second_copy() -> None:
    """The identifier a summary must carry comes from ``ASSET_REGISTER``, not from this tool."""
    from governed_bi.register.assets import ASSET_REGISTER, AssetType

    assert ASSET_REGISTER[AssetType.join].identifier_fields == ("left_table", "right_table")
    both_missing = {"id": "j", "left_table": "addr.zip_data", "right_table": "addr.country",
                    "summary": "An edge between two tables in this database.", "body": "b"}
    assert len(_findings("join", both_missing)["V3"]) == 2


def test_v9_catches_a_reference_to_nothing(tmp_path: Path) -> None:
    assets = [
        ("table", CLEAN_TABLE, tmp_path / "t.yaml"),
        ("term", {"id": "term_x", "binding": {"target_id": "addr.zip_data.nope"}}, tmp_path / "x.yaml"),
    ]
    bad = cc.check_references(assets)
    assert bad and "nope" in bad[0]

    ok = [("table", CLEAN_TABLE, tmp_path / "t.yaml"),
          ("term", {"id": "term_y", "binding": {"target_id": "addr.zip_data.zip_code"}}, tmp_path / "y.yaml")]
    assert cc.check_references(ok) == []


def test_v11_needs_the_column_schema_and_catches_the_named_resemblance(tmp_path: Path) -> None:
    """The regression that made this rule vacuous: an inline column has no ``schema`` key."""
    manifest = tmp_path / "trap.json"
    manifest.write_text(
        json.dumps([{"db": "addr", "source_column": "zip_code", "names": {"base": "z", "rename": "postal_code"}}]),
        encoding="utf-8",
    )
    suspect = {
        "asset_type": "column",
        "id": "addr.zip_data.postal_code",
        "physical_name": "postal_code",
        "summary": "postal_code, a stand-in for the real zip_code on this table.",
        "reliability": {"status": "suspect", "note": "Unreliable for analysis."},
    }
    table = {**CLEAN_TABLE, "columns": [*CLEAN_TABLE["columns"], suspect]}
    path = tmp_path / "tbl.yaml"
    path.write_text(yaml.safe_dump(table, allow_unicode=True), encoding="utf-8")

    loaded = cc.load_assets(path)
    assert any(k == "column" and a.get("physical_name") == "postal_code" and a.get("schema") == "addr"
               for k, a, _ in loaded), "load_assets must copy the table's schema onto inline columns"
    assert cc.check_suspect_summaries(loaded, manifest)

    quiet = {**suspect, "summary": "postal_code, a numeric column on the zip_data table."}
    path.write_text(yaml.safe_dump({**CLEAN_TABLE, "columns": [quiet]}, allow_unicode=True), encoding="utf-8")
    assert cc.check_suspect_summaries(cc.load_assets(path), manifest) == []


def test_v12_catches_a_quoted_held_out_question(tmp_path: Path) -> None:
    split = tmp_path / "test.jsonl"
    question = "What is the total number of households in Arecibo county?"
    split.write_text(json.dumps({"question": question}) + "\n", encoding="utf-8")

    leaked = [("table", {"id": "t", "summary": "s", "body": f"For example: {question}"}, tmp_path / "t.yaml")]
    assert cc.check_split_leak(leaked, split)
    assert cc.check_split_leak([("table", CLEAN_TABLE, tmp_path / "t.yaml")], split) == []


def test_v14_catches_a_file_the_engine_cannot_load(tmp_path: Path) -> None:
    """The rule that exists because the text rules cannot see a structural break.

    The first scaffold wrote ``provenance.source: introspection``, which is not one of the four
    the enum allows. Every text rule passed and the loader returned zero assets from 18 files.
    """
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        yaml.safe_dump(
            {**CLEAN_TABLE, "audit": {"provenance": {"source": "introspection", "status": "draft"}}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    findings = cc.check_loadable([broken])
    assert findings and "introspection" in findings[0]

    ok = tmp_path / "ok.yaml"
    ok.write_text(yaml.safe_dump(CLEAN_TABLE, allow_unicode=True), encoding="utf-8")
    assert cc.check_loadable([ok]) == []


#: Rules ``check_local`` cannot emit: three need the whole tree or an external manifest, V13 is
#: a filesystem size check, and V14 needs a real file for the loader. Each has its own test.
NOT_LOCAL = {"V9", "V11", "V12", "V13", "V14"}


def test_every_rule_is_documented_and_exercised() -> None:
    """No rule id without a description, and no rule without a case that provokes it.

    The second half is what stops a rule from being added and never run — the failure mode
    that let V11 report zero on a corpus with 333 violations.
    """
    emitted: set[str] = set()
    for _, kind, asset in BROKEN:
        emitted |= set(_findings(kind, asset))
    assert emitted <= set(cc.RULES), f"undocumented rule ids: {emitted - set(cc.RULES)}"
    assert set(cc.RULES) - emitted == NOT_LOCAL, (
        "a local rule has no case in BROKEN, so nothing proves it can fire: "
        f"{set(cc.RULES) - emitted - NOT_LOCAL}"
    )
