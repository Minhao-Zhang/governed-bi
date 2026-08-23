/**
 * Capability helpers. Every optional UI affordance is gated on `/capabilities` so the UI
 * adapts to whatever the attached backend can actually do rather than assuming. The contract
 * is ADR 0007 §7 — every field is an observation, and `false` is a legitimate answer.
 */

import type { Capabilities } from "@/lib/types";

/**
 * Whether the backend reports it can edit the corpus. **No render path calls this, and none
 * should**: `capabilities_for` returns `can_edit: false` with `edit_mode: "none"` on every
 * engine, because the curator is out of scope of the served surface (ADR 0007 §7). The edit
 * sheet this used to gate posted to `POST /corpus/edit`, a route that does not exist, and is
 * deleted. Kept because the flag is still on the wire and is still a true observation worth
 * reading on an audit surface — not because there is an affordance behind it.
 */
export function canEdit(caps: Capabilities | undefined): boolean {
  return caps?.can_edit === true;
}

/**
 * Whether chat can be served at all. There is one transport (`useStream`); the non-streaming
 * `POST /chat` this used to fall back to is deleted, so `false` mounts `<NoTransport/>` rather
 * than a degraded composer. See `components/chat/chat-panel.tsx`.
 */
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
 * worth showing on an audit surface. It is just not a switch any render path may read. ADR 0009
 * D4 is the rule it follows ("a flag is flipped by building the thing"); the review that first
 * wrote it down is `git-history:docs/plans/api-design-review-2026-08-04.md` D-1.
 */

/**
 * Whether a server-ranked `GET /search` is available. **Never true, and no render path calls
 * this**: ADR 0009 Amendment 1 records that the route is "deliberately **not** built" and
 * `capabilities_for` hardcodes `can_search: false`. Ranking is the client Fuse index
 * (`lib/catalog.ts`, `lib/asset-catalog.ts`) — the only one there is. Kept for the same reason
 * as `canEdit`: the flag is on the wire and this is the client's view of it.
 */
export function canSearch(caps: Capabilities | undefined): boolean {
  return caps?.can_search === true;
}

// No `canClarify` helper: HITL is gated on the arriving `interrupt()`, not on
// `can_clarify`, so the prompt can never be hidden by a stale flag while the graph
// waits on an answer. See components/chat/clarification-prompt.tsx for the
// reasoning. The wire field is still parsed in lib/schemas.ts.
