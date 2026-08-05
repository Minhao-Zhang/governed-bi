"""The leak detector must bite, and must not bite the corpus it is meant to clear.

``docs/plans/corpus-summary-rewrite-2026-08-05.md`` outsources a corpus rewrite whose loudest
constraint is that the held-out set stays held out. ``tools/check_train_only.py`` is the only
mechanical check on that, and a check that cannot fail is this repository's named defect (L§7) --
its first draft reported **67 containments in the certified train-only gold layer** and then, once
the window was widened to clear them, **missed a question planted verbatim in a table body**.

So both directions are asserted here over hand-built corpora: a planted question is found, and
clean prose that merely shares vocabulary is not.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]


def _tool():
    """Loaded by path: ``tools/`` is not a package and this is how the script is run."""
    path = REPO / "tools" / "check_train_only.py"
    spec = importlib.util.spec_from_file_location("_check_train_only", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    if str(REPO / "tools") not in sys.path:
        sys.path.insert(0, str(REPO / "tools"))
    spec.loader.exec_module(module)
    return module


HELD_OUT = [
    {
        "question_id": "held_1",
        "question": "In which streets of the city of San Francisco are there restaurants "
        "that serve seafood?",
    },
    {"question_id": "held_2", "question": "How many restaurants are there?"},
]


def _corpus(root: pathlib.Path, *, table_body: str) -> pathlib.Path:
    (root / "restaurant").mkdir(parents=True, exist_ok=True)
    (root / "restaurant" / "restaurant.yaml").write_text(
        yaml.safe_dump(
            {
                "asset_type": "schema",
                "id": "restaurant",
                "name": "restaurant",
                "summary": "restaurant: dining directory",
            }
        ),
        encoding="utf-8",
    )
    (root / "restaurant" / "generalinfo.yaml").write_text(
        yaml.safe_dump(
            {
                "asset_type": "table",
                "id": "restaurant.generalinfo",
                "schema": "restaurant",
                "physical_name": "generalinfo",
                "summary": "generalinfo: name cuisine rating city",
                "body": table_body,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_a_planted_question_is_found(tmp_path: pathlib.Path) -> None:
    """The failure the widened window missed. The planted question has 7 content words, which
    is below the 8-window the first draft settled on to clear its false positives."""
    tool = _tool()
    root = _corpus(
        tmp_path / "leaky",
        table_body=(
            "One row per restaurant. Analysts ask: In which streets of the city of San "
            "Francisco are there restaurants that serve seafood?"
        ),
    )
    out = tool.scan(root, HELD_OUT)
    assert out["exact_containments"] >= 1, "a verbatim held-out question must be found"
    assert any(qid == "held_1" for _, qid in out["exact_examples"])
    assert any("table:restaurant.generalinfo" in where for where, _ in out["exact_examples"])


def test_shared_vocabulary_alone_is_not_a_containment(tmp_path: pathlib.Path) -> None:
    """The 67-false-positive class. This body is about the same subject in the same words and
    contains no question, so it must come back clean -- otherwise the check is unreadable."""
    tool = _tool()
    root = _corpus(
        tmp_path / "clean",
        table_body=(
            "One row per restaurant in San Francisco: its name, cuisine or food type, review "
            "rating, and the city and street it sits on. Grain is one restaurant."
        ),
    )
    out = tool.scan(root, HELD_OUT)
    assert out["exact_containments"] == 0, (
        f"clean prose was flagged: {out['exact_examples']}. A detector that fires on shared "
        "vocabulary is one the next reader learns to ignore"
    )
    assert out["test_provenance_citations"] == 0


def test_a_test_side_provenance_citation_fails(tmp_path: pathlib.Path) -> None:
    """The exact check. A corpus whose own audit names the held-out set was authored against it."""
    tool = _tool()
    root = _corpus(tmp_path / "cited", table_body="One row per restaurant.")
    (root / "restaurant" / "generalinfo.yaml").write_text(
        yaml.safe_dump(
            {
                "asset_type": "table",
                "id": "restaurant.generalinfo",
                "schema": "restaurant",
                "physical_name": "generalinfo",
                "summary": "generalinfo: name cuisine rating city",
                "body": "One row per restaurant.",
                "audit": {"evidence": "derived from held_1 in test_final.jsonl"},
            }
        ),
        encoding="utf-8",
    )
    out = tool.scan(root, HELD_OUT)
    assert out["test_provenance_citations"] >= 1
    assert out["exact_containments"] == 0, "this must fail on provenance, not on wording"


def test_the_window_is_not_silently_widened() -> None:
    """``NGRAM`` is the one arbitrary number in the tool and it was swept, not chosen.

    Pinned because raising it back to 8 is exactly what made the check blind -- 52% of held-out
    questions became ineligible and the planted leak went undetected, while every number the tool
    printed still looked healthy.
    """
    tool = _tool()
    assert tool.NGRAM <= 6, (
        f"NGRAM={tool.NGRAM} leaves too many held-out questions ineligible: at 8 only 52% have "
        "enough content words and a leak planted in a table body was missed"
    )
    assert "few_shot" in tool.CONTAINMENT_EXEMPT, (
        "few_shot summaries are train questions verbatim and are the entire false-positive "
        "population; excluding them is what lets NGRAM stay small"
    )


@pytest.mark.parametrize("kind", ["schema", "table"])
def test_authored_types_are_not_exempt(kind: str) -> None:
    """Only ``few_shot`` may skip the wording check. A curator writes the rest freely."""
    tool = _tool()
    assert kind not in tool.CONTAINMENT_EXEMPT
