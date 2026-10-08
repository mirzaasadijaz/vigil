import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const cx = (...classes) => classes.filter(Boolean).join(" ");

const navItems = [
  { id: "overview", label: "Overview", short: "OV" },
  { id: "operations", label: "Operations", short: "OP" },
  { id: "intelligence", label: "Intelligence", short: "IN" },
  { id: "assets", label: "Assets & Access", short: "AA" },
  { id: "admin", label: "Admin Settings", short: "AD" },
];

const topStats = [
  { label: "Total Cameras", value: "124", hint: "24 active in demo" },
  { label: "Live Alerts", value: "03", hint: "1 critical" },
  { label: "Uptime", value: "99.98%", hint: "Stable" },
  { label: "Avg Response", value: "420 ms", hint: "Alert dispatch" },
];

const activityData = [
  { t: "08:00", detections: 12, accesses: 7, anomalies: 1 },
  { t: "10:00", detections: 18, accesses: 10, anomalies: 2 },
  { t: "12:00", detections: 27, accesses: 18, anomalies: 1 },
  { t: "14:00", detections: 34, accesses: 22, anomalies: 4 },
  { t: "16:00", detections: 30, accesses: 27, anomalies: 2 },
  { t: "18:00", detections: 20, accesses: 31, anomalies: 3 },
];

const zoneTraffic = [
  { name: "Main Gate", value: 34 },
  { name: "Parking", value: 22 },
  { name: "Lobby", value: 18 },
  { name: "Server Room", value: 12 },
  { name: "Warehouse", value: 14 },
];

const deviceHealth = [
  { name: "Online", value: 18, color: "#22d3ee" },
  { name: "Warning", value: 4, color: "#f59e0b" },
  { name: "Offline", value: 2, color: "#fb7185" },
];

const devices = [
  { name: "CAM_01_WEST_LOBBY", status: "Online", zone: "Lobby", ip: "192.168.1.104", type: "Camera", fw: "v2.4.11-STABLE" },
  { name: "CAM_04_NORTH_PERIMETER", status: "Alert", zone: "Perimeter", ip: "192.168.1.107", type: "Camera", fw: "v2.4.10-STABLE" },
  { name: "CAM_08_LOADING_DOCK", status: "Online", zone: "Dock", ip: "192.168.1.112", type: "Camera", fw: "v2.4.11-STABLE" },
  { name: "SENSOR_02_FIRE_EXIT", status: "Offline", zone: "Fire Exit", ip: "192.168.1.145", type: "Sensor", fw: "v2.3.00-DEPRECATED" },
  { name: "DOOR_09_DATA_CENTER", status: "Online", zone: "Data Center", ip: "192.168.1.201", type: "Controller", fw: "v1.0.02-SECURITY" },
];

const accessRules = [
  { title: "Face Recognition", description: "Biometric access for authorized staff" },
  { title: "Visitor QR Pass", description: "Temporary access through a QR credential" },
  { title: "Time Schedules", description: "Rules enforced by time window" },
  { title: "Role Based Access", description: "Permissions by operator, guard, visitor" },
];

const logs = [
  { time: "14:22:05", type: "Human Detection", detail: "OBJECT_A // ZONE_04" },
  { time: "14:26:12", type: "Vehicle Log", detail: "PLATE_READ_XF89" },
  { time: "14:28:44", type: "Motion Anomaly", detail: "Unauthorized access attempt" },
  { time: "14:35:50", type: "Human Detection", detail: "CLEANING_CREW_09" },
  { time: "14:42:01", type: "System Log", detail: "Light threshold adjusted" },
];

const alerts = [
  { title: "Unauthorized face at North Perimeter", severity: "Critical", status: "Escalated" },
  { title: "Camera CAM_03 buffering", severity: "Medium", status: "Monitoring" },
  { title: "Door anomaly at Data Center", severity: "High", status: "Open" },
];

const liveTiles = [
  { name: "HQ Lobby B", zone: "Live", fps: 30, res: "1920×1080", time: "14:22:10 UTC", badge: "LIVE / MOTION_DETECTED" },
  { name: "Parking Level 02", zone: "Live", fps: 24, res: "2K", time: "14:22:08 UTC", badge: "LIVE" },
  { name: "Office West", zone: "Live", fps: 30, res: "1080p", time: "14:21:58 UTC", badge: "LIVE" },
  { name: "Warehouse_C3", zone: "Live", fps: 30, res: "2K", time: "14:21:50 UTC", badge: "LIVE" },
  { name: "Meeting_Room_04", zone: "Live", fps: 20, res: "1080p", time: "14:21:44 UTC", badge: "LIVE" },
  { name: "Perimeter_Wall_South", zone: "Alert", fps: 24, res: "1080p", time: "14:21:34 UTC", badge: "ANOMALY DETECTED" },
];

const incidentItems = [
  { title: "Incident #4289_XRAY", label: "Archive Playback", time: "2023-11-24 14:22:05" },
  { title: "Motion Detected [CAM_04]", label: "Perimeter", time: "2023-11-24 14:28:44" },
  { title: "Access Denied", label: "Lobby Gate", time: "2023-11-24 14:33:11" },
  { title: "Vehicle Read", label: "Loading Dock", time: "2023-11-24 14:41:00" },
];

const timeline = [
  { t: "14:20", kind: "motion" },
  { t: "14:25", kind: "access" },
  { t: "14:30", kind: "motion" },
  { t: "14:35", kind: "vehicle" },
  { t: "14:40", kind: "access" },
];

const mapNodes = [
  { x: "18%", y: "22%", status: "stable" },
  { x: "48%", y: "51%", status: "alert" },
  { x: "78%", y: "78%", status: "stable" },
];

const heatCells = [
  [2, 3, 5, 7, 6, 4],
  [1, 2, 6, 9, 8, 5],
  [1, 3, 7, 10, 9, 6],
  [2, 4, 6, 8, 7, 4],
  [1, 2, 4, 6, 5, 3],
  [0, 1, 2, 3, 3, 2],
];

const alertParameters = [
  { id: "p01", tier: "Core", category: "Safety & Compliance", name: "Helmet / Hard Hat Non-Compliance", enabled: true },
  { id: "p02", tier: "Core", category: "Safety & Security", name: "Restricted Zone Unauthorized Intrusion", enabled: true },
  { id: "p03", tier: "Core", category: "Safety — Critical", name: "Fire, Smoke & Spark Detection", enabled: true },
  { id: "p04", tier: "Core", category: "Productivity", name: "Operator Absent from Active Machine", enabled: true },
  { id: "p05", tier: "Core", category: "Safety & Compliance", name: "PPE Full Kit Compliance Check", enabled: true },
  { id: "p06", tier: "Core", category: "Safety", name: "Slip & Fall Hazard — Spillage Detection", enabled: true },
  { id: "p07", tier: "Core", category: "Security & Loss Prevention", name: "Theft / Unauthorized Asset Removal", enabled: true },
  { id: "p08", tier: "Core", category: "Workforce Productivity", name: "Idle Time Detection — Operator Inactivity", enabled: true },
  { id: "p09", tier: "Core", category: "Safety — Critical", name: "Emergency Exit Blockage", enabled: true },
  { id: "p10", tier: "Core", category: "Safety", name: "Crowd / Overcrowding in Hazardous Zone", enabled: true },
  { id: "p11", tier: "Addon", category: "Safety Add-On", name: "Gloves & Mask Compliance — Chemical Zones", enabled: false },
  { id: "p12", tier: "Addon", category: "Process Efficiency", name: "Machine Running Without Any Operator", enabled: false },
  { id: "p13", tier: "Addon", category: "Workforce Health", name: "Fatigue / Low Activity Detection", enabled: false },
  { id: "p14", tier: "Addon", category: "Quality Control", name: "Improper Material Handling — Fabric Dragging", enabled: false },
  { id: "p15", tier: "Addon", category: "Quality Control", name: "Wrong Material Movement / Batch Mix-Up", enabled: false },
  { id: "p16", tier: "Addon", category: "Security & Compliance", name: "Contractor vs. Staff Compliance Violation", enabled: false },
  { id: "p17", tier: "Addon", category: "Security", name: "Unauthorized Machine Usage", enabled: false },
  { id: "p18", tier: "Addon", category: "Workforce Productivity", name: "Break Time Violation — Extended Absence", enabled: false },
  { id: "p19", tier: "Addon", category: "Safety", name: "Machine Guard Removal Detection", enabled: false },
  { id: "p20", tier: "Addon", category: "Process Efficiency", name: "Abnormal Machine Stoppage Frequency", enabled: false },
  { id: "p21", tier: "Addon", category: "Workforce Management", name: "Headcount Verification Per Shift", enabled: false },
  { id: "p22", tier: "Addon", category: "Quality / Compliance", name: "Housekeeping & Cleanliness Compliance", enabled: false },
  { id: "p23", tier: "Addon", category: "Security", name: "Visitor / Unauthorized Person in Production Zone", enabled: false },
  { id: "p24", tier: "Addon", category: "Workforce Health", name: "Posture Compliance — Role-Based Sitting/Standing", enabled: false },
  { id: "p25", tier: "Addon", category: "Safety", name: "Object Left Unattended in Walkway", enabled: false },
  { id: "p26", tier: "Addon", category: "Productivity", name: "Multi-Operator Clustering / Inefficiency Indicator", enabled: false },
  { id: "p27", tier: "Addon", category: "Process", name: "Shift Handover Compliance Verification", enabled: false },
  { id: "p28", tier: "Addon", category: "Security", name: "Suspicious Repeated Perimeter Approach", enabled: false },
  { id: "p29", tier: "Addon", category: "Safety", name: "Forklift / Vehicle Proximity to Pedestrian", enabled: false },
  { id: "p30", tier: "Addon", category: "Compliance / Process", name: "End-of-Day Zone Clearance Verification", enabled: false },
];

const Card = React.forwardRef(function Card({ children, className = "" }, ref) {
  return (
    <div
      ref={ref}
      className={cx(
        "rounded-[28px] border border-cyan-400/10 bg-[#11161f]/90 shadow-2xl shadow-black/40 backdrop-blur-xl transition-all duration-300 hover:-translate-y-0.5 hover:shadow-cyan-500/10",
        className
      )}
    >
      {children}
    </div>
  );
});

function Button({ children, className = "", variant = "default", ...props }) {
  const styles =
    variant === "ghost"
      ? "border border-cyan-400/15 bg-white/4 text-slate-100 hover:bg-white/8"
      : variant === "subtle"
        ? "border border-white/10 bg-white/6 text-slate-100 hover:bg-white/10"
        : "bg-cyan-300 text-slate-950 hover:bg-cyan-200";

  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium transition-all duration-300 active:scale-[0.99] hover:-translate-y-0.5",
        styles,
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

function Badge({ children, tone = "neutral" }) {
  const styles =
    tone === "success"
      ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-200"
      : tone === "warning"
        ? "border-amber-400/20 bg-amber-400/10 text-amber-200"
        : tone === "danger"
          ? "border-rose-400/20 bg-rose-400/10 text-rose-200"
          : "border-cyan-400/15 bg-white/5 text-slate-200";

  return <span className={cx("inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold tracking-wider uppercase", styles)}>{children}</span>;
}

function Switch({ checked, onChange, label }) {
  return (
    <button
      type="button"
      onClick={onChange}
      className="flex w-full items-center justify-between rounded-2xl border border-white/10 bg-black/20 px-4 py-3 text-left transition-all duration-300 hover:bg-black/30"
      aria-pressed={checked}
      aria-label={label}
    >
      <div className="pr-4">
        <p className="text-sm font-medium text-white">{label}</p>
      </div>
      <span
        className={cx(
          "relative inline-flex h-7 w-12 items-center rounded-full border transition-all duration-300",
          checked ? "border-cyan-300/30 bg-cyan-300/20" : "border-white/15 bg-white/5"
        )}
      >
        <span
          className={cx(
            "absolute h-5 w-5 rounded-full bg-white shadow-lg transition-all duration-300",
            checked ? "left-6 bg-cyan-200" : "left-1"
          )}
        />
      </span>
    </button>
  );
}

function SectionTitle({ title, subtitle, action }) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <h2 className="text-xl font-semibold tracking-tight text-white sm:text-2xl">{title}</h2>
        {subtitle ? <p className="mt-1 text-sm text-slate-400">{subtitle}</p> : null}
      </div>
      {action ? <div>{action}</div> : null}
    </div>
  );
}

function MetricCard({ label, value, hint, accent = "CY" }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm text-slate-400">{label}</p>
          <p className="mt-2 text-3xl font-semibold tracking-tight text-white">{value}</p>
          <p className="mt-2 text-xs text-slate-500">{hint}</p>
        </div>
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-400/15 bg-cyan-400/10 text-cyan-200 shadow-lg shadow-cyan-900/20">
          {accent}
        </div>
      </div>
    </Card>
  );
}

function useInView(threshold = 0.3) {
  const ref = useRef(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
        }
      },
      { threshold }
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold]);

  return [ref, isVisible];
}

function Sidebar({ active, setActive, collapsed, setCollapsed }) {
  return (
    <aside
      className={cx(
        "shrink-0 border-b border-cyan-400/10 bg-[#090d14]/95 p-4 backdrop-blur-xl xl:sticky xl:top-0 xl:h-screen xl:border-b-0 xl:border-r transition-all duration-300",
        collapsed ? "w-full xl:w-[88px]" : "w-full xl:w-[280px]"
      )}
    >
      <div className={cx("rounded-[24px] border border-cyan-400/10 bg-white/5 p-4 shadow-2xl shadow-black/20 transition-all duration-300", collapsed && "xl:p-3")}>
        <div className={cx("flex items-center gap-3", collapsed && "xl:justify-center")}>
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
            <span className="text-lg font-bold">CS</span>
          </div>
          {!collapsed ? (
            <div>
              <p className="text-sm font-semibold tracking-[0.22em] text-cyan-300">CORE_SENTRY</p>
              <p className="text-xs text-slate-400">SECURITY COMMAND CENTER</p>
            </div>
          ) : null}
        </div>

        {!collapsed ? (
          <div className="mt-4 flex items-center justify-between rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-slate-300">
            <span>Operator_01</span>
            <Badge tone="success">Online</Badge>
          </div>
        ) : null}
      </div>

      <div className="mt-4 flex items-center gap-2">
        <Button variant="ghost" className="w-full justify-start" onClick={() => setCollapsed((v) => !v)}>
          {collapsed ? "Show Panel" : "Hide Panel"}
        </Button>
      </div>

      <nav className={cx("mt-4 grid gap-2", collapsed ? "grid-cols-1" : "grid-cols-2 xl:grid-cols-1")}>
        {navItems.map((item) => {
          const activeItem = active === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActive(item.id)}
              className={cx(
                "group flex items-center justify-between rounded-2xl border px-4 py-3 text-left transition-all duration-300 hover:-translate-y-0.5",
                activeItem
                  ? "border-cyan-400/30 bg-cyan-400/10 text-white shadow-lg shadow-cyan-500/10"
                  : "border-white/10 bg-white/0 text-slate-300 hover:border-white/20 hover:bg-white/5",
                collapsed && "xl:justify-center xl:px-3"
              )}
              title={item.label}
            >
              <span className="flex items-center gap-3">
                <span
                  className={cx(
                    "flex h-7 w-7 items-center justify-center rounded-xl border text-[11px] font-semibold transition-all duration-300",
                    activeItem ? "border-cyan-300/30 bg-cyan-300/10 text-cyan-200" : "border-white/10 bg-white/5 text-slate-400"
                  )}
                >
                  {item.short}
                </span>
                {!collapsed ? <span className="text-sm font-medium">{item.label}</span> : null}
              </span>
              {!collapsed ? <span className={cx("text-sm transition-transform duration-300 group-hover:translate-x-1", activeItem ? "text-cyan-300" : "text-slate-500")}>›</span> : null}
            </button>
          );
        })}
      </nav>

      {!collapsed ? (
        <div className="mt-5 rounded-[24px] border border-cyan-400/15 bg-gradient-to-br from-cyan-500/10 to-violet-500/10 p-5">
          <p className="text-sm font-semibold text-white">Pitch Ready</p>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            Operator console, incident replay, map view, population heatview, live cameras, and configurable AI parameters are integrated for a client-facing demo.
          </p>
        </div>
      ) : null}
    </aside>
  );
}

function TopBar({ collapsed, setCollapsed }) {
  return (
    <header className="rounded-[30px] border border-cyan-400/10 bg-[#11161f]/85 p-5 shadow-2xl shadow-black/30 backdrop-blur-xl">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="text-xs uppercase tracking-[0.28em] text-cyan-300/80">CORE_SENTRY</div>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white lg:text-4xl">AI Surveillance Dashboard</h1>
          <p className="mt-2 max-w-3xl text-sm text-slate-400">
            Premium command center for live feeds, incident archive, map visualization, density heatview, analytics, access control, alerts, logs, and AI parameter configuration.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" onClick={() => setCollapsed((v) => !v)}>{collapsed ? "Show Sidebar" : "Hide Sidebar"}</Button>
          <Button variant="ghost">Terminal Search</Button>
          <Button>New Incident</Button>
        </div>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {topStats.map((item) => (
          <div key={item.label} className="rounded-2xl border border-white/10 bg-black/20 p-4 transition-all duration-300 hover:-translate-y-0.5 hover:bg-black/25">
            <p className="text-xs uppercase tracking-wider text-slate-500">{item.label}</p>
            <p className="mt-2 text-xl font-semibold text-white">{item.value}</p>
            <p className="mt-2 text-xs text-slate-500">{item.hint}</p>
          </div>
        ))}
      </div>
    </header>
  );
}

function LiveCameraPreview() {
  return (
    <div className="relative aspect-video overflow-hidden rounded-[26px] border border-cyan-400/20 bg-[radial-gradient(circle_at_top,rgba(56,189,248,0.18),transparent_35%),linear-gradient(135deg,#0a1019_0%,#02070d_100%)]">
      <div className="absolute inset-0 opacity-35">
        <div className="absolute inset-0 bg-[linear-gradient(transparent_49%,rgba(255,255,255,0.05)_50%,transparent_51%)] bg-[length:100%_6px]" />
        <div className="absolute inset-0 animate-pulse border border-cyan-400/20" />
      </div>

      <div className="absolute left-4 top-4 flex gap-2">
        <Badge tone="success">LIVE</Badge>
        <Badge tone="neutral">Motion Detected</Badge>
      </div>

      <div className="absolute right-4 top-4 rounded-2xl border border-white/10 bg-black/35 px-3 py-2 text-right text-xs text-slate-200 backdrop-blur">
        <p>FPS</p>
        <p className="text-lg font-semibold text-white">18.6</p>
      </div>

      <div className="absolute bottom-4 left-4 right-4 grid grid-cols-3 gap-3">
        <div className="rounded-2xl border border-white/10 bg-black/35 p-3 text-xs text-slate-200 backdrop-blur">
          <p className="text-slate-400">Detected</p>
          <p className="mt-1 text-lg font-semibold text-white">2 Persons</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-black/35 p-3 text-xs text-slate-200 backdrop-blur">
          <p className="text-slate-400">Identity</p>
          <p className="mt-1 text-lg font-semibold text-white">Ahmad Javed</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-black/35 p-3 text-xs text-slate-200 backdrop-blur">
          <p className="text-slate-400">Access</p>
          <p className="mt-1 text-lg font-semibold text-emerald-300">Granted</p>
        </div>
      </div>
    </div>
  );
}

function OverviewView() {
  const [activityRef, activityVisible] = useInView(0.3);
  const [healthRef, healthVisible] = useInView(0.3);

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Total Cameras" value="124" hint="24 active in demo" accent="◌" />
        <MetricCard label="Active Alerts" value="03" hint="1 critical" accent="!" />
        <MetricCard label="System Load" value="12.4%" hint="Stable" accent="⌁" />
        <MetricCard label="Storage" value="88%" hint="Healthy headroom" accent="▣" />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.7fr_1fr]">
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Live Camera Wall" subtitle="Quick overview of active streams and status" />
          </div>
          <div className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-3">
            {liveTiles.map((cam, idx) => (
              <div key={cam.name} className={cx("group rounded-2xl border p-3 transition-all duration-300 hover:-translate-y-1", cam.badge.includes("ANOMALY") ? "border-rose-400/20 bg-rose-500/10" : "border-cyan-400/15 bg-black/20") }>
                <div className="flex items-center justify-between">
                  <Badge tone={cam.badge.includes("ANOMALY") ? "danger" : cam.badge.includes("MOTION") ? "warning" : "success"}>{cam.badge}</Badge>
                  <span className="text-xs text-slate-500">{cam.time}</span>
                </div>
                <div className={cx("mt-3 h-28 rounded-xl border border-white/10 transition-all duration-300 group-hover:scale-[1.01]", idx === 0 ? "bg-[linear-gradient(135deg,rgba(15,23,42,1),rgba(30,41,59,1))]" : "bg-[linear-gradient(135deg,rgba(10,15,23,1),rgba(25,34,48,1))]")} />
                <p className="mt-3 text-sm font-semibold text-white">{cam.name}</p>
                <p className="mt-1 text-xs text-slate-400">{cam.res} · FPS: {cam.fps}</p>
              </div>
            ))}
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <div className="border-b border-white/10 p-5">
              <SectionTitle title="Operator Console" subtitle="Quick actions and operational control" />
            </div>
            <div className="space-y-3 p-5">
              <Button className="w-full justify-start">Acknowledge Current Alert</Button>
              <Button variant="ghost" className="w-full justify-start">Lock Down Zone</Button>
              <Button variant="ghost" className="w-full justify-start">Send Guard Dispatch</Button>
              <Button variant="ghost" className="w-full justify-start">Export Incident Log</Button>
            </div>
          </Card>

          <Card>
            <div className="border-b border-white/10 p-5">
              <SectionTitle title="Recent Alerts" subtitle="What is happening right now" />
            </div>
            <div className="space-y-3 p-5">
              {alerts.map((a) => (
                <div key={a.title} className="rounded-2xl border border-white/10 bg-black/20 p-4 transition-all duration-300 hover:bg-black/30">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium text-white">{a.title}</p>
                      <p className="mt-1 text-xs text-slate-400">Status: {a.status}</p>
                    </div>
                    <Badge tone={a.severity === "Critical" ? "danger" : a.severity === "High" ? "warning" : "neutral"}>{a.severity}</Badge>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card ref={activityRef} className={cx("xl:col-span-2 transition-all duration-700", activityVisible ? "translate-y-0 opacity-100" : "translate-y-6 opacity-60") }>
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Activity Analytics" subtitle="Detections, access events, and anomalies across time" />
          </div>
          <div className="h-[320px] p-5">
            {activityVisible ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={activityData}>
                  <defs>
                    <linearGradient id="det" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="acc" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="ano" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#fb7185" stopOpacity={0.32} />
                      <stop offset="95%" stopColor="#fb7185" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.12} />
                  <XAxis dataKey="t" tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: "#020617", border: "1px solid rgba(255,255,255,.12)" }} />
                  <Legend />
                  <Area type="monotone" dataKey="detections" stroke="#22d3ee" fill="url(#det)" />
                  <Area type="monotone" dataKey="accesses" stroke="#8b5cf6" fill="url(#acc)" />
                  <Area type="monotone" dataKey="anomalies" stroke="#fb7185" fill="url(#ano)" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-white/10 bg-black/10 text-sm text-slate-500">
                Scroll to animate chart
              </div>
            )}
          </div>
        </Card>

        <Card ref={healthRef} className={cx("transition-all duration-700", healthVisible ? "translate-y-0 opacity-100" : "translate-y-6 opacity-60") }>
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Device Health" subtitle="Fleet status at a glance" />
          </div>
          <div className="h-[320px] p-5">
            {healthVisible ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={deviceHealth} dataKey="value" innerRadius={68} outerRadius={102} paddingAngle={4}>
                    {deviceHealth.map((entry, idx) => (
                      <Cell key={idx} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ background: "#020617", border: "1px solid rgba(255,255,255,.12)" }} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-white/10 bg-black/10 text-sm text-slate-500">
                Scroll to animate chart
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

function OperationsView() {
  return (
    <div className="grid gap-6 xl:grid-cols-[1.7fr_1fr]">
      <Card className="p-0 overflow-hidden">
        <div className="border-b border-white/10 p-5">
          <SectionTitle title="Live Feed" subtitle="Multi-camera wall for the client demo" />
        </div>
        <div className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-3">
          {liveTiles.map((cam, idx) => (
            <div key={cam.name} className={cx("group rounded-2xl border p-3 transition-all duration-300 hover:-translate-y-1", idx === 0 ? "border-cyan-400/30 bg-cyan-400/10" : "border-white/10 bg-black/20")}>
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>{cam.zone}</span>
                <Badge tone={idx === 0 ? "success" : cam.badge.includes("ANOMALY") ? "danger" : "neutral"}>{cam.badge}</Badge>
              </div>
              <div className="mt-3 h-28 rounded-xl border border-white/10 bg-[linear-gradient(135deg,rgba(15,23,42,1),rgba(30,41,59,1))] transition-all duration-300 group-hover:scale-[1.01]" />
              <div className="mt-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-white">{cam.name}</p>
                  <p className="text-xs text-slate-400">RES: {cam.res} · FPS: {cam.fps}</p>
                </div>
                <span className="text-xs text-slate-500">{cam.time}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div className="space-y-6">
        <Card>
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Incident Replay" subtitle="Archive playback and export controls" />
          </div>
          <div className="p-5">
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <Badge tone="neutral">2023-11-24 14:22:05</Badge>
              <Button variant="ghost">Export Log</Button>
              <Button variant="ghost">Clip Segment</Button>
            </div>
            <div className="relative aspect-video overflow-hidden rounded-[26px] border border-cyan-400/20 bg-[radial-gradient(circle_at_top,rgba(56,189,248,0.16),transparent_35%),linear-gradient(135deg,#0a1019_0%,#02070d_100%)]">
              <div className="absolute left-4 top-4 flex gap-2">
                <Badge tone="danger">Archive Playback</Badge>
                <Badge tone="neutral">CAM_04_NORTH_PERIMETER</Badge>
              </div>
              <div className="absolute inset-0 opacity-35 animate-pulse border border-cyan-400/10" />
            </div>
          </div>
        </Card>

        <Card>
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Operator Console" subtitle="Quick actions and operational control" />
          </div>
          <div className="space-y-3 p-5">
            <Button className="w-full justify-start">Acknowledge Alert</Button>
            <Button variant="ghost" className="w-full justify-start">Lock Down Zone</Button>
            <Button variant="ghost" className="w-full justify-start">Dispatch Guard</Button>
            <Button variant="ghost" className="w-full justify-start">Export Incident</Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

function IntelligenceView() {
  return (
    <div className="space-y-6">
      <div className="grid gap-6 xl:grid-cols-[1.4fr_0.9fr]">
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Map Overview" subtitle="Live tactical view with active nodes and incident markers" />
          </div>
          <div className="relative h-[560px] bg-[radial-gradient(circle_at_center,rgba(34,211,238,0.10),transparent_30%),linear-gradient(180deg,#070b12_0%,#04070c_100%)] p-5">
            <div className="absolute inset-0 opacity-25 bg-[linear-gradient(rgba(34,211,238,0.07)_1px,transparent_1px),linear-gradient(90deg,rgba(34,211,238,0.07)_1px,transparent_1px)] bg-[size:56px_56px]" />
            <div className="absolute left-6 top-6 rounded-2xl border border-cyan-400/20 bg-black/30 px-3 py-2 text-xs text-cyan-200">SYSTEM_STABLE</div>
            {mapNodes.map((n, idx) => (
              <div key={idx} className="absolute" style={{ left: n.x, top: n.y }}>
                <div className={cx("h-5 w-5 rounded-full shadow-[0_0_30px_rgba(34,211,238,.6)]", n.status === "alert" ? "bg-rose-400" : "bg-cyan-400")} />
                {n.status === "alert" ? <div className="mt-2 rounded-lg border border-rose-400/30 bg-rose-500/90 px-3 py-1 text-xs font-semibold tracking-wider text-white">MOTION_DETECTED [CAM_04]</div> : null}
              </div>
            ))}
            <div className="absolute bottom-6 left-6 grid grid-cols-2 gap-3">
              <div className="rounded-2xl border border-white/10 bg-black/30 p-4 text-sm text-slate-200">
                <p className="text-xs uppercase tracking-wider text-slate-500">Total Devices</p>
                <p className="mt-2 text-2xl font-semibold text-white">124</p>
              </div>
              <div className="rounded-2xl border border-white/10 bg-black/30 p-4 text-sm text-slate-200">
                <p className="text-xs uppercase tracking-wider text-slate-500">Active Alerts</p>
                <p className="mt-2 text-2xl font-semibold text-rose-200">03</p>
              </div>
            </div>
            <div className="absolute bottom-6 right-6 rounded-2xl border border-white/10 bg-black/30 px-4 py-2 text-xs text-slate-300">LAT: 34.0522° N · LONG: 118.2437° W</div>
          </div>
        </Card>

        <Card className="p-0 overflow-hidden">
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Population Density Heatview" subtitle="Crowd concentration across zones in real time" />
          </div>
          <div className="p-5 space-y-5">
            <div className="grid grid-cols-6 gap-2 rounded-[22px] border border-white/10 bg-black/20 p-3">
              {heatCells.flat().map((v, idx) => (
                <div
                  key={idx}
                  className="aspect-square rounded-lg transition-all duration-300 hover:scale-110"
                  style={{
                    background: `rgba(34, 211, 238, ${0.06 + v * 0.08})`,
                    boxShadow: v >= 8 ? "0 0 20px rgba(251,113,133,0.25)" : "none",
                  }}
                />
              ))}
            </div>

            <div className="grid grid-cols-3 gap-3 rounded-[22px] border border-white/10 bg-black/20 p-4 text-xs text-slate-300">
              <div>
                <p className="text-slate-500">Peak Zone</p>
                <p className="mt-2 text-lg font-semibold text-white">Main Gate</p>
              </div>
              <div>
                <p className="text-slate-500">Density Index</p>
                <p className="mt-2 text-lg font-semibold text-rose-200">84%</p>
              </div>
              <div>
                <p className="text-slate-500">Crowd Shift</p>
                <p className="mt-2 text-lg font-semibold text-cyan-200">North-East</p>
              </div>
            </div>

            {[
              ["Main Gate", 84, "High"],
              ["Parking", 63, "Medium"],
              ["Lobby", 57, "Medium"],
              ["Warehouse", 31, "Low"],
              ["Server Room", 18, "Restricted"],
            ].map(([name, value, level]) => (
              <div key={name} className="rounded-3xl border border-white/10 bg-black/20 p-4 transition-all duration-300 hover:bg-black/30">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-semibold text-white">{name}</p>
                    <p className="mt-1 text-xs text-slate-400">Density level</p>
                  </div>
                  <Badge tone={level === "High" ? "danger" : level === "Medium" ? "warning" : "success"}>{level}</Badge>
                </div>
                <div className="mt-3 h-2 rounded-full bg-white/10">
                  <div
                    className={cx("h-2 rounded-full transition-all duration-500", level === "High" ? "bg-rose-400" : level === "Medium" ? "bg-amber-400" : "bg-emerald-400")}
                    style={{ width: `${value}%` }}
                  />
                </div>
                <p className="mt-2 text-xs text-slate-500">{value}% population intensity</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Spatial Analytics" subtitle="Detection and anomaly trend by time" />
          </div>
          <div className="h-[320px] p-5">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={activityData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.12} />
                <XAxis dataKey="t" tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: "#020617", border: "1px solid rgba(255,255,255,.12)" }} />
                <Legend />
                <Line type="monotone" dataKey="detections" stroke="#22d3ee" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="anomalies" stroke="#fb7185" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card>
          <div className="border-b border-white/10 p-5">
            <SectionTitle title="Zone Traffic" subtitle="Hot zones by activity" />
          </div>
          <div className="h-[320px] p-5">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={zoneTraffic}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.12} />
                <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: "#020617", border: "1px solid rgba(255,255,255,.12)" }} />
                <Bar dataKey="value" radius={[12, 12, 0, 0]}>
                  {zoneTraffic.map((_, idx) => (
                    <Cell key={idx} fill={idx % 2 === 0 ? "#22d3ee" : "#8b5cf6"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>
    </div>
  );
}

function AssetsView() {
  return (
    <div className="grid gap-6 xl:grid-cols-[1.35fr_0.95fr]">
      <Card>
        <div className="border-b border-white/10 p-5">
          <SectionTitle title="Access Control Panel" subtitle="Policies, schedules, visitors, and identity-based access" />
        </div>
        <div className="grid gap-4 p-5 md:grid-cols-2">
          {accessRules.map((rule) => (
            <div key={rule.title} className="rounded-3xl border border-white/10 bg-black/20 p-5 transition hover:bg-black/30">
              <div className="flex items-center gap-3">
                <div className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-cyan-400/15 bg-cyan-400/10 text-cyan-200">✓</div>
                <div>
                  <p className="font-semibold text-white">{rule.title}</p>
                  <p className="mt-1 text-sm text-slate-400">{rule.description}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <div className="border-b border-white/10 p-5">
          <SectionTitle title="Devices" subtitle="Live inventory, status, and zonewise binding" />
        </div>
        <div className="space-y-3 p-5">
          {devices.map((d) => (
            <div key={d.name} className="grid gap-3 rounded-3xl border border-white/10 bg-black/20 p-4 transition-all duration-300 hover:bg-black/30">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-semibold text-white">{d.name}</p>
                  <p className="mt-1 text-sm text-slate-400">{d.type} · {d.zone}</p>
                </div>
                <Badge tone={d.status === "Online" ? "success" : d.status === "Alert" ? "warning" : "danger"}>{d.status}</Badge>
              </div>
              <p className="text-xs text-slate-500">{d.ip} · {d.fw}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function AdminView({ params, toggleParam, setAllCore, setAllAddon, setAll }) {
  const enabledCount = params.filter((p) => p.enabled).length;
  const coreCount = params.filter((p) => p.tier === "Core" && p.enabled).length;
  const addonCount = params.filter((p) => p.tier === "Addon" && p.enabled).length;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Enabled Parameters" value={`${enabledCount}/30`} hint="Per-camera configurable" accent="✓" />
        <MetricCard label="Core Tier Active" value={`${coreCount}/10`} hint="Gold tier" accent="★" />
        <MetricCard label="Add-on Active" value={`${addonCount}/20`} hint="Premium phase 2" accent="◇" />
        <MetricCard label="Notification Mode" value="Auto" hint="WhatsApp / Email / Clip" accent="↯" />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <Card>
          <div className="border-b border-white/10 p-5">
            <SectionTitle
              title="AI Parameter Library"
              subtitle="Enable or disable each detection rule from the admin dashboard"
              action={
                <div className="flex flex-wrap gap-2">
                  <Button variant="ghost" onClick={setAll}>All On/Off</Button>
                  <Button variant="ghost" onClick={setAllCore}>Core On</Button>
                  <Button variant="ghost" onClick={setAllAddon}>Add-ons Off</Button>
                </div>
              }
            />
          </div>

          <div className="p-5 space-y-6">
            <div className="rounded-[24px] border border-emerald-400/15 bg-emerald-400/10 p-4 text-sm text-emerald-100">
              Based on your parameter sheet, all 30 alerts can be configured per camera. Tier 1 contains the non-negotiable core rules, while Tier 2 is ideal for premium add-ons and phase-2 rollouts. fileciteturn0file0
            </div>

            <div>
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-white">Tier 1 — Core Parameters</p>
                  <p className="text-xs text-slate-500">High ROI, recommended for every deployment</p>
                </div>
                <Badge tone="success">Always visible</Badge>
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                {params.filter((p) => p.tier === "Core").map((p) => (
                  <div key={p.id} className="rounded-3xl border border-white/10 bg-black/20 p-4 transition-all duration-300 hover:bg-black/30">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-semibold text-white">{p.name}</p>
                        <p className="mt-1 text-xs text-slate-400">{p.category}</p>
                      </div>
                      <Badge tone="success">Core</Badge>
                    </div>
                    <div className="mt-4">
                      <Switch checked={p.enabled} onChange={() => toggleParam(p.id)} label={p.enabled ? "Enabled" : "Disabled"} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-white">Tier 2 — Add-on Parameters</p>
                  <p className="text-xs text-slate-500">Premium / phase-2 advanced rules</p>
                </div>
                <Badge tone="warning">Optional</Badge>
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                {params.filter((p) => p.tier === "Addon").map((p) => (
                  <div key={p.id} className="rounded-3xl border border-white/10 bg-black/20 p-4 transition-all duration-300 hover:bg-black/30">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-semibold text-white">{p.name}</p>
                        <p className="mt-1 text-xs text-slate-400">{p.category}</p>
                      </div>
                      <Badge tone={p.enabled ? "success" : "neutral"}>{p.enabled ? "On" : "Off"}</Badge>
                    </div>
                    <div className="mt-4">
                      <Switch checked={p.enabled} onChange={() => toggleParam(p.id)} label={p.enabled ? "Enabled" : "Disabled"} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <div className="border-b border-white/10 p-5">
              <SectionTitle title="Admin Controls" subtitle="Bulk actions and deployment presets" />
            </div>
            <div className="space-y-3 p-5">
              <Button className="w-full justify-start" onClick={setAllCore}>Enable All Core Rules</Button>
              <Button variant="ghost" className="w-full justify-start" onClick={setAllAddon}>Disable All Add-ons</Button>
              <Button variant="ghost" className="w-full justify-start" onClick={setAll}>Toggle All Parameters</Button>
              <Button variant="ghost" className="w-full justify-start">Apply Safety Preset</Button>
              <Button variant="ghost" className="w-full justify-start">Apply Security Preset</Button>
              <Button variant="ghost" className="w-full justify-start">Apply Productivity Preset</Button>
            </div>
          </Card>

          <Card>
            <div className="border-b border-white/10 p-5">
              <SectionTitle title="Delivery Notes" subtitle="What each trigger generates" />
            </div>
            <div className="space-y-3 p-5 text-sm text-slate-300">
              <div className="rounded-2xl border border-white/10 bg-black/20 p-3">Alert log entry</div>
              <div className="rounded-2xl border border-white/10 bg-black/20 p-3">Annotated screenshot</div>
              <div className="rounded-2xl border border-white/10 bg-black/20 p-3">60-second video clip</div>
              <div className="rounded-2xl border border-white/10 bg-black/20 p-3">WhatsApp / Email notification</div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function MobileTabs({ active, setActive }) {
  return (
    <div className="xl:hidden">
      <div className="flex gap-2 overflow-x-auto pb-1">
        {navItems.map((item) => {
          const isActive = active === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActive(item.id)}
              className={cx(
                "flex shrink-0 items-center gap-2 rounded-full border px-4 py-2 text-sm transition-all duration-300",
                isActive ? "border-cyan-400/30 bg-cyan-400/10 text-white" : "border-white/10 bg-white/5 text-slate-300"
              )}
            >
              <span className="text-xs font-semibold">{item.short}</span>
              {item.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function App() {
  const [active, setActive] = useState("overview");
  const [collapsed, setCollapsed] = useState(false);
  const [params, setParams] = useState(() => alertParameters);

  const toggleParam = (id) => {
    setParams((prev) => prev.map((p) => (p.id === id ? { ...p, enabled: !p.enabled } : p)));
  };

  const setAllCore = () => {
    setParams((prev) => prev.map((p) => (p.tier === "Core" ? { ...p, enabled: true } : p)));
  };

  const setAllAddon = () => {
    setParams((prev) => prev.map((p) => (p.tier === "Addon" ? { ...p, enabled: false } : p)));
  };

  const setAll = () => {
    setParams((prev) => prev.map((p) => ({ ...p, enabled: !p.enabled })));
  };

  const view = useMemo(() => {
    switch (active) {
      case "operations":
        return <OperationsView />;
      case "intelligence":
        return <IntelligenceView />;
      case "assets":
        return <AssetsView />;
      case "admin":
        return <AdminView params={params} toggleParam={toggleParam} setAllCore={setAllCore} setAllAddon={setAllAddon} setAll={setAll} />;
      default:
        return <OverviewView />;
    }
  }, [active, params]);

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,0.10),transparent_28%),radial-gradient(circle_at_top_right,rgba(168,85,247,0.08),transparent_26%),linear-gradient(180deg,#020617_0%,#0b1120_42%,#020617_100%)] text-white">
      <div className="flex min-h-screen flex-col xl:flex-row">
        <Sidebar active={active} setActive={setActive} collapsed={collapsed} setCollapsed={setCollapsed} />

        <main className="flex-1 p-4 lg:p-6 xl:p-8">
          <div className="mx-auto max-w-[1680px] space-y-6">
            <TopBar collapsed={collapsed} setCollapsed={setCollapsed} />
            <MobileTabs active={active} setActive={setActive} />
            <section key={active} className="animate-[fadeIn_0.35s_ease-out]">
              {view}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}
