import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnswerCard } from "@/components/answer/answer-card";
import { MOCK_AGENT_ANSWER, MOCK_AGENT_EVENTS, MOCK_REFUSAL } from "@/lib/mock/fixtures";
import { reduceSteps, type GovEvent, type TimelineStep } from "@/lib/steps";

import { renderWithProviders } from "./render";

/** The recorded turn, with its `execute` event's result detail replaced. */
function stepsWithExecute(detail: Record<string, unknown>): TimelineStep[] {
  return MOCK_AGENT_EVENTS.map((ev): GovEvent =>
    ev.step === "execute" ? { ...ev, detail: { ...ev.detail, ...detail } } : ev,
  ).reduce<TimelineStep[]>(reduceSteps, []);
}

/** The highlighter splits SQL into one span per token, so match the whole `<code>` block. */
function sqlContaining(fragment: string) {
  return (_: string, el: Element | null) =>
    el?.tagName === "CODE" && (el.textContent ?? "").replace(/\s+/g, " ").includes(fragment);
}

describe("AnswerCard", () => {
  it("shows the governed SQL, the outcome stamp and the answer text", () => {
    renderWithProviders(<AnswerCard answer={MOCK_AGENT_ANSWER} />, "engineer");

    expect(screen.getByText("Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.")).toBeTruthy();
    expect(screen.getByText("answered")).toBeTruthy();
    expect(screen.getByText("ledger: answered")).toBeTruthy();
    expect(screen.getByText(/1 passed governance/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
    expect(screen.getByText(sqlContaining("FROM gbi_demo_sales.customers c"))).toBeTruthy();
  });

  it("hides the SQL and badges from a business reader but keeps the answer", () => {
    renderWithProviders(<AnswerCard answer={MOCK_AGENT_ANSWER} />, "business");

    expect(screen.getByText(/Kansai 15/)).toBeTruthy();
    expect(screen.queryByText(sqlContaining("FROM gbi_demo_sales.customers c"))).toBeNull();
    expect(screen.queryByText("ledger: answered")).toBeNull();
  });

  it("renders no result grid, because the record does not carry the rows", () => {
    renderWithProviders(<AnswerCard answer={MOCK_AGENT_ANSWER} />, "engineer");

    expect(screen.queryByRole("table")).toBeNull();
  });

  it("reports the executed row count on the step trace", () => {
    renderWithProviders(
      <AnswerCard answer={MOCK_AGENT_ANSWER} steps={stepsWithExecute({})} />,
      "analyst",
    );

    expect(screen.getByText("Executed, 4 rows")).toBeTruthy();
  });

  it("reports an empty result as zero rows rather than hiding it", () => {
    renderWithProviders(
      <AnswerCard answer={MOCK_AGENT_ANSWER} steps={stepsWithExecute({ row_count: 0 })} />,
      "analyst",
    );

    expect(screen.getByText("Executed, 0 rows")).toBeTruthy();
  });

  it("marks a truncated result as truncated", () => {
    renderWithProviders(
      <AnswerCard
        answer={MOCK_AGENT_ANSWER}
        steps={stepsWithExecute({ row_count: 1000, truncated: true })}
      />,
      "analyst",
    );

    expect(screen.getByText("Executed, 1000 rows (truncated)")).toBeTruthy();
  });

  it("shows a refusal's own copy and no SQL", () => {
    renderWithProviders(<AnswerCard answer={MOCK_REFUSAL} />, "engineer");

    expect(screen.getByText(/needs a table this corpus does not license/)).toBeTruthy();
    expect(screen.getByText("refused")).toBeTruthy();
    expect(screen.getByText("refused by check")).toBeTruthy();
    expect(screen.getByText(/0 passed governance, 1 blocked/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
  });
});
