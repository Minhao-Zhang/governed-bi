import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ClarificationPrompt } from "@/components/chat/clarification-prompt";
import { MOCK_CLARIFICATION } from "@/lib/mock/fixtures";

/** What the engine sends: no `choices`, so the prompt is freeform-only. */
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
    const why = screen.getByText("The corpus defines two fiscal calendars.");
    expect(why.className).toContain("text-[var(--muted)]");
    expect(screen.queryByRole("button", { name: "Logged in within the last 30 days" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Answer the clarification" })).toBeTruthy();
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

  it("resumes with a choice, and with no choices offers only free text", async () => {
    const onRespond = vi.fn();
    const { rerender } = render(
      <ClarificationPrompt
        request={{
          ...FREEFORM,
          choices: [
            { id: "calendar year", label: "calendar year" },
            { id: "fiscal year", label: "fiscal year" },
          ],
          allow_freeform: false,
        }}
        onRespond={onRespond}
      />,
    );

    expect(screen.getByRole("textbox", { name: "Answer the clarification" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "calendar year" }));
    expect(onRespond).toHaveBeenCalledExactlyOnceWith({
      clarification_id: "clar_live01",
      choice_id: "calendar year",
    });

    rerender(<ClarificationPrompt request={FREEFORM} onRespond={onRespond} />);
    expect(screen.queryByRole("button", { name: "calendar year" })).toBeNull();
    expect(screen.queryByRole("button", { name: "fiscal year" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Answer the clarification" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Answer" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Defer" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Decline" })).toBeTruthy();
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
