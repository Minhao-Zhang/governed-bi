"use client";

/**
 * Which conversation is on screen, kept in the URL.
 *
 * The URL and not `localStorage`, because it makes a conversation **linkable**: a turn that went
 * wrong can be handed to someone else as an address, which is the same reason `/audit` keys its
 * trace panel on a turn id. `localStorage` would also silently reopen yesterday's conversation
 * on a bare visit to `/`, and "I wanted a fresh start and got old context" is a worse surprise
 * than one click on a list.
 *
 * **`useSyncExternalStore`, because the URL genuinely is an external store.** The first draft
 * mirrored it into `useState` and synced on mount with an effect; the lint rule refused that,
 * correctly — a `setState` in an effect body is a cascading render, and it also renders `null`
 * on the server and a thread id in the browser, which is a hydration mismatch. Subscribing is
 * both the idiomatic and the cheaper shape: `getServerSnapshot` returns `null`, React reconciles
 * after hydration, and no component holds a stale copy of a value the address bar owns.
 *
 * **`history.replaceState`, not `router.replace`.** A Next navigation re-renders the route, and
 * this state changes *during a run* — `onThreadId` fires as the first turn creates its thread —
 * so a router call there risks disturbing the component that owns the open stream. The native
 * call updates the address bar and touches nothing else; Next has supported it since 14.1. It
 * does not fire `popstate`, so writes announce themselves on a private event instead.
 *
 * Nothing here *pushes* a history entry, so the back button leaves the page rather than stepping
 * between conversations. That is deliberate: the list is how you switch, and a half-working
 * back button is worse than one that does the ordinary thing. `popstate` is still subscribed to,
 * so if an entry is ever pushed this stays correct rather than going stale.
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
