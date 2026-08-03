"""Injectable scripted chat model for F3 CI (supports ``bind_tools``)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

__all__ = ["ScriptedChatModel"]


class ScriptedChatModel(BaseChatModel):
    """Deterministic responses keyed by how many ``AIMessage``s are already present."""

    responses: list[BaseMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        n_ai = sum(1 for m in messages if isinstance(m, AIMessage))
        if not self.responses:
            msg: BaseMessage = AIMessage(content="")
        else:
            msg = self.responses[min(n_ai, len(self.responses) - 1)]
        return ChatResult(generations=[ChatGeneration(message=msg)])
