import { describe, expect, it } from "vitest";

import { parseClarification } from "@/lib/clarification";

const BASE = {
  kind: "clarification",
  clarification_id: "clar_live01",
  question: "Which year do you mean?",
  why: "Two calendars are in use.",
  basis: "data_definition",
};

describe("clarification choices", () => {
  it("accepts an optional list of strings and still accepts the mock's objects", () => {
    const strings = parseClarification({ ...BASE, choices: ["calendar year", "fiscal year"] });
    expect(strings?.choices).toEqual([
      { id: "calendar year", label: "calendar year" },
      { id: "fiscal year", label: "fiscal year" },
    ]);

    const objects = parseClarification({
      ...BASE,
      choices: [{ id: "opt_status", label: "Account status = 'active'" }],
    });
    expect(objects?.choices).toEqual([{ id: "opt_status", label: "Account status = 'active'" }]);

    const freeform = parseClarification(BASE);
    expect(freeform?.choices).toBeUndefined();
  });
});
