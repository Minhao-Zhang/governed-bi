"""Smoke: FakeToolModel works with create_agent offline (no API key)."""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from governed_bi.llm.fake import FakeToolModel, ai_tool_turn


@tool
def ping(x: str) -> str:
    """Echo the input."""
    return f"pong:{x}"


def test_fake_tool_model_create_agent_smoke():
    model = FakeToolModel(
        responses=[
            ai_tool_turn("ping", {"x": "hi"}, "c1"),
            AIMessage(content="done"),
        ]
    )
    agent = create_agent(model=model, tools=[ping])
    final = agent.invoke({"messages": [HumanMessage(content="go")]})
    texts = [getattr(m, "content", "") for m in final["messages"]]
    assert any("pong:hi" in str(t) for t in texts)
    assert texts[-1] == "done"
