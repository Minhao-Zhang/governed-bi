"use client";

import { PlugZap } from "lucide-react";

import { MockChat } from "@/components/chat/mock-chat";
import { StreamChat } from "@/components/chat/stream-chat";
import { Skeleton } from "@/components/ui/skeleton";
import { useCapabilities } from "@/hooks/queries";
import { canStream } from "@/lib/capabilities";
import { LANGGRAPH_URL, USE_MOCKS } from "@/lib/env";

/**
 * The chat cockpit's transport selector. Each transport owns exactly one hook, so
 * we mount whichever container fits the environment (mounting different components
 * is fine — calling hooks conditionally is not):
 *
 *  - USE_MOCKS (no backend URL)      → <MockChat/>   (synthetic, keeps the banner)
 *  - backend + can_stream === true   → <StreamChat/> (useStream)
 *  - backend + can_stream === false  → <NoTransport/> (there is no chat here; say so)
 *
 * **The `POST /chat` fallback is gone**, with the engine route it called. There is now one
 * chat transport against a real engine, so `can_stream: false` is not a degraded mode — it
 * is an engine that cannot serve chat at all, and the third branch exists to say that
 * instead of rendering a composer that can never answer.
 *
 * `USE_MOCKS` is a build-time constant, so the early return never changes across
 * renders; the capabilities probe lives in its own child so its hook runs
 * unconditionally.
 */
export function ChatPanel() {
  if (USE_MOCKS) return <MockChat />;
  return <BackendChat />;
}

function BackendChat() {
  const { data: caps, isPending } = useCapabilities();

  if (isPending) return <ChatSkeleton />;
  if (!canStream(caps)) return <NoTransport />;
  return <StreamChat />;
}

/** Placeholder while `/capabilities` resolves — mirrors the composer footprint. */
function ChatSkeleton() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1" />
      <div className="border-t pt-4">
        <div className="mx-auto w-full max-w-5xl">
          <Skeleton className="h-9 w-full" />
        </div>
      </div>
    </div>
  );
}

/**
 * The attached backend cannot stream, so this app has no way to ask it anything.
 *
 * Deliberately not a composer: there is no transport behind it. The copy names the flag,
 * the address it was read from, and the fact that the read-only surfaces are unaffected —
 * everything a reader needs to tell "the engine is misconfigured" from "chat is broken".
 */
function NoTransport() {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-center gap-3 text-center">
        <PlugZap className="size-6 text-muted-foreground" aria-hidden />
        <p className="text-sm font-medium">Chat is not available on this backend</p>
        <p className="text-sm text-muted-foreground">
          The engine at{" "}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">{LANGGRAPH_URL}</code> reports{" "}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">can_stream: false</code>. Chat is
          served only over the LangGraph streaming runtime, and there is no non-streaming
          fallback, so no question can be answered from here.
        </p>
        <p className="text-sm text-muted-foreground">
          Point the UI at a server started with <code className="text-xs">langgraph dev</code>. The
          Schema, Corpus and Audit views read plain HTTP routes and still work.
        </p>
      </div>
    </div>
  );
}
