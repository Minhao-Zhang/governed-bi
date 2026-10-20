import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";

// jsdom does not implement layout, so `scrollIntoView` is absent; <MessageList/> calls it on
// every new turn to follow the transcript.
Element.prototype.scrollIntoView ??= () => {};

/** Render with the providers the app's root layout supplies. */
export function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}
