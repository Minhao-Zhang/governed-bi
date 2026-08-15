"use client";

import { useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import {
  Boxes,
  History,
  MenuIcon,
  MessagesSquare,
  MoonStar,
  Network,
  ScrollText,
  Settings2,
  Sun,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useCapabilities } from "@/hooks/queries";
import { resolveTier, tierReaches } from "@/lib/capabilities";
import { useDisplayModeOverride } from "@/lib/display-mode";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

// `/health` was here and is deleted: it and `/corpus`'s state header were fed by two routes
// projecting the same session fields, and the health page's only distinctive content was three
// counters hardcoded to zero. See `components/corpus/corpus-status.tsx`.
//
// `/history` sits directly under Chat because that is the pair: it lists conversations and its
// rows lead straight back into one. It was a popover on the chat page, which put an unbounded
// list inside a menu-sized panel — seventeen rows covered the answer and the composer both.
//
// **Which of these a visitor sees depends on their tier**, and `lib/capabilities.ts::tierReaches`
// owns that rule — not this file. The list here is data; the filter is one expression over it, so
// an upstream change to the rail's markup and a change to who sees what cannot conflict.
//
// `/settings` is last and reachable at every tier: a tier that could not get back out of itself
// would be a trap.
const LINKS = [
  { href: "/", label: "Chat", icon: MessagesSquare },
  { href: "/history", label: "History", icon: History },
  { href: "/schema", label: "Schema", icon: Network },
  { href: "/corpus", label: "Corpus", icon: Boxes },
  { href: "/audit", label: "Audit", icon: ScrollText },
  { href: "/settings", label: "Settings", icon: Settings2 },
] as const;

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

/** The shared link list, reused by the desktop rail and the mobile sheet, filtered to the tier. */
function NavLinks({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const { data: caps } = useCapabilities();
  const tier = resolveTier(caps, useDisplayModeOverride());

  return (
    <>
      {LINKS.filter(({ href }) => tierReaches(tier, href)).map(({ href, label, icon: Icon }) => (
        <Link
          key={href}
          href={href}
          onClick={onNavigate}
          aria-current={isActive(pathname, href) ? "page" : undefined}
          className={cn(
            "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
            isActive(pathname, href)
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
          )}
        >
          <Icon className="size-4" />
          {label}
        </Link>
      ))}
    </>
  );
}

/** Left navigation rail (desktop, ≥lg) + a small backend-capabilities strip. */
export function Nav() {
  const pathname = usePathname();

  return (
    <aside className="hidden h-full w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground lg:flex">
      <div className="flex h-14 items-center justify-between gap-2 border-b px-4">
        <span className="font-mono text-sm font-semibold tracking-tight">governed-bi</span>
        <ThemeToggle />
      </div>

      <nav className="flex-1 space-y-1 p-2">
        <NavLinks pathname={pathname} />
      </nav>

      <CapabilitiesStrip />
    </aside>
  );
}

/** Mobile top bar (below lg): brand + theme toggle + a hamburger that opens the
 * navigation in a left sheet. Keeps the content column full-width on phones,
 * where the fixed 224px rail would otherwise swallow most of the viewport. */
export function MobileNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b bg-sidebar px-3 text-sidebar-foreground lg:hidden">
      <div className="flex items-center gap-1">
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label="Open navigation">
              <MenuIcon className="size-5" />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-64 p-0">
            <SheetTitle className="flex h-14 items-center border-b px-4 font-mono text-sm font-semibold tracking-tight">
              governed-bi
            </SheetTitle>
            <nav className="flex-1 space-y-1 p-2">
              <NavLinks pathname={pathname} onNavigate={() => setOpen(false)} />
            </nav>
            <CapabilitiesStrip />
          </SheetContent>
        </Sheet>
        <span className="font-mono text-sm font-semibold tracking-tight">governed-bi</span>
      </div>
      <ThemeToggle />
    </header>
  );
}

// The Simple/Audit eye button used to sit in this header. It is gone, replaced by `/settings`:
// it was a two-state control with no label, next to the theme toggle, changing what an entire
// application shows — and there was no way for it to say what either state meant. A role is not a
// display preference. See `docs/utkuai-role-tiers-and-clarification-cancel.md`.

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  // Client-only: avoid hydration mismatch on the theme icon without an effect.
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );

  const isDark = resolvedTheme === "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      title={isDark ? "Light theme" : "Dark theme"}
      className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      {/* Render a stable icon until mounted to avoid a hydration mismatch. */}
      {mounted && isDark ? <Sun className="size-4" /> : <MoonStar className="size-4" />}
    </button>
  );
}

function CapabilitiesStrip() {
  const { data: caps } = useCapabilities();
  const tier = resolveTier(caps, useDisplayModeOverride());

  return (
    <div className="border-t p-3 text-xs text-muted-foreground">
      {/* The tier is named here because the rail above it is filtered by it. A visitor who cannot
          find Corpus should be able to see why without opening Settings to find out. */}
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-medium capitalize text-foreground">{tier}</span>
        <Link href="/settings" className="underline underline-offset-2 hover:text-foreground">
          change
        </Link>
      </div>
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-2 rounded-full",
            caps?.has_live_model ? "bg-tier-governed" : "bg-muted-foreground/50",
          )}
          aria-hidden
        />
        <span className="truncate">
          {caps ? `${caps.environment} · ${caps.dialect}` : "connecting…"}
        </span>
      </div>
      <div className="mt-1 truncate font-mono">{caps?.model ?? ""}</div>
    </div>
  );
}
