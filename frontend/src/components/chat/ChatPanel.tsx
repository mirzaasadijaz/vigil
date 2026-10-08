import { useEffect, useRef, useState } from "react";
import { useChat } from "../../hooks/useChat";
import MessageBubble from "./MessageBubble";

export default function ChatPanel() {
  const { turns, busy, error, send, clear } = useChat();
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  // Braces matter: an effect must return nothing or a cleanup function. Returning the value of
  // scrollIntoView() (a Promise with some extensions/polyfills) makes React crash with
  // "x is not a function" when the effect re-runs.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  const submit = () => {
    if (!input.trim()) return;
    send(input);
    setInput("");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", maxHeight: 640 }}>
      <div style={{ display: "flex", justifyContent: "flex-end", padding: "4px 12px" }}>
        <button onClick={clear} disabled={busy || turns.length === 0} style={{ fontSize: 12 }}>
          Clear chat
        </button>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        {turns.length === 0 && (
          <p style={{ opacity: 0.6 }}>
            Ask: "how many critical alerts were received today?", "which camera has the most alerts?"
            or "what is the SOP for an intrusion alert?"
          </p>
        )}
        {turns.map((t, i) => <MessageBubble key={i} turn={t} />)}
        {busy && <div style={{ opacity: 0.6 }}>Thinking…</div>}
        {error && <div style={{ color: "#dc2626" }}>{error}</div>}
        <div ref={endRef} />
      </div>
      <div style={{ display: "flex", gap: 8, padding: 12 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Ask about alerts or SOPs…"
          maxLength={2000}
          style={{ flex: 1, padding: "8px 12px", borderRadius: 8, border: "1px solid rgba(128,128,128,.4)",
                     background: "transparent", color: "inherit" }}
        />
        <button onClick={submit} disabled={busy || !input.trim()} style={{ padding: "8px 16px", borderRadius: 8 }}>
          Send
        </button>
      </div>
    </div>
  );
}