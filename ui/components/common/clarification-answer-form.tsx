"use client";

/**
 * Shared choice-toggles + freeform-input answer UI, used by both clarification
 * surfaces: the curator-time admin queue (`components/corpus/clarifications-
 * panel.tsx`, round 2) and the serve-time chat interrupt prompt
 * (`components/chat/clarification-prompt.tsx`, round 4). Both answer the same
 * shape — pick a `choices` entry and/or, when `allow_freeform`, type free text —
 * so the form itself is transport-neutral: callers own fetching the record and
 * submitting the answer, this owns only the pick/type/submit interaction.
 */

import { useState } from "react";
import { Check, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ClarificationChoice } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface ClarificationAnswer {
  choiceId?: string;
  answer?: string;
}

export function ClarificationAnswerForm({
  choices,
  allowFreeform,
  disabled,
  submitting,
  submitLabel = "Submit answer",
  freeformAriaLabel,
  freeformPlaceholder,
  inputType = "text",
  onSubmit,
}: {
  choices?: ClarificationChoice[] | null;
  allowFreeform: boolean;
  /** Answering is unavailable (e.g. missing capability) — disables all controls. */
  disabled?: boolean;
  submitting?: boolean;
  submitLabel?: string;
  freeformAriaLabel?: string;
  freeformPlaceholder?: string;
  /** Elicitation wizard category C ("required numeric field") passes "number";
   * every pre-existing caller keeps the default plain-text input. */
  inputType?: "text" | "number";
  onSubmit: (answer: ClarificationAnswer) => void;
}) {
  const [choiceId, setChoiceId] = useState<string | null>(null);
  const [freeform, setFreeform] = useState("");

  const trimmed = freeform.trim();
  const canSubmit = choiceId !== null || trimmed.length > 0;
  const hasChoices = !!choices && choices.length > 0;

  return (
    <div className="space-y-4">
      {hasChoices && (
        <div className="space-y-1.5">
          <span className="text-xs font-medium text-muted-foreground">Pick one</span>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Choices">
            {choices!.map((choice) => (
              <ChoiceToggle
                key={choice.id}
                active={choiceId === choice.id}
                disabled={!!disabled || !!submitting}
                onClick={() => setChoiceId(choiceId === choice.id ? null : choice.id)}
              >
                {choice.label}
              </ChoiceToggle>
            ))}
          </div>
        </div>
      )}

      {allowFreeform && (
        <label className="block space-y-1">
          <span className="text-xs font-medium text-muted-foreground">
            {hasChoices ? "Or answer in your own words" : "Answer"}
          </span>
          <Input
            type={inputType}
            value={freeform}
            onChange={(e) => setFreeform(e.target.value)}
            placeholder={freeformPlaceholder ?? "Type an answer…"}
            disabled={!!disabled || !!submitting}
            aria-label={freeformAriaLabel}
          />
        </label>
      )}

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!!disabled || !!submitting || !canSubmit}
          onClick={() =>
            onSubmit({
              choiceId: choiceId ?? undefined,
              answer: trimmed.length > 0 ? trimmed : undefined,
            })
          }
        >
          {submitting ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              Submitting
            </>
          ) : (
            submitLabel
          )}
        </Button>
      </div>
    </div>
  );
}

/** Radio-like single-select toggle for one `choices` entry. */
function ChoiceToggle({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={active ? "secondary" : "outline"}
      role="radio"
      aria-checked={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "gap-1.5 transition-colors",
        active ? "ring-1 ring-ring/50" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {active && <Check className="size-3.5" />}
      {children}
    </Button>
  );
}
