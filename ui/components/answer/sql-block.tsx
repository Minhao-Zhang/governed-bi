"use client";

import { useId, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useSqlFormatEnabled } from "@/hooks/use-sql-format";
import {
  SQL_FUNCTIONS,
  SQL_KEYWORDS,
  SQL_TOKEN,
  formatSql,
} from "@/lib/sql-format";

/**
 * Read-only SQL, monospace + lightly syntax-highlighted. The engine owns the write
 * path; this only displays the SQL the answer executed.
 *
 * The engine returns it as one line. A "Format" switch re-wraps it across lines for
 * reading — whitespace only, verified token-identical to what ran (see
 * `lib/sql-format.ts`) — and the switch is shared by every block on the page and
 * remembered across reloads. Turn it off to read the statement exactly as the engine
 * emitted it.
 */
export function SqlBlock({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false);
  const [formatEnabled, setFormatEnabled] = useSqlFormatEnabled();
  const switchId = useId();

  const formatted = formatSql(sql);
  // `formatSql` returns its input untouched when there is nothing to gain (already
  // multi-line, or short). Hide the switch then, rather than offer a control that
  // visibly does nothing.
  const canFormat = formatted !== sql;
  const shown = formatEnabled && canFormat ? formatted : sql;

  async function copy() {
    try {
      // Copy what's on screen. The formatted text is token-identical to the
      // original, so it runs the same query — and handing over different characters
      // than the ones being read would be the surprising choice.
      await navigator.clipboard.writeText(shown);
      setCopied(true);
      toast.success("SQL copied");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy to clipboard");
    }
  }

  return (
    <div className="relative rounded-md border bg-muted/40">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-1.5">
        <span className="text-xs font-medium text-muted-foreground">SQL</span>
        <div className="flex items-center gap-3">
          {canFormat && (
            <div className="flex items-center gap-1.5">
              <label
                htmlFor={switchId}
                className="cursor-pointer text-xs text-muted-foreground select-none"
              >
                Format
              </label>
              <Switch
                id={switchId}
                checked={formatEnabled}
                onCheckedChange={setFormatEnabled}
                aria-label="Format SQL across multiple lines"
              />
            </div>
          )}
          <Button variant="ghost" size="sm" className="h-6 gap-1 px-2 text-xs" onClick={copy}>
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            Copy
          </Button>
        </div>
      </div>
      <pre className="overflow-x-auto p-3 text-xs leading-relaxed">
        <code className="font-mono">{highlightSql(shown)}</code>
      </pre>
    </div>
  );
}

/* ── Minimal SQL syntax highlighter ───────────────────────────────────────── */

/**
 * Colorize using the same tokenizer the formatter uses (`lib/sql-format.ts`), so the
 * two can never disagree about where a string or quoted identifier ends.
 */
function highlightSql(sql: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  const push = (text: string, cls?: string) => {
    if (!text) return;
    out.push(
      cls ? (
        <span key={key++} className={cls}>
          {text}
        </span>
      ) : (
        <span key={key++}>{text}</span>
      ),
    );
  };

  SQL_TOKEN.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = SQL_TOKEN.exec(sql)) !== null) {
    if (m.index > last) push(sql.slice(last, m.index)); // whitespace / unmatched
    const [full, comment, str, dquote, num, word, punct] = m;
    // Code palette deliberately avoids the reliability hues (green/amber/red) so
    // "color = trust" holds app-wide; shades are AA-safe on the muted code bg.
    if (comment) push(full, "text-muted-foreground italic");
    else if (str) push(full, "text-cyan-700 dark:text-cyan-300");
    else if (dquote) push(full, "text-foreground");
    else if (num) push(full); // numbers stay default — no tier-hue collision
    else if (word) {
      const upper = word.toUpperCase();
      if (SQL_KEYWORDS.has(upper)) push(full, "font-medium text-blue-700 dark:text-blue-300");
      else if (SQL_FUNCTIONS.has(upper)) push(full, "text-violet-700 dark:text-violet-300");
      else push(full);
    } else if (punct) push(full, "text-muted-foreground");
    else push(full);
    last = m.index + full.length;
  }
  if (last < sql.length) push(sql.slice(last));
  return out;
}
