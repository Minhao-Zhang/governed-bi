/**
 * Links into the semantic layer view, which embeds the ossie-viewer build (ADR 0021). The viewer
 * opens the model at `?file=` and the item named by its `#dataset/<name>` fragment.
 */

import { LANGGRAPH_URL } from "@/lib/env";

/** The viewer's own encoding: a dot is %2E, so the dot in `#field/a.b` stays its separator. */
function encodeName(value: string): string {
  return encodeURIComponent(value).replaceAll(".", "%2E");
}

/** The view's URL for a table id `schema.table`, or for a whole schema. */
export function layerHref(schema: string, dataset?: string | null): string {
  const search = new URLSearchParams({ schema });
  if (dataset) search.set("dataset", dataset);
  return `/layer?${search.toString()}`;
}

/** `layerHref` for the first table id an answer used, or null when it names none. */
export function layerHrefForTable(tableId: string | undefined): string | null {
  if (!tableId) return null;
  const dot = tableId.indexOf(".");
  if (dot <= 0 || dot === tableId.length - 1) return null;
  return layerHref(tableId.slice(0, dot), tableId.slice(dot + 1));
}

/** The frame's source: the viewer, the schema's model from `GET /layer/{schema}`, and the dataset. */
export function viewerSrc(schema: string, dataset?: string | null): string {
  const file = `${LANGGRAPH_URL}/layer/${encodeURIComponent(schema)}`;
  const fragment = dataset ? `#dataset/${encodeName(dataset)}` : "";
  return `/viewer/index.html?file=${encodeURIComponent(file)}${fragment}`;
}
