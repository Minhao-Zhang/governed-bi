import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/chat/conversation";

import { renderWithProviders } from "./render";

const harness = vi.hoisted(() => ({
  examples: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  api: {
    examples: (...args: unknown[]) => harness.examples(...args),
  },
}));

const EXAMPLES = {
  examples: [
    { question: "How many customers are in each region?", db_id: "gbi_demo_sales" },
    { question: "What is the total revenue this year?", db_id: "gbi_demo_sales" },
    { question: "Which products sold the most last quarter?", db_id: "gbi_demo_catalog" },
  ],
};

beforeEach(() => {
  harness.examples.mockReset();
  harness.examples.mockResolvedValue(EXAMPLES);
});

describe("new conversation", () => {
  it("shows three examples and a click fills the composer", async () => {
    const send = vi.fn();
    renderWithProviders(
      <Conversation messages={[]} send={send} isRunning={false} stop={vi.fn()} />,
    );

    expect(screen.getByText("What do you want to know?")).toBeTruthy();
    expect(await screen.findByRole("link", { name: EXAMPLES.examples[0].question })).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(harness.examples).toHaveBeenCalledWith({ limit: 3 });

    await userEvent.click(screen.getByRole("link", { name: EXAMPLES.examples[1].question }));
    const box = screen.getByRole("textbox", {
      name: "Ask a question about the governed data",
    }) as HTMLTextAreaElement;
    expect(box.value).toBe(EXAMPLES.examples[1].question);
    expect(document.activeElement).toBe(box);
    expect(send).not.toHaveBeenCalled();
  });

  it("shows no examples when the request fails", async () => {
    harness.examples.mockRejectedValue(new Error("offline"));
    renderWithProviders(
      <Conversation messages={[]} send={vi.fn()} isRunning={false} stop={vi.fn()} />,
    );

    expect(screen.getByText("What do you want to know?")).toBeTruthy();
    await waitFor(() => expect(harness.examples).toHaveBeenCalled());
    expect(screen.queryByRole("link")).toBeNull();
  });
});
