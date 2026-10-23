"use client";

import { useComposerDraft } from "@/components/chat/composer-draft";
import { useExamples } from "@/hooks/queries";

/** Centred prompt when the column has no messages yet. */
export function EmptyState() {
  return (
    <div className="flex h-full items-center justify-center">
      <p>What do you want to know?</p>
    </div>
  );
}

/**
 * Three example questions in grey under the composer. A failed request, or an
 * empty list, leaves this blank. Clicking one fills the composer.
 */
export function ExampleQuestions() {
  const { setDraft } = useComposerDraft();
  const { data, isError } = useExamples({ limit: 3 });
  const examples = !isError && data ? data.examples.slice(0, 3) : [];
  if (examples.length === 0) return null;

  return (
    <ul className="mt-3 space-y-1">
      {examples.map((example) => (
        <li key={`${example.db_id}\n${example.question}`}>
          <a
            href={`#${encodeURIComponent(example.question)}`}
            className="text-sm text-[var(--muted)]"
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
  );
}
