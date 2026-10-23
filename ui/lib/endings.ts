/** How a conversation ended, in the words the sidebar and the Conversations page show. */

import type { Ending } from "@/lib/types";

export const ENDING_WORDS: Record<Ending, string> = {
  answered: "Answered",
  not_answered: "Couldn't answer",
  waiting: "Waiting for you",
  stopped: "Stopped",
  failed: "Failed",
};

export const ENDINGS = Object.keys(ENDING_WORDS) as Ending[];
