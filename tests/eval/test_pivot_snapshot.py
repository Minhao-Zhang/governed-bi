"""``tools/pivot_snapshot.py`` and ``tools/pivot_compare.py`` on a hand-made corpus.

The snapshot is the instrument later milestones lean on to say "no verdict moved", so these tests
pin the two properties that make it one: it is the same bytes on every run, and it touches neither
the network nor a model. The corpus is built here, not read from ``../BIRD-corpus``, so the test
runs everywhere.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pivot_compare
import pivot_snapshot
import pytest

from governed_bi.corpus.schema import JoinAsset, SchemaAsset, TableAsset
from governed_bi.corpus.store import write

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "pivot_snapshot.py"

_QUESTIONS = (
    ("q1", "how many customers placed an order", "sales_a"),
    ("q2", "voltage reading per sensor device", "ops_b"),
    ("q3", "which invoices are unpaid", "sales_a"),
)


def _write_corpus(root: pathlib.Path, *, sensors_summary: str = "sensors voltage readings") -> None:
    write(root, SchemaAsset(id="sales_a", name="sales_a", summary="sales_a customers orders invoices"))
    write(root, SchemaAsset(id="ops_b", name="ops_b", summary="ops_b sensors and shipments"))
    for schema, name, summary in (
        ("sales_a", "customers", "customers who buy goods"),
        ("sales_a", "orders", "orders placed by customers"),
        ("sales_a", "invoices", "invoices billed for orders"),
        ("ops_b", "sensors", sensors_summary),
        ("ops_b", "shipments", "outbound shipments"),
    ):
        write(root, TableAsset(id=f"{schema}.{name}", schema=schema, physical_name=name, summary=summary))
    for left, right in (("customers", "orders"), ("orders", "invoices")):
        write(
            root,
            JoinAsset(
                id=f"join_{left}_{right}",
                left_table=f"sales_a.{left}",
                right_table=f"sales_a.{right}",
                on=f"sales_a.{left}.id = sales_a.{right}.{left}_id",
                summary=f"{left} to {right}",
            ),
            namespace="sales_a",
        )


def _write_questions(path: pathlib.Path) -> pathlib.Path:
    rows = [
        {
            "question_id": qid,
            "question": text,
            "db_id": db,
            "sql_rename": "SELECT 1",
        }
        for qid, text, db in _QUESTIONS
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[pathlib.Path, pathlib.Path]:
    root = tmp_path_factory.mktemp("pivot")
    _write_corpus(root / "corpus")
    return root / "corpus", _write_questions(root / "questions.jsonl")


def _questions(path: pathlib.Path) -> list[dict[str, Any]]:
    from governed_bi.eval.datalake import load_questions

    return load_questions(path)


# ── no network, no model ──────────────────────────────────────────────────────


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Refuse every non-loopback connection and every model client; record any attempt.

    Loopback stays open because asyncio's self-pipe is a connected socket pair on Windows.
    """
    attempts: list[str] = []

    def refuse(what: str) -> Any:
        def _raise(*_a: Any, **_k: Any) -> Any:
            attempts.append(what)
            raise AssertionError(f"offline test: {what}")

        return _raise

    real_connect = socket.socket.connect

    def connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            attempts.append(f"connect {address}")
            raise AssertionError(f"offline test: connect {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))

    from governed_bi.model import provider

    for name in ("chat_model", "embedder"):
        monkeypatch.setattr(provider, name, refuse(f"model client: provider.{name}"))
    import langchain_openai
    import openai

    for klass in (langchain_openai.ChatOpenAI, openai.OpenAI, openai.AsyncOpenAI):
        monkeypatch.setattr(klass, "__init__", refuse(f"model client: {klass.__name__}"))
    yield attempts


def test_the_trap_is_armed(offline: list[str]) -> None:
    """Without this, "nothing was constructed" could mean the trap never worked."""
    from governed_bi.model import provider

    with pytest.raises(AssertionError):
        provider.chat_model("any", surface="agent")
    with pytest.raises(AssertionError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    assert len(offline) == 2


def test_a_run_touches_neither_the_network_nor_a_model(
    inputs: tuple[pathlib.Path, pathlib.Path], offline: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, questions = inputs
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    rows = pivot_snapshot.snapshot(corpus, _questions(questions))

    assert offline == [], f"the snapshot reached out: {offline}"
    assert {r["kind"] for r in rows} == {"meta", "question", "statement", "probe"}


# ── shape and determinism ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def rows(inputs: tuple[pathlib.Path, pathlib.Path]) -> list[dict[str, Any]]:
    corpus, questions = inputs
    return pivot_snapshot.snapshot(corpus, _questions(questions))


def test_there_is_one_row_per_question_statement_and_probe(rows: list[dict[str, Any]]) -> None:
    import tomllib

    from governed_bi.govern.adversarial import SUITE_FILE

    suite = tomllib.loads(SUITE_FILE.read_text(encoding="utf-8"))
    by_kind: dict[str, int] = {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1

    assert by_kind == {
        "meta": 1,
        "question": len(_QUESTIONS),
        "statement": len(suite["case"]) + len(suite["guard_case"]),
        "probe": len(suite["probe"]),
    }


def test_a_question_row_names_its_routing_licensing_join_plan_and_context(rows: list[dict[str, Any]]) -> None:
    row = next(r for r in rows if r["kind"] == "question" and r["id"] == "q1")

    assert row["schemas"], "no schema was routed"
    assert row["schema_ranking"] and len(row["schema_ranking"][0]) == 2
    assert "sales_a.customers" in row["licensed"]
    assert set(row["join_plan"]) == {"hit_joins", "joins", "steiner_tables", "crossings"}
    assert len(row["context_sha256"]) == 64


def test_rows_are_sorted_and_two_runs_write_the_same_bytes(
    inputs: tuple[pathlib.Path, pathlib.Path], rows: list[dict[str, Any]], tmp_path: pathlib.Path
) -> None:
    corpus, questions = inputs
    again = pivot_snapshot.snapshot(corpus, _questions(questions))

    first = pivot_snapshot.write_snapshot(rows, tmp_path / "a.jsonl")
    second = pivot_snapshot.write_snapshot(again, tmp_path / "b.jsonl")

    assert first == second
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()
    assert b"\r" not in (tmp_path / "a.jsonl").read_bytes()
    keys = [(r["kind"], r["id"]) for r in rows]
    assert keys == sorted(keys)


def test_the_tool_pins_the_hash_seed_whatever_the_caller_set(
    inputs: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """Two separate processes, two inherited seeds, one file: the tool re-executes itself."""
    corpus, questions = inputs
    digests = []
    for seed in ("7", "4242"):
        out = tmp_path / f"seed-{seed}.jsonl"
        done = subprocess.run(
            [sys.executable, str(TOOL), "--corpus-dir", str(corpus), "--questions", str(questions), "--out", str(out)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        digests.append(out.read_bytes())

    assert digests[0] == digests[1]
    assert json.loads(digests[0].splitlines()[1])["kind"] in {"probe", "question", "statement"}


def test_tracing_is_forced_off_in_the_tool_and_in_the_process_it_re_executes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A shell with LangSmith tracing on must not make the snapshot open a socket."""
    for name in pivot_snapshot._TRACING_OFF:
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    children: list[dict[str, str]] = []
    monkeypatch.setattr(pivot_snapshot.subprocess, "call", lambda _cmd, env: children.append(env) or 0)

    argv = ["--corpus-dir", str(tmp_path), "--questions", str(tmp_path / "q.jsonl"), "--out", str(tmp_path / "o")]
    assert pivot_snapshot.main(argv) == 0

    (child,) = children
    assert child["PYTHONHASHSEED"] == "0"
    for name in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING"):
        assert child[name] == "false", name
        assert os.environ[name] == "false", name


def test_help_says_what_the_snapshot_cannot_prove() -> None:
    for tool in ("pivot_snapshot.py", "pivot_compare.py"):
        done = subprocess.run(
            [sys.executable, str(ROOT / "tools" / tool), "--help"], capture_output=True, text=True, cwd=ROOT
        )
        assert done.returncode == 0
        text = " ".join(done.stdout.lower().split())
        assert "facet rewriting is off" in text, tool
        assert "cannot prove" in text, tool


# ── the comparison ────────────────────────────────────────────────────────────


def _write(path: pathlib.Path, rows: list[dict[str, Any]]) -> pathlib.Path:
    pivot_snapshot.write_snapshot(rows, path)
    return path


def _edited(rows: list[dict[str, Any]], row_id: str, **changes: Any) -> list[dict[str, Any]]:
    return [{**r, **changes} if r["id"] == row_id else dict(r) for r in rows]


def test_an_identical_pair_exits_zero(rows: list[dict[str, Any]], tmp_path: pathlib.Path, capsys: Any) -> None:
    a, b = _write(tmp_path / "a.jsonl", rows), _write(tmp_path / "b.jsonl", rows)

    assert pivot_compare.main([str(a), str(b)]) == 0
    assert "0 differences" in capsys.readouterr().out


def test_an_unlisted_difference_is_printed_and_exits_one(
    rows: list[dict[str, Any]], tmp_path: pathlib.Path, capsys: Any
) -> None:
    a = _write(tmp_path / "a.jsonl", rows)
    b = _write(tmp_path / "b.jsonl", _edited(rows, "q1", licensed=["sales_a.orders"]))

    assert pivot_compare.main([str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "UNEXPECTED question q1 licensed" in out


def test_a_listed_difference_passes_and_carries_its_reason(
    rows: list[dict[str, Any]], tmp_path: pathlib.Path, capsys: Any
) -> None:
    a = _write(tmp_path / "a.jsonl", rows)
    b = _write(tmp_path / "b.jsonl", _edited(rows, "q1", licensed=["sales_a.orders"]))
    expected = tmp_path / "expected.toml"
    expected.write_text(
        '[[expected]]\nkind = "question"\nid = "q1"\nfield = "licensed"\nreason = "the router changed"\n',
        encoding="utf-8",
    )

    assert pivot_compare.main([str(a), str(b), "--expected", str(expected)]) == 0
    assert "the router changed" in capsys.readouterr().out


def test_a_listed_field_does_not_excuse_another_field_of_the_same_row(
    rows: list[dict[str, Any]], tmp_path: pathlib.Path
) -> None:
    a = _write(tmp_path / "a.jsonl", rows)
    b = _write(tmp_path / "b.jsonl", _edited(rows, "q1", licensed=["x"], context_sha256="0" * 64))
    expected = tmp_path / "expected.toml"
    expected.write_text(
        '[[expected]]\nkind = "question"\nid = "q1"\nfield = "licensed"\nreason = "r"\n', encoding="utf-8"
    )

    assert pivot_compare.main([str(a), str(b), "--expected", str(expected)]) == 1


def test_an_expected_entry_without_a_reason_is_refused(
    rows: list[dict[str, Any]], tmp_path: pathlib.Path
) -> None:
    a = _write(tmp_path / "a.jsonl", rows)
    expected = tmp_path / "expected.toml"
    expected.write_text('[[expected]]\nkind = "question"\nid = "q1"\nfield = "licensed"\n', encoding="utf-8")

    assert pivot_compare.main([str(a), str(a), "--expected", str(expected)]) == 2


def test_a_row_that_disappears_is_a_difference_and_can_be_listed(
    rows: list[dict[str, Any]], tmp_path: pathlib.Path
) -> None:
    case = next(r["id"] for r in rows if r["kind"] == "statement")
    a = _write(tmp_path / "a.jsonl", rows)
    b = _write(tmp_path / "b.jsonl", [r for r in rows if r["id"] != case])
    expected = tmp_path / "expected.toml"
    expected.write_text(
        f'[[expected]]\nkind = "statement"\nids = ["{case}"]\nfield = "<row>"\nreason = "case deleted"\n',
        encoding="utf-8",
    )

    assert pivot_compare.main([str(a), str(b)]) == 1
    assert pivot_compare.main([str(a), str(b), "--expected", str(expected)]) == 0


def test_a_renamed_field_must_keep_its_value(rows: list[dict[str, Any]], tmp_path: pathlib.Path) -> None:
    def renamed(row: dict[str, Any], value: Any = None) -> dict[str, Any]:
        if row["kind"] != "question":
            return dict(row)
        moved = {k: v for k, v in row.items() if k != "licensed"}
        return {**moved, "retrieved_tables": row["licensed"] if value is None else value}

    expected = tmp_path / "expected.toml"
    expected.write_text('[[rename]]\nkind = "question"\nfrom = "licensed"\nto = "retrieved_tables"\n', encoding="utf-8")
    a = _write(tmp_path / "a.jsonl", rows)
    same = _write(tmp_path / "same.jsonl", [renamed(r) for r in rows])
    changed = _write(tmp_path / "changed.jsonl", [renamed(r, ["other"]) for r in rows])

    assert pivot_compare.main([str(a), str(same), "--expected", str(expected)]) == 0
    assert pivot_compare.main([str(a), str(changed), "--expected", str(expected)]) == 1
    assert pivot_compare.main([str(a), str(same)]) == 1, "an unlisted rename is a difference"


def test_one_edited_character_in_a_table_summary_fails_the_comparison(
    inputs: tuple[pathlib.Path, pathlib.Path], rows: list[dict[str, Any]], tmp_path: pathlib.Path, capsys: Any
) -> None:
    """The edit must move a question row, not only the corpus digest in the meta row.

    A summary never enters the rendered context, so the edit is one the ranking sees: q2 asks
    about a voltage "reading" and the edited summary now says "reading" too.
    """
    _, questions = inputs
    copy = tmp_path / "corpus"
    _write_corpus(copy, sensors_summary="sensors voltage reading")
    edited_rows = pivot_snapshot.snapshot(copy, _questions(questions))
    base = _write(tmp_path / "base.jsonl", rows)
    edited = _write(tmp_path / "edited.jsonl", edited_rows)

    assert pivot_compare.main([str(base), str(edited)]) == 1
    out = capsys.readouterr().out
    assert "UNEXPECTED meta snapshot corpus_content_hash" in out
    assert "UNEXPECTED question q2 schema_ranking" in out
    moved = [r["id"] for r in rows if r["kind"] == "question" and r not in edited_rows]
    assert moved, "no question row differs, so the edit only reached the meta row"
