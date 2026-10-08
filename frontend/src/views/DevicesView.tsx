import { useState, useMemo } from "react";
import { Search, Layers, X } from "lucide-react";
import { Badge, Button, Card, Input, Modal, Select, SectionTitle, Spinner, Toast, cx } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { apiFetch } from "../lib/api";
import { CameraModulesEditor, type ModuleAssignment } from "../components/domain/CameraModulesEditor";
import { MODULE_BY_ID } from "../lib/modules";

interface Camera {
  id: number;
  name: string;
  ip_address: string;
  username: string;
  password: string;
  channel: number;
  process_frame_rate: number;
  is_active: boolean;
  location: string | null;
  department: string | null;
  module_config?: ModuleAssignment[];
  nvr_id: number | null;
  nvr_name: string | null;
}

const blank = { name: "", ip_address: "", username: "", password: "", channel: "102", process_frame_rate: "10", location: "", department: "" };

export default function DevicesView() {
  const { data: cameras, loading, refetch } = useApi<Camera[]>("/cameras/?ip_only=true");
  const { data: settings } = useApi<{ locations: string[]; departments: string[] }>("/settings/");
  const [showAdd, setShowAdd] = useState(false);
  const [editCam, setEditCam] = useState<Camera | null>(null);
  const [form, setForm] = useState({ ...blank });
  const [modules, setModules] = useState<ModuleAssignment[]>([]);
  const [formError, setFormError] = useState("");
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  // ── directory filters + bulk selection ──────────────────────────────────────
  const [search, setSearch] = useState("");
  const [locFilter, setLocFilter] = useState("all");
  const [zoneFilter, setZoneFilter] = useState<"all" | "default" | "custom">("all");
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkModules, setBulkModules] = useState<ModuleAssignment[]>([]);
  const [bulkSaving, setBulkSaving] = useState(false);

  const camList = cameras ?? [];
  const locations = useMemo(() => [...new Set(camList.map((c) => c.location).filter(Boolean) as string[])].sort(), [camList]);

  const zoneClass = (c: Camera): "default" | "custom" | "none" => {
    const geo = (c.module_config || []).filter((a) => a.enabled && (a.line || a.zones));
    if (geo.some((a) => a.zone_default === false)) return "custom";
    if (geo.some((a) => a.zone_default === true)) return "default";
    return "none";
  };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return camList.filter((c) => {
      if (locFilter !== "all" && c.location !== locFilter) return false;
      if (zoneFilter !== "all" && zoneClass(c) !== zoneFilter) return false;
      if (q && !`${c.name} ${c.ip_address ?? ""} ${c.location ?? ""}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [camList, search, locFilter, zoneFilter]);

  const allSelected = filtered.length > 0 && filtered.every((c) => sel.has(c.id));
  const toggleSel = (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAll = () => setSel((s) => {
    if (filtered.every((c) => s.has(c.id))) { const n = new Set(s); filtered.forEach((c) => n.delete(c.id)); return n; }
    return new Set([...s, ...filtered.map((c) => c.id)]);
  });

  const openBulk = () => { setBulkModules([]); setBulkOpen(true); };

  const applyBulk = async () => {
    const assignments = bulkModules.filter((a) => a.enabled);
    if (assignments.length === 0 || sel.size === 0) return;
    setBulkSaving(true);
    const res = await apiFetch("/cameras/bulk-modules", { method: "POST", body: JSON.stringify({ camera_ids: [...sel], assignments }) });
    setBulkSaving(false);
    if (res.ok) {
      const d = await res.json().catch(() => ({ updated: sel.size, modules: assignments.length }));
      setToast({ message: `Applied ${d.modules ?? assignments.length} module(s) to ${d.updated} camera(s)`, type: "success" });
      setBulkOpen(false); setBulkModules([]); setSel(new Set()); refetch();
    } else setToast({ message: "Bulk apply failed", type: "error" });
  };

  const resetForm = () => { setForm({ ...blank }); setModules([]); setFormError(""); };
  const openAdd = () => { resetForm(); setShowAdd(true); };
  const openEdit = (c: Camera) => {
    setForm({ name: c.name, ip_address: c.ip_address, username: c.username || "", password: c.password || "", channel: String(c.channel), process_frame_rate: String(c.process_frame_rate), location: c.location || "", department: c.department || "" });
    setModules(c.module_config || []);
    setEditCam(c);
  };
  const closeAll = () => { setShowAdd(false); setEditCam(null); resetForm(); };

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const handleCreate = async () => {
    setFormError("");
    if (!form.ip_address) { setFormError("IP address is required"); return; }
    const body = { name: form.name || form.ip_address, ip_address: form.ip_address, username: form.username || null, password: form.password || null, channel: parseInt(form.channel), process_frame_rate: parseInt(form.process_frame_rate), location: form.location || null, department: form.department || null, module_config: modules, is_active: true };
    const res = await apiFetch("/cameras/", { method: "POST", body: JSON.stringify(body) });
    if (!res.ok) { const d = await res.json(); setFormError(d.detail || "Error"); return; }
    setToast({ message: "Camera added", type: "success" }); closeAll(); refetch();
  };

  const handleUpdate = async () => {
    if (!editCam) return; setFormError("");
    const body: any = { name: form.name, ip_address: form.ip_address, username: form.username || null, channel: parseInt(form.channel), process_frame_rate: parseInt(form.process_frame_rate), location: form.location || null, department: form.department || null, module_config: modules };
    // Only overwrite the password if the user actually typed one — blank means "keep existing".
    if (form.password) body.password = form.password;
    const res = await apiFetch(`/cameras/${editCam.id}`, { method: "PUT", body: JSON.stringify(body) });
    if (!res.ok) { const d = await res.json(); setFormError(d.detail || "Error"); return; }
    setToast({ message: "Camera updated", type: "success" }); closeAll(); refetch();
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this camera?")) return;
    setDeleting(id);
    await apiFetch(`/cameras/${id}`, { method: "DELETE" }).catch(() => {});
    setToast({ message: "Camera deleted", type: "success" }); setDeleting(null); refetch();
  };

  const bulkEnabledCount = bulkModules.filter((a) => a.enabled).length;

  const FormFields = () => (
    <div className="flex flex-col gap-4">
      <Input label="Camera Name" placeholder="e.g. Front Gate" value={form.name} onChange={set("name")} />
      <Input label="IP Address *" placeholder="192.168.1.100" value={form.ip_address} onChange={set("ip_address")} />
      <div className="grid grid-cols-2 gap-4">
        <Input label="Username" value={form.username} onChange={set("username")} />
        <Input label="Password" type="password" value={form.password} onChange={set("password")} placeholder={editCam ? "•••••• (unchanged)" : ""} />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Input label="Channel" type="number" value={form.channel} onChange={set("channel")} />
        <Input label="Frame Rate" type="number" value={form.process_frame_rate} onChange={set("process_frame_rate")} />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Select label="Location" value={form.location}
          onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
          options={[{ value: "", label: "Unassigned" }, ...(settings?.locations ?? []).map((l) => ({ value: l, label: l }))]} />
        <Select label="Department" value={form.department}
          onChange={(e) => setForm((f) => ({ ...f, department: e.target.value }))}
          options={[{ value: "", label: "Unassigned" }, ...(settings?.departments ?? []).map((d) => ({ value: d, label: d }))]} />
      </div>
      {(!settings?.locations?.length && !settings?.departments?.length) && (
        <p className="text-xs text-faint">Tip: define Locations &amp; Departments in Admin Settings → Camera Metadata.</p>
      )}
      <div className="border-t border-border pt-4">
        <CameraModulesEditor
          value={modules}
          onChange={setModules}
          snapshotUrl={editCam ? `/api/stream/snapshot?cam_id=${editCam.id}` : undefined}
        />
      </div>
      {formError && <p className="text-sm text-dnd-text">{formError}</p>}
    </div>
  );

  return (
    <div className="space-y-6">
      <SectionTitle title="IP Cameras" subtitle="Direct-IP cameras & their AI modules — NVR/DVR channels live under NVR / DVR" action={<Button onClick={openAdd}>+ Add Camera</Button>} />

      <Card className="p-0 overflow-hidden">
        {/* toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-base px-5 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search cameras…"
                className="w-52 rounded-md border border-base bg-base py-2 pl-9 pr-3 text-sm text-tx placeholder:text-faint outline-none focus:border-accent focus:ring-2 focus:ring-accent/20" />
            </div>
            <select value={locFilter} onChange={(e) => setLocFilter(e.target.value)}
              className="rounded-md border border-border bg-base px-3 py-2 text-sm text-tx outline-none focus:border-accent">
              <option value="all">All locations</option>
              {locations.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
            <select value={zoneFilter} onChange={(e) => setZoneFilter(e.target.value as any)}
              className="rounded-md border border-border bg-base px-3 py-2 text-sm text-tx outline-none focus:border-accent">
              <option value="all">All zones</option>
              <option value="default">Default zones</option>
              <option value="custom">Customized zones</option>
            </select>
          </div>
          <span className="text-xs text-faint">{filtered.length} of {camList.length}</span>
        </div>

        {/* bulk action bar */}
        {sel.size > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-accent/10 px-5 py-3">
            <span className="text-sm text-tx font-medium">{sel.size} selected</span>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={openBulk}><Layers size={14} /> Apply modules</Button>
              <Button variant="ghost" size="sm" onClick={() => setSel(new Set())}><X size={13} /> Clear</Button>
            </div>
          </div>
        )}

        {loading && camList.length === 0 ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-elevated text-left text-xs font-semibold uppercase tracking-wider text-muted">
                  <th className="px-4 py-3 w-10"><input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Select all" className="accent-[var(--color-accent)]" /></th>
                  <th className="px-4 py-3">Name</th><th className="px-4 py-3">IP</th><th className="px-4 py-3">Location</th>
                  <th className="px-4 py-3">Modules</th><th className="px-4 py-3">Zone</th><th className="px-4 py-3">Status</th><th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-elevated">
                {filtered.length === 0 ? (
                  <tr><td colSpan={8} className="px-4 py-12 text-center text-faint">No cameras match.</td></tr>
                ) : filtered.map((c) => {
                  const zc = zoneClass(c);
                  const mods = (c.module_config || []).filter((a) => a.enabled);
                  return (
                    <tr key={c.id} className={cx("transition-colors hover:bg-hover", sel.has(c.id) && "bg-accent/5")}>
                      <td className="px-4 py-3"><input type="checkbox" checked={sel.has(c.id)} onChange={() => toggleSel(c.id)} aria-label={`Select ${c.name}`} className="accent-[var(--color-accent)]" /></td>
                      <td className="px-4 py-3 font-medium text-th">{c.name}</td>
                      <td className="px-4 py-3 font-mono text-muted text-xs">{c.ip_address || "—"}</td>
                      <td className="px-4 py-3 text-muted text-xs">{c.location || "—"}{c.department ? ` · ${c.department}` : ""}</td>
                      <td className="px-4 py-3">{mods.length ? <div className="flex flex-wrap gap-1">{mods.map((a) => <Badge key={a.module} tone="blue">{MODULE_BY_ID[a.module]?.name.split(" ")[0] ?? a.module}</Badge>)}</div> : <span className="text-faint text-xs">none</span>}</td>
                      <td className="px-4 py-3">{zc === "default" ? <Badge tone="neutral">Default</Badge> : zc === "custom" ? <Badge tone="blue">Custom</Badge> : <span className="text-faint text-xs">—</span>}</td>
                      <td className="px-4 py-3"><Badge tone={c.is_active ? "success" : "neutral"}>{c.is_active ? "Active" : "Inactive"}</Badge></td>
                      <td className="px-4 py-3"><div className="flex gap-2">
                        <Button variant="subtle" size="sm" onClick={() => openEdit(c)}>Edit</Button>
                        <Button variant="danger" size="sm" disabled={deleting === c.id} onClick={() => handleDelete(c.id)}>{deleting === c.id ? <Spinner size="sm" /> : "Delete"}</Button>
                      </div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal open={showAdd} onClose={closeAll} title="Add Camera">{FormFields()}<div className="flex gap-3 mt-5"><Button variant="ghost" className="flex-1" onClick={closeAll}>Cancel</Button><Button className="flex-1" onClick={handleCreate}>Add Camera</Button></div></Modal>
      <Modal open={!!editCam} onClose={closeAll} title="Edit Camera">{FormFields()}<div className="flex gap-3 mt-5"><Button variant="ghost" className="flex-1" onClick={closeAll}>Cancel</Button><Button className="flex-1" onClick={handleUpdate}>Save</Button></div></Modal>

      {/* bulk module modal — enable any number of modules at once */}
      <Modal open={bulkOpen} onClose={() => setBulkOpen(false)} size="xl" title={`Apply modules to ${sel.size} camera(s)`}>
        <div className="space-y-4">
          <p className="text-xs text-muted">Toggle any modules below to apply them to all {sel.size} selected camera(s). Geometry modules get a full-frame zone by default — refine each camera's zone afterwards in its Edit view.</p>
          <CameraModulesEditor value={bulkModules} onChange={setBulkModules} />
          <div className="flex gap-3 border-t border-border pt-4">
            <Button variant="ghost" className="flex-1" onClick={() => setBulkOpen(false)}>Cancel</Button>
            <Button className="flex-1" onClick={applyBulk} disabled={bulkEnabledCount === 0 || bulkSaving}>
              {bulkSaving ? <><Spinner size="sm" /> Applying…</> : `Apply ${bulkEnabledCount || ""} module${bulkEnabledCount === 1 ? "" : "s"} to ${sel.size}`}
            </Button>
          </div>
        </div>
      </Modal>

      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}
    </div>
  );
}
