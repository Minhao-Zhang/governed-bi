"use client";

/**
 * Task C's own view: does the loop -- refusal/wrong-answer → reader entrance → approved rule →
 * retrieved again -- actually turn, and where does it stop (`GET /trust-loop/metrics`).
 *
 * **The funnel is the headline, and it is four numbers in a row on purpose.** `43 / 2 / 2 / 2`
 * reads as "the loop turned, twice, out of 43 refusals" without arithmetic; `43 / 40 / 38 / 35`
 * would read as a healthy pipeline. Rendering four separate cards with no shared axis is how a
 * reader ends up doing that subtraction themselves, which is the exact failure this task exists
 * to prevent -- see `api/trust_loop_routes.py::make_trust_loop_metrics_router`'s own docstring.
 *
 * **`null` renders as "not measured", never as a dash standing in for zero.** A session with no
 * `corpus_root` cannot read a ledger at all, and this component says so in words rather than
 * leaving a blank a reader might read as "checked, and found nothing."
 *
 * Admin/engineer only -- gated at the render site (`/corpus/page.tsx`) on
 * `tierShowsTrustLoopMetrics`, never an inline tier comparison here.
 */

import type { TrustLoopMetrics } from "@/lib/types";
import { useTrustLoopMetrics } from "@/hooks/queries";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

const FUNNEL_LABELS = ["Refusals", "Reader entrances", "Approved rules", "Retrieved again"] as const;

export function TrustLoopMetricsView() {
  const metrics = useTrustLoopMetrics();

  return (
    <QueryState query={metrics} isEmpty={() => false}>
      {(data) => (
        <div className="space-y-4">
          <Funnel data={data} />
          <div className="grid gap-3 sm:grid-cols-3">
            <RefusalsCard data={data} />
            <EntrancesCard data={data} />
            <ApprovedRulesCard data={data} />
          </div>
          <RetrievedCard data={data} />
          {data.notes.length > 0 && (
            <ul className="space-y-1 text-xs text-muted-foreground">
              {data.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </QueryState>
  );
}

/** The four-number drop-off, in one row so the ratio between adjacent steps is the thing a
 * reader sees first -- never four unrelated stat tiles. */
function Funnel({ data }: { data: TrustLoopMetrics }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Does the loop turn?</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-stretch gap-2 overflow-x-auto">
          {data.funnel.map((n, i) => (
            <div key={FUNNEL_LABELS[i]} className="flex items-center gap-2">
              <div className="flex min-w-24 flex-col items-center gap-1 rounded-lg border px-3 py-2">
                <span className="text-2xl font-semibold tabular-nums">
                  {n === null ? "—" : n.toLocaleString()}
                </span>
                <span className="text-center text-xs text-muted-foreground">
                  {FUNNEL_LABELS[i]}
                </span>
              </div>
              {i < data.funnel.length - 1 && (
                <span className="text-muted-foreground" aria-hidden>
                  →
                </span>
              )}
            </div>
          ))}
        </div>
        {data.funnel.slice(1).some((n) => n === null) && (
          <p className="mt-2 text-xs text-muted-foreground">
            “—” means not measured (no corpus attached to read a ledger from), not zero.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function RefusalsCard({ data }: { data: TrustLoopMetrics }) {
  const { refusals } = data;
  const reasons = Object.entries(refusals.by_reason).sort(([, a], [, b]) => b - a);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Refusals, by reason</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {reasons.length === 0 ? (
          <p className="text-xs text-muted-foreground">No refusals in the scanned turns.</p>
        ) : (
          <NumberList rows={reasons} />
        )}
        <ScanFootnote
          scanned={refusals.turns_scanned}
          bound={refusals.scan_bound}
          truncated={refusals.possibly_truncated}
          unit="turn"
        />
      </CardContent>
    </Card>
  );
}

function EntrancesCard({ data }: { data: TrustLoopMetrics }) {
  const { entrances } = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Became reader entrances</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {entrances === null ? (
          <NotMeasured reason="no corpus_root" />
        ) : (
          <NumberList
            rows={[
              ["Refusal → clarification (task A)", entrances.refusal_clarifications],
              ["Wrong-answer report (task H)", entrances.reports],
            ]}
          />
        )}
      </CardContent>
    </Card>
  );
}

function ApprovedRulesCard({ data }: { data: TrustLoopMetrics }) {
  const { approved_rules: approved } = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Became approved rules</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {approved === null ? (
          <NotMeasured reason="no corpus_root" />
        ) : Object.keys(approved.by_source).length === 0 ? (
          <p className="text-xs text-muted-foreground">No certified reader-channel asset yet.</p>
        ) : (
          <>
            <NumberList rows={Object.entries(approved.by_source).sort(([, a], [, b]) => b - a)} />
            <Separator />
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-muted-foreground">reader-initiated (refusal + feedback)</span>
              <Badge variant="secondary" className="tabular-nums">
                {approved.reader_initiated_total}
              </Badge>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RetrievedCard({ data }: { data: TrustLoopMetrics }) {
  const { retrieved } = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Retrieved again, on a later turn</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {retrieved === null ? (
          <NotMeasured reason="no corpus_root" />
        ) : (
          <>
            <p className="text-sm">
              <span className="font-semibold tabular-nums">{retrieved.n_retrieved}</span>{" "}
              <span className="text-muted-foreground">
                of the certified reader-initiated rule(s) appeared as a retrieval candidate on at
                least one scanned turn.
              </span>
            </p>
            <p className="text-xs text-muted-foreground">{retrieved.method}</p>
            <ScanFootnote
              scanned={retrieved.turns_scanned}
              bound={retrieved.scan_bound}
              truncated={retrieved.possibly_truncated}
              unit="turn"
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function NotMeasured({ reason }: { reason: string }) {
  return <p className="text-xs text-muted-foreground">Not measured ({reason}).</p>;
}

/** A labelled column of `label … number`, right-aligned and `tabular-nums` so the numbers are
 * comparable down the column -- the only reason to list them together (mirrors
 * `corpus-status.tsx`'s own `Numbers` helper; not shared because that one is module-private
 * there and this shape needed no third caller to justify exporting it). */
function NumberList({ rows }: { rows: Array<[string, number]> }) {
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
      {rows.map(([label, n]) => (
        <div key={label} className="col-span-2 grid grid-cols-subgrid">
          <dt className="truncate text-muted-foreground" title={label}>
            {label}
          </dt>
          <dd className="tabular-nums">{n.toLocaleString()}</dd>
        </div>
      ))}
    </dl>
  );
}

/** How much of the log this counter actually looked at -- rendered every time a bound applies,
 * never only when truncation happened, so a bounded-but-complete scan is still visibly bounded
 * (the plan's own instruction: "if you bound the scan, log it in the output"). */
function ScanFootnote({
  scanned,
  bound,
  truncated,
  unit,
}: {
  scanned: number;
  bound: number;
  truncated: boolean;
  unit: string;
}) {
  return (
    <p className="text-[11px] text-muted-foreground">
      Scanned {scanned.toLocaleString()} {unit}
      {scanned === 1 ? "" : "s"} (bound {bound.toLocaleString()})
      {truncated ? " — more may exist past this bound." : "."}
    </p>
  );
}
