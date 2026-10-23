import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/sidebar/sidebar";
import type { ConversationSummary } from "@/lib/threads";

import { renderWithProviders } from "./render";

const harness = vi.hoisted(() => ({
  listConversations: vi.fn(),
  pendingClarifications: vi.fn(),
}));

vi.mock("@/lib/env", () => ({ LANGGRAPH_URL: "http://engine.test", USE_MOCKS: false }));

vi.mock("@/lib/threads", () => ({
  listConversations: (...args: unknown[]) => harness.listConversations(...args),
}));

vi.mock("@/lib/api-client", () => ({
  api: {
    pendingClarifications: () => harness.pendingClarifications(),
  },
}));

/** Local noon on a fixed day. Grouping uses this clock, not the machine's. */
const NOW = new Date(2026, 9, 22, 12, 0, 0);

function iso(daysBefore: number, msEarlier = 0): string {
  const day = new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate());
  day.setDate(day.getDate() - daysBefore);
  return new Date(day.getTime() - msEarlier).toISOString();
}

function row(threadId: string, question: string | null, updatedAt: string): ConversationSummary {
  return { thread_id: threadId, updated_at: updatedAt, status: "idle", question, turns: 1 };
}

const CONVERSATIONS: ConversationSummary[] = [
  row("t-wait", "Original sales question", iso(0)),
  row("t-today", "Today midnight", iso(0)),
  row("t-before-today", "Before today", iso(0, 1)),
  row("t-yesterday", "Yesterday midnight", iso(1)),
  row("t-before-yesterday", "Before yesterday", iso(1, 1)),
  row("t-seven", "Seven days midnight", iso(7)),
  row("t-before-seven", "Before seven days", iso(7, 1)),
  row("t-thirty", "Thirty days midnight", iso(30)),
  row("t-before-thirty", "Before thirty days", iso(30, 1)),
  row("t-empty", null, iso(40)),
];

const PENDING = {
  rows: [{ thread_id: "t-wait", question: "Which region do you mean?" }],
};

function buttonLabels(regionName: string): string[] {
  return within(screen.getByRole("region", { name: regionName }))
    .getAllByRole("button")
    .map((button) => button.textContent);
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  harness.listConversations.mockReset();
  harness.pendingClarifications.mockReset();
  harness.listConversations.mockResolvedValue(CONVERSATIONS);
  harness.pendingClarifications.mockResolvedValue(PENDING);
});

describe("Sidebar", () => {
  it("lists waiting conversations first, with a dot, and not again by date", async () => {
    renderWithProviders(<Sidebar now={NOW} />);
    await screen.findByRole("heading", { name: "Older" });
    await screen.findByRole("heading", { name: "Waiting for you" });
    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual([
      "Waiting for you",
      "Today",
      "Yesterday",
      "Previous 7 days",
      "Previous 30 days",
      "Older",
    ]);

    const waiting = screen.getByRole("region", { name: "Waiting for you" });
    // Labelled by the person's question, not the engine's, and listed only once.
    expect(within(waiting).getByRole("button", { name: "Original sales question" })).toBeTruthy();
    expect(waiting.querySelectorAll("[data-slot='waiting-dot']").length).toBe(1);
    expect(screen.getAllByText("Original sales question")).toHaveLength(1);
    expect(screen.queryByText("Which region do you mean?")).toBeNull();
    expect(
      screen.getByRole("region", { name: "Today" }).querySelector("[data-slot='waiting-dot']"),
    ).toBeNull();

    expect(buttonLabels("Today")).toEqual(["Today midnight"]);
    expect(buttonLabels("Yesterday")).toEqual(["Before today", "Yesterday midnight"]);
    expect(buttonLabels("Previous 7 days")).toEqual(["Before yesterday", "Seven days midnight"]);
    expect(buttonLabels("Previous 30 days")).toEqual(["Before seven days", "Thirty days midnight"]);
    expect(buttonLabels("Older")).toEqual(["Before thirty days", "Nothing asked yet"]);
    expect(
      screen.getByRole("button", { name: "Today midnight" }).querySelector(".truncate"),
    ).toBeTruthy();
  });

  it("omits the waiting heading when nothing is waiting", async () => {
    harness.pendingClarifications.mockResolvedValue({ rows: [] });
    renderWithProviders(<Sidebar now={NOW} />);
    await screen.findByRole("heading", { name: "Today" });
    expect(screen.queryByRole("heading", { name: "Waiting for you" })).toBeNull();
    expect(buttonLabels("Today")).toEqual(["Original sales question", "Today midnight"]);
  });

  it("opens a conversation and clears it", async () => {
    renderWithProviders(<Sidebar now={NOW} />);
    await userEvent.click(await screen.findByRole("button", { name: "Original sales question" }));
    expect(new URLSearchParams(window.location.search).get("thread")).toBe("t-wait");
    expect(
      screen.getByRole("button", { name: "Original sales question" }).getAttribute("aria-current"),
    ).toBe("page");

    await userEvent.click(screen.getByRole("button", { name: "Today midnight" }));
    expect(new URLSearchParams(window.location.search).get("thread")).toBe("t-today");
    expect(screen.getByRole("button", { name: "Today midnight" }).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(
      screen.getByRole("button", { name: "Original sales question" }).getAttribute("aria-current"),
    ).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(new URLSearchParams(window.location.search).get("thread")).toBeNull();
    expect(screen.getByRole("button", { name: "Today midnight" }).hasAttribute("aria-current")).toBe(
      false,
    );
  });
});
