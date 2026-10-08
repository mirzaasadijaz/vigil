import { Badge, Button, Spinner } from "../ui";
import { useApi } from "../../hooks/useApi";
import { usePolling } from "../../hooks/usePolling";
import { apiFetch } from "../../lib/api";
import { useState, useCallback } from "react";

interface AlertItem {
  id: number;
  violation_type: string;
  timestamp: string;
  unknown_person: boolean;
  likely_candidate: string | null;
  distance_score: number | null;
  snapshot: string | null;
  employee?: { name: string } | null;
}

function severityTone(vType: string): "danger" | "warning" | "neutral" {
  if (vType.toLowerCase().includes("no-") || vType.toLowerCase().includes("missing")) return "danger";
  if (vType.toLowerCase().includes("phone")) return "warning";
  return "neutral";
}

export function AlertFeed({ limit = 20 }: { limit?: number }) {
  const { data, loading, refetch } = useApi<AlertItem[]>(`/alerts/?limit=${limit}`);
  const [clearing, setClearing] = useState(false);

  usePolling(refetch, 5000);

  const clearAll = useCallback(async () => {
    if (!confirm("Clear all alerts?")) return;
    setClearing(true);
    try { await apiFetch("/alerts/", { method: "DELETE" }); refetch(); }
    finally { setClearing(false); }
  }, [refetch]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-th tracking-wide">LIVE ALERTS</h3>
        <Button variant="ghost" size="sm" onClick={clearAll} disabled={clearing}>
          {clearing ? <Spinner size="sm" /> : "Clear All"}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : !data?.length ? (
        <div className="rounded-xl border border-border bg-black/20 px-4 py-8 text-center text-sm text-border">No alerts</div>
      ) : (
        <div className="flex flex-col gap-2 max-h-96 overflow-y-auto no-scrollbar">
          {data.map((alert) => (
            <div key={alert.id} className="rounded-xl border border-border bg-black/20 p-3 transition-colors hover:bg-black/30">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-th truncate">{alert.violation_type}</p>
                  <p className="text-xs text-border mt-0.5">
                    {alert.employee?.name ?? (alert.unknown_person ? `Unknown${alert.likely_candidate ? ` (maybe ${alert.likely_candidate})` : ""}` : "Unidentified")}
                  </p>
                </div>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <Badge tone={severityTone(alert.violation_type)}>{severityTone(alert.violation_type)}</Badge>
                  <span className="text-[10px] text-border">{new Date(alert.timestamp).toLocaleTimeString()}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
