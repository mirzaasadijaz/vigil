import { useState, useCallback, useEffect } from "react";
import {
  LayoutDashboard, Video, BarChart3, ShieldCheck,
  Camera, Server, Radio, Settings, Users, CalendarCheck2,
  UserCog, ChevronRight, LogOut, Menu, X, UtensilsCrossed,
  Users2, Wifi, AlertTriangle, Bell, Maximize, Minimize, RefreshCw, Check, Truck, MessageCircle,
} from "lucide-react";
import { navItems as allNavItems } from "../../data/mockData";
import { cx, Button } from "../ui";
import { ThemeSwitcher } from "../ThemeSwitcher";
import { useAuth, type UserRole } from "../../contexts/AuthContext";
import { useApi } from "../../hooks/useApi";
import { usePolling } from "../../hooks/usePolling";
import { apiJson } from "../../lib/api";

const roleVisibility: Record<UserRole, string[]> = {
  admin:      allNavItems.map((n) => n.id).concat(["users"]),
  supervisor: ["overview", "operations", "attendance", "mess", "truck_count", "alerts", "intelligence", "assets", "chat", "employees", "ip_cameras", "nvr_dvr", "rtsp_strings"],
  operator:   ["overview", "operations", "attendance", "mess", "truck_count", "alerts", "intelligence", "assets", "chat"],
  viewer:     ["overview", "attendance", "mess", "truck_count", "alerts", "intelligence", "assets", "chat"],
};

const allNavWithUsers = [
  ...allNavItems,
  { id: "users", label: "User Accounts", short: "UA", category: "SYSTEM" },
];

const navIcons: Record<string, React.ReactNode> = {
  overview:     <LayoutDashboard size={16} />,
  operations:   <Video size={16} />,
  intelligence: <BarChart3 size={16} />,
  assets:       <ShieldCheck size={16} />,
  chat:         <MessageCircle size={16} />,
  ip_cameras:   <Camera size={16} />,
  nvr_dvr:      <Server size={16} />,
  rtsp_strings: <Radio size={16} />,
  admin:        <Settings size={16} />,
  employees:    <Users size={16} />,
  attendance:   <CalendarCheck2 size={16} />,
  mess:         <UtensilsCrossed size={16} />,
  truck_count:  <Truck size={16} />,
  alerts:       <Bell size={16} />,
  users:        <UserCog size={16} />,
};

const categoryLabels: Record<string, string> = {
  "MAIN MENU": "MONITOR",
  "CAMERAS":   "DEVICES",
  "HR":        "WORKFORCE",
  "SYSTEM":    "SETTINGS",
  "MODULES":   "MODULES",
};

export function Sidebar({
  active, setActive, collapsed, setCollapsed,
}: {
  active: string;
  setActive: (v: string) => void;
  collapsed: boolean;
  setCollapsed: (fn: (v: boolean) => boolean) => void;
}) {
  const [collapsedCats, setCollapsedCats] = useState<Record<string, boolean>>({});
  const { user, logout } = useAuth();
  const visible = roleVisibility[user?.role ?? "viewer"];
  const navItems = allNavWithUsers.filter((n) => visible.includes(n.id));

  const toggleCat = (cat: string) =>
    setCollapsedCats((prev) => ({ ...prev, [cat]: !prev[cat] }));

  const grouped = navItems.reduce((acc: Record<string, typeof navItems>, item) => {
    acc[item.category] = acc[item.category] || [];
    acc[item.category].push(item);
    return acc;
  }, {});

  return (
    <aside
      className={cx(
        "no-scrollbar shrink-0 border-r border-base bg-surface flex flex-col xl:sticky xl:top-0 xl:h-screen xl:overflow-y-auto transition-all duration-300",
        collapsed ? "xl:w-[72px]" : "xl:w-[260px]",
        "w-full"
      )}
    >
      {/* Brand */}
      <div className={cx("flex items-center gap-3 border-b border-border p-5", collapsed && "xl:justify-center xl:px-3")}>
        <img src="/logo.png" alt="VIGIL" className="h-14 w-14 shrink-0 rounded-2xl object-cover shadow-lg shadow-accent/20" />
        {!collapsed && (
          <div>
            <p className="text-sm font-bold text-tx tracking-widest">VIGIL</p>
            <p className="text-[10px] text-faint tracking-wider">WORKPLACE INTELLIGENCE</p>
          </div>
        )}
      </div>

      {/* User info */}
      {!collapsed && user && (
        <div className="border-b border-border px-4 py-3">
          <div className="flex items-center gap-3 rounded-md bg-base px-3 py-2.5">
            <div className="relative shrink-0">
              <div className="flex h-9 w-9 items-center justify-center rounded-2xl bg-gradient-to-br from-accent to-accent-hover text-[10px] font-bold text-white uppercase transition-all duration-200 hover:rounded-xl">
                {(user.name ?? user.username).slice(0, 2)}
              </div>
              <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full bg-online ring-[3px] ring-base" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-tx">{user.name ?? user.username}</p>
              <p className="text-[10px] text-faint capitalize">{user.role}</p>
            </div>
            <span className="ml-auto rounded-sm bg-online/15 px-1.5 py-0.5 text-[8px] font-bold tracking-wider text-online uppercase">Online</span>
          </div>
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto no-scrollbar p-3 space-y-5 py-4">
        {Object.entries(grouped).map(([category, items]) => {
          const isCatCollapsed = collapsedCats[category];
          const label = categoryLabels[category] ?? category;
          return (
            <div key={category}>
              {!collapsed && (
                <button
                  onClick={() => toggleCat(category)}
                  className="mb-1.5 flex w-full items-center justify-between px-2 text-[9px] font-bold tracking-[0.25em] text-faint hover:text-muted transition-colors"
                >
                  <span>{label}</span>
                  <span className={cx("transition-transform duration-200", isCatCollapsed ? "rotate-0" : "rotate-90")}>›</span>
                </button>
              )}
              {(!isCatCollapsed || collapsed) && (
                <div className="space-y-0.5">
                  {items.map((item) => {
                    const isActive = active === item.id;
                    return (
                      <button
                        key={item.id}
                        onClick={() => setActive(item.id)}
                        title={collapsed ? item.label : undefined}
                        className={cx(
                          "group relative flex w-full min-h-[40px] items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm transition-all duration-150",
                          isActive
                            ? "bg-overlay text-th before:absolute before:left-0 before:top-1/2 before:h-5 before:w-[3px] before:-translate-y-1/2 before:rounded-r-full before:bg-accent"
                            : "text-muted hover:bg-hover hover:text-tx",
                          collapsed && "xl:justify-center xl:px-0"
                        )}
                      >
                        <span className={cx("shrink-0", isActive ? "text-th" : "text-faint group-hover:text-muted")}>
                          {navIcons[item.id] ?? <span className="text-[10px] font-bold">{item.short}</span>}
                        </span>
                        {!collapsed && (
                          <>
                            <span className="flex-1 text-[13px] font-medium">{item.label}</span>
                            {isActive && <ChevronRight size={12} className="text-accent" />}
                          </>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="border-t border-border p-3 space-y-1">
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="flex w-full min-h-[36px] items-center gap-3 rounded-xl px-3 py-2 text-faint hover:text-muted hover:bg-surface transition-all text-xs"
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? <Menu size={15} /> : <><X size={15} /><span>Collapse</span></>}
        </button>
        {!collapsed && (
          <button
            onClick={logout}
            className="flex w-full min-h-[36px] items-center gap-3 rounded-xl px-3 py-2 text-faint hover:text-dnd-text hover:bg-dnd/5 transition-all text-xs"
          >
            <LogOut size={15} />
            <span>Sign Out</span>
          </button>
        )}
      </div>
    </aside>
  );
}

// Toggles the WHOLE app into browser full-screen (like YouTube's fullscreen),
// hiding the browser chrome/tabs. Stays in sync if the user presses Esc/F11.
function FullscreenToggle() {
  const [fs, setFs] = useState(typeof document !== "undefined" && !!document.fullscreenElement);
  useEffect(() => {
    const onChange = () => setFs(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);
  const toggle = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else document.documentElement.requestFullscreen?.().catch(() => {});
  };
  return (
    <Button variant="ghost" size="sm" onClick={toggle} title={fs ? "Exit full screen (Esc)" : "Full screen"} aria-label={fs ? "Exit full screen" : "Enter full screen"}>
      {fs ? <Minimize size={15} /> : <Maximize size={15} />}
    </Button>
  );
}

// Applies dashboard changes to the LIVE pipeline without a restart: rebuilds the
// face-recognition gallery (drops stale/removed/orphan photos, re-seeds learned
// vectors) and refreshes each running camera's assigned AI modules from the DB.
function ReloadButton() {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const reload = useCallback(async () => {
    setState("loading");
    try {
      await apiJson("/system/reload", { method: "POST" });
      setState("done");
    } catch {
      setState("error");
    }
    setTimeout(() => setState("idle"), 2500);
  }, []);
  return (
    <Button
      variant="ghost" size="sm" onClick={reload} disabled={state === "loading"}
      title="Apply changes — reload recognition data & camera config into the live system"
      aria-label="Apply dashboard changes to the live system"
    >
      {state === "done" ? <Check size={15} className="text-online" />
        : <RefreshCw size={15} className={cx(state === "loading" && "animate-spin", state === "error" && "text-dnd")} />}
    </Button>
  );
}

export function TopBar({ collapsed, setCollapsed }: { collapsed: boolean; setCollapsed: (fn: (v: boolean) => boolean) => void }) {
  const { data: cameras, refetch: refetchCams } = useApi<any[]>("/cameras/");
  const { data: alerts, refetch: refetchAlerts } = useApi<any[]>("/alerts/?limit=100");
  const { data: employees } = useApi<any[]>("/employees/");

  const refresh = useCallback(() => { refetchCams(); refetchAlerts(); }, [refetchCams, refetchAlerts]);
  usePolling(refresh, 10000);

  const totalCams   = cameras?.length ?? 0;
  const activeCams  = cameras?.filter((c: any) => c.is_active).length ?? 0;
  const alertCount  = alerts?.length ?? 0;
  const empCount    = employees?.length ?? 0;

  const now = new Date();
  const dateStr = now.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" });

  return (
    <header className="rounded-lg border border-border bg-surface px-6 py-4 shadow-lg shadow-black/20">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-[10px] font-semibold tracking-[0.3em] text-accent uppercase mb-1">VIGIL</p>
          <h1 className="text-2xl font-bold tracking-tight text-th">Workplace Intelligence Platform</h1>
          <p className="mt-1 text-xs text-faint">{dateStr}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1.5 rounded-md border border-border bg-base px-3 py-2">
            <Users2 size={13} className="text-accent" />
            <span className="text-xs font-semibold text-tx font-mono">{empCount}</span>
            <span className="text-[10px] text-faint">employees</span>
          </div>
          <div className="flex items-center gap-1.5 rounded-md border border-border bg-base px-3 py-2">
            <Wifi size={13} className={activeCams > 0 ? "text-online" : "text-faint"} />
            <span className="text-xs font-semibold text-tx font-mono">{activeCams}/{totalCams}</span>
            <span className="text-[10px] text-faint">cameras</span>
          </div>
          <div className="flex items-center gap-1.5 rounded-md border border-border bg-base px-3 py-2">
            <AlertTriangle size={13} className={alertCount > 0 ? "text-idle" : "text-faint"} />
            <span className="text-xs font-semibold text-tx font-mono">{alertCount}</span>
            <span className="text-[10px] text-faint">alerts</span>
          </div>
          <ReloadButton />
          <ThemeSwitcher />
          <FullscreenToggle />
          <Button variant="ghost" size="sm" onClick={() => setCollapsed((v) => !v)}>
            {collapsed ? "Show Sidebar" : "Hide Sidebar"}
          </Button>
        </div>
      </div>
    </header>
  );
}

export function MobileTabs({ active, setActive }: { active: string; setActive: (v: string) => void }) {
  const { user } = useAuth();
  const visible = roleVisibility[user?.role ?? "viewer"];
  const navItems = allNavWithUsers.filter((n) => visible.includes(n.id));
  return (
    <div className="xl:hidden">
      <div className="flex gap-2 overflow-x-auto pb-1 no-scrollbar">
        {navItems.map((item) => {
          const isActive = active === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActive(item.id)}
              className={cx(
                "flex shrink-0 min-h-[40px] items-center gap-2 rounded-md border px-4 py-2 text-xs font-medium transition-all duration-200",
                isActive
                  ? "border-accent bg-overlay text-th"
                  : "border-border bg-surface text-muted"
              )}
            >
              <span className="text-accent">{navIcons[item.id] ?? item.short}</span>
              {item.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
