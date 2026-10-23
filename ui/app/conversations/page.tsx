import { ConversationTable } from "@/components/conversations/conversation-table";

export default function ConversationsPage() {
  return (
    <main className="h-full overflow-y-auto bg-white">
      <ConversationTable />
    </main>
  );
}
