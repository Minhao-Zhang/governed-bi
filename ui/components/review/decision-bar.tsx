"use client";

/**
 * The four things a steward can do with an observation, and the sentence saying what none of them
 * does.
 *
 * **`decisionBoundary` is on the bar permanently, not in a tooltip.** A steward who believes a
 * button here changed the engine stops checking whether anything landed, and that belief is the one
 * failure this whole design is built to prevent. It is one sentence and it stays.
 *
 * **No optimistic state.** The server's transition table decides what is legal and answers 409 when
 * a move is not declared, so a bar that painted the new state first would show a state the store
 * refused. On the screen whose whole job is deciding, a wrong state is worse than a spinner.
 *
 * **Declining requires a reason and the requirement is the server's**, not this form's — the
 * validator refuses a `declined` row without one. The form asks for it because a 422 arriving after
 * the click is a worse way to learn it.
 *
 * **`MOVES` below is hand-maintained, and this comment used to claim it was not.** It said the
 * buttons come from the server's declared vocabulary, so a state added to `ObservationState` would
 * appear here. Nothing serves that vocabulary: `lifecycle.py` exports `allowed_next`, no route
 * exposes it, and `MOVES` is a literal. So a member added to the enum is a move no steward can make
 * until somebody edits this file, and nothing says so at the time.
 *
 * What would remove the second copy is a route that answers "what can this row do next" from the
 * transition table. Until then the list is a copy, named as one.
 */

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useTriageObservation } from "@/hooks/queries";
import { ApiError } from "@/lib/api-client";
import { DECLINE_COPY, REVIEW_COPY, STATE_COPY } from "@/lib/review-copy";
import type { ObservationState } from "@/lib/review-copy";
import type { Observation } from "@/lib/types";

/** Moves a steward makes from the queue, in the order they are reached. `duplicate` and
 *  `addressed` are absent on purpose: the first needs another row to point at and the second is
 *  set by drafting a patch, not by a button. */
const MOVES: { to: ObservationState; label: string; needsReason?: boolean }[] = [
  { to: "triaged", label: "I am looking at this" },
  { to: "declined", label: "Close without a change", needsReason: true },
  { to: "blocked_on_a_person", label: "Waiting on a person", needsReason: true },
];

export function DecisionBar({
  observation,
  onDraft,
}: {
  observation: Observation;
  onDraft: () => void;
}): React.JSX.Element {
  const triage = useTriageObservation();
  const [pending, setPending] = useState<ObservationState | null>(null);
  const [reason, setReason] = useState("");
  const [declineReason, setDeclineReason] = useState<string>("working_as_intended");

  const state = observation.state as ObservationState;
  const copy = STATE_COPY[state];
  const move = MOVES.find((m) => m.to === pending);

  async function commit(): Promise<void> {
    if (!move) return;
    await triage.mutateAsync({
      observationId: observation.observation_id,
      to: move.to,
      detail: reason,
      ...(move.to === "declined" ? { decline_reason: declineReason } : {}),
      ...(move.to === "blocked_on_a_person" ? { blocked_note: reason } : {}),
    });
    setPending(null);
    setReason("");
  }

  return (
    <div className="space-y-3 rounded-lg border bg-background/95 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={observation.open ? "default" : "outline"}>{copy?.label ?? state}</Badge>
        <span className="text-xs text-muted-foreground">{copy?.sentence}</span>
      </div>

      {!move && (
        <div className="flex flex-wrap gap-2">
          {MOVES.filter((m) => m.to !== state).map((m) => (
            <Button
              key={m.to}
              size="sm"
              variant="outline"
              onClick={() => setPending(m.to)}
              disabled={triage.isPending}
            >
              {m.label}
            </Button>
          ))}
          <Button size="sm" onClick={onDraft}>
            {REVIEW_COPY.draftHeading}
          </Button>
        </div>
      )}

      {move && (
        <div className="space-y-2">
          {move.to === "declined" && (
            <div className="space-y-1">
              <select
                className="w-full rounded-lg border bg-transparent px-2 py-1 text-sm"
                value={declineReason}
                onChange={(event) => setDeclineReason(event.target.value)}
              >
                {Object.keys(DECLINE_COPY).map((key) => (
                  <option key={key} value={key}>
                    {key}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">{DECLINE_COPY[declineReason]}</p>
            </div>
          )}
          {move.needsReason && (
            <>
              <Textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={REVIEW_COPY.reasonRequired}
                rows={2}
              />
              <p className="text-xs text-muted-foreground">{REVIEW_COPY.reasonRequired}</p>
            </>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={commit}
              disabled={triage.isPending || (move.needsReason === true && reason.trim() === "")}
            >
              {move.label}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setPending(null);
                setReason("");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {triage.isError && (
        <p className="text-xs text-destructive">
          {triage.error instanceof ApiError && triage.error.status === 409
            ? "The server refused that move: it is not one the transition table declares from this state."
            : triage.error instanceof ApiError && triage.error.status === 404
              ? "That verb is not mounted on this engine (GOVERNED_BI_FEEDBACK_ADMIN is unset), or the row is gone."
              : String(triage.error)}
        </p>
      )}

      <p className="text-xs text-muted-foreground">{REVIEW_COPY.decisionBoundary}</p>
    </div>
  );
}
