import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageList } from "@/components/chat/message-list";
import type { ChatMessage } from "@/hooks/use-chat";
import { MOCK_CLARIFICATION_CLOSED, MOCK_REFUSAL } from "@/lib/mock/fixtures";
import type { TimelineStep } from "@/lib/steps";

import { renderWithProviders } from "./render";

const REFUSED: ChatMessage[] = [
  { id: "u1", role: "user", text: "Show me payroll by employee" },
  { id: "a1", role: "assistant", answer: MOCK_REFUSAL },
];

const DECLINED: ChatMessage[] = [
  { id: "u1", role: "user", text: "How many active users?" },
  { id: "a1", role: "assistant", answer: MOCK_CLARIFICATION_CLOSED },
];

function step(name: string, status: TimelineStep["status"], seq: number): TimelineStep {
  return { key: `${name}:${seq}`, seq, kind: "rail", step: name, status, label: "", detail: {} };
}

describe("MessageList", () => {
  it("shows one status line and changes it when the current stage changes", () => {
    const { rerender } = renderWithProviders(
      <MessageList
        messages={[{ id: "u1", role: "user", text: "Top categories?" }]}
        isRunning
        steps={[step("resolve", "running", 1)]}
      />,
    );

    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status").textContent).toBe("Finding your definitions…");

    rerender(
      <MessageList
        messages={[{ id: "u1", role: "user", text: "Top categories?" }]}
        isRunning
        steps={[step("resolve", "ok", 1), step("check", "running", 2)]}
      />,
    );

    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status").textContent).toBe("Checking the query…");
  });

  it("falls back when the stage is unknown and before any stage arrives", () => {
    const { rerender } = renderWithProviders(
      <MessageList
        messages={[{ id: "u1", role: "user", text: "Top categories?" }]}
        isRunning
        steps={[]}
      />,
    );
    expect(screen.getByRole("status").textContent).toBe("Working on your question…");

    rerender(
      <MessageList
        messages={[{ id: "u1", role: "user", text: "Top categories?" }]}
        isRunning
        steps={[step("not_a_stage", "running", 1)]}
      />,
    );
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status").textContent).toBe("Working on your question…");
  });

  it("hides the status line once the turn settles", () => {
    renderWithProviders(
      <MessageList
        messages={[{ id: "u1", role: "user", text: "Top categories?" }]}
        isRunning={false}
        steps={[step("finish", "ok", 1)]}
      />,
    );
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders the question as a bubble and the answer as plain text", () => {
    renderWithProviders(<MessageList messages={REFUSED} isRunning={false} />);

    const question = screen.getByText("Show me payroll by employee");
    expect(question.hasAttribute("data-question")).toBe(true);
    expect(question.className).toContain("rounded-2xl");
    expect(question.parentElement?.className).toContain("justify-end");

    const answer = screen.getByText(/needs a table this corpus does not license/);
    expect(answer.closest("[data-refusal]")).toBeTruthy();
    expect(answer.closest("[data-question]")).toBeNull();
    expect(screen.queryByText("refused")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("renders a declined clarification as its own plain text", () => {
    renderWithProviders(<MessageList messages={DECLINED} isRunning={false} />);

    expect(screen.getByText(/The user declined this clarification/)).toBeTruthy();
    expect(screen.queryByText("clarification")).toBeNull();
    expect(screen.queryByText("refused")).toBeNull();
  });
});
