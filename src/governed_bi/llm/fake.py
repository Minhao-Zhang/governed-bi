"""Scripted chat models for offline agent tests (no API key / network).

``FakeListChatModel`` cannot ``bind_tools``; ``create_agent`` always binds tools,
so tests need a model that tolerates that call and plays back scripted
``AIMessage`` turns (including ``tool_calls``).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeToolModel(BaseChatModel):
    """Scripted chat model that tolerates ``create_agent``'s ``bind_tools`` call.

    Feed it a list of ``AIMessage`` turns (with ``.tool_calls``) to script a
    trajectory. Once the script is exhausted, the last message is repeated.
    """

    responses: list[BaseMessage]
    i: int = 0
    #: Every prompt this model was sent, in order. The trajectory is scripted, so a
    #: fake that discards ``messages`` makes the whole system prompt untested — the
    #: governance instructions could be deleted and every agent test would still pass
    #: (AUDIT T3). Recording them costs nothing and lets a test assert on what the
    #: model was actually handed.
    prompts_seen: list[list[BaseMessage]] = []
    #: Tool sets passed to :meth:`bind_tools`, for the same reason.
    tools_seen: list[list[Any]] = []

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.prompts_seen.append(list(messages))
        msg = self.responses[min(self.i, len(self.responses) - 1)]
        object.__setattr__(self, "i", self.i + 1)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "fake-tool-model"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        self.tools_seen.append(list(tools))
        return self

    def system_text(self) -> str:
        """Concatenated system-message text across every call, for assertions."""
        out: list[str] = []
        for call in self.prompts_seen:
            for msg in call:
                if getattr(msg, "type", None) == "system":
                    content = msg.content
                    out.append(content if isinstance(content, str) else str(content))
        return "\n".join(out)


def tool_call(name: str, args: dict, id_: str) -> dict:
    """Build a LangChain tool_call dict for scripted ``AIMessage`` turns."""
    return {"name": name, "args": args, "id": id_, "type": "tool_call"}


def ai_tool_turn(name: str, args: dict, id_: str, *, content: str = "") -> AIMessage:
    """Convenience: an ``AIMessage`` that requests one tool call."""
    return AIMessage(content=content, tool_calls=[tool_call(name, args, id_)])
