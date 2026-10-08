import { useEffect, useRef, useState } from "react";
import { X, ZoomIn, ZoomOut } from "lucide-react";

/** Full-screen image viewer: click to toggle zoom (1x ↔ 2.5x), drag to pan when zoomed, Esc to close. */
export function Lightbox({ src, alt = "", caption, onClose }: { src: string; alt?: string; caption?: string; onClose: () => void }) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggleZoom = () => { setZoom((z) => (z > 1 ? 1 : 2.5)); setPan({ x: 0, y: 0 }); };

  const onPointerDown = (e: React.PointerEvent) => {
    if (zoom === 1) return;
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    setPan({ x: drag.current.px + (e.clientX - drag.current.x), y: drag.current.py + (e.clientY - drag.current.y) });
  };
  const endDrag = () => { drag.current = null; };

  return (
    <div role="dialog" aria-modal="true" onClick={onClose}
      className="fixed inset-0 z-[300] flex flex-col items-center justify-center bg-black/80 backdrop-blur-sm animate-[fadeIn_0.15s_ease-out]">
      <div className="absolute right-4 top-4 flex gap-2">
        <button onClick={(e) => { e.stopPropagation(); toggleZoom(); }} aria-label={zoom > 1 ? "Zoom out" : "Zoom in"}
          className="flex h-10 w-10 items-center justify-center rounded-md border border-white/15 bg-black/50 text-white/90 hover:bg-black/70">
          {zoom > 1 ? <ZoomOut size={18} /> : <ZoomIn size={18} />}
        </button>
        <button onClick={onClose} aria-label="Close"
          className="flex h-10 w-10 items-center justify-center rounded-md border border-white/15 bg-black/50 text-white/90 hover:bg-black/70">
          <X size={18} />
        </button>
      </div>
      <img
        src={src} alt={alt}
        onClick={(e) => { e.stopPropagation(); toggleZoom(); }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerLeave={endDrag}
        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, cursor: zoom > 1 ? "grab" : "zoom-in" }}
        className="max-h-[85vh] max-w-[92vw] select-none rounded-lg object-contain shadow-2xl transition-transform duration-200"
        draggable={false}
      />
      {caption && <p className="mt-3 text-sm text-white/80" onClick={(e) => e.stopPropagation()}>{caption}</p>}
    </div>
  );
}
