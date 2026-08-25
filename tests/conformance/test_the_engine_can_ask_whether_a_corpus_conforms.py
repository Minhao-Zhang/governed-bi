"""The conformance rules are a library call, not a subprocess.

Until 2026-08-25 the twenty-two rules lived in ``tools/check_corpus_conformance.py``, so the only
way for anything in the engine to ask "does this corpus satisfy my rules" was to spawn that script
and parse its JSON — which is what ``tools/conformance_findings.py`` still does *on purpose*, since
the gates want the exit codes CI reads. This file holds the other half: :func:`problems_with_corpus`
and :func:`problems_with_asset_file` answer the question in process, and the CLI adds nothing to
their answer.

The last test is the one that keeps the seam honest. A rule set is easy to move and hard to keep
moved: the CLI could grow a rule of its own, and nothing else here would notice.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from governed_bi.conform import (
    RULE_DESCRIPTIONS,
    WHOLE_TREE_ONLY,
    Manifests,
    problems_with_asset_file,
    problems_with_corpus,
)

ROOT = Path(__file__).resolve().parents[2]
CONFORMANCE = ROOT / "tools" / "check_corpus_conformance.py"

#: A table with one column, clean against every rule that can be answered without a manifest.
#: Copied in shape from ``test_corpus_conformance_rules_fire.py``, not imported: a fixture shared
#: between a rules test and an interface test would let one file's edit silently change the other's
#: subject.
CLEAN_TABLE: dict[str, object] = {
    "asset_type": "table",
    "id": "addr.zip_data",
    "schema": "addr",
    "physical_name": "zip_data",
    "summary": "zip_data holds one row for each US postal point in the address database.",
    "body": "One row per ZIP code. Join to country on zip_code for the owning county.",
    "columns": [
        {
            "physical_name": "zip_code",
            "summary": "The zip_code that identifies this postal point, and the key others join on.",
            "body": "Five numeric digits, zero-padded. The join key for every other table here.",
        }
    ],
}


def _tree(tmp_path: Path, **assets: dict[str, object]) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(exist_ok=True)
    for name, document in assets.items():
        (root / f"{name}.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    return root


def test_a_clean_tree_reports_no_finding_and_names_its_population(tmp_path: Path) -> None:
    """``total`` is the verdict and ``asset_count`` is what it is a verdict about.

    Both, because a report over a tree with nothing in it also totals zero. ``asset_count`` is how
    a caller tells "clean" from "read nothing", which is the failure this package's own gates exist
    to catch on the corpus.
    """
    report = problems_with_corpus(_tree(tmp_path, zip_data=CLEAN_TABLE))

    assert report.total == 0, report.findings
    assert report.whole_tree is True
    # The table plus the column unpacked out of it: what the loader would build, not what the file
    # holds.
    assert report.assets_by_type == {"column": 1, "table": 1}
    assert report.asset_count == 2


def test_a_broken_asset_is_reported_under_the_rule_that_caught_it(tmp_path: Path) -> None:
    broken = {**CLEAN_TABLE, "summary": "TODO"}
    report = problems_with_corpus(_tree(tmp_path, zip_data=broken))

    assert "V2" in report.findings, f"the scaffold sentinel is V2: {sorted(report.findings)}"
    assert report.total >= 1
    # A rule with nothing to say is absent rather than present-and-empty, so "did this rule fire"
    # is one question and not two.
    assert all(report.findings[rule] for rule in report.findings)


def test_a_missing_corpus_raises_instead_of_reporting_clean(tmp_path: Path) -> None:
    """The vacuous pass this whole package exists to refuse, in its own front door.

    Zero findings over a directory that is not there reads exactly like a clean corpus, and the
    reader who would notice is the one who already trusts the number.
    """
    with pytest.raises(NotADirectoryError) as err:
        problems_with_corpus(tmp_path / "not-a-corpus")
    assert "not-a-corpus" in str(err.value)


def test_one_file_defers_the_rules_that_need_a_second_asset(tmp_path: Path) -> None:
    """``--file`` mode's contract, as a library call: deferred, never reported clean."""
    path = tmp_path / "zip_data.yaml"
    path.write_text(yaml.safe_dump(CLEAN_TABLE), encoding="utf-8")

    report = problems_with_asset_file(path)

    assert report.whole_tree is False
    assert set(WHOLE_TREE_ONLY) <= set(report.not_evaluated), (
        "a rule that needs the tree must say so, because its zero is indistinguishable from a pass: "
        f"{sorted(report.not_evaluated)}"
    )
    for rule in WHOLE_TREE_ONLY:
        assert rule not in report.findings


def test_a_rule_with_no_manifest_names_the_manifest_and_not_the_tree(tmp_path: Path) -> None:
    """Two different facts, and the second is the one a caller can act on.

    ``Manifests()`` supplies nothing, which is a caller omission; a path that does not exist is a
    checkout. V11, V12 and V15 are the three rules with an external input, and V12 is the
    held-out-split leakage gate — the one whose silent zero costs the most.
    """
    report = problems_with_corpus(_tree(tmp_path, zip_data=CLEAN_TABLE), Manifests())

    assert "trap manifest" in report.not_evaluated["V11"]
    assert "test split" in report.not_evaluated["V12"]
    assert "manifests" in report.not_evaluated["V15"]

    missing = tmp_path / "nowhere" / "trap_manifest.json"
    named = problems_with_corpus(
        _tree(tmp_path, zip_data=CLEAN_TABLE), Manifests(trap=missing)
    )
    assert str(missing) in named.not_evaluated["V11"], (
        "a path that was given and does not exist must be quoted, or nobody can fix the checkout"
    )


def test_v16_measures_what_the_renderer_renders(tmp_path: Path) -> None:
    """V16's cap is over ``serve/context.py::rendered_closure_chars`` and not a copy of it.

    The rule reached past that interface into two private functions until 2026-08-25 and did the
    addition itself. This asserts the numbers in the finding are the renderer's own, so the cap
    cannot start bounding something else while still reporting characters.
    """
    from governed_bi.conform.rules_tree import CLOSURE_CAP, check_delivery_closure
    from governed_bi.corpus.store import load_file
    from governed_bi.serve.context import rendered_closure_chars

    wide = {
        **CLEAN_TABLE,
        "columns": [
            {
                "physical_name": f"measurement_reading_channel_{i:04d}",
                "summary": (
                    f"The measurement_reading_channel_{i:04d} reading recorded against this row "
                    "by the meter."
                ),
                "body": "One float per interval.",
            }
            for i in range(600)
        ],
    }
    path = tmp_path / "wide.yaml"
    path.write_text(yaml.safe_dump(wide), encoding="utf-8")

    loaded, _ = load_file(path)
    tables = [a for a in loaded if type(a).__name__ == "TableAsset"]
    columns = [a for a in loaded if type(a).__name__ == "ColumnAsset"]
    own, roster = rendered_closure_chars(tables[0], columns)

    findings = check_delivery_closure([path])
    assert findings, f"{own + roster:,} chars against a {CLOSURE_CAP:,} cap should fire V16"
    assert f"{own + roster:,} chars" in findings[0]
    assert f"{own:,} table + {roster:,} roster" in findings[0]


def test_importing_the_rules_does_not_pull_the_graph() -> None:
    """``import governed_bi.conform`` must not cost langgraph, and only a fresh interpreter can say.

    ``serve/__init__.py`` re-exports the compiled graph, so a module-level
    ``governed_bi.serve.context`` in ``rules_tree.py`` would put langgraph behind every rule in the
    package. ADR 0016 records a trimmed CI install dying at exactly that import. The rule that needs
    the renderer imports it inside the function, and this is the assertion that keeps it there.
    """
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import governed_bi.conform; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in "
            "{'langgraph', 'langchain', 'fastapi'}))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]", (
        f"importing the rules pulled the serve stack: {done.stdout.strip()}"
    )


def test_the_cli_reports_exactly_what_the_library_found(tmp_path: Path) -> None:
    """The adapter adds no rule and drops none, which is what makes it thin.

    A CLI free to answer differently is a second implementation of the question, and the gates read
    the CLI: ``check_ratchet.py`` and ``check_corpus_delta.py`` both parse ``--json``. So the two
    are compared on the same tree, by rule and by count.
    """
    broken = {**CLEAN_TABLE, "summary": "TODO"}
    root = _tree(tmp_path, zip_data=broken)

    report = problems_with_corpus(root, Manifests())
    done = subprocess.run(
        [sys.executable, str(CONFORMANCE), "--corpus-dir", str(root), "--json",
         "--trap-manifest", str(tmp_path / "absent.json"),
         "--table-manifest", str(tmp_path / "absent.json"),
         "--rename-map", str(tmp_path / "absent.json"),
         "--test-split", str(tmp_path / "absent.jsonl")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)

    assert len(payload["findings"]) == report.total
    assert {row["rule"] for row in payload["findings"]} == set(report.findings)
    assert set(payload["not_evaluated"]) == set(report.not_evaluated)
    assert set(payload["findings"][0]) == {"rule", "where", "message"}


def test_every_rule_id_the_cli_prints_is_one_the_library_describes() -> None:
    """The description table is the library's, so the printed report cannot name a rule that is
    not there — or omit one that is. Twenty-two, which is the number ADR 0016 quotes."""
    assert len(RULE_DESCRIPTIONS) == 22, sorted(RULE_DESCRIPTIONS)
    assert set(WHOLE_TREE_ONLY) <= set(RULE_DESCRIPTIONS)
