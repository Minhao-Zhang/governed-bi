"use client";

/**
 * Which `/corpus` curation tab groups this browser shows — a view preference, not an engine
 * knob, which is why this section sits apart from `EngineToggles` above it and does not go
 * through `/settings/toggles`: that route is `KNOB_REGISTER` only and 404s on anything else
 * (`api/curation_routes.py::set_toggle`). These five never leave the browser.
 *
 * **A toggle here can only ever hide a tab, never show one the capability forbids.** Each row's
 * switch is this reader's own preference (`lib/corpus-tab-groups.ts`); whether the group's tab(s)
 * can render at all is `lib/capabilities.ts::corpusTabGroupCapable`, unaffected by the switch. A
 * row whose capability is off says so in place of pretending the switch is in charge — the same
 * reason `EngineToggles` names the environment variable instead of accepting a click the engine
 * would ignore.
 */

import { corpusTabGroupCapable, resolveTier } from "@/lib/capabilities";
import type { CorpusTabGroup } from "@/lib/corpus-tab-groups";
import { setCorpusTabGroup, useCorpusTabGroups } from "@/lib/corpus-tab-groups";
import { useDisplayModeOverride } from "@/lib/display-mode";
import { useCapabilities } from "@/hooks/queries";
import { Switch } from "@/components/ui/switch";

const ROWS: ReadonlyArray<{ group: CorpusTabGroup; label: string; tabs: string; why: string }> = [
  {
    group: "wizard",
    label: "Setup Wizard",
    tabs: "Setup Wizard",
    why: "Onboarding-time questions about gaps in the semantic layer. Turn it off once setup is done.",
  },
  {
    group: "clarifications",
    label: "Clarifications",
    tabs: "Clarifications, Agreed Assumptions, Needs Review",
    why: "Three views over one ledger — what's still owed, what's agreed, what's disputed.",
  },
  {
    group: "reports",
    label: "Reports",
    tabs: "Reports",
    why: "Where a reader flags an answer as wrong. A different record than a clarification, so it stays its own group.",
  },
  {
    group: "approvals",
    label: "Approvals",
    tabs: "Drafts",
    why: "Where Clarifications and Reports both end up, waiting to be certified into the corpus.",
  },
  {
    group: "trust-loop",
    label: "Trust Loop",
    tabs: "Trust Loop",
    why: "Read-only measurement of whether the loop above is moving anyone.",
  },
];

export function CorpusTabToggles() {
  const { data: caps } = useCapabilities();
  const tier = resolveTier(caps, useDisplayModeOverride());
  const preferences = useCorpusTabGroups();

  return (
    <div className="divide-y rounded-lg border">
      {ROWS.map((row) => {
        const capable = corpusTabGroupCapable(row.group, caps, tier);
        const on = preferences[row.group];
        return (
          <div key={row.group} className="flex items-start justify-between gap-4 p-4">
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium">{row.label}</p>
                <span className="rounded-full bg-secondary px-2 py-0.5 text-xs text-muted-foreground">
                  {row.tabs}
                </span>
              </div>
              <p className="max-w-prose text-xs text-muted-foreground">{row.why}</p>
              {!capable && (
                <p className="text-xs text-muted-foreground">
                  Hidden regardless of this switch: this session&apos;s corpus capability does not
                  offer {row.group === "trust-loop" ? "trust-loop metrics" : "corpus curation"}{" "}
                  right now.
                </p>
              )}
            </div>
            <Switch
              checked={on}
              onCheckedChange={(next) => setCorpusTabGroup(row.group, next)}
              aria-label={`${row.label}: ${on ? "on" : "off"}`}
            />
          </div>
        );
      })}
    </div>
  );
}
