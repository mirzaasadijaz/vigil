import { useApi } from "../../hooks/useApi";
import { Spinner } from "../ui";
import { StreamTile } from "./StreamTile";

interface Camera {
  id: number;
  name: string;
  is_active: boolean;
  process_frame_rate: number;
}

export function CameraGrid() {
  const { data: cameras, loading } = useApi<Camera[]>("/cameras/");

  if (loading) return <div className="flex justify-center py-16"><Spinner /></div>;
  if (!cameras?.length) {
    return (
      <div className="rounded-xl border border-border bg-black/20 px-4 py-12 text-center text-sm text-border">
        No cameras configured. Add cameras in the IP Cameras section.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {cameras.map((cam) => (
        <StreamTile key={cam.id} cameraId={cam.id} name={cam.name} isActive={cam.is_active} />
      ))}
    </div>
  );
}
