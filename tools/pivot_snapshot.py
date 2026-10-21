"""Write the offline snapshot: what retrieval and the SQL checks do, with no model and no network.

One JSON Lines file, sorted by (kind, id) with sorted keys, that two runs at one commit
reproduce byte for byte. Rows:

* ``question``  one per question: routed schemas, the schema ranking, licensed tables, the join
  plan (joins and Steiner tables ``connect`` pulled in, and the schema crossings), and the
  SHA-256 of the rendered context.
* ``statement`` one per ``[[case]]`` and ``[[guard_case]]`` in ``govern/adversarial.toml``:
  the verdict, layer and rule id from ``check()`` and ``prepare()``, and whether SQL was produced.
* ``probe``     one per ``[[probe]]``, run through ``eval.disclosure_probes.run_disclosure_probes``.
* ``meta``      one row naming the corpus digest, the embedder, the sqlglot version and the seed.

What it runs: a session over ``--corpus-dir`` with the deterministic embedder built in code,
``agent_model=None`` and the guard rules off, through the compiled graph's stub answer path, as
``eval.datalake.routing_recall`` does. **Facet rewriting is off**: no utility model is configured,
so every facet searches the raw question.

What it proves: routing, retrieval, join planning, context rendering and the SQL checks did not
move between two commits. What it cannot prove: anything about the production embedder, the facet
rewriters, the prompts, the agent loop, abstention, clarification or the UI. Those rest on the
test suite and on the baseline run.

The process re-executes itself with ``PYTHONHASHSEED`` pinned (``--hash-seed``, default 0), so a
set-order dependence the engine's sorts did not reach still cannot change the file. LangSmith
tracing is forced off in both processes, so a shell with tracing on cannot open a socket. Compare
two snapshots with ``tools/pivot_compare.py``.

Usage::

    uv run --frozen python tools/pivot_snapshot.py \\
        --corpus-dir ../BIRD-corpus \\
        --questions ../BIRD-Data-Obfuscation/eval_dataset/test_final.jsonl \\
        --out runs/pivot/snapshot-a.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

#: Ranking scores are floats from a hashing embedder and BM25; six places keep the file stable
#: against last-bit noise while a real change in score still shows.
_SCORE_PLACES = 6

#: Every name ``langsmith.utils.get_env_var`` reads for tracing, since it takes the first one set:
#: forcing only the ``LANGCHAIN_`` pair would lose to a ``LANGSMITH_TRACING_V2=true`` in the shell.
#: Set before anything imports langchain, because langsmith caches the lookup.
_TRACING_OFF = {
    name: "false"
    for name in ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING")
}


def _layer(verdict: Mapping[str, Any]) -> str | None:
    layer = verdict["failed_layer"]
    return None if layer is None else str(layer.name)


def question_row(question: Mapping[str, Any], out: Mapping[str, Any], assets: Mapping[str, Any]) -> dict[str, Any]:
    """One question's row, from the state the compiled graph returned."""
    retrieved = out.get("retrieved") or {}
    pulled_in: Mapping[str, str] = retrieved.get("pulled_in") or {}

    def kind_of(asset_id: str) -> str:
        asset = assets.get(asset_id)
        return "" if asset is None else str(asset.asset_type.value)

    delivery = out.get("delivery") or {}
    return {
        "kind": "question",
        "id": str(question["question_id"]),
        "db_id": str(question["db_id"]),
        "schemas": [str(s) for s in out.get("schemas") or ()],
        "schema_ranking": [
            [str(name), round(float(score), _SCORE_PLACES)]
            for name, score in retrieved.get("schema_ranking") or ()
        ],
        "licensed": sorted(str(t) for t in out.get("licensed") or ()),
        "join_plan": {
            "hit_joins": sorted(str(j) for j in (retrieved.get("by_type") or {}).get("join") or ()),
            "joins": sorted([str(a), str(origin)] for a, origin in pulled_in.items() if kind_of(str(a)) == "join"),
            "steiner_tables": sorted(
                str(a) for a, origin in pulled_in.items() if origin == "connect" and kind_of(str(a)) == "table"
            ),
            "crossings": [
                [str(c["from_schema"]), str(c["into_schema"]), str(c["table_id"])]
                for c in out.get("crossings") or ()
            ],
        },
        "context_sha256": delivery.get("context_hash"),
        "path_kind": out.get("path_kind"),
        "terminal_reason": out.get("terminal_reason"),
    }


def question_rows(session: Any, questions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Run each question through the whole compiled graph, one thread each, saver emptied after."""
    from governed_bi.serve.graph import compile_graph

    graph = compile_graph()
    # `InMemorySaver` never expires a thread, and a 1,351-question loop would keep all of them.
    saver = getattr(graph, "checkpointer", None)
    rows = []
    for index, question in enumerate(questions):
        text = str(question["question"])
        turn = session.turn(text, turn_index=1)
        config = session.configurable(question=text)
        thread_id = f"snapshot-{index}-{question['question_id']}"
        config["configurable"]["thread_id"] = thread_id
        out = graph.invoke(turn, config)
        rows.append(question_row(question, out, session.assets_by_id))
        if saver is not None:
            saver.delete_thread(thread_id)
    return rows


def statement_rows() -> list[dict[str, Any]]:
    """One row per ``[[case]]`` (through ``check()`` and ``prepare()``) and ``[[guard_case]]``."""
    from governed_bi.govern.adversarial import load_adversarial_suite
    from governed_bi.govern.adversarial_run import run_adversarial_suite, run_guard_suite

    suite = load_adversarial_suite()
    rows: list[dict[str, Any]] = []
    for result in run_adversarial_suite(suite).results:
        check, prepare = result.verdict, result.prepared.verdict
        rows.append(
            {
                "kind": "statement",
                "id": f"case:{result.case.id}",
                "case_kind": result.case.kind,
                "check_passed": bool(check["passed"]),
                "check_layer": _layer(check),
                "check_rule": check["reason_code"],
                "prepare_passed": bool(prepare["passed"]),
                "prepare_layer": _layer(prepare),
                "prepare_rule": prepare["reason_code"],
                "executable": result.prepared.sql is not None,
                "status": result.status,
            }
        )
    for guard_result in run_guard_suite(suite).results:
        rows.append(
            {
                "kind": "statement",
                "id": f"guard_case:{guard_result.case.id}",
                "case_kind": guard_result.case.kind,
                "outcome": guard_result.verdict["outcome"],
                "rule": guard_result.verdict["rule_id"],
                "status": guard_result.status,
            }
        )
    return rows


def probe_rows() -> list[dict[str, Any]]:
    """One row per ``[[probe]]``, through the disclosure runner."""
    from governed_bi.eval.disclosure_probes import run_disclosure_probes

    return [
        {
            "kind": "probe",
            "id": f"probe:{probe.id}",
            "probe_kind": probe.kind,
            "surface": probe.surface,
            "status": probe.status,
            "detail": probe.detail,
        }
        for probe in run_disclosure_probes()
    ]


def meta_row(session: Any, embedder: Any, *, hash_seed: int, n_questions: int) -> dict[str, Any]:
    from governed_bi.govern.functions import sqlglot_version

    return {
        "kind": "meta",
        "id": "snapshot",
        "corpus_content_hash": session.corpus_content_hash,
        "embedder": embedder.model,
        "facet_rewriting": "off",
        "hash_seed": hash_seed,
        "questions": n_questions,
        "sqlglot_version": sqlglot_version(),
    }


def build_session(corpus_dir: str | pathlib.Path) -> tuple[Any, Any]:
    """The session and its embedder. No DSN: ``connector=None`` and no model of any kind."""
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.model.deterministic_embedder import DeterministicEmbedder
    from governed_bi.serve import session as session_mod

    embedder = DeterministicEmbedder()
    session = session_mod.from_corpus_dir(
        corpus_dir,
        connector=None,
        # Guard rules off, as `routing_recall` runs: a blocked question would have no retrieval row.
        policy=GovernancePolicy(guard_rules_enabled={}),
        agent_model=None,
        embedder=embedder,
        run_id="pivot-snapshot",
    )
    return session, embedder


def snapshot(
    corpus_dir: str | pathlib.Path,
    questions: Sequence[Mapping[str, Any]],
    *,
    hash_seed: int = 0,
) -> list[dict[str, Any]]:
    """Every row, sorted by (kind, id)."""
    session, embedder = build_session(corpus_dir)
    rows = [
        meta_row(session, embedder, hash_seed=hash_seed, n_questions=len(questions)),
        *question_rows(session, questions),
        *statement_rows(),
        *probe_rows(),
    ]
    return sorted(rows, key=lambda r: (r["kind"], r["id"]))


def write_snapshot(rows: Sequence[Mapping[str, Any]], path: pathlib.Path) -> str:
    """Write ``rows`` as sorted-key JSON Lines with ``\\n`` endings and return the file's SHA-256.

    Bytes, not text mode: Windows would otherwise write ``\\r\\n`` and the same snapshot would
    hash differently on two machines.
    """
    lines = (json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows)
    data = "".join(f"{line}\n" for line in lines).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _reexec_with_seed(argv: Sequence[str], seed: int) -> int:
    env = {**os.environ, **_TRACING_OFF, "PYTHONHASHSEED": str(seed)}
    return subprocess.call([sys.executable, str(pathlib.Path(__file__).resolve()), *argv], env=env)


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--corpus-dir", required=True, type=pathlib.Path, help="the semantic layer to serve")
    parser.add_argument("--questions", required=True, type=pathlib.Path, help="test_final.jsonl")
    parser.add_argument("--out", required=True, type=pathlib.Path, help="the snapshot file to write")
    parser.add_argument("--limit", type=int, default=None, help="first N questions only (smoke runs)")
    parser.add_argument(
        "--hash-seed",
        type=int,
        default=0,
        help="PYTHONHASHSEED for the run (default 0). Other seeds exist to look for set-order "
        "dependence, not to produce a baseline.",
    )
    args = parser.parse_args(args_in)
    os.environ.update(_TRACING_OFF)

    if os.environ.get("PYTHONHASHSEED") != str(args.hash_seed):
        return _reexec_with_seed(args_in, args.hash_seed)

    from governed_bi.eval.datalake import load_questions

    questions = load_questions(args.questions, limit=args.limit)
    started = time.monotonic()
    rows = snapshot(args.corpus_dir, questions, hash_seed=args.hash_seed)
    digest = write_snapshot(rows, args.out)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    print(f"wrote {args.out}")
    print(f"rows: {json.dumps(counts, sort_keys=True)}")
    print(f"seconds: {time.monotonic() - started:.1f}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
