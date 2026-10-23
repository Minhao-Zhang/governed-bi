/** TypeScript types inferred from the zod schemas in `lib/schemas.ts`. */

import type { z } from "zod";
import type {
  answerViewSchema,
  capabilitiesSchema,
  exampleQuestionSchema,
  examplesResponseSchema,
  conversationRowSchema,
  conversationsSchema,
  endingSchema,
} from "./schemas";

export type Capabilities = z.infer<typeof capabilitiesSchema>;
export type AnswerView = z.infer<typeof answerViewSchema>;
export type Ending = z.infer<typeof endingSchema>;
export type ConversationRow = z.infer<typeof conversationRowSchema>;
export type Conversations = z.infer<typeof conversationsSchema>;
export type ExampleQuestion = z.infer<typeof exampleQuestionSchema>;
export type ExamplesResponse = z.infer<typeof examplesResponseSchema>;
