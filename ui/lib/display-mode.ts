/**
 * Which display mode this browser renders as — how much of a turn's machinery a reader sees.
 *
 * Three modes, widening: `business` (the answer and whether it consulted your data), `analyst`
 * (plus what was retrieved and licensed), `engineer` (plus the generated SQL, the attempt ledger
 * and the record). The engine sends the same payload to all three; this only decides what is
 * rendered from it.
 *
 * **This is display, not permission. It is not a security boundary and must never be described as
 * one.** Anyone can set it — it is a value in their own `localStorage` — and the engine neither
 * knows nor honours it. What actually withholds an asset is the access seam (ADR 0012): a grant
 * removes it from the model's prompt, from the four tools that can name one, and from every route
 * that projects a corpus asset. Choosing `business` hides the SQL panel from a reader who did not
 * want it; it does not stop anyone from reading the SQL out of `/audit/turns`, which today needs no
 * credential at all (audit finding A7, open). Presenting this control as protection would make it
 * exactly the kind of thing `docs/enterprise-fork.md` warns about: something that looks like a
 * boundary and is not.
 *
 * **Deliberately client-only** (owner's decision, 2026-08-19). There is no `ui_display_mode` on
 * `/capabilities` and nothing in the engine produces one, so this store is the whole mechanism.
 * The cost is that a deployment cannot set a default mode for its readers — worth stating, since
 * the alternative was considered and declined rather than overlooked.
 *
 * **On the word "mode" rather than "tier".** `tier` already means two other things here and a
 * third would be worse than a longer word: v1 required a *reliability* tier on the answer card,
 * which ADR 0007 §3 forbids and a test pins; and `RecordField.tier` off the engine's record
 * register says *why a field is recorded* (`identity` | `treatment` | `decision` | `outcome` |
 * `cost` | `health`), which `auditTraceFieldSchema` carries. Neither is this.
 *
 * Persisted to `localStorage` and shared across mounted components through a small external store,
 * the way `next-themes` keeps its toggle and the rendered page in sync — no context provider, no
 * prop drilling.
 */

import { useSyncExternalStore } from "react";

export type DisplayMode = "business" | "analyst" | "engineer";

/** Widening order. Index comparison is what `atLeast` uses, so the order is the contract. */
export const DISPLAY_MODES: readonly DisplayMode[] = ["business", "analyst", "engineer"];

/** What a reader sees before touching the control. */
export const DEFAULT_DISPLAY_MODE: DisplayMode = "business";

const STORAGE_KEY = "governed-bi:display-mode";

const listeners = new Set<() => void>();

function isMode(value: unknown): value is DisplayMode {
  return typeof value === "string" && (DISPLAY_MODES as readonly string[]).includes(value);
}

/**
 * The stored mode, or the default.
 *
 * An unrecognised stored value falls back to the default rather than being mapped forward from
 * some older spelling. There is no older spelling in this repository — the fork this control came
 * from carried a `simple`/`audit` two-state version and needed the migration; adopting the
 * migration too would have shipped a branch that can never run, justified by a history that is
 * not ours.
 */
function read(): DisplayMode {
  if (typeof window === "undefined") return DEFAULT_DISPLAY_MODE;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return isMode(raw) ? raw : DEFAULT_DISPLAY_MODE;
}

// `useSyncExternalStore` compares snapshots by reference and re-renders forever if a new one
// arrives every call, so the read is cached until something writes.
let cached: DisplayMode = DEFAULT_DISPLAY_MODE;
let hasRead = false;

function getSnapshot(): DisplayMode {
  if (!hasRead) {
    cached = read();
    hasRead = true;
  }
  return cached;
}

/** Server render has no `localStorage`, so it renders the default and hydrates into the stored one. */
function getServerSnapshot(): DisplayMode {
  return DEFAULT_DISPLAY_MODE;
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  return () => listeners.delete(onStoreChange);
}

/** Set the display mode for this browser. */
export function setDisplayMode(mode: DisplayMode): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, mode);
  cached = mode;
  hasRead = true;
  for (const listener of listeners) listener();
}

/** The current display mode, reactively. Re-renders every consumer on a change. */
export function useDisplayMode(): DisplayMode {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/**
 * Is `mode` at least as wide as `floor`?
 *
 * The one predicate every consumer should use, so that adding a fourth mode is an edit to
 * `DISPLAY_MODES` and not a sweep through every component's `===` comparisons.
 */
export function atLeast(mode: DisplayMode, floor: DisplayMode): boolean {
  return DISPLAY_MODES.indexOf(mode) >= DISPLAY_MODES.indexOf(floor);
}
