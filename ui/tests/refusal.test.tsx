import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Refusal } from "@/components/answer/refusal";
import { Composer } from "@/components/chat/composer";
import { ComposerDraftProvider } from "@/components/chat/composer-draft";

import { renderWithProviders } from "./render";

const harness = vi.hoisted(() => ({
  examples: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  api: {
    examples: (...args: unknown[]) => harness.examples(...args),
  },
}));

const NEARBY = {
  examples: [
    { question: "How many customers joined last month?", db_id: "gbi_demo_sales" },
    { question: "Which regions have the most orders?", db_id: "gbi_demo_sales" },
    { question: "What is the average order value?", db_id: "gbi_demo_sales" },
  ],
};

const QUESTION = "Show me payroll by employee";
const REASON = "This question needs a table this corpus does not license.";

beforeEach(() => {
  harness.examples.mockReset();
  harness.examples.mockResolvedValue(NEARBY);
});

function renderRefusal(reason: string | null) {
  const onSend = vi.fn();
  renderWithProviders(
    <ComposerDraftProvider>
      <Refusal reason={reason} question={QUESTION} />
      <Composer onSend={onSend} />
    </ComposerDraftProvider>,
  );
  return onSend;
}

describe("Refusal", () => {
  it("shows the sentence, the reason, and nearby questions that fill the composer", async () => {
    const onSend = renderRefusal(REASON);

    expect(screen.getByText("I can't answer this from your data.")).toBeTruthy();
    const reason = screen.getByText(REASON);
    expect(reason.className).toContain("text-[var(--muted)]");
    expect(await screen.findByRole("link", { name: NEARBY.examples[0].question })).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(harness.examples).toHaveBeenCalledWith({ limit: 3, near: QUESTION });

    await userEvent.click(screen.getByRole("link", { name: NEARBY.examples[2].question }));
    const box = screen.getByRole("textbox", {
      name: "Ask a question about the governed data",
    }) as HTMLTextAreaElement;
    expect(box.value).toBe(NEARBY.examples[2].question);
    expect(document.activeElement).toBe(box);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows nothing extra when nearby questions fail or come back empty", async () => {
    harness.examples.mockRejectedValue(new Error("offline"));
    renderRefusal(null);

    expect(screen.getByText("I can't answer this from your data.")).toBeTruthy();
    expect(screen.queryByText(REASON)).toBeNull();
    await waitFor(() => expect(harness.examples).toHaveBeenCalled());
    expect(screen.queryByRole("link")).toBeNull();
  });
});
