"""Deterministic chat model for tests. Records prompts and bound tool names.

``bind_tools`` returns ``self`` so the recording stays on the instance the test holds.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

__all__ = ["ScriptedChatModel", "answer_call"]


def answer_call(
    text: str, assumptions: list[dict[str, Any]] | None = None, call_id: str = "answer-1"
) -> AIMessage:
    """The ``answer`` tool call a scripted turn ends with. Prose alone is not an answer."""
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "answer",
            "args": {"answer_text": text, "assumptions": list(assumptions or [])},
            "id": call_id,
            "type": "tool_call",
        }],
    )


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        return str(tool.get("name") or tool)
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", None) or tool)


class ScriptedChatModel(BaseChatModel):
    """Responses chosen by how many ``AIMessage``s are already present."""

    responses: list[BaseMessage]

    prompts_seen: list[list[BaseMessage]] = Field(default_factory=list)

    #: Per ``bind_tools`` call, not a set, so two different bindings stay distinct.
    tools_seen: list[list[str]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def tool_names(self) -> set[str]:
        return {name for call in self.tools_seen for name in call}

    def system_prompts(self) -> list[str]:
        """``""`` when a call had no system message. Skipping the call would hide that."""
        out: list[str] = []
        for messages in self.prompts_seen:
            texts = [
                str(getattr(m, "text", None) or getattr(m, "content", ""))
                for m in messages
                if str(getattr(m, "type", "")) == "system"
            ]
            out.append("\n".join(texts))
        return out

    def calls_with_system(self, text: str) -> list[int]:
        """Index 0 is whichever caller ran first. One model serves several."""
        return [i for i, seen in enumerate(self.system_prompts()) if seen == text]

    def prompt_text(self, call: int = 0) -> str:
        if call >= len(self.prompts_seen):
            return ""
        return "\n".join(
            str(getattr(m, "text", None) or getattr(m, "content", ""))
            for m in self.prompts_seen[call]
        )

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        """Return ``self``. A binding would hide the lists from the instance the test holds."""
        self.tools_seen.append([_tool_name(t) for t in (tools or ())])
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.prompts_seen.append(list(messages))
        n_ai = sum(1 for m in messages if isinstance(m, AIMessage))
        if not self.responses:
            msg: BaseMessage = AIMessage(content="")
        else:
            msg = self.responses[min(n_ai, len(self.responses) - 1)]
        return ChatResult(generations=[ChatGeneration(message=msg)])
