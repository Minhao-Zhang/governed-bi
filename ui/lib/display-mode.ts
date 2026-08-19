/**
 * Client-side override for `ui_display_mode` — the role tier this browser renders as.
 *
 * The backend's `/capabilities` response is meant to set the *default* tier, and a user overrides
 * it here without editing config or restarting anything (`/settings`). **The server does not
 * populate that field yet** — `grep -r ui_display_mode src/` on the engine is empty — so today
 * this store is the only thing that decides. That is stated rather than hidden: a future
 * multi-tenant server fills the field and every screen already honours it, with no interface
 * change. See `docs/detentai-role-tiers-and-clarification-cancel.md`.
 *
 * Persisted to localStorage and shared across every mounted component via a tiny external store
 * (mirrors how `next-themes` keeps its toggle and the rendered page in sync) — no Context
 * provider, no prop drilling. `null` means "no override, use whatever `/capabilities` says."
 */

import { useSyncExternalStore } from "react";

/** The three tiers, and the two spellings they replace.
 *
 * `simple`/`audit` are read and mapped forward rather than rejected: they are already sitting in
 * users' localStorage from the two-state version, and a stored value that silently stopped working
 * would look like the setting failing to persist. */
export type Tier = "business" | "analyst" | "engineer";
export type DisplayModeOverride = Tier | null;

const LEGACY: Record<string, Tier> = { simple: "business", audit: "engineer" };
const TIERS: readonly string[] = ["business", "analyst", "engineer"];

const STORAGE_KEY = "governed-bi:ui-display-mode-override";

const listeners = new Set<() => void>();

function readOverride(): DisplayModeOverride {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === null) return null;
  if (TIERS.includes(raw)) return raw as Tier;
  return LEGACY[raw] ?? null;
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
