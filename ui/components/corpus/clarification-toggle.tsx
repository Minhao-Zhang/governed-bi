"use client";

/**
 * Live switch for the backend's `allow_user_clarification` setting (Round D3).
 * Gates the ENTIRE admin-clarification/Enhancer feature (live `ask_user`,
 * defer, the offline Clarifications/Agreed Assumptions/Needs Review tabs on
 * this page): off = vanilla governed-bi (Minhao's fail-closed-until-approved
 * philosophy), on = this session's live self-correction feature.
 *
 * Flips a runtime override on the backend (POST
 * /settings/allow-user-clarification) that every real gating point re-checks
 * fresh per request/turn — effective on the very next call, no restart. On
 * success, refetches `/capabilities` so this page's tab visibility (gated on
 * `capabilities.can_clarify`) updates immediately.
 *
 * Rendered only when `capabilities.can_edit` — same admin-only gate as every
 * other corpus mutation (`/corpus/edit`, conflict resolution, draft approval).
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api-client";
import { canClarify, canEdit } from "@/lib/capabilities";
import { useCapabilities } from "@/hooks/queries";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";

export function ClarificationToggle() {
  const { data: caps } = useCapabilities();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);

  if (!canEdit(caps)) return null;

  const enabled = canClarify(caps);

  async function toggle(next: boolean) {
    if (pending) return;
    setPending(true);
    try {
      await api.setAllowUserClarification(next);
      await queryClient.invalidateQueries({ queryKey: ["capabilities"] });
      toast.success(next ? "User clarification enabled." : "User clarification disabled.");
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to update the setting.";
      toast.error(message);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-4 py-4">
        <div className="space-y-1">
          <p className="text-sm font-medium">Enable user clarification (live self-correction)</p>
          <p className="text-xs text-muted-foreground">
            When off, behaves like the default governed-bi: nothing is served without
            analyst approval.
          </p>
        </div>
        <Switch
          checked={enabled}
          disabled={pending}
          onCheckedChange={(checked) => void toggle(checked)}
          aria-label="Enable user clarification"
        />
      </CardContent>
    </Card>
  );
}
