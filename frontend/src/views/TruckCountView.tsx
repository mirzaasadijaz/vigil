import { useMemo, useState } from "react";
import { Truck, MapPin, TrendingUp, ArrowDownToLine, ArrowUpFromLine, ScanLine } from "lucide-react";
import { Badge, Button, Card, cx } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { useCameraStream } from "../hooks/useCameraStream";

interface ModAssign { module: string; enabled: boolean }
interface Cam {
  id: number; name: string; ip_address: string; channel: number;
  is_active: boolean; location: string | null; department: string | null;
  nvr_id: number | null; nvr_name: string | null; module_config?: ModAssign[];
}
interface CountRow { camera_id: number; in: number; out: number; total: number }

export default function TruckCountView({ onNavigate }: { onNavigate?: (v: string) => void }) {
  const { data: cameras } = useApi<Cam[]>("/cameras/");
  const camList = cameras ?? [];

  const truckCams = useMemo(
    () => camList.filter((c) => (c.module_config || []).some((a) => a.module === "truck_count" && a.enabled)),
    [camList],
  );

  const [camId, setCamId] = useState<number | null>(null);
  const selected = truckCams.find((c) => c.id === camId) ?? truckCams[0] ?? null;
  const selectedId = selected?.id ?? null;

  const { canvasRef, status } = useCameraStream(selectedId);

  const { data: counts, refetch } = useApi<CountRow[]>("/cameras/counts?module=truck_count&today=true");
  usePolling(() => refetch(), 4000, !!selectedId);

  const row = (counts ?? []).find((r) => r.camera_id === selectedId);

  const stats: { label: string; value: number; tone: string; icon: React.ReactNode }[] = [
    { label: "TRUCKS IN", value: row?.in ?? 0, tone: "text-online-text", icon: <ArrowDownToLine size={18} /> },
    { label: "TRUCKS OUT", value: row?.out ?? 0, tone: "text-dnd", icon: <ArrowUpFromLine size={18} /> },
    { label: "TOTAL", value: row?.total ?? 0, tone: "text-accent-text", icon: <TrendingUp size={18} /> },
  ];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-th flex items-center gap-2"><Truck size={20} className="text-accent" /> Truck Count</h1>
          <p className="mt-1 text-sm text-muted">Main gate truck counting — draw the gate ROI line on the camera, trucks crossing it are counted.</p>
        </div>
        {selected && (
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 rounded-md border border-border bg-base px-3 py-2">
              <MapPin size={13} className="text-faint" />
              <select value={selected.id} onChange={(e) => setCamId(Number(e.target.value))}
                className="bg-transparent text-sm text-tx outline-none cursor-pointer max-w-[220px]">
                {truckCams.map((c) => <option key={c.id} value={c.id} className="bg-surface">{c.name}</option>)}
              </select>
            </label>
          </div>
        )}
      </div>

      {truckCams.length === 0 ? (
        <Card className="py-16 text-center">
          <Truck size={36} className="mx-auto text-border mb-3" />
          <p className="text-muted text-sm">No camera has the <b>Truck Count</b> module enabled.</p>
          <p className="text-faint text-xs mt-1">Enable it on the main gate camera in Devices → IP Cameras → Edit → Modules.</p>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* Live feed */}
          <Card className="overflow-hidden p-0 lg:col-span-2">
            <div className="flex flex-wrap items-center gap-2 border-b border-border bg-base px-4 py-3">
              <Badge tone={status === "live" ? "success" : "danger"}>{status === "live" ? "LIVE" : status.toUpperCase()}</Badge>
              <Badge tone="blue">Truck Count</Badge>
              <span className="ml-auto inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-online-text">
                <span className="h-1.5 w-1.5 rounded-full bg-online animate-pulse" /> Gate Camera
              </span>
            </div>
            <div className="relative aspect-video bg-black">
              <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
              {status !== "live" && (
                <div className="absolute inset-0 flex items-center justify-center text-faint text-xs font-mono">{status.toUpperCase()}…</div>
              )}
            </div>
          </Card>

          {/* Counters + ROI */}
          <div className="flex flex-col gap-4">
            <Card className="p-4">
              <h3 className="text-sm font-semibold text-th flex items-center gap-2 mb-3"><TrendingUp size={14} className="text-accent" /> Today's Totals</h3>
              <div className="space-y-3">
                {stats.map((s) => (
                  <div key={s.label} className="flex items-center justify-between rounded-lg border border-border bg-base px-4 py-3">
                    <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-faint">
                      <span className="text-muted">{s.icon}</span>{s.label}
                    </div>
                    <span className={cx("text-2xl font-bold tabular-nums", s.tone)}>{s.value}</span>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="p-4">
              <h3 className="text-sm font-semibold text-th flex items-center gap-2 mb-2"><ScanLine size={14} className="text-accent" /> ROI / Gate Line</h3>
              <p className="text-xs text-muted leading-relaxed">
                The gate line is drawn on the feed. Configure/move it in
                <b> Devices → IP Cameras → Edit → {selected?.name} → Modules → Truck Count</b>.
                If small vehicles (rickshaws) get counted, raise the{" "}
                <b>Confidence threshold</b> there.
              </p>
              <Button variant="subtle" size="sm" className="mt-3 w-full" onClick={() => onNavigate?.("ip_cameras")}>
                Open camera settings
              </Button>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
