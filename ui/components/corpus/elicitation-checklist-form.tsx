"use client";

/**
 * Multi-select checklist input for the Phase 1 elicitation wizard's category B
 * (value mapping NL<->DB — "check all the real DB values that count as X").
 * The shared `ClarificationAnswerForm` (components/common/clarification-answer-
 * form.tsx) is single-choice (radiogroup) by design, reused for A/C/E — too
 * specific to extend cleanly for multi-select without changing its answer
 * shape, so this is a small sibling component instead, following the same
 * pick/submit interaction.
 */

import { useState } from "react";
import { Check, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ClarificationChoice } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function ElicitationChecklistForm({
  choices,
  disabled,
  submitting,
  submitLabel = "Submit answer",
  onSubmit,
}: {
  choices: ClarificationChoice[];
  disabled?: boolean;
  submitting?: boolean;
  submitLabel?: string;
  /** Every elicitation question accepts both input modes — the checklist AND
   * a freeform fallback for anything the picker doesn't cover — so this fires
   * with whichever one the admin actually used. */
  onSubmit: (answer: { choiceIds?: string[]; answer?: string }) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [freeform, setFreeform] = useState("");

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const trimmed = freeform.trim();
  const canSubmit = selected.size > 0 || trimmed.length > 0;

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <span className="text-xs font-medium text-muted-foreground">Check all that apply</span>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Values">
          {choices.map((choice) => {
            const active = selected.has(choice.id);
            return (
              <Button
                key={choice.id}
                type="button"
                size="sm"
                variant={active ? "secondary" : "outline"}
                role="checkbox"
                aria-checked={active}
                disabled={!!disabled || !!submitting}
                onClick={() => toggle(choice.id)}
                className={cn(
                  "gap-1.5 transition-colors",
                  active ? "ring-1 ring-ring/50" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {active && <Check className="size-3.5" />}
                {choice.label}
              </Button>
            );
          })}
        </div>
      </div>

      <label className="block space-y-1">
        {/* Not "Or". Both checklist questions now ask for the picks *and* a sentence — B wants
            the word you call a group of values, and the describe-columns question wants what the
            columns you checked actually hold — and the backend composes whichever arrive
            (`curator/elicitation_answers.py`). "Or" told the admin to supply half an answer. */}
        <span className="text-xs font-medium text-muted-foreground">In your own words</span>
        <Input
          type="text"
          value={freeform}
          onChange={(e) => setFreeform(e.target.value)}
          placeholder="Type an answer…"
          disabled={!!disabled || !!submitting}
        />
      </label>

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!!disabled || !!submitting || !canSubmit}
          onClick={() =>
            onSubmit({
              choiceIds: selected.size > 0 ? Array.from(selected) : undefined,
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
