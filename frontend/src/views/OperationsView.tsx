import { useEffect, useMemo, useState } from "react";
import { Search, Video, MapPin, Server, Building2, ScanLine, SlidersHorizontal, X, ScanFace, Maximize2 } from "lucide-react";
import { Badge, Button, Card, cx } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useCameraStream, type StreamStatus } from "../hooks/useCameraStream";
import { ModuleFilter } from "../components/ModuleFilter";
import { MODULE_BY_ID } from "../lib/modules";
import { LiveAttendancePanel } from "../components/domain/attendance";

interface ModAssign { module: string; enabled: boolean }
interface Cam {
  id: number; name: string; ip_address: string; channel: number;
  is_active: boolean; location: string | null; department: string | null;
  nvr_id: number | null; nvr_name: string | null; module_config?: ModAssign[];
}

const enabledModules = (c: Cam) => (c.module_config || []).filter((a) => a.enabled).map((a) => a.module);

const ALL = "__all__";

function FilterSelect({ icon, label, value, onChange, options }: {
  icon: React.ReactNode; label: string; value: string; onChange: (v: string) => void; options: string[];
}) {
  return (
    <label className="flex items-center gap-2 rounded-md border border-border bg-base px-3 py-2">
      <span className="text-faint">{icon}</span>
      <span className="text-[10px] font-bold uppercase tracking-wider text-faint">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-sm text-tx outline-none cursor-pointer max-w-[140px]">
        <option value={ALL} className="bg-surface">All</option>
        {options.map((o) => <option key={o} value={o} className="bg-surface">{o}</option>)}
      </select>
    </label>
  );
}

function CamTile({ cam, onClick }: { cam: Cam; onClick: () => void }) {
  const { canvasRef, status } = useCameraStream(cam.id);
  const isLive = status === "live";
  const mods = enabledModules(cam);
  return (
    <div onClick={onClick}
      className="cursor-pointer group rounded-lg border border-border bg-base overflow-hidden hover:border-accent/40 hover:-translate-y-0.5 transition-all duration-200">
      <div className="relative aspect-video bg-black">
        <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
        <div className="absolute top-2 left-2 flex items-center gap-1.5">
          <span className={cx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[9px] font-bold tracking-wider",
            isLive ? "bg-online/20 text-online-text border border-online/30" : "bg-black/60 text-muted border border-border")}>
            <span className={cx("h-1.5 w-1.5 rounded-full", isLive ? "bg-online animate-pulse" : "bg-faint")} />
            {isLive ? "LIVE" : status.toUpperCase()}
          </span>
        </div>
        <div className="absolute top-2 right-2 flex flex-wrap justify-end gap-1 max-w-[70%]">
          {mods.length === 0 ? (
            <span className="inline-flex items-center gap-1 rounded-md bg-black/60 border border-border px-1.5 py-0.5 text-[9px] font-semibold text-faint">
              <ScanLine size={9} /> no module
            </span>
          ) : (
            <>
              {mods.slice(0, 3).map((id) => (
                <span key={id} className="inline-flex items-center gap-1 rounded-md bg-black/60 border border-border px-1.5 py-0.5 text-[9px] font-semibold text-accent-text">
                  <ScanLine size={9} /> {MODULE_BY_ID[id]?.name.split(" ")[0] ?? id}
                </span>
              ))}
              {/* Modules past the third used to be dropped with no indication, so a
                  camera running four looked like it was running three - and the missing
                  one looked broken rather than merely unlisted. */}
              {mods.length > 3 && (
                <span
                  title={mods.slice(3).map((id) => MODULE_BY_ID[id]?.name ?? id).join(", ")}
                  className="inline-flex items-center gap-1 rounded-md bg-black/60 border border-border px-1.5 py-0.5 text-[9px] font-semibold text-faint">
                  +{mods.length - 3}
                </span>
              )}
            </>
          )}
        </div>
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-3 py-2">
          <p className="text-xs font-semibold text-white truncate">{cam.name}</p>
          <p className="text-[10px] text-white/60 truncate flex items-center gap-1"><MapPin size={9} />{cam.location ?? "Unassigned"} · {cam.department ?? "—"}</p>
        </div>
      </div>
    </div>
  );
}

function FullStream({ camId, onExpand, onStatus }: { camId: number; onExpand?: () => void; onStatus?: (s: StreamStatus) => void }) {
  const { canvasRef, status } = useCameraStream(camId);
  useEffect(() => { onStatus?.(status); }, [status, onStatus]);
  return (
    <div className={cx("relative aspect-video bg-black", onExpand && "cursor-pointer")}
      onClick={onExpand} title={onExpand ? "Click to view full camera" : undefined}>
      <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
      {onExpand && (
        <button type="button" onClick={(e) => { e.stopPropagation(); onExpand(); }}
          className="absolute top-2 right-2 z-10 inline-flex items-center gap-1 rounded-md border border-border bg-black/60 px-2 py-1 text-[10px] font-semibold text-white transition-colors hover:bg-black/80">
          <Maximize2 size={11} /> Full view
        </button>
      )}
      {status !== "live" && (
        <div className="absolute inset-0 flex items-center justify-center text-faint text-xs font-mono">{status.toUpperCase()}…</div>
      )}
    </div>
  );
}

export default function OperationsView() {
  const { data: cameras } = useApi<Cam[]>("/cameras/");
  const { data: mode } = useApi<{ mode: string }>("/mode/");
  const [selected, setSelected] = useState<Cam | null>(null);
  const [attExpanded, setAttExpanded] = useState(false);   // attendance cam: full feed vs split
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");
  const [query, setQuery] = useState("");
  const openCam = (cam: Cam | null) => { setSelected(cam); setAttExpanded(false); setStreamStatus("connecting"); };
  const [loc, setLoc] = useState(ALL);
  const [nvr, setNvr] = useState(ALL);
  const [dept, setDept] = useState(ALL);
  const [stat, setStat] = useState(ALL);
  const [mod, setMod] = useState("all");

  const camList = cameras ?? [];
  const analytic = mode?.mode === "face" ? "Face ID" : mode?.mode === "ppe" ? "PPE" : "—";

  const locations = useMemo(() => [...new Set(camList.map((c) => c.location).filter(Boolean) as string[])].sort(), [camList]);
  const nvrs = useMemo(() => [...new Set(camList.map((c) => c.nvr_name ?? "Direct / IP").filter(Boolean))].sort(), [camList]);
  const depts = useMemo(() => [...new Set(camList.map((c) => c.department).filter(Boolean) as string[])].sort(), [camList]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return camList.filter((c) => {
      if (loc !== ALL && c.location !== loc) return false;
      if (nvr !== ALL && (c.nvr_name ?? "Direct / IP") !== nvr) return false;
      if (dept !== ALL && c.department !== dept) return false;
      if (stat === "Online" && !c.is_active) return false;
      if (stat === "Offline" && c.is_active) return false;
      if (mod !== "all" && !enabledModules(c).includes(mod)) return false;
      if (q && !`${c.name} ${c.ip_address ?? ""} ${c.location ?? ""}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [camList, query, loc, nvr, dept, stat, mod]);

  const activeFilters = [loc, nvr, dept, stat].filter((v) => v !== ALL).length + (mod !== "all" ? 1 : 0) + (query ? 1 : 0);
  const clearAll = () => { setQuery(""); setLoc(ALL); setNvr(ALL); setDept(ALL); setStat(ALL); setMod("all"); };

  if (selected) {
    const isAttendanceCam = enabledModules(selected).includes("attendance");
    const splitView = isAttendanceCam && !attExpanded;
    // Badge reflects the actual WebSocket stream state, not stale polled data.
    const streamLive = streamStatus === "live";
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-th">{selected.name}</h2>
            <p className="text-xs text-faint mt-0.5 flex items-center gap-1">
              <MapPin size={11} />{selected.location ?? "Unassigned"} · {selected.department ?? "—"} · {selected.ip_address} · CH {selected.channel}
            </p>
          </div>
          <Button variant="ghost" onClick={() => openCam(null)}>← All Cameras</Button>
        </div>
        {splitView ? (
          // Attendance camera: feed left (~50%), live attendance cards right (~50%).
          // Click the feed (or "Full view") to expand to the full camera.
          <div className="flex flex-col lg:flex-row gap-4">
            <div className="lg:w-1/2 lg:shrink-0">
              <Card className="overflow-hidden p-0">
                <div className="border-b border-border bg-base p-4 flex flex-wrap items-center gap-2">
                  <Badge tone={streamLive ? "success" : "danger"}>{streamLive ? "LIVE" : "OFFLINE"}</Badge>
                  <Badge tone="blue">Smart Attendance</Badge>
                  <span className="ml-auto text-[11px] text-faint">Click feed for full view</span>
                </div>
                <FullStream camId={selected.id} onExpand={() => setAttExpanded(true)} onStatus={setStreamStatus} />
              </Card>
            </div>
            <Card className="flex flex-1 min-w-0 flex-col overflow-hidden p-0 max-h-[calc(100vh-13rem)]">
              <div className="flex items-center gap-2 border-b border-border bg-base px-4 py-3">
                <ScanFace size={15} className="text-accent" />
                <h3 className="text-sm font-semibold text-th">Live Attendance</h3>
                <span className="ml-auto inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-online-text">
                  <span className="h-1.5 w-1.5 rounded-full bg-online animate-pulse" /> Live
                </span>
              </div>
              <LiveAttendancePanel />
            </Card>
          </div>
        ) : (
          <Card className="overflow-hidden p-0">
            <div className="border-b border-border bg-base p-4 flex flex-wrap items-center gap-2">
              <Badge tone={streamLive ? "success" : "danger"}>{streamLive ? "LIVE" : "OFFLINE"}</Badge>
              <Badge tone="blue">{isAttendanceCam ? "Smart Attendance" : analytic}</Badge>
              {isAttendanceCam ? (
                <Button variant="subtle" size="sm" className="ml-auto" onClick={() => setAttExpanded(false)}>
                  <ScanFace size={13} /> Attendance view
                </Button>
              ) : (
                <span className="text-xs text-faint ml-auto">AI analytic running on this feed</span>
              )}
            </div>
            <FullStream camId={selected.id} onStatus={setStreamStatus} />
          </Card>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th flex items-center gap-2"><Video size={20} className="text-accent" /> Live Feed</h1>
          <p className="mt-1 text-sm text-muted">{filtered.length} of {camList.length} cameras · analytic: {analytic}</p>
        </div>
      </div>

      {/* Filter bar */}
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[200px]">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search cameras…"
              className="w-full rounded-md border border-base bg-base py-2 pl-9 pr-3 text-sm text-tx placeholder:text-faint outline-none focus:border-accent focus:ring-2 focus:ring-accent/20" />
          </div>
          <FilterSelect icon={<MapPin size={13} />} label="Location" value={loc} onChange={setLoc} options={locations} />
          <FilterSelect icon={<Server size={13} />} label="NVR" value={nvr} onChange={setNvr} options={nvrs} />
          <FilterSelect icon={<Building2 size={13} />} label="Dept" value={dept} onChange={setDept} options={depts} />
          <FilterSelect icon={<ScanLine size={13} />} label="Status" value={stat} onChange={setStat} options={["Online", "Offline"]} />
          <ModuleFilter value={mod} onChange={setMod} />
          {activeFilters > 0 && (
            <Button variant="ghost" size="sm" onClick={clearAll}><X size={13} /> Clear ({activeFilters})</Button>
          )}
        </div>
      </Card>

      {/* Grid */}
      {camList.length === 0 ? (
        <Card className="py-16 text-center"><Video size={36} className="mx-auto text-border mb-3" /><p className="text-muted text-sm">No cameras configured.</p><p className="text-faint text-xs mt-1">Add cameras in Devices → IP Cameras.</p></Card>
      ) : filtered.length === 0 ? (
        <Card className="py-16 text-center"><SlidersHorizontal size={32} className="mx-auto text-border mb-3" /><p className="text-muted text-sm">No cameras match the current filters.</p>
          <Button variant="ghost" size="sm" className="mt-3 mx-auto" onClick={clearAll}>Clear filters</Button></Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {filtered.map((cam) => <CamTile key={cam.id} cam={cam} onClick={() => openCam(cam)} />)}
        </div>
      )}
    </div>
  );
}
