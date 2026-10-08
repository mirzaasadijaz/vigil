import { Badge, Card, SectionTitle, Spinner } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { accessRules } from "../data/mockData";

interface Camera {
  id: number;
  name: string;
  ip_address: string;
  channel: number;
  is_active: boolean;
}

export default function AssetsView() {
  const { data: cameras, loading } = useApi<Camera[]>("/cameras/");

  return (
    <div className="space-y-6">
      <SectionTitle title="Assets & Access" subtitle="Live device inventory and access control policies" />

      <div className="grid gap-6 xl:grid-cols-2">
        {/* Camera Devices */}
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border p-5 bg-white/5">
            <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Camera Inventory</h3>
          </div>
          {loading ? (
            <div className="flex justify-center py-8"><Spinner /></div>
          ) : (
            <div className="divide-y divide-content">
              {(cameras ?? []).length === 0 ? (
                <p className="px-5 py-8 text-center text-sm text-border">No cameras found</p>
              ) : (cameras ?? []).map((cam) => (
                <div key={cam.id} className="flex items-center justify-between px-5 py-4">
                  <div>
                    <p className="text-sm font-medium text-th">{cam.name}</p>
                    <p className="text-xs text-border font-mono">{cam.ip_address} · CH {cam.channel}</p>
                  </div>
                  <Badge tone={cam.is_active ? "success" : "neutral"}>{cam.is_active ? "Online" : "Offline"}</Badge>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Access Rules */}
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-border p-5 bg-white/5">
            <h3 className="text-sm font-semibold text-th uppercase tracking-wider">Access Policies</h3>
          </div>
          <div className="divide-y divide-content">
            {accessRules.map((rule, idx) => (
              <div key={idx} className="flex items-start gap-4 px-5 py-4">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-online/20 bg-online/10 text-online text-xs font-bold">
                  {idx + 1}
                </div>
                <div>
                  <p className="text-sm font-medium text-th">{rule.title}</p>
                  <p className="text-xs text-border mt-0.5">{rule.description}</p>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
