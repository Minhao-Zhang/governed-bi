"use client";

import { MockChat } from "@/components/chat/mock-chat";
import { RestChat } from "@/components/chat/rest-chat";
import { StreamChat } from "@/components/chat/stream-chat";
import { Skeleton } from "@/components/ui/skeleton";
import { useCapabilities } from "@/hooks/queries";
import { canStream } from "@/lib/capabilities";
import { USE_MOCKS } from "@/lib/env";

/**
 * The chat cockpit's transport selector. Each transport owns exactly one hook, so
 * we mount whichever container fits the environment (mounting different components
 * is fine — calling hooks conditionally is not):
 *
 *  - USE_MOCKS (no backend URL)      → <MockChat/>   (synthetic, keeps the banner)
 *  - backend + can_stream === true   → <StreamChat/> (useStream)
 *  - backend + can_stream === false  → <RestChat/>   (POST /chat fallback)
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
  return canStream(caps) ? <StreamChat /> : <RestChat />;
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
