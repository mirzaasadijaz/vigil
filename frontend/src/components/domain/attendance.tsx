import { useMemo, useState } from "react";
import { Clock, Timer, LogIn, LogOut as LogOutIcon, Gauge } from "lucide-react";
import { cx, Badge } from "../ui";
import { useApi } from "../../hooks/useApi";
import { usePolling } from "../../hooks/usePolling";

export function fmtDur(ms: number): string {
  if (ms <= 0) return "0h 0m";
  const m = Math.floor(ms / 60000);
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Working / productive / idle ms for an attendance row + idle seconds for the day. */
export function workTimes(att: any | null, idleSeconds = 0) {
  if (!att?.check_in) return { working: 0, idle: 0, productive: 0 };
  const end = att.check_out ? new Date(att.check_out).getTime() : Date.now();
  const working = Math.max(0, end - new Date(att.check_in).getTime());
  const idle = Math.min(working, idleSeconds * 1000);
  return { working, idle, productive: Math.max(0, working - idle) };
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: true });
}

export function duration(checkIn: string, checkOut: string | null): string {
  const end = checkOut ? new Date(checkOut).getTime() : Date.now();
  const mins = Math.max(0, Math.floor((end - new Date(checkIn).getTime()) / 60000));
  const h = Math.floor(mins / 60), m = mins % 60;
  return `${h}h ${m}m${checkOut ? "" : " (ongoing)"}`;
}

export function statusTone(status: string | undefined): "success" | "warning" | "danger" | "blue" | "neutral" {
  if (!status) return "neutral";
  if (status === "Present" || status === "On Time" || status === "Completed") return "success";
  return "danger";
}

function initials(name: string) {
  return name.split(" ").slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

/** Normalize an employee photo to a usable URL. Accepts a relative path
 * (employees/x.jpg), an absolute /media/... URL, or a full http(s) URL. */
function mediaUrl(photo: string | null | undefined): string | null {
  if (!photo) return null;
  if (photo.startsWith("http") || photo.startsWith("/media/") || photo.startsWith("/")) return photo;
  return `/media/${photo}`;
}

export function Avatar({ photo, name, size = 72 }: { photo?: string; name: string; size?: number }) {
  const [err, setErr] = useState(false);
  const url = mediaUrl(photo);
  if (url && !err) {
    return (
      <img src={url} alt={name} style={{ width: size, height: size }}
        className="rounded-full object-cover border-2 border-border shadow-lg" onError={() => setErr(true)} />
    );
  }
  const colors = ["from-accent-hover to-accent", "from-online-dim to-online", "from-dnd-dim to-orange", "from-accent-hover to-accent-soft", "from-overlay to-accent"];
  const idx = name.charCodeAt(0) % colors.length;
  return (
    <div style={{ width: size, height: size }}
      className={cx("rounded-full bg-gradient-to-br flex items-center justify-center border-2 border-border shadow-lg text-white font-bold shrink-0", colors[idx])}>
      <span style={{ fontSize: size * 0.32 }}>{initials(name)}</span>
    </div>
  );
}

export function AttendanceCard({ emp, att, idleSeconds = 0 }: { emp: any; att: any | null; idleSeconds?: number }) {
  const present = !!att;
  const status: string | undefined = att?.status;
  const { working, idle, productive } = workTimes(att, idleSeconds);
  return (
    <div className={cx(
      "rounded-lg border bg-surface p-5 flex flex-col gap-4 transition-all duration-200 hover:shadow-xl hover:-translate-y-0.5",
      present
        ? "border-online/20 hover:border-online/40"
        : "border-border hover:border-overlay"
    )}>
      <div className="flex items-center justify-between">
        <Badge tone={present ? statusTone(status) : "neutral"}>{present ? (status ?? "Present") : "Not Arrived"}</Badge>
        {present && att.check_in && (
          <div className="flex items-center gap-1 text-[10px] text-faint font-mono"><Clock size={10} />{fmtTime(att.check_in)}</div>
        )}
      </div>
      <div className="flex flex-col items-center text-center gap-3">
        <Avatar photo={emp.photo} name={emp.name} size={72} />
        <div>
          <p className="font-semibold text-tx text-sm leading-snug">{emp.name}</p>
          <p className="text-[10px] text-faint font-mono mt-0.5">{emp.employee_id}</p>
        </div>
      </div>
      <div className="border-t border-border pt-3 space-y-1.5">
        <div className="flex items-center justify-between text-xs">
          <span className="flex items-center gap-1.5 text-faint"><LogIn size={11} /> Check In</span>
          <span className="font-mono text-tx">{fmtTime(att?.check_in)}</span>
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="flex items-center gap-1.5 text-faint"><LogOutIcon size={11} /> Check Out</span>
          <span className="font-mono text-tx">{fmtTime(att?.check_out)}</span>
        </div>
        {att?.check_in && (
          <>
            <div className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 text-faint"><Timer size={11} /> Working</span>
              <span className="font-mono text-muted text-[10px]">{fmtDur(working)}{!att.check_out ? " ·" : ""}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 text-faint"><Gauge size={11} /> Productive</span>
              <span className="font-mono text-online-text text-[10px]">{fmtDur(productive)}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 text-faint"><Timer size={11} /> Idle</span>
              <span className="font-mono text-idle-text text-[10px]">{fmtDur(idle)}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// Most-recent activity timestamp (ms) for ordering, and a key that changes whenever
// a row gains a check-in or check-out (so that card re-mounts and re-animates).
const activityTs = (att: any) => Math.max(
  att?.check_in ? new Date(att.check_in).getTime() : 0,
  att?.check_out ? new Date(att.check_out).getTime() : 0,
);
const activityKey = (att: any) => `${att?.employee?.employee_id ?? att?.id}|${att?.check_out ?? att?.check_in ?? ""}`;

// Status -> card accent.
const LIVE_TONE: Record<string, { border: string; footer: string; chip: string }> = {
  success: { border: "border-online/60", footer: "bg-online/10", chip: "text-online-text" },
  warning: { border: "border-idle/60",   footer: "bg-idle/10",   chip: "text-idle-text" },
  danger:  { border: "border-dnd/60",    footer: "bg-dnd/10",    chip: "text-dnd-text" },
  blue:    { border: "border-accent/60", footer: "bg-accent/10", chip: "text-accent-text" },
  neutral: { border: "border-border",    footer: "bg-base",      chip: "text-muted" },
};

/** Big square photo with graceful initials fallback (for the live feed card). */
function SquarePhoto({ photo, name }: { photo?: string; name: string }) {
  const [err, setErr] = useState(false);
  const url = mediaUrl(photo);
  if (url && !err) {
    return <img src={url} alt={name} onError={() => setErr(true)} className="h-full w-full object-cover" />;
  }
  const colors = ["from-accent-hover to-accent", "from-online-dim to-online", "from-dnd-dim to-orange", "from-overlay to-accent"];
  const idx = name.charCodeAt(0) % colors.length;
  return (
    <div className={cx("flex h-full w-full items-center justify-center bg-gradient-to-br text-white font-bold", colors[idx])}>
      <span className="text-3xl">{initials(name)}</span>
    </div>
  );
}

/** Compact, photo-forward live card: large clear picture + name, colour-coded by status. */
export function LiveAttendanceCard({ emp, att }: { emp: any; att: any }) {
  const status: string | undefined = att?.status;
  const t = LIVE_TONE[statusTone(status)] ?? LIVE_TONE.neutral;
  return (
    <div className={cx("overflow-hidden rounded-lg border-2 bg-surface transition-all duration-200 hover:-translate-y-0.5", t.border)}>
      <div className="aspect-square w-full bg-base">
        <SquarePhoto photo={emp.photo} name={emp.name} />
      </div>
      <div className={cx("px-2.5 py-2", t.footer)}>
        <p className="truncate text-sm font-semibold leading-tight text-tx" title={emp.name}>{emp.name}</p>
        <div className="mt-1 flex items-center justify-between gap-1">
          <span className={cx("text-[11px] font-bold", t.chip)}>{status ?? "Present"}</span>
          <span className="font-mono text-[10px] text-faint">{fmtTime(att?.check_in)}</span>
        </div>
      </div>
    </div>
  );
}

/**
 * Live attendance feed for the right pane of an attendance camera (#7). Photo-forward
 * cards (large registered photo + name). Newest check-in/out first; new cards drop in
 * (att-card-in) and older cards fade out toward the bottom (att-fade-mask). Polls today's
 * attendance.
 */
export function LiveAttendancePanel() {
  const date = new Date().toISOString().split("T")[0];
  const { data: employees, refetch: refetchEmps } = useApi<any[]>("/employees/");
  const { data: attendance, refetch: refetchAtt } = useApi<any[]>(`/attendance/?date=${date}`);
  usePolling(() => { refetchAtt(); }, 4000);   // fast for a "live" feel
  usePolling(() => { refetchEmps(); }, 30000);

  // employee_id (code) -> full employee (carries DB id + registered photo)
  const empByCode = useMemo(() => {
    const m = new Map<string, any>();
    for (const e of (employees ?? [])) m.set(e.employee_id, e);
    return m;
  }, [employees]);

  const rows = useMemo(() =>
    (attendance ?? [])
      .filter((a) => a.employee && a.check_in)
      .map((a) => ({ att: a, emp: empByCode.get(a.employee?.employee_id) ?? a.employee }))
      .sort((x, y) => activityTs(y.att) - activityTs(x.att)),
    [attendance, empByCode]);

  if (rows.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center py-16">
        <p className="text-sm text-muted">No check-ins yet today.</p>
      </div>
    );
  }

  return (
    <div className="att-fade-mask flex-1 overflow-y-auto p-3">
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
        {rows.map(({ emp, att }) => (
          <div key={activityKey(att)} className="att-card-in">
            <LiveAttendanceCard emp={emp} att={att} />
          </div>
        ))}
      </div>
    </div>
  );
}
