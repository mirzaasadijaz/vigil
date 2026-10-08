import { useEffect, useRef, useState } from "react";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "offline";

export function useCameraStream(camId: number | null) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const retryDelay = useRef(1000);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const unmounted = useRef(false);

  useEffect(() => {
    if (camId === null) return;
    unmounted.current = false;

    function connect() {
      if (unmounted.current) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${window.location.host}/api/stream/ws/${camId}`);
      wsRef.current = ws;
      ws.binaryType = "arraybuffer";
      setStatus("connecting");

      ws.onopen = () => {
        if (unmounted.current) { ws.close(); return; }
        setStatus("live");
        retryDelay.current = 1000;
      };

      ws.onmessage = (ev) => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const blob = new Blob([ev.data as ArrayBuffer], { type: "image/jpeg" });
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
          // Only resize the pixel buffer when dimensions change (avoids flash)
          if (canvas.width !== img.naturalWidth) canvas.width = img.naturalWidth;
          if (canvas.height !== img.naturalHeight) canvas.height = img.naturalHeight;
          canvas.getContext("2d")?.drawImage(img, 0, 0);
          URL.revokeObjectURL(url);
        };
        img.src = url;
      };

      ws.onclose = () => {
        if (unmounted.current) return;
        // A socket we closed because the tab went to the background must stay closed;
        // reconnecting here would defeat the point and reopen it immediately.
        if (document.hidden) { setStatus("reconnecting"); return; }
        setStatus("reconnecting");
        retryTimer.current = setTimeout(() => {
          retryDelay.current = Math.min(retryDelay.current * 2, 10000);
          connect();
        }, retryDelay.current);
      };

      ws.onerror = () => {
        setStatus("offline");
        ws.close();
      };
    }

    connect();

    // Close the socket while the tab is hidden, reopen when it comes back.
    //
    // The server only encodes a JPEG for a camera that has a connected client, so a
    // background tab was costing a full encode every 100 ms per camera for pictures
    // nobody could see. With several tabs open that is a large share of the machine
    // spent rendering to a screen that is not on screen.
    //
    // Closing also lets the camera idle-stop server-side if nothing else wants it, which
    // frees the decode thread too. Reconnection uses the same backoff as any other drop,
    // so returning to the tab behaves exactly like recovering from a brief outage.
    function onVisibility() {
      if (document.hidden) {
        clearTimeout(retryTimer.current);
        wsRef.current?.close();
      } else if (!unmounted.current
                 && wsRef.current?.readyState !== WebSocket.OPEN) {
        retryDelay.current = 1000;
        connect();
      }
    }
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      unmounted.current = true;
      document.removeEventListener("visibilitychange", onVisibility);
      clearTimeout(retryTimer.current);
      wsRef.current?.close();
    };
  }, [camId]);

  return { canvasRef, status };
}
