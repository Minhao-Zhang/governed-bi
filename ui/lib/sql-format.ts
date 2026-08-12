/**
 * SQL tokenizing + display formatting.
 *
 * The engine returns executed SQL as a single line. That is the right thing on the
 * wire — it is an audit artifact, and `provenance` / the governance ledger record
 * it verbatim — but it is hard to read in the answer card once there are joins and
 * a few predicates. This module re-wraps it for display only.
 *
 * **The formatter only ever changes whitespace between tokens.** It never rewrites,
 * reorders, uppercases, or re-quotes anything. That is not a style preference: the
 * SQL on screen is what a reviewer audits, so a "prettier" rendering that differed
 * from what actually ran would be a correctness bug, not a cosmetic one.
 * `sameTokens()` below encodes that invariant so it can be checked.
 *
 * The tokenizer is shared with the syntax highlighter in `sql-block.tsx` — one
 * definition of what counts as a string / quoted identifier / comment, so the two
 * can never disagree about where a literal ends. That matters most for the
 * obfuscated corpora, whose identifiers are quoted and non-English.
 */

/**
 * Order matters: comments and strings are matched before words so a keyword inside
 * a string or comment is never treated as one. Double-quoted tokens are SQL
 * identifiers, not strings. `''` / `""` escapes are consumed as part of the token.
 *
 * Punctuation matches multi-character operators first, then **one character at a
 * time** — deliberately not a greedy `[...]+` run. A run would swallow `),` into a
 * single token, which loses the paren-depth transition and the list separator with
 * it. (The highlighter used a greedy run; it only got away with it because coloring
 * a whole run the same way looks identical.)
 */
export const SQL_TOKEN =
  /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*')|("(?:[^"]|"")*")|(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(<=|>=|<>|!=|\|\||::|[(),.;*=<>!+/-])/g;

export type SqlTokenKind =
  | "comment"
  | "string"
  | "identifier"
  | "number"
  | "word"
  | "punct"
  | "other";

export interface SqlToken {
  text: string;
  kind: SqlTokenKind;
}

/** Split SQL into tokens, dropping the whitespace between them. */
export function tokenizeSql(sql: string): SqlToken[] {
  const tokens: SqlToken[] = [];
  let last = 0;
  SQL_TOKEN.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = SQL_TOKEN.exec(sql)) !== null) {
    // Anything the regex skipped that isn't whitespace is still content — keep it
    // so a token stream never silently loses characters.
    const gap = sql.slice(last, m.index).trim();
    if (gap) tokens.push({ text: gap, kind: "other" });
    const [full, comment, str, dquote, num, word, punct] = m;
    const kind: SqlTokenKind = comment
      ? "comment"
      : str
        ? "string"
        : dquote
          ? "identifier"
          : num
            ? "number"
            : word
              ? "word"
              : punct
                ? "punct"
                : "other";
    tokens.push({ text: full, kind });
    last = m.index + full.length;
  }
  const tail = sql.slice(last).trim();
  if (tail) tokens.push({ text: tail, kind: "other" });
  return tokens;
}

/**
 * Do two SQL strings carry the identical token stream? True ⇒ they differ only in
 * whitespace between tokens. This is the formatter's safety net: if a format pass
 * ever changed anything that matters, this returns false and the caller shows the
 * original instead.
 */
export function sameTokens(a: string, b: string): boolean {
  const ta = tokenizeSql(a);
  const tb = tokenizeSql(b);
  if (ta.length !== tb.length) return false;
  return ta.every((t, i) => t.text === tb[i].text && t.kind === tb[i].kind);
}

/* ── Shared vocabulary (highlighter + formatter) ─────────────────────────── */

export const SQL_KEYWORDS = new Set([
  "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "AS", "ON", "JOIN", "INNER",
  "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "GROUP", "BY", "ORDER", "HAVING",
  "LIMIT", "OFFSET", "DISTINCT", "UNION", "ALL", "IN", "IS", "NULL", "LIKE",
  "BETWEEN", "CASE", "WHEN", "THEN", "ELSE", "END", "ASC", "DESC", "WITH",
  "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE", "EXISTS", "USING",
  "OVER", "PARTITION", "TRUE", "FALSE", "NATURAL", "EXCEPT", "INTERSECT",
  "WINDOW", "RETURNING", "FETCH", "ANY", "SOME", "FILTER", "WITHIN",
]);

export const SQL_FUNCTIONS = new Set([
  "AVG", "COUNT", "SUM", "MIN", "MAX", "ROUND", "COALESCE", "CAST", "ABS",
  "LOWER", "UPPER", "LENGTH", "NOW", "DATE", "SUBSTR", "SUBSTRING", "TRIM",
  "CONCAT", "IFNULL", "NULLIF",
]);

/* ── Clause layout ───────────────────────────────────────────────────────── */

/** Keywords that start a new line at the current nesting depth. */
const CLAUSE_STARTERS = new Set([
  "SELECT", "FROM", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET",
  "UNION", "EXCEPT", "INTERSECT", "WITH", "WINDOW", "RETURNING", "FETCH",
  "JOIN", "LEFT", "RIGHT", "INNER", "FULL", "CROSS", "NATURAL",
]);

/**
 * A join modifier: when one of these immediately precedes JOIN, the line break
 * already happened at the modifier, so JOIN must not break again — otherwise
 * `LEFT JOIN` renders across two lines.
 */
const JOIN_MODIFIERS = new Set(["LEFT", "RIGHT", "INNER", "FULL", "CROSS", "OUTER", "NATURAL"]);

/** Boolean connectives get their own continuation line inside a predicate. */
const CONNECTIVES = new Set(["AND", "OR"]);

/** Tokens that must not have a space before them. */
const NO_SPACE_BEFORE = new Set([",", ")", ";", ".", "::"]);

/** Tokens after which a space would read wrong. */
const NO_SPACE_AFTER = new Set(["(", ".", "::"]);

const INDENT = "  ";

function isWord(t: SqlToken, word: string): boolean {
  return t.kind === "word" && t.text.toUpperCase() === word;
}

/**
 * Should a space precede an opening paren? Yes after a keyword (`IN (…)`,
 * `OVER (…)`, `VALUES (…)`), no after a name — `COUNT(x)` is a call, and
 * `COUNT (x)` reads as a typo even though it parses the same.
 */
function spaceBeforeOpenParen(prev: SqlToken | undefined): boolean {
  if (!prev) return false;
  if (prev.kind === "word") return SQL_KEYWORDS.has(prev.text.toUpperCase());
  // After `(`, `.` or another operator, never; after a literal, sure.
  return prev.kind !== "punct";
}

/**
 * Re-wrap single-line SQL across lines: one clause per line, nested
 * parenthesised sub-selects indented, boolean connectives on continuation lines.
 *
 * Returns the input unchanged when it is already multi-line (the engine, or a
 * human, already chose a layout — don't fight it), when it is trivially short, or
 * when the result would somehow not tokenize identically to the input.
 */
export function formatSql(sql: string): string {
  const original = sql;
  const trimmed = sql.trim();
  if (!trimmed) return original;
  // Already laid out, or short enough that one line is easier to read.
  if (trimmed.includes("\n")) return original;
  if (trimmed.length <= 60) return original;

  const tokens = tokenizeSql(trimmed);
  if (tokens.length === 0) return original;

  const lines: string[] = [];
  let current = "";
  let depth = 0;
  // Nesting depth of the innermost SELECT list, so commas break only in a select
  // list and not inside a function's argument list.
  const selectDepths: number[] = [];

  const flush = () => {
    if (current.trim()) lines.push(current.replace(/\s+$/, ""));
    current = "";
  };
  const startLine = (extraIndent = 0) => {
    flush();
    current = INDENT.repeat(Math.max(0, depth + extraIndent));
  };
  const append = (text: string, { space = true }: { space?: boolean } = {}) => {
    if (current === "") current = INDENT.repeat(Math.max(0, depth));
    const needsSpace =
      space &&
      current.trim() !== "" &&
      !current.endsWith(" ") &&
      !NO_SPACE_AFTER.has(current.trimEnd().slice(-1));
    current += (needsSpace ? " " : "") + text;
  };

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    const prev = tokens[i - 1];
    const next = tokens[i + 1];
    const upper = token.kind === "word" ? token.text.toUpperCase() : "";

    if (token.kind === "punct" && token.text === "(") {
      append("(", { space: spaceBeforeOpenParen(prev) });
      depth += 1;
      // A parenthesised sub-select becomes its own indented block.
      if (next && isWord(next, "SELECT")) startLine();
      continue;
    }
    if (token.kind === "punct" && token.text === ")") {
      depth -= 1;
      // Leaving this nesting level ends any select list opened inside it.
      while (selectDepths.length > 0 && selectDepths[selectDepths.length - 1] > depth) {
        selectDepths.pop();
      }
      append(")", { space: false });
      continue;
    }

    if (token.kind === "punct" && token.text === ",") {
      append(",", { space: false });
      // Break a select / group / order list one item per line, but leave function
      // arguments and row constructors alone.
      if (selectDepths.length > 0 && selectDepths[selectDepths.length - 1] === depth) {
        startLine(1);
      }
      continue;
    }

    if (upper && CLAUSE_STARTERS.has(upper)) {
      // `LEFT JOIN` etc. already broke at the modifier.
      const brokenByModifier =
        upper === "JOIN" && prev?.kind === "word" && JOIN_MODIFIERS.has(prev.text.toUpperCase());
      // `GROUP`/`ORDER` are only clauses when followed by BY (vs. a column named
      // "order"); the parser is not that deep, but this check is cheap and right.
      const needsBy = upper === "GROUP" || upper === "ORDER";
      const isClause = !brokenByModifier && (!needsBy || (next != null && isWord(next, "BY")));
      if (isClause) {
        startLine();
        if (upper === "SELECT") selectDepths.push(depth);
      }
      append(token.text);
      continue;
    }

    if (upper && CONNECTIVES.has(upper)) {
      startLine(1);
      append(token.text);
      continue;
    }

    append(token.text, { space: !NO_SPACE_BEFORE.has(token.text) });
  }
  flush();

  const formatted = lines.join("\n").trim();
  // The invariant, enforced rather than asserted: if the rewrite changed anything
  // beyond whitespace, discard it. Showing awkward SQL beats showing SQL that is
  // not what ran.
  if (!formatted || !sameTokens(original, formatted)) return original;
  return formatted;
}
