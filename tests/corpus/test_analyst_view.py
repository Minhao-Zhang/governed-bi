"""AnalystCorpus and phase-boundary restamp."""

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
    """The graft tool was the only model-authored write path. Absence is the control now."""
    from pathlib import Path

    tools = Path(__file__).resolve().parents[2] / "tools"
    assert not (tools / "graft_corpus_fields.py").exists()
    offenders = [
        path.name
        for path in tools.glob("*.py")
        if "GRAFTABLE" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_b10_excluded_column_cannot_be_licensed_via_allow_set_drift() -> None:
    """Excluded in the raw corpus, absent from the analyst view: referencing it refuses."""
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
    verdict = check(
        "SELECT c.ssn FROM customers c",
        licensed=frozenset({"customers"}),
        corpus=corpus,
    )
    assert verdict["passed"] is False
    assert verdict["failed_layer"] is Layer.COLUMNS
