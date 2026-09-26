"""``tools/regrade.py`` re-executes model-written SQL, so it must use the harness's connector.

It opened a bare ``psycopg.connect``: no read-only session, no statement timeout, no row cap. A
statement that timed out in the original run could then finish and flip to correct, and the flip
would be reported as the grader change. These tests drive ``main`` with a stand-in connector.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from governed_bi import credentials
from governed_bi.datasource import postgres
from governed_bi.datasource.errors import ConnectionError as DbConnectionError
from governed_bi.datasource.errors import QueryError

TOOL = Path(__file__).resolve().parents[2] / "tools" / "regrade.py"


def _tool() -> Any:
    spec = importlib.util.spec_from_file_location("regrade_connector_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Connector:
    """Records how it was built and answers from a table; raises what it is told to."""

    built: list[dict[str, Any]] = []

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        type(self).built.append({"dsn": dsn, **kwargs})
        self.results: dict[str, Any] = _Connector.results

    results: dict[str, Any] = {}

    def __enter__(self) -> "_Connector":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str) -> tuple[list[str], list[list[Any]], bool]:
        answer = self.results[sql]
        if isinstance(answer, Exception):
            raise answer
        return ["a"], answer, False


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    _Connector.built = []
    monkeypatch.setattr(postgres, "PostgresConnector", _Connector)
    monkeypatch.setattr(credentials, "load_into_environ", lambda *a, **k: None)
    monkeypatch.setattr(credentials, "secret", lambda *names: "host=db dbname=bird")

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    gold = [
        {"question_id": "q1", "sql_rename": "GOLD1"},
        {"question_id": "q2", "sql_rename": "GOLD2"},
        {"question_id": "q3", "sql_rename": "GOLD3"},
    ]
    (dataset / "test_final.jsonl").write_text("".join(json.dumps(g) + "\n" for g in gold))
    artifact = tmp_path / "arm.jsonl"
    rows = [
        {"question_id": "q1", "outcome": "answered", "correct": False, "generated_sql": "P1"},
        {"question_id": "q2", "outcome": "answered", "correct": True, "generated_sql": "P2"},
        {"question_id": "q3", "outcome": "answered", "correct": False, "generated_sql": "P3"},
    ]
    artifact.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return {"dataset": dataset, "artifact": artifact, "out": tmp_path / "out" / "arm.regraded.jsonl"}


def _run(world: dict[str, Path], *extra: str) -> int:
    return _tool().main(
        [str(world["artifact"]), "--dataset", str(world["dataset"]), "--out", str(world["out"]), *extra]
    )


def _out(world: dict[str, Path]) -> dict[str, dict[str, Any]]:
    lines = world["out"].read_text(encoding="utf-8").splitlines()
    return {r["question_id"]: r for r in map(json.loads, lines)}


def test_statements_run_on_the_guarded_connector_with_the_register_bounds(world) -> None:
    _Connector.results = {"P1": [[1 / 3]], "GOLD1": [[0.33333333333333337]], "P2": [[5]],
                          "GOLD2": [[5]], "P3": [[1]], "GOLD3": [[2]]}
    assert _run(world) == 0

    (built,) = _Connector.built
    assert built["dsn"] == "host=db dbname=bird"
    assert built["statement_timeout_ms"] == 120_000
    assert built["max_rows"] == 200_000

    rows = _out(world)
    # q1: float noise. The digest rule calls it wrong, the current grader right, on the same rows.
    assert rows["q1"]["correct"] is True
    assert rows["q1"]["correct_digest_rule"] is False
    assert rows["q2"]["correct"] is rows["q2"]["correct_digest_rule"] is True
    assert rows["q3"]["correct"] is rows["q3"]["correct_digest_rule"] is False
    stamp = rows["q1"]["regrade"]
    assert stamp["grader"] == "rows-v3"
    assert stamp["statement_timeout_ms"] == 120_000
    assert stamp["correct_before"] is False
    assert "git_sha" in stamp and "working_tree_dirty" in stamp


def test_a_statement_fault_is_graded_as_the_harness_grades_it(world) -> None:
    _Connector.results = {"P1": QueryError("canceling statement due to statement timeout", sqlstate="57014"),
                          "GOLD1": [[1]], "P2": [[5]], "GOLD2": QueryError("bad", sqlstate="42P01"),
                          "P3": [[1]], "GOLD3": [[1]]}
    assert _run(world) == 0
    rows = _out(world)
    assert rows["q1"]["correct"] is False and rows["q1"]["grade_detail"] == "missing_prediction"
    assert rows["q2"]["correct"] is None and rows["q2"]["correct_digest_rule"] is None
    assert rows["q3"]["correct"] is True


def test_a_lost_connection_aborts_and_writes_nothing(world) -> None:
    _Connector.results = {"P1": [[1]], "GOLD1": [[1]], "P2": DbConnectionError("server closed"),
                          "GOLD2": [[5]], "P3": [[1]], "GOLD3": [[1]]}
    assert _run(world) == 3
    assert not world["out"].exists()
    assert not list(world["out"].parent.glob("*.partial"))


def test_an_existing_output_is_refused_without_overwrite(world) -> None:
    _Connector.results = {"P1": [[1]], "GOLD1": [[1]], "P2": [[5]], "GOLD2": [[5]], "P3": [[1]], "GOLD3": [[1]]}
    world["out"].parent.mkdir(parents=True)
    world["out"].write_text("kept\n")
    assert _run(world) == 2
    assert world["out"].read_text() == "kept\n"
    assert _Connector.built == []
    assert _run(world, "--overwrite") == 0
    assert len(_out(world)) == 3


def test_the_input_artifact_is_never_the_output(world) -> None:
    code = _tool().main([str(world["artifact"]), "--dataset", str(world["dataset"]),
                         "--out", str(world["artifact"]), "--overwrite"])
    assert code == 2
