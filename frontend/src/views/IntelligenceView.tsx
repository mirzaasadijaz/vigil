import { useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend,
} from "recharts";
import { BarChart3, Activity, Layers, ShieldAlert, Camera, Users2 } from "lucide-react";
import { Card, MetricCard } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { ModuleFilter } from "../components/ModuleFilter";
import { MODULES, MODULE_BY_ID, moduleName } from "../lib/modules";

const tooltipStyle = {
  background: "var(--color-floating)", border: "1px solid var(--color-border)",
  borderRadius: 8, color: "var(--color-th)", fontSize: 12,
} as const;

// module id -> chart color (CSS var, re-themes automatically)
const MOD_COLOR: Record<string, string> = {
  attendance: "var(--color-accent)", headcount: "var(--color-online)",
  occupancy: "var(--color-accent-soft)", idle: "var(--color-idle)",
  intrusion: "var(--color-dnd)", mobile: "var(--color-idle)", fire_smoke: "var(--color-dnd)",
};
const colorFor = (m: string) => MOD_COLOR[m] ?? "var(--color-muted)";

const CRITICAL = /(unknown|intrusion|over_capacity|fire|smoke|phone)/;

export default function IntelligenceView() {
  const [mod, setMod] = useState("all");
  const { data, refetch } = useApi<any[]>(`/alerts/?limit=500${mod !== "all" ? `&module=${mod}` : ""}`);
  usePolling(refetch, 10000);
  const list = data ?? [];

  const activeModules = useMemo(() => [...new Set(list.map((a) => a.module).filter(Boolean))] as string[], [list]);
  const criticalCount = list.filter((a) => CRITICAL.test((a.event_type || a.violation_type || "").toLowerCase())).length;
  const camerasReporting = useMemo(() => new Set(list.map((a) => a.camera_id).filter((v) => v != null)).size, [list]);

  const byModule = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of list) if (a.module) m.set(a.module, (m.get(a.module) ?? 0) + 1);
    return [...m.entries()].map(([id, value]) => ({ id, name: moduleName(id), value })).sort((a, b) => b.value - a.value);
  }, [list]);

  const byType = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of list) { const t = a.event_type || "other"; m.set(t, (m.get(t) ?? 0) + 1); }
    return [...m.entries()].map(([t, value]) => ({ name: t.replace(/_/g, " "), value })).sort((a, b) => b.value - a.value).slice(0, 8);
  }, [list]);

  // 24h stacked-by-module
  const stackKeys = mod === "all" ? activeModules : [mod];
  const hourly = useMemo(() => {
    const now = new Date();
    const rows: any[] = [];
    const map = new Map<string, any>();
    for (let i = 23; i >= 0; i--) {
      const d = new Date(now.getTime() - i * 3600_000);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}-${d.getHours()}`;
      const row: any = { t: d.toLocaleTimeString("en-US", { hour: "numeric", hour12: true }) };
      for (const k of stackKeys) row[k] = 0;
      map.set(key, row); rows.push(row);
    }
    for (const a of list) {
      const d = new Date(a.timestamp);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}-${d.getHours()}`;
      const row = map.get(key);
      if (row && a.module && (mod === "all" || a.module === mod)) row[a.module] = (row[a.module] ?? 0) + 1;
    }
    return rows;
  }, [list, mod, stackKeys.join(",")]);

  const topIdentities = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of list) if (a.employee?.name) m.set(a.employee.name, (m.get(a.employee.name) ?? 0) + 1);
    return [...m.entries()].map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value).slice(0, 8);
  }, [list]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th flex items-center gap-2"><BarChart3 size={20} className="text-accent" /> Analytics</h1>
          <p className="mt-1 text-sm text-muted">Event intelligence across all AI modules · last 500 events</p>
        </div>
        <ModuleFilter value={mod} onChange={setMod} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard tone="accent" label="Total Events" value={list.length} hint="recent window" icon={<Activity size={20} />} />
        <MetricCard tone="accent" label="Active Modules" value={activeModules.length} hint={`${MODULES.length} available`} icon={<Layers size={20} />} />
        <MetricCard tone={criticalCount ? "danger" : "success"} label="Critical Events" value={criticalCount} hint="unknown · intrusion · fire…" icon={<ShieldAlert size={20} />} />
        <MetricCard tone="accent" label="Cameras Reporting" value={camerasReporting} hint="distinct sources" icon={<Camera size={20} />} />
      </div>

      {/* events by module + by type */}
      <div className="grid gap-6 xl:grid-cols-2">
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border bg-base px-5 py-4"><h2 className="text-base font-semibold text-th flex items-center gap-2"><Layers size={16} className="text-accent" /> Events by Module</h2></div>
          <div className="h-[280px] p-4">
            {byModule.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={byModule} layout="vertical" margin={{ top: 4, right: 16, left: 12, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" opacity={0.4} horizontal={false} />
                  <XAxis type="number" tick={{ fill: "var(--color-faint)", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" width={130} tick={{ fill: "var(--color-muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--color-hover)" }} />
                  <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={18}>
                    {byModule.map((d) => <Cell key={d.id} fill={colorFor(d.id)} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="flex h-full items-center justify-center text-sm text-faint">No events yet.</div>}
          </div>
        </Card>

        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border bg-base px-5 py-4"><h2 className="text-base font-semibold text-th flex items-center gap-2"><BarChart3 size={16} className="text-accent" /> Events by Type</h2></div>
          <div className="h-[280px] p-4">
            {byType.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={byType} layout="vertical" margin={{ top: 4, right: 16, left: 12, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" opacity={0.4} horizontal={false} />
                  <XAxis type="number" tick={{ fill: "var(--color-faint)", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" width={110} tick={{ fill: "var(--color-muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--color-hover)" }} />
                  <Bar dataKey="value" fill="var(--color-accent)" radius={[0, 4, 4, 0]} barSize={16} />
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="flex h-full items-center justify-center text-sm text-faint">No events yet.</div>}
          </div>
        </Card>
      </div>

      {/* hourly stacked by module */}
      <Card className="p-0 overflow-hidden">
        <div className="flex items-center justify-between border-b border-border bg-base px-5 py-4">
          <h2 className="text-base font-semibold text-th flex items-center gap-2"><Activity size={16} className="text-accent" /> Events by Hour</h2>
          <span className="text-xs text-faint">last 24h · stacked by module</span>
        </div>
        <div className="h-[300px] p-4">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={hourly} margin={{ top: 10, right: 10, left: -18, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" opacity={0.4} vertical={false} />
              <XAxis dataKey="t" tick={{ fill: "var(--color-faint)", fontSize: 10 }} axisLine={false} tickLine={false} interval={2} />
              <YAxis tick={{ fill: "var(--color-faint)", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--color-hover)" }} />
              <Legend formatter={(v) => <span style={{ color: "var(--color-muted)", fontSize: 11 }}>{moduleName(v)}</span>} />
              {stackKeys.map((k) => (
                <Bar key={k} dataKey={k} name={k} stackId="a" fill={colorFor(k)} radius={[2, 2, 0, 0]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      {/* top identities (attendance) */}
      <Card className="p-0 overflow-hidden">
        <div className="border-b border-border bg-base px-5 py-4"><h2 className="text-base font-semibold text-th flex items-center gap-2"><Users2 size={16} className="text-accent" /> Most Recognized Identities</h2></div>
        <div className="h-[260px] p-4">
          {topIdentities.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={topIdentities} layout="vertical" margin={{ top: 4, right: 16, left: 12, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" opacity={0.4} horizontal={false} />
                <XAxis type="number" tick={{ fill: "var(--color-faint)", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                <YAxis type="category" dataKey="name" width={130} tick={{ fill: "var(--color-muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--color-hover)" }} />
                <Bar dataKey="value" fill={MODULE_BY_ID.attendance ? "var(--color-accent)" : "var(--color-accent)"} radius={[0, 4, 4, 0]} barSize={16} />
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="flex h-full items-center justify-center text-sm text-faint">No recognized identities in this window.</div>}
        </div>
      </Card>
    </div>
  );
}
