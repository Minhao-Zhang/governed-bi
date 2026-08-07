"""AnalystCorpus and phase-boundary restamp."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


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


def test_no_tool_can_write_governance_onto_an_asset() -> None:
    """The control that replaces ``restamp_model_authored``, which had zero callers.

    ADR 0005 §1.5 says ``governance.excluded`` and certified human provenance are human-only,
    and ``corpus/provenance.py`` existed to strip forgeries at the phase boundary where a
    model-authored corpus is accepted. It was never called — by anything, ever (audit §10) — so
    a reader of §1.5 came away believing a boundary check ran.

    There is no such boundary in this tree, and the reason is stronger than the check would
    have been: the only model-authored write path, ``tools/graft_corpus_fields.py``, **refuses
    the whole ``governance`` field** rather than sanitising it, and refuses ``reliability`` and
    ``summary`` too. A refusal cannot be forged past; a re-stamp can be forgotten.

    So this asserts the refusal instead, and it asserts it against the tool's own declared list
    so that adding ``governance`` to the graftable set fails here. When a curator is built, it
    is the thing that owes a re-stamp, and ADR 0005 §1.5 now says so.
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_graft", root / "tools" / "graft_corpus_fields.py"
    )
    assert spec is not None and spec.loader is not None
    graft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graft)

    assert "governance" in graft.REFUSED, (
        "governance is no longer refused by the one tool that writes authored fields, and "
        "nothing re-stamps model-authored assets. ADR 0005 §1.5 would be unenforced."
    )
    assert not any(path.startswith("governance") for path in graft.GRAFTABLE), graft.GRAFTABLE
    # The two fields that carry a caveat or the indexed text are refused for their own reasons,
    # and a graft of either is how a softened decoy warning or a corpus swap would arrive.
    assert {"reliability", "summary"} <= set(graft.REFUSED)


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
