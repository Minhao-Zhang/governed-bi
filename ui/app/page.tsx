import { ChatPanel } from "@/components/chat/chat-panel";
import { PageShell } from "@/components/layout/page-shell";

/**
 * Chat — the default route ("/"). A pure read/audit cockpit over the governed-bi
 * serve pipeline. PageShell (a Server Component) frames the page; ChatPanel is
 * the client boundary that owns chat state and the mock transport.
 */
export default function ChatPage() {
  return (
    <PageShell
      title="Chat"
      description="Ask a question and audit the governed answer — reliability, SQL, result, and provenance."
    >
      <ChatPanel />
    </PageShell>
  );
}
