"use client";

/**
 * The "format SQL" display preference, shared by every SQL block on the page.
 *
 * It lives outside React state because a transcript holds one block per answer, and
 * a per-block toggle would mean flipping it again on every turn. A tiny module-level
 * store + `useSyncExternalStore` keeps every mounted block in step, and the choice
 * persists in localStorage so it survives a reload.
 *
 * Defaults to ON: the engine emits single-line SQL, which is what the audit record
 * should hold but not how anyone wants to read a join.
 */

import { useSyncExternalStore } from "react";

const STORAGE_KEY = "governed-bi:sql-format";
const DEFAULT = true;

let value = DEFAULT;
let hydrated = false;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Read the stored preference on first client access. Deliberately NOT read during
 * module init: on the server there is no localStorage, and seeding from it during
 * the first render would make the server and client markup disagree.
 */
function getSnapshot(): boolean {
  if (!hydrated) {
    hydrated = true;
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored === "0" || stored === "1") value = stored === "1";
    } catch {
      // Private mode / blocked storage: keep the default, never throw over a
      // display preference.
    }
  }
  return value;
}

/** The server render always uses the default, so hydration matches. */
function getServerSnapshot(): boolean {
  return DEFAULT;
}

export function setSqlFormatEnabled(next: boolean): void {
  if (next === value) return;
  value = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
  } catch {
    // Non-persistent is fine; the in-memory value still drives this session.
  }
  emit();
}

/** `[enabled, setEnabled]` — shared across every SQL block. */
export function useSqlFormatEnabled(): [boolean, (next: boolean) => void] {
  const enabled = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return [enabled, setSqlFormatEnabled];
}
