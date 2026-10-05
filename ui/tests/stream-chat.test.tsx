import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StreamChat } from "@/components/chat/stream-chat";
import {
  MOCK_AGENT_ANSWER,
  MOCK_AGENT_EVENTS,
  MOCK_CLARIFICATION_CLOSED,
  MOCK_REFUSAL,
} from "@/lib/mock/fixtures";

import { renderWithProviders } from "./render";

/**
 * `useStream` replaced by a stand-in whose state the test drives, so the transport under test is
 * the real `useStreamChat` fold: custom events into the timeline, `values.answer` into the card,
 * and `respondClarification` into `submit`. The options the component passed are kept so the
 * test can deliver events through the same callbacks the SDK would.
 */
type FakeState = {
  isLoading: boolean;
  messages: Array<{ id: string; type: string; content: string }>;
  values: Record<string, unknown>;
  interrupt?: { value: unknown };
};

const harness = vi.hoisted(() => ({
  options: null as null | {
    onCustomEvent: (data: unknown) => void;
    onFinish: (state: { values: unknown }) => void;
  },
  setState: null as null | ((s: FakeState) => void),
  submit: vi.fn(),
  stop: vi.fn(),
}));

vi.mock("@langchain/langgraph-sdk/react", async () => {
  const { useState } = await import("react");
  return {
    useStream: (options: NonNullable<typeof harness.options>) => {
      const [state, setState] = useState<FakeState>({ isLoading: false, messages: [], values: {} });
      harness.options = options;
      harness.setState = setState;
      return { ...state, submit: harness.submit, stop: harness.stop };
    },
  };
});

const HUMAN = { id: "h1", type: "human", content: "Top categories by revenue?" };
const AI = { id: "ai1", type: "ai", content: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15." };

function finishWith(answer: unknown) {
  const values = { messages: [HUMAN, AI], answer };
  act(() => {
    harness.options!.onFinish({ values });
    harness.setState!({ isLoading: false, messages: [HUMAN, AI], values });
  });
}

beforeEach(() => {
  harness.submit.mockReset();
  window.history.replaceState(null, "", "/");
});

describe("StreamChat", () => {
  it("renders a streamed answer from the recorded event sequence", () => {
    renderWithProviders(<StreamChat />, "analyst");

    act(() => harness.setState!({ isLoading: true, messages: [HUMAN], values: {} }));
    act(() => MOCK_AGENT_EVENTS.forEach((ev) => harness.options!.onCustomEvent(ev)));
    expect(screen.getByText("How this answer is being built")).toBeTruthy();
    expect(screen.getByText("Governance blocked: r_table_not_licensed (table)")).toBeTruthy();

    finishWith(MOCK_AGENT_ANSWER);

    expect(screen.queryByText("How this answer is being built")).toBeNull();
    expect(screen.getByText("Top categories by revenue?")).toBeTruthy();
    expect(screen.getByText(AI.content)).toBeTruthy();
    expect(screen.getByText("answered")).toBeTruthy();
    // The finished card keeps the live trace it was built from.
    expect(screen.getByText("Executed, 4 rows")).toBeTruthy();
  });

  it("sends a question with the subgraph stream options", async () => {
    renderWithProviders(<StreamChat />, "analyst");

    await userEvent.type(screen.getByRole("textbox", { name: "Ask a question about the governed data" }), "Top categories by revenue?{Enter}");

    expect(harness.submit).toHaveBeenCalledExactlyOnceWith(
      { messages: [{ type: "human", content: "Top categories by revenue?" }] },
      { streamMode: ["values", "messages", "custom"], streamSubgraphs: true },
    );
  });

  it("renders a refusal and a declined clarification as different endings", () => {
    const { unmount } = renderWithProviders(<StreamChat />, "analyst");
    finishWith(MOCK_REFUSAL);
    expect(screen.getByText(/needs a table this corpus does not license/)).toBeTruthy();
    expect(screen.getByText("refused")).toBeTruthy();
    unmount();

    renderWithProviders(<StreamChat />, "analyst");
    finishWith(MOCK_CLARIFICATION_CLOSED);
    expect(screen.getByText(/The user declined this clarification/)).toBeTruthy();
    expect(screen.getByText("clarification")).toBeTruthy();
    expect(screen.queryByText("refused")).toBeNull();
  });

  it("resumes the suspended run with the reader's answer", async () => {
    renderWithProviders(<StreamChat />, "analyst");
    act(() =>
      harness.setState!({
        isLoading: false,
        messages: [HUMAN],
        values: {},
        interrupt: {
          value: {
            kind: "clarification",
            clarification_id: "clar_live01",
            question: "Which fiscal year do you mean?",
            why: "The corpus defines two fiscal calendars.",
          },
        },
      }),
    );

    // On the prompt, and on the synthesised `ask_user` row of the live trace beside it.
    expect(screen.getAllByText("Which fiscal year do you mean?").length).toBeGreaterThan(0);
    expect((screen.getByRole("textbox", { name: "Ask a question about the governed data" }) as HTMLTextAreaElement).disabled).toBe(true);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Answer the clarification" }),
      "FY2025{Enter}",
    );

    expect(harness.submit).toHaveBeenCalledExactlyOnceWith(null, {
      streamMode: ["values", "messages", "custom"],
      streamSubgraphs: true,
      command: { resume: { clarification_id: "clar_live01", answer: "FY2025" } },
    });
  });
});
