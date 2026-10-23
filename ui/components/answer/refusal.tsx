"use client";

import { useComposerDraft } from "@/components/chat/composer-draft";
import { useExamples } from "@/hooks/queries";

/** A refused turn: the sentence, the reason, and nearby questions the layer can answer. */
export function Refusal({ reason, question }: { reason: string | null; question: string }) {
  const { setDraft } = useComposerDraft();
  const { data, isError } = useExamples({ limit: 3, near: question });
  const nearby = !isError && data ? data.examples.slice(0, 3) : [];

  return (
    <div data-refusal className="space-y-2 text-sm text-[var(--text)]">
      <p>I can&apos;t answer this from your data.</p>
      {reason ? <p className="text-[var(--muted)]">{reason}</p> : null}
      {nearby.length > 0 && (
        <ul className="space-y-1">
          {nearby.map((example) => (
            <li key={`${example.db_id}\n${example.question}`}>
              <a
                href={`#${encodeURIComponent(example.question)}`}
                className="text-[var(--muted)]"
                onClick={(event) => {
                  event.preventDefault();
                  setDraft(example.question);
                }}
              >
                {example.question}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
