"use client";

/**
 * One asset field, before and after, word by word.
 *
 * **Which field it is, stated on the row, because the two fields do different things.** `summary`
 * feeds the retrieval index and `body` feeds the model's prompt — so a change to `summary` is a
 * change to *what gets found* and a change to `body` is a change to *what the model reads*. A
 * reviewer deciding whether an edit fixes a coverage miss needs to know which one they are looking
 * at, and a diff that only showed the words would leave them guessing.
 *
 * **Colour is not the only signal.** Each run carries a `+`/`−` marker as well, because a
 * red/green diff is unreadable to a colour-blind reviewer and this is the screen where the
 * decision is made.
 *
 * **"+0 −0 words" is two different situations and they get two different sentences.** The replacement can
 * be the text already there, or it can differ only in whitespace. Both count zero words; only the
 * second is a value the steward typed and cannot submit. `classifyEdit` names which.
 */

import { Badge } from "@/components/ui/badge";
import { classifyEdit, diffSize, diffWords } from "@/lib/asset-diff";
import { FIELD_COPY, REVIEW_COPY } from "@/lib/review-copy";

export function AssetDiff({
  assetId,
  fieldPath,
  was,
  becomes,
}: {
  assetId: string;
  fieldPath: string;
  was: string;
  becomes: string;
}): React.JSX.Element {
  const spans = diffWords(was, becomes);
  const { added, removed } = diffSize(spans);
  const kind = classifyEdit(was, becomes);
  const field = FIELD_COPY[fieldPath];

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <code className="text-xs">{assetId}</code>
        <Badge variant="outline">{fieldPath}</Badge>
        <span className="text-xs text-muted-foreground">
          +{added} −{removed} {added + removed === 1 ? "word" : "words"}
        </span>
      </div>

      {field && <p className="text-xs text-muted-foreground">{field}</p>}

      <p className="rounded-lg border bg-muted/30 p-3 text-sm leading-relaxed">
        {spans.map((span, index) => {
          if (span.op === "same") return <span key={index}>{span.text} </span>;
          const added_ = span.op === "added";
          return (
            <span
              key={index}
              className={
                added_
                  ? "rounded bg-emerald-500/15 px-1 text-emerald-700 dark:text-emerald-300"
                  : "rounded bg-red-500/15 px-1 text-red-700 line-through dark:text-red-300"
              }
            >
              {added_ ? "+" : "−"}
              {span.text}{" "}
            </span>
          );
        })}
      </p>

      {kind === "identical" && (
        <p className="text-xs text-muted-foreground">{REVIEW_COPY.diffEmpty}</p>
      )}

      {kind === "whitespace_only" && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          {REVIEW_COPY.diffWhitespaceOnly}
        </p>
      )}
    </div>
  );
}
