import { useState } from "react";
import { Maximize2 } from "lucide-react";
import { MODULES, FIELD_SPECS, HANDCODED_FIELDS} from "../../lib/modules";
import { Badge, Button, cx } from "../ui";
import { ZoneEditor, fullFramePolygon, defaultLine } from "./ZoneEditor";

export interface ModuleAssignment {
  module: string;
  enabled: boolean;
  role?: "in" | "out" | "both";
  line?: [number, number][];
  zones?: [number, number][][];
  capacity?: number;
  dwell_seconds?: number;
  idle_mode?: "away" | "still" | "loiter";
  confidence_threshold?: number;
  schedule?: { start?: string; end?: string };
  zone_default?: boolean;   // true = auto full-frame default; false = user-customized
}

interface Props {
  value: ModuleAssignment[];
  onChange: (v: ModuleAssignment[]) => void;
  snapshotUrl?: string;   // camera snapshot for geometry editing (edit mode only)
}

// Seed default geometry when a geometry module is first enabled.
export function seedGeometry(moduleId: string): Partial<ModuleAssignment> {
  const geo = MODULES.find((m) => m.id === moduleId)?.geometry;
  if (geo === "zones") return { zones: [fullFramePolygon()], zone_default: true };
  if (geo === "line") return { line: [...defaultLine()], zone_default: true };
  return {};
}

export function CameraModulesEditor({ value, onChange, snapshotUrl }: Props) {
  const [drawing, setDrawing] = useState<string | null>(null);
  const byId = (id: string) => value.find((a) => a.module === id);

  const toggle = (id: string) => {
    const existing = byId(id);
    if (existing) { onChange(value.map((a) => (a.module === id ? { ...a, enabled: !a.enabled } : a))); return; }
    onChange([...value, { module: id, enabled: true, ...seedGeometry(id) }]);
  };

  const patch = (id: string, p: Partial<ModuleAssignment>) =>
    onChange(value.map((a) => (a.module === id ? { ...a, ...p } : a)));

  const hasGeo = (m: any, a?: ModuleAssignment) =>
    m.geometry === "line" ? (a?.line?.length === 2) : !!a?.zones?.length;

  const drawMod = drawing ? MODULES.find((m) => m.id === drawing) : null;
  const drawAssign = drawing ? byId(drawing) : undefined;

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold tracking-wider text-muted uppercase">AI Modules</p>
      <div className="space-y-2 max-h-[360px] overflow-y-auto no-scrollbar pr-1">
        {MODULES.map((m) => {
          const a = byId(m.id);
          const on = !!a?.enabled;
          const Icon = m.icon;
          return (
            <div key={m.id} className={cx("rounded-md border bg-base/40 p-3", on ? "border-accent/30" : "border-border")}>
              <div className="flex items-center gap-3">
                <Icon size={16} className={on ? "text-accent" : "text-faint"} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-tx">{m.name}</p>
                  <p className="text-[11px] text-faint truncate">{m.description}</p>
                </div>
                <Badge tone={m.phase === 1 ? "success" : m.phase === 2 ? "warning" : "neutral"}>P{m.phase}</Badge>
                <button type="button" role="switch" aria-checked={on} aria-label={m.name} onClick={() => toggle(m.id)}
                  className={cx("relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors", on ? "bg-accent" : "bg-border")}>
                  <span className={cx("inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform", on ? "translate-x-[18px]" : "translate-x-0.5")} />
                </button>
              </div>

              {on && (m.config.length > 0 || m.geometry) && (
                <div className="mt-3 space-y-3 border-t border-border pt-3">
                  {m.config.includes("role") && (
                    <label className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted">Role</span>
                      <select value={a?.role ?? "both"} onChange={(e) => patch(m.id, { role: e.target.value as any })}
                        className="rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent">
                        <option value="both">Check-in &amp; out</option>
                        <option value="in">Check-in only</option>
                        <option value="out">Check-out only</option>
                      </select>
                    </label>
                  )}
                  {m.config.includes("capacity") && (
                    <label className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted">Capacity</span>
                      <input type="number" value={a?.capacity ?? ""} onChange={(e) => patch(m.id, { capacity: parseInt(e.target.value) || 0 })}
                        className="w-24 rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
                    </label>
                  )}
                  {m.config.includes("idle_mode") && (
                    <label className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted">Idle definition</span>
                      <select value={a?.idle_mode ?? "away"} onChange={(e) => patch(m.id, { idle_mode: e.target.value as any })}
                        className="rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent">
                        <option value="away">Away from station (zone empty)</option>
                        <option value="still">Stationary too long</option>
                        <option value="loiter">Loitering (lingering in zone)</option>
                      </select>
                    </label>
                  )}
                  {m.config.includes("confidence_threshold") && (
                    <label className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted">{m.id === "attendance" ? "Min face confidence" : "Confidence threshold"}</span>
                      <input type="number" min="0" max="1" step="0.05" value={a?.confidence_threshold ?? ""}
                        placeholder={m.id === "attendance" ? "0.62" : "0.5"}
                        onChange={(e) => patch(m.id, { confidence_threshold: parseFloat(e.target.value) || 0 })}
                        className="w-24 rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
                    </label>
                  )}
                  {m.config.includes("dwell_seconds") && (
                    <label className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted">{(a?.idle_mode ?? "away") === "away" ? "Empty threshold (seconds)" : a?.idle_mode === "loiter" ? "Linger threshold (seconds)" : "Idle threshold (seconds)"}</span>
                      <input type="number" value={a?.dwell_seconds ?? ""} onChange={(e) => patch(m.id, { dwell_seconds: parseInt(e.target.value) || 0 })}
                        className="w-24 rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
                    </label>
                  )}
                  {/* Anything with a FIELD_SPEC renders automatically. Adding a
                      parameter to the registry is now enough to make it editable —
                      previously a new field was reachable only by environment variable,
                      and only with a restart. */}
                  {m.config
                    .filter((f) => !HANDCODED_FIELDS.has(f) && FIELD_SPECS[f])
                    .map((f) => {
                      const spec = FIELD_SPECS[f]!;
                      // ModuleAssignment has no index signature; the field name comes from the
                      // registry, so an unknown-first cast is the honest way to read it.
                      const val = (a as unknown as Record<string, unknown> | undefined)?.[f];
                      return (
                        <label key={f} className="flex items-start justify-between gap-3 text-xs">
                          <span className="text-muted">
                            {spec.label}
                            {spec.hint && (
                              <span className="block text-[10px] text-faint mt-0.5 max-w-[16rem]">{spec.hint}</span>
                            )}
                          </span>
                          {spec.kind === "select" ? (
                            <select
                              value={(val as string) ?? spec.options?.[0]?.value ?? ""}
                              onChange={(e) => patch(m.id, { [f]: e.target.value } as never)}
                              className="shrink-0 rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent">
                              {spec.options?.map((o) => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                              ))}
                            </select>
                          ) : spec.kind === "text" ? (
                            <input
                              type="text"
                              value={(val as string) ?? ""}
                              placeholder={spec.placeholder}
                              onChange={(e) => patch(m.id, { [f]: e.target.value } as never)}
                              className="w-40 shrink-0 rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
                          ) : (
                            <input
                              type="number"
                              min={spec.min} max={spec.max} step={spec.step}
                              value={(val as number | undefined) ?? ""}
                              placeholder={spec.placeholder}
                              onChange={(e) => {
                                const raw = e.target.value;
                                patch(m.id, { [f]: raw === "" ? undefined : Number(raw) } as never);
                              }}
                              className="w-24 shrink-0 rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
                          )}
                        </label>
                      );
                    })}
                  {m.config.includes("schedule") && (
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted">Armed window</span>
                      <div className="flex items-center gap-1">
                        <input type="time" value={a?.schedule?.start ?? ""} onChange={(e) => patch(m.id, { schedule: { ...a?.schedule, start: e.target.value } })}
                          className="rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
                        <span className="text-faint">–</span>
                        <input type="time" value={a?.schedule?.end ?? ""} onChange={(e) => patch(m.id, { schedule: { ...a?.schedule, end: e.target.value } })}
                          className="rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
                      </div>
                    </div>
                  )}
                  {m.geometry && (
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted">{m.geometry === "line" ? "Counting line" : "Zone(s)"}</span>
                      <div className="flex items-center gap-2">
                        <Badge tone={hasGeo(m, a) ? (a?.zone_default ? "neutral" : "blue") : "warning"}>
                          {hasGeo(m, a) ? (a?.zone_default ? "Full frame · default" : "Custom") : "Not set"}
                        </Badge>
                        {snapshotUrl ? (
                          <Button variant="subtle" size="sm" onClick={() => setDrawing(m.id)}><Maximize2 size={13} /> Draw / edit</Button>
                        ) : (
                          <span className="text-[11px] text-idle">Save first to draw</span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Enlarged drawing surface */}
      {drawing && drawMod && snapshotUrl && (
        <div role="dialog" aria-modal="true" className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
          onClick={() => setDrawing(null)}>
          <div className="w-full max-w-4xl rounded-lg border border-border bg-surface p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-base font-semibold text-th">{drawMod.name} — {drawMod.geometry === "line" ? "counting line" : "zone(s)"}</h3>
              <Button variant="primary" size="sm" onClick={() => setDrawing(null)}>Done</Button>
            </div>
            <ZoneEditor
              snapshotUrl={snapshotUrl}
              mode={drawMod.geometry as "line" | "zones"}
              line={drawAssign?.line}
              zones={drawAssign?.zones}
              onChangeLine={(line) => patch(drawing, { line, zone_default: false })}
              onChangeZones={(zones) => patch(drawing, { zones, zone_default: false })}
            />
          </div>
        </div>
      )}
    </div>
  );
}
