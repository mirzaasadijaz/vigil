import { useEffect, useRef, useState } from "react";
import { UserX, AlertTriangle, ShieldCheck, X } from "lucide-react";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { cx } from "./ui";

interface Alert {
  id: number;
  violation_type: string;
  timestamp: string;
  unknown_person: boolean;
  snapshot: string | null;
  employee?: { name: string } | null;
}

interface Toast { id: number; alert: Alert; leaving: boolean; }

const VISIBLE_MS = 6000;
const MAX_VISIBLE = 6;

// Views where the toaster is suppressed — the live-feed / live-attendance page already
// shows this activity, so corner toasts would just be redundant there.
const SUPPRESS_ON = new Set(["operations"]);

function tone(v: string): { ring: string; icon: React.ReactNode } {
  const s = v.toLowerCase();
  if (s.includes("no-") || s.includes("missing")) return { ring: "border-l-dnd", icon: <AlertTriangle size={15} className="text-dnd" /> };
  if (s.includes("phone")) return { ring: "border-l-idle", icon: <AlertTriangle size={15} className="text-idle" /> };
  return { ring: "border-l-accent", icon: <ShieldCheck size={15} className="text-accent" /> };
}

export function ActivityToaster({ activeView }: { activeView?: string }) {
  const suppressed = !!activeView && SUPPRESS_ON.has(activeView);
  const { data, refetch } = useApi<Alert[]>("/alerts/?limit=15");
  usePolling(refetch, 5000);
  const lastSeenId = useRef<number | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    if (!data) return;
    const maxId = data.reduce((m, a) => Math.max(m, a.id), 0);
    if (lastSeenId.current === null) { lastSeenId.current = maxId; return; }   // baseline; don't flood on first load
    // On a suppressed page keep the baseline current (so we don't dump a backlog when
    // the user navigates away) but never raise a toast.
    if (suppressed) { lastSeenId.current = maxId; return; }
    const fresh = data.filter((a) => a.id > (lastSeenId.current ?? 0)).sort((a, b) => a.id - b.id);
    if (fresh.length === 0) return;
    lastSeenId.current = maxId;
    setToasts((prev) => [...prev, ...fresh.map((a) => ({ id: a.id, alert: a, leaving: false }))].slice(-MAX_VISIBLE));
    for (const a of fresh) scheduleDismiss(a.id);
  }, [data, suppressed]);

  function scheduleDismiss(id: number) {
    if (timers.current[id]) return;
    timers.current[id] = setTimeout(() => {
      setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
      setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 400);
    }, VISIBLE_MS);
  }

  function dismiss(id: number) {
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 400);
  }

  useEffect(() => () => { Object.values(timers.current).forEach(clearTimeout); }, []);

  if (suppressed || toasts.length === 0) return null;

  return (
    // Stack anchored bottom-right, capped at 40% of the viewport height (oldest clip out
    // the top). Compact list rows — no snapshot image (see it in Alerts instead).
    <div className="pointer-events-none fixed bottom-5 right-5 z-[200] flex max-h-[40vh] w-80 flex-col justify-end gap-2 overflow-hidden">
      {toasts.map((t, i) => {
        const a = t.alert;
        const { ring, icon } = tone(a.violation_type);
        // Newest sits at the bottom; the 3rd-newest and older fade progressively. The
        // fade lives on a NON-animated wrapper so the toast-in fill doesn't reset it.
        const fromBottom = toasts.length - 1 - i;
        const opacity = fromBottom <= 1 ? 1 : Math.max(0.15, 1 - (fromBottom - 1) * 0.35);
        return (
          <div key={t.id} style={{ opacity }} className="shrink-0 transition-opacity duration-300">
            <div
              className={cx(
                "pointer-events-auto flex items-center gap-2.5 rounded-lg border border-border border-l-4 bg-floating px-3 py-2.5 shadow-2xl shadow-black/50",
                ring, t.leaving ? "toast-out" : "toast-in"
              )}
            >
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-overlay">
                {a.unknown_person ? <UserX size={15} className="text-muted" /> : icon}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-semibold text-th">
                  {a.employee?.name ?? (a.unknown_person ? "Unknown person" : a.violation_type)}
                </p>
                <p className="truncate text-[10px] text-faint">
                  {a.violation_type} · {new Date(a.timestamp).toLocaleTimeString()}
                </p>
              </div>
              <button onClick={() => dismiss(t.id)} className="shrink-0 text-faint transition-colors hover:text-tx" aria-label="Dismiss">
                <X size={13} />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
