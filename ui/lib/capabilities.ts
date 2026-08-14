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

/**
 * Whether the engine reports it *can* pause a turn to ask a question.
 *
 * **Never gate the clarification prompt on this.** Upstream deleted this helper for that
 * reason and the reason is right: HITL is gated on the arriving `interrupt()`, so a stale
 * flag must not be able to hide a prompt while the graph is waiting on an answer (see
 * `components/chat/clarification-prompt.tsx`).
 *
 * It survives for the one caller that is asking a different question:
 * `components/corpus/clarification-toggle.tsx`, the admin's `allow_user_clarification`
 * switch. That control is not rendering a pending question — it is offering to change a
 * setting, and a setting the engine cannot honour should read as unavailable rather than
 * silently do nothing.
 */
export function canClarify(caps: Capabilities | undefined): boolean {
  return caps?.can_clarify === true;
}

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
 * UtkuAI Phase 1b: the backend wants the business-user view (plain-language
 * answer + reliability only, no SQL/pipeline) by default. The payload is
 * unchanged either way — this only picks the frontend's default rendering; a
 * user may still reveal the audit view client-side (see `chat/conversation.tsx`).
 */
export function isSimpleUiMode(caps: Capabilities | undefined): boolean {
  return caps?.ui_display_mode === "simple";
}

/**
 * Effective simple/audit mode: an in-UI toggle (`lib/display-mode.ts`) always
 * wins over the backend's `/capabilities` default, so a user can flip modes
 * live in one session without touching `governed_bi.toml` or restarting the
 * backend. See `useEffectiveSimpleMode` for the reactive hook form.
 */
export function effectiveSimpleMode(
  caps: Capabilities | undefined,
  override: "simple" | "audit" | null,
): boolean {
  if (override === "simple") return true;
  if (override === "audit") return false;
  return isSimpleUiMode(caps);
}
