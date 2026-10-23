/** The follow-up keeps the question and states the corrected assumption in it. */
export function correctedAssumptionFollowUp(question: string, corrected: string): string {
  return `${question} (Assume instead: ${corrected})`;
}
