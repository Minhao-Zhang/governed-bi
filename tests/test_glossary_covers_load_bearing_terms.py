"""Glossary must define the load-bearing ops/eval terms (batch-m2 N5).

``REQUIRED`` is hardcoded here on purpose. Loading the expected set from
``docs/glossary.md`` itself would make this test always pass.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GLOSSARY = REPO_ROOT / "docs" / "glossary.md"

# Hardcoded — never derive from the glossary under test.
REQUIRED = (
    "arm",
    "rung",
    "ledger",
    "stamp",
    "scope",
    "tier",
    "verdict",
    "block",
    "kind",
    "db_id",
    "resume",
    "budget (always-note)",
    "budget (tool-call / step)",
    "budget (table)",
    "budget (node)",
    "suspect",
    "outcome",
    "pooled",
    "licensed",
    "driver",
    "refuse",
    "quotable",
    "solver",
    "twin",
    "comparable",
    "headline",
    "crashed",
    "replicate",
    "fold",
    "shortlist",
    "graded_delivery",
    "routing_escaped",
    "promote",
    "licensed_tables",
    "claim_ready",
    "hygiene_ok",
    "stage",
    "step",
    "index",
    "layer",
    "pin",
    "harness",
)

TRAPS = (
    "graded_delivery",
    "safety_clearance=False",
    "semantic_assurance=unflagged",
    "ledger",
    "stamp",
    "scope",
    "tier",
)

_TERM_CELL = re.compile(r"^\|\s*\*\*(.+?)\*\*\s*\|")


def _term_rows(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        m = _TERM_CELL.match(line)
        if m:
            found.add(m.group(1).strip())
    return found


def test_glossary_has_a_row_for_every_required_ops_eval_term():
    text = GLOSSARY.read_text(encoding="utf-8")
    rows = _term_rows(text)
    missing = [t for t in REQUIRED if t not in rows]
    assert not missing, f"docs/glossary.md missing own-row terms: {missing}"


def test_homonym_traps_section_is_greppable():
    text = GLOSSARY.read_text(encoding="utf-8")
    assert "## Homonym traps" in text
    for trap in TRAPS:
        assert f"**{trap}**" in text, f"trap {trap!r} not greppable in glossary"


def test_homonym_traps_come_before_product_term_table():
    text = GLOSSARY.read_text(encoding="utf-8")
    traps_at = text.index("## Homonym traps")
    domain_at = text.index("| **Domain** |")
    assert traps_at < domain_at, "homonym traps must sit before the product term table"


def test_ops_eval_polysemes_cite_file_line():
    """batch-m2: each sense of a polysemous ops term gets a file:line anchor."""
    text = GLOSSARY.read_text(encoding="utf-8")
    ops = text.split("## Ops and eval vocabulary", 1)[1]
    for term in ("ledger", "stamp", "scope", "tier", "arm", "rung", "budget (always-note)"):
        row = next(
            (ln for ln in ops.splitlines() if ln.startswith(f"| **{term}** |")),
            None,
        )
        assert row is not None, term
        assert re.search(r"`[^`]+\.py:\d+", row), f"{term} row lacks file:line cite: {row}"
