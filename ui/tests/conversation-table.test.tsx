import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationTable } from "@/components/conversations/conversation-table";
import type { ConversationRow, Conversations, Ending } from "@/lib/types";

import { renderWithProviders } from "./render";

const harness = vi.hoisted(() => ({ conversations: vi.fn() }));

vi.mock("@/lib/env", () => ({ LANGGRAPH_URL: "http://engine.test", USE_MOCKS: false }));

vi.mock("@/lib/api-client", () => ({
  api: { conversations: (...args: unknown[]) => harness.conversations(...args) },
}));

function turn(question: string, outcome: string) {
  return { asked_at: "2026-10-20T10:00:00Z", question, outcome };
}

const ROWS: ConversationRow[] = [
  {
    thread_id: "t-answered",
    updated_at: "2026-10-21T10:00:00Z",
    status: "idle",
    waiting: false,
    ending: "answered",
    question: "And by region?",
    elided_turns: 3,
    turns: [turn("How many sales?", "answered"), turn("And by region?", "answered")],
  },
  {
    thread_id: "t-waiting",
    updated_at: "2026-10-20T10:00:00Z",
    status: "interrupted",
    waiting: true,
    ending: "waiting",
    question: "Which customers churned?",
    elided_turns: 0,
    turns: [],
  },
];

function page(ending: Ending | null): Conversations {
  const rows = ending ? ROWS.filter((r) => r.ending === ending) : ROWS;
  return {
    rows,
    meta: {
      n: rows.length,
      total: rows.length,
      truncated: false,
      threads_scanned: 2,
      limit: 500,
      offset: 0,
      ending,
    },
  };
}

beforeEach(() => {
  harness.conversations.mockReset();
  harness.conversations.mockImplementation(({ ending }: { ending: Ending | null }) =>
    Promise.resolve(page(ending)),
  );
});

describe("ConversationTable", () => {
  it("lists each conversation's first question, turns, ending and date, opening on click", async () => {
    renderWithProviders(<ConversationTable />);
    const link = await screen.findByRole("link", { name: "How many sales?" });
    expect(link.getAttribute("href")).toBe("/?thread=t-answered");
    const answered = link.closest("tr")!;
    const cells = within(answered).getAllByRole("cell").map((cell) => cell.textContent);
    expect(cells.slice(0, 3)).toEqual(["How many sales?", "5", "Answered"]);
    expect(cells[3]).toMatch(/2026/);

    const waiting = screen.getByRole("link", { name: "Which customers churned?" }).closest("tr")!;
    expect(within(waiting).getAllByRole("cell").map((c) => c.textContent).slice(1, 3)).toEqual([
      "1",
      "Waiting for you",
    ]);
  });

  it("filters by how the conversation ended", async () => {
    renderWithProviders(<ConversationTable />);
    await screen.findByRole("link", { name: "How many sales?" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "How it ended" }), "waiting");
    await screen.findByText("Waiting for you", { selector: "td" });
    expect(screen.queryByRole("link", { name: "How many sales?" })).toBeNull();
    expect(harness.conversations).toHaveBeenLastCalledWith({ limit: 500, ending: "waiting" });
  });

  it("says when there are none", async () => {
    harness.conversations.mockResolvedValue({ ...page(null), rows: [] });
    renderWithProviders(<ConversationTable />);
    expect(await screen.findByText("No conversations.")).toBeTruthy();
  });
});
