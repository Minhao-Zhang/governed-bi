import { PageShell } from "@/components/layout/page-shell";
import { DisplayModePicker } from "@/components/settings/display-mode-picker";
import { ModelSettings } from "@/components/settings/model-settings";

/**
 * `/settings` — what this engine is configured with, and how much of it to show.
 *
 * Today that is the three model surfaces (agent, utility, embedding), plus the one control that
 * belongs to whoever is looking rather than to the engine: how much of a turn to show. It is a
 * route and not a popover because the model values are things a reader checks against an artifact
 * — "which model produced this run" — and that is a thing you want addressable, not a menu you
 * dismiss.
 *
 * The two halves are deliberately asymmetric. The models are read-only because a knob has one home
 * and `.env` is it; the display mode is writable because its home is this browser. Neither is a
 * permission — see `lib/display-mode.ts`.
 */
export default function SettingsPage() {
  return (
    <PageShell
      title="Settings"
      description="What this engine resolved at startup, and how much of it to show you."
    >
      <div className="space-y-6">
        <ModelSettings />
        <DisplayModePicker />
      </div>
    </PageShell>
  );
}
