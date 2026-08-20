"use client";

import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

/**
 * Renders model-written text as markdown. The one place in the app that does.
 *
 * **This replaces a hand-rolled renderer, and the trade it makes is deliberate.** The
 * previous `Narration`/`Emphasised` pair in `answer/answer-card.tsx` supported four
 * constructs — paragraphs, bullets, `**bold**`, pipe tables — and argued that a general
 * renderer is "a dependency and a surface: this text comes from a model, so every construct
 * a renderer supports is a construct the model can emit". That argument was sound and its
 * premise stopped being true. It rested on the narrate prompt asking for one or two
 * sentences, and `serve/nodes/narrate.py` *adopts the agent's own last message* whenever the
 * turn was not capped — so the prompt's constraints never ran on the text the card shows.
 * What a reader actually saw was `##` and backticks rendered as literal characters in the
 * middle of a report the agent had structured with headings, code spans and sections.
 *
 * **What is still not rendered: raw HTML.** No `rehype-raw`, so `<script>`, `<iframe>` and
 * every other tag arrive as text — react-markdown's default, and the half of the old
 * argument that still holds. `urlTransform` is also left at its default, which strips
 * everything but http, https, mailto and relative URLs, so `javascript:` in a link is
 * dropped rather than clicked.
 *
 * **Links and images now render, which is new and is the accepted cost.** A model can put an
 * anchor or an `<img src>` on the most-read line of the app. Anchors carry
 * `rel="noreferrer nofollow"` and open in a new tab so they cannot reach back into this
 * origin, and that is mitigation, not prevention. Owner decision, 2026-08-19.
 */
export function ModelMarkdown({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2 text-sm leading-relaxed", className)}>
      <Markdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </Markdown>
    </div>
  );
}

/**
 * The element map. Every entry exists to keep a construct looking like the rest of the app
 * rather than like unstyled HTML — Tailwind's preflight removes list bullets, heading sizes
 * and table borders, so an unmapped element renders as indistinguishable body text.
 *
 * `...props` is spread on the table cells on purpose: `remark-gfm` puts a column's
 * alignment there as `style={{textAlign}}`, and dropping it would left-align every number
 * in a result the model wrote as right-aligned. The `text-left` class beside it is the
 * default for a column that declared no alignment — an inline style outranks a class, so
 * the two do not fight.
 */
const COMPONENTS: Components = {
  h1: ({ children }) => (
    <h2 className="pt-1 text-base font-semibold">{children}</h2>
  ),
  h2: ({ children }) => (
    <h3 className="pt-1 text-sm font-semibold">{children}</h3>
  ),
  h3: ({ children }) => (
    <h4 className="pt-1 text-sm font-semibold">{children}</h4>
  ),
  // A model that reaches h4 in an answer card is past the point where more sizes help.
  h4: ({ children }) => (
    <h5 className="pt-1 text-sm font-medium">{children}</h5>
  ),
  h5: ({ children }) => (
    <h6 className="pt-1 text-sm font-medium">{children}</h6>
  ),
  h6: ({ children }) => (
    <h6 className="pt-1 text-sm font-medium">{children}</h6>
  ),
  p: ({ children }) => <p>{children}</p>,
  ul: ({ children }) => (
    <ul className="list-disc space-y-1 pl-5">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal space-y-1 pl-5">{children}</ol>
  ),
  li: ({ children }) => <li>{children}</li>,
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer nofollow"
      className="underline underline-offset-2"
    >
      {children}
    </a>
  ),
  // Inline code and fenced blocks are one element in mdast; the `pre` below wraps the fenced
  // case, so this styles the inline one and steps aside when it is inside a block.
  code: ({ children, className: language }) =>
    language ? (
      <code className={cn("font-mono text-xs", language)}>{children}</code>
    ) : (
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
        {children}
      </code>
    ),
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 pl-3 text-muted-foreground">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-border" />,
  // The wrapper is what makes a wide table scroll instead of widening the card.
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-auto border-collapse text-sm tabular-nums">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => <thead>{children}</thead>,
  tr: ({ children }) => (
    <tr className="border-b last:border-0">{children}</tr>
  ),
  th: ({ children, ...props }) => (
    <th
      {...props}
      className="px-3 py-1.5 text-left font-medium text-muted-foreground"
    >
      {children}
    </th>
  ),
  td: ({ children, ...props }) => (
    <td {...props} className="px-3 py-1.5 text-left">
      {children}
    </td>
  ),
};
