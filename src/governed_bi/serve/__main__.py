"""Serve one question and print what was recorded. ADR 0005 §2.8.2.2.

**A skeleton, not a demo, and the difference is the exit code**: it exits non-zero when
``missing_required(record)`` is non-empty and names the fields, because an entry point that
prints a plausible answer and exits 0 is indistinguishable from one that works.

It also refuses to serve when the corpus reports a fatal problem. ADR 0005 §2.8.2 requires an
unresolvable join endpoint to surface **where the corpus is built**, and until there was an
entry point no caller was in a position to exit non-zero.

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

from ..paths import TOOLS_DIR


def _credentials() -> Any:
    """The shared reader, with ``.env`` bridged into the environment for this process.

    The bridge is for the libraries, not for us: `langchain_openai` and the `openai` client
    read `os.environ` directly, so `have()` returning true is not the same as their being able
    to use the value.
    """
    sys.path.insert(0, str(TOOLS_DIR))
    import credentials

    credentials.load_into_environ()
    return credentials


def _model(name: str, creds: Any, effort: str | None = None) -> Any:
    """A real chat model, constructed **here** rather than behind a port.

    Decision #1: LangChain's ``BaseChatModel`` already *is* that port, and v1's three layers
    over it are recorded as a mistake. This is the only place a model is chosen.
    """
    if not creds.have(*creds.OPENAI_KEY_NAMES):
        raise SystemExit(
            f"no model credential: set one of {' / '.join(creds.OPENAI_KEY_NAMES)} in the "
            "environment or .env, or pass --no-model to serve the stub path"
        )
    from langchain.chat_models import init_chat_model

    # `use_responses_api` because this agent binds tools and the provider refuses tools
    # alongside `reasoning_effort` on chat completions. Kept identical to `api/graph_app.py`:
    # two entry points constructing a model differently are two answers to "what did this run
    # use", on a comparability knob.
    kwargs: dict[str, Any] = {"model_provider": "openai", "use_responses_api": True}
    if effort:
        kwargs["reasoning_effort"] = effort
    return init_chat_model(name, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="governed_bi.serve", description=__doc__)
    parser.add_argument("-q", "--question", required=True)
    parser.add_argument("--schema", help="schema to seed from, or the manifest entry to load")
    parser.add_argument("--corpus-dir", help="a corpus already on disk; omit to seed from --schema")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--no-model", action="store_true", help="serve without a model (stub answer path)")
    parser.add_argument("--embed", action="store_true", help="build the index with an embedder (costs tokens)")
    parser.add_argument("--effort", help="reasoning effort for models that take one (none/low/medium/high/xhigh)")
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
    vector_cache = None
    if args.embed:
        from ..model import OpenAIEmbedder
        from ..retrieve.vector_cache import vector_cache_from_environment

        embedder = OpenAIEmbedder()
        # The same persisted cache the server uses. Without it every invocation re-embedded
        # all 13,968 summaries in the pooled corpus before answering one question, and nothing
        # here reports how many vectors were reused, so it went unnoticed.
        vector_cache = vector_cache_from_environment(model=embedder.requested_model)
    model = None if args.no_model else _model(args.model, creds, args.effort)

    kwargs: dict[str, Any] = {
        "connector": connector,
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": model,
        "embedder": embedder,
        "vector_cache": vector_cache,
    }
    if args.corpus_dir:
        schemas = [args.schema] if args.schema else None
        session = session_mod.from_corpus_dir(args.corpus_dir, schemas=schemas, **kwargs)
    else:
        root = pathlib.Path(tempfile.mkdtemp(prefix="gbi_corpus_"))
        session = session_mod.from_live_schema(args.schema, corpus_root=root, **kwargs)
        if not args.json:
            print(f"seeded {len(session.assets_by_id)} assets from {args.schema!r} into {root}")

    # Fatal problems stop the serve; a warning printed beside an answer changes no outcome.
    # Refusing on *every* problem was the opposite failure — this exited 3 on a corpus the
    # server served without checking anything, so two readers of one list disagreed
    # (ADR 0008 D9). `Problem.fatal` decides.
    if session.fatal_problems:
        print(
            f"corpus has {len(session.fatal_problems)} fatal problem(s); refusing to serve:",
            file=sys.stderr,
        )
        for problem in session.fatal_problems:
            print(f"  {problem}", file=sys.stderr)
        return 3
    if session.degradations and not args.json:
        # Printed and counted, not a stop: a run over a degraded corpus is not comparable to
        # a run over a clean one, so the number goes next to the answer.
        print(f"corpus has {len(session.degradations)} degradation(s) (serving anyway):")
        for problem in session.degradations[:10]:
            print(f"  {problem}")
        if len(session.degradations) > 10:
            print(f"  ... and {len(session.degradations) - 10} more")

    graph = compile_graph()
    # One question, one thread. `configurable()` supplies no `thread_id` -- a thread is per
    # conversation, not a run constant, and defaulting it collapsed conversations together --
    # so the caller names it, and here the run id is the honest answer.
    config = session.configurable(question=args.question)
    config["configurable"]["thread_id"] = session.run_id
    out = graph.invoke(session.turn(args.question), config)

    # A paused turn is not a failed one. `ask_user` interrupts and no node writes `answer`, so
    # the code below would print `outcome: None` and exit 1 on an incomplete record, naming
    # fifteen absent fields for a turn that is waiting rather than broken. Exit 4 says which.
    pending = _pending_clarification(out)
    if pending:
        print(f"\nThe turn is paused on a clarification: {pending.get('question')}", file=sys.stderr)
        print(f"why: {pending.get('why')}", file=sys.stderr)
        print(
            "This entry point serves one turn and has nowhere to send an answer. Use "
            "POST /chat + POST /chat/resume, or LangGraph Server's own resume.",
            file=sys.stderr,
        )
        return 4

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


def _pending_clarification(state: dict[str, Any]) -> dict[str, Any] | None:
    """The ``ask_user`` payload if the graph paused. ADR 0007 §6's ``kind`` decides."""
    for item in state.get("__interrupt__") or ():
        value = getattr(item, "value", item)
        if isinstance(value, dict) and value.get("kind") == "clarification":
            return value
    return None


def _answer_text(state: dict[str, Any], answer: dict[str, Any]) -> str:
    """The model's answer from ``messages``; the system's from ``answer["text"]``.

    ADR 0007 §4: ``text`` is *system copy* and is null on the answered path, so a caller that
    reads only ``answer["text"]`` shows nothing for every successful turn.
    """
    if answer.get("text"):
        return str(answer["text"])
    for message in reversed(state.get("messages") or []):
        # `.text` rather than `str(content)`: the Responses API returns content blocks, and
        # `str()` on those prints a Python repr of a list of dicts and calls it the answer.
        text = getattr(message, "text", None)
        if text and getattr(message, "type", "") != "human":
            return str(text)
    return "(no text)"


if __name__ == "__main__":
    raise SystemExit(main())
