"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";

/** Lets an answer put text in the composer without owning it. */
export interface ComposerDraft {
  setDraft: (text: string) => void;
}

interface DraftControl {
  text: string;
  setText: (text: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}

export const ComposerDraftContext = createContext<ComposerDraft>({ setDraft: () => {} });

const DraftControlContext = createContext<DraftControl | null>(null);

export function useComposerDraft(): ComposerDraft {
  return useContext(ComposerDraftContext);
}

/** The composer reads this. Other callers only need {@link useComposerDraft}. */
export function useComposerDraftControl(): DraftControl | null {
  return useContext(DraftControlContext);
}

/**
 * Holds the composer text for one column. `setDraft` replaces that text and
 * focuses the field, so an example or a nearby question can fill it.
 */
export function ComposerDraftProvider({ children }: { children: ReactNode }) {
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const [focusTick, setFocusTick] = useState(0);

  const setDraft = useCallback((next: string) => {
    setText(next);
    setFocusTick((tick) => tick + 1);
    const field = inputRef.current;
    if (field && !field.disabled) field.focus();
  }, []);

  useEffect(() => {
    if (focusTick === 0) return;
    const field = inputRef.current;
    if (!field || field.disabled) return;
    field.focus();
    const end = field.value.length;
    field.setSelectionRange(end, end);
  }, [focusTick]);

  return (
    <ComposerDraftContext.Provider value={{ setDraft }}>
      <DraftControlContext.Provider value={{ text, setText, inputRef }}>
        {children}
      </DraftControlContext.Provider>
    </ComposerDraftContext.Provider>
  );
}
