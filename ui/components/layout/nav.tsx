"use client";

import { useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { Boxes, History, MenuIcon, MessagesSquare, MoonStar, Network, ScrollText, Settings, Sun } from "lucide-react";

import { cn } from "@/lib/utils";
import { useCapabilities } from "@/hooks/queries";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

// `/health` was here and is deleted: it and `/corpus`'s state header were fed by two routes
// projecting the same session fields, and the health page's only distinctive content was three
// counters hardcoded to zero. See `components/corpus/corpus-status.tsx`.
//
// `/history` sits directly under Chat because that is the pair: it lists conversations and its
// rows lead straight back into one. It was a popover on the chat page, which put an unbounded
// list inside a menu-sized panel — seventeen rows covered the answer and the composer both.
const LINKS = [
  { href: "/", label: "Chat", icon: MessagesSquare },
  { href: "/history", label: "History", icon: History },
  { href: "/schema", label: "Schema", icon: Network },
  { href: "/corpus", label: "Corpus", icon: Boxes },
  { href: "/audit", label: "Audit", icon: ScrollText },
  // Last, and read-only: it answers "what is this engine running on", which is a question you
  // ask about a run you are already looking at, not a place you start.
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

/** The shared link list, reused by the desktop rail and the mobile sheet. */
function NavLinks({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <>
      {LINKS.map(({ href, label, icon: Icon }) => (
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

/**
 * Bottom of the rail: **whether the engine is reachable, and nothing else.**
 *
 * It used to also carry `environment · dialect` and the model id. Both moved to `/settings`,
 * where there is room to say what they mean — the model id in particular was truncated to
 * `us.anthropic.claude-sonnet…` at this width, which is the shape of a value nobody can act on.
 *
 * **The dot now keys on reachability, not on `has_live_model`.** That was the older bug hiding
 * in the same nine lines: a server that was answering perfectly well but had no model attached
 * showed the same grey dot as a server that was down, so the one thing a status light exists to
 * tell you apart was the one thing it could not. `isSuccess` means `/capabilities` parsed;
 * `isError` means it did not; neither is a claim about the model.
 */
function CapabilitiesStrip() {
  const { isSuccess, isError } = useCapabilities();
  const state = isSuccess ? "connected" : isError ? "disconnected" : "connecting…";

  return (
    <div className="border-t p-3 text-xs text-muted-foreground">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-2 rounded-full",
            isSuccess && "bg-tier-governed",
            isError && "bg-destructive",
            !isSuccess && !isError && "bg-muted-foreground/50 animate-pulse",
          )}
          aria-hidden
        />
        {/* `role="status"`: the connection state changes without a navigation, so a screen
            reader should hear it when it flips rather than only on request. */}
        <span className="truncate" role="status">
          {state}
        </span>
      </div>
    </div>
  );
}
