"use client";

/**
 * Pick the role tier this browser renders as.
 *
 * Three cards rather than a dropdown, because the choice is not obvious from its label: a visitor
 * who does not already know what "Analyst" means in this product needs to read what it can see
 * before picking it. The list of surfaces under each option is that explanation, and it is the
 * same list `lib/capabilities.ts::tierReaches` enforces — stated here, enforced there, one rule.
 *
 * **What this does not do.** It sets a local override, persisted per browser
 * (`lib/display-mode.ts`). The engine never sends `ui_display_mode`, so there is no server default
 * to fall back to yet and "Use the server's default" is deliberately not offered — an option that
 * always resolved to `business` would be a third way of saying the same thing. When a
 * multi-tenant server starts setting the field, `resolveTier` already prefers it whenever this
 * override is `null`, and this component grows a "follow the deployment" choice then.
 */

import { Check, Eye, ScrollText, Table2 } from "lucide-react";

import { useCapabilities } from "@/hooks/queries";
import { resolveTier } from "@/lib/capabilities";
import { setDisplayModeOverride, useDisplayModeOverride, type Tier } from "@/lib/display-mode";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";

const TIERS: ReadonlyArray<{
  id: Tier;
  label: string;
  who: string;
  icon: typeof Eye;
  sees: readonly string[];
  hidden: string;
}> = [
  {
    id: "business",
    label: "Business",
    who: "Asks questions in plain language and reads the answer.",
    icon: Eye,
    sees: ["Chat — the answer and how reliable it is"],
    hidden:
      "No SQL, no pipeline, no corpus. History is hidden too: it lists the server's conversations, not just yours.",
  },
  {
    id: "analyst",
    label: "Analyst",
    who: "Answers other people's questions and needs to check the data model.",
    icon: Table2,
    sees: [
      "Chat — plus the SQL that produced each answer",
      "Schema — tables, columns and how they join",
      "History — past conversations",
    ],
    hidden: "No corpus curation and no engine audit: both change what everyone else gets.",
  },
  {
    id: "engineer",
    label: "Engineer",
    who: "Curates the semantic layer and debugs the engine.",
    icon: ScrollText,
    sees: [
      "Everything an Analyst sees",
      "Corpus — assets, the Setup Wizard and the curation queues",
      "Audit — every served turn, stage by stage",
      "Full provenance on each answer",
    ],
    hidden: "Nothing.",
  },
];

export function RoleSwitcher() {
  const { data: caps } = useCapabilities();
  const override = useDisplayModeOverride();
  const active = resolveTier(caps, override);

  return (
    <div className="grid gap-3 md:grid-cols-3">
      {TIERS.map(({ id, label, who, icon: Icon, sees, hidden }) => {
        const selected = id === active;
        return (
          <Card
            key={id}
            role="radio"
            aria-checked={selected}
            tabIndex={0}
            onClick={() => setDisplayModeOverride(id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                setDisplayModeOverride(id);
              }
            }}
            className={cn(
              "cursor-pointer transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
              selected ? "border-primary bg-primary/5" : "hover:border-muted-foreground/40",
            )}
          >
            <CardContent className="space-y-3 py-4">
              <div className="flex items-start gap-2">
                <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{label}</p>
                  <p className="text-xs text-muted-foreground">{who}</p>
                </div>
                {selected && <Check className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />}
              </div>

              <ul className="space-y-1 text-xs text-muted-foreground">
                {sees.map((line) => (
                  <li key={line} className="flex gap-1.5">
                    <span aria-hidden>·</span>
                    <span>{line}</span>
                  </li>
                ))}
              </ul>

              {/* What a tier hides is the load-bearing half of picking one, so it is on the card
                  rather than in a tooltip. */}
              <p className="border-t pt-2 text-xs text-muted-foreground/80">{hidden}</p>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
