"use client";

/**
 * The reader's own entrance into the semantic layer (utku-ai-trust-loop-plan.md, task A-3).
 *
 * Renders directly under I-5's refusal sentence, and only for the one refusal reason its own
 * design decision is about: `no_schema_matched` -- the engine found no schema for the term the
 * reader used, which is exactly the shape of gap a reader can close in their own words. Every
 * other refusal reason is a bug, a safety boundary, or a structural limit, not a vocabulary gap
 * the reader's explanation would resolve -- so `answer-card.tsx` gates mounting this on
 * `refusedBy === "no_schema_matched"` before this component ever renders.
 *
 * One line of input, not a form: a single `Input` plus a `Send` button, submitting straight to
 * `api.fileClarificationFromRefusal` (mirroring `ClarificationCard`'s own local-state pattern,
 * not `useMutation` -- nothing here needs to invalidate a query, since the admin surfaces this
 * feeds are on pages a business reader cannot reach). Once it succeeds the input is replaced by
 * a short confirmation rather than a toast that vanishes: this sits inside a permanent answer
 * card, the same reasoning `elicitation-wizard.tsx`'s `ScanReportLine` already gives for staying
 * on screen rather than fading after a few seconds.
 */

import { useState } from "react";
import { Loader2, Send } from "lucide-react";

import { api, ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function RefusalClarificationPrompt({ question }: { question: string }) {
  const [explanation, setExplanation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (sent) {
    return (
      <p className="text-sm text-muted-foreground">
        Thanks — we&rsquo;ve noted what you meant. An admin will review it.
      </p>
    );
  }

  const trimmed = explanation.trim();

  async function submit() {
    if (submitting || trimmed.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.fileClarificationFromRefusal(question, trimmed);
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send that. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <p className="text-sm">Tell us what you meant, and we&rsquo;ll remember it next time.</p>
      <div className="flex gap-2">
        <Input
          value={explanation}
          onChange={(e) => setExplanation(e.target.value)}
          placeholder='e.g. "By popular I mean the highest download count"'
          disabled={submitting}
          aria-label="What you meant"
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
          }}
        />
        <Button
          type="button"
          size="sm"
          disabled={submitting || trimmed.length === 0}
          onClick={() => void submit()}
        >
          {submitting ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Send className="size-3.5" />
          )}
          {submitting ? "Sending" : "Send"}
        </Button>
      </div>
      {error && <p className="text-xs text-tier-refused">{error}</p>}
    </div>
  );
}
