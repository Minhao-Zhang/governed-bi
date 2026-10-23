/**
 * Re-wraps the engine's one-line SQL for display. It only changes whitespace between tokens:
 * the SQL on screen is what a reviewer audits, so a rendering that differed from what ran
 * would be a correctness bug. `sameTokens` enforces that, and the original is shown otherwise.
 */

/**
 * Comments and strings match before words, so a keyword inside one is not a keyword. Double
 * quotes are identifiers. Punctuation matches one character at a time: a greedy run would
 * swallow `),` and lose the depth change with it.
 */
const SQL_TOKEN =
  /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*')|("(?:[^"]|"")*")|(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(<=|>=|<>|!=|\|\||::|[(),.;*=<>!+/-])/g;

type SqlTokenKind = "comment" | "string" | "identifier" | "number" | "word" | "punct" | "other";

interface SqlToken {
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
    // Skipped text that is not whitespace is still content, so no character is lost.
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

/** True when the two differ only in whitespace between tokens. */
export function sameTokens(a: string, b: string): boolean {
  const ta = tokenizeSql(a);
  const tb = tokenizeSql(b);
  if (ta.length !== tb.length) return false;
  return ta.every((t, i) => t.text === tb[i].text && t.kind === tb[i].kind);
}

const SQL_KEYWORDS = new Set([
  "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "AS", "ON", "JOIN", "INNER",
  "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "GROUP", "BY", "ORDER", "HAVING",
  "LIMIT", "OFFSET", "DISTINCT", "UNION", "ALL", "IN", "IS", "NULL", "LIKE",
  "BETWEEN", "CASE", "WHEN", "THEN", "ELSE", "END", "ASC", "DESC", "WITH",
  "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE", "EXISTS", "USING",
  "OVER", "PARTITION", "TRUE", "FALSE", "NATURAL", "EXCEPT", "INTERSECT",
  "WINDOW", "RETURNING", "FETCH", "ANY", "SOME", "FILTER", "WITHIN",
]);

/** Keywords that start a new line at the current nesting depth. */
const CLAUSE_STARTERS = new Set([
  "SELECT", "FROM", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET",
  "UNION", "EXCEPT", "INTERSECT", "WITH", "WINDOW", "RETURNING", "FETCH",
  "JOIN", "LEFT", "RIGHT", "INNER", "FULL", "CROSS", "NATURAL",
]);

/** The line already broke at the modifier, so `LEFT JOIN` stays on one line. */
const JOIN_MODIFIERS = new Set(["LEFT", "RIGHT", "INNER", "FULL", "CROSS", "OUTER", "NATURAL"]);

const CONNECTIVES = new Set(["AND", "OR"]);
const NO_SPACE_BEFORE = new Set([",", ")", ";", ".", "::"]);
const NO_SPACE_AFTER = new Set(["(", ".", "::"]);
const INDENT = "  ";

function isWord(t: SqlToken, word: string): boolean {
  return t.kind === "word" && t.text.toUpperCase() === word;
}

/** `IN (…)` takes a space and `COUNT(x)` does not. */
function spaceBeforeOpenParen(prev: SqlToken | undefined): boolean {
  if (!prev) return false;
  if (prev.kind === "word") return SQL_KEYWORDS.has(prev.text.toUpperCase());
  return prev.kind !== "punct";
}

/**
 * One clause per line, sub-selects indented, `AND`/`OR` on continuation lines. SQL that is
 * already multi-line or at most 60 characters is returned as it is.
 */
export function formatSql(sql: string): string {
  const original = sql;
  const trimmed = sql.trim();
  if (!trimmed) return original;
  if (trimmed.includes("\n")) return original;
  if (trimmed.length <= 60) return original;

  const tokens = tokenizeSql(trimmed);
  if (tokens.length === 0) return original;

  const lines: string[] = [];
  let current = "";
  let depth = 0;
  // Depth of each open select list, so commas break there and not in a function's arguments.
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
      if (next && isWord(next, "SELECT")) startLine();
      continue;
    }
    if (token.kind === "punct" && token.text === ")") {
      depth -= 1;
      while (selectDepths.length > 0 && selectDepths[selectDepths.length - 1] > depth) {
        selectDepths.pop();
      }
      append(")", { space: false });
      continue;
    }

    if (token.kind === "punct" && token.text === ",") {
      append(",", { space: false });
      if (selectDepths.length > 0 && selectDepths[selectDepths.length - 1] === depth) {
        startLine(1);
      }
      continue;
    }

    if (upper && CLAUSE_STARTERS.has(upper)) {
      const brokenByModifier =
        upper === "JOIN" && prev?.kind === "word" && JOIN_MODIFIERS.has(prev.text.toUpperCase());
      // `GROUP` and `ORDER` are clauses only before `BY`; a column may be named "order".
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
  if (!formatted || !sameTokens(original, formatted)) return original;
  return formatted;
}
