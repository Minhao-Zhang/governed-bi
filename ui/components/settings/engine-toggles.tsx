"use client";

/**
 * Switches that change what the **engine** does, not what this browser shows.
 *
 * The distinction is the reason this is a separate section from the role switcher above it: a role
 * is a per-browser preference, and these change behaviour for everyone the deployment serves. It
 * is also why the section is engineer-only.
 *
 * **Every row says where its value came from**, and that is the point rather than a detail. This
 * fork shipped three controls whose server half did not exist — a switch posting to a route that
 * was never written, a control gated on a flag hardcoded `False` so it could never render, and a
 * client schema field the engine never populates. All three *looked* finished. A `source` on every
 * row is what makes "this switch is not in charge of this value" something the interface can say
 * out loud: when the environment pins a knob, the switch is disabled and names the variable
 * instead of accepting a click the engine would ignore.
 *
 * The engine decides what may be listed here at all — see
 * `serve/runtime_overrides.py::TOGGLEABLE`, which is an allowlist and not a role filter, because
 * the `operational` role also carries the fields a measurement's provenance is made of.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type { RuntimeToggle } from "@/lib/types";
import { QueryState } from "@/components/common/query-state";
import { Switch } from "@/components/ui/switch";

export function EngineToggles() {
  const query = useQuery({ queryKey: ["settings-toggles"], queryFn: api.toggles });

  return (
    <QueryState
      query={query}
      isEmpty={(rows) => rows.length === 0}
      emptyMessage="This engine exposes no runtime switches."
    >
      {(rows) => (
        <div className="divide-y rounded-lg border">
          {rows.map((row) => (
            <ToggleRow key={row.name} row={row} />
          ))}
        </div>
      )}
    </QueryState>
  );
}

function ToggleRow({ row }: { row: RuntimeToggle }) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const on = row.value === true;

  async function flip(next: boolean) {
    if (pending) return;
    setPending(true);
    try {
      // `null` clears rather than writing `false`, so a knob returns to whatever the register and
      // the environment say instead of being pinned to a value that only looks like the default.
      await api.setToggle(row.name, next ? true : null);
      await queryClient.invalidateQueries({ queryKey: ["settings-toggles"] });
      // `/capabilities` reports two of these, so the corpus page's gating follows immediately.
      await queryClient.invalidateQueries({ queryKey: ["capabilities"] });
      toast.success(next ? `${row.name} enabled` : `${row.name} back to its default`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not change the setting.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex items-start justify-between gap-4 p-4">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-mono text-sm font-medium">{row.name}</p>
          <SourceBadge row={row} />
        </div>
        <p className="max-w-prose text-xs text-muted-foreground">{row.why}</p>
        {!row.editable && row.env_var && (
          <p className="text-xs text-muted-foreground">
            <span className="font-mono">{row.env_var}</span> pins this for the running process. An
            exported variable is how an eval arm pins a run, so a click here would be a value the
            engine does not use. Unset it and restart to make this switch effective.
          </p>
        )}
      </div>
      <Switch
        checked={on}
        disabled={pending || !row.editable}
        onCheckedChange={(next) => void flip(next)}
        aria-label={`${row.name}: ${on ? "on" : "off"}`}
      />
    </div>
  );
}

/** Where the current value came from. `override` is the only one a click produced. */
function SourceBadge({ row }: { row: RuntimeToggle }) {
  if (row.source === "environment") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs text-muted-foreground">
        <Lock className="size-3" aria-hidden />
        pinned by the environment
      </span>
    );
  }
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-xs",
        row.source === "override"
          ? "bg-primary/10 text-primary"
          : "bg-secondary text-muted-foreground",
      )}
    >
      {row.source === "override" ? "set here" : "engine default"}
    </span>
  );
}
