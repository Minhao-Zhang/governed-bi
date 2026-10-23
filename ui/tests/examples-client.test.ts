import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api-client";
import { MOCK_EXAMPLE_QUESTIONS, MOCK_NEARBY_QUESTIONS } from "@/lib/mock/fixtures";

const fetchMock = vi.fn();

vi.mock("@/lib/env", () => ({ LANGGRAPH_URL: "", USE_MOCKS: true }));

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("api.examples in mock mode", () => {
  it("returns fixture questions without a network call", async () => {
    await expect(api.examples({ limit: 3 })).resolves.toEqual({
      examples: MOCK_EXAMPLE_QUESTIONS,
    });
    await expect(api.examples({ limit: 2, near: "customers" })).resolves.toEqual({
      examples: MOCK_NEARBY_QUESTIONS.slice(0, 2),
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
