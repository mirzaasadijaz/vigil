import { useEffect, useState } from "react";
import { ShieldAlert, Eye, Zap, RotateCcw } from "lucide-react";
import { Badge, Button, Card, Input, Select, Spinner, Switch, Toast, cx } from "../ui";
import { apiFetch, apiJson } from "../../lib/api";

/**
 * False-detection control panel.
 *
 * Implements section 5 of `false-detection-plan.md`. The layout deliberately puts the
 * LIVE COUNTERS above the inputs: the point of `observe` mode is that an operator can
 * see what the layer would have suppressed before trusting it to suppress anything, and
 * that number is the decision, not the knobs.
 */

type FDConfig = Record<string, unknown>;

interface FDResponse {
  config: FDConfig;
  defaults: FDConfig;
  counters: {
    window_hours: number;
    alerts_written: number;
    by_severity: Record<string, number>;
  };
}

const SEVERITIES = ["info", "low", "medium", "high", "critical"];
const SEVERITY_OPTIONS = SEVERITIES.map((s) => ({ value: s, label: s }));

const SEVERITY_TONE: Record<string, string> = {
  critical: "text-dnd",
  high: "text-dnd",
  medium: "text-idle",
  low: "text-online",
  info: "text-faint",
  unclassified: "text-faint",
};

function num(cfg: FDConfig, key: string, fallback = 0): number {
  const v = cfg[key];
  return typeof v === "number" ? v : fallback;
}

function str(cfg: FDConfig, key: string, fallback = ""): string {
  const v = cfg[key];
  return typeof v === "string" ? v : fallback;
}

function Section({ title, hint, children }: {
  title: string; hint: string; children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="border-b border-border pb-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-th">{title}</h4>
        <p className="mt-0.5 text-[11px] text-faint">{hint}</p>
      </div>
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">{children}</div>
    </div>
  );
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-th">{label}</span>
        {hint && <span className="text-[11px] text-faint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export default function FalseDetectionPanel() {
  const [data, setData] = useState<FDResponse | null>(null);
  const [cfg, setCfg] = useState<FDConfig>({});
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const body = await apiJson<FDResponse>("/settings/false-detection");
      setData(body);
      setCfg(body.config);
      setError(null);
    } catch {
      // The endpoint is absent until the migration has run. Say so plainly rather than
      // rendering an empty panel that looks like "no settings".
      setError("Could not load false-detection settings. Has the migration been applied?");
    }
  };

  useEffect(() => { void load(); }, []);

  const set = (key: string, value: unknown) => setCfg((p) => ({ ...p, [key]: value }));

  const save = async () => {
    setSaving(true);
    try {
      const res = await apiFetch("/settings/false-detection", {
        method: "PUT",
        body: JSON.stringify(cfg),
      });
      if (!res.ok) throw new Error(await res.text());
      setToast("False-detection settings saved");
      await load();
    } catch (e) {
      setToast(`Save failed: ${e instanceof Error ? e.message : "unknown error"}`);
    } finally {
      setSaving(false);
    }
  };

  const reset = () => { if (data) setCfg(data.defaults); };

  if (error) {
    return (
      <Card className="p-6">
        <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Unusual Activity</h3>
        <p className="mt-2 text-xs text-dnd">{error}</p>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card className="p-6 flex items-center gap-3">
        <Spinner size="sm" /> <span className="text-xs text-faint">Loading false-detection settings…</span>
      </Card>
    );
  }

  const enabled = cfg.fd_enabled === true;
  const mode = str(cfg, "fd_mode", "observe");
  const { counters } = data;

  return (
    <Card className="p-6 space-y-6">
      {toast && <Toast message={toast} onDismiss={() => setToast(null)} />}

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <ShieldAlert size={16} className="mt-0.5 text-accent" />
          <div>
            <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Unusual Activity</h3>
            <p className="text-xs text-faint mt-1">
              False-detection controls. One stage every module&apos;s events pass through
              before becoming alerts. Switched off, nothing changes.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-faint">{enabled ? "Enabled" : "Off"}</span>
          <Switch checked={enabled} onChange={() => set("fd_enabled", !enabled)} />
        </div>
      </div>

      {/* Mode. Observe is the safe default and the panel says why. */}
      <div className="rounded-md border border-border bg-base/40 p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant={mode === "observe" ? "primary" : "subtle"}
            onClick={() => set("fd_mode", "observe")}
          >
            <Eye size={13} /> Observe
          </Button>
          <Button
            size="sm"
            variant={mode === "enforce" ? "primary" : "subtle"}
            onClick={() => set("fd_mode", "enforce")}
          >
            <Zap size={13} /> Enforce
          </Button>
        </div>
        <p className="text-xs text-faint">
          {mode === "observe"
            ? "Observe judges every event and records what it would have suppressed, but never removes an alert. Review the log before switching to Enforce."
            : "Enforce actually suppresses. Only switch to this after reviewing an Observe capture — a suppressed security event is a failure, not an annoyance."}
        </p>
      </div>

      {/* The important element: what actually happened. */}
      <div className="rounded-md border border-border bg-base/40 p-4">
        <p className="text-xs font-medium text-th">
          Last {counters.window_hours} h — {counters.alerts_written} alerts written
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {Object.entries(counters.by_severity).length === 0 && (
            <span className="text-xs text-faint">No alerts in this window.</span>
          )}
          {Object.entries(counters.by_severity).map(([sev, n]) => (
            <Badge key={sev}>
              <span className={cx("font-medium", SEVERITY_TONE[sev] ?? "text-faint")}>{sev}</span>
              <span className="ml-1 text-faint">{n}</span>
            </Badge>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-faint">
          Suppression counts per rule are in <code>logs/false_detection/fd_metrics.log</code>.
        </p>
      </div>

      <div className="space-y-7">
        <Section
          title="Duplicate suppression"
          hint="Stops one ongoing event being reported over and over while it is still the same event."
        >
        <Field label="Deduplication window" hint="seconds">
          <Input
            type="number" min={0}
            value={num(cfg, "fd_dedup_window", 60)}
            onChange={(e) => set("fd_dedup_window", Number(e.target.value))}
          />
        </Field>
        <Field label="Deduplication scope" hint="track is safest">
          <Select
            value={str(cfg, "fd_dedup_scope", "track")}
            onChange={(e) => set("fd_dedup_scope", e.target.value)}
            options={[
              { value: "track", label: "track — per person" },
              { value: "camera", label: "camera — one alert per camera" },
              { value: "global", label: "global" },
            ]}
          />
        </Field>
        </Section>

        <Section
          title="Alert volume"
          hint="Caps what a single camera can produce, so one noisy scene cannot bury everything else."
        >
        <Field label="Max alerts / minute" hint="per camera, 0 = off">
          <Input
            type="number" min={0}
            value={num(cfg, "fd_max_alerts_per_minute", 20)}
            onChange={(e) => set("fd_max_alerts_per_minute", Number(e.target.value))}
          />
        </Field>
        <Field label="Storm threshold" hint="alerts in the window">
          <Input
            type="number" min={1}
            value={num(cfg, "fd_storm_threshold", 40)}
            onChange={(e) => set("fd_storm_threshold", Number(e.target.value))}
          />
        </Field>
        <Field label="Storm action">
          <Select
            value={str(cfg, "fd_storm_action", "demote")}
            onChange={(e) => set("fd_storm_action", e.target.value)}
            options={[
              { value: "demote", label: "demote — keep the row, drop its priority" },
              { value: "suppress", label: "suppress — drop it entirely" },
              { value: "none", label: "none" },
            ]}
          />
        </Field>
        </Section>

        <Section
          title="Evidence quality"
          hint="Rejects weak detections before they become alerts. Set either to 0 to disable that check."
        >
        <Field label="Minimum confidence" hint="0 = off">
          <Input
            type="number" min={0} max={1} step={0.05}
            value={num(cfg, "fd_min_confidence", 0)}
            onChange={(e) => set("fd_min_confidence", Number(e.target.value))}
          />
        </Field>
        <Field label="Minimum evidence frames" hint="0 = off">
          <Input
            type="number" min={0}
            value={num(cfg, "fd_min_evidence_frames", 0)}
            onChange={(e) => set("fd_min_evidence_frames", Number(e.target.value))}
          />
        </Field>
        </Section>

        <Section
          title="Severity routing"
          hint="Which events reach the alert feed, and which are worth writing a snapshot to disk for."
        >
        <Field label="Alert feed shows" hint="and above">
          <Select
            value={str(cfg, "fd_min_severity_alert", "low")}
            onChange={(e) => set("fd_min_severity_alert", e.target.value)}
            options={SEVERITY_OPTIONS}
          />
        </Field>
        <Field label="Save snapshots for" hint="and above — controls disk growth">
          <Select
            value={str(cfg, "fd_min_severity_snapshot", "medium")}
            onChange={(e) => set("fd_min_severity_snapshot", e.target.value)}
            options={SEVERITY_OPTIONS}
          />
        </Field>
        </Section>

        <Section
          title="Repeated source"
          hint="Learns fixed spots that alert constantly — a poster, a reflection, a screen on a wall."
        >
        <Field label="Repeated-source hits" hint="before an address is furniture">
          <Input
            type="number" min={1}
            value={num(cfg, "fd_repeat_hits", 25)}
            onChange={(e) => set("fd_repeat_hits", Number(e.target.value))}
          />
        </Field>
        <Field label="Repeated-source tracks" hint="different people needed">
          <Input
            type="number" min={1}
            value={num(cfg, "fd_repeat_min_tracks", 3)}
            onChange={(e) => set("fd_repeat_min_tracks", Number(e.target.value))}
          />
        </Field>
        </Section>

        <Section
          title="Timing"
          hint="A camera's first seconds are full of false positives while exposure and focus settle."
        >
        <Field label="Warm-up" hint="seconds after a camera starts">
          <Input
            type="number" min={0}
            value={num(cfg, "fd_warmup_seconds", 30)}
            onChange={(e) => set("fd_warmup_seconds", Number(e.target.value))}
          />
        </Field>
        </Section>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
        <p className="text-[11px] text-faint">
          Ignore zones are drawn per camera in the camera editor.
        </p>
        <div className="flex gap-2">
          <Button variant="subtle" size="sm" onClick={reset}>
            <RotateCcw size={13} /> Defaults
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving ? <><Spinner size="sm" /> Saving…</> : "Save"}
          </Button>
        </div>
      </div>
    </Card>
  );
}
