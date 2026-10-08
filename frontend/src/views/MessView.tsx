import { useEffect, useMemo, useRef, useState } from "react";
import {
  Receipt, Plus, Minus, Trash2, Wallet, UtensilsCrossed,
  Clock, CircleDot, CalendarDays, UserPlus, Camera, MapPin, ChevronLeft, ChevronRight,
  Pencil, Eye, EyeOff, Check, Search,
} from "lucide-react";
import { Badge, Button, Card, Input, Modal, Spinner, Toast, cx } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { useCameraStream } from "../hooks/useCameraStream";
import { useAuth } from "../contexts/AuthContext";
import { fmtTime } from "../components/domain/attendance";
import { apiJson } from "../lib/api";

type MenuItem = { id: number; name: string; category: string; price: number; icon?: string | null; is_active: boolean };
type LineItem = { id: number; menu_item_id: number; name: string; price: number; qty: number; amount: number };
type MessSession = {
  id: number;
  employee: { id: string; name: string; employee_id: string; photo: string | null } | null;
  camera_id: number | null;
  status: string;
  started_at: string | null;
  closed_at: string | null;
  total: number;
  items: LineItem[];
};
type Receipt = {
  id: number;
  employee: { name: string; employee_id: string; photo: string | null } | null;
  date: string | null;
  closed_at: string | null;
  items: LineItem[];
  total: number;
  budget: { used: number; label: string };
};
type BudgetRow = { id: string; name: string; employee_id: string; photo: string | null; used: number };
type EmpMonth = {
  employee: { id: string; name: string; employee_id: string; photo: string | null };
  period_start: string; period_end: string; label: string; used: number;
  days: { date: string | null; total: number; items: { name: string; qty: number; amount: number }[] }[];
};
type Cam = { id: number; name: string; ip_address: string; is_active: boolean; location: string | null; department: string | null; module_config?: { module: string; enabled: boolean }[] };

const rs = (n: number) => `Rs ${(n ?? 0).toLocaleString("en-PK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const cap = (s: string) => (s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const toISO = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// 15-day billing periods: 1st–15th, then 16th–end of month (handles 28/29/30/31-day months).
const periodStartIso = (d: Date) => (d.getDate() <= 15
  ? new Date(d.getFullYear(), d.getMonth(), 1)
  : new Date(d.getFullYear(), d.getMonth(), 16));

const prevPeriodIso = (iso: string) => {
  const d = new Date(iso + "T00:00:00");
  return toISO(d.getDate() === 1 ? new Date(d.getFullYear(), d.getMonth() - 1, 16) : new Date(d.getFullYear(), d.getMonth(), 1));
};

const nextPeriodIso = (iso: string) => {
  const d = new Date(iso + "T00:00:00");
  return toISO(d.getDate() === 1 ? new Date(d.getFullYear(), d.getMonth(), 16) : new Date(d.getFullYear(), d.getMonth() + 1, 1));
};

const periodEndIso = (iso: string) => {
  const d = new Date(iso + "T00:00:00");
  return toISO(d.getDate() === 1 ? new Date(d.getFullYear(), d.getMonth(), 15) : new Date(d.getFullYear(), d.getMonth() + 1, 0));
};

const pLabel = (iso: string) => {
  const s = new Date(iso + "T00:00:00");
  const e = new Date(periodEndIso(iso) + "T00:00:00");
  const fmt = (x: Date) => x.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `${fmt(s)} – ${fmt(e)}`;
};

const enabledModules = (c: Cam) => (c.module_config || []).filter((a) => a.enabled).map((a) => a.module);

/** Live tile for a canteen camera (camera that has the mess module assigned). */
function MessCamTile({ cam }: { cam: Cam }) {
  const { canvasRef, status } = useCameraStream(cam.id);
  const isLive = status === "live";
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-base transition-all duration-200 hover:border-accent/40 hover:-translate-y-0.5">
      <div className="relative aspect-video bg-black">
        <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
        <div className="absolute top-2 left-2">
          <span className={cx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[9px] font-bold tracking-wider",
            isLive ? "bg-online/20 text-online-text border border-online/30" : "bg-black/60 text-muted border border-border")}>
            <span className={cx("h-1.5 w-1.5 rounded-full", isLive ? "bg-online animate-pulse" : "bg-faint")} />
            {isLive ? "LIVE" : status.toUpperCase()}
          </span>
        </div>
        <div className="absolute top-2 right-2">
          <span className="inline-flex items-center gap-1 rounded-md bg-black/60 border border-border px-1.5 py-0.5 text-[9px] font-semibold text-accent-text">
            <UtensilsCrossed size={9} /> Mess
          </span>
        </div>
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-3 py-2">
          <p className="text-xs font-semibold text-white truncate">{cam.name}</p>
          <p className="text-[10px] text-white/60 truncate flex items-center gap-1"><MapPin size={9} />{cam.location ?? "Unassigned"} · {cam.department ?? "—"}</p>
        </div>
      </div>
    </div>
  );
}

export default function MessView() {
  const { user } = useAuth();
  const role = user?.role ?? "viewer";
  const canBill = ["admin", "supervisor", "operator"].includes(role);
  const canEditMenu = ["admin", "supervisor"].includes(role);

  const [period, setPeriod] = useState(toISO(periodStartIso(new Date())));
  const [activeBill, setActiveBill] = useState<MessSession | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualEmp, setManualEmp] = useState("");
  const [manualQuery, setManualQuery] = useState("");
  const [empMonth, setEmpMonth] = useState<EmpMonth | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [openSessions, setOpenSessions] = useState<MessSession[]>([]);
  const autoSeenRef = useRef<Set<number>>(new Set());
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState<MenuItem | null>(null);
  const [form, setForm] = useState({ name: "", price: "", category: "meal" });

  const { data: menu, refetch: refetchMenu } = useApi<MenuItem[]>("/mess/menu");
  const { data: cameras } = useApi<Cam[]>("/cameras/");
  const { data: stats, refetch: refetchStats } = useApi<{ total_spent: number; employees: number }>("/mess/stats");
  const { data: budgetData, refetch: refetchBudget } = useApi<{ period_start: string; period_end: string; label: string; employees: BudgetRow[] }>(`/mess/budget?period=${period}`);
  const { data: employees, refetch: refetchEmps } = useApi<any[]>("/employees/");

  usePolling(() => { refetchMenu(); }, 5000);
  usePolling(() => { refetchBudget(); refetchEmps(); refetchStats(); }, 30000);

  // Poll live (open) sessions; auto-open the billing modal the moment a recognised
  // employee's bill appears. Runs in the async fetch callback, so it is safe to setState.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await apiJson<{ sessions: MessSession[]; budget: { used: number; label: string } }>("/mess/live-sessions");
        if (cancelled) return;
        setOpenSessions(res.sessions);
        setActiveBill((prev) => {
          if (prev) return prev;
          const fresh = res.sessions.find((s) => !autoSeenRef.current.has(s.id));
          if (fresh) {
            autoSeenRef.current = new Set(autoSeenRef.current).add(fresh.id);
            return fresh;
          }
          return prev;
        });
      } catch { /* transient poll errors are fine */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Canteen cameras = cameras that have the mess module assigned & enabled.
  const messCameras = useMemo(() => (cameras ?? []).filter((c) => enabledModules(c).includes("mess")), [cameras]);

  const notify = (message: string, type: "success" | "error") => setToast({ message, type });

  const openBill = (s: MessSession) => {
    autoSeenRef.current = new Set(autoSeenRef.current).add(s.id);
    setActiveBill(s);
  };

  const dismissBill = () => setActiveBill(null);

  const addItem = async (sessionId: number, menuItemId: number, qty = 1) => {
    if (!canBill) return;
    setBusy(`add-${menuItemId}`);
    try {
      const s = await apiJson<MessSession>(`/mess/sessions/${sessionId}/items`, {
        method: "POST", body: JSON.stringify({ menu_item_id: menuItemId, qty }),
      });
      setActiveBill(s);
    } catch (e: any) { notify(e?.message || "Failed to add item", "error"); }
    finally { setBusy(null); }
  };

  const removeItem = async (sessionId: number, itemId: number) => {
    if (!canBill) return;
    setBusy(`rm-${itemId}`);
    try {
      const s = await apiJson<MessSession>(`/mess/sessions/${sessionId}/items/${itemId}`, { method: "DELETE" });
      setActiveBill(s);
    } catch (e: any) { notify(e?.message || "Failed to remove item", "error"); }
    finally { setBusy(null); }
  };

  const closeBill = async (sessionId: number) => {
    if (!canBill) return;
    setBusy(`close-${sessionId}`);
    try {
      const res = await apiJson<{ receipt: Receipt }>(`/mess/sessions/${sessionId}/close`, { method: "POST" });
      setActiveBill(null);
      setOpenSessions((prev) => prev.filter((s) => s.id !== sessionId));
      setReceipt(res.receipt);
      notify("Bill closed — receipt generated", "success");
      refetchBudget();
    } catch (e: any) { notify(e?.message || "Failed to close bill", "error"); }
    finally { setBusy(null); }
  };

  const openManual = async () => {
    const emp = (employees ?? []).find((e) => String(e.id) === manualEmp);
    if (!emp) { notify("Select an employee", "error"); return; }
    setBusy("manual");
    try {
      const s = await apiJson<MessSession>("/mess/sessions", {
        method: "POST", body: JSON.stringify({ employee_key: String(emp.id) }),
      });
      setManualOpen(false);
      setManualEmp("");
      openBill(s);
    } catch (e: any) { notify(e?.message || "Failed to open bill", "error"); }
    finally { setBusy(null); }
  };

  const viewEmployeeMonth = async (id: string) => {
    try {
      const d = await apiJson<EmpMonth>(`/mess/budget?employee_key=${id}&period=${period}`);
      setEmpMonth(d);
    } catch (e: any) { notify(e?.message || "Failed to load schedule", "error"); }
  };

  const menuByCategory = useMemo(() => {
    const m = new Map<string, MenuItem[]>();
    for (const item of menu ?? []) {
      if (!item.is_active) continue;
      const cat = item.category || "meal";
      if (!m.has(cat)) m.set(cat, []);
      m.get(cat)!.push(item);
    }
    return Array.from(m.entries());
  }, [menu]);

  // All items (including inactive) grouped by category, for the management modal.
  const menuGroups = useMemo(() => {
    const m = new Map<string, MenuItem[]>();
    for (const item of menu ?? []) {
      const cat = item.category || "meal";
      if (!m.has(cat)) m.set(cat, []);
      m.get(cat)!.push(item);
    }
    return Array.from(m.entries());
  }, [menu]);

  // Searchable roster for the manual "Open New Bill" picker.
  const manualCandidates = useMemo(() => {
    const q = manualQuery.trim().toLowerCase();
    const list = (employees ?? []) as any[];
    if (!q) return list.slice(0, 60);
    return list
      .filter((e: any) =>
        String(e?.name ?? "").toLowerCase().includes(q) ||
        String(e?.employee_id ?? "").toLowerCase().includes(q) ||
        String(e?.id ?? "").toLowerCase() === q)
      .slice(0, 60);
  }, [employees, manualQuery]);

  const resetForm = () => setForm({ name: "", price: "", category: "meal" });

  const startEdit = (item: MenuItem) => {
    setEditing(item);
    setForm({ name: item.name, price: String(item.price), category: item.category || "meal" });
  };

  const saveMenuItem = async () => {
    const price = parseFloat(form.price);
    if (!form.name.trim()) { notify("Item name is required", "error"); return; }
    if (isNaN(price) || price < 0) { notify("Enter a valid price", "error"); return; }
    const payload = {
      name: form.name.trim(),
      price,
      category: form.category.trim() || "meal",
    };
    const key = editing ? `edit-${editing.id}` : "add-menu";
    setBusy(key);
    try {
      if (editing) {
        await apiJson(`/mess/menu/${editing.id}`, { method: "PUT", body: JSON.stringify(payload) });
        notify("Menu item updated", "success");
      } else {
        await apiJson("/mess/menu", { method: "POST", body: JSON.stringify(payload) });
        notify("Menu item added", "success");
      }
      setEditing(null);
      resetForm();
      refetchMenu();
    } catch (e: any) { notify(e?.message || "Failed to save menu item", "error"); }
    finally { setBusy(null); }
  };

  const toggleMenuItem = async (item: MenuItem) => {
    setBusy(`tgl-${item.id}`);
    try {
      await apiJson(`/mess/menu/${item.id}`, {
        method: "PUT", body: JSON.stringify({ is_active: !item.is_active }),
      });
      refetchMenu();
    } catch (e: any) { notify(e?.message || "Failed to update item", "error"); }
    finally { setBusy(null); }
  };

  const deleteMenuItem = async (item: MenuItem) => {
    if (!window.confirm(`Delete "${item.name}" from the menu?`)) return;
    setBusy(`del-${item.id}`);
    try {
      await apiJson(`/mess/menu/${item.id}`, { method: "DELETE" });
      notify("Menu item deleted", "success");
      refetchMenu();
    } catch (e: any) { notify(e?.message || "Failed to delete item", "error"); }
    finally { setBusy(null); }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th">Mess Management</h1>
          <p className="mt-1 text-sm text-muted">Face-recognition canteen billing · auto-open bills · shared 15-day budget</p>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex items-center gap-1 rounded-md border border-base bg-base px-1.5 py-1">
            <button onClick={() => setPeriod(prevPeriodIso(period))} className="rounded p-1 text-muted transition-colors hover:bg-white/5 hover:text-tx" title="Previous 15 days">
              <ChevronLeft size={16} />
            </button>
            <div className="min-w-[130px] px-2 text-center">
              <p className="text-sm font-medium text-tx">{pLabel(period)}</p>
              <p className="text-[10px] uppercase tracking-wider text-faint">15-day budget period</p>
            </div>
            <button onClick={() => setPeriod(nextPeriodIso(period))} className="rounded p-1 text-muted transition-colors hover:bg-white/5 hover:text-tx" title="Next 15 days">
              <ChevronRight size={16} />
            </button>
          </div>
          {canEditMenu && (
            <Button variant="subtle" onClick={() => setMenuOpen(true)}>
              <UtensilsCrossed size={14} /> Menu
            </Button>
          )}
          {canBill && (
            <Button onClick={() => { setManualOpen(true); setManualQuery(""); }} disabled={busy === "manual"}>
              {busy === "manual" ? <Spinner size="sm" /> : <><UserPlus size={14} /> New Bill</>}
            </Button>
          )}
        </div>
      </div>

      {/* Summary */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="p-5">
          <p className="text-xs uppercase tracking-wider text-muted">Total Spent</p>
          <p className="mt-2 text-3xl font-bold font-mono text-th">{rs(stats?.total_spent ?? 0)}</p>
          <p className="mt-1 text-xs text-faint">all-time mess billing</p>
        </Card>
        <Card className="p-5">
          <p className="text-xs uppercase tracking-wider text-muted">Employees Purchased</p>
          <p className="mt-2 text-3xl font-bold font-mono text-online-text">{stats?.employees ?? 0}</p>
          <p className="mt-1 text-xs text-faint">used mess till today</p>
        </Card>
        <Card className="p-5">
          <p className="text-xs uppercase tracking-wider text-muted">Open Bills</p>
          <p className={cx("mt-2 text-3xl font-bold font-mono", openSessions.length ? "text-idle-text" : "text-faint")}>{openSessions.length}</p>
          <p className="mt-1 text-xs text-faint">awaiting close-out</p>
        </Card>
        <Card className="p-5">
          <p className="text-xs uppercase tracking-wider text-muted">Employees on Mess</p>
          <p className="mt-2 text-3xl font-bold font-mono text-accent-text">{budgetData?.employees?.length ?? 0}</p>
          <p className="mt-1 text-xs text-faint">used budget this period</p>
        </Card>
      </div>

      {/* Mess cameras (cameras with the mess module assigned) */}
      <div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Camera size={16} className="text-accent" />
          <h2 className="text-sm font-semibold text-th">Canteen Cameras</h2>
          <Badge tone="blue">{messCameras.length} camera{messCameras.length === 1 ? "" : "s"}</Badge>
          <span className="text-xs text-faint">Live feeds · bills auto-open when a recognized employee is seen here</span>
        </div>
        {messCameras.length === 0 ? (
          <Card className="py-12 text-center">
            <Camera size={36} className="mx-auto text-border mb-3" />
            <p className="text-muted text-sm">No mess cameras yet.</p>
            <p className="text-faint text-xs mt-1">Assign the <span className="font-semibold text-accent-text">Mess Management</span> module to a camera in Devices → IP Cameras (or NVR channels) and it will appear here.</p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {messCameras.map((cam) => <MessCamTile key={cam.id} cam={cam} />)}
          </div>
        )}
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
          {/* Open bills */}
          <Card className="p-0 overflow-hidden">
            <div className="flex items-center gap-2 border-b border-border bg-base px-5 py-4">
              <CircleDot size={15} className={openSessions.length ? "text-idle" : "text-faint"} />
              <h2 className="text-sm font-semibold text-th">Open Bills</h2>
              <span className="ml-auto rounded-full bg-idle/15 px-2 py-0.5 font-mono text-[10px] font-bold text-idle-text">{openSessions.length}</span>
            </div>
            <div className="p-4 space-y-3">
              {openSessions.length === 0 ? (
                <p className="py-8 text-center text-sm text-faint">No open bills. Bills open automatically when a recognized employee is seen at a canteen camera.</p>
              ) : openSessions.map((s) => (
                <div key={s.id} className="flex items-center gap-3 rounded-lg border border-idle/25 bg-idle/5 p-3">
                  {s.employee?.photo
                    ? <img src={s.employee.photo} alt={s.employee.name} className="h-10 w-10 shrink-0 rounded-full border border-border object-cover" />
                    : <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-idle/15 text-idle-text font-bold">{s.employee?.name?.slice(0, 1) ?? "?"}</div>}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-tx">{s.employee?.name ?? "Unknown"}</p>
                    <div className="flex items-center gap-1.5 text-[10px] text-faint font-mono">
                      <Clock size={9} />{fmtTime(s.started_at)} · <span className="text-idle-text font-bold">{rs(s.total)}</span>
                    </div>
                  </div>
                  <Button size="sm" onClick={() => openBill(s)}>Bill</Button>
                </div>
              ))}
            </div>
          </Card>

          {/* 15-day usage */}
          <Card className="p-0 overflow-hidden">
            <div className="flex items-center gap-2 border-b border-border bg-base px-5 py-4">
              <Wallet size={15} className="text-accent" />
              <h2 className="text-sm font-semibold text-th">15-Day Usage</h2>
              <span className="ml-auto font-mono text-[10px] text-faint">{budgetData?.label ?? pLabel(period)}</span>
            </div>
            <div className="p-4 space-y-3">
              {(budgetData?.employees ?? []).length === 0 ? (
                <p className="py-8 text-center text-sm text-faint">No mess usage recorded in this period.</p>
              ) : (budgetData!.employees).slice(0, 12).map((e) => (
                <button key={e.id} onClick={() => viewEmployeeMonth(e.id)} className="block w-full text-left rounded-lg border border-border bg-base p-3 transition-colors hover:border-accent/40 hover:bg-white/5 cursor-pointer">
                  <div className="flex items-center gap-3">
                    {e.photo
                      ? <img src={e.photo} alt={e.name} className="h-9 w-9 shrink-0 rounded-full border border-border object-cover" />
                      : <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent-text text-xs font-bold">{e.name.slice(0, 1)}</div>}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <p className="truncate text-sm font-medium text-tx">{e.name}</p>
                        <p className="shrink-0 font-mono text-[11px] text-muted">{rs(e.used)}</p>
                      </div>
                      <p className="mt-1 text-[10px] text-faint">spent this period · view schedule</p>
                    </div>
                  </div>
                </button>
              ))}
              {(budgetData?.employees ?? []).length > 12 && (
                <p className="text-center text-[11px] text-faint">+{(budgetData?.employees ?? []).length - 12} more — clicking any row shows the 15-day schedule</p>
              )}
            </div>
          </Card>
        </div>

      {/* Billing modal */}
      <Modal open={!!activeBill} onClose={dismissBill} title="Mess Bill" size="2xl">
        {activeBill && (
          <div className="space-y-5">
            <div className="flex items-center gap-4 rounded-lg border border-border bg-base p-4">
              {activeBill.employee?.photo
                ? <img src={activeBill.employee.photo} alt={activeBill.employee.name} className="h-14 w-14 rounded-full border-2 border-border object-cover" />
                : <div className="flex h-14 w-14 items-center justify-center rounded-full bg-accent/15 text-accent-text font-bold">{activeBill.employee?.name?.slice(0, 1) ?? "?"}</div>}
              <div className="min-w-0">
                <p className="text-base font-semibold text-th">{activeBill.employee?.name ?? "Unknown"}</p>
                <p className="font-mono text-xs text-faint">{activeBill.employee?.employee_id ?? ""}</p>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-faint">
                  <Clock size={11} /> opened {fmtTime(activeBill.started_at)}
                  <Badge tone="warning">OPEN</Badge>
                </div>
              </div>
              <p className="ml-auto shrink-0 text-2xl font-bold font-mono text-th">{rs(activeBill.total)}</p>
            </div>

            {/* Quick-add menu — the subsidized items (full meal, chai, drinks…) */}
            <div>
              <p className="mb-2 text-xs font-bold uppercase tracking-wider text-muted">Menu — tap to add to this bill</p>
              <div className="space-y-4">
                {menuByCategory.length === 0 ? (
                  <p className="rounded-md border border-dashed border-border py-6 text-center text-sm text-faint">No menu items yet.</p>
                ) : menuByCategory.map(([cat, items]) => (
                  <div key={cat}>
                    <p className="mb-1.5 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-faint">
                      {cap(cat)} <span className="h-px flex-1 bg-border" />
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {items.map((item) => (
                        <button key={item.id} disabled={busy === `add-${item.id}`} onClick={() => addItem(activeBill.id, item.id)}
                          className="flex items-center justify-between gap-2 rounded-md border border-border bg-base px-3 py-2.5 text-left transition-colors hover:border-accent/40 disabled:opacity-50">
                          <div className="min-w-0">
                            <p className="truncate text-sm text-tx">{item.name}</p>
                            <p className="font-mono text-[11px] text-accent-text">{rs(item.price)}</p>
                          </div>
                          <span className="shrink-0 rounded bg-accent p-1 text-white"><Plus size={13} /></span>
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Lines */}
            <div>
              <p className="mb-2 text-xs font-bold uppercase tracking-wider text-muted">Bill items</p>
              {activeBill.items.length === 0 ? (
                <p className="rounded-md border border-dashed border-border py-8 text-center text-sm text-faint">No items yet — tap a menu item to add.</p>
              ) : (
                <div className="overflow-hidden rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-elevated text-left text-xs font-semibold uppercase tracking-wider text-muted">
                        <th className="px-4 py-2.5">Item</th><th className="px-4 py-2.5 text-center">Qty</th>
                        <th className="px-4 py-2.5 text-right">Amount</th><th className="px-2 py-2.5" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-elevated">
                      {activeBill.items.map((li) => (
                        <tr key={li.id}>
                          <td className="px-4 py-2.5">
                            <p className="font-medium text-tx">{li.name}</p>
                            <p className="text-[11px] text-faint font-mono">@ {rs(li.price)}</p>
                          </td>
                          <td className="px-4 py-2.5 text-center">
                            <div className="inline-flex items-center gap-2">
                              {canBill && (
                                <button disabled={busy === `add-${li.menu_item_id}`} onClick={() => addItem(activeBill.id, li.menu_item_id)}
                                  className="rounded bg-base p-1 text-muted hover:text-tx disabled:opacity-50"><Plus size={12} /></button>
                              )}
                              <span className="font-mono font-semibold text-tx w-6 text-center">{li.qty}</span>
                              {canBill && (
                                <button disabled={busy === `add-${li.menu_item_id}`} onClick={() => addItem(activeBill.id, li.menu_item_id, -1)}
                                  className="rounded bg-base p-1 text-dnd-text/70 hover:text-dnd-text disabled:opacity-50"><Minus size={12} /></button>
                              )}
                            </div>
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono text-tx">{rs(li.amount)}</td>
                          <td className="px-2 py-2.5 text-right">
                            {canBill && (
                              <button disabled={busy === `rm-${li.id}`} onClick={() => removeItem(activeBill.id, li.id)} className="p-1 text-faint hover:text-dnd-text disabled:opacity-50" title="Remove line">
                                <Trash2 size={13} />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr className="border-t border-border bg-elevated">
                        <td colSpan={2} className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-muted">Total</td>
                        <td colSpan={2} className="px-4 py-3 text-right font-mono text-base font-bold text-th">{rs(activeBill.total)}</td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </div>

            {canBill && (
              <div className="flex justify-end gap-3 border-t border-border pt-4">
                <Button variant="ghost" onClick={dismissBill}>Keep Open</Button>
                <Button variant="primary" disabled={busy === `close-${activeBill.id}`} onClick={() => closeBill(activeBill.id)}>
                  {busy === `close-${activeBill.id}` ? <Spinner size="sm" /> : <><Receipt size={14} /> Close Bill</>}
                </Button>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Receipt modal */}
      <Modal open={!!receipt} onClose={() => setReceipt(null)} title="Receipt">
        {receipt && (
          <div className="mx-auto max-w-sm overflow-hidden rounded-xl bg-base shadow-xl">
            {/* Perforated top edge */}
            <div className="flex justify-between px-4 pt-2.5">
              {Array.from({ length: 16 }).map((_, i) => (
                <span key={i} className="h-2.5 w-2.5 rounded-full border border-border bg-surface" />
              ))}
            </div>

            <div className="px-6 pb-6 pt-2 font-mono">
              {/* Header */}
              <div className="text-center">
                <div className="mx-auto mb-2.5 flex h-11 w-11 items-center justify-center rounded-full bg-accent/15 text-accent-text">
                  <UtensilsCrossed size={18} />
                </div>
                <p className="text-lg font-bold tracking-[0.3em] text-th">VIGIL MESS</p>
                <p className="text-[10px] uppercase tracking-widest text-faint">Workplace Intelligence · Canteen Bill</p>
              </div>

              <div className="my-4 border-t border-dashed border-border" />

              {/* Meta */}
              <div className="space-y-1.5 text-[11px] text-muted">
                <div className="flex justify-between"><span>Receipt No.</span><span className="font-semibold text-tx">#{receipt.id}</span></div>
                <div className="flex justify-between"><span>Date</span><span className="text-tx">{receipt.closed_at ? new Date(receipt.closed_at).toLocaleString("en-PK", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}</span></div>
                <div className="flex justify-between"><span>Employee</span><span className="text-tx">{receipt.employee?.name ?? "—"}</span></div>
                <div className="flex justify-between"><span>ID</span><span className="text-tx">{receipt.employee?.employee_id ?? "—"}</span></div>
              </div>

              <div className="my-4 border-t border-dashed border-border" />

              {/* Items */}
              {receipt.items.length === 0 ? (
                <p className="py-4 text-center text-xs text-faint">No items on this bill</p>
              ) : (
                <div className="space-y-2.5">
                  {receipt.items.map((li, i) => (
                    <div key={i} className="text-xs">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="text-tx">{li.name}</span>
                        <span className="ml-auto shrink-0 font-medium text-tx">{rs(li.amount)}</span>
                      </div>
                      <p className="mt-0.5 text-[10px] text-faint">{li.qty} × {rs(li.price)}</p>
                    </div>
                  ))}
                  <div className="my-3 flex items-baseline justify-between border-t border-dashed border-border pt-3">
                    <span className="text-[11px] uppercase tracking-wider text-muted">
                      Total · {receipt.items.reduce((n, i) => n + i.qty, 0)} item{receipt.items.reduce((n, i) => n + i.qty, 0) === 1 ? "" : "s"}
                    </span>
                    <span className="text-base font-bold text-th">{rs(receipt.total)}</span>
                  </div>
                </div>
              )}

              <div className="my-4 border-t border-dashed border-border" />

              {/* Budget */}
              <div className="flex justify-between text-[11px] text-faint">
                <span>Spent · {receipt.budget.label}</span>
                <span className="font-semibold text-online-text">{rs(receipt.budget.used)}</span>
              </div>

              <div className="my-4 border-t border-dashed border-border" />

              {/* Footer */}
              <div className="text-center">
                <p className="text-[10px] font-semibold tracking-[0.45em] text-faint">THANK YOU</p>
                <p className="mt-1.5 text-[9px] text-faint/60">Vigil Workplace Intelligence Platform</p>
              </div>
            </div>

            {/* Perforated bottom edge */}
            <div className="flex justify-between px-4 pb-2.5">
              {Array.from({ length: 16 }).map((_, i) => (
                <span key={i} className="h-2.5 w-2.5 rounded-full border border-border bg-surface" />
              ))}
            </div>

            <div className="flex justify-center border-t border-border bg-surface py-3">
              <Button size="sm" variant="ghost" onClick={() => setReceipt(null)}>Done</Button>
            </div>
          </div>
        )}
      </Modal>

      {/* Manual open */}
      <Modal open={manualOpen} onClose={() => { setManualOpen(false); setManualEmp(""); setManualQuery(""); }} title="Open New Bill">
        <div className="space-y-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Employee</span>
            <div className="relative">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
              <Input value={manualQuery} onChange={(e) => setManualQuery(e.target.value)}
                placeholder="Search by name or employee ID…" className="pl-9" autoFocus />
            </div>
          </label>

          {manualEmp && (() => {
            const sel = (employees ?? []).find((e: any) => String(e.id) === manualEmp);
            if (!sel) return null;
            return (
              <div className="flex items-center gap-3 rounded-lg border border-accent/30 bg-accent/10 px-3 py-2.5">
                {sel.photo
                  ? <img src={`/media/${sel.photo}`} alt={sel.name} className="h-12 w-12 shrink-0 rounded-full border-2 border-accent/40 object-cover" />
                  : <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent/20 text-accent-text text-sm font-bold">{sel.name?.slice(0, 1)}</div>}
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-tx">{sel.name}</p>
                  <p className="font-mono text-[11px] text-faint">{sel.employee_id}</p>
                </div>
                <Check size={16} className="ml-auto shrink-0 text-accent-text" />
              </div>
            );
          })()}

          <div className="max-h-72 overflow-y-auto space-y-1 rounded-md border border-border bg-base p-1.5">
            {manualCandidates.length === 0 ? (
              <p className="py-8 text-center text-sm text-faint">No employees match “{manualQuery}”.</p>
            ) : manualCandidates.map((e: any) => {
              const selected = String(e.id) === manualEmp;
              return (
                <button key={e.id} type="button" onClick={() => setManualEmp(String(e.id))}
                  className={cx("flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors",
                    selected ? "bg-accent/15 ring-1 ring-accent/40" : "hover:bg-white/5")}>
                  {e.photo
                    ? <img src={`/media/${e.photo}`} alt={e.name} className="h-9 w-9 shrink-0 rounded-full border border-border object-cover" />
                    : <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent-text text-xs font-bold">{e.name?.slice(0, 1)}</div>}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-tx">{e.name}</p>
                    <p className="font-mono text-[11px] text-faint">{e.employee_id}</p>
                  </div>
                  {selected && <Check size={15} className="shrink-0 text-accent-text" />}
                </button>
              );
            })}
          </div>
          <p className="text-[11px] text-faint">{(employees ?? []).length} employees · type to filter</p>

          <div className="flex justify-end gap-3">
            <Button variant="ghost" onClick={() => setManualOpen(false)}>Cancel</Button>
            <Button onClick={openManual} disabled={busy === "manual" || !manualEmp}>
              {busy === "manual" ? <Spinner size="sm" /> : "Open Bill"}
            </Button>
          </div>
        </div>
      </Modal>

      {/* 15-day schedule */}
      <Modal open={!!empMonth} onClose={() => setEmpMonth(null)} title="15-Day Schedule" size="xl">
        {empMonth && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-4 rounded-lg border border-border bg-base p-4">
              {empMonth.employee.photo
                ? <img src={empMonth.employee.photo} alt={empMonth.employee.name} className="h-12 w-12 rounded-full border-2 border-border object-cover" />
                : <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/15 text-accent-text text-lg font-bold">
                    {empMonth.employee.name.slice(0, 1)}
                  </div>}
              <div>
                <p className="text-base font-semibold text-th">{empMonth.employee.name}</p>
                <p className="font-mono text-xs text-faint">{empMonth.employee.employee_id} · {empMonth.label}</p>
              </div>
              <div className="ml-auto text-right">
                <p className="font-mono text-xl font-bold text-th">{rs(empMonth.used)}</p>
                <p className="text-[11px] text-faint">spent in {empMonth.label}</p>
              </div>
            </div>

            {empMonth.days.length === 0 ? (
              <p className="py-10 text-center text-sm text-faint"><CalendarDays size={28} className="mx-auto mb-3 text-border" />No mess usage in {empMonth.label}.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-elevated text-left text-xs font-semibold uppercase tracking-wider text-muted">
                      <th className="px-4 py-3">Date</th><th className="px-4 py-3">Items</th><th className="px-4 py-3 text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-elevated">
                    {empMonth.days.map((d, i) => (
                      <tr key={i}>
                        <td className="px-4 py-3 font-mono text-muted whitespace-nowrap">{d.date ? new Date(d.date).toLocaleDateString("en-PK", { day: "2-digit", month: "short" }) : "—"}</td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-1">
                            {d.items.map((it, j) => (
                              <span key={j} className="rounded bg-base px-1.5 py-0.5 text-[11px] text-muted">{it.name} ×{it.qty}</span>
                            ))}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right font-mono font-semibold text-tx">{rs(d.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Menu management (admin/supervisor) */}
      <Modal open={menuOpen} onClose={() => { setMenuOpen(false); setEditing(null); resetForm(); }} title="Menu Items" size="xl">
        <div className="space-y-5">
          {/* Add / edit form */}
          <div className="rounded-lg border border-border bg-base p-4">
            <p className="mb-3 text-xs font-bold uppercase tracking-wider text-muted">
              {editing ? `Edit — ${editing.name}` : "Add a menu item"}
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <Input label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Chicken Karahi + Roti" />
              <Input label="Price (PKR)" type="number" step="0.01" min="0" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} placeholder="250.00" />
              <Input label="Category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="meal, breakfast, chai, drinks…" />
            </div>
            <div className="mt-4 flex justify-end gap-3">
              {editing && (
                <Button variant="ghost" onClick={() => { setEditing(null); resetForm(); }}>Cancel Edit</Button>
              )}
              <Button onClick={saveMenuItem} disabled={busy === (editing ? `edit-${editing.id}` : "add-menu")}>
                {busy === (editing ? `edit-${editing.id}` : "add-menu")
                  ? <Spinner size="sm" />
                  : editing
                    ? <><Pencil size={14} /> Save Changes</>
                    : <><Plus size={14} /> Add Item</>}
              </Button>
            </div>
          </div>

          {/* Existing items */}
          <div>
            <p className="mb-2 text-xs font-bold uppercase tracking-wider text-muted">
              Current menu <span className="ml-1 font-mono normal-case text-faint">({menu?.length ?? 0} items)</span>
            </p>
            {menuGroups.length === 0 ? (
              <p className="rounded-md border border-dashed border-border py-8 text-center text-sm text-faint">
                No menu items yet — add the first one above.
              </p>
            ) : (
              <div className="space-y-4">
                {menuGroups.map(([cat, items]) => (
                  <div key={cat}>
                    <p className="mb-1.5 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-faint">
                      {cap(cat)} <span className="h-px flex-1 bg-border" />
                    </p>
                    <div className="space-y-2">
                      {items.map((item) => (
                        <div key={item.id} className="flex items-center gap-3 rounded-md border border-border bg-base px-3 py-2.5">
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-tx">{item.name}</p>
                            <p className="font-mono text-[11px] text-muted">{rs(item.price)}</p>
                          </div>
                          <Badge tone={item.is_active ? "success" : "neutral"}>{item.is_active ? "Active" : "Hidden"}</Badge>
                          <button onClick={() => toggleMenuItem(item)} disabled={busy === `tgl-${item.id}`}
                            className="p-1.5 text-muted transition-colors hover:text-accent-text disabled:opacity-50"
                            title={item.is_active ? "Hide from billing" : "Show in billing"}>
                            {item.is_active ? <EyeOff size={14} /> : <Eye size={14} />}
                          </button>
                          <button onClick={() => startEdit(item)} disabled={busy === `edit-${item.id}`}
                            className="p-1.5 text-muted transition-colors hover:text-accent-text disabled:opacity-50" title="Edit">
                            <Pencil size={14} />
                          </button>
                          <button onClick={() => deleteMenuItem(item)} disabled={busy === `del-${item.id}`}
                            className="p-1.5 text-muted transition-colors hover:text-dnd-text disabled:opacity-50" title="Delete">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </Modal>

      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}
    </div>
  );
}
