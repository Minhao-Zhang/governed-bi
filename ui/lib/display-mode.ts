/**
 * Client-side override for `ui_display_mode` (UtkuAI Phase 1b).
 *
 * The backend's `/capabilities` response sets the *default* rendering mode,
 * but a user should be able to flip Simple/Audit themselves without editing
 * `governed_bi.toml` and restarting the backend — e.g. to demo both the
 * business-user view and the engineering-audit view in one live session.
 *
 * Persisted to localStorage and shared across every mounted component via a
 * tiny external store (mirrors how `next-themes` keeps the theme toggle and
 * the rendered page in sync) — no Context provider or prop drilling needed.
 * `null` means "no override, use whatever `/capabilities` says."
 */

import { useSyncExternalStore } from "react";

export type DisplayModeOverride = "simple" | "audit" | null;

const STORAGE_KEY = "governed-bi:ui-display-mode-override";

const listeners = new Set<() => void>();

function readOverride(): DisplayModeOverride {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw === "simple" || raw === "audit" ? raw : null;
}

// Cached snapshot so `useSyncExternalStore` gets a stable reference between
// renders when nothing changed (it requires this to avoid an infinite loop).
let cached: DisplayModeOverride = null;
let cachedRead = false;

function getSnapshot(): DisplayModeOverride {
  if (!cachedRead) {
    cached = readOverride();
    cachedRead = true;
  }
  return cached;
}

function getServerSnapshot(): DisplayModeOverride {
  return null;
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  return () => listeners.delete(onStoreChange);
}

/** Set (or clear, with `null`) the client-side display-mode override. */
export function setDisplayModeOverride(value: DisplayModeOverride): void {
  if (typeof window === "undefined") return;
  if (value === null) {
    window.localStorage.removeItem(STORAGE_KEY);
  } else {
    window.localStorage.setItem(STORAGE_KEY, value);
  }
  cached = value;
  cachedRead = true;
  for (const listener of listeners) listener();
}

/** Read the current override reactively; re-renders on every change. */
export function useDisplayModeOverride(): DisplayModeOverride {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
