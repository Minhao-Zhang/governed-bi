import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ClarificationPrompt } from "@/components/chat/clarification-prompt";
import { MOCK_CLARIFICATION } from "@/lib/mock/fixtures";

/** What the engine actually sends: no `choices`, so the prompt is freeform-only. */
const FREEFORM = {
  kind: "clarification" as const,
  clarification_id: "clar_live01",
  question: "Which fiscal year do you mean?",
  why: "The corpus defines two fiscal calendars.",
};

describe("ClarificationPrompt", () => {
  it("shows the question and why it is being asked", () => {
    render(<ClarificationPrompt request={FREEFORM} onRespond={vi.fn()} />);

    expect(screen.getByText("Which fiscal year do you mean?")).toBeTruthy();
    expect(screen.getByText("The corpus defines two fiscal calendars.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Logged in within the last 30 days" })).toBeNull();
  });

  it("submits a trimmed freeform answer with the clarification id", async () => {
    const onRespond = vi.fn();
    render(<ClarificationPrompt request={FREEFORM} onRespond={onRespond} />);

    const answer = screen.getByRole("button", { name: "Answer" }) as HTMLButtonElement;
    expect(answer.disabled).toBe(true);
    await userEvent.type(screen.getByRole("textbox", { name: "Answer the clarification" }), "  FY2025  ");
    await userEvent.click(answer);

    expect(onRespond).toHaveBeenCalledExactlyOnceWith({
      clarification_id: "clar_live01",
      answer: "FY2025",
    });
  });

  it("sends the choice id, not its label, when an option is picked", async () => {
    const onRespond = vi.fn();
    render(<ClarificationPrompt request={MOCK_CLARIFICATION} onRespond={onRespond} />);

    await userEvent.click(screen.getByRole("button", { name: "Account status = 'active'" }));

    expect(onRespond).toHaveBeenCalledExactlyOnceWith({
      clarification_id: "clar_mock01",
      choice_id: "opt_status",
    });
  });

  it("declines and defers with the two distinct payloads the server reads", async () => {
    const onRespond = vi.fn();
    render(<ClarificationPrompt request={FREEFORM} onRespond={onRespond} />);

    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    await userEvent.click(screen.getByRole("button", { name: "Defer" }));

    expect(onRespond.mock.calls).toEqual([
      [{ clarification_id: "clar_live01", declined: true }],
      [{ clarification_id: "clar_live01", deferred: true }],
    ]);
  });
});
