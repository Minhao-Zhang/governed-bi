"use client";

/**
 * Which conversation is on screen, kept in the URL (`?thread=<id>`) so a conversation is
 * linkable and a bare visit to `/` starts fresh rather than reopening yesterday's.
 *
 * `useSyncExternalStore`, because the URL is an external store: mirroring it into state would
 * be a cascading render and a hydration mismatch. Writes use `history.replaceState`, not
 * `router.replace`, because a Next navigation during a run could disturb the open stream; it
 * fires no `popstate`, so writes announce themselves on a private event. Nothing pushes a
 * history entry, so the back button leaves the page.
 */

import { useCallback, useSyncExternalStore } from "react";

const PARAM = "thread";

/** Broadcast for our own writes: `replaceState` does not fire `popstate`. */
const CHANGED = "governed-bi:thread-changed";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

/** Reads a string, so `useSyncExternalStore`'s identity check compares by value. */
function getSnapshot(): string | null {
  const value = new URLSearchParams(window.location.search).get(PARAM);
  return value && value.trim() !== "" ? value : null;
}

function getServerSnapshot(): string | null {
  return null;
}

export function useThreadId(): {
  threadId: string | null;
  /** Open a conversation, or `null` to start a new one. */
  setThreadId: (threadId: string | null) => void;
} {
  const threadId = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setThreadId = useCallback((next: string | null) => {
    const url = new URL(window.location.href);
    if (next) url.searchParams.set(PARAM, next);
    else url.searchParams.delete(PARAM);
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new Event(CHANGED));
  }, []);

  return { threadId, setThreadId };
}
