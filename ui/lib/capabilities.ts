/**
 * Capability helpers. Every optional UI affordance is gated on `/capabilities`
 * so the UI adapts to whatever the attached backend can actually do (handoff §4)
 * rather than assuming.
 */

import type { Tier } from "@/lib/display-mode";
import type { Capabilities } from "@/lib/types";

/** Editing affordances show only when the backend reports it can edit. */
export function canEdit(caps: Capabilities | undefined): boolean {
  return caps?.can_edit === true;
}

/** Live streaming chat (`useStream`) vs the non-streaming `/chat` fallback. */
export function canStream(caps: Capabilities | undefined): boolean {
  return caps?.can_stream === true;
}

/** Whether a real model is attached (drives NL narration vs compact render). */
export function hasLiveModel(caps: Capabilities | undefined): boolean {
  return caps?.has_live_model === true;
}

/**
 * `canScope` was **deleted**, and the deletion is the fix.
 *
 * It gated the two graph hooks and the catalog on `can_scope === true`, which is false whenever
 * `caps` is merely *undefined* — every first render, before `/capabilities` resolves. So the
 * first fetch of each graph went out unscoped, the engine's echoed budget could not match the
 * one the component filters with, and the client fell back to truncating alphabetically: 150
 * nodes with **0 edges** between them, measured. The false branch was a trapdoor to a known
 * bug, and nothing behind the flag was ever actually optional — the engine honours a scope
 * parameter whether or not it advertises `can_scope`.
 *
 * `can_scope` still arrives on the wire and is still declared, because it is a true observation
 * worth showing on an audit surface. It is just not a switch any render path may read.
 * See `docs/plans/api-design-review-2026-08-04.md` D-1.
 */

/** D15: server-ranked `GET /search` is available (else the client Fuse index). */
export function canSearch(caps: Capabilities | undefined): boolean {
  return caps?.can_search === true;
}

// No `canClarify` helper. Upstream deleted theirs because HITL is gated on the arriving
// `interrupt()`, never on a flag that can be stale while the graph waits — and this fork kept one
// for `clarification-toggle.tsx`, a component that gated on `can_edit` (hardcoded `False`, so it
// never rendered) and posted to a route that never existed. The component is gone and so is the
// helper. The wire field is still parsed in `lib/schemas.ts`.

/**
 * Phase 5: whether this session's corpus_root is writable, i.e. whether the
 * corpus-curation admin routes (`/corpus/conflicts*`, `/corpus/assumptions`,
 * `/corpus/drafts/{id}/approve`) will actually work — a different question
 * from `canClarify` above, which is about a live `ask_user` interrupt, not
 * corpus-write capability.
 */
export function canCurateCorpus(caps: Capabilities | undefined): boolean {
  return caps?.can_curate_corpus === true;
}

/**
 * **The one function that decides which tier this browser renders as.**
 *
 * Local override (`/settings`, persisted in `lib/display-mode.ts`) beats the server's
 * `ui_display_mode`, which beats the safest default. Everything that branches on tier calls this
 * — `nav.tsx` for which surfaces exist, `message-list.tsx` for how much of an answer card shows —
 * so there is one place the precedence lives.
 *
 * **`business` is the default when nothing says otherwise**, and that direction is deliberate. The
 * tiers differ in what they *expose*: `/audit` returns every thread's SQL and an absolute log path,
 * and `/history` lists the server's threads rather than the caller's. Defaulting to the tier that
 * shows least means a misconfiguration hides things rather than leaking them. It also means a
 * fresh browser starts business-facing, which is the product this is.
 *
 * The server half is **not wired**: the engine never populates `ui_display_mode`
 * (`grep -r ui_display_mode src/` is empty). The read stays here so a future multi-tenant server
 * can set it per tenant with no change to any screen — see
 * `docs/utkuai-role-tiers-and-clarification-cancel.md`.
 */
export function resolveTier(
  caps: Capabilities | undefined,
  override: Tier | null,
): Tier {
  if (override !== null) return override;
  const fromServer = caps?.ui_display_mode;
  if (fromServer === "business" || fromServer === "analyst" || fromServer === "engineer") {
    return fromServer;
  }
  // The two-state spellings this replaced. Mapped rather than ignored so a server still sending
  // them keeps working; see `lib/display-mode.ts` for the same mapping on the stored override.
  if (fromServer === "simple") return "business";
  if (fromServer === "audit") return "engineer";
  return "business";
}

/** Which sidebar surfaces a tier may reach, and the reason each exclusion is not taste.
 *
 * - **History is not in `business`** because `lib/threads.ts` lists the *server's* threads, not
 *   the caller's. Until threads are per-principal, a business user would read other people's
 *   questions.
 * - **Corpus is not in `analyst`** because curating the semantic layer changes what the engine
 *   answers for everyone. It stays additionally gated on `can_curate_corpus`: one gate asks
 *   whether this person may curate, the other whether this deployment can curate at all.
 * - **Audit is `engineer` only** because it returns every thread's SQL, the complete turn records
 *   and `TURN_LOG_DIR` as an absolute path.
 *
 * `/settings` is absent from this map on purpose — it is reachable at every tier, so gating it
 * would be a rule with no false case, and a tier that could not get back out of itself is a trap.
 */
const REACHABLE: Record<Tier, readonly string[]> = {
  business: ["/"],
  analyst: ["/", "/schema", "/history"],
  engineer: ["/", "/schema", "/history", "/corpus", "/audit"],
};

/** Whether `href` is a surface `tier` may reach. `/settings` is always true. */
export function tierReaches(tier: Tier, href: string): boolean {
  return href === "/settings" || REACHABLE[tier].includes(href);
}

/** How much of an answer card a tier sees. Named rather than inlined because two components
 * branch on it (`answer-card.tsx`, `serve-progress.tsx`) and a second copy of the rule would
 * drift the first time a tier changed. */
export function tierShowsSql(tier: Tier): boolean {
  return tier !== "business";
}

/** The provenance drawer, the corpus pin and the reasoning timeline — the audit surfaces. */
export function tierShowsAudit(tier: Tier): boolean {
  return tier === "engineer";
}
