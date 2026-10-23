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
 * the real `useStreamChat` fold: custom events into the running line, `values.answer` into the
 * transcript, and `respondClarification` into `submit`.
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
    renderWithProviders(<StreamChat />);

    act(() => harness.setState!({ isLoading: true, messages: [HUMAN], values: {} }));
    act(() => MOCK_AGENT_EVENTS.forEach((ev) => harness.options!.onCustomEvent(ev)));
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status").textContent).toBe("Finishing the answer…");

    finishWith(MOCK_AGENT_ANSWER);

    expect(screen.queryByRole("status")).toBeNull();
    const question = screen.getByText("Top categories by revenue?");
    expect(question.hasAttribute("data-question")).toBe(true);
    expect(screen.getByText(AI.content).closest("[data-answer]")).toBeTruthy();
    expect(screen.queryByText("answered")).toBeNull();
    expect(screen.queryByText("Executed, 4 rows")).toBeNull();
  });

  it("sends a question with the subgraph stream options", async () => {
    renderWithProviders(<StreamChat />);

    await userEvent.type(screen.getByRole("textbox", { name: "Ask a question about the governed data" }), "Top categories by revenue?{Enter}");

    expect(harness.submit).toHaveBeenCalledExactlyOnceWith(
      { messages: [{ type: "human", content: "Top categories by revenue?" }] },
      { streamMode: ["values", "messages", "custom"], streamSubgraphs: true },
    );
  });

  it("renders a refusal and a declined clarification as different endings", () => {
    const { unmount } = renderWithProviders(<StreamChat />);
    finishWith(MOCK_REFUSAL);
    expect(screen.getByText(/needs a table this corpus does not license/)).toBeTruthy();
    expect(screen.queryByText("refused")).toBeNull();
    unmount();

    renderWithProviders(<StreamChat />);
    finishWith(MOCK_CLARIFICATION_CLOSED);
    expect(screen.getByText(/The user declined this clarification/)).toBeTruthy();
    expect(screen.queryByText("clarification")).toBeNull();
    expect(screen.queryByText("refused")).toBeNull();
  });

  it("resumes the suspended run with the reader's answer", async () => {
    renderWithProviders(<StreamChat />);
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

    expect(screen.getByText("Which fiscal year do you mean?")).toBeTruthy();
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
