import { cn } from "@/lib/utils";

/** Consistent page frame: a sticky header (title + optional actions) over a
 * padded content column. Every route uses this so the surfaces read as one app. */
export function PageShell({
  title,
  description,
  actions,
  fill,
  children,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  /** Hand the page's whole height to the content instead of letting it scroll.
   *
   * The default is a scrolling content column, which is right for a page you read top to bottom.
   * It is wrong for a page that is mostly one long table: the header and the table's own column
   * headers scroll away, and the reader loses the labels for the rows they are looking at. With
   * `fill`, the content column is exactly as tall as what is left and never scrolls — the child
   * owns a scroll box of its own (the table body) and is responsible for the `min-h-0` flex
   * chain down to it. `/corpus` and `/audit` use it. */
  fill?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col">
      {/* `flex-wrap` and a `min-w-0` title block because the actions slot is now used: a status
          control ~380px wide beside a 470px description has nothing to give on a phone, and
          without these the two just squeeze each other. Wrapped, the actions drop below the
          title instead. */}
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-6 py-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
        {/* `min-w-0` so a wide action can shrink: a flex item defaults to `min-width: auto`,
            which is its content's *max*-content width — so a 395px status control kept its full
            width on a 390px phone and hung off the right edge instead of wrapping. */}
        {actions && <div className="flex min-w-0 items-center gap-2">{actions}</div>}
      </header>
      {/* `min-h-0` in both branches: a flex child's default `min-height: auto` is its content's
          height, so an overlong table would push the column past the viewport instead of
          scrolling — the constraint has to be re-stated at every link of the chain. */}
      <div className={cn("min-h-0 flex-1 p-6", fill ? "flex flex-col overflow-hidden" : "overflow-y-auto")}>
        {children}
      </div>
    </div>
  );
}
