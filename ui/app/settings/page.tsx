"use client";

/**
 * `/settings` — who this browser is, and therefore what it shows.
 *
 * **Why a page and not the header icon it replaces.** The Simple/Audit switch was a two-state eye
 * button beside the theme toggle, with no label and no way to say what either state meant, and it
 * changed what an entire application shows. A role is not a display preference. A page has room to
 * name each tier and list what it can and cannot reach, which is the only way the choice is legible
 * before you make it.
 *
 * Reachable at every tier on purpose (`lib/capabilities.ts::tierReaches` special-cases it): a tier
 * that could not get back out of itself would be a trap.
 *
 * **Two sections, and the split is who each one affects.** A role is a per-browser preference.
 * Engine switches change what the deployment does for everyone it serves, so they are engineer-only
 * — not as a security boundary (this engine binds to loopback and `api/auth.py` says reaching the
 * port is sufficient) but because offering them to a business user would be offering a decision
 * they have no way to evaluate.
 *
 * The engine switches replace `allow_user_clarification`, which had a schema, an `api-client`
 * method and a rendered component in this fork and **no route on either branch** — the name is not
 * in the engine's knob register at all. What is listed now is what
 * `serve/runtime_overrides.py::TOGGLEABLE` actually allows.
 */

import { PageShell } from "@/components/layout/page-shell";
import { EngineToggles } from "@/components/settings/engine-toggles";
import { RoleSwitcher } from "@/components/settings/role-switcher";
import { resolveTier } from "@/lib/capabilities";
import { useCapabilities } from "@/hooks/queries";
import { useDisplayModeOverride } from "@/lib/display-mode";

export default function SettingsPage() {
  const { data: caps } = useCapabilities();
  const tier = resolveTier(caps, useDisplayModeOverride());

  return (
    <PageShell
      title="Settings"
      description="Who you are here decides what you see — and what stays out of your way."
    >
      <div className="flex max-w-5xl flex-col gap-8">
        <section className="space-y-3">
          <div className="space-y-1">
            <h2 className="text-sm font-medium">Role</h2>
            <p className="max-w-prose text-xs text-muted-foreground">
              Saved in this browser only, and it takes effect immediately — nothing restarts. When
              this deployment starts assigning roles per user, that assignment becomes the default
              and this stays available as an override.
            </p>
          </div>
          <RoleSwitcher />
        </section>

        {tier === "engineer" && (
          <section className="space-y-3">
            <div className="space-y-1">
              <h2 className="text-sm font-medium">Engine behaviour</h2>
              <p className="max-w-prose text-xs text-muted-foreground">
                These change what the engine does for <em>everyone</em> it serves, not just this
                browser, and they take effect on the next turn. Each row says where its current
                value came from — a switch the environment pins is disabled rather than accepting a
                click the engine would ignore.
              </p>
            </div>
            <EngineToggles />
          </section>
        )}
      </div>
    </PageShell>
  );
}
