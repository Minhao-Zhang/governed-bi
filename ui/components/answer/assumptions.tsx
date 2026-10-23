"use client";

import { useState } from "react";

import { correctedAssumptionFollowUp } from "@/lib/assumption-follow-up";
import { layerSource, type Assumption } from "@/lib/answer-parts";

/** "To answer this, I assumed:" and one bullet per assumption. Nothing when the list is empty. */
export function Assumptions({
  assumptions,
  question,
  send,
}: {
  assumptions: Assumption[];
  question: string;
  send: (text: string) => void;
}) {
  if (assumptions.length === 0) return null;
  return (
    <div>
      <p>To answer this, I assumed:</p>
      <ul className="mt-1 list-disc space-y-1 pl-5">
        {assumptions.map((assumption, index) => (
          <AssumptionItem key={index} assumption={assumption} question={question} send={send} />
        ))}
      </ul>
    </div>
  );
}

function AssumptionItem({
  assumption,
  question,
  send,
}: {
  assumption: Assumption;
  question: string;
  send: (text: string) => void;
}) {
  return (
    <li data-basis={assumption.basis}>
      {assumption.statement}
      <AssumptionEnding assumption={assumption} question={question} send={send} />
    </li>
  );
}

function AssumptionEnding({
  assumption,
  question,
  send,
}: {
  assumption: Assumption;
  question: string;
  send: (text: string) => void;
}) {
  if (assumption.basis === "layer") {
    return <span className="text-[var(--muted)]"> {layerSource(assumption.asset_id)}</span>;
  }
  if (assumption.basis === "guess") {
    return (
      <>
        {" "}
        <span className="text-[var(--waiting)]">This is a guess.</span>{" "}
        <ChangeGuess statement={assumption.statement} question={question} send={send} />
      </>
    );
  }
  if (assumption.basis === "found_in_query") {
    return <span> The query does this, though you didn&apos;t ask for it.</span>;
  }
  return null;
}

function ChangeGuess({
  statement,
  question,
  send,
}: {
  statement: string;
  question: string;
  send: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button type="button" className="text-[var(--waiting)] underline" onClick={() => setOpen(true)}>
        Change
      </button>
    );
  }
  return <ChangeForm statement={statement} question={question} send={send} onDone={() => setOpen(false)} />;
}

function ChangeForm({
  statement,
  question,
  send,
  onDone,
}: {
  statement: string;
  question: string;
  send: (text: string) => void;
  onDone: () => void;
}) {
  const [draft, setDraft] = useState(statement);
  return (
    <form
      className="mt-1 flex gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const corrected = draft.trim();
        if (!corrected) return;
        send(correctedAssumptionFollowUp(question, corrected));
        onDone();
      }}
    >
      <input
        aria-label="Corrected assumption"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        className="w-full border-b border-[var(--muted)] bg-transparent text-sm text-[var(--text)] outline-none"
      />
      <button type="submit" className="text-[var(--muted)]">
        Send
      </button>
    </form>
  );
}
