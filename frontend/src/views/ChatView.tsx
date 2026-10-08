import { MessageCircle } from "lucide-react";
import { Card } from "../components/ui";
import ChatPanel from "../components/chat/ChatPanel";

export default function ChatView() {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th flex items-center gap-2">
            <MessageCircle size={20} className="text-accent" /> AI Assistant
          </h1>
          <p className="mt-1 text-sm text-muted">
            Ask about alerts, camera status, SOPs, or system analytics
          </p>
        </div>
      </div>

      <Card className="p-0 overflow-hidden">
        <div className="border-b border-border bg-base px-5 py-4">
          <h2 className="text-base font-semibold text-th flex items-center gap-2">
            <MessageCircle size={16} className="text-accent" />
            Chat with Vigil Assistant
          </h2>
          <p className="mt-1 text-xs text-faint">
            Real-time insights powered by AI • Connected to your system data
          </p>
        </div>
        <div className="p-4">
          <ChatPanel />
        </div>
      </Card>

      <Card className="p-5">
        <h3 className="text-sm font-semibold text-th mb-3">What can I help you with?</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border border-border bg-base p-3">
            <p className="text-xs font-medium text-tx mb-1">📊 Alert Analytics</p>
            <p className="text-[11px] text-faint">
              "How many critical alerts today?" • "Which camera has most alerts?"
            </p>
          </div>
          <div className="rounded-lg border border-border bg-base p-3">
            <p className="text-xs font-medium text-tx mb-1">📹 Camera Status</p>
            <p className="text-[11px] text-faint">
              "List all cameras" • "Show cameras in parking area"
            </p>
          </div>
          <div className="rounded-lg border border-border bg-base p-3">
            <p className="text-xs font-medium text-tx mb-1">📚 SOPs & Policies</p>
            <p className="text-[11px] text-faint">
              "What's the SOP for intrusion?" • "Fire alarm procedure?"
            </p>
          </div>
          <div className="rounded-lg border border-border bg-base p-3">
            <p className="text-xs font-medium text-tx mb-1">🔍 Search History</p>
            <p className="text-[11px] text-faint">
              "Was this false alarm before?" • "Similar incidents this week?"
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}
