import { ChatPanel } from "@/components/chat/chat-panel";
import { Sidebar } from "@/components/sidebar/sidebar";

/** Chat: a sidebar, then the conversation. */
export default function ChatPage() {
  return (
    <div className="flex h-full min-h-0 bg-white">
      <Sidebar />
      <main className="min-h-0 min-w-0 flex-1">
        <ChatPanel />
      </main>
    </div>
  );
}
