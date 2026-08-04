"""Serve one question and print what was recorded. ADR 0005 §2.8.2.2.

**This is a skeleton, not a demo, and the difference is the exit code.** It exits non-zero
when ``missing_required(record)`` is non-empty, and it names the fields. An entry point that
prints a plausible answer and exits 0 is indistinguishable from one that works — which is the
failure this repository keeps rediscovering in new costumes: ``STUB_ANSWER`` reaching an
artifact, ``ex=1.00`` from zero executions, a degradation gate passing with no index.

It also refuses to serve at all when the corpus reports a problem. ADR 0005 §2.8.2 requires an
unresolvable join endpoint to surface **where the corpus is built**, and until there was an
entry point there was no caller in a position to exit non-zero, so the requirement was
unsatisfiable rather than unsatisfied.

Usage::

    # a live schema: seed a corpus from it, write it, serve over it
    uv run --frozen python -m governed_bi.serve --schema gbi_demo_sales -q "how many customers?"

    # a corpus already on disk
    uv run --frozen python -m governed_bi.serve --corpus-dir corpus/ --schema gbi_demo_sales -q "..."

    # no model: the graph runs, retrieval and governance are real, the answer is the stub
    uv run --frozen python -m governed_bi.serve --schema gbi_demo_sales -q "..." --no-model

Credentials come from the environment or the git-ignored ``.env`` and are never printed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from typing import Any

#: Repository root, so ``tools/credentials.py`` is importable. `src/` must not read ``.env``
#: itself — a library that decides its own configuration behind its caller's back is the
#: layering ``tools/check_imports.py`` exists to keep — so the bridge lives at this entry
#: point, which is the process that actually wants the file.
_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent


def _credentials() -> Any:
    """The shared reader, with ``.env`` bridged into the environment for this process.

    The bridge is not optional and it is not for us: `langchain_openai` and the `openai`
    client read `os.environ` directly, so knowing a key exists is not the same as their
    being able to use it. Asking `have()` and then handing control to a library that cannot
    see the value fails with the library's message, three frames deep, naming an environment
    variable that *is* set in the file the caller was looking at.
    """
    sys.path.insert(0, str(_ROOT / "tools"))
    import credentials

    credentials.load_into_environ()
    return credentials


def _model(name: str, creds: Any) -> Any:
    """A real chat model, constructed **here** rather than behind a port.

    Decision #1: LangChain's ``BaseChatModel`` already *is* that port, and v1's three layers
    over it (`llm/client.py` + `llm/langchain_client.py` + `llm/fake.py`) are recorded as a
    mistake. So this is the only place a model is chosen.
    """
    if not creds.have(*creds.OPENAI_KEY_NAMES):
        raise SystemExit(
            f"no model credential: set one of {' / '.join(creds.OPENAI_KEY_NAMES)} in the "
            "environment or .env, or pass --no-model to serve the stub path"
        )
    from langchain.chat_models import init_chat_model

    return init_chat_model(name, model_provider="openai", temperature=0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="governed_bi.serve", description=__doc__)
    parser.add_argument("-q", "--question", required=True)
    parser.add_argument("--schema", help="schema to seed from, or the manifest entry to load")
    parser.add_argument("--corpus-dir", help="a corpus already on disk; omit to seed from --schema")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--no-model", action="store_true", help="serve without a model (stub answer path)")
    parser.add_argument("--embed", action="store_true", help="build the index with an embedder (costs tokens)")
    parser.add_argument("--json", action="store_true", help="print the record as JSON and nothing else")
    args = parser.parse_args(argv)

    if not args.schema and not args.corpus_dir:
        parser.error("one of --schema or --corpus-dir is required")

    creds = _credentials()
    dsn = creds.secret(*creds.PG_DSN_NAMES)
    if not dsn:
        print(
            f"no database: set one of {' / '.join(creds.PG_DSN_NAMES)} in the environment or .env",
            file=sys.stderr,
        )
        return 2

    from ..datasource.postgres import PostgresConnector
    from ..govern.policy import GovernancePolicy
    from ..register.record import missing_required
    from . import session as session_mod
    from .graph import compile_graph

    connector = PostgresConnector(dsn)
    embedder = None
    if args.embed:
        from ..model import OpenAIEmbedder

        embedder = OpenAIEmbedder()
    model = None if args.no_model else _model(args.model, creds)

    kwargs: dict[str, Any] = {
        "connector": connector,
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": model,
        "embedder": embedder,
    }
    if args.corpus_dir:
        schemas = [args.schema] if args.schema else None
        session = session_mod.from_corpus_dir(args.corpus_dir, schemas=schemas, **kwargs)
    else:
        root = pathlib.Path(tempfile.mkdtemp(prefix="gbi_corpus_"))
        session = session_mod.from_live_schema(args.schema, corpus_root=root, **kwargs)
        if not args.json:
            print(f"seeded {len(session.assets_by_id)} assets from {args.schema!r} into {root}")

    # Problems first, and they stop the serve. A warning printed beside an answer is the
    # silent-skip shape: it satisfies "we reported it" and changes no outcome.
    if session.fatal_problems:
        print(f"corpus has {len(session.fatal_problems)} problem(s); refusing to serve:", file=sys.stderr)
        for problem in session.fatal_problems:
            print(f"  {problem}", file=sys.stderr)
        return 3

    graph = compile_graph()
    out = graph.invoke(session.turn(args.question), session.configurable(question=args.question))
    answer = out.get("answer") or {}
    record = answer.get("record") or {}

    if args.json:
        print(json.dumps(record, indent=2, default=str))
    else:
        text = _answer_text(out, answer)
        print()
        print(f"question : {args.question}")
        print(f"outcome  : {answer.get('outcome')}")
        print(f"answer   : {text}")
        print(f"sql      : {record.get('generated_sql')}")
        print(f"licensed : {', '.join(record.get('licensed') or []) or '(none)'}")
        execution = record.get("execution") or {}
        print(f"terminal : {execution.get('terminal')}  attempts={len(execution.get('attempts') or [])}")
        for attempt in execution.get("attempts") or []:
            print(f"           passed={attempt.get('passed')} {attempt.get('reason_code')}")
        print(f"context  : {record.get('context_hash')}")

    missing = missing_required(record)
    if missing:
        absent = ", ".join(sorted(missing))
        print(f"\nINCOMPLETE RECORD: {len(missing)} required field(s) absent: {absent}", file=sys.stderr)
        print("A turn whose record is incomplete is not a turn that worked.", file=sys.stderr)
        return 1
    if not args.json:
        print("record   : complete (every required field present)")
    return 0


def _answer_text(state: dict[str, Any], answer: dict[str, Any]) -> str:
    """The model's answer from ``messages``; the system's from ``answer["text"]``.

    ADR 0007 §4: ``text`` is *system copy* and is null on the answered path, so a caller that
    reads only ``answer["text"]`` shows nothing for every successful turn. One source each,
    rather than two fields that must agree.
    """
    if answer.get("text"):
        return str(answer["text"])
    for message in reversed(state.get("messages") or []):
        # `.text` rather than `str(content)`: `langchain-core` already concatenates content
        # blocks, and the Responses API returns blocks. `str()` on that prints a Python repr
        # of a list of dicts and calls it the answer.
        text = getattr(message, "text", None)
        if text and getattr(message, "type", "") != "human":
            return str(text)
    return "(no text)"


if __name__ == "__main__":
    raise SystemExit(main())
