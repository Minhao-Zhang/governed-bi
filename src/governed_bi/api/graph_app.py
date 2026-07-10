"""The chat surface as a LangGraph Server graph (LangGraph rework, phase 2).

A thin **chat** graph the LangChain ``useStream`` SDK consumes over the LangGraph
Server protocol. Its persisted state is only ``{messages, answer}`` (both
JSON-serializable), and the whole governed pipeline runs inside a single node,
which calls :func:`governed_bi.server.flow.answer_question` and streams stage
progress through ``get_stream_writer()``. The heavy per-turn objects (the
``networkx`` graph, the allowlist, retrieval/context) stay as locals in that
node and never enter a state channel, so nothing here has to be made
checkpoint-serializable (this is why the ADR 0001 "``ServeState`` serializability"
consequence does not apply; see docs/langgraph-rework-plan.md).

Requires the ``agents`` extra (langgraph + langchain-core). Imported by path from
``langgraph.json`` (``graphs.serve``) and by tests; it is intentionally *not*
re-exported from ``governed_bi.api`` so that ``import governed_bi.api`` stays free
of langgraph for the offline REST profile.

This module deliberately does *not* use ``from __future__ import annotations``:
LangGraph inspects the node's raw parameter annotation to decide whether to
inject the ``RunnableConfig``, and a stringized annotation defeats that.
"""

from typing import TYPE_CHECKING, Annotated, TypedDict

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
    matching how :func:`answer_question` reads ``working_memory.history``.
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


def build_chat_graph(stack: "ServeStack"):
    """Compile the chat graph for a serve stack.

    One ``answer`` node runs the governed flow and returns the assistant message
    plus the governed answer dict. Compiled **without** a checkpointer: on
    LangGraph Server the runtime injects persistence. For a standalone/local
    checkpointer, compile the builder yourself.
    """
    from dataclasses import asdict

    # Absolute imports: the LangGraph server loads this module by file path (no
    # parent package), so relative imports would fail at call time.
    from governed_bi.gateway import Gateway, SqliteConnector
    from governed_bi.server import answer_question
    from governed_bi.viz import presenter

    def answer(state: ChatState, config: RunnableConfig | None = None) -> dict:
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id") or "default"
        question, history = _split_question_and_history(state["messages"])
        memory = _working_memory_from(history, thread_id)

        if not stack.sqlite_path.exists():
            raise RuntimeError("database unavailable")

        try:
            writer = get_stream_writer()
        except Exception:  # not in a streaming context (e.g. plain invoke)
            writer = None

        connector = SqliteConnector(stack.sqlite_path)
        try:
            result = answer_question(
                question,
                stack.identity,
                corpus=stack.corpus_server,
                gateway=Gateway(connector),
                settings=stack.settings,
                session_id=thread_id,
                sql_generator=stack.generator,
                embedder=stack.embedder,
                narrator=stack.narrator,
                working_memory=memory,
                on_event=writer,
            )
        finally:
            connector.close()

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
    return builder.compile()


def make_graph(config: RunnableConfig | None = None):
    """Factory referenced by ``langgraph.json`` (``graphs.serve``).

    Builds the serve stack from the environment (see ``api.stack.build_stack``)
    and returns the compiled chat graph. The server calls this once at startup
    with a ``RunnableConfig``; tests may call it with no argument.
    """
    from governed_bi.api.stack import build_stack

    return build_chat_graph(build_stack())
