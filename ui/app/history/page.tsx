import { PageShell } from "@/components/layout/page-shell";
import { ConversationList } from "@/components/history/conversation-list";

/**
 * `/history` — every conversation the engine has kept. A Server Component shell around the
 * interactive <ConversationList> (which fetches through the LangGraph SDK on the client).
 *
 * Distinct from `/audit`, which lists **turns** — one row per question, with the record and the
 * governance ledger behind it. This lists **conversations**, and its rows lead back to the chat
 * rather than into a trace. Same server, two different questions: "what did this engine do" and
 * "where was I".
 */
export default function HistoryPage() {
  return (
    <PageShell
      title="History"
      description="Every conversation on this server. Open one to carry on where you left off."
    >
      <ConversationList />
    </PageShell>
  );
}
