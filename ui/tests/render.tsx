import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";
import { setDisplayMode, type DisplayMode } from "@/lib/display-mode";

// jsdom does not implement layout, so `scrollIntoView` is absent; <MessageList/> calls it on
// every new turn to follow the transcript.
Element.prototype.scrollIntoView ??= () => {};

/**
 * Render with the providers the app's root layout supplies, in one display mode. The mode is
 * explicit on every call because it decides what a card shows, and a test that inherited the
 * previous test's mode from `localStorage` would pass or fail by file order.
 */
export function renderWithProviders(ui: ReactElement, mode: DisplayMode = "engineer") {
  setDisplayMode(mode);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}
