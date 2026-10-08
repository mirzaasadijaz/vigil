import { useState } from "react";
import { Layers, Trash2, Plus, X, Settings2 } from "lucide-react";
import { Button, Card, Input, Select, Modal, SectionTitle, Spinner, Table, Toast, Badge, cx } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { apiFetch } from "../lib/api";
import { CameraModulesEditor, type ModuleAssignment } from "../components/domain/CameraModulesEditor";
import { MODULE_BY_ID } from "../lib/modules";

interface Channel { id: number; name: string; channel: number; fps: number; module_config?: ModuleAssignment[] }
interface NVR { id: number; name: string; ip_address: string; port: number; username: string; password: string; rtsp_template_id: number | null; channels?: Channel[] }
interface Template { id: number; name: string; template_string: string }

const blank = { name: "", ip_address: "", port: "554", username: "", password: "", rtsp_template: "" };

// Build the resolved RTSP URL for one channel, mirroring the backend
// CameraDevice.get_rtsp_url(): base = rtsp://user:pass@ip:port, then the template
// string (a path suffix) with {channel} replaced. Password shown in clear so the
// URL can be copied straight into VLC / the NVR for testing.
function buildNvrRtsp(template: string, ip: string, port: number | string, username: string, password: string, channel: number | string): string {
  const auth = username && password ? `${username}:${password}@` : username ? `${username}@` : "";
  let suffix = (template || "").replace(/\{channel\}/g, String(channel));
  if (!suffix.startsWith("/")) suffix = "/" + suffix;
  return `rtsp://${auth}${ip || "<ip>"}:${port || 554}${suffix}`;
}

// Parse "1-16, 20, 22" into a sorted unique list of channel numbers.
function parseChannels(s: string): number[] {
  const out = new Set<number>();
  for (const part of s.split(",")) {
    const t = part.trim();
    if (!t) continue;
    const m = t.match(/^(\d+)\s*-\s*(\d+)$/);
    if (m) { let a = +m[1], b = +m[2]; if (a > b) [a, b] = [b, a]; for (let i = a; i <= b; i++) out.add(i); }
    else if (/^\d+$/.test(t)) out.add(+t);
  }
  return [...out].sort((a, b) => a - b);
}

export default function NvrDvrView() {
  const { data: nvrs, loading, refetch } = useApi<NVR[]>("/nvrs/");
  const { data: templates } = useApi<Template[]>("/rtsp-templates/");
  const [showAdd, setShowAdd] = useState(false);
  const [editNvr, setEditNvr] = useState<NVR | null>(null);
  const [form, setForm] = useState({ ...blank });
  const [formError, setFormError] = useState("");
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  // ── channel management ──────────────────────────────────────────────────────
  const [chanNvrId, setChanNvrId] = useState<number | null>(null);
  const [chanRange, setChanRange] = useState("");
  const [chanFps, setChanFps] = useState("10");
  const [chanSaving, setChanSaving] = useState(false);
  const [chanError, setChanError] = useState("");
  const [chanDeleting, setChanDeleting] = useState<number | null>(null);
  // per-channel + bulk AI module assignment (NVR channels are configured here)
  const [chanSel, setChanSel] = useState<Set<number>>(new Set());
  const [modOpen, setModOpen] = useState(false);
  const [modAssignments, setModAssignments] = useState<ModuleAssignment[]>([]);
  const [modSaving, setModSaving] = useState(false);

  const tmplList = templates ?? [];
  const tmplName = (id: number | null) => tmplList.find((t) => t.id === id)?.name ?? "—";

  // Always read the live NVR (so the channel list refreshes after add/delete).
  const chanNvr = chanNvrId != null ? (nvrs ?? []).find((n) => n.id === chanNvrId) ?? null : null;
  const chanTmpl = tmplList.find((t) => t.id === chanNvr?.rtsp_template_id) ?? null;
  const chanPreview = parseChannels(chanRange);

  const openChannels = (n: NVR) => { setChanNvrId(n.id); setChanRange(""); setChanFps("10"); setChanError(""); setChanSel(new Set()); };

  const chanList = chanNvr?.channels ?? [];
  const toggleChanSel = (id: number) => setChanSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const allChanSelected = chanList.length > 0 && chanList.every((c) => chanSel.has(c.id));
  const toggleAllChan = () => setChanSel((s) => (chanList.every((c) => s.has(c.id)) ? new Set() : new Set(chanList.map((c) => c.id))));

  // Open the module editor for the current selection. One channel → preload its
  // modules (and allow drawing on its snapshot); many → start blank (bulk add).
  const openModules = (ids?: number[]) => {
    const sel = ids ?? [...chanSel];
    if (sel.length === 0) return;
    if (ids) setChanSel(new Set(ids));
    const single = sel.length === 1 ? chanList.find((c) => c.id === sel[0]) : null;
    setModAssignments(single?.module_config ?? []);
    setModOpen(true);
  };

  const applyChannelModules = async () => {
    const ids = [...chanSel];
    if (ids.length === 0) return;
    const enabled = modAssignments.filter((a) => a.enabled);
    setModSaving(true);
    let res: Response;
    if (ids.length === 1) {
      // Single channel: editor is authoritative (full config, incl. removals).
      res = await apiFetch(`/cameras/${ids[0]}`, { method: "PUT", body: JSON.stringify({ module_config: modAssignments }) });
    } else {
      // Bulk: merge the enabled modules onto each channel (keeps their other modules).
      res = await apiFetch("/cameras/bulk-modules", { method: "POST", body: JSON.stringify({ camera_ids: ids, assignments: enabled }) });
    }
    setModSaving(false);
    if (res.ok) {
      setToast({ message: `Applied ${enabled.length} module(s) to ${ids.length} channel(s)`, type: "success" });
      setModOpen(false); setModAssignments([]); setChanSel(new Set()); refetch();
    } else setToast({ message: "Failed to apply modules", type: "error" });
  };

  const modSnapshot = chanSel.size === 1 ? `/api/stream/snapshot?cam_id=${[...chanSel][0]}` : undefined;
  const modSingle = chanSel.size === 1 ? chanList.find((c) => c.id === [...chanSel][0]) : null;
  // Channels are auto-named "{nvr} - CH {n}", so don't prepend "CH {n} ·" when the
  // name already carries the channel — avoids a redundant "CH 3 · test - CH 3".
  const modTitle = modSingle
    ? (modSingle.name?.includes(`CH ${modSingle.channel}`) ? modSingle.name : `CH ${modSingle.channel} · ${modSingle.name}`)
    : `${chanSel.size} channels`;

  const handleAddChannels = async () => {
    if (chanNvrId == null) return;
    setChanError("");
    const channels = parseChannels(chanRange);
    if (!channels.length) { setChanError("Enter channel numbers, e.g. 1-16 or 1,2,5"); return; }
    setChanSaving(true);
    const res = await apiFetch(`/nvrs/${chanNvrId}/channels/bulk`, {
      method: "POST", body: JSON.stringify({ channels, process_frame_rate: parseInt(chanFps) || 10 }),
    });
    setChanSaving(false);
    if (!res.ok) { const d = await res.json().catch(() => ({})); setChanError(d.detail || "Error"); return; }
    const d = await res.json().catch(() => ({ added: channels.length, skipped: 0 }));
    setToast({ message: `Added ${d.added} channel(s)${d.skipped ? `, ${d.skipped} already existed` : ""}`, type: "success" });
    setChanRange(""); refetch();
  };

  const handleDeleteChannel = async (camId: number) => {
    if (chanNvrId == null) return;
    setChanDeleting(camId);
    await apiFetch(`/nvrs/${chanNvrId}/channels/${camId}`, { method: "DELETE" }).catch(() => {});
    setChanDeleting(null); refetch();
  };

  const resetForm = () => { setForm({ ...blank }); setFormError(""); };
  const openAdd = () => { resetForm(); setShowAdd(true); };
  const openEdit = (n: NVR) => {
    setForm({ name: n.name, ip_address: n.ip_address, port: String(n.port), username: n.username || "", password: "", rtsp_template: n.rtsp_template_id ? String(n.rtsp_template_id) : "" });
    setEditNvr(n);
  };
  const closeAll = () => { setShowAdd(false); setEditNvr(null); resetForm(); };
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const buildBody = () => ({
    name: form.name || form.ip_address,
    ip_address: form.ip_address,
    port: parseInt(form.port) || 554,
    username: form.username || null,
    password: form.password || null,
    rtsp_template: form.rtsp_template ? parseInt(form.rtsp_template) : null,
  });

  const handleCreate = async () => {
    setFormError("");
    if (!form.ip_address) { setFormError("IP address is required"); return; }
    if (!form.rtsp_template) { setFormError("Select an RTSP template"); return; }
    const res = await apiFetch("/nvrs/", { method: "POST", body: JSON.stringify(buildBody()) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); setFormError(d.detail || "Error"); return; }
    setToast({ message: "NVR added", type: "success" }); closeAll(); refetch();
  };

  const handleUpdate = async () => {
    if (!editNvr) return; setFormError("");
    const body: any = buildBody();
    if (!form.password) delete body.password;   // blank = keep existing password
    const res = await apiFetch(`/nvrs/${editNvr.id}`, { method: "PUT", body: JSON.stringify(body) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); setFormError(d.detail || "Error"); return; }
    setToast({ message: "NVR updated", type: "success" }); closeAll(); refetch();
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this NVR?")) return;
    setDeleting(id);
    await apiFetch(`/nvrs/${id}`, { method: "DELETE" });
    setToast({ message: "NVR deleted", type: "success" }); setDeleting(null); refetch();
  };

  const columns = [
    { key: "name", header: "Name", render: (n: NVR) => <span className="font-medium text-th">{n.name}</span> },
    { key: "ip_address", header: "IP Address", render: (n: NVR) => <span className="font-mono text-muted">{n.ip_address}:{n.port}</span> },
    { key: "rtsp", header: "RTSP Template", render: (n: NVR) => n.rtsp_template_id ? <Badge tone="blue">{tmplName(n.rtsp_template_id)}</Badge> : <Badge tone="danger">None</Badge> },
    { key: "channels", header: "Channels", render: (n: NVR) => (
      <button onClick={() => openChannels(n)} className="inline-flex items-center gap-1.5 rounded-md border border-border bg-base px-2 py-1 text-xs text-tx transition-colors hover:border-accent hover:text-accent">
        <Layers size={12} /> {n.channels?.length ?? 0} channel{(n.channels?.length ?? 0) === 1 ? "" : "s"}
      </button>
    ) },
    {
      key: "actions", header: "", render: (n: NVR) => (
        <div className="flex gap-2">
          <Button variant="subtle" size="sm" onClick={() => openChannels(n)}><Layers size={13} /> Channels</Button>
          <Button variant="subtle" size="sm" onClick={() => openEdit(n)}>Edit</Button>
          <Button variant="danger" size="sm" disabled={deleting === n.id} onClick={() => handleDelete(n.id)}>
            {deleting === n.id ? <Spinner size="sm" /> : "Delete"}
          </Button>
        </div>
      )
    },
  ];

  const selectedTmpl = tmplList.find((t) => String(t.id) === form.rtsp_template);

  const FormFields = () => (
    <div className="flex flex-col gap-4">
      <Input label="NVR Name" placeholder="e.g. Main Building NVR" value={form.name} onChange={set("name")} />
      <Input label="IP Address *" placeholder="192.168.1.50" value={form.ip_address} onChange={set("ip_address")} />
      <div className="grid grid-cols-3 gap-3">
        <Input label="Port" type="number" value={form.port} onChange={set("port")} />
        <Input label="Username" value={form.username} onChange={set("username")} />
        <Input label="Password" type="password" value={form.password} onChange={set("password")} />
      </div>
      <Select label="RTSP Template *" value={form.rtsp_template}
        onChange={(e) => setForm((f) => ({ ...f, rtsp_template: e.target.value }))}
        options={[{ value: "", label: tmplList.length ? "Select a template…" : "No templates — add in RTSP Strings" },
          ...tmplList.map((t) => ({ value: String(t.id), label: t.name }))]} />
      {selectedTmpl && (
        <div className="rounded-md border border-border bg-base px-3 py-2">
          <p className="text-[10px] font-bold uppercase tracking-wider text-faint mb-1">Stream URL pattern</p>
          <p className="font-mono text-xs text-accent-text break-all">
            {buildNvrRtsp(selectedTmpl.template_string, form.ip_address, form.port, form.username, form.password || editNvr?.password || "", "{channel}")}
          </p>
          <p className="mt-1 text-[11px] text-faint"><span className="font-mono text-tx">{"{channel}"}</span> is replaced with each channel number you add — see the per-channel URLs under <span className="text-tx">Channels</span>.</p>
        </div>
      )}
      {formError && <p className="text-sm text-dnd-text">{formError}</p>}
    </div>
  );

  return (
    <div className="space-y-6">
      <SectionTitle title="NVR / DVR" subtitle="Manage network video recorders & their RTSP templates" action={<Button onClick={openAdd}>+ Add NVR</Button>} />
      <Card className="p-0 overflow-hidden">
        {loading ? <div className="flex justify-center py-16"><Spinner /></div> : <Table columns={columns} data={nvrs ?? []} keyField="id" emptyMessage="No NVRs found" />}
      </Card>
      <Modal open={showAdd} onClose={closeAll} title="Add NVR">{FormFields()}<div className="flex gap-3 mt-5"><Button variant="ghost" className="flex-1" onClick={closeAll}>Cancel</Button><Button className="flex-1" onClick={handleCreate}>Add NVR</Button></div></Modal>
      <Modal open={!!editNvr} onClose={closeAll} title="Edit NVR">{FormFields()}<div className="flex gap-3 mt-5"><Button variant="ghost" className="flex-1" onClick={closeAll}>Cancel</Button><Button className="flex-1" onClick={handleUpdate}>Save</Button></div></Modal>

      {/* Channels */}
      <Modal open={chanNvrId != null} onClose={() => setChanNvrId(null)} size="xl" title={chanNvr ? `${chanNvr.name} — Channels` : "Channels"}>
        <div className="space-y-5">
          {/* add channels */}
          <div className="rounded-lg border border-border bg-base/40 p-4">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">Add channels</p>
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-[180px] flex-1">
                <Input label="Channel numbers" placeholder="e.g. 1-16 or 1,2,5" value={chanRange} onChange={(e) => setChanRange(e.target.value)} />
              </div>
              <div className="w-28">
                <Input label="Frame rate" type="number" value={chanFps} onChange={(e) => setChanFps(e.target.value)} />
              </div>
              <Button onClick={handleAddChannels} disabled={chanSaving || chanPreview.length === 0}>
                {chanSaving ? <Spinner size="sm" /> : <><Plus size={14} /> Add {chanPreview.length || ""}</>}
              </Button>
            </div>
            {chanPreview.length > 0 && (
              <>
                <p className="mt-2 text-[11px] text-faint">Will add channels: {chanPreview.join(", ")} @ {parseInt(chanFps) || 10} fps</p>
                {chanNvr && chanTmpl && (
                  <p className="mt-1 font-mono text-[11px] text-accent-text break-all">
                    e.g. CH {chanPreview[0]} → {buildNvrRtsp(chanTmpl.template_string, chanNvr.ip_address, chanNvr.port, chanNvr.username, chanNvr.password, chanPreview[0])}
                  </p>
                )}
              </>
            )}
            {chanError && <p className="mt-2 text-sm text-dnd-text">{chanError}</p>}
          </div>

          {/* existing channels */}
          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <label className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted">
                {chanList.length > 0 && <input type="checkbox" checked={allChanSelected} onChange={toggleAllChan} className="accent-[var(--color-accent)]" aria-label="Select all channels" />}
                Existing channels ({chanList.length})
              </label>
              {chanSel.size > 0 && (
                <div className="flex items-center gap-2">
                  <Button size="sm" onClick={() => openModules()}><Settings2 size={13} /> Modules ({chanSel.size})</Button>
                  <Button variant="ghost" size="sm" onClick={() => setChanSel(new Set())}><X size={12} /> Clear</Button>
                </div>
              )}
            </div>
            {!chanList.length ? (
              <p className="rounded-md border border-dashed border-border py-8 text-center text-sm text-faint">No channels yet — add some above.</p>
            ) : (
              <div className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
                {[...chanList].sort((a, b) => a.channel - b.channel).map((c) => {
                  const mods = (c.module_config ?? []).filter((a) => a.enabled);
                  return (
                  <div key={c.id} className={cx("rounded-md border bg-base px-3 py-2", chanSel.has(c.id) ? "border-accent/40" : "border-border")}>
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2 min-w-0">
                        <input type="checkbox" checked={chanSel.has(c.id)} onChange={() => toggleChanSel(c.id)} className="accent-[var(--color-accent)]" aria-label={`Select channel ${c.channel}`} />
                        <Badge tone="blue">CH {c.channel}</Badge>
                        <span className="truncate text-xs text-muted">{c.name}</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="font-mono text-[11px] text-faint">{c.fps} fps</span>
                        <button onClick={() => openModules([c.id])} className="text-faint transition-colors hover:text-accent" aria-label={`Edit modules for channel ${c.channel}`}><Settings2 size={14} /></button>
                        <button onClick={() => handleDeleteChannel(c.id)} disabled={chanDeleting === c.id}
                          className="text-faint transition-colors hover:text-dnd disabled:opacity-50" aria-label={`Delete channel ${c.channel}`}>
                          {chanDeleting === c.id ? <Spinner size="sm" /> : <Trash2 size={14} />}
                        </button>
                      </div>
                    </div>
                    {mods.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {mods.map((a) => <Badge key={a.module} tone="blue">{MODULE_BY_ID[a.module]?.name.split(" ")[0] ?? a.module}</Badge>)}
                      </div>
                    )}
                    {chanTmpl
                      ? <p className="mt-1.5 font-mono text-[11px] text-faint break-all">{buildNvrRtsp(chanTmpl.template_string, chanNvr!.ip_address, chanNvr!.port, chanNvr!.username, chanNvr!.password, c.channel)}</p>
                      : <p className="mt-1.5 text-[11px] text-dnd-text">No RTSP template set on this NVR.</p>}
                  </div>
                  );
                })}
              </div>
            )}
            <p className="mt-3 text-[11px] text-faint">Select channels and click <span className="text-tx">Modules</span> to assign AI modules in bulk, or use the <Settings2 size={11} className="inline" /> icon to configure one channel (with zone drawing).</p>
          </div>
        </div>
      </Modal>

      {/* AI module editor for the selected channel(s) — sits above the Channels modal */}
      {modOpen && (
        <div role="dialog" aria-modal="true" className="fixed inset-0 z-[110] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onClick={() => !modSaving && setModOpen(false)}>
          <div className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-lg border border-border bg-surface shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-border px-6 py-4">
              <h2 className="text-base font-semibold text-th">AI Modules — {modTitle}</h2>
              <button onClick={() => setModOpen(false)} className="flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-white/10 hover:text-white" aria-label="Close">✕</button>
            </div>
            <div className="overflow-y-auto px-6 py-5 space-y-4">
              {chanSel.size > 1 && <p className="text-[11px] text-idle">Bulk mode: enabled modules are merged onto every selected channel (their other modules are kept). Draw per-channel zones afterwards via the single-channel editor.</p>}
              <CameraModulesEditor value={modAssignments} onChange={setModAssignments} snapshotUrl={modSnapshot} />
              <div className="flex gap-3 border-t border-border pt-4">
                <Button variant="ghost" className="flex-1" onClick={() => setModOpen(false)}>Cancel</Button>
                <Button className="flex-1" onClick={applyChannelModules} disabled={modSaving}>{modSaving ? <><Spinner size="sm" /> Saving…</> : "Save"}</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}
    </div>
  );
}
