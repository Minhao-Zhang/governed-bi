"use client";

/**
 * Thin corpus edit sheet (handoff §7). Opens when `can_edit`; submits the raw
 * asset mapping to `POST /corpus/edit` and surfaces validation findings + the
 * unified YAML diff. Does not own SME clarification / accept_answer.
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type { AssetRow, EditResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

/** Seed a minimal editable JSON payload from the list-row fields. */
function seedAssetJson(row: AssetRow): string {
  return JSON.stringify(
    {
      id: row.id,
      asset_type: row.asset_type,
      // List view only has a summary — expand to a full on-disk shape before write.
      summary: row.summary,
      excluded: row.excluded,
    },
    null,
    2,
  );
}

export function AssetEditSheet({
  row,
  open,
  onOpenChange,
}: {
  row: AssetRow | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Edit corpus asset</SheetTitle>
          <SheetDescription>
            {row
              ? `Validate and write ${row.asset_type} \`${row.id}\`. Expand the JSON to the on-disk YAML shape before submitting.`
              : "Select an asset to edit."}
          </SheetDescription>
        </SheetHeader>

        {/* Remount on asset change so draft/result reset without an effect. */}
        {row ? (
          <AssetEditForm key={`${row.asset_type}:${row.id}`} row={row} onClose={() => onOpenChange(false)} />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function AssetEditForm({ row, onClose }: { row: AssetRow; onClose: () => void }) {
  const [draft, setDraft] = useState(() => seedAssetJson(row));
  const [parseError, setParseError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<EditResponse | null>(null);

  async function submit() {
    if (submitting) return;
    let asset: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(draft);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setParseError("Asset must be a JSON object.");
        return;
      }
      asset = parsed as Record<string, unknown>;
      setParseError(null);
    } catch {
      setParseError("Invalid JSON — fix the draft before submitting.");
      return;
    }

    setSubmitting(true);
    setResult(null);
    try {
      const response = await api.edit(asset);
      setResult(response);
      if (response.written) {
        toast.success(`Wrote ${response.asset_id}`);
      } else {
        toast.message("Not written", {
          description:
            response.findings[0] ?? "Validation blocked the write — see findings.",
        });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Edit request failed";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="flex flex-1 flex-col gap-3 px-4 pb-4">
        <label className="space-y-1.5">
          <span className="text-xs font-medium text-muted-foreground">Asset JSON</span>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            rows={14}
            disabled={submitting}
            aria-label="Asset JSON"
            className={cn(
              "w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-2 font-mono text-xs outline-none transition-colors",
              "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          />
        </label>

        {parseError && (
          <p className="text-sm text-tier-refused" role="alert">
            {parseError}
          </p>
        )}

        {result && (
          <div className="space-y-3 rounded-lg border p-3">
            <p className="text-sm">
              {result.written ? (
                <span className="text-tier-governed">
                  Written{result.path ? ` → ${result.path}` : ""}.
                </span>
              ) : (
                <span className="text-tier-lineage">Not written.</span>
              )}
            </p>

            {result.findings.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">Findings</p>
                <ul className="list-inside list-disc space-y-0.5 text-xs text-tier-refused">
                  {result.findings.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              </div>
            )}

            {result.diff ? (
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">Diff</p>
                <pre className="max-h-48 overflow-auto rounded-md border bg-muted/40 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
                  {result.diff}
                </pre>
              </div>
            ) : null}
          </div>
        )}
      </div>

      <SheetFooter className="border-t">
        <Button type="button" variant="outline" onClick={onClose}>
          Close
        </Button>
        <Button type="button" onClick={() => void submit()} disabled={submitting}>
          {submitting ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              Submitting
            </>
          ) : (
            "Validate & write"
          )}
        </Button>
      </SheetFooter>
    </>
  );
}
