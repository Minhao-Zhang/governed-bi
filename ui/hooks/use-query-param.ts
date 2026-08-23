"use client";

/**
 * One URL query parameter, read and written as state.
 *
 * `useThreadId` is this hook with `PARAM` hardcoded, and it came first. Its docstring carries the
 * whole argument and is worth reading before changing anything here; the short version is that the
 * URL genuinely is an external store, so `useSyncExternalStore` is both the idiomatic and the
 * cheaper shape — mirroring it into `useState` and syncing in an effect is a cascading render and a
 * hydration mismatch, and the lint rule refuses it.
 *
 * **Generalised rather than copied**, because `/review` needs the same thing for `?cluster=`: a
 * steward's whole job on that screen is handing a decision to somebody else, and "look at this" has
 * to be a link. `/audit` gets away with `useState` because nobody links to a trace.
 *
 * `useThreadId` is deliberately **not** rewritten in terms of this. It owns a private event name
 * that the chat surface's `onThreadId` writes during a live run, and folding two subscribers onto
 * one event would make an unrelated `?cluster=` write re-render the component holding the open
 * stream. Two hooks, two events, one shape.
 */

import { useCallback, useSyncExternalStore } from "react";

/** Broadcast for our own writes: `replaceState` does not fire `popstate`. */
const CHANGED = "governed-bi:query-param-changed";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

export function useQueryParam(name: string): {
  value: string | null;
  setValue: (next: string | null) => void;
} {
  // Reads a string, so `useSyncExternalStore`'s identity check compares by value rather than by
  // reference — the reason `useThreadId` returns a string too instead of a parsed object.
  const getSnapshot = useCallback((): string | null => {
    const raw = new URLSearchParams(window.location.search).get(name);
    return raw && raw.trim() !== "" ? raw : null;
  }, [name]);

  const getServerSnapshot = useCallback((): string | null => null, []);

  const value = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setValue = useCallback(
    (next: string | null) => {
      const url = new URL(window.location.href);
      if (next) url.searchParams.set(name, next);
      else url.searchParams.delete(name);
      // `replaceState` and not `router.replace`: a Next navigation re-renders the route, and
      // selecting a row must not remount the panel that is about to show its evidence. Nothing
      // pushes a history entry either, so the back button leaves the page rather than stepping
      // through selections — a half-working back button is worse than the ordinary one.
      window.history.replaceState(null, "", url);
      window.dispatchEvent(new Event(CHANGED));
    },
    [name],
  );

  return { value, setValue };
}
