import { useMemo, useState } from "react";
import { LayoutGrid, List, Search, Users2, UserCheck, UserX, Download } from "lucide-react";
import { Badge, Button, Card, Spinner, cx } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { Avatar, AttendanceCard, fmtTime, fmtDur, workTimes, statusTone } from "../components/domain/attendance";
import { EmployeeDetail } from "../components/domain/EmployeeDetail";
import { apiFetch } from "../lib/api";

type ViewMode = "card" | "list";
type AttFilter = "all" | "present" | "absent";

function SummaryChip({ label, value, dot }: { label: string; value: number; dot: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-base px-3 py-1.5">
      <span className={cx("h-2 w-2 rounded-full", dot)} />
      <span className="text-sm font-bold font-mono text-th">{value}</span>
      <span className="text-[11px] text-faint">{label}</span>
    </div>
  );
}

export default function AttendanceView() {
  const [date, setDate] = useState(new Date().toISOString().split("T")[0]);
  const [mode, setMode] = useState<ViewMode>("card");
  const [filter, setFilter] = useState<AttFilter>("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<{ emp: any; att: any } | null>(null);
  const [rangeStart, setRangeStart] = useState(date);
  const [rangeEnd, setRangeEnd] = useState(date);
  const [exporting, setExporting] = useState(false);

  const { data: employees, refetch: refetchEmps } = useApi<any[]>("/employees/");
  const { data: attendance, refetch: refetchAtt } = useApi<any[]>(`/attendance/?date=${date}`);
  const { data: idleEvents, refetch: refetchIdle } = useApi<any[]>("/alerts/?module=idle&limit=1000");
  usePolling(() => { refetchAtt(); refetchEmps(); refetchIdle(); }, 10000);

  const empList = employees ?? [];
  const attList = attendance ?? [];

  const attMap = useMemo(() => {
    const m = new Map<string, any>();
    for (const a of attList) if (a.employee) m.set(a.employee.employee_id, a);
    return m;
  }, [attList]);

  // idle seconds per employee (DB id) for the selected day (timestamps carry local tz)
  const idleByEmp = useMemo(() => {
    const m = new Map<number, number>();
    for (const e of (idleEvents ?? [])) {
      if (e.employee_id == null || (e.timestamp || "").slice(0, 10) !== date) continue;
      m.set(e.employee_id, (m.get(e.employee_id) ?? 0) + (e.value || 0));
    }
    return m;
  }, [idleEvents, date]);

  const present = attList.length;
  const absent = Math.max(0, empList.length - present);
  const rate = empList.length ? Math.round((present / empList.length) * 100) : 0;

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return empList
      .map((emp: any) => ({ emp, att: attMap.get(emp.employee_id) ?? null }))
      .filter(({ emp, att }) => {
        if (filter === "present" && !att) return false;
        if (filter === "absent" && att) return false;
        if (q && !`${emp.name} ${emp.employee_id}`.toLowerCase().includes(q)) return false;
        return true;
      });
  }, [empList, attMap, filter, query]);

  const filters: { id: AttFilter; label: string; icon: React.ReactNode }[] = [
    { id: "all", label: `All ${empList.length}`, icon: <Users2 size={13} /> },
    { id: "present", label: `Present ${present}`, icon: <UserCheck size={13} /> },
    { id: "absent", label: `Absent ${absent}`, icon: <UserX size={13} /> },
  ];

  // Export attendance across a date range (with working / productive / idle per day)
  const exportRange = async () => {
    if (rangeStart > rangeEnd) { return; }
    setExporting(true);
    try {
      const recs = await (await apiFetch(`/attendance/?start=${rangeStart}&end=${rangeEnd}`)).json();
      const idle = await (await apiFetch(`/alerts/?module=idle&limit=5000`)).json().catch(() => []);
      const idleMap = new Map<string, number>();   // `${code}|${day}` -> seconds
      for (const ev of idle) {
        const code = ev.employee_id != null ? String(ev.employee_id) : null;
        const day = (ev.timestamp || "").slice(0, 10);
        if (!code || !day) continue;
        const k = `${code}|${day}`;
        idleMap.set(k, (idleMap.get(k) ?? 0) + (ev.value || 0));
      }
      const head = ["Employee", "ID", "Date", "Status", "Check In", "Check Out", "Working", "Productive", "Idle"];
      const lines = (recs as any[]).map((r) => {
        const code = r.employee?.employee_id ?? "";
        const idleS = idleMap.get(`${code}|${r.date}`) ?? 0;
        const wt = workTimes(r, idleS);
        return [r.employee?.name ?? "", code, r.date, r.status ?? "",
          r.check_in ? fmtTime(r.check_in) : "", r.check_out ? fmtTime(r.check_out) : "",
          fmtDur(wt.working), fmtDur(wt.productive), fmtDur(wt.idle)].map((v) => `"${v}"`).join(",");
      });
      const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `attendance-${rangeStart}_to_${rangeEnd}.csv`; a.click();
    } finally {
      setExporting(false);
    }
  };

  if (selected) {
    return <EmployeeDetail emp={selected.emp} att={selected.att} date={date} onBack={() => setSelected(null)} />;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th">Attendance</h1>
          <p className="mt-1 text-sm text-muted">Face-recognition attendance · {new Date(date + "T00:00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })}</p>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-faint">View date</span>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
              className="rounded-md border border-base bg-base px-3 py-2 text-sm text-tx outline-none focus:border-accent focus:ring-2 focus:ring-accent/20" />
          </label>
          <div className="flex items-end gap-2 rounded-md border border-border bg-base/40 p-2">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-faint">From</span>
              <input type="date" value={rangeStart} max={rangeEnd} onChange={(e) => setRangeStart(e.target.value)}
                className="rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-faint">To</span>
              <input type="date" value={rangeEnd} min={rangeStart} onChange={(e) => setRangeEnd(e.target.value)}
                className="rounded-md border border-base bg-base px-2 py-1.5 text-sm text-tx outline-none focus:border-accent" />
            </label>
            <Button variant="ghost" size="sm" onClick={exportRange} disabled={exporting}>
              {exporting ? <Spinner size="sm" /> : <><Download size={14} /> Export CSV</>}
            </Button>
          </div>
        </div>
      </div>

      {/* Summary */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Card interactive className="p-5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted">Attendance Rate</p>
          <p className="mt-2 text-3xl font-bold font-mono text-th">{rate}%</p>
          <div className="mt-3 h-1.5 w-full rounded-full bg-base overflow-hidden">
            <div className="h-full rounded-full bg-online transition-all duration-500" style={{ width: `${rate}%` }} />
          </div>
        </Card>
        <Card className="p-5"><p className="text-xs uppercase tracking-wider text-muted">Present</p><p className="mt-2 text-3xl font-bold font-mono text-online">{present}</p><p className="mt-1 text-xs text-faint">checked in today</p></Card>
        <Card className="p-5"><p className="text-xs uppercase tracking-wider text-muted">Absent</p><p className="mt-2 text-3xl font-bold font-mono text-faint">{absent}</p><p className="mt-1 text-xs text-faint">no check-in</p></Card>
      </div>

      {/* Toolbar + body */}
      <Card className="p-0 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-base px-5 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-1">
              {filters.map((f) => (
                <button key={f.id} onClick={() => setFilter(f.id)}
                  className={cx("flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors",
                    filter === f.id ? "bg-accent text-white" : "text-muted hover:text-tx")}>
                  {f.icon}{f.label}
                </button>
              ))}
            </div>
            <div className="hidden sm:flex items-center gap-2">
              <SummaryChip label="present" value={present} dot="bg-online" />
              <SummaryChip label="absent" value={absent} dot="bg-faint" />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search…"
                className="w-48 rounded-md border border-surface bg-surface py-2 pl-9 pr-3 text-sm text-tx placeholder:text-faint outline-none focus:border-accent focus:ring-2 focus:ring-accent/20" />
            </div>
            <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-1">
              <button onClick={() => setMode("card")} title="Card view"
                className={cx("rounded p-1.5 transition-colors", mode === "card" ? "bg-accent text-white" : "text-muted hover:text-tx")}><LayoutGrid size={15} /></button>
              <button onClick={() => setMode("list")} title="List view"
                className={cx("rounded p-1.5 transition-colors", mode === "list" ? "bg-accent text-white" : "text-muted hover:text-tx")}><List size={15} /></button>
            </div>
          </div>
        </div>

        <div className="p-5">
          {empList.length === 0 ? (
            <div className="py-16 text-center"><Users2 size={40} className="mx-auto text-border mb-4" /><p className="text-muted text-sm">No employees registered.</p></div>
          ) : rows.length === 0 ? (
            <div className="py-16 text-center"><Search size={32} className="mx-auto text-border mb-3" /><p className="text-muted text-sm">No records match this filter.</p></div>
          ) : mode === "card" ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
              {rows.map(({ emp, att }) => (
                <button key={emp.employee_id} onClick={() => setSelected({ emp, att })} className="text-left cursor-pointer" title="View employee detail">
                  <AttendanceCard emp={emp} att={att} idleSeconds={idleByEmp.get(emp.id) ?? 0} />
                </button>
              ))}
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-elevated text-left text-xs font-semibold uppercase tracking-wider text-muted">
                    <th className="px-4 py-3">Employee</th><th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Check In</th><th className="px-4 py-3">Check Out</th>
                    <th className="px-4 py-3">Working</th><th className="px-4 py-3">Productive</th><th className="px-4 py-3">Idle</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-elevated">
                  {rows.map(({ emp, att }) => {
                    const wt = workTimes(att, idleByEmp.get(emp.id) ?? 0);
                    return (
                    <tr key={emp.employee_id} onClick={() => setSelected({ emp, att })} className="cursor-pointer transition-colors hover:bg-hover">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <Avatar photo={emp.photo} name={emp.name} size={36} />
                          <div><p className="font-medium text-tx">{emp.name}</p><p className="text-xs text-faint font-mono">{emp.employee_id}</p></div>
                        </div>
                      </td>
                      <td className="px-4 py-3"><Badge tone={att ? statusTone(att.status) : "neutral"}>{att ? (att.status ?? "Present") : "Absent"}</Badge></td>
                      <td className="px-4 py-3 font-mono text-muted">{fmtTime(att?.check_in)}</td>
                      <td className="px-4 py-3 font-mono text-muted">{fmtTime(att?.check_out)}</td>
                      <td className="px-4 py-3 font-mono text-muted text-xs">{att?.check_in ? fmtDur(wt.working) : "—"}</td>
                      <td className="px-4 py-3 font-mono text-online-text text-xs">{att?.check_in ? fmtDur(wt.productive) : "—"}</td>
                      <td className="px-4 py-3 font-mono text-idle-text text-xs">{att?.check_in ? fmtDur(wt.idle) : "—"}</td>
                    </tr>
                  ); })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
