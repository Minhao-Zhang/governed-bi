import { describe, expect, it } from "vitest";

import { correctedAssumptionFollowUp } from "@/lib/assumption-follow-up";

describe("correctedAssumptionFollowUp", () => {
  it("keeps the question and states the corrected assumption", () => {
    expect(correctedAssumptionFollowUp("How many customers?", "Active means the account is open")).toBe(
      "How many customers? (Assume instead: Active means the account is open)",
    );
  });
});
