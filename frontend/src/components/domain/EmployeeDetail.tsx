import { useMemo } from "react";
import { ArrowLeft, LogIn, LogOut, Timer, Activity, Gauge, Clock, Camera } from "lucide-react";
import { Badge, Button, Card, cx } from "../ui";
import { useApi } from "../../hooks/useApi";
import { usePolling } from "../../hooks/usePolling";
import { Avatar, fmtTime, statusTone } from "./attendance";
import { MODULE_BY_ID, moduleName } from "../../lib/modules";

function fmtDur(ms: number): string {
  if (ms <= 0) return "0h 0m";
  const m = Math.floor(ms / 60000);
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function EmployeeDetail({ emp, att, date, onBack }: { emp: any; att: any | null; date: string; onBack: () => void }) {
  const { data: events, refetch } = useApi<any[]>(`/alerts/?employee_id=${emp.id}&limit=300`);
  usePolling(refetch, 10000);

  const dayEvents = useMemo(() => {
    const list = events ?? [];
    return list.filter((e) => new Date(e.timestamp).toISOString().split("T")[0] === date);
  }, [events, date]);

  const present = !!att?.check_in;
  const end = att?.check_out ? new Date(att.check_out) : new Date();
  const workingMs = present ? Math.max(0, end.getTime() - new Date(att.check_in).getTime()) : 0;
  const idleSeconds = dayEvents.filter((e) => e.module === "idle").reduce((s, e) => s + (e.value || 0), 0);
  const productiveMs = Math.max(0, workingMs - idleSeconds * 1000);
  const idleMs = Math.min(workingMs, idleSeconds * 1000);
  const prodPct = workingMs ? Math.round((productiveMs / workingMs) * 100) : 0;

  const byModule = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of dayEvents) if (e.module) m.set(e.module, (m.get(e.module) ?? 0) + 1);
    return [...m.entries()].map(([id, value]) => ({ id, value })).sort((a, b) => b.value - a.value);
  }, [dayEvents]);

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft size={14} /> Back to Attendance</Button>

      {/* profile header */}
      <Card className="p-6">
        <div className="flex flex-wrap items-center gap-5">
          <Avatar photo={emp.photo} name={emp.name} size={88} />
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-bold tracking-tight text-th">{emp.name}</h1>
            <p className="text-sm text-faint font-mono mt-0.5">{emp.employee_id}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone={present ? statusTone(att?.status) : "neutral"}>{present ? (att?.status ?? "Present") : "Absent"}</Badge>
              <span className="text-xs text-faint">{new Date(date + "T00:00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })}</span>
            </div>
          </div>
          <div className="flex gap-4 text-sm">
            <div className="text-center"><p className="text-faint text-[11px] uppercase tracking-wider flex items-center gap-1 justify-center"><LogIn size={11} /> In</p><p className="font-mono text-tx mt-1">{fmtTime(att?.check_in)}</p></div>
            <div className="text-center"><p className="text-faint text-[11px] uppercase tracking-wider flex items-center gap-1 justify-center"><LogOut size={11} /> Out</p><p className="font-mono text-tx mt-1">{fmtTime(att?.check_out)}</p></div>
          </div>
        </div>
      </Card>

      {/* time KPIs */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="p-5"><p className="text-xs uppercase tracking-wider text-muted flex items-center gap-1.5"><Clock size={13} /> Working Hours</p><p className="mt-2 text-3xl font-bold font-mono text-th">{fmtDur(workingMs)}</p><p className="mt-1 text-xs text-faint">{att?.check_out ? "checked out" : present ? "ongoing" : "not in"}</p></Card>
        <Card className="p-5"><p className="text-xs uppercase tracking-wider text-muted flex items-center gap-1.5"><Gauge size={13} /> Productive</p><p className="mt-2 text-3xl font-bold font-mono text-online">{fmtDur(productiveMs)}</p><p className="mt-1 text-xs text-faint">{prodPct}% of working time</p></Card>
        <Card className="p-5"><p className="text-xs uppercase tracking-wider text-muted flex items-center gap-1.5"><Timer size={13} /> Idle</p><p className="mt-2 text-3xl font-bold font-mono text-idle">{fmtDur(idleMs)}</p><p className="mt-1 text-xs text-faint">{idleSeconds ? `${dayEvents.filter((e) => e.module === "idle").length} idle events` : "none recorded"}</p></Card>
        <Card className="p-5"><p className="text-xs uppercase tracking-wider text-muted flex items-center gap-1.5"><Activity size={13} /> Sightings</p><p className="mt-2 text-3xl font-bold font-mono text-accent">{dayEvents.length}</p><p className="mt-1 text-xs text-faint">events today</p></Card>
      </div>

      {/* productive vs idle bar */}
      <Card className="p-5">
        <div className="flex items-center justify-between text-xs mb-2"><span className="text-muted">Productivity breakdown</span><span className="font-mono text-muted">{prodPct}% productive</span></div>
        <div className="flex h-3 w-full overflow-hidden rounded-full bg-base">
          <div className="h-full bg-online transition-all duration-500" style={{ width: `${prodPct}%` }} />
          <div className="h-full bg-idle transition-all duration-500" style={{ width: `${workingMs ? Math.round((idleMs / workingMs) * 100) : 0}%` }} />
        </div>
        <div className="mt-2 flex gap-5 text-xs text-muted">
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-online" /> Productive {fmtDur(productiveMs)}</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-idle" /> Idle {fmtDur(idleMs)}</span>
        </div>
        <p className="mt-3 text-[11px] text-faint">Idle is attributed to this employee only on cameras running the Idle module where their face is recognized (stillness mode). Away-from-station idle is tracked at the zone level.</p>
      </Card>

      {/* activity */}
      <div className="grid gap-6 xl:grid-cols-[1fr_1.6fr]">
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border bg-base px-5 py-4"><h2 className="text-base font-semibold text-th">Activity by Module</h2></div>
          <div className="p-5 space-y-3">
            {byModule.length === 0 ? <p className="text-sm text-faint py-6 text-center">No events today.</p> : byModule.map((d) => (
              <div key={d.id} className="flex items-center justify-between text-sm">
                <Badge tone={MODULE_BY_ID[d.id]?.tone ?? "neutral"}>{moduleName(d.id)}</Badge>
                <span className="font-mono text-tx">{d.value}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border bg-base px-5 py-4"><h2 className="text-base font-semibold text-th">Presence Timeline</h2></div>
          <div className="max-h-[380px] overflow-y-auto no-scrollbar p-3 space-y-2">
            {dayEvents.length === 0 ? <p className="text-sm text-faint py-10 text-center">No sightings recorded today.</p> : dayEvents.map((e) => (
              <div key={e.id} className="flex items-center gap-3 rounded-md border border-border bg-base/60 p-2.5">
                <span className="text-[11px] font-mono text-faint w-16 shrink-0">{new Date(e.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                {e.module && <Badge tone={MODULE_BY_ID[e.module]?.tone ?? "neutral"}>{moduleName(e.module)}</Badge>}
                <span className="text-xs text-tx truncate flex-1">{(e.event_type || e.violation_type || "").replace(/_/g, " ")}</span>
                {e.camera_id != null && <span className={cx("inline-flex items-center gap-1 text-[10px] text-faint shrink-0")}><Camera size={10} /> {e.camera_id}</span>}
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
