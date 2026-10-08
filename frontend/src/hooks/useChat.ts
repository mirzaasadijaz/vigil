import { useCallback, useEffect, useState } from "react";
import { ChatError, clearHistory, getHistory, sendMessage, type Turn } from "../lib/chatApi";

function errorText(e: unknown): string {
  const status = e instanceof ChatError ? e.status : 0;
  if (status === 503) return "The assistant is not configured (GEMINI_API_KEY missing). Please contact an admin.";
  if (status === 502) return "The assistant is unavailable right now. Please try again in a moment.";
  if (status === 422) return "Message is empty or too long (max 2000 characters).";
  return "Could not reach the assistant. Check your connection and try again.";
}

export function useChat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHistory().then((h) => setTurns(h.turns)).catch(() => {});
  }, []);

  const send = useCallback(async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    setError(null);
    setBusy(true);
    setTurns((t) => [...t, { role: "user", content: message }]);
    try {
      const res = await sendMessage(message);
      setTurns((t) => [...t, {
        role: "assistant",
        content: typeof res?.reply === "string" ? res.reply : "(empty response)",
        sources: Array.isArray(res?.sources) ? res.sources : [],
      }]);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [busy]);

  const clear = useCallback(async () => {
    await clearHistory().catch(() => {});
    setTurns([]);
  }, []);

  return { turns, busy, error, send, clear };
}
