import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/sidebar/sidebar";
import type { ConversationRow, Conversations } from "@/lib/types";

import { renderWithProviders } from "./render";

const harness = vi.hoisted(() => ({ conversations: vi.fn() }));

vi.mock("@/lib/env", () => ({ LANGGRAPH_URL: "http://engine.test", USE_MOCKS: false }));

vi.mock("@/lib/api-client", () => ({
  api: { conversations: (...args: unknown[]) => harness.conversations(...args) },
}));

/** Local noon on a fixed day. Grouping uses this clock, not the machine's. */
const NOW = new Date(2026, 9, 22, 12, 0, 0);

function iso(daysBefore: number, msEarlier = 0): string {
  const day = new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate());
  day.setDate(day.getDate() - daysBefore);
  return new Date(day.getTime() - msEarlier).toISOString();
}

function row(
  threadId: string,
  question: string | null,
  updatedAt: string,
  extra: Partial<ConversationRow> = {},
): ConversationRow {
  return {
    thread_id: threadId,
    updated_at: updatedAt,
    status: "idle",
    waiting: false,
    ending: "answered",
    question,
    elided_turns: 0,
    turns: [],
    ...extra,
  };
}

const CONVERSATIONS: ConversationRow[] = [
  row("t-wait", "Original sales question", iso(0), { waiting: true, ending: "waiting" }),
  row("t-today", "Today midnight", iso(0), { ending: "not_answered" }),
  row("t-before-today", "Before today", iso(0, 1), { ending: "stopped" }),
  row("t-yesterday", "Yesterday midnight", iso(1), { ending: "failed" }),
  row("t-before-yesterday", "Before yesterday", iso(1, 1)),
  row("t-seven", "Seven days midnight", iso(7)),
  row("t-before-seven", "Before seven days", iso(7, 1)),
  row("t-thirty", "Thirty days midnight", iso(30)),
  row("t-before-thirty", "Before thirty days", iso(30, 1)),
  row("t-empty", null, iso(40), { ending: null }),
];

function page(rows: ConversationRow[]): Conversations {
  return {
    rows,
    meta: {
      n: rows.length,
      total: rows.length,
      truncated: false,
      threads_scanned: rows.length,
      limit: 50,
      offset: 0,
      ending: null,
    },
  };
}

function buttonLabels(regionName: string): string[] {
  return within(screen.getByRole("region", { name: regionName }))
    .getAllByRole("button")
    .map((button) => button.getAttribute("aria-label") ?? "");
}

function endingOf(name: string): string | null {
  const button = screen.getByRole("button", { name });
  return button.querySelector("[data-slot='ending']")?.textContent ?? null;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  harness.conversations.mockReset();
  harness.conversations.mockResolvedValue(page(CONVERSATIONS));
});

describe("Sidebar", () => {
  it("lists waiting conversations first, with a dot, and not again by date", async () => {
    renderWithProviders(<Sidebar now={NOW} />);
    await screen.findByRole("heading", { name: "Older" });
    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual([
      "Waiting for you",
      "Today",
      "Yesterday",
      "Previous 7 days",
      "Previous 30 days",
      "Older",
    ]);

    const waiting = screen.getByRole("region", { name: "Waiting for you" });
    expect(within(waiting).getByRole("button", { name: "Original sales question" })).toBeTruthy();
    expect(waiting.querySelectorAll("[data-slot='waiting-dot']").length).toBe(1);
    expect(screen.getAllByRole("button", { name: "Original sales question" })).toHaveLength(1);
    expect(
      screen.getByRole("region", { name: "Today" }).querySelector("[data-slot='waiting-dot']"),
    ).toBeNull();

    expect(buttonLabels("Today")).toEqual(["Today midnight"]);
    expect(buttonLabels("Yesterday")).toEqual(["Before today", "Yesterday midnight"]);
    expect(buttonLabels("Previous 7 days")).toEqual(["Before yesterday", "Seven days midnight"]);
    expect(buttonLabels("Previous 30 days")).toEqual(["Before seven days", "Thirty days midnight"]);
    expect(buttonLabels("Older")).toEqual(["Before thirty days", "Nothing asked yet"]);
  });

  it("says under each conversation how it ended", async () => {
    renderWithProviders(<Sidebar now={NOW} />);
    await screen.findByRole("heading", { name: "Older" });
    expect(endingOf("Original sales question")).toBe("Waiting for you");
    expect(endingOf("Today midnight")).toBe("Couldn't answer");
    expect(endingOf("Before today")).toBe("Stopped");
    expect(endingOf("Yesterday midnight")).toBe("Failed");
    expect(endingOf("Before yesterday")).toBe("Answered");
    expect(endingOf("Nothing asked yet")).toBeNull();
  });

  it("omits the waiting heading when nothing is waiting", async () => {
    harness.conversations.mockResolvedValue(
      page(CONVERSATIONS.map((r) => ({ ...r, waiting: false }))),
    );
    renderWithProviders(<Sidebar now={NOW} />);
    await screen.findByRole("heading", { name: "Today" });
    expect(screen.queryByRole("heading", { name: "Waiting for you" })).toBeNull();
    expect(buttonLabels("Today")).toEqual(["Original sales question", "Today midnight"]);
  });

  it("links to the Conversations page", async () => {
    renderWithProviders(<Sidebar now={NOW} />);
    expect(screen.getByRole("link", { name: "Conversations" }).getAttribute("href")).toBe(
      "/conversations",
    );
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
