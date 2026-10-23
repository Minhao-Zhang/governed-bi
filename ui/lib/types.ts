/** TypeScript types inferred from the zod schemas in `lib/schemas.ts`. */

import type { z } from "zod";
import type {
  answerViewSchema,
  capabilitiesSchema,
  exampleQuestionSchema,
  examplesResponseSchema,
  pendingClarificationRowSchema,
  pendingClarificationsSchema,
} from "./schemas";

export type Capabilities = z.infer<typeof capabilitiesSchema>;
export type AnswerView = z.infer<typeof answerViewSchema>;
export type PendingClarificationRow = z.infer<typeof pendingClarificationRowSchema>;
export type PendingClarifications = z.infer<typeof pendingClarificationsSchema>;
export type ExampleQuestion = z.infer<typeof exampleQuestionSchema>;
export type ExamplesResponse = z.infer<typeof examplesResponseSchema>;
