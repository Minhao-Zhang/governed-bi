"use client";

import { PlugZap } from "lucide-react";

import { MockChat } from "@/components/chat/mock-chat";
import { StreamChat } from "@/components/chat/stream-chat";
import { Skeleton } from "@/components/ui/skeleton";
import { useCapabilities } from "@/hooks/queries";
import { canStream } from "@/lib/capabilities";
import { LANGGRAPH_URL, USE_MOCKS } from "@/lib/env";

/**
 * Picks the transport: <MockChat/> with no backend URL, <StreamChat/> when the backend reports
 * `can_stream`, else <NoTransport/>, since there is no non-streaming chat to fall back to.
 * `USE_MOCKS` is a build-time constant, so the early return never changes between renders.
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

/** The backend cannot stream, so there is no composer: nothing could answer it. */
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
          Point the UI at a server started with <code className="text-xs">langgraph dev</code>.
        </p>
      </div>
    </div>
  );
}
