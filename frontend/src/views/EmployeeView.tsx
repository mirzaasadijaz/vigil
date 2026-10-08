import { useMemo, useState } from "react";
import {
  Users2, Search, Mail, Phone, ChevronLeft, ChevronRight, UserCheck, UserMinus, UserX, RefreshCw, Layers,
} from "lucide-react";
import { Badge, Button, Card, Spinner, cx } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { apiFetch } from "../lib/api";

interface HrEmployee {
  id: string | number;
  name: string;
  employee_id?: string;
  status?: string;
  picture_url?: string | null;
  designation?: string | null;
  department?: string | null;
  email?: string | null;
  phone?: string | null;
}

interface HrPage {
  data: HrEmployee[];
  meta?: { current_page?: number; last_page?: number; total?: number };
}

type StatusFilter = "all" | "active" | "on_leave" | "inactive";

const STATUS_META: Record<string, { label: string; tone: "success" | "warning" | "neutral"; dot: string }> = {
  active:   { label: "Active",   tone: "success", dot: "bg-online" },
  on_leave: { label: "On Leave", tone: "warning", dot: "bg-idle" },
  inactive: { label: "Inactive", tone: "neutral", dot: "bg-faint" },
};

function normStatus(status?: string): string {
  return (status || "active").toLowerCase().replace(/[\s-]+/g, "_");
}

function Avatar({ src, name, size = 52 }: { src?: string | null; name: string; size?: number }) {
  const [err, setErr] = useState(false);
  if (src && !err) {
    return <img src={src} alt={name} style={{ width: size, height: size }}
      className="rounded-full object-cover border-2 border-border" onError={() => setErr(true)} />;
  }
  const initials = name.split(" ").slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return (
    <div style={{ width: size, height: size }} className="flex items-center justify-center rounded-full border-2 border-border bg-gradient-to-br from-accent-hover to-accent text-white font-bold shrink-0">
      <span style={{ fontSize: size * 0.34 }}>{initials}</span>
    </div>
  );
}

function pageNumbers(current: number, last: number): (number | "…")[] {
  if (last <= 7) return Array.from({ length: last }, (_, i) => i + 1);
  const set = new Set<number>([1, last, current - 1, current, current + 1]);
  const nums = [...set].filter((n) => n >= 1 && n <= last).sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  let prev = 0;
  for (const n of nums) {
    if (prev && n - prev > 1) out.push("…");
    out.push(n);
    prev = n;
  }
  return out;
}

export default function EmployeeView() {
  const [page, setPage] = useState(1);
  const { data, loading, refetch } = useApi<HrPage>(`/employees/hr?page=${page}`);
  const [query, setQuery] = useState("");
  const [stat, setStat] = useState<StatusFilter>("all");
  const [syncing, setSyncing] = useState(false);

  const doSync = async () => {
    setSyncing(true);
    try {
      const res = await apiFetch("/employees/sync", { method: "POST" });
      if (res.ok) await refetch();
    } finally {
      setSyncing(false);
    }
  };

  const list = data?.data ?? [];
  const meta = data?.meta;
  const currentPage = meta?.current_page ?? page;
  const lastPage = meta?.last_page ?? 1;
  const total = meta?.total ?? 0;

  const counts = useMemo(() => {
    const active = list.filter((e) => normStatus(e.status) === "active").length;
    const onLeave = list.filter((e) => normStatus(e.status) === "on_leave").length;
    return { active, onLeave };
  }, [list]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return list.filter((e) => {
      if (stat !== "all" && normStatus(e.status) !== stat) return false;
      if (q && !`${e.name} ${e.employee_id ?? ""} ${e.designation ?? ""} ${e.email ?? ""}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [list, query, stat]);

  const filters: { id: StatusFilter; label: string; icon: React.ReactNode }[] = [
    { id: "all", label: "All", icon: <Users2 size={13} /> },
    { id: "active", label: "Active", icon: <UserCheck size={13} /> },
    { id: "on_leave", label: "On Leave", icon: <UserMinus size={13} /> },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th flex items-center gap-2"><Users2 size={20} className="text-accent" /> Employees</h1>
          <p className="mt-1 text-sm text-muted">Synced from HR — directory &amp; face-recognition enrollment</p>
        </div>
        <Button variant="subtle" size="sm" onClick={doSync} disabled={syncing}>
          {syncing ? <><Spinner size="sm" /> Syncing…</> : <><RefreshCw size={14} /> Sync from HR</>}
        </Button>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {([["Total", total, "text-th", Users2], ["Active", counts.active, "text-online", UserCheck],
           ["On Leave", counts.onLeave, "text-idle", UserMinus], ["Pages", lastPage, "text-accent", Layers]] as const).map(
          ([label, n, txt, Icon]) => (
            <Card key={label} className="flex items-center gap-3 p-4">
              <span className="flex h-9 w-9 items-center justify-center rounded-md bg-base text-muted"><Icon size={16} /></span>
              <div><p className={cx("text-2xl font-bold font-mono", txt)}>{n}</p><p className="text-[11px] text-faint uppercase tracking-wider">{label}</p></div>
            </Card>
          ))}
      </div>

      {/* toolbar */}
      <Card className="p-0 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-base px-5 py-4">
          <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-1">
            {filters.map((f) => (
              <button key={f.id} onClick={() => setStat(f.id)}
                className={cx("flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors", stat === f.id ? "bg-accent text-white" : "text-muted hover:text-tx")}>
                {f.icon}{f.label}
              </button>
            ))}
          </div>
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search…"
              className="w-52 rounded-md border border-surface bg-surface py-2 pl-9 pr-3 text-sm text-tx placeholder:text-faint outline-none focus:border-accent focus:ring-2 focus:ring-accent/20" />
          </div>
        </div>

        <div className="p-5">
          {loading && list.length === 0 ? (
            <div className="flex justify-center py-16"><Spinner /></div>
          ) : filtered.length === 0 ? (
            <div className="py-16 text-center">
              <UserX size={40} className="mx-auto text-border mb-3" />
              <p className="text-muted text-sm">{list.length === 0 ? "No employees found in HR." : "No employees match your filters."}</p>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
              {filtered.map((e) => {
                const key = String(e.employee_id ?? e.id);
                const sm = STATUS_META[normStatus(e.status)] ?? STATUS_META.active;
                return (
                  <div key={key} className="group rounded-xl border border-border bg-surface overflow-hidden transition-all hover:-translate-y-1 hover:shadow-lg hover:border-accent/40">
                    <div className="flex items-center gap-3.5 px-5 pt-4 pb-3">
                      <div className="relative">
                        <div className="rounded-full ring-2 ring-accent/20 ring-offset-2 ring-offset-surface">
                          <Avatar src={e.picture_url} name={e.name} size={52} />
                        </div>
                        <span className={cx("absolute bottom-0 right-0 h-3 w-3 rounded-full ring-2 ring-surface", sm.dot)} title={sm.label} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="font-bold text-th truncate leading-tight">{e.name}</p>
                        {e.designation && <p className="text-xs text-muted truncate mt-0.5">{e.designation}</p>}
                        <p className="text-[10px] text-faint font-mono mt-0.5 tracking-wide">{key}</p>
                      </div>
                    </div>
                    <div className="mt-3 px-5 flex flex-wrap items-center gap-1.5">
                      {e.department && <Badge tone="blue">{e.department}</Badge>}
                      <Badge tone={sm.tone}>{sm.label}</Badge>
                    </div>
                    <div className="mt-3 px-5 border-t border-border/60 pt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted">
                      {e.email && <p className="flex items-center gap-1 truncate"><Mail size={10} className="text-faint shrink-0" /> {e.email}</p>}
                      {e.phone && <p className="flex items-center gap-1"><Phone size={10} className="text-faint shrink-0" /> {e.phone}</p>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* pagination */}
        {lastPage > 1 && (
          <div className="flex items-center justify-between gap-3 border-t border-border bg-base px-5 py-3">
            <p className="text-[11px] text-faint">Page {currentPage} of {lastPage}</p>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={currentPage <= 1}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted transition-colors hover:bg-surface disabled:opacity-40 disabled:hover:bg-transparent">
                <ChevronLeft size={14} />
              </button>
              {pageNumbers(currentPage, lastPage).map((n, i) =>
                n === "…" ? (
                  <span key={`e${i}`} className="px-1 text-faint text-xs">…</span>
                ) : (
                  <button key={n} onClick={() => setPage(n)}
                    className={cx("h-8 w-8 rounded-md border text-xs font-medium transition-colors",
                      n === currentPage ? "border-accent bg-accent text-white" : "border-border text-muted hover:bg-surface")}>
                    {n}
                  </button>
                )
              )}
              <button onClick={() => setPage((p) => Math.min(lastPage, p + 1))} disabled={currentPage >= lastPage}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted transition-colors hover:bg-surface disabled:opacity-40 disabled:hover:bg-transparent">
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
