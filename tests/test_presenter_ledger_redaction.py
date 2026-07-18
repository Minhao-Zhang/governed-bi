"""Client-facing provenance must not ship bulk result rows (audit finding S7).

The middleware ledger snapshots full executed rows for finalize's reuse; that
internal record must be redacted before it reaches the API client. Redaction must
NOT mutate the internal ``Answer.provenance`` (finalize/cache rely on the rows).
"""

from __future__ import annotations

from governed_bi.viz.presenter import _redact_provenance_for_client


def test_ledger_rows_redacted_and_input_not_mutated():
    prov = {
        "governance_ledger": [
            {
                "action": "run_query",
                "verdict": "pass",
                "result": {
                    "columns": ["a"],
                    "rows": [(1,), (2,)],
                    "row_count": 2,
                    "truncated": False,
                },
            },
            {"action": "inspect_schema"},  # entry with no result key
        ],
        "other": "kept",
    }

    out = _redact_provenance_for_client(prov)

    res = out["governance_ledger"][0]["result"]
    assert res["rows"] == []
    assert res["rows_redacted"] is True
    assert res["columns"] == ["a"]  # columns/row_count kept for audit
    assert res["row_count"] == 2
    assert out["governance_ledger"][1] == {"action": "inspect_schema"}
    assert out["other"] == "kept"

    # The internal provenance is untouched (rows still present for finalize/cache).
    assert prov["governance_ledger"][0]["result"]["rows"] == [(1,), (2,)]


def test_no_governance_ledger_is_noop():
    prov = {"x": 1}
    assert _redact_provenance_for_client(prov) == {"x": 1}
