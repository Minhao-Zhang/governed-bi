import { PageShell } from "@/components/layout/page-shell";
import { AuditSurface } from "@/components/audit/audit-surface";

/**
 * `/audit` — what this server did, turn by turn. A Server Component shell around the
 * interactive <AuditSurface> (which fetches via React Query on the client). Static
 * route, no params.
 *
 * Everything on it is about a **run**: the governance ledger, the licensed set, and every
 * recorded field grouped by the pipeline stage that produced it. Corpus state used to be the
 * first section and now lives on `/corpus`, where the rest of the corpus already was — it
 * described the corpus, not a run, and `/health` was a third surface saying the same thing.
 */
export default function AuditPage() {
  return (
    <PageShell
      title="Audit"
      description="Every turn this server has served, and one turn's record stage by stage."
      // The turn list and the selected turn's trace each scroll inside their own box, so
      // picking a row never moves the row you picked.
      fill
    >
      <AuditSurface />
    </PageShell>
  );
}
