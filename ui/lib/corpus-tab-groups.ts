/**
 * Client-side, per-browser preference for which `/corpus` curation tab *groups* render —
 * grouped by what the tabs are for (Setup Wizard, Clarifications, Reports, Approvals, Trust
 * Loop), not toggled one tab at a time.
 *
 * **A view preference layered on top of the capability gate, never a replacement for it.**
 * `canCurateCorpus` / `tierShowsTrustLoopMetrics` (`lib/capabilities.ts`) decide whether a group
 * can ever render; this store decides whether it does, on top of that. See
 * `lib/capabilities.ts::corpusTabGroupVisible`, which is the only place the two are combined —
 * never inline that comparison in a component.
 *
 * **Every group defaults ON.** A group that defaulted off would silently remove a tab a reader
 * who has never opened Settings already relies on, which reads as the tab breaking rather than
 * a preference.
 *
 * Same persistence shape as `lib/display-mode.ts`'s tier override: localStorage, mirrored across
 * every mounted component via a tiny external store, no Context provider.
 */

import { useSyncExternalStore } from "react";

export type CorpusTabGroup = "wizard" | "clarifications" | "reports" | "approvals" | "trust-loop";

export const CORPUS_TAB_GROUPS: readonly CorpusTabGroup[] = [
  "wizard",
  "clarifications",
  "reports",
  "approvals",
  "trust-loop",
];

const DEFAULT: Record<CorpusTabGroup, boolean> = {
  wizard: true,
  clarifications: true,
  reports: true,
  approvals: true,
  "trust-loop": true,
};

const STORAGE_KEY = "governed-bi:corpus-tab-groups";

const listeners = new Set<() => void>();

function readStored(): Record<CorpusTabGroup, boolean> {
  if (typeof window === "undefined") return DEFAULT;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === null) return DEFAULT;
  try {
    const parsed = JSON.parse(raw) as Partial<Record<CorpusTabGroup, boolean>>;
    return { ...DEFAULT, ...parsed };
  } catch {
    return DEFAULT;
  }
}

// Cached snapshot so `useSyncExternalStore` gets a stable reference between renders when
// nothing changed (it requires this to avoid an infinite loop) — same trick as `display-mode.ts`.
let cached: Record<CorpusTabGroup, boolean> = DEFAULT;
let cachedRead = false;

function getSnapshot(): Record<CorpusTabGroup, boolean> {
  if (!cachedRead) {
    cached = readStored();
    cachedRead = true;
  }
  return cached;
}

function getServerSnapshot(): Record<CorpusTabGroup, boolean> {
  return DEFAULT;
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  return () => listeners.delete(onStoreChange);
}

/** Turn one group on or off for this browser. */
export function setCorpusTabGroup(group: CorpusTabGroup, on: boolean): void {
  if (typeof window === "undefined") return;
  const next = { ...getSnapshot(), [group]: on };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  cached = next;
  cachedRead = true;
  for (const listener of listeners) listener();
}

/** Read every group's preference reactively; re-renders on every change. */
export function useCorpusTabGroups(): Record<CorpusTabGroup, boolean> {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
