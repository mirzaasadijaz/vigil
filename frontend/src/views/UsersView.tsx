import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { apiFetch } from "../lib/api";
import { Card, Button, Badge, Table, Modal, Input, Select, Toast, Spinner, SectionTitle } from "../components/ui";
import type { UserRole } from "../contexts/AuthContext";

interface SystemUser {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

const roleOptions = [
  { value: "viewer", label: "Viewer" },
  { value: "operator", label: "Operator" },
  { value: "supervisor", label: "Supervisor" },
  { value: "admin", label: "Admin" },
];

const roleTone: Record<UserRole, "success" | "warning" | "info" | "danger"> = {
  admin: "danger",
  supervisor: "warning",
  operator: "info",
  viewer: "success",
};

export default function UsersView() {
  const { data: users, loading, refetch } = useApi<SystemUser[]>("/users/");

  const [showCreate, setShowCreate] = useState(false);
  const [editUser, setEditUser] = useState<SystemUser | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  const [form, setForm] = useState({ name: "", email: "", password: "", role: "viewer" as UserRole });
  const [formError, setFormError] = useState("");

  const resetForm = () => {
    setForm({ name: "", email: "", password: "", role: "viewer" });
    setFormError("");
  };

  const openCreate = () => { resetForm(); setShowCreate(true); };
  const openEdit = (u: SystemUser) => { setForm({ name: u.name, email: u.email || "", password: "", role: u.role }); setEditUser(u); };
  const closeAll = () => { setShowCreate(false); setEditUser(null); resetForm(); };

  const handleCreate = async () => {
    setFormError("");
    if (!form.name || !form.password) { setFormError("Name and password are required"); return; }
    try {
      const res = await apiFetch("/users/", {
        method: "POST",
        body: JSON.stringify({ name: form.name, email: form.email || null, password: form.password, role: form.role }),
      });
      if (!res.ok) { const d = await res.json(); setFormError(d.detail || "Error"); return; }
      setToast({ message: "User created", type: "success" });
      closeAll(); refetch();
    } catch { setFormError("Network error"); }
  };

  const handleUpdate = async () => {
    if (!editUser) return;
    setFormError("");
    try {
      const body: any = { name: form.name, email: form.email || null, role: form.role };
      if (form.password) body.password = form.password;
      const res = await apiFetch(`/users/${editUser.id}`, { method: "PUT", body: JSON.stringify(body) });
      if (!res.ok) { const d = await res.json(); setFormError(d.detail || "Error"); return; }
      setToast({ message: "User updated", type: "success" });
      closeAll(); refetch();
    } catch { setFormError("Network error"); }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this user?")) return;
    setDeleting(id);
    try {
      await apiFetch(`/users/${id}`, { method: "DELETE" });
      setToast({ message: "User deleted", type: "success" });
      refetch();
    } catch { setToast({ message: "Delete failed", type: "error" }); }
    finally { setDeleting(null); }
  };

  const columns = [
    { key: "name", header: "Name", render: (u: SystemUser) => <span className="font-medium text-th">{u.name}</span> },
    { key: "email", header: "Email", render: (u: SystemUser) => <span className="text-muted">{u.email || "—"}</span> },
    {
      key: "role", header: "Role",
      render: (u: SystemUser) => <Badge tone={roleTone[u.role]}>{u.role}</Badge>
    },
    {
      key: "is_active", header: "Status",
      render: (u: SystemUser) => <Badge tone={u.is_active ? "success" : "neutral"}>{u.is_active ? "Active" : "Inactive"}</Badge>
    },
    {
      key: "actions", header: "",
      render: (u: SystemUser) => (
        <div className="flex items-center gap-2">
          <Button variant="subtle" size="sm" onClick={() => openEdit(u)}>Edit</Button>
          <Button variant="danger" size="sm" disabled={deleting === u.id} onClick={() => handleDelete(u.id)}>
            {deleting === u.id ? <Spinner size="sm" /> : "Delete"}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <SectionTitle
        title="User Accounts"
        subtitle="Manage system users and role-based access"
        action={<Button onClick={openCreate}>+ Add User</Button>}
      />

      <Card className="p-0 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><Spinner /></div>
        ) : (
          <Table columns={columns} data={users ?? []} keyField="id" emptyMessage="No users found" />
        )}
      </Card>

      {/* Create Modal */}
      <Modal open={showCreate} onClose={closeAll} title="Create User">
        <div className="flex flex-col gap-4">
          <Input label="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          <Input label="Email" type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
          <Input label="Password" type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} />
          <Select label="Role" value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as UserRole }))} options={roleOptions} />
          {formError && <p className="text-sm text-dnd-text">{formError}</p>}
          <div className="flex gap-3 pt-2">
            <Button variant="ghost" className="flex-1" onClick={closeAll}>Cancel</Button>
            <Button className="flex-1" onClick={handleCreate}>Create</Button>
          </div>
        </div>
      </Modal>

      {/* Edit Modal */}
      <Modal open={!!editUser} onClose={closeAll} title="Edit User">
        <div className="flex flex-col gap-4">
          <Input label="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          <Input label="Email" type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
          <Input label="New Password (leave blank to keep)" type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} />
          <Select label="Role" value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as UserRole }))} options={roleOptions} />
          {formError && <p className="text-sm text-dnd-text">{formError}</p>}
          <div className="flex gap-3 pt-2">
            <Button variant="ghost" className="flex-1" onClick={closeAll}>Cancel</Button>
            <Button className="flex-1" onClick={handleUpdate}>Save</Button>
          </div>
        </div>
      </Modal>

      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}
    </div>
  );
}
