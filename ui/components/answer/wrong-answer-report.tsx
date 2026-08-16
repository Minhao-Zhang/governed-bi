"use client";

/**
 * The reader's other entrance into the semantic layer (utku-ai-trust-loop-plan.md, task H-3) --
 * distinct from `RefusalClarificationPrompt`, which fires when the engine said nothing back.
 * This one fires when the engine *did* answer and the reader, who knows the business, knows the
 * answer is wrong -- H-a's own argument for why this control exists at all: "this answer is
 * wrong" is the one judgement a business reader is uniquely qualified to make.
 *
 * **Quiet by design.** H-3 asks for "not a prominent button — the default assumption should be
 * that the answer is fine", so this starts as a single line of muted underline-on-hover text,
 * the same visual register `sql-block.tsx`'s and `provenance-drawer.tsx`'s low-emphasis controls
 * use, not a colored banner competing with the answer above it. Clicking it reveals one input
 * (the reader's optional one-line reason) and a submit action; submitting replaces the whole
 * control with a short confirmation -- mirroring `RefusalClarificationPrompt`'s own local-state
 * pattern for the same reason: nothing here needs to invalidate a query, since the admin queue
 * this feeds (`components/corpus/feedback-panel.tsx`) lives on a page a business reader cannot
 * reach.
 *
 * **Not shown on a refusal.** `answer-card.tsx` gates mounting this on `delivery !== "refused"`:
 * a refusal has no answer to be wrong about, and a reader who was refused already has the
 * `no_schema_matched` entrance A built (`RefusalClarificationPrompt`, right above the refusal
 * text on that branch of the same card). Filing a report against a refused turn would be a
 * report with an empty `answer_text`, which is not the thing this control exists to catch, and
 * `POST /feedback` requires a non-empty `answer_text` server-side for exactly this reason.
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { api, ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function WrongAnswerReport({
  turnId,
  question,
  answerText,
}: {
  turnId: string;
  question: string;
  answerText: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (sent) {
    return <p className="text-xs text-muted-foreground">Thanks — an admin will see this.</p>;
  }

  async function submit() {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.fileFeedback({ turnId, question, answerText, reason: reason.trim() || undefined });
      setSent(true);
    } catch (err) {
      // Logged, never rendered -- same reasoning as `RefusalClarificationPrompt`'s identical
      // choice: `ApiError.message` is engine/HTTP vocabulary, fine for the admin-facing surfaces
      // that render it directly, wrong here, where this control is business-tier-only by
      // construction.
      if (err instanceof ApiError) console.error("fileFeedback failed:", err.message);
      setError("Could not send that. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!expanded) {
    return (
      <Button
        type="button"
        variant="link"
        size="xs"
        className="h-auto p-0 text-muted-foreground"
        onClick={() => setExpanded(true)}
      >
        This isn&rsquo;t right
      </Button>
    );
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs text-muted-foreground">What&rsquo;s wrong, in a line? (optional)</p>
      <div className="flex gap-2">
        <Input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. &quot;That's not how we count active customers&quot;"
          disabled={submitting}
          aria-label="What's wrong"
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
          }}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={submitting}
          onClick={() => void submit()}
        >
          {submitting ? <Loader2 className="size-3.5 animate-spin" /> : "Report"}
        </Button>
      </div>
      {error && <p className="text-xs text-tier-refused">{error}</p>}
    </div>
  );
}
