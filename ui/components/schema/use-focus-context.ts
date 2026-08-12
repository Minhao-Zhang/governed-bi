"use client";

import { useCallback, useMemo, useState } from "react";

/**
 * Focus+context for the graph views: hovering a node highlights it and its
 * 1-hop neighborhood and dims everything else, so a dense graph stays readable
 * without relayout. Shared by the ER diagram and the knowledge graph.
 */
export function useFocusContext(edges: { source: string; target: string }[]) {
  const [focusedId, setFocusedId] = useState<string | null>(null);

  const neighbors = useMemo(() => {
    if (focusedId === null) return null;
    const set = new Set<string>([focusedId]);
    for (const e of edges) {
      if (e.source === focusedId) set.add(e.target);
      if (e.target === focusedId) set.add(e.source);
    }
    return set;
  }, [focusedId, edges]);

  /** True when a node should be dimmed (a focus is active and it's outside it). */
  const dimNode = useCallback(
    (id: string) => neighbors !== null && !neighbors.has(id),
    [neighbors],
  );

  /** True when an edge should be dimmed (doesn't touch the focused node). */
  const dimEdge = useCallback(
    (source: string, target: string) =>
      focusedId !== null && source !== focusedId && target !== focusedId,
    [focusedId],
  );

  const focus = useCallback((id: string) => setFocusedId(id), []);
  const clear = useCallback(() => setFocusedId(null), []);

  return { focusedId, focus, clear, dimNode, dimEdge, active: focusedId !== null };
}
