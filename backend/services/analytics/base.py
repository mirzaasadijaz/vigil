"""
Rules engine base — analytics are cheap stateful consumers of shared detections.

Each analytic is instantiated per (camera, module) inside the perception child and
fed primitives (person tracks / faces) every processed frame. It returns:
  - events:   [{event_type, value, data, ...}]  -> persisted to AlertLog by the main process
  - overlays: [{kind, ...}]                       -> drawn on the live feed (normalized 0-1 coords)

Geometry in camera module_config is stored normalized (0-1) so it's resolution-independent.
"""
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Karachi")


def within_schedule(schedule) -> bool:
    """Is 'now' inside an optional armed window? schedule = {start:"HH:MM", end:"HH:MM"}.
    None/empty = always armed. Handles overnight windows (start > end)."""
    if not schedule or not schedule.get("start") or not schedule.get("end"):
        return True
    try:
        now = datetime.now(TZ).time()
        s = datetime.strptime(schedule["start"], "%H:%M").time()
        e = datetime.strptime(schedule["end"], "%H:%M").time()
        return (s <= now <= e) if s <= e else (now >= s or now <= e)
    except Exception:
        return True


# ── geometry helpers (normalized 0-1 space) ──────────────────────────────────
def side_of_line(px, py, ax, ay, bx, by) -> float:
    """Signed side of point (px,py) relative to line A->B. >0 one side, <0 other."""
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def signed_distance_to_line(px, py, ax, ay, bx, by) -> float:
    """Signed PERPENDICULAR distance from (px,py) to the line A->B, in the same
    normalized 0-1 units as the point. Same sign convention as side_of_line() — it is
    just that value divided by |AB| — so direction semantics are unchanged, but the
    magnitude is now a real distance and can be compared against a configured band
    width regardless of how long the operator drew the line."""
    dx, dy = bx - ax, by - ay
    denom = math.hypot(dx, dy)
    if denom < 1e-9:
        return 0.0
    return (dx * (py - ay) - dy * (px - ax)) / denom


def segment_position(px, py, ax, ay, bx, by) -> float:
    """Where the point projects ALONG segment A->B: 0 at A, 1 at B.

    side_of_line() and signed_distance_to_line() both describe an INFINITE line, but the
    operator drew a finite segment across a doorway. Without this, somebody walking past
    the end of the drawn line still registers as crossing its mathematical extension."""
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        return 0.0
    return ((px - ax) * dx + (py - ay) * dy) / denom


def line_normal(ax, ay, bx, by) -> tuple[float, float]:
    """Unit normal of line A->B, pointing towards the positive side_of_line() half."""
    dx, dy = bx - ax, by - ay
    denom = math.hypot(dx, dy)
    if denom < 1e-9:
        return 0.0, 0.0
    return -dy / denom, dx / denom


def point_in_poly(px, py, poly) -> bool:
    """Ray-casting point-in-polygon. poly = [[x,y], ...] normalized."""
    if not poly or len(poly) < 3:
        return False
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def in_any_zone(px, py, zones) -> bool:
    return any(point_in_poly(px, py, z) for z in (zones or []))


class Analytic:
    """Base consumer. Subclasses implement process()."""
    module_id = "base"

    def __init__(self, cam_id, config: dict):
        self.cam_id = cam_id
        self.config = config or {}

    def process(self, tracks, faces, shape) -> tuple[list, list]:
        """tracks: person tracks; faces: recognized faces; shape: (h, w) of det frame.
        Returns (events, overlays). Coords in primitives are PIXELS in det frame;
        config geometry is NORMALIZED 0-1; overlays returned NORMALIZED 0-1."""
        return [], []

    # small helper for per-consumer event cooldowns
    def _cooldown(self, key: str, seconds: float, _last={}) -> bool:
        now = time.time()
        k = (id(self), key)
        if now - _last.get(k, 0) >= seconds:
            _last[k] = now
            return True
        return False
