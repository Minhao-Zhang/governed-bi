"use client";

/**
 * Draft a one-field change, and see what happens to it after that.
 *
 * **The form asks for `was` and will not guess it.** `corpus/patch.py` refuses the edit if the
 * field no longer holds what the patch says it held, so the current text is part of the patch
 * rather than something read at apply time. Pasting it here is the price of that refusal being
 * possible, and the refusal is what stops an edit landing on top of somebody else's.
 *
 * **The corpus hash must be the full 64 characters.** Every display in this app shows a 16-character
 * prefix, and a prefix in this field is refused with 422 — it never equals the digest the landing
 * check compares against, so a patch nobody had touched would report `superseded`. The field says
 * so, because the value a person has to hand is exactly the wrong one.
 *
 * **A replacement that changes no words cannot be submitted.** `classifyEdit` decides that, and
 * it is the same call the diff below the form renders its caption from — a second comparison here
 * would be a second definition of "a word", and the two drifting is how the form came to enable a
 * submit under a diff reading "+0 −0 words".
 *
 * **What this panel cannot do, and says so:** export a bundle. That is `tools/export_bundle.py`,
 * run by a person, and it is where two content checks are fatal — an excluded column named in the
 * new text, and five consecutive words quoted from a held-out question. A button here that skipped
 * them would be a button that contaminates the benchmark.
 */

import { useState } from "react";

import { AssetDiff } from "@/components/review/asset-diff";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useDraftPatch, useWithdrawPatch } from "@/hooks/queries";
import { ApiError } from "@/lib/api-client";
import { classifyEdit } from "@/lib/asset-diff";
import { HASH_CHARS, patchHashProblem } from "@/lib/patch-fields";
import { REVIEW_COPY } from "@/lib/review-copy";
import type { Observation } from "@/lib/types";


/** A patch as it arrives on an observation's detail: opaque on the wire, so the fields this panel
 *  reads are narrowed here rather than trusted from a type. */
interface PatchRow {
  patch_id?: unknown;
  state?: unknown;
  asset_id?: unknown;
  field_path?: unknown;
  was?: unknown;
  becomes?: unknown;
  ladder?: unknown;
  derived_state?: unknown;
  withdrawn_reason?: unknown;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function HandoffPanel({
  observation,
  open,
  onClose,
}: {
  observation: Observation;
  open: boolean;
  onClose: () => void;
}): React.JSX.Element {
  const draft = useDraftPatch();
  const withdraw = useWithdrawPatch();

  const [assetId, setAssetId] = useState(observation.missing_tables[0] ?? "");
  const [fieldPath, setFieldPath] = useState("summary");
  const [was, setWas] = useState("");
  const [becomes, setBecomes] = useState("");
  const [rationale, setRationale] = useState("");
  const [hash, setHash] = useState("");

  const patches = (observation.patches ?? []) as PatchRow[];
  const live = patches.filter((p) => str(p.state) !== "withdrawn");

  const hashProblem = patchHashProblem(hash);
  const canSubmit =
    assetId.trim() !== "" &&
    was.trim() !== "" &&
    becomes.trim() !== "" &&
    classifyEdit(was, becomes) === "words_changed" &&
    hash !== "" &&
    hashProblem === null &&
    !draft.isPending;

  async function submit(): Promise<void> {
    await draft.mutateAsync({
      intent: "edit_asset",
      namespace: observation.db_id ?? observation.schemas[0] ?? "",
      asset_type: "table",
      asset_id: assetId.trim(),
      field_path: fieldPath,
      was,
      becomes,
      rationale,
      base_corpus_content_hash: hash,
      observations: [observation.observation_id],
    });
    setWas("");
    setBecomes("");
    onClose();
  }

  return (
    <div className="space-y-4">
      {live.length > 0 && (
        <Card className="space-y-3 p-4">
          <h3 className="text-sm font-medium">Drafted changes</h3>
          {live.map((patch) => {
            const ladder = (patch.ladder ?? {}) as Record<string, unknown>;
            const exported = str(patch.state) === "exported";
            const landing = str(patch.derived_state);
            return (
              <div key={str(patch.patch_id)} className="space-y-2 border-t pt-3 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-xs">{str(patch.patch_id)}</code>
                  <Badge variant="outline">{str(patch.state)}</Badge>
                  {landing && <Badge>{landing}</Badge>}
                </div>

                {str(patch.was) !== "" && (
                  <AssetDiff
                    assetId={str(patch.asset_id)}
                    fieldPath={str(patch.field_path)}
                    was={str(patch.was)}
                    becomes={str(patch.becomes)}
                  />
                )}

                <p className="text-xs text-muted-foreground">
                  {Object.keys(ladder).length === 0
                    ? REVIEW_COPY.ladderUnrun
                    : `Verified: ${Object.keys(ladder).sort().join(", ")}.`}
                </p>
                <p className="text-xs text-muted-foreground">
                  {exported ? REVIEW_COPY.handoffExported : REVIEW_COPY.handoffPending}
                </p>
                {landing === "landed_matched" && (
                  <p className="text-xs text-muted-foreground">{REVIEW_COPY.landedMatchedNote}</p>
                )}

                <pre className="overflow-x-auto rounded-md bg-muted/40 p-2 text-xs">
                  {`uv run --frozen python tools/export_bundle.py --patch ${str(patch.patch_id)} --dry-run`}
                </pre>

                <Button
                  size="sm"
                  variant="ghost"
                  disabled={withdraw.isPending}
                  onClick={() =>
                    withdraw.mutate({
                      patchId: str(patch.patch_id),
                      reason: "withdrawn from the review surface",
                    })
                  }
                >
                  Withdraw
                </Button>
              </div>
            );
          })}
        </Card>
      )}

      {open && (
        <Card className="space-y-3 p-4">
          <h3 className="text-sm font-medium">{REVIEW_COPY.draftHeading}</h3>
          {observation.question_is_held_out && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              {REVIEW_COPY.draftHeldOutWarning}
            </p>
          )}

          <div className="grid gap-2 sm:grid-cols-2">
            <label className="space-y-1 text-xs">
              <span className="text-muted-foreground">Asset id</span>
              <Input value={assetId} onChange={(e) => setAssetId(e.target.value)} />
            </label>
            <label className="space-y-1 text-xs">
              <span className="text-muted-foreground">Field</span>
              <select
                className="h-8 w-full rounded-lg border bg-transparent px-2 text-sm"
                value={fieldPath}
                onChange={(e) => setFieldPath(e.target.value)}
              >
                <option value="summary">summary</option>
                <option value="body">body</option>
              </select>
            </label>
          </div>

          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">
              What the field holds now, exactly. The edit is refused if it has moved.
            </span>
            <Textarea value={was} onChange={(e) => setWas(e.target.value)} rows={2} />
          </label>

          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">What it should say</span>
            <Textarea value={becomes} onChange={(e) => setBecomes(e.target.value)} rows={3} />
          </label>

          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">Why</span>
            <Textarea value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
          </label>

          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">
              Corpus content hash — all {HASH_CHARS} characters, not the 16 shown elsewhere
            </span>
            <Input
              value={hash}
              onChange={(e) => setHash(e.target.value.trim())}
              aria-invalid={hashProblem !== null}
            />
            {hashProblem !== null && (
              <span className="text-destructive">{hashProblem}</span>
            )}
          </label>

          {was !== "" && becomes !== "" && (
            <div className="space-y-1">
              <h4 className="text-xs font-medium">{REVIEW_COPY.diffHeading}</h4>
              <AssetDiff assetId={assetId} fieldPath={fieldPath} was={was} becomes={becomes} />
            </div>
          )}

          <div className="flex gap-2">
            <Button size="sm" onClick={submit} disabled={!canSubmit}>
              {REVIEW_COPY.draftSubmit}
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          </div>

          {draft.isError && (
            <p className="text-xs text-destructive">
              {draft.error instanceof ApiError && draft.error.status === 404
                ? "Drafting is not mounted on this engine (GOVERNED_BI_FEEDBACK_ADMIN is unset), or an observation id is unknown."
                : String(draft.error)}
            </p>
          )}

          <p className="text-xs text-muted-foreground">{REVIEW_COPY.decisionBoundary}</p>
        </Card>
      )}
    </div>
  );
}
