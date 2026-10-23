/**
 * The one sentence the running line shows for a stage. Stage names are the
 * wire vocabulary (ADR 0010); an unknown name uses the fallback.
 */

import type { TimelineStep } from "@/lib/steps";

const SENTENCES = new Map<string, string>([
  ["accept", "Starting…"],
  ["guard", "Checking the question…"],
  ["rewrite", "Reading the follow-up…"],
  ["negative_gate", "Checking the question against known cases…"],
  ["channel_schema", "Finding your definitions…"],
  ["channel_term", "Finding your definitions…"],
  ["channel_metric", "Finding your definitions…"],
  ["channel_entity", "Finding your definitions…"],
  ["channel_example", "Finding your definitions…"],
  ["route", "Choosing which data to use…"],
  ["resolve", "Finding your definitions…"],
  ["connect", "Finding how the tables join…"],
  ["assemble", "Gathering the definitions…"],
  ["agent_core", "Working on the answer…"],
  ["read_body", "Reading the definitions…"],
  ["inspect_schema", "Looking at the tables…"],
  ["sample_rows", "Looking at sample values…"],
  ["check", "Checking the query…"],
  ["execute", "Running the query…"],
  ["cap", "Stopping after too many attempts…"],
  ["ask_user", "Waiting for your answer…"],
  ["refuse", "Deciding whether this can be answered…"],
  ["decline", "Deciding whether this can be answered…"],
  ["finish", "Finishing the answer…"],
]);

export const STATUS_FALLBACK = "Working on your question…";

/**
 * The stage to name: the latest one still running, otherwise the latest stage.
 * Concurrent stages share this one line.
 */
export function currentStage(steps: readonly TimelineStep[]): string | null {
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].status === "running") return steps[i].step;
  }
  return steps.length > 0 ? steps[steps.length - 1].step : null;
}

export function statusSentence(stage: string | null): string {
  if (stage === null) return STATUS_FALLBACK;
  return SENTENCES.get(stage) ?? STATUS_FALLBACK;
}
