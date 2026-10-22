"""The analyst view drops excluded assets and records their column keys."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))



def test_excluded_assets_leave_the_analyst_view_but_stay_in_the_raw_load() -> None:
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.schema import ColumnAsset, Governance

    raw = [
        ColumnAsset(
            id="s.t.ok",
            schema="s",
            parent_table="t",
            physical_name="ok",
            summary="ok - visible",
        ),
        ColumnAsset(
            id="s.t.ssn",
            schema="s",
            parent_table="t",
            physical_name="ssn",
            summary="ssn - secret",
            governance=Governance(excluded=True, reason="PII", by="human"),
        ),
    ]
    view = for_analyst(raw)
    assert "s.t.ok" in view.by_id
    assert "s.t.ssn" not in view.by_id
    assert "s.t.ssn" in view.excluded_columns or "t.ssn" in view.excluded_columns


def test_no_tool_writes_authored_corpus_fields() -> None:
    """No tool under ``tools/`` may author corpus fields."""
    from pathlib import Path

    tools = Path(__file__).resolve().parents[2] / "tools"
    assert not (tools / "graft_corpus_fields.py").exists()
    offenders = [
        path.name
        for path in tools.glob("*.py")
        if "GRAFTABLE" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_b10_excluded_column_cannot_be_retrieved_tables_via_allow_set_drift() -> None:
    """An excluded column is absent from the view, and a statement that names it fails the column check."""
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.schema import ColumnAsset, Governance
    from governed_bi.govern.check import check
    from governed_bi.govern.layers import Layer

    raw = [
        ColumnAsset(
            id="customers.id",
            schema="",
            parent_table="customers",
            physical_name="id",
            summary="id - key",
        ),
        ColumnAsset(
            id="customers.ssn",
            schema="",
            parent_table="customers",
            physical_name="ssn",
            summary="ssn - PII",
            governance=Governance(excluded=True, reason="PII", by="human"),
        ),
    ]
    corpus = for_analyst(raw)
    assert "customers.ssn" not in corpus.by_id
    result = check(
        "SELECT c.ssn FROM customers c",
        retrieved_tables=frozenset({"customers"}),
        corpus=corpus,
    )
    assert result["passed"] is False
    assert result["failed_layer"] is Layer.COLUMNS
