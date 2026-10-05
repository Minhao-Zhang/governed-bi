import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageList } from "@/components/chat/message-list";
import type { ChatMessage } from "@/hooks/use-chat";
import {
  MOCK_AGENT_EVENTS,
  MOCK_CLARIFICATION_CLOSED,
  MOCK_REFUSAL,
} from "@/lib/mock/fixtures";
import { reduceSteps, type TimelineStep } from "@/lib/steps";

import { renderWithProviders } from "./render";

const REFUSED: ChatMessage[] = [
  { id: "u1", role: "user", text: "Show me payroll by employee" },
  { id: "a1", role: "assistant", answer: MOCK_REFUSAL },
];

const DECLINED: ChatMessage[] = [
  { id: "u1", role: "user", text: "How many active users?" },
  { id: "a1", role: "assistant", answer: MOCK_CLARIFICATION_CLOSED },
];

describe("MessageList", () => {
  it("shows the live timeline while a turn is running", () => {
    const steps = MOCK_AGENT_EVENTS.slice(0, 20).reduce<TimelineStep[]>(reduceSteps, []);
    renderWithProviders(
      <MessageList messages={[{ id: "u1", role: "user", text: "Top categories?" }]} isRunning steps={steps} />,
      "analyst",
    );

    expect(screen.getByText("Top categories?")).toBeTruthy();
    expect(screen.getByText("How this answer is being built")).toBeTruthy();
  });

  it("renders a refusal with the engine's copy and a refused stamp", () => {
    renderWithProviders(<MessageList messages={REFUSED} isRunning={false} />, "analyst");

    expect(screen.getByText("Show me payroll by employee")).toBeTruthy();
    expect(screen.getByText(/needs a table this corpus does not license/)).toBeTruthy();
    expect(screen.getByText("refused")).toBeTruthy();
    expect(screen.queryByText("clarification")).toBeNull();
    expect(screen.getByRole("button", { name: "This refusal looks wrong" })).toBeTruthy();
  });

  it("renders a declined clarification distinctly from a refusal", () => {
    renderWithProviders(<MessageList messages={DECLINED} isRunning={false} />, "analyst");

    expect(screen.getByText(/The user declined this clarification/)).toBeTruthy();
    expect(screen.getByText("clarification")).toBeTruthy();
    expect(screen.queryByText("refused")).toBeNull();
    expect(screen.queryByText(/needs a table this corpus does not license/)).toBeNull();
    expect(screen.getByText(/no SQL attempted/)).toBeTruthy();
  });
});
