import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Answer } from "@/components/answer/answer";
import { parseAnswer, turnAnswersByMessageId } from "@/lib/stream-messages";
import type { AnswerView } from "@/lib/types";

import { renderWithProviders } from "./render";

const SQL = "SELECT r.name FROM sales.regions";

function answered(overrides: Partial<AnswerView> & { assumptions?: unknown }): AnswerView {
  const { assumptions, record, ...rest } = overrides;
  return {
    outcome: "answered",
    text: null,
    answer_text: "Two regions lead.",
    record: {
      assumptions,
      generated_sql: SQL,
      execution: { attempts: [{ passed: false, result_layer: "TABLES" }] },
      retrieved_tables: ["sales.regions"],
      usage: [{ model: "gpt-5.6-luna" }],
      latency_sec: 1.5,
      ...record,
    },
    ...rest,
  };
}

function renderAnswer(answer: AnswerView, send: (text: string) => void = () => {}) {
  return renderWithProviders(<Answer answer={answer} question="How many customers?" send={send} />);
}

describe("assumptions", () => {
  it("ends a layer assumption with its source in grey", () => {
    const { container } = renderAnswer(
      answered({
        assumptions: [
          {
            statement: "Closed businesses are included.",
            about: "closed",
            basis: "layer",
            asset_id: "gbi_demo_sales.term.closed_business",
          },
        ],
      }),
    );

    expect(screen.getByText("To answer this, I assumed:")).toBeTruthy();
    expect(screen.getByText("From your definition: closed business.")).toBeTruthy();
    expect(screen.getByText("From your definition: closed business.").className).toContain("--muted");
    expect(container.querySelector("[class*='--waiting']")).toBeNull();
    expect(screen.queryByRole("button", { name: "Change" })).toBeNull();
  });

  it("gives a convention assumption no ending", () => {
    const { container } = renderAnswer(
      answered({
        assumptions: [{ statement: "Counts are of distinct customers.", about: null, basis: "convention", asset_id: null }],
      }),
    );

    const item = container.querySelector("[data-basis='convention']");
    expect(item?.textContent?.trim()).toBe("Counts are of distinct customers.");
    expect(container.querySelector("[class*='--waiting']")).toBeNull();
    expect(screen.queryByText("This is a guess.")).toBeNull();
  });

  it("ends a guess in amber with a Change link", () => {
    const { container } = renderAnswer(
      answered({
        assumptions: [
          { statement: "Active means logged in within 30 days.", about: "active", basis: "guess", asset_id: null },
        ],
      }),
    );

    const guess = screen.getByText("This is a guess.");
    expect(guess.className).toContain("--waiting");
    expect(container.querySelectorAll("[class*='--waiting']").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Change" }).className).toContain("--waiting");
    expect(screen.queryByText(/From your definition/)).toBeNull();
  });

  it("says what the query did for a found_in_query assumption", () => {
    const { container } = renderAnswer(
      answered({
        assumptions: [
          { statement: "Unmatched regions are dropped.", about: null, basis: "found_in_query", asset_id: null },
        ],
      }),
    );

    expect(screen.getByText(/The query does this, though you didn't ask for it/)).toBeTruthy();
    expect(container.querySelector("[class*='--waiting']")).toBeNull();
    expect(screen.queryByRole("button", { name: "Change" })).toBeNull();
  });
});

describe("result table", () => {
  it("shows how many rows were kept when the result is longer", () => {
    renderAnswer(
      answered({
        assumptions: [],
        result_table: {
          columns: ["region"],
          rows: Array.from({ length: 20 }, (_, index) => [`r${index}`]),
          row_count: 1204,
        },
      }),
    );

    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("Showing 20 of 1,204 rows")).toBeTruthy();
  });

  it("omits the caption when every row is shown, and omits the table when there is none", () => {
    const { rerender } = renderAnswer(
      answered({
        assumptions: [],
        result_table: { columns: ["region"], rows: [["Kansai"], ["Great Lakes"]], row_count: 2 },
      }),
    );

    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.queryByText(/Showing/)).toBeNull();

    rerender(<Answer answer={answered({ assumptions: [] })} question="How many customers?" send={() => {}} />);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows a reloaded thread's table from the turn row", () => {
    const table = { columns: ["city"], rows: [["Katy"]], row_count: 1 };
    const turns = [
      {
        outcome: "answered",
        answer_text: "The alias is Katy.",
        result_table: table,
        record: { turn_id: "turn-1", usage: [{ turn_index: 1, model: "gpt-5.6-luna" }] },
      },
    ];
    const messages = [
      { id: "h1", type: "human" as const, content: "Alias?" },
      { id: "a1", type: "ai" as const, content: "The alias is Katy." },
    ];
    const answer = turnAnswersByMessageId(turns, messages).get("a1");
    expect(answer?.result_table?.columns).toEqual(["city"]);

    renderWithProviders(<Answer answer={answer!} question="Alias?" send={() => {}} />);
    expect(screen.getByRole("columnheader", { name: "city" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "Katy" })).toBeTruthy();
  });
});

describe("Change", () => {
  it("sends a follow-up with the corrected assumption", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    renderAnswer(
      answered({
        assumptions: [
          { statement: "Active means logged in within 30 days.", about: "active", basis: "guess", asset_id: null },
        ],
      }),
      send,
    );

    await user.click(screen.getByRole("button", { name: "Change" }));
    const input = screen.getByRole("textbox", { name: "Corrected assumption" }) as HTMLInputElement;
    expect(input.value).toBe("Active means logged in within 30 days.");
    await user.clear(input);
    await user.type(input, "Active means the account is open");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(send).toHaveBeenCalledExactlyOnceWith(
      "How many customers? (Assume instead: Active means the account is open)",
    );
  });
});

describe("Details and Copy", () => {
  it("shows the SQL and the six checks, and which layer refused", async () => {
    const user = userEvent.setup();
    renderAnswer(answered({ assumptions: [] }));

    await user.click(screen.getByRole("button", { name: "Details" }));

    expect(screen.getByText(SQL)).toBeTruthy();
    expect(document.querySelectorAll("[data-check]")).toHaveLength(6);
    expect(screen.getByText("parse: passed")).toBeTruthy();
    expect(screen.getByText("no write: passed")).toBeTruthy();
    expect(screen.getByText("functions: passed")).toBeTruthy();
    expect(screen.getByText("binding: passed")).toBeTruthy();
    expect(screen.getByText("columns: passed")).toBeTruthy();
    expect(screen.getByText("tables: refused")).toBeTruthy();
    expect(screen.getByText("sales.regions").className).toContain("font-mono");
    expect(screen.getByText("gpt-5.6-luna")).toBeTruthy();
    expect(screen.getByText("1.5 seconds")).toBeTruthy();
  });

  it("copies the answer text and the table", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    renderAnswer(
      answered({
        assumptions: [],
        result_table: { columns: ["region", "customers"], rows: [["Kansai", 15]], row_count: 1 },
      }),
    );

    await user.click(screen.getByRole("button", { name: "Copy" }));

    expect(writeText).toHaveBeenCalledOnce();
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("Two regions lead.");
    expect(copied).toContain("region\tcustomers");
    expect(copied).toContain("Kansai\t15");
  });
});

describe("refusal", () => {
  it("renders Refusal instead of the answer", () => {
    renderAnswer({
      outcome: "refused",
      text: "This question needs a table the corpus does not have.",
      answer_text: null,
      record: {},
    });

    expect(document.querySelector("[data-refusal]")).toBeTruthy();
    expect(screen.getByText("I can't answer this from your data.")).toBeTruthy();
    expect(screen.getByText("This question needs a table the corpus does not have.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("parseAnswer", () => {
  it("keeps a result table on the live answer and drops a null one", () => {
    const table = { columns: ["city"], rows: [["Katy"]], row_count: 1 };
    const live = parseAnswer({
      outcome: "answered",
      text: null,
      answer_text: "The alias is Katy.",
      record: {},
      result_table: table,
    });
    expect(live?.result_table).toEqual(table);

    const none = parseAnswer({
      outcome: "answered",
      text: null,
      answer_text: "Nothing ran.",
      record: {},
      result_table: null,
    });
    expect(none?.result_table ?? null).toBeNull();
  });
});

describe("crashed turn", () => {
  it("says the turn failed instead of drawing nothing", () => {
    renderAnswer({ outcome: "crashed", text: null, answer_text: null, record: { error_type: "NoAnswerCall" } });
    expect(
      screen.getByText("Something went wrong with this question. You can ask it again."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
  });

  it("keeps system text when a crash has some", () => {
    renderAnswer({ outcome: "crashed", text: "The query timed out.", answer_text: null, record: {} });
    expect(screen.getByText("The query timed out.")).toBeTruthy();
  });
});
