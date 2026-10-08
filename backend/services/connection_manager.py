import asyncio
from contextlib import suppress
from typing import Dict
from fastapi import WebSocket


class ConnectionManager:
    """Manage websocket clients per camera.

    Each connected WebSocket gets an asyncio.Lock to serialize sends and closes
    for that connection. This avoids concurrent drain/close races in the
    websockets transport when multiple coroutines write/close the same socket.
    """

    def __init__(self):
        # cam_id -> (ws -> asyncio.Lock)
        self._connections: Dict[str, Dict[WebSocket, asyncio.Lock]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, cam_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(cam_id, {})[ws] = asyncio.Lock()

    async def disconnect(self, cam_id: str, ws: WebSocket) -> None:
        s = self._connections.get(cam_id)
        if not s:
            return
        lock = s.pop(ws, None)
        # Attempt a safe close while holding the send lock to avoid races with
        # concurrent send/drain calls from other coroutines.
        if lock is not None:
            try:
                async with lock:
                    with suppress(Exception):
                        await ws.close()
            except Exception:
                # Best-effort: if closing fails, ignore — we'll remove the ref.
                pass
        if not s:
            # remove empty camera entry
            self._connections.pop(cam_id, None)

    async def broadcast(self, cam_id: str, data: bytes) -> None:
        """Send bytes to all clients for the camera.

        Each client has its own lock so sends to different clients can proceed in
        parallel while ensuring a single writer per client.
        """
        conns = dict(self._connections.get(cam_id, {}))  # shallow copy
        dead = set()
        for ws, lock in conns.items():
            try:
                async with lock:
                    await ws.send_bytes(data)
            except Exception:
                dead.add(ws)
        # cleanup dead connections
        s = self._connections.get(cam_id)
        if s and dead:
            for d in dead:
                s.pop(d, None)

    def has_clients(self, cam_id: str) -> bool:
        return bool(self._connections.get(cam_id))

    def broadcast_sync(self, cam_id: str, data: bytes) -> None:
        """Called from sync AI thread — schedules async broadcast on the event loop."""
        if self._loop and self.has_clients(cam_id):
            asyncio.run_coroutine_threadsafe(self.broadcast(cam_id, data), self._loop)


connection_manager = ConnectionManager()
