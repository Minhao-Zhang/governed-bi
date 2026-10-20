import { ChatPanel } from "@/components/chat/chat-panel";
import { PageShell } from "@/components/layout/page-shell";

/** The only page. ChatPanel is the client boundary that owns chat state and the transport. */
export default function ChatPage() {
  return (
    <PageShell
      title="Chat"
      description="Ask a question about your data."
    >
      <ChatPanel />
    </PageShell>
  );
}
