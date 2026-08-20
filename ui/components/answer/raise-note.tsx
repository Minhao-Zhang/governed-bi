"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api-client";

export function RaiseNote({
  turnId,
  kind,
}: {
  turnId: string;
  kind: "from_refusal" | "wrong_answer";
}) {
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = kind === "from_refusal" ? "This refusal looks wrong" : "This answer is wrong";

  if (sent) {
    return <p className="text-xs text-muted-foreground">Filed. It is on the pending list.</p>;
  }

  return (
    <form
      className="space-y-2 border-t pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        setError(null);
        void api
          .raiseTurn(turnId, { kind, note: note.trim() || undefined })
          .then(() => setSent(true))
          .catch((err: unknown) =>
            setError(err instanceof Error ? err.message : "Could not file the note."),
          );
      }}
    >
      <textarea
        value={note}
        onChange={(event) => setNote(event.target.value)}
        rows={2}
        placeholder="Optional note"
        aria-label={label}
        className="flex max-h-32 min-h-8 w-full resize-y rounded-md border border-input bg-background px-2.5 py-1.5 text-sm outline-none"
      />
      <Button type="submit" variant="outline" size="sm">
        {label}
      </Button>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </form>
  );
}
