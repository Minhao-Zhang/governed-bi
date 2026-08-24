/**
 * A word-level diff between an asset field's current text and its replacement.
 *
 * **Why word-level and not line-level.** A corpus `summary` is one or two sentences on a single
 * line, so a line diff over it says "this line changed" — which is every character the reviewer
 * has to compare by eye, on the one screen where the whole decision is *whether the change is
 * right*. The unified diff in the bundle stays line-based, because that is what `git apply` reads.
 *
 * **Why this is honest rather than a guess.** The server enforces `was`: `corpus/patch.py`
 * refuses the edit if the field no longer holds it. So the left-hand side of this diff is the text
 * that is really there at apply time, and not a snapshot that may have moved. A renderer that
 * diffed against a stale copy would draw a change nobody is making.
 *
 * The algorithm is a plain LCS over whitespace-delimited tokens. No library, and none is needed:
 * the inputs are a sentence or two, so an O(n·m) table over ~40 tokens is nothing, and adding a
 * diff dependency for two fields is how a bundle grows 40 KB to render 12 words.
 *
 * Punctuation rides with its word (`order.` is one token). That is deliberate: a period moving is
 * a change a reviewer wants to see attached to the word it moved on, not as a lone `.` marker.
 */

export type DiffOp = "same" | "added" | "removed";

export interface DiffSpan {
  op: DiffOp;
  /** The tokens, re-joined with single spaces. Runs are merged so a render is one span per run. */
  text: string;
}

/** Split on whitespace, keeping nothing empty. Whitespace runs collapse, which is fine: YAML
 *  folded scalars re-wrap anyway, so a newline moving is not a change to the text. */
function tokenize(text: string): string[] {
  return text.split(/\s+/u).filter((token) => token.length > 0);
}

/**
 * The longest-common-subsequence table, as lengths.
 *
 * Row-major and full-size rather than the two-row trick, because the backtrack below needs the
 * whole table and these inputs are tiny.
 */
function lcsLengths(a: string[], b: string[]): number[][] {
  const table: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  return table;
}

/**
 * `was` → `becomes` as a list of runs.
 *
 * Removals are emitted before additions at the same position, so a reviewer reads "this went, that
 * came" in the order an edit is made rather than in an order the algorithm happened to produce.
 */
export function diffWords(was: string, becomes: string): DiffSpan[] {
  const a = tokenize(was);
  const b = tokenize(becomes);
  const table = lcsLengths(a, b);

  const ops: { op: DiffOp; token: string }[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      ops.push({ op: "same", token: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      ops.push({ op: "removed", token: a[i] });
      i += 1;
    } else {
      ops.push({ op: "added", token: b[j] });
      j += 1;
    }
  }
  while (i < a.length) {
    ops.push({ op: "removed", token: a[i] });
    i += 1;
  }
  while (j < b.length) {
    ops.push({ op: "added", token: b[j] });
    j += 1;
  }

  const spans: DiffSpan[] = [];
  for (const { op, token } of ops) {
    const last = spans[spans.length - 1];
    if (last && last.op === op) last.text = `${last.text} ${token}`;
    else spans.push({ op, text: token });
  }
  return spans;
}

/**
 * Words added and removed. What the surface states instead of "the summary changed".
 *
 * Counted in words rather than characters because a reviewer's question is how much of the meaning
 * moved, and "+11 −1 words" answers it where "+63 characters" does not.
 */
export function diffSize(spans: DiffSpan[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const span of spans) {
    const n = tokenize(span.text).length;
    if (span.op === "added") added += n;
    if (span.op === "removed") removed += n;
  }
  return { added, removed };
}

/** Which of the three things a replacement is. Only the third is a change. */
export type EditKind = "identical" | "whitespace_only" | "words_changed";

/**
 * Whether `becomes` changes anything, and if not, which way it fails to.
 *
 * **This lives beside the diff rather than in the draft form, because the caption and the submit
 * gate have to agree.** A form that decided submittability itself would hold a second definition of
 * "a word", and the two would drift: the diff says "+0 −0 words" while the button says the edit is
 * real, which is exactly the defect this replaces. One `tokenize`, one rule, both callers read it.
 *
 * A whitespace-only replacement is not submittable. Nothing a reader sees changes, nothing
 * retrieval indexes changes, and `corpus/patch.py::apply_edit` refuses part of the family outright
 * — it re-parses the text it wrote and raises `UnwritableValue` when the value does not read back
 * as given, which is what an added trailing newline or a doubled space inside a folded block does.
 * The rest of the family lands a commit no reader can tell from its parent. Neither is worth a
 * patch, and the difference between them is not worth a second sentence on the screen.
 */
export function classifyEdit(was: string, becomes: string): EditKind {
  if (was === becomes) return "identical";
  const a = tokenize(was);
  const b = tokenize(becomes);
  const sameWords = a.length === b.length && a.every((token, index) => token === b[index]);
  return sameWords ? "whitespace_only" : "words_changed";
}
