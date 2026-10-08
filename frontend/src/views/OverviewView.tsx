import { useMemo } from "react";
import {
  Users2, Camera, ScanFace, Activity, Cpu, Layers,
  Sparkles, TrendingUp, UserX, CheckCircle2, Clock, Wifi,
} from "lucide-react";
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid,
  PieChart, Pie, Cell,
} from "recharts";
import { cx, Card, MetricCard, Badge } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { moduleName } from "../lib/modules";

const MOD_BAR: Record<string, string> = {
  attendance: "bg-accent", headcount: "bg-online", occupancy: "bg-accent-soft",
  idle: "bg-idle", intrusion: "bg-dnd", mobile: "bg-idle", fire_smoke: "bg-dnd",
};

function todayISO() { return new Date().toISOString().split("T")[0]; }

const tooltipStyle = {
  background: "var(--color-floating)", border: "1px solid var(--color-border)",
  borderRadius: 8, color: "var(--color-th)", fontSize: 12,
} as const;

// ── AI insight pill ──────────────────────────────────────────────────────────
function Insight({ icon, title, body, tone = "accent" }: { icon: React.ReactNode; title: string; body: string; tone?: "accent" | "success" | "warning" | "danger" }) {
  const tint = { accent: "bg-accent/10 text-accent", success: "bg-online/10 text-online", warning: "bg-idle/10 text-idle", danger: "bg-dnd/10 text-dnd" }[tone];
  return (
    <div className="flex items-start gap-3 rounded-md border border-border bg-base/60 p-3">
      <div className={cx("flex h-8 w-8 shrink-0 items-center justify-center rounded-md", tint)}>{icon}</div>
      <div className="min-w-0">
        <p className="text-xs font-semibold text-tx">{title}</p>
        <p className="text-[11px] text-muted leading-relaxed mt-0.5">{body}</p>
      </div>
    </div>
  );
}

function HealthRow({ label, value, status }: { label: string; value: string; status: "online" | "idle" | "dnd" }) {
  const dot = { online: "bg-online", idle: "bg-idle", dnd: "bg-dnd" }[status];
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-border last:border-0">
      <span className="text-xs text-muted">{label}</span>
      <span className="flex items-center gap-2 text-xs font-medium text-tx">
        <span className={cx("h-2 w-2 rounded-full", dot, status === "online" && "animate-pulse")} />{value}
      </span>
    </div>
  );
}

export default function OverviewView() {
  const { data: cameras, refetch: rc } = useApi<any[]>("/cameras/");
  const { data: alerts, refetch: ra } = useApi<any[]>("/alerts/?limit=200");
  const { data: employees, refetch: re } = useApi<any[]>("/employees/");
  const { data: attendance, refetch: rt } = useApi<any[]>(`/attendance/?date=${todayISO()}`);
  const { data: mode } = useApi<{ mode: string }>("/mode/");

  usePolling(() => { ra(); rt(); }, 5000);
  usePolling(() => { rc(); re(); }, 15000);

  const camList = cameras ?? [];
  const alertList = alerts ?? [];
  const empList = employees ?? [];
  const attList = attendance ?? [];

  const activeCams = camList.filter((c: any) => c.is_active).length;
  const present = attList.length;
  const absent = Math.max(0, empList.length - present);
  const rate = empList.length ? Math.round((present / empList.length) * 100) : 0;

  // today's alerts/identifications
  const todaysAlerts = useMemo(() => {
    const start = new Date(); start.setHours(0, 0, 0, 0);
    return alertList.filter((a: any) => new Date(a.timestamp) >= start);
  }, [alertList]);
  const unknownCount = todaysAlerts.filter((a: any) => a.unknown_person).length;
  const knownCount = todaysAlerts.length - unknownCount;

  // hourly activity (last 12h)
  const hourly = useMemo(() => {
    const now = new Date();
    const buckets: { t: string; known: number; unknown: number }[] = [];
    const map = new Map<string, { known: number; unknown: number }>();
    for (let i = 11; i >= 0; i--) {
      const d = new Date(now.getTime() - i * 3600_000);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}-${d.getHours()}`;
      const label = d.toLocaleTimeString("en-US", { hour: "numeric", hour12: true });
      map.set(key, { known: 0, unknown: 0 });
      buckets.push({ t: label, known: 0, unknown: 0 });
    }
    for (const a of alertList) {
      const d = new Date(a.timestamp);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}-${d.getHours()}`;
      const b = map.get(key);
      if (b) { if (a.unknown_person) b.unknown++; else b.known++; }
    }
    let i = 0;
    for (const v of map.values()) { buckets[i].known = v.known; buckets[i].unknown = v.unknown; i++; }
    return buckets;
  }, [alertList]);

  const peakHour = useMemo(() => hourly.reduce((m, h) => (h.known + h.unknown > m.known + m.unknown ? h : m), hourly[0] ?? { t: "—", known: 0, unknown: 0 }), [hourly]);

  // events grouped by module (today) — module-driven breakdown
  const byModuleToday = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of todaysAlerts) if (a.module) m.set(a.module, (m.get(a.module) ?? 0) + 1);
    return [...m.entries()].map(([id, value]) => ({ id, value })).sort((a, b) => b.value - a.value);
  }, [todaysAlerts]);
  const moduleMax = Math.max(1, ...byModuleToday.map((d) => d.value));
  const attendanceMix = [
    { name: "Present", value: present, color: "var(--color-online)" },
    { name: "Absent", value: absent, color: "var(--color-faint)" },
  ];
  const analyticLabel = mode?.mode === "face" ? "Face Recognition" : mode?.mode === "ppe" ? "PPE Detection" : "—";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th flex items-center gap-2"><Sparkles size={20} className="text-accent" /> AI Operations Overview</h1>
          <p className="mt-1 text-sm text-muted">Real-time intelligence across workforce, cameras, and detections</p>
        </div>
        <Badge tone="blue">Engine · {analyticLabel}</Badge>
      </div>

      {/* KPIs */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard tone="success" label="Present Today" value={`${present}/${empList.length}`}
          trend={{ dir: rate >= 75 ? "up" : rate >= 40 ? "flat" : "down", text: `${rate}%` }}
          hint={`${present} checked in today`} icon={<CheckCircle2 size={20} />} />
        <MetricCard tone="accent" label="Identifications Today" value={todaysAlerts.length}
          hint={`${knownCount} recognized · ${unknownCount} unknown`} icon={<ScanFace size={20} />} />
        <MetricCard tone={unknownCount ? "danger" : "success"} label="Unknown Faces" value={unknownCount}
          hint={unknownCount ? "Review required" : "None today"} icon={<UserX size={20} />} />
        <MetricCard tone={activeCams ? "accent" : "warning"} label="Cameras Online" value={`${activeCams}/${camList.length}`}
          hint={activeCams ? "Monitoring active" : "No active feeds"} icon={<Camera size={20} />} />
      </div>

      {/* Activity + detection mix */}
      <div className="grid gap-6 xl:grid-cols-[2fr_1fr]">
        <Card className="p-0 overflow-hidden">
          <div className="flex items-center justify-between border-b border-border bg-base px-5 py-4">
            <h2 className="text-base font-semibold text-th flex items-center gap-2"><Activity size={16} className="text-accent" /> Detection Activity</h2>
            <span className="text-xs text-faint">last 12 hours</span>
          </div>
          <div className="h-[300px] p-4">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={hourly} margin={{ top: 10, right: 10, left: -18, bottom: 0 }}>
                <defs>
                  <linearGradient id="gKnown" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-accent)" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="var(--color-accent)" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gUnknown" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-dnd)" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="var(--color-dnd)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" opacity={0.4} vertical={false} />
                <XAxis dataKey="t" tick={{ fill: "var(--color-faint)", fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "var(--color-faint)", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                <Tooltip contentStyle={tooltipStyle} />
                <Area type="monotone" dataKey="known" name="Recognized" stroke="var(--color-accent)" strokeWidth={2} fill="url(#gKnown)" />
                <Area type="monotone" dataKey="unknown" name="Unknown" stroke="var(--color-dnd)" strokeWidth={2} fill="url(#gUnknown)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border bg-base px-5 py-4">
            <h2 className="text-base font-semibold text-th flex items-center gap-2"><Layers size={16} className="text-accent" /> Events by Module</h2>
          </div>
          <div className="p-5 space-y-3.5">
            {byModuleToday.length === 0 ? (
              <div className="py-12 text-center"><Layers size={28} className="mx-auto text-border mb-2" /><p className="text-muted text-sm">No events today.</p></div>
            ) : byModuleToday.map((d) => (
              <div key={d.id}>
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="text-tx">{moduleName(d.id)}</span>
                  <span className="font-mono text-muted">{d.value}</span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-base overflow-hidden">
                  <div className={cx("h-full rounded-full transition-all duration-500", MOD_BAR[d.id] ?? "bg-accent")} style={{ width: `${(d.value / moduleMax) * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Attendance + AI insights + system health */}
      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border bg-base px-5 py-4">
            <h2 className="text-base font-semibold text-th flex items-center gap-2"><Users2 size={16} className="text-accent" /> Attendance Today</h2>
          </div>
          <div className="p-4">
            <div className="h-[180px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={empList.length === 0 ? [{ name: "No data", value: 1, color: "var(--color-border)" }] : attendanceMix}
                    dataKey="value" innerRadius={48} outerRadius={72} paddingAngle={2} stroke="none">
                    {(empList.length === 0 ? [{ color: "var(--color-border)" }] : attendanceMix).map((e, i) => <Cell key={i} fill={e.color} />)}
                  </Pie>
                  <Tooltip contentStyle={tooltipStyle} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="grid grid-cols-2 gap-2 mt-2 text-center">
              <div><p className="text-lg font-bold font-mono text-online">{present}</p><p className="text-[10px] text-faint uppercase tracking-wider">Present</p></div>
              <div><p className="text-lg font-bold font-mono text-faint">{absent}</p><p className="text-[10px] text-faint uppercase tracking-wider">Absent</p></div>
            </div>
          </div>
        </Card>

        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border bg-base px-5 py-4">
            <h2 className="text-base font-semibold text-th flex items-center gap-2"><Sparkles size={16} className="text-accent" /> AI Insights</h2>
          </div>
          <div className="p-3 space-y-2">
            <Insight icon={<TrendingUp size={15} />} tone="accent" title="Peak detection window"
              body={peakHour.known + peakHour.unknown > 0 ? `Most activity around ${peakHour.t} with ${peakHour.known + peakHour.unknown} detections.` : "No detections recorded in the last 12 hours."} />
            <Insight icon={<UserX size={15} />} tone={unknownCount > knownCount && todaysAlerts.length ? "danger" : "warning"} title="Unknown ratio"
              body={todaysAlerts.length ? `${Math.round((unknownCount / todaysAlerts.length) * 100)}% of today's detections were unrecognized faces.` : "No detections today yet."} />
            <Insight icon={<CheckCircle2 size={15} />} tone={rate >= 75 ? "success" : "warning"} title="Attendance health"
              body={empList.length ? `${rate}% present. ${absent} not yet checked in.` : "No employees registered yet."} />
            <Insight icon={<Wifi size={15} />} tone={activeCams === camList.length ? "success" : "danger"} title="Camera coverage"
              body={camList.length ? `${activeCams}/${camList.length} cameras online${activeCams < camList.length ? " — some feeds are down." : " — full coverage."}` : "No cameras configured."} />
          </div>
        </Card>

        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border bg-base px-5 py-4">
            <h2 className="text-base font-semibold text-th flex items-center gap-2"><Cpu size={16} className="text-accent" /> System Health</h2>
          </div>
          <div className="px-5 py-2">
            <HealthRow label="Recognition Engine" value="Operational" status="online" />
            <HealthRow label="Active Analytic" value={analyticLabel} status="online" />
            <HealthRow label="Enrolled Identities" value={`${empList.length}`} status={empList.length ? "online" : "idle"} />
            <HealthRow label="Camera Feeds" value={`${activeCams}/${camList.length} online`} status={activeCams === camList.length && camList.length ? "online" : activeCams ? "idle" : "dnd"} />
            <HealthRow label="Alerts (24h window)" value={`${todaysAlerts.length}`} status={unknownCount ? "idle" : "online"} />
          </div>
        </Card>
      </div>

      {/* Camera status strip */}
      <Card className="p-0 overflow-hidden">
        <div className="flex items-center justify-between border-b border-border bg-base px-5 py-4">
          <h2 className="text-base font-semibold text-th flex items-center gap-2"><Camera size={16} className="text-accent" /> Camera Fleet</h2>
          <span className="flex items-center gap-1.5 text-xs text-faint"><span className={cx("h-2 w-2 rounded-full", activeCams ? "bg-online animate-pulse" : "bg-faint")} />{activeCams} online</span>
        </div>
        <div className="p-4">
          {camList.length === 0 ? (
            <div className="py-10 text-center"><Camera size={32} className="mx-auto text-border mb-3" /><p className="text-muted text-sm">No cameras configured.</p></div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {camList.map((cam: any) => (
                <div key={cam.id} className="flex items-center gap-3 rounded-md border border-border bg-base/60 p-3">
                  <span className={cx("h-2.5 w-2.5 shrink-0 rounded-full", cam.is_active ? "bg-online animate-pulse" : "bg-dnd")} />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold text-tx truncate">{cam.name}</p>
                    <p className="text-[10px] text-faint truncate">{cam.location ?? "Unassigned"} · {cam.department ?? "—"}</p>
                  </div>
                  <Clock size={12} className="text-faint shrink-0" />
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
