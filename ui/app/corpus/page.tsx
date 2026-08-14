"use client";

/**
 * `/corpus` — every asset the engine loaded, plus the admin surface for curating it.
 *
 * **State is a header control, not a section.** `CorpusStatus` sits in the page header with
 * every number the `/audit/corpus` route reports behind it; only a corpus the engine refuses to
 * serve earns space in the content column (`CorpusFatalNotice`). It was a tinted full-width
 * panel here, which pushed the table — the thing the page is *for* — 250px down the page on
 * every visit to report that nothing was wrong.
 *
 * **Two reading modes, and the split is the corpus's, not the implementation's.** An asset's
 * fields depend on its type: a metric has an expression and dimensions, a column has a physical
 * type and a role. So "every field of one type" and "one common projection of every type" are
 * two different tables, and no single grid is both:
 *
 *   - **By type** — one type at a time, every field the engine declares, filtered and sorted
 *     server-side (ADR 0009) and pulled in as you scroll. For triage on a known axis.
 *   - **Search** — a ranked Fuse match over a client-side catalog of all types at once, on the
 *     six fields they share. For finding the asset you cannot name exactly, which a per-column
 *     `contains` is not: one column's substring is not a ranked match across several.
 *
 * The two used to be labelled "Table" and "Search" — the *how*, not the *what* — and they did
 * not talk to each other, so a hit you found in one had to be re-found by hand in the other.
 * Now a hit hands off: picking one switches to **By type**, scoped to that asset by id, where
 * its full record is a click away. Search locates; the type view explains.
 *
 * **Four curation tabs after them, and reading vs curating is why they are tabs here rather
 * than a second page.** Setup Wizard / Clarifications / Agreed Assumptions / Needs Review are
 * this fork's admin surface for the clarification-Q&A + Enhancer feature, and every one of them
 * is about the same assets the two tabs above list — an admin who finds a wrong term in **By
 * type** answers the question that fixes it two tabs over, on the same route.
 *
 * They are hidden entirely when `capabilities.can_curate_corpus` is false rather than shown
 * empty: with the backend toggle off nothing is ever folded or certified that way, so an empty
 * Needs Review would read as "nothing needs review". Gated on `can_curate_corpus` and not
 * `can_clarify` — that one means "a live model is attached and `ask_user` interrupts work",
 * which is orthogonal to whether these admin routes exist.
 *
 * No "Skills" tab: the backend's `skill` asset type was removed upstream (ADR 0003 generalised
 * it into `NoteAsset`, visible under the Note filter), so `GET /skills` would only ever 404. */

import { useState } from "react";

import { PageShell } from "@/components/layout/page-shell";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AssetBrowser } from "@/components/corpus/asset-browser";
import { AssetTable } from "@/components/corpus/asset-table";
import { CorpusFatalNotice, CorpusStatus } from "@/components/corpus/corpus-status";
import { AssumptionsLog } from "@/components/corpus/assumptions-log";
import { ClarificationToggle } from "@/components/corpus/clarification-toggle";
import { ClarificationsPanel } from "@/components/corpus/clarifications-panel";
import { ConflictsPanel } from "@/components/corpus/conflicts-panel";
import { ElicitationWizard } from "@/components/corpus/elicitation-wizard";
import { canCurateCorpus } from "@/lib/capabilities";
import { useCapabilities } from "@/hooks/queries";

type Mode = "type" | "search" | "wizard" | "clarifications" | "assumptions" | "conflicts";

/** Only the two reading modes get a hint line; the curation tabs explain themselves inside. */
const HINT: Partial<Record<Mode, string>> = {
  type: "One asset type at a time, with every field the engine declares for it. Filtered, sorted and paged on the server.",
  search: "Ranked name search across every type at once. Pick a hit to open it in the type view.",
};

export default function CorpusPage() {
  const { data: caps } = useCapabilities();
  const curationFeatureOn = canCurateCorpus(caps);
  const [mode, setMode] = useState<Mode>("type");
  //: A located asset, and the counter that makes locating it *again* an event. The table below
  //: takes this as its initial state and is keyed on it, so a hand-off is a deliberate reset of
  //: type, scope and filters — while an ordinary tab switch keeps every bit of that state (see
  //: `forceMount`). Threading it as live props instead would need an effect to sync props into
  //: state, and an effect that overwrites what the reader has since typed is the same bug.
  const [focus, setFocus] = useState<{ type: string; id: string; nonce: number } | null>(null);

  return (
    <PageShell
      title="Corpus"
      description="Every asset the engine loaded — one type in full, a search across all of them, or the curation queues."
      actions={<CorpusStatus />}
      // The page itself does not scroll; the rows do. Everything above the grid — the tabs, the
      // scope selects, the filter chips, the paging footer — is a control for the grid, and a
      // control that scrolls off the top while you are reading what it controls is not one.
      fill
    >
      {/* The gap the old layout did not have: the state panel's bottom border and the tab list
          were 1px apart, so the two read as one control. `PageShell` frames a page and does not
          space its sections — pages with more than one section own their own rhythm, as
          `/schema` does. */}
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        <CorpusFatalNotice />

        {/* Above the tabs, not in the header beside `CorpusStatus`: this is a `Card` with two
            lines of explanation, and the switch changes what the *engine* does on the next turn
            rather than what this page shows. Header actions are for the latter. */}
        {curationFeatureOn && <ClarificationToggle />}

        <Tabs
          value={mode}
          onValueChange={(next) => setMode(next as Mode)}
          className="min-h-0 flex-1 gap-4"
        >
          {/* The hint sits beside the tabs rather than under the page title, because it
              describes the *mode*, and which mode you are in is a thing you change here. */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <TabsList>
              <TabsTrigger value="type">By type</TabsTrigger>
              <TabsTrigger value="search">Search</TabsTrigger>
              {curationFeatureOn && (
                <>
                  <TabsTrigger value="wizard">Setup Wizard</TabsTrigger>
                  <TabsTrigger value="clarifications">Clarifications</TabsTrigger>
                  <TabsTrigger value="assumptions">Agreed Assumptions</TabsTrigger>
                  <TabsTrigger value="conflicts">Needs Review</TabsTrigger>
                </>
              )}
            </TabsList>
            {HINT[mode] && <p className="max-w-prose text-xs text-muted-foreground">{HINT[mode]}</p>}
          </div>

          {/* `forceMount` on this tab only. Radix unmounts inactive tab content, so switching
              to Search and back **discarded the asset type, every filter, the sort and the
              page** — which reads as the table refreshing itself for no reason. Kept mounted
              (Radix marks it `hidden`) so the state survives; its queries are small, 43 KB for
              a page of rows. The Search tab stays lazy because its catalog is 2.25 MB and
              mounting it eagerly would pay that on every visit to this page. The four curation
              tabs stay lazy for the same reason in reverse: each opens its own admin queue, and
              a reader who never curates should not fetch four of them. */}
          <TabsContent
            value="type"
            forceMount
            // `hidden` + `data-[state=active]:flex`, not `flex` + `data-[state=inactive]:hidden`:
            // both set `display`, and a variant beating a base utility is the one ordering
            // Tailwind actually guarantees. (The HTML `hidden` attribute Radix puts on an
            // inactive `forceMount` panel loses to any CSS `display` at all, so the class has to
            // do this itself.)
            className="hidden min-h-0 flex-col data-[state=active]:flex"
          >
            <AssetTable
              key={focus ? `${focus.type}:${focus.id}:${focus.nonce}` : "browse"}
              focus={focus}
            />
          </TabsContent>

          <TabsContent value="search" className="flex min-h-0 flex-col">
            <AssetBrowser
              onLocate={(row) => {
                setFocus((prev) => ({
                  type: row.asset_type,
                  id: row.id,
                  nonce: (prev?.nonce ?? 0) + 1,
                }));
                setMode("type");
              }}
            />
          </TabsContent>

          {curationFeatureOn && (
            <>
              <TabsContent value="wizard">
                <ElicitationWizard />
              </TabsContent>
              <TabsContent value="clarifications">
                <ClarificationsPanel />
              </TabsContent>
              <TabsContent value="assumptions">
                <AssumptionsLog />
              </TabsContent>
              <TabsContent value="conflicts">
                <ConflictsPanel />
              </TabsContent>
            </>
          )}
        </Tabs>
      </div>
    </PageShell>
  );
}
