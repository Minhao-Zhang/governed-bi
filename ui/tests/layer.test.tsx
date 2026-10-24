import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LayerView } from "@/components/layer/layer-view";
import { layerHrefForTable, viewerSrc } from "@/lib/layer";

import { renderWithProviders } from "./render";

vi.mock("@/lib/env", () => ({ LANGGRAPH_URL: "http://engine.test", USE_MOCKS: false }));

vi.mock("@/lib/api-client", () => ({
  api: { layer: async () => ({ schemas: ["beer", "formula_1"] }) },
}));

describe("links into the semantic layer", () => {
  it("names the schema and the dataset of a table id", () => {
    expect(layerHrefForTable("formula_1.carreras")).toBe("/layer?schema=formula_1&dataset=carreras");
    expect(layerHrefForTable("no_dot")).toBeNull();
    expect(layerHrefForTable(undefined)).toBeNull();
  });

  it("opens the viewer on the served model and the dataset, in the viewer's own encoding", () => {
    const src = viewerSrc("formula_1", "a.b");
    const url = new URL(src, "http://ui.test");
    expect(url.pathname).toBe("/viewer/index.html");
    expect(url.searchParams.get("file")).toBe("http://engine.test/layer/formula_1");
    expect(url.hash).toBe("#dataset/a%2Eb");
  });
});

describe("the semantic layer view", () => {
  it("lists the schemas and frames the viewer on the one asked for", async () => {
    renderWithProviders(<LayerView schema="formula_1" dataset="carreras" />);
    expect(await screen.findByRole("link", { name: "beer" })).toBeTruthy();
    const frame = await screen.findByTitle("The formula_1 model in the Ossie viewer");
    expect(frame.getAttribute("src")).toBe(viewerSrc("formula_1", "carreras"));
  });

  it("opens the first schema when the one asked for is not served", async () => {
    renderWithProviders(<LayerView schema="absent" dataset="x" />);
    const frame = await screen.findByTitle("The beer model in the Ossie viewer");
    expect(frame.getAttribute("src")).toBe(viewerSrc("beer", null));
  });
});
