import { useState, useEffect } from "react";
import { cx } from "../ui";

interface StreamTileProps {
  cameraId: number;
  name: string;
  isActive: boolean;
  mode?: "snapshot" | "live";
}

export function StreamTile({ cameraId, name, isActive, mode = "snapshot" }: StreamTileProps) {
  const [ts, setTs] = useState(Date.now());
  const snapshotUrl = `/api/stream/snapshot?cam_id=${cameraId}&t=${ts}`;
  const videoUrl = `/api/stream/video?cam_id=${cameraId}`;

  useEffect(() => {
    if (mode === "snapshot") {
      const id = setInterval(() => setTs(Date.now()), 1000);
      return () => clearInterval(id);
    }
  }, [mode]);

  return (
    <div className="group relative overflow-hidden rounded-xl border border-border bg-black aspect-video">
      {mode === "live" ? (
        <img src={videoUrl} alt={name} className="h-full w-full object-cover" />
      ) : (
        <img
          key={ts}
          src={snapshotUrl}
          alt={name}
          className="h-full w-full object-cover transition-opacity duration-300"
          onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.3"; }}
        />
      )}
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-3 py-2">
        <p className="text-xs font-medium text-th truncate">{name}</p>
        <div className="flex items-center gap-1.5 mt-0.5">
          <span className={cx("inline-block h-1.5 w-1.5 rounded-full", isActive ? "bg-online" : "bg-dnd")} />
          <span className="text-[10px] text-muted">{isActive ? "LIVE" : "OFFLINE"}</span>
        </div>
      </div>
    </div>
  );
}
