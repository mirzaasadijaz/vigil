import { useState } from "react";
import { Button, Card, Input, Modal, SectionTitle, Spinner, Table, Toast } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { apiFetch } from "../lib/api";

interface RTSPTemplate { id: number; name: string; template_string: string }
const blank = { name: "", template_string: "" };

export default function RtspStringsView() {
  const { data: templates, loading, refetch } = useApi<RTSPTemplate[]>("/rtsp-templates/");
  const [showAdd, setShowAdd] = useState(false);
  const [editT, setEditT] = useState<RTSPTemplate | null>(null);
  const [form, setForm] = useState({ ...blank });
  const [formError, setFormError] = useState("");
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  const resetForm = () => { setForm({ ...blank }); setFormError(""); };
  const openAdd = () => { resetForm(); setShowAdd(true); };
  const openEdit = (t: RTSPTemplate) => { setForm({ name: t.name, template_string: t.template_string }); setEditT(t); };
  const closeAll = () => { setShowAdd(false); setEditT(null); resetForm(); };
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const handleCreate = async () => {
    setFormError("");
    if (!form.name || !form.template_string) { setFormError("Name and template are required"); return; }
    const res = await apiFetch("/rtsp-templates/", { method: "POST", body: JSON.stringify(form) });
    if (!res.ok) { const d = await res.json(); setFormError(d.detail || "Error"); return; }
    setToast({ message: "Template added", type: "success" }); closeAll(); refetch();
  };

  const handleUpdate = async () => {
    if (!editT) return; setFormError("");
    const res = await apiFetch(`/rtsp-templates/${editT.id}`, { method: "PUT", body: JSON.stringify(form) });
    if (!res.ok) { const d = await res.json(); setFormError(d.detail || "Error"); return; }
    setToast({ message: "Template updated", type: "success" }); closeAll(); refetch();
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this template?")) return;
    setDeleting(id);
    await apiFetch(`/rtsp-templates/${id}`, { method: "DELETE" });
    setToast({ message: "Template deleted", type: "success" }); setDeleting(null); refetch();
  };

  const columns = [
    { key: "name", header: "Name", render: (t: RTSPTemplate) => <span className="font-medium text-th">{t.name}</span> },
    { key: "template_string", header: "Template String", render: (t: RTSPTemplate) => <span className="font-mono text-xs text-muted break-all">{t.template_string}</span> },
    {
      key: "actions", header: "", render: (t: RTSPTemplate) => (
        <div className="flex gap-2">
          <Button variant="subtle" size="sm" onClick={() => openEdit(t)}>Edit</Button>
          <Button variant="danger" size="sm" disabled={deleting === t.id} onClick={() => handleDelete(t.id)}>
            {deleting === t.id ? <Spinner size="sm" /> : "Delete"}
          </Button>
        </div>
      )
    },
  ];

  const FormFields = () => (
    <div className="flex flex-col gap-4">
      <Input label="Template Name *" placeholder="e.g. Hikvision H264" value={form.name} onChange={set("name")} />
      <Input label="RTSP Template *" placeholder="rtsp://{username}:{password}@{ip}:{port}/..." value={form.template_string} onChange={set("template_string")} />
      <p className="text-xs text-border">Use {"{ip}"}, {"{port}"}, {"{username}"}, {"{password}"}, {"{channel}"} as placeholders.</p>
      {formError && <p className="text-sm text-dnd-text">{formError}</p>}
    </div>
  );

  return (
    <div className="space-y-6">
      <SectionTitle title="RTSP Strings" subtitle="Manage RTSP URL templates for camera brands" action={<Button onClick={openAdd}>+ Add Template</Button>} />
      <Card className="p-0 overflow-hidden">
        {loading ? <div className="flex justify-center py-16"><Spinner /></div> : <Table columns={columns} data={templates ?? []} keyField="id" emptyMessage="No templates found" />}
      </Card>
      <Modal open={showAdd} onClose={closeAll} title="Add RTSP Template">{FormFields()}<div className="flex gap-3 mt-5"><Button variant="ghost" className="flex-1" onClick={closeAll}>Cancel</Button><Button className="flex-1" onClick={handleCreate}>Add</Button></div></Modal>
      <Modal open={!!editT} onClose={closeAll} title="Edit RTSP Template">{FormFields()}<div className="flex gap-3 mt-5"><Button variant="ghost" className="flex-1" onClick={closeAll}>Cancel</Button><Button className="flex-1" onClick={handleUpdate}>Save</Button></div></Modal>
      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}
    </div>
  );
}
