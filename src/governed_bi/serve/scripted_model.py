"""Injectable scripted chat model for F3 CI (supports ``bind_tools``).

**It records what it was given.** The previous version dropped both of its inputs —
``bind_tools`` returned ``self`` without looking at the tools, and ``_generate`` used
``messages`` only to count ``AIMessage``s — so nothing could observe what reached the model:
emptying ``SYSTEM_PROMPT`` to ``""`` left the suite byte-identical at 358 passed / 27 xfailed,
and so did removing tools from the bound set. Decision #1 named ``prompts_seen`` /
``tools_seen`` as the remedy in v1 and they were never built.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

__all__ = ["ScriptedChatModel"]


def _tool_name(tool: Any) -> str:
    """A bound tool's name, whether it arrived as a ``BaseTool``, a dict or a callable."""
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        return str(tool.get("name") or tool)
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", None) or tool)


class ScriptedChatModel(BaseChatModel):
    """Deterministic responses keyed by how many ``AIMessage``s are already present."""

    responses: list[BaseMessage]

    #: Every message list this model was called with, in call order. A test asserts on
    #: ``prompts_seen[0]`` to check what the *first* call carried.
    prompts_seen: list[list[BaseMessage]] = Field(default_factory=list)

    #: Tool names per ``bind_tools`` call. A list of lists rather than a set, so "bound twice
    #: with different sets" is distinguishable from "bound once".
    tools_seen: list[list[str]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    # ── what a test asserts on ────────────────────────────────────────────────

    @property
    def tool_names(self) -> set[str]:
        """Every tool name ever bound to this model."""
        return {name for call in self.tools_seen for name in call}

    def system_prompts(self) -> list[str]:
        """The system message text of each call, ``""`` when a call carried none.

        Empty string rather than skipping the call: "the model was called with no system
        message" is the defect being guarded against, so it has to be visible in the list
        rather than absent from it.
        """
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
        """Indices of the calls whose system prompt is exactly ``text``.

        **One model serves several callers** — the guard's scope gate and the four facet query
        rewriters (``FACET_EXTRACTS``; ``facet_schema`` is the fifth facet *node* and rewrites
        nothing) share it whenever no separate utility model is configured — so call 0 is
        whichever ran first, and an assertion about the analyst that reads it silently changes
        subject. Selecting by system prompt stays true as more callers appear.
        """
        return [i for i, seen in enumerate(self.system_prompts()) if seen == text]

    def prompt_text(self, call: int = 0) -> str:
        """Every message of one call concatenated — for "did the context reach the model"."""
        if call >= len(self.prompts_seen):
            return ""
        return "\n".join(
            str(getattr(m, "text", None) or getattr(m, "content", ""))
            for m in self.prompts_seen[call]
        )

    # ── the model surface ─────────────────────────────────────────────────────

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        """Record the bound names and return ``self``.

        Returning ``self`` rather than a ``RunnableBinding`` is what makes the recording
        reachable: ``tools_seen`` and ``prompts_seen`` accumulate on the instance the test holds.
        """
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
