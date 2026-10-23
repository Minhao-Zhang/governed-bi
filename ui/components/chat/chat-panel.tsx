"use client";

import { MockChat } from "@/components/chat/mock-chat";
import { StreamChat } from "@/components/chat/stream-chat";
import { Skeleton } from "@/components/ui/skeleton";
import { useCapabilities } from "@/hooks/queries";
import { canStream } from "@/lib/capabilities";
import { LANGGRAPH_URL, USE_MOCKS } from "@/lib/env";

/**
 * Picks the transport: MockChat with no backend URL, StreamChat when the backend
 * reports `can_stream`, otherwise a notice. There is no non-streaming chat.
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

/** Placeholder while `/capabilities` resolves. */
function ChatSkeleton() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1" />
      <div className="mx-auto w-full max-w-[720px] px-4 pb-4">
        <Skeleton className="h-12 w-full rounded-3xl" />
      </div>
    </div>
  );
}

/** The backend cannot stream, so there is no composer. */
function NoTransport() {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center text-[var(--text)]">
      <div className="max-w-md space-y-3">
        <p className="text-sm">Chat is not available on this backend</p>
        <p className="text-sm text-[var(--muted)]">
          The engine at <code className="text-xs">{LANGGRAPH_URL}</code> reports{" "}
          <code className="text-xs">can_stream: false</code>. Chat is served only over the
          LangGraph streaming runtime, and there is no non-streaming fallback, so no question
          can be answered from here.
        </p>
        <p className="text-sm text-[var(--muted)]">
          Point the UI at a server started with <code className="text-xs">langgraph dev</code>.
        </p>
      </div>
    </div>
  );
}
