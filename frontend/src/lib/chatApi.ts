import { apiFetch } from "./api";

export type Turn = { role: "user" | "assistant"; content: string; sources?: string[] };

export class ChatError extends Error {
  status: number;
  constructor(status: number) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

// apiFetch sends the auth cookie and reloads to the login screen on 401 (expired session).
async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new ChatError(res.status);
  return res.json();
}

export const sendMessage = (message: string) =>
  apiFetch("/chat/", { method: "POST", body: JSON.stringify({ message }) })
    .then(json<{ reply: string; sources: string[] }>);

// The UI must never crash on a odd response (older backend, proxy page, null...): normalise.
export const getHistory = () =>
  apiFetch("/chat/history")
    .then(json<{ turns?: unknown }>)
    .then((h): { turns: Turn[] } => ({
      turns: (Array.isArray(h?.turns) ? h.turns : []).filter(
        (t): t is Turn =>
          !!t && typeof t === "object" &&
          ((t as Turn).role === "user" || (t as Turn).role === "assistant") &&
          typeof (t as Turn).content === "string"),
    }));

export const clearHistory = () =>
  apiFetch("/chat/history", { method: "DELETE" }).then(json<{ ok: boolean }>);

// admin only
export const reindex = (full = false) =>
  apiFetch(`/chat/reindex?full=${full}`, { method: "POST" }).then(json<{ started: boolean }>);
