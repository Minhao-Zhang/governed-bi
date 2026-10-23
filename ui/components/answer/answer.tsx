"use client";

import { AnswerActions } from "@/components/answer/answer-actions";
import { Assumptions } from "@/components/answer/assumptions";
import { Refusal } from "@/components/answer/refusal";
import { ResultRows } from "@/components/answer/result-rows";
import { ModelMarkdown } from "@/components/common/model-markdown";
import { readAssumptions, shownTable } from "@/lib/answer-parts";
import type { AnswerView } from "@/lib/types";

export const CRASHED = "Something went wrong with this question. You can ask it again.";

/**
 * An answer, top to bottom. A refusal is the refusal block. The other unfinished
 * outcomes show the system text and nothing else.
 */
export function Answer({
  answer,
  question,
  send,
}: {
  answer: AnswerView;
  question: string;
  send: (text: string) => void;
}) {
  if (answer.outcome === "refused") {
    return <Refusal reason={answer.text} question={question} />;
  }
  if (answer.outcome !== "answered") {
    // A crash carries no system text, and a blank turn reads as nothing happened.
    const text =
      answer.text ?? answer.answer_text ?? (answer.outcome === "crashed" ? CRASHED : null);
    if (!text) return null;
    return (
      <div data-answer className="text-sm text-[var(--text)]">
        <p>{text}</p>
      </div>
    );
  }

  const text = answer.answer_text ?? null;
  const table = shownTable(answer.result_table);
  const assumptions = readAssumptions(answer.record?.assumptions);
  return (
    <div data-answer className="space-y-4 text-sm text-[var(--text)]">
      {text && <ModelMarkdown text={text} />}
      {table && <ResultRows table={table} />}
      <Assumptions assumptions={assumptions} question={question} send={send} />
      <AnswerActions text={text} table={table} record={answer.record ?? {}} />
    </div>
  );
}
