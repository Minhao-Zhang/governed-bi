import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RecentConversations } from "@/components/chat/recent-conversations";

import { renderWithProviders } from "./render";

vi.mock("@/lib/env", () => ({ LANGGRAPH_URL: "http://engine.test", USE_MOCKS: false }));

vi.mock("@/lib/threads", () => ({
  listConversations: vi.fn(async () => [
    {
      thread_id: "t-1",
      updated_at: new Date().toISOString(),
      status: "idle",
      question: "What were the total sales?",
      turns: 2,
    },
    {
      thread_id: "t-2",
      updated_at: new Date().toISOString(),
      status: "interrupted",
      question: "Which is the best product?",
      turns: 1,
    },
  ]),
  deleteConversation: vi.fn(async () => {}),
}));

describe("RecentConversations", () => {
  it("lists the server's threads and opens the one picked", async () => {
    const onOpen = vi.fn();
    renderWithProviders(<RecentConversations onOpen={onOpen} />);

    await waitFor(() => expect(screen.getByText("What were the total sales?")).toBeTruthy());
    expect(screen.getByText("2 turns")).toBeTruthy();
    expect(screen.getByText("interrupted")).toBeTruthy();

    await userEvent.click(screen.getByText("Which is the best product?"));
    expect(onOpen).toHaveBeenCalledWith("t-2");
  });
});
