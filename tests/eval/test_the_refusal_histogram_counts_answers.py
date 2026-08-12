"""The refusal histogram counts attempts that tried to answer, not introspection probes.

``sample_rows`` goes through the same governance layers as a draft answer and lands in the same
ledger, so a driver that counts the raw ``attempts`` list reports a column probe as governance
declining to answer. On the v3-fold arm that was 129 ``passed`` and 3 ``r_ambiguous_fold``
attempts — inside the very histogram the ``attempts`` field was added to support.

``serve/ledger.py`` states the rule: three copies of "which attempts count" is three answers.
``execution_from_attempts``, ``stamp`` and ``agent_core`` all route through
``answering_attempts``; the driver was a fourth copy that disagreed. The row still carries every
attempt and its ``path``, so nothing is lost from the artifact — the filter belongs in the
reader.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def refusal_layers():
    # In ``datalake_report.py`` since the driver was split at the plan / execute / report seam
    # (architecture review C2) to get back under the 1 000-line hard cap.
    path = REPO / "tools" / "datalake_report.py"
    spec = importlib.util.spec_from_file_location("_run_datalake_eval_histogram", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._refusal_layers


def _attempt(reason, *, path, passed=False, layer="COLUMNS"):
    return {"layer": layer, "reason_code": reason, "passed": passed, "path": path}


def test_a_sample_probe_refusal_is_not_counted_as_a_governance_refusal(refusal_layers, capsys):
    rows = [
        {
            "attempts": [
                _attempt("r_column_not_allowed", path="sample"),
                _attempt("r_table_not_licensed", path="agent", layer="TABLES"),
            ]
        }
    ]
    refusal_layers(rows)
    out = capsys.readouterr().out
    assert "TABLES/r_table_not_licensed" in out
    assert "r_column_not_allowed" not in out


def test_a_turn_whose_only_refusals_were_probes_prints_no_histogram(refusal_layers, capsys):
    """Not merely a smaller count — the whole section must be absent.

    A histogram built only from probes reads as "governance refused this run" when governance
    refused nothing the agent offered as an answer.
    """
    rows = [{"attempts": [_attempt("r_column_suspect", path="sample")]}]
    refusal_layers(rows)
    assert capsys.readouterr().out == ""


def test_answering_refusals_are_still_counted(refusal_layers, capsys):
    rows = [
        {"attempts": [_attempt("r_star_projection", path="agent", layer="BINDING")]},
        {"attempts": [_attempt("r_star_projection", path="agent", layer="BINDING")]},
    ]
    refusal_layers(rows)
    out = capsys.readouterr().out
    assert "BINDING/r_star_projection" in out
    assert "2" in out


def test_a_passing_answering_attempt_is_not_a_refusal(refusal_layers, capsys):
    rows = [{"attempts": [_attempt(None, path="agent", passed=True)]}]
    refusal_layers(rows)
    assert capsys.readouterr().out == ""
