"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, Inbox, PlugZap } from "lucide-react";

import { USE_MOCKS } from "@/lib/env";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Renders the right state for a React Query result: loading skeletons, an error
 * (with a "no backend attached" hint since this is a pure client), an empty
 * state, or the data. Keeps every surface's fetch handling consistent.
 */
export function QueryState<T>({
  query,
  isEmpty,
  emptyMessage = "Nothing to show yet.",
  skeleton,
  children,
}: {
  query: UseQueryResult<T>;
  isEmpty?: (data: T) => boolean;
  emptyMessage?: string;
  skeleton?: React.ReactNode;
  children: (data: T) => React.ReactNode;
}) {
  if (query.isPending) {
    return <>{skeleton ?? <DefaultSkeleton />}</>;
  }

  if (query.isError) {
    return (
      <StateBox
        icon={<AlertTriangle className="size-5 text-tier-refused" />}
        title="Couldn't load data"
        detail={query.error instanceof Error ? query.error.message : "Unknown error."}
      />
    );
  }

  const data = query.data as T;
  if (isEmpty?.(data)) {
    return (
      <StateBox
        icon={USE_MOCKS ? <PlugZap className="size-5" /> : <Inbox className="size-5" />}
        title={emptyMessage}
        detail={
          USE_MOCKS
            ? "No backend is attached. Set NEXT_PUBLIC_LANGGRAPH_URL to load live data."
            : undefined
        }
      />
    );
  }

  return <>{children(data)}</>;
}

function StateBox({
  icon,
  title,
  detail,
}: {
  icon: React.ReactNode;
  title: string;
  detail?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-16 text-center">
      <div className="text-muted-foreground">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      {detail && <p className="max-w-sm text-xs text-muted-foreground">{detail}</p>}
    </div>
  );
}

function DefaultSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}
