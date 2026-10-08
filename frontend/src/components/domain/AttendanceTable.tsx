import { Badge, Table } from "../ui";

interface AttendanceRecord {
  id: number;
  employee: { name: string; employee_id: string } | null;
  date: string;
  check_in: string | null;
  check_out: string | null;
  status: string;
}

const statusTone = (s: string): "success" | "warning" | "danger" | "neutral" => {
  if (s === "Present" || s === "Completed" || s === "On Time") return "success";
  if (s?.includes("Departure") || s?.includes("Early")) return "danger";
  return "neutral";
};

interface Props {
  data: AttendanceRecord[];
}

export function AttendanceTable({ data }: Props) {
  const columns = [
    { key: "employee", header: "Employee", render: (r: AttendanceRecord) => (
      <div>
        <p className="font-medium text-th">{r.employee?.name ?? "—"}</p>
        <p className="text-xs text-border">{r.employee?.employee_id}</p>
      </div>
    )},
    { key: "date", header: "Date", render: (r: AttendanceRecord) => <span className="text-muted">{r.date}</span> },
    { key: "check_in", header: "Check In", render: (r: AttendanceRecord) => <span className="text-muted">{r.check_in ? new Date(r.check_in).toLocaleTimeString() : "—"}</span> },
    { key: "check_out", header: "Check Out", render: (r: AttendanceRecord) => <span className="text-muted">{r.check_out ? new Date(r.check_out).toLocaleTimeString() : "—"}</span> },
    { key: "status", header: "Status", render: (r: AttendanceRecord) => <Badge tone={statusTone(r.status)}>{r.status || "—"}</Badge> },
  ];

  return <Table columns={columns} data={data} keyField="id" emptyMessage="No attendance records found" />;
}
