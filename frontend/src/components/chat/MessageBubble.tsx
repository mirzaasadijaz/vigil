import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import type { Turn } from "../../lib/chatApi";

const md: Components = {
  p: ({ children }) => <p style={{ margin: "0 0 8px" }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: "0 0 8px", paddingLeft: 20, listStyle: "disc" }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: "0 0 8px", paddingLeft: 20, listStyle: "decimal" }}>{children}</ol>,
  li: ({ children }) => <li style={{ margin: "2px 0" }}>{children}</li>,
  strong: ({ children }) => <strong style={{ fontWeight: 700 }}>{children}</strong>,
  code: ({ children }) => (
    <code style={{ background: "rgba(128,128,128,.25)", padding: "1px 5px", borderRadius: 4, fontSize: "0.9em" }}>
      {children}
    </code>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" style={{ color: "#60a5fa", textDecoration: "underline" }}>
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div style={{ overflowX: "auto", margin: "0 0 8px" }}>
      <table style={{ borderCollapse: "collapse", fontSize: 13, width: "100%" }}>{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th style={{ border: "1px solid rgba(128,128,128,.4)", padding: "4px 8px", textAlign: "left" }}>{children}</th>
  ),
  td: ({ children }) => (
    <td style={{ border: "1px solid rgba(128,128,128,.3)", padding: "4px 8px" }}>{children}</td>
  ),
};

export default function MessageBubble({ turn }: { turn: Turn }) {
  const mine = turn.role === "user";
  return (
    <div style={{ alignSelf: mine ? "flex-end" : "flex-start", maxWidth: "85%" }}>
      <div style={{
        padding: "8px 12px", borderRadius: 10,
        background: mine ? "#2563eb" : "rgba(128,128,128,.18)",
        color: mine ? "#fff" : "inherit",
        whiteSpace: mine ? "pre-wrap" : "normal",
        overflowWrap: "anywhere",
      }}>
        {mine ? (
          turn.content
        ) : (
          <div className="md-last">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={md}>
              {turn.content}
            </ReactMarkdown>
          </div>
        )}
      </div>
      {!mine && Array.isArray(turn.sources) && turn.sources.length > 0 && (
        <div style={{ fontSize: 12, opacity: 0.65, marginTop: 4 }}>
          Source: {turn.sources.join(", ")}
        </div>
      )}
    </div>
  );
}