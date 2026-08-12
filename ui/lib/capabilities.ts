/**
 * Capability helpers. Every optional UI affordance is gated on `/capabilities`
 * so the UI adapts to whatever the attached backend can actually do (handoff §4)
 * rather than assuming.
 */

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

// No `canClarify` helper: HITL is gated on the arriving `interrupt()`, not on
// `can_clarify`, so the prompt can never be hidden by a stale flag while the graph
// waits on an answer. See components/chat/clarification-prompt.tsx for the
// reasoning. The wire field is still parsed in lib/schemas.ts.
