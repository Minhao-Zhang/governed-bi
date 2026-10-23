import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/composer";

describe("Composer", () => {
  it("sends on Enter, keeps Shift+Enter as a new line, and shows the small print", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);

    const box = screen.getByRole("textbox", { name: "Ask a question about the governed data" });
    await userEvent.type(box, "line one");
    await userEvent.keyboard("{Shift>}{Enter}{/Shift}");
    await userEvent.type(box, "line two");

    expect(onSend).not.toHaveBeenCalled();
    expect((box as HTMLTextAreaElement).value).toBe("line one\nline two");

    await userEvent.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledExactlyOnceWith("line one\nline two");
    expect(screen.getByText("Read-only. Every query is checked before it runs.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });
});
