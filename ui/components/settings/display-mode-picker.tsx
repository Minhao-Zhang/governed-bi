"use client";

/**
 * `/settings` — how much of a turn's machinery this browser shows.
 *
 * The one writable control on this page, and it writes nothing to the engine. `model-settings.tsx`
 * beside it is read-only because a knob has one home (`register/knobs.py`) and a form here would be
 * a second place deciding it; this control has the opposite shape for the opposite reason — it is a
 * preference belonging to whoever is looking, so `localStorage` *is* its home and no engine round
 * trip exists to make.
 *
 * The card states that it is not a permission. That sentence is load-bearing rather than decorative:
 * a reader who believes `business` withholds the SQL from anyone else has been misled, and
 * `docs/enterprise-fork.md` exists partly to stop this repository shipping something that looks
 * like a boundary and is not. See `lib/display-mode.ts` for the full argument.
 */

import { Eye, Ruler, Wrench } from "lucide-react";

import {
  DISPLAY_MODES,
  setDisplayMode,
  useDisplayMode,
  type DisplayMode,
} from "@/lib/display-mode";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const COPY: Record<DisplayMode, { title: string; what: string; icon: React.ElementType }> = {
  business: {
    title: "Business",
    what: "The answer, and whether it consulted your data.",
    icon: Eye,
  },
  analyst: {
    title: "Analyst",
    what: "Adds what was retrieved and which tables were licensed.",
    icon: Ruler,
  },
  engineer: {
    title: "Engineer",
    what: "Adds the generated SQL, the attempt ledger and the turn's record.",
    icon: Wrench,
  },
};

export function DisplayModePicker() {
  const active = useDisplayMode();

  return (
    <Card className="p-4">
      <div className="mb-3">
        <h3 className="text-sm font-medium">Display mode</h3>
        <p className="text-muted-foreground mt-1 text-xs">
          How much of each turn is shown. Stored in this browser only — the engine sends the same
          answer either way, so this is not a permission and withholds nothing from anyone else.
        </p>
      </div>

      <div
        role="radiogroup"
        aria-label="Display mode"
        className="grid gap-2 sm:grid-cols-3"
      >
        {DISPLAY_MODES.map((mode) => {
          const { title, what, icon: Icon } = COPY[mode];
          const selected = mode === active;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => setDisplayMode(mode)}
              className={cn(
                "rounded-md border p-3 text-left transition-colors",
                "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                selected
                  ? "border-primary bg-primary/5"
                  : "border-border hover:bg-muted/50",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <Icon className="size-4 shrink-0" aria-hidden />
                {title}
              </span>
              <span className="text-muted-foreground mt-1 block text-xs">{what}</span>
            </button>
          );
        })}
      </div>
    </Card>
  );
}
