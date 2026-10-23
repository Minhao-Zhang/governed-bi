"use client";

import { ModelMarkdown } from "@/components/common/model-markdown";
import { displayText } from "@/lib/answer-delivery";
import type { AnswerView } from "@/lib/types";

/** Plain text for now: a later milestone fills in what an answer contains. */
export function Answer({ answer }: { answer: AnswerView }) {
  const text = displayText(answer);
  if (!text) return null;
  return (
    <div data-answer className="text-[var(--text)]">
      <ModelMarkdown text={text} />
    </div>
  );
}
