import { PageShell } from "@/components/layout/page-shell";
import { ModelSettings } from "@/components/settings/model-settings";

/**
 * `/settings` — what this engine is configured with, read-only.
 *
 * Today that is the three model surfaces (agent, utility, embedding). It is a route and not a
 * popover because these are values a reader checks against an artifact — "which model produced
 * this run" — and that is a thing you want addressable, not a menu you dismiss.
 */
export default function SettingsPage() {
  return (
    <PageShell
      title="Settings"
      description="The models this engine resolved at startup. Read-only — set them in the engine's environment."
    >
      <ModelSettings />
    </PageShell>
  );
}
