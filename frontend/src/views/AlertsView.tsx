import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bell, Search, UserX, UserCheck, AlertTriangle, ShieldCheck,
  ArrowRightLeft, Timer, Users, Flame, Smartphone, Camera,
  Fence,
  UsersRound,
  Car,
  CameraOff,
  ThumbsUp,
  ThumbsDown,
  PackageX,
  CalendarRange} from "lucide-react";
import { Badge, Button, Card, Spinner, cx } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { apiFetch } from "../lib/api";
import { ModuleFilter } from "../components/ModuleFilter";
import { Lightbox } from "../components/Lightbox";
import { MODULE_BY_ID, MODULES, moduleName } from "../lib/modules";

interface Alert {
  id: number; module: string | null; event_type: string | null; camera_id: number | null;
  violation_type: string; value: number | null; distance_score: number | null;
  unknown_person: boolean; snapshot: string | null; timestamp: string;
  // Written by the false-detection layer; NULL when that layer is off (the default).
  severity?: string | null;
  // 'up' | 'down' | null. NULL means NOBODY HAS JUDGED IT, which the UI must render
  // differently from "judged correct" - otherwise every untouched alert looks approved
  // and the accuracy figure this exists to measure is meaningless.
  feedback?: string | null;
  feedback_note?: string | null;
  employee?: { name: string } | null;
}

// Severity of one alert. The DATABASE is the source of truth when it has an opinion:
// `severity` is written by the false-detection layer from its configurable map, so an
// operator who reclassifies an event type sees that reflected here.
//
// It is NULL for rows written before that layer existed, and for every row written while
// it is disabled - which is the shipped default. So the regex below is not dead code, it
// is the path most rows still take, and any new event type must be added to it or it
// silently renders as "info". That is exactly how `crossing` - a HIGH severity perimeter
// breach - first appeared in the feed looking like routine information.
const SEVERITY_TONE: Record<string, "danger" | "warning" | "success" | "info"> = {
  critical: "danger", high: "danger", medium: "warning", low: "success", info: "info",
};

function severity(a: Alert): "danger" | "warning" | "success" | "info" {
  if (a.severity && SEVERITY_TONE[a.severity]) return SEVERITY_TONE[a.severity];
  const t = (a.event_type || a.violation_type || "").toLowerCase();
  if (/(unknown|intrusion|over_capacity|fire|smoke|phone|tamper|crossing|gathering)/.test(t)) return "danger";
  if (/(idle|late|checkout|no_stopping)/.test(t)) return "warning";
  if (/(checkin|entry|recognized)/.test(t)) return "success";
  return "info";
}

const EVENT_ICON: Record<string, React.ElementType> = {
  unknown: UserX, recognized: UserCheck, checkin: UserCheck, checkout: ArrowRightLeft,
  entry: ArrowRightLeft, exit: ArrowRightLeft, idle: Timer, occupancy: Users,
  over_capacity: Users, intrusion: ShieldCheck, fire: Flame, smoke: Flame,
  phone_use: Smartphone,
  // Kohat POC
  crossing: Fence, gathering: UsersRound, dispersed: UsersRound, no_stopping: Car,
  tamper_blur: CameraOff, tamper_moved: CameraOff, tamper_covered: CameraOff,
  unattended_object: PackageX,
};

/** Format a bare event type for the filter dropdown. `eventLabel` below takes a whole
 *  Alert, so it cannot be reused here. */
function prettyEvent(t: string) {
  return t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function eventLabel(a: Alert) {
  if (a.event_type) return a.event_type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return a.violation_type;
}

/** Thumbs up / down on one alert.
 *
 * This is a labelling tool, not a reaction. Every alert carries the numbers its decision
 * was made from in `data` - confidence, association, drift, dwell - so marking one WRONG
 * labels a specific point in that parameter space as a false positive. That is what
 * `backend/scripts/feedback_report.py` reads, and it is the only way to see where the
 * right and wrong populations actually separate.
 *
 * Clicking the active verdict again CLEARS it. There is deliberately no neutral value:
 * "nobody looked" and "somebody looked and it was fine" are different facts.
 */
function FeedbackButtons({ a, onChange }: { a: Alert; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");

  async function send(verdict: string | null, withNote?: string) {
    setBusy(true);
    try {
      await apiFetch(`/alerts/${a.id}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback: verdict, note: withNote ?? null }),
      });
      onChange();
    } finally {
      setBusy(false);
      setAsking(false);
      setNote("");
    }
  }

  const up = a.feedback === "up";
  const down = a.feedback === "down";

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-1">
        <button
          disabled={busy}
          onClick={() => send(up ? null : "up")}
          title={up ? "Marked correct - click to clear" : "This alert was correct"}
          aria-label="Mark alert correct"
          aria-pressed={up}
          className={cx("rounded p-1 transition-colors disabled:opacity-40",
            up ? "bg-online/15 text-online" : "text-faint hover:text-online hover:bg-online/10")}
        >
          <ThumbsUp size={14} />
        </button>
        <button
          disabled={busy}
          onClick={() => (down ? send(null) : setAsking(true))}
          title={down ? "Marked wrong - click to clear" : "This alert was wrong"}
          aria-label="Mark alert wrong"
          aria-pressed={down}
          className={cx("rounded p-1 transition-colors disabled:opacity-40",
            down ? "bg-dnd/15 text-dnd" : "text-faint hover:text-dnd hover:bg-dnd/10")}
        >
          <ThumbsDown size={14} />
        </button>
      </div>
      {asking && (
        // Asked for, never demanded: a note is the single most useful field for tuning
        // ("that is the printed phone on his shirt" is a diagnosis; conf=0.55 is not),
        // but requiring one would stop people reporting at all.
        <div className="flex items-center gap-1">
          <input
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") send("down", note.trim() || undefined);
              if (e.key === "Escape") setAsking(false);
            }}
            placeholder="Why? (optional)"
            className="w-40 rounded border border-border bg-base px-1.5 py-0.5 text-[11px] text-tx outline-none focus:border-dnd"
          />
          <button
            disabled={busy}
            onClick={() => send("down", note.trim() || undefined)}
            className="rounded bg-dnd/15 px-1.5 py-0.5 text-[10px] font-semibold text-dnd hover:bg-dnd/25"
          >
            Save
          </button>
        </div>
      )}
      {down && a.feedback_note && !asking && (
        <span className="max-w-[10rem] truncate text-[10px] text-faint" title={a.feedback_note}>
          {a.feedback_note}
        </span>
      )}
    </div>
  );
}

function AlertRow({ a, onZoom, onFeedback }: { a: Alert; onZoom: (src: string) => void; onFeedback: () => void }) {
  const sev = severity(a);
  const accent = { danger: "border-l-dnd", warning: "border-l-idle", success: "border-l-online", info: "border-l-accent" }[sev];
  const tint = { danger: "bg-dnd/10 text-dnd", warning: "bg-idle/10 text-idle", success: "bg-online/10 text-online", info: "bg-accent/10 text-accent" }[sev];
  const Icon = (a.event_type && EVENT_ICON[a.event_type]) || (a.module && MODULE_BY_ID[a.module]?.icon) || AlertTriangle;
  const when = new Date(a.timestamp);
  const who = a.employee?.name ?? (a.unknown_person ? "Unknown person" : null);

  return (
    <div className={cx("flex items-stretch gap-3 rounded-lg border border-border border-l-4 bg-surface p-3 transition-all hover:border-accent/40 hover:-translate-y-0.5", accent)}>
      {a.snapshot ? (
        <button onClick={() => onZoom(a.snapshot!)} className="shrink-0 cursor-zoom-in" aria-label="Zoom snapshot">
          <img src={a.snapshot} alt="" className="h-16 w-16 rounded-md object-cover border border-border transition-transform hover:scale-105" loading="lazy" />
        </button>
      ) : (
        <div className={cx("flex h-16 w-16 shrink-0 items-center justify-center rounded-md", tint)}><Icon size={22} /></div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={cx("flex h-5 w-5 items-center justify-center rounded", tint)}><Icon size={12} /></span>
          <p className="text-sm font-semibold text-th truncate">{eventLabel(a)}</p>
        </div>
        <p className="mt-0.5 text-xs text-muted truncate">{who || a.violation_type}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {a.module && <Badge tone={MODULE_BY_ID[a.module]?.tone ?? "neutral"}>{moduleName(a.module)}</Badge>}
          {a.camera_id != null && (
            <span className="inline-flex items-center gap-1 text-[10px] text-faint"><Camera size={10} /> Cam {a.camera_id}</span>
          )}
          {a.value != null && <span className="text-[10px] text-faint font-mono">val {a.value}</span>}
          {a.distance_score != null && <span className="text-[10px] text-faint font-mono">sim {a.distance_score.toFixed(2)}</span>}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end justify-between text-right">
        <Badge tone={sev === "info" ? "blue" : sev}>{sev}</Badge>
        <FeedbackButtons a={a} onChange={onFeedback} />
        <div className="text-[10px] text-faint font-mono leading-tight">
          <div>{when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
          <div className="text-faint/70">{when.toLocaleDateString([], { month: "short", day: "numeric" })}</div>
        </div>
      </div>
    </div>
  );
}

const PAGE_SIZE = 50;

/** Today, as the SERVER will understand it.
 *
 * The backend reads a picked date as a day in Asia/Karachi — that is the timezone every
 * timestamp on this dashboard is rendered in. Using the browser's own date instead would
 * be right only for a viewer sitting in that zone, and would silently be a day out for
 * anyone else during the hours the two dates disagree. Asking Intl for the date IN that
 * zone makes the default mean the same thing on both sides of the request. */
function todayLocal(): string {
  // en-CA formats as YYYY-MM-DD, which is exactly what <input type="date"> wants.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Karachi" }).format(new Date());
}

/** Event types offered by whichever module is selected, so the two filters agree. */
function eventTypesFor(moduleId: string): string[] {
  if (moduleId !== "all") return MODULE_BY_ID[moduleId]?.eventTypes ?? [];
  return Array.from(new Set(MODULES.flatMap((m) => m.eventTypes))).sort();
}

export default function AlertsView() {
  const [mod, setMod] = useState("all");
  const [eventType, setEventType] = useState("all");
  // Default to TODAY. Most of the time the question is "what happened today", and on a
  // deployment with months of history an unfiltered first page is slow and rarely what
  // anyone wanted. "Clear filters" is right there for the times it is not.
  const [from, setFrom] = useState(todayLocal);
  const [to, setTo] = useState(todayLocal);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [zoomSrc, setZoomSrc] = useState<string | null>(null);

  // Filtering happens in SQL, not in the browser. This page used to pull 300 rows every
  // five seconds and filter them client-side, which meant anything older than those 300
  // was simply unreachable and the cost grew with the table. `alert_logs.timestamp` is
  // indexed, so a date range stays cheap however large the history gets.
  const params = useMemo(() => {
    const p = new URLSearchParams();
    if (mod !== "all") p.set("module", mod);
    if (eventType !== "all") p.set("event_type", eventType);
    if (from) p.set("date_from", from);
    if (to) p.set("date_to", to);
    return p.toString();
  }, [mod, eventType, from, to]);

  const listPath = `/alerts/?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}${params ? `&${params}` : ""}`;
  const { data, loading, refetch } = useApi<Alert[]>(listPath);
  const { data: countData, refetch: refetchCount } =
    useApi<{ total: number }>(`/alerts/count${params ? `?${params}` : ""}`);

  const reload = useCallback(() => { refetch(); refetchCount(); }, [refetch, refetchCount]);
  const noop = useCallback(() => {}, []);
  // Poll the FIRST page only. Refreshing under someone reading page 4 shuffles rows out
  // from under them, and new alerts arrive at the top anyway.
  usePolling(page === 0 ? reload : noop, 5000);

  const list = data ?? [];
  const total = countData?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Any filter change invalidates the current page number.
  useEffect(() => { setPage(0); }, [mod, eventType, from, to]);
  // Changing module can leave an event type that module never emits.
  useEffect(() => {
    if (eventType !== "all" && !eventTypesFor(mod).includes(eventType)) setEventType("all");
  }, [mod, eventType]);

  // Free text stays client-side and searches THIS PAGE only. It matches employee names
  // and labels, which the SQL filters cannot, and the placeholder says so rather than
  // implying it searches the whole history.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((a) => `${a.violation_type} ${a.event_type ?? ""} ${a.employee?.name ?? ""} ${a.module ?? ""}`.toLowerCase().includes(q));
  }, [list, query]);

  const counts = useMemo(() => {
    const c = { danger: 0, warning: 0, success: 0, info: 0 };
    for (const a of list) c[severity(a)]++;
    return c;
  }, [list]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th flex items-center gap-2"><Bell size={20} className="text-accent" /> Alerts</h1>
          <p className="mt-1 text-sm text-muted">Detection & event history across all modules</p>
        </div>
      </div>

      {/* severity summary */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {([["Critical", counts.danger, "bg-dnd", "text-dnd"], ["Warning", counts.warning, "bg-idle", "text-idle"],
           ["Normal", counts.success, "bg-online", "text-online"], ["Info", counts.info, "bg-accent", "text-accent"]] as const).map(
          ([label, n, dot, txt]) => (
            <Card key={label} className="flex items-center gap-3 p-4">
              <span className={cx("h-2.5 w-2.5 rounded-full", dot)} />
              <div><p className={cx("text-2xl font-bold font-mono", txt)}>{n}</p><p className="text-[11px] text-faint uppercase tracking-wider">{label}</p></div>
            </Card>
          ))}
      </div>

      {/* toolbar */}
      <Card className="p-0 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-base px-5 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <ModuleFilter value={mod} onChange={setMod} />

            <label className="flex items-center gap-2 rounded-md border border-border bg-base px-3 py-2">
              <span className="text-[10px] font-bold uppercase tracking-wider text-faint">Event</span>
              <select value={eventType} onChange={(e) => setEventType(e.target.value)}
                className="bg-transparent text-sm text-tx outline-none cursor-pointer">
                <option value="all" className="bg-surface">All events</option>
                {eventTypesFor(mod).map((t) => (
                  <option key={t} value={t} className="bg-surface">{prettyEvent(t)}</option>
                ))}
              </select>
            </label>

            <label className="flex items-center gap-2 rounded-md border border-border bg-base px-3 py-2">
              <CalendarRange size={14} className="text-accent" />
              <span className="text-[10px] font-bold uppercase tracking-wider text-faint">From</span>
              <input type="date" value={from} max={to || undefined}
                onChange={(e) => setFrom(e.target.value)}
                className="bg-transparent text-sm text-tx outline-none cursor-pointer" />
              <span className="text-[10px] font-bold uppercase tracking-wider text-faint">To</span>
              <input type="date" value={to} min={from || undefined}
                onChange={(e) => setTo(e.target.value)}
                className="bg-transparent text-sm text-tx outline-none cursor-pointer" />
            </label>

            {(mod !== "all" || eventType !== "all" || from || to) && (
              <Button variant="ghost" size="sm"
                onClick={() => { setMod("all"); setEventType("all"); setFrom(""); setTo(""); }}>
                Clear filters
              </Button>
            )}
          </div>

          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search this page…"
              title="Searches the alerts shown on this page. Use the filters above to narrow the whole history."
              className="w-56 rounded-md border border-base bg-base py-2 pl-9 pr-3 text-sm text-tx placeholder:text-faint outline-none focus:border-accent focus:ring-2 focus:ring-accent/20" />
          </div>
        </div>
        <div className="p-4">
          {loading && list.length === 0 ? (
            <div className="flex justify-center py-16"><Spinner /></div>
          ) : filtered.length === 0 ? (
            <div className="py-16 text-center">
              <ShieldCheck size={40} className="mx-auto text-online/50 mb-3" />
              <p className="text-muted text-sm">{list.length === 0 ? "No alerts recorded yet." : "No alerts match your filter."}</p>
              <p className="text-faint text-xs mt-1">Events from active AI modules will appear here.</p>
            </div>
          ) : (
            <div className="grid gap-2.5 lg:grid-cols-2">
              {filtered.map((a) => <AlertRow key={a.id} a={a} onZoom={setZoomSrc} onFeedback={reload} />)}
            </div>
          )}
        </div>

        {total > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border bg-base px-5 py-3">
            <p className="text-xs text-faint font-mono">
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              {query && <span className="text-faint/70"> · text filter applies to this page only</span>}
            </p>
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" disabled={page === 0}
                onClick={() => setPage(0)} aria-label="First page">« First</Button>
              <Button variant="ghost" size="sm" disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}>‹ Prev</Button>
              <span className="text-xs text-muted font-mono px-1">{page + 1} / {pages}</span>
              <Button variant="ghost" size="sm" disabled={page + 1 >= pages}
                onClick={() => setPage((p) => p + 1)}>Next ›</Button>
              <Button variant="ghost" size="sm" disabled={page + 1 >= pages}
                onClick={() => setPage(pages - 1)} aria-label="Last page">Last »</Button>
            </div>
          </div>
        )}
      </Card>
      {zoomSrc && <Lightbox src={zoomSrc} onClose={() => setZoomSrc(null)} />}
    </div>
  );
}
