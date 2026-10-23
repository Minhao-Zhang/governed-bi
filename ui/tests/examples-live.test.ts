import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api-client";

const fetchMock = vi.fn();

vi.mock("@/lib/env", () => ({ LANGGRAPH_URL: "http://engine.test", USE_MOCKS: false }));

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("api.examples", () => {
  it("requests GET /examples with limit and near", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ examples: [{ question: "How many?", db_id: "sales" }] }),
    });
    await expect(api.examples({ limit: 3, near: "regions?" })).resolves.toEqual({
      examples: [{ question: "How many?", db_id: "sales" }],
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.origin + url.pathname).toBe("http://engine.test/examples");
    expect(url.searchParams.get("limit")).toBe("3");
    expect(url.searchParams.get("near")).toBe("regions?");
  });
});
