"""Smart Occupancy — live count of people inside zone(s) vs capacity."""
from backend.services.analytics.base import Analytic, in_any_zone


class Occupancy(Analytic):
    module_id = "occupancy"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        self.last_count = -1

    def process(self, tracks, faces, shape):
        h, w = shape
        zones = self.config.get("zones") or []
        capacity = int(self.config.get("capacity") or 0)
        if zones:
            count = 0
            for t in tracks:
                cx, cy = t["centroid"]
                if in_any_zone(cx / w, cy / h, zones):
                    count += 1
        else:
            count = len(tracks)   # whole-frame occupancy if no zone drawn

        self.last_count = count

        events = []
        # Occupancy raises a NOTIFICATION only when the zone is OVER capacity. A within-limit
        # count is the normal state and must not spawn an alert/toast — emitting one every time
        # the count changed also flooded the event history and crowded genuine alerts out of the
        # latest-alerts feed. The live count still renders every frame on the overlay below; it
        # is simply no longer persisted as an event unless capacity is exceeded.
        if capacity and count > capacity and self._cooldown("over", 30):
            events.append({"event_type": "over_capacity", "value": count,
                           "data": {"capacity": capacity}, "snapshot": True})

        label = f"OCCUPANCY {count}" + (f"/{capacity}" if capacity else "")
        color = "dnd" if (capacity and count > capacity) else "accent-soft"
        overlays = [{"kind": "zone", "polygon": z, "color": color, "label": label if i == 0 else None}
                    for i, z in enumerate(zones)] or [{"kind": "label", "text": label, "color": color}]
        return events, overlays
