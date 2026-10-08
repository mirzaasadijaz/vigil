import { useEffect, useState } from "react";
import { ToggleLeft, AlertTriangle } from "lucide-react";
import { Badge, Button, Card, Spinner, Switch, Toast, cx } from "../ui";
import { apiFetch, apiJson } from "../../lib/api";

/**
 * Global capability switches.
 *
 * These sit ON TOP of the per-camera module assignments, they do not replace them. The
 * panel says so explicitly, because an operator who thinks this is the per-camera
 * configuration will switch something off here and wonder why the camera editor still
 * shows it assigned.
 *
 * The important property, stated in the UI as well as in the API: a capability that has
 * never been toggled is ENABLED. Switching one off here can only ever subtract from what
 * the cameras asked for.
 */

interface ModuleRow {
  id: string;
  name: string;
  phase: number | null;
  backbone: string | null;
  geometry: string | null;
  event_types: string[];
  description: string;
  enabled: boolean;
  configured: boolean;
}

interface ModulesResponse {
  modules: ModuleRow[];
  stored: Record<string, boolean>;
  live: Record<string, boolean>;
}

// Grouping mirrors section 2 of plan_kohat_poc.md. A flat list of thirteen capabilities
// is not scannable, and the categories are how an operator thinks about them.
const CATEGORIES: { title: string; hint: string; ids: string[] }[] = [
  { title: "Perimeter & Access", hint: "Lines and restricted areas",
    ids: ["tripwire", "intrusion"] },
  { title: "Asset & Scene", hint: "The camera itself, and what is left behind",
    ids: ["tamper", "no_stopping"] },
  { title: "Crowd & Behaviour", hint: "How people move and group",
    ids: ["crowd", "occupancy", "idle", "headcount"] },
  { title: "Conduct", hint: "Policy breaches by individuals",
    ids: ["mobile", "smoking"] },
  { title: "Threat", hint: "Life-safety events",
    ids: ["fire_smoke"] },
  { title: "Identity", hint: "Who, rather than what",
    ids: ["attendance", "reid"] },
];

export default function ModuleTogglePanel() {
  const [data, setData] = useState<ModulesResponse | null>(null);
  const [draft, setDraft] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const body = await apiJson<ModulesResponse>("/settings/modules");
      setData(body);
      setDraft(Object.fromEntries(body.modules.map((m) => [m.id, m.enabled])));
      setError(null);
    } catch {
      setError("Could not load capability switches. Has the migration been applied?");
    }
  };

  useEffect(() => { void load(); }, []);

  const save = async () => {
    setSaving(true);
    try {
      const res = await apiFetch("/settings/modules", {
        method: "PUT",
        body: JSON.stringify(draft),
      });
      if (!res.ok) throw new Error(await res.text());
      setToast("Capability switches saved — applied immediately, no restart needed");
      await load();
    } catch (e) {
      setToast(`Save failed: ${e instanceof Error ? e.message : "unknown error"}`);
    } finally {
      setSaving(false);
    }
  };

  if (error) {
    return (
      <Card className="p-6">
        <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Capabilities</h3>
        <p className="mt-2 text-xs text-dnd">{error}</p>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card className="p-6 flex items-center gap-3">
        <Spinner size="sm" />
        <span className="text-xs text-faint">Loading capability switches…</span>
      </Card>
    );
  }

  const byId = new Map(data.modules.map((m) => [m.id, m]));
  const dirty = data.modules.some((m) => draft[m.id] !== m.enabled);
  const offCount = Object.values(draft).filter((v) => !v).length;
  const known = new Set(CATEGORIES.flatMap((c) => c.ids));
  const ungrouped = data.modules.filter((m) => !known.has(m.id)).map((m) => m.id);
  const groups = ungrouped.length
    ? [...CATEGORIES, { title: "Other", hint: "Not yet categorised", ids: ungrouped }]
    : CATEGORIES;

  return (
    <Card className="p-6 space-y-6">
      {toast && <Toast message={toast} onDismiss={() => setToast(null)} />}

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <ToggleLeft size={16} className="mt-0.5 text-accent" />
          <div>
            <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Capabilities</h3>
            <p className="text-xs text-faint mt-1 max-w-2xl">
              A global switch per capability, on top of the per-camera assignments — not
              a replacement for them. Switching one off here stops it everywhere while
              leaving each camera&apos;s configuration intact, so you can switch it back
              on without redoing anything.
            </p>
          </div>
        </div>
        {offCount > 0 && (
          <Badge>
            <AlertTriangle size={12} className="text-idle" />
            <span className="ml-1 text-idle">{offCount} disabled</span>
          </Badge>
        )}
      </div>

      <div className="space-y-6">
        {groups.map((cat) => {
          const rows = cat.ids.map((id) => byId.get(id)).filter(Boolean) as ModuleRow[];
          if (!rows.length) return null;
          return (
            <div key={cat.title} className="space-y-2">
              <div className="border-b border-border pb-2">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-th">{cat.title}</h4>
                <p className="mt-0.5 text-[11px] text-faint">{cat.hint}</p>
              </div>
              {rows.map((m) => {
                const on = draft[m.id] ?? true;
                return (
                  <div
                    key={m.id}
                    className={cx(
                      "flex items-start justify-between gap-4 rounded-md border border-border p-3 transition-opacity",
                      !on && "opacity-60",
                    )}
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-medium text-th">{m.name}</span>
                        {m.backbone === null && (
                          <Badge><span className="text-[10px] text-faint">no model</span></Badge>
                        )}
                        {!on && (
                          <Badge><span className="text-[10px] text-dnd">OFF everywhere</span></Badge>
                        )}
                      </div>
                      <p className="mt-1 text-[11px] text-faint line-clamp-2">{m.description}</p>
                      {m.event_types.length > 0 && (
                        <p className="mt-1 text-[10px] text-faint font-mono">
                          {m.event_types.join(" · ")}
                        </p>
                      )}
                    </div>
                    <Switch
                      checked={on}
                      onChange={() => setDraft((p) => ({ ...p, [m.id]: !on }))}
                    />
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
        <p className="text-[11px] text-faint">
          A capability that has never been switched here is enabled. Changes apply
          immediately — no restart.
        </p>
        <Button onClick={save} disabled={saving || !dirty}>
          {saving ? <><Spinner size="sm" /> Saving…</> : dirty ? "Save changes" : "Saved"}
        </Button>
      </div>
    </Card>
  );
}
