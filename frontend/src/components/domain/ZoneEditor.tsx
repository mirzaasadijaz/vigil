import { useRef, useState } from "react";
import { Button } from "../ui";

type Pt = [number, number];   // normalized 0-1

// Shared geometry defaults (reused by bulk + on-enable seeding).
export const fullFramePolygon = (): Pt[] => [[0.02, 0.02], [0.98, 0.02], [0.98, 0.98], [0.02, 0.98]];
export const defaultLine = (): Pt[] => [[0.1, 0.5], [0.9, 0.5]];

interface Props {
  snapshotUrl: string;            // camera snapshot to draw over
  mode: "line" | "zones";
  line?: Pt[];                    // for mode "line": [[x,y],[x,y]]
  zones?: Pt[][];                 // for mode "zones": list of polygons
  onChangeLine?: (line: Pt[]) => void;
  onChangeZones?: (zones: Pt[][]) => void;
}

export function ZoneEditor({ snapshotUrl, mode, line = [], zones = [], onChangeLine, onChangeZones }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<Pt[]>([]);   // polygon being drawn (zones mode)

  const toNorm = (e: React.MouseEvent): Pt => {
    const r = ref.current!.getBoundingClientRect();
    return [
      Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    ];
  };

  const onClick = (e: React.MouseEvent) => {
    const p = toNorm(e);
    if (mode === "line") {
      const next = line.length >= 2 ? [p] : [...line, p];
      onChangeLine?.(next);
    } else {
      setDraft((d) => [...d, p]);
    }
  };

  const finishZone = () => {
    if (draft.length >= 3) onChangeZones?.([...(zones || []), draft]);
    setDraft([]);
  };

  const clearAll = () => {
    setDraft([]);
    if (mode === "line") onChangeLine?.([]);
    else onChangeZones?.([]);
  };

  const C = (n: number) => n * 100;   // normalized -> 0..100 viewBox units

  return (
    <div className="space-y-2">
      <div ref={ref} onClick={onClick}
        className="relative w-full overflow-hidden rounded-md border border-border bg-black cursor-crosshair select-none">
        <img src={snapshotUrl} alt="camera" className="w-full block opacity-90"
          onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.25"; }} />
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
          {/* counting line */}
          {mode === "line" && line.length === 2 && (
            <line x1={C(line[0][0])} y1={C(line[0][1])} x2={C(line[1][0])} y2={C(line[1][1])}
              stroke="var(--color-online)" strokeWidth={3} vectorEffect="non-scaling-stroke" />
          )}
          {mode === "line" && line.map((p, i) => (
            <circle key={i} cx={C(p[0])} cy={C(p[1])} r={1.4} fill="var(--color-online)" />
          ))}
          {/* committed zones */}
          {mode === "zones" && zones.map((z, zi) => (
            <polygon key={zi} points={z.map((p) => `${C(p[0])},${C(p[1])}`).join(" ")}
              fill="var(--color-accent)" fillOpacity={0.18} stroke="var(--color-accent)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
          ))}
          {/* draft polygon */}
          {mode === "zones" && draft.length > 0 && (
            <polyline points={draft.map((p) => `${C(p[0])},${C(p[1])}`).join(" ")}
              fill="none" stroke="var(--color-idle)" strokeWidth={2} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
          )}
          {mode === "zones" && draft.map((p, i) => (
            <circle key={i} cx={C(p[0])} cy={C(p[1])} r={1.2} fill="var(--color-idle)" />
          ))}
        </svg>
      </div>
      <div className="flex items-center gap-2">
        <p className="flex-1 text-[11px] text-faint">
          {mode === "line"
            ? "Click two points to set the counting line."
            : "Click to add zone points, then Finish Zone. Add multiple zones if needed."}
        </p>
        {mode === "zones" && <Button variant="subtle" size="sm" onClick={finishZone} disabled={draft.length < 3}>Finish Zone</Button>}
        <Button variant="subtle" size="sm" onClick={() => { setDraft([]); mode === "line" ? onChangeLine?.(defaultLine()) : onChangeZones?.([fullFramePolygon()]); }}>
          {mode === "line" ? "Center line" : "Full frame"}
        </Button>
        <Button variant="ghost" size="sm" onClick={clearAll}>Clear</Button>
      </div>
    </div>
  );
}
