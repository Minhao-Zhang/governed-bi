/** Capability helpers over `/capabilities`: every field is an observation (ADR 0007 §7). */

import type { Capabilities } from "@/lib/types";

/** Whether chat can be served at all; `false` mounts <NoTransport/>, as there is no fallback. */
export function canStream(caps: Capabilities | undefined): boolean {
  return caps?.can_stream === true;
}
