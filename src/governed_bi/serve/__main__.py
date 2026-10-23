"""Serve one question and print the recorded turn.

Usage::
    uv run --frozen python -m governed_bi.serve --schema gbi_demo_sales -q "how many customers?"

Credentials come from the environment or the git-ignored ``.env`` and are never printed.
A fatal corpus problem refuses the serve (ADR 0005 §2.8.2). Exit 1 when required fields are absent."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from typing import Any


def _chat_model_var() -> str:
    """The variable that names the agent's model, shared with the server."""
    from ..model.provider import SURFACE_MODEL_VARS

    return SURFACE_MODEL_VARS["agent"]


def _credentials() -> Any:
    """Load ``.env``. The OpenAI client reads the environment itself."""
    from .. import credentials
    from ..boot import resolve_settings

    resolve_settings()
    return credentials


def _model(name: str, effort: str | None = None) -> Any:
    """The server's chat client, so this command is not a second construction."""
    from ..model import provider as provider_mod

    if not provider_mod.credentials_present():
        names = " / ".join(provider_mod.credential_names())
        raise SystemExit(
            f"no credential for the openai gateway ({names}); set one in the environment "
            "or .env, or pass --no-model to serve the stub path"
        )
    from ..registry.settings import setting_default

    return provider_mod.chat_model(
        name,
        surface="agent",
        effort=effort or None,
        max_output_tokens=int(setting_default("llm_max_output_tokens")),
        tools=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="governed_bi.serve", description=__doc__)
    parser.add_argument("-q", "--question", required=True)
    parser.add_argument("--schema", help="schema to seed from, or the manifest entry to load")
    parser.add_argument("--corpus-dir", help="a corpus already on disk; omit to seed from --schema")
    # No `default=`: an unnamed thread is a fresh id, and a constant would look nameable.
    parser.add_argument(
        "--thread-id",
        help="name the durable thread, so a later invocation can reach this turn; omit and the "
        "turn's checkpoints are deleted once it has printed",
    )
    parser.add_argument(
        "--model",
        help="chat model id; omit to read "
        f"{_chat_model_var()} from the environment (not .env, which holds secrets only)",
    )
    parser.add_argument("--no-model", action="store_true", help="serve without a model (stub answer path)")
    parser.add_argument("--embed", action="store_true", help="build the index with an embedder (costs tokens)")
    parser.add_argument("--effort", help="reasoning effort for models that take one (none/low/medium/high/xhigh)")
    parser.add_argument("--json", action="store_true", help="print the record as JSON and nothing else")
    args = parser.parse_args(argv)

    if not args.schema and not args.corpus_dir:
        parser.error("one of --schema or --corpus-dir is required")

    creds = _credentials()
    from ..registry.settings import effective_settings

    settings = effective_settings()
    dsn = creds.secret(*creds.PG_DSN_NAMES)
    if not dsn:
        print(
            f"no database: set {creds.PG_DSN_NAMES[0]} in the environment or .env",
            file=sys.stderr,
        )
        return 2

    from ..datasource.postgres import PostgresConnector
    from ..govern.policy import GovernancePolicy
    from ..registry.record import missing_required
    from . import session as session_mod
    from .graph import compile_durable

    connector = PostgresConnector(dsn)
    embedder = None
    vector_cache = None
    if args.embed:
        from ..model import provider as provider_mod
        from ..retrieve.vector_cache import vector_cache_from_environment

        embedder = provider_mod.embedder(str(settings["embedding_model"]))
        # The server's cache. Without it one question re-embedded all 13,968 summaries.
        vector_cache = vector_cache_from_environment(model=embedder.requested_model)
    # After `_credentials()` has loaded `.env`. `--model` overrides for one run.
    model_var = _chat_model_var()
    model_name = args.model or settings["chat_model"]
    if not args.no_model and not model_name:
        print(
            f"no model: pass --model, or set {model_var} in the environment or .env, "
            "or pass --no-model to serve the stub path",
            file=sys.stderr,
        )
        return 2
    effort = args.effort or settings["llm_reasoning_effort"] or None
    model = None if args.no_model else _model(model_name, effort)

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

    # Only fatal problems refuse. Refusing every problem exited 3 on a corpus the server still served (ADR 0008 D9).
    if session.fatal_problems:
        print(
            f"corpus has {len(session.fatal_problems)} fatal problem(s); refusing to serve:",
            file=sys.stderr,
        )
        for problem in session.fatal_problems:
            print(f"  {problem}", file=sys.stderr)
        return 3
    if session.degradations and not args.json:
        # Printed beside the answer: a degraded corpus is not comparable with a clean one.
        print(f"corpus has {len(session.degradations)} degradation(s) (serving anyway):")
        for problem in session.degradations[:10]:
            print(f"  {problem}")
        if len(session.degradations) > 10:
            print(f"  ... and {len(session.degradations) - 10} more")

    # Durable so a turn paused on `ask_user` survives this process. An in-memory saver would drop it.
    graph = compile_durable()
    # The caller names the thread. Without `--thread-id` the id is deleted after printing.
    thread_id = args.thread_id or session.run_id
    config = session.configurable(question=args.question)
    config["configurable"]["thread_id"] = thread_id
    try:
        # `thread_id` goes into the state as well as the config, because `turn_id` is derived from it.
        out = graph.invoke(session.turn(args.question, thread_id=thread_id), config)
    finally:
        # Delete before `close`: deletion needs the loop `close` tears down, including when the turn raised.
        if not args.thread_id:
            _forget_thread(graph, thread_id)
        # An open checkpoint handle stops the process exiting.
        graph.close()

    # Exit 4: a pause has no `answer`, so the incomplete-record check would exit 1.
    pending = _pending_clarification(out)
    if pending:
        print(f"\nThe turn is paused on a clarification: {pending.get('question')}", file=sys.stderr)
        print(f"why: {pending.get('why')}", file=sys.stderr)
        print(
            "This entry point serves one turn and has nowhere to send an answer. Resume by "
            # ASCII `...`: a cp1252 console cannot encode an ellipsis.
            "posting a run carrying {'command': {'resume': ...}} against this thread.",
            file=sys.stderr,
        )
        # The id is required: an unnamed thread is a per-process uuid.
        if args.thread_id:
            print(f"thread: {thread_id} (checkpoint kept)", file=sys.stderr)
        else:
            print(
                # ASCII hyphen: a cp1252 console raises UnicodeEncodeError on an em dash.
                f"thread: {thread_id} - discarded, because an unnamed thread is unreachable. "
                "Re-run the question with --thread-id to keep the paused turn.",
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
        print(f"licensed : {', '.join(record.get('retrieved_tables') or []) or '(none)'}")
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


def _forget_thread(graph: Any, thread_id: str) -> None:
    """``adelete_thread`` on the app loop. The sync delete blocks on a loop that is not running."""
    saver = getattr(graph, "checkpointer", None)
    adelete = getattr(saver, "adelete_thread", None)
    if adelete is None:
        return
    try:
        graph.run_coro(adelete(thread_id))
    except Exception:  # noqa: BLE001 -- a saver that cannot delete the thread is a leak, not a failed turn
        pass


def _pending_clarification(state: dict[str, Any]) -> dict[str, Any] | None:
    """The ``ask_user`` payload when the graph paused (ADR 0007 §6)."""
    for item in state.get("__interrupt__") or ():
        value = getattr(item, "value", item)
        if isinstance(value, dict) and value.get("kind") == "clarification":
            return value
    return None


def _answer_text(state: dict[str, Any], answer: dict[str, Any]) -> str:
    """Model prose from ``messages``. ``answer["text"]`` is system copy (ADR 0007 §4)."""
    if answer.get("text"):
        return str(answer["text"])
    for message in reversed(state.get("messages") or []):
        # `.text`: `str()` on Responses API content blocks prints a list repr.
        text = getattr(message, "text", None)
        if text and getattr(message, "type", "") != "human":
            return str(text)
    return "(no text)"


if __name__ == "__main__":
    raise SystemExit(main())
