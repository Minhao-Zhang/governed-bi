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


def test_restamp_strips_excluded_and_certified_human_audit() -> None:
    from governed_bi.corpus.provenance import restamp_model_authored
    from governed_bi.corpus.schema import (
        Audit,
        ColumnAsset,
        Governance,
        Provenance,
        ProvenanceSource,
        ProvenanceStatus,
    )

    forged = ColumnAsset(
        id="s.t.col",
        schema="s",
        parent_table="t",
        physical_name="col",
        summary="col - forged",
        governance=Governance(excluded=True, reason="model says so", by="curator"),
        audit=Audit(
            provenance=Provenance(
                source=ProvenanceSource.human,
                status=ProvenanceStatus.certified,
            )
        ),
    )
    cleaned = restamp_model_authored(forged, model="test-model")
    assert cleaned.governance.excluded is False
    assert cleaned.audit is not None
    assert cleaned.audit.provenance is not None
    assert cleaned.audit.provenance.source is ProvenanceSource.curator
    assert cleaned.audit.provenance.status is ProvenanceStatus.proposed
    assert cleaned.audit.provenance.model == "test-model"


def test_a_proposed_draft_is_invisible_to_the_analyst_but_a_certified_one_is_not() -> None:
    """The other half of the draft/approve split (corpus/drafts.py): without this, a
    freshly-restamped `proposed` write would index and serve exactly like a certified one."""
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.schema import (
        Audit,
        FewShotAsset,
        Provenance,
        ProvenanceSource,
        ProvenanceStatus,
    )

    def few_shot(status: ProvenanceStatus) -> FewShotAsset:
        return FewShotAsset(
            id=f"fs.{status.value}",
            schema="s",
            sql="SELECT 1",
            summary="example",
            audit=Audit(provenance=Provenance(source=ProvenanceSource.curator, status=status)),
        )

    seeded_no_audit = FewShotAsset(id="fs.seeded", schema="s", sql="SELECT 1", summary="example")
    raw = [few_shot(ProvenanceStatus.proposed), few_shot(ProvenanceStatus.certified), seeded_no_audit]
    view = for_analyst(raw)
    assert "fs.proposed" not in view.by_id
    assert "fs.certified" in view.by_id
    assert "fs.seeded" in view.by_id  # no audit trail at all is not evidence of a draft


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
