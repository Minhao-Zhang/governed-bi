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
 * One section today. The next candidate is the `allow_user_clarification` switch — whether the
 * agent may pause a turn to ask — which is **not here** because its backend does not exist: no
 * route, and no writable knob in the register behind it. Adding a control for it would be a fourth
 * client-only half, which is the pattern
 * `docs/utkuai-role-tiers-and-clarification-cancel.md` exists partly to stop.
 */

import { PageShell } from "@/components/layout/page-shell";
import { RoleSwitcher } from "@/components/settings/role-switcher";

export default function SettingsPage() {
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
      </div>
    </PageShell>
  );
}
