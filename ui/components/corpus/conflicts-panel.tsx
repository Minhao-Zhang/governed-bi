"use client";

/**
 * Admin "Needs Review" queue (Round C). A clarification whose Enhancer
 * decision flagged `conflict_with` an existing NoteAsset/MetricAsset (see
 * `AssetBag._record_conflict` / `GET /corpus/conflicts`) — a DISAGREEING
 * definition, not a settled one. Deliberately distinct from the calm
 * `AssumptionsLog`: a destructive/amber-flavored card, an explicit "these
 * disagree" framing, and a two-button resolution action wired to
 * `POST /corpus/conflicts/{id}/resolve`.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api-client";
import { canCurateCorpus } from "@/lib/capabilities";
import type { ConflictRow } from "@/lib/types";
import { useCapabilities, useConflicts } from "@/hooks/queries";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function ConflictsPanel() {
  const conflicts = useConflicts("unresolved");
  const { data: caps } = useCapabilities();
  // Phase 5 fix: POST /corpus/conflicts/{id}/resolve is not gated on can_edit on the
  // backend (mirrors /corpus/drafts/{id}/approve's pattern exactly -- can_edit is a
  // hard-coded /capabilities report for the unrelated free-form corpus editor, not a
  // gate this route ever checks). Gating the resolve buttons on can_edit here made them
  // unreachable in every deployment, since can_edit is hard-coded false in v2 today.
  // can_curate_corpus mirrors this route's actual precondition (session.corpus_root).
  const editable = canCurateCorpus(caps);

  return (
    <QueryState
      query={conflicts}
      isEmpty={(data) => data.length === 0}
      emptyMessage="No unresolved conflicts — every answered clarification agrees with the corpus."
    >
      {(data) => (
        <div className="space-y-3">
          {data.map((row) => (
            <ConflictCard key={row.id} row={row} editable={editable} />
          ))}
        </div>
      )}
    </QueryState>
  );
}

function ConflictCard({ row, editable }: { row: ConflictRow; editable: boolean }) {
  const queryClient = useQueryClient();
  const [resolving, setResolving] = useState<"keep_existing" | "replace" | null>(null);

  async function resolve(resolution: "keep_existing" | "replace") {
    if (resolving) return;
    setResolving(resolution);
    try {
      await api.resolveConflict(row.id, resolution);
      toast.success(
        resolution === "keep_existing"
          ? `Kept the existing definition for ${row.existing_asset_id}`
          : `Replaced ${row.existing_asset_id} with the new answer`,
      );
      await queryClient.invalidateQueries({ queryKey: ["conflicts"] });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to resolve the conflict.";
      toast.error(message);
    } finally {
      setResolving(null);
    }
  }

  return (
    <Card className="border-destructive/40 bg-destructive/5">
      <CardHeader>
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
          <CardTitle className="flex-1 text-sm leading-snug font-medium">
            These two answers disagree
          </CardTitle>
          <Badge variant="destructive">Needs review</Badge>
          <Badge variant="outline" className="text-muted-foreground">
            {row.id}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <DefinitionSide
            label={`Existing (${row.existing_asset_type})`}
            question={row.existing_question}
            text={row.existing_text}
            assetId={row.existing_asset_id}
          />
          <DefinitionSide label="New, conflicting answer" question={row.new_question} text={row.new_text} />
        </div>
        {editable ? (
          <div className="flex items-center gap-2 pt-1">
            <Button
              size="sm"
              variant="outline"
              disabled={resolving !== null}
              onClick={() => void resolve("keep_existing")}
            >
              Keep existing
            </Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={resolving !== null}
              onClick={() => void resolve("replace")}
            >
              Replace with new answer
            </Button>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Resolving requires a connected dev backend (`capabilities.can_edit`).
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function DefinitionSide({
  label,
  question,
  text,
  assetId,
}: {
  label: string;
  question: string | null;
  text: string;
  assetId?: string;
}) {
  return (
    <div className="space-y-1 rounded-md border border-border bg-background p-3">
      <p className="text-xs font-semibold text-muted-foreground uppercase">{label}</p>
      {question && <p className="text-xs text-muted-foreground italic">&ldquo;{question}&rdquo;</p>}
      <p className="text-sm">{text}</p>
      {assetId && <p className="font-mono text-xs text-muted-foreground">{assetId}</p>}
    </div>
  );
}
