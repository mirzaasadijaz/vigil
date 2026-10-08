import { useState, useEffect } from "react";
import { MapPin, Building2, Plus, X, ChevronDown, ChevronUp, BellRing } from "lucide-react";
import { Badge, Button, Card, Input, SectionTitle, Switch, Toast, Spinner, cx } from "../components/ui";
import FalseDetectionPanel from "../components/domain/FalseDetectionPanel";
import ModuleTogglePanel from "../components/domain/ModuleTogglePanel";
import { useApi } from "../hooks/useApi";
import { apiFetch } from "../lib/api";
import { alertParameters } from "../data/mockData";

interface Settings {
  locations: string[];
  departments: string[];
  // GET /settings/ returns these as "HH:MM"; PUT /settings/ takes them back the same way.
  office_open_time?: string;
  office_close_time?: string;
}

// ── Reusable chip-list editor ────────────────────────────────────────────────
function TagListEditor({ icon, title, items, setItems }: {
  icon: React.ReactNode; title: string; items: string[]; setItems: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim();
    if (v && !items.some((i) => i.toLowerCase() === v.toLowerCase())) setItems([...items, v]);
    setDraft("");
  };
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted">{icon}{title}</div>
      <div className="flex flex-wrap gap-2">
        {items.length === 0 && <span className="text-xs text-faint">None yet — add one below.</span>}
        {items.map((it) => (
          <span key={it} className="inline-flex items-center gap-1.5 rounded-full border border-border bg-base px-3 py-1 text-xs text-tx">
            {it}
            <button onClick={() => setItems(items.filter((x) => x !== it))} className="text-faint hover:text-dnd transition-colors" aria-label={`Remove ${it}`}>
              <X size={12} />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())}
          placeholder={`Add ${title.toLowerCase().replace(/s$/, "")}…`}
          className="flex-1 rounded-md border border-base bg-base px-3 py-2 text-sm text-tx placeholder:text-faint outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
        />
        <Button variant="subtle" size="sm" onClick={add}><Plus size={14} /> Add</Button>
      </div>
    </div>
  );
}

export default function AdminView() {
  const { data: settings, refetch: refetchSettings } = useApi<Settings>("/settings/");
  const { data: modeData, refetch: refetchMode } = useApi<{ mode: string }>("/mode/");

  const [locations, setLocations] = useState<string[]>([]);
  const [departments, setDepartments] = useState<string[]>([]);
  const [savingMeta, setSavingMeta] = useState(false);
  const [openTime, setOpenTime] = useState("09:00");
  const [closeTime, setCloseTime] = useState("18:00");
  const [savingSettings, setSavingSettings] = useState(false);
  const [systemMode, setSystemMode] = useState("face");
  const [isChangingMode, setIsChangingMode] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [params, setParams] = useState(() => alertParameters);
  const [showAllParams, setShowAllParams] = useState(false);
  const PARAM_PREVIEW = 4;

  useEffect(() => {
    if (settings) {
      setLocations(settings.locations ?? []);
      setDepartments(settings.departments ?? []);
      setOpenTime(settings.office_open_time ?? "09:00");
      setCloseTime(settings.office_close_time ?? "18:00");
    }
  }, [settings]);

  useEffect(() => {
    if (modeData?.mode) setSystemMode(modeData.mode);
  }, [modeData]);

  const putSettings = async (extra: Record<string, string>) => {
    const body = new URLSearchParams(extra);
    return apiFetch("/settings/", { method: "PUT", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body.toString() });
  };

  const saveMeta = async () => {
    setSavingMeta(true);
    const res = await putSettings({ locations: JSON.stringify(locations), departments: JSON.stringify(departments) });
    setSavingMeta(false);
    if (res.ok) { setToast({ message: "Camera metadata saved", type: "success" }); refetchSettings(); }
    else setToast({ message: "Failed to save", type: "error" });
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    const res = await putSettings({ office_open_time: openTime, office_close_time: closeTime });
    setSavingSettings(false);
    if (res.ok) { setToast({ message: "Office hours saved", type: "success" }); refetchSettings(); }
    else setToast({ message: "Failed to save office hours", type: "error" });
  };

  // POST /mode/ takes `mode` as a query parameter, not a body.
  const toggleMode = async (next: string) => {
    setIsChangingMode(true);
    const res = await apiFetch(`/mode/?mode=${encodeURIComponent(next)}`, { method: "POST" });
    setIsChangingMode(false);
    if (res.ok) {
      setSystemMode(next);
      refetchMode();
      setToast({ message: next === "ppe" ? "PPE Safety enabled" : "PPE Safety disabled", type: "success" });
    } else setToast({ message: "Failed to switch detection mode", type: "error" });
  };

  const toggleParam = (id: string) => setParams((prev) => prev.map((p) => (p.id === id ? { ...p, enabled: !p.enabled } : p)));

  return (
    <div className="space-y-6">
      <SectionTitle title="Admin Settings" subtitle="System configuration, camera metadata and AI parameters" />

      <div className="grid gap-6 xl:grid-cols-2">
        {/* Office Hours */}
        <Card className="p-6 space-y-5">
          <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Office Hours</h3>
          <div className="grid grid-cols-2 gap-4">
            <Input label="Office Open" type="time" value={openTime} onChange={(e) => setOpenTime(e.target.value)} />
            <Input label="Office Close" type="time" value={closeTime} onChange={(e) => setCloseTime(e.target.value)} />
          </div>
          <Button onClick={saveSettings} disabled={savingSettings}>
            {savingSettings ? <><Spinner size="sm" /> Saving...</> : "Save Office Hours"}
          </Button>
        </Card>

        {/* Detection Mode */}
        <Card className="p-6 space-y-5">
          <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Detection Mode</h3>
          <div className="flex items-center justify-between rounded-md border border-border bg-base/40 px-4 py-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-th">PPE Safety Detection</p>
              <p className="text-xs text-faint mt-0.5">Enable hardhat, vest &amp; phone violation alerts alongside Face ID</p>
            </div>
            <Switch checked={systemMode === "ppe"} onChange={() => { if (!isChangingMode) toggleMode(systemMode === "ppe" ? "face" : "ppe"); }} />
          </div>
          {isChangingMode && <p className="text-xs text-idle animate-pulse">Switching AI model… Please wait.</p>}
          <p className="text-xs text-faint">
            Face Recognition &amp; Attendance is always active.{" "}
            <span className="text-th font-medium">{systemMode === "ppe" ? "PPE Safety also enabled." : "PPE Safety off."}</span>
          </p>
        </Card>
      </div>

      {/* Capability switches */}
      <ModuleTogglePanel />

      {/* False Detection */}
      <FalseDetectionPanel />

      {/* Camera Metadata */}
      <Card className="p-6 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Camera Metadata</h3>
            <p className="text-xs text-faint mt-1">Define the Locations and Departments selectable when registering a camera.</p>
          </div>
          <Button onClick={saveMeta} disabled={savingMeta}>
            {savingMeta ? <><Spinner size="sm" /> Saving...</> : "Save Metadata"}
          </Button>
        </div>
        <div className="grid gap-8 md:grid-cols-2">
          <TagListEditor icon={<MapPin size={13} />} title="Locations" items={locations} setItems={setLocations} />
          <TagListEditor icon={<Building2 size={13} />} title="Departments" items={departments} setItems={setDepartments} />
        </div>
      </Card>

      {/* Alert Parameters */}
      <Card className="p-6">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Alert Parameters</h3>
            <p className="text-xs text-faint mt-1">{params.filter((p) => p.enabled).length} of {params.length} analytics enabled</p>
          </div>
          <div className="flex gap-2">
            <Button variant="subtle" size="sm" onClick={() => setParams((p) => p.map((x) => ({ ...x, enabled: x.tier === "Core" })))}>Core Only</Button>
            <Button variant="subtle" size="sm" onClick={() => setParams((p) => p.map((x) => ({ ...x, enabled: true })))}>Enable All</Button>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {(showAllParams ? params : params.slice(0, PARAM_PREVIEW)).map((p) => (
            <div key={p.id}
              className={cx(
                "flex flex-col gap-3 rounded-lg border bg-base/40 p-4 transition-all duration-200 hover:-translate-y-0.5",
                p.enabled ? "border-accent/30" : "border-border hover:border-overlay"
              )}>
              <div className="flex items-start justify-between gap-3">
                <span className={cx("flex h-9 w-9 shrink-0 items-center justify-center rounded-md",
                  p.enabled ? "bg-accent/10 text-accent" : "bg-overlay text-faint")}>
                  <BellRing size={16} />
                </span>
                <button
                  type="button" role="switch" aria-checked={p.enabled} aria-label={p.name}
                  onClick={() => toggleParam(p.id)}
                  className={cx("relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors", p.enabled ? "bg-accent" : "bg-border")}>
                  <span className={cx("inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform", p.enabled ? "translate-x-[18px]" : "translate-x-0.5")} />
                </button>
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-tx leading-snug">{p.name}</p>
                <div className="mt-1.5 flex items-center gap-2">
                  <Badge tone={p.tier === "Core" ? "success" : "neutral"}>{p.tier}</Badge>
                  <span className="text-[11px] text-faint truncate">{p.category}</span>
                </div>
              </div>
            </div>
          ))}
        </div>

        {params.length > PARAM_PREVIEW && (
          <button
            onClick={() => setShowAllParams((v) => !v)}
            className="mt-4 flex w-full items-center justify-center gap-1.5 rounded-md border border-border bg-base/40 py-2.5 text-xs font-medium text-muted transition-colors hover:text-tx hover:border-accent/40">
            {showAllParams
              ? <>Show less <ChevronUp size={14} /></>
              : <>Show {params.length - PARAM_PREVIEW} more <ChevronDown size={14} /></>}
          </button>
        )}
      </Card>

      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}
    </div>
  );
}
