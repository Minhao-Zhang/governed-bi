import { PageShell } from "@/components/layout/page-shell";
import { ReviewSurface } from "@/components/review/review-surface";
import { REVIEW_COPY } from "@/lib/review-copy";

/**
 * `/review` — failures somebody flagged, grouped by what looks like the same problem (ADR 0015).
 *
 * **Its own route rather than a section of `/audit`**, for `pending-queue.tsx`'s own stated reason
 * applied one turn further: `/audit` is newest-first and every turn, this is oldest-first and only
 * what somebody flagged, and putting both scroll directions on one screen makes each worse.
 *
 * The description is the product boundary in one sentence and it stays on the page permanently:
 * deciding here **drafts** a change to the semantic layer, and does not apply one. The only write to
 * corpus content anywhere in this loop is a human's `git commit` in the corpus repository.
 */
export default function ReviewPage() {
  return (
    <PageShell title={REVIEW_COPY.pageTitle} description={REVIEW_COPY.pageDescription} fill>
      <ReviewSurface />
    </PageShell>
  );
}
