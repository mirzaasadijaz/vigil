"""Intrusion Detection — alert on any person inside a restricted zone (optional schedule).

Diagnostics only were added to this file (INTRUSION_DEBUG=trace, see intrusion_debug.py).
The detection logic is untouched: same centroid membership test, same fixed 10-second
cooldown, same events and same overlays. Every logging call observes a decision that was
already being made.

The one structural edit is that `if intruders and self._cooldown(...)` became a nested
pair of ifs, so the SUPPRESSED case has somewhere to log. The short-circuit is preserved
— `_cooldown` is still only consulted when somebody is actually in the zone, so frames
with an empty zone still do not consume the timer — and there is a test for exactly that.
"""
from backend.services.analytics import intrusion_debug as dbg
from backend.services.analytics.base import Analytic, in_any_zone, within_schedule


class Intrusion(Analytic):
    module_id = "intrusion"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        self.metrics = dbg.Metrics(cam_id)
        # Logged at construction, so "assigned to this camera" and "never assigned" stay
        # distinguishable even when the module then has nothing to report.
        dbg.info(event="CREATED", camera=cam_id,
                 zones=len((config or {}).get("zones") or []))

    def process(self, tracks, faces, shape):
        h, w = shape
        zones = self.config.get("zones") or []

        # NO _announced guard here on purpose. DiagnosticLog.session() already
        # fingerprints on the config and re-emits only when it CHANGES, which is what
        # makes a mid-run retune visible. Guarding it with a one-shot flag threw that
        # away: an operator could edit a value in the admin panel and the log would keep
        # reporting the value from start-up, with no way to tell whether the edit had
        # reached the pipeline at all.
        dbg.session(self.cam_id, {
            "zones": len(zones),
            "scheduled": bool(self.config.get("schedule")),
            "cooldown": 10,                 # fixed in this module
            "reference_point": "centroid",  # what the membership test uses
        })

        if not zones or not within_schedule(self.config.get("schedule")):
            dbg.debug(event="INERT", camera=self.cam_id, zones=len(zones),
                      verdict="NO_ZONE" if not zones else "OUT_OF_SCHEDULE")
            return [], [{"kind": "zone", "polygon": z, "color": "dnd"} for z in zones]

        self.metrics.bump("frames")
        self.metrics.bump("tracks_seen", len(tracks))

        intruders = [t for t in tracks if in_any_zone(t["centroid"][0] / w, t["centroid"][1] / h, zones)]
        self.metrics.bump("tracks_in_zone", len(intruders))

        if dbg.tracing():
            inside = {t["id"] for t in intruders}
            for t in tracks:
                dbg.trace(event="OBS", camera=self.cam_id, track=t["id"],
                          point=(t["centroid"][0] / w, t["centroid"][1] / h),
                          in_zone=t["id"] in inside)

        events = []
        if intruders:
            if self._cooldown("intrusion", 10):
                self.metrics.bump("intrusions_alerted")
                dbg.info(event="INTRUSION", camera=self.cam_id, count=len(intruders),
                         tracks=tuple(t["id"] for t in intruders), verdict="ALERT")
                events.append({"event_type": "intrusion", "value": len(intruders),
                               "data": {"tracks": [t["id"] for t in intruders]}, "snapshot": True})
            else:
                self.metrics.bump("filtered_cooldown")
                dbg.debug(event="FILTER", camera=self.cam_id, count=len(intruders),
                          tracks=tuple(t["id"] for t in intruders), cooldown=10,
                          verdict="COOLDOWN")

        overlays = [{"kind": "zone", "polygon": z, "color": "dnd",
                     "label": "RESTRICTED" if i == 0 else None} for i, z in enumerate(zones)]
        for t in intruders:
            x1, y1, x2, y2 = t["box"]
            overlays.append({"kind": "box", "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                             "color": "dnd", "label": "INTRUSION", "snapshot": True})

        self.metrics.maybe_report()
        return events, overlays
