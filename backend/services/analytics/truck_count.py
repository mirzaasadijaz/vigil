"""Truck Counting at the main gate — directional truck counting across a line."""
from datetime import datetime
from time import time
from backend.services.analytics.base import Analytic, side_of_line, TZ

# Seconds a truck track must wait before its flip can count again — a big truck
# backing up / lining up near the gate line would otherwise get double-counted.
_TRUCK_COOLDOWN = float(__import__("os").environ.get("TRUCK_COOLDOWN", "8"))


class TruckCount(Analytic):
    module_id = "truck_count"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        self.prev_side: dict[int, float] = {}
        self.last_count: dict[int, float] = {}
        self.entries = 0
        self.exits = 0
        self.day = datetime.now(TZ).date().isoformat()

    def process(self, tracks, faces, shape):
        h, w = shape
        line = self.config.get("line")
        if not line or len(line) < 2:
            return [], []
        (ax, ay), (bx, by) = line[0], line[1]

        today = datetime.now(TZ).date().isoformat()
        if today != self.day:
            self.day = today
            self.entries = 0
            self.exits = 0
            self.prev_side.clear()
            self.last_count.clear()

        now = time()
        events = []
        for t in tracks:
            cx, cy = t["centroid"]
            nx, ny = cx / w, cy / h
            s = side_of_line(nx, ny, ax, ay, bx, by)
            prev = self.prev_side.get(t["id"])
            self.prev_side[t["id"]] = s
            if prev is None or s == 0 or (s > 0) == (prev > 0):
                continue
            if now - self.last_count.get(t["id"], 0) < _TRUCK_COOLDOWN:
                continue
            self.last_count[t["id"]] = now
            if prev < 0 <= s:
                self.entries += 1
                events.append({"event_type": "entry", "value": 1,
                               "data": {"track": t["id"], "in": self.entries, "out": self.exits,
                                        "total": self.entries + self.exits}})
            else:
                self.exits += 1
                events.append({"event_type": "exit", "value": 1,
                               "data": {"track": t["id"], "in": self.entries, "out": self.exits,
                                        "total": self.entries + self.exits}})

        overlays = [{"kind": "line", "points": line, "color": "online",
                     "label": f"IN {self.entries}  OUT {self.exits}"}]
        for t in tracks:
            x1, y1, x2, y2 = t["box"]
            overlays.append({"kind": "box",
                             "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                             "color": "online", "label": "TRUCK"})
        return events, overlays
