"""The chat surface as a LangGraph Server graph (LangGraph rework, phase 2).

A thin **chat** graph the LangChain ``useStream`` SDK consumes over the LangGraph
Server protocol. Its persisted state is only ``{messages, answer}`` (both
JSON-serializable), and the whole governed pipeline runs inside a single node,
which calls :func:`governed_bi.analyst.agent.answer_question_agent` (agent-only
serve, ADR 0002) and streams step progress through ``get_stream_writer()``. The
heavy per-turn objects (the
``networkx`` graph, the allowlist, retrieval/context) stay as locals in that
node and never enter a state channel, so nothing here has to be made
checkpoint-serializable (this is why the ADR 0001 "``ServeState`` serializability"
consequence does not apply).

Requires the ``agents`` extra (langgraph + langchain-core). Imported by path from
``langgraph.json`` (``graphs.serve``) and by tests; it is intentionally *not*
re-exported from ``governed_bi.api`` so that ``import governed_bi.api`` stays free
of langgraph for the offline REST profile.

This module deliberately does *not* use ``from __future__ import annotations``:
LangGraph inspects the node's raw parameter annotation to decide whether to
inject the ``RunnableConfig``, and a stringized annotation defeats that.
"""

import asyncio
import threading
from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

if TYPE_CHECKING:
    from governed_bi.api.stack import ServeStack


class ChatState(TypedDict):
    """Persisted chat state. ``messages`` is the thread transcript the frontend
    reads via ``stream.messages``; ``answer`` is the governed answer as a plain
    dict (the ``presenter.answer_view`` shape) read via ``stream.values.answer``.
    Both round-trip through the checkpointer the server injects."""

    messages: Annotated[list, add_messages]
    answer: dict | None


def _message_text(message) -> str:
    """The plain text of a message, flattening multimodal content blocks."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return " ".join(p for p in parts if p)


def _is_human(message) -> bool:
    return getattr(message, "type", None) == "human"


def _split_question_and_history(messages: list) -> tuple[str, list]:
    """Split the transcript into (current question, prior turns).

    The current question is the last human message (the turn just submitted); the
    prior turns are everything before it. The graph node answers the question and
    replays the prior turns as working memory, so history comes from the durable
    thread rather than a separate channel.
    """
    for i in range(len(messages) - 1, -1, -1):
        if _is_human(messages[i]):
            return _message_text(messages[i]), messages[:i]
    raise ValueError("no human message to answer in the thread")


def _working_memory_from(history: list, session_id: str):
    """Rebuild working memory (D8) from prior thread turns.

    Human messages map to the ``user`` role, everything else to ``assistant``;
    empty-content messages are skipped. Keyed by ``session_id`` (the thread id),
    matching how :func:`answer_question_agent` reads ``working_memory.history``.
    """
    from governed_bi.memory import InMemoryWorkingMemory

    memory = InMemoryWorkingMemory()
    for message in history:
        text = _message_text(message)
        if not text:
            continue
        role = "user" if _is_human(message) else "assistant"
        memory.append(session_id, role, text)
    return memory


def build_chat_graph(stack: "ServeStack", *, checkpointer: Any = None):
    """Compile the chat graph for a serve stack.

    One ``answer`` node runs the governed flow and returns the assistant message
    plus the governed answer dict. Compiled **without** a checkpointer by default:
    on LangGraph Server the runtime injects persistence. Pass ``checkpointer`` for
    standalone/local use or to exercise the ask_user HITL interrupt/resume round
    trip (which needs the outer graph to be checkpointed).
    """
    from dataclasses import asdict

    # Absolute imports: the LangGraph server loads this module by file path (no
    # parent package), so relative imports would fail at call time.
    from governed_bi.gateway import Gateway
    from governed_bi.analyst.agent import ClarificationPending, answer_question_agent
    from governed_bi.viz import presenter
    from langgraph.types import interrupt

    def answer(state: ChatState, config: RunnableConfig | None = None) -> dict:
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id") or "default"
        question, history = _split_question_and_history(state["messages"])
        memory = _working_memory_from(history, thread_id)

        try:
            writer = get_stream_writer()
        except Exception:  # not in a streaming context (e.g. plain invoke)
            writer = None

        # Serve-time HITL: a per-turn inner thread, stable across the outer graph's
        # re-execution on resume (contract §2). Keyed by the human-turn count so a
        # new turn gets a fresh inner thread and never resumes a stale pause.
        n_human = sum(1 for m in state["messages"] if getattr(m, "type", None) == "human")
        clarify_thread = f"{thread_id}:{n_human}"

        # Run the turn; if the agent pauses on ask_user, surface a client interrupt
        # and loop back with the answer. When clarify is off (no checkpointer),
        # answer_question_agent never returns ClarificationPending, so this runs once.
        resume: Any = None
        while True:
            try:
                connector = stack.open_connector()  # SQLite or Postgres/Redshift
            except Exception as exc:
                raise RuntimeError("database unavailable") from exc
            try:
                gateway = Gateway(connector)
                result = answer_question_agent(
                    question,
                    stack.identity,
                    corpus=stack.corpus_analyst,
                    gateway=gateway,
                    settings=stack.settings,
                    session_id=thread_id,
                    model=stack.chat_model,
                    embedder=stack.embedder,
                    narrator=stack.narrator,
                    working_memory=memory,
                    on_event=writer,
                    clarify_checkpointer=stack.clarify_checkpointer,
                    clarify_thread=clarify_thread,
                    clarify_resume=resume,
                )
            finally:
                connector.close()
            if isinstance(result, ClarificationPending):
                # Raises GraphInterrupt on the first pass (client sees
                # stream.interrupt.value); returns the ClarificationResponse when
                # the outer graph is resumed via stream.respond(...).
                resume = interrupt(result.request)
                continue
            break

        view = asdict(presenter.answer_view(result))
        text = view.get("text") or view.get("escalation") or ""
        return {
            "messages": [AIMessage(content=text, additional_kwargs={"governed_bi": view})],
            "answer": view,
        }

    builder = StateGraph(ChatState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=checkpointer) if checkpointer else builder.compile()


def _build_graph():
    """Sync build of the serve stack + chat graph (filesystem / config I/O).

    Agent-only serve (ADR 0002): the chat graph needs a live model. Fail closed at
    startup with a clear message rather than booting a serve process that would
    503 on every turn.
    """
    from governed_bi.api.stack import build_stack

    stack = build_stack()
    if stack.chat_model is None:
        raise RuntimeError(
            "agentic serve requires a live model but none is configured; set "
            f"{stack.settings.models.api_key_env} (and install the 'agents' extra)"
        )
    return build_chat_graph(stack)


_GRAPH = None
_GRAPH_LOCK = threading.Lock()


def _get_or_build_graph():
    """Build the chat graph once; safe to call from a worker thread."""
    global _GRAPH
    with _GRAPH_LOCK:
        if _GRAPH is None:
            _GRAPH = _build_graph()
        return _GRAPH


async def make_graph(config: RunnableConfig | None = None):
    """Factory referenced by ``langgraph.json`` (``graphs.serve``).

    Builds the serve stack from ``load_settings()`` (``governed_bi.toml`` +
    optional local overlay) off the event loop via ``asyncio.to_thread`` so
    corpus/config filesystem I/O does not trip LangGraph's ASGI blockbuster.
    The compiled graph is cached for subsequent runs. ``config`` is accepted for
    the LangGraph factory signature; the stack is TOML-driven.
    """
    if _GRAPH is not None:
        return _GRAPH
    return await asyncio.to_thread(_get_or_build_graph)
