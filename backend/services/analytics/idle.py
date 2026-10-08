"""
Idle Time Monitoring — per-zone configurable.

Definitions of "idle", chosen per camera via the `idle_mode` config:

  • "away"  (DEFAULT, away-from-station): the zone is a work station that should be
            occupied. Idle = the station is EMPTY (no person in the zone) for longer
            than the dwell threshold. A seated worker keeps it occupied, so desk work
            is never flagged. Re-arms after someone returns and leaves again.

  • "still" (stillness): a person stays in the zone but is spatially motionless for
            longer than the threshold. Movement is measured against the position where
            the idle timer STARTED (the anchor) — not the previous frame — so a person
            who keeps moving slowly is NOT mistakenly flagged as idle.

  • "loiter" (lingering): a person remains in the zone — moving or not — continuously
            for longer than the threshold. Useful for entrances / restricted areas
            where simply hanging around is the concern.

Escalation: for "still"/"loiter", graded events fire at 50% / 75% / 100% of the dwell
threshold (carried as data.level / data.pct) so prolonged idleness escalates instead
of emitting a single terminal event.

Schedule: an optional armed window {start,end} suppresses flagging outside work hours
(e.g. don't count idle time during lunch / after close).
"""

import time

from backend.services.analytics.base import Analytic, in_any_zone, within_schedule

MOVE_EPS = 0.004         # normalized displacement from the anchor under this = "stationary"
LEVELS = (0.5, 0.75, 1.0)  # escalation thresholds (fraction of dwell)


class IdleTime(Analytic):
    module_id = "idle"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        self.state: dict[int, dict] = {}   # still/loiter: track_id -> {since, anchor, level}
        self.empty_since: float | None = None   # away mode
        self.flagged_empty = False

    def _zone_overlays(self, zones, color, label=None):
        return [
            {
                "kind": "zone",
                "polygon": z,
                "color": color,
                "label": label if i == 0 else None,
            }
            for i, z in enumerate(zones)
        ]

    @staticmethod
    def _level_for(elapsed: float, dwell: float) -> int:
        """Highest escalation level (0..3) reached for `elapsed` seconds vs `dwell`."""
        return sum(1 for frac in LEVELS if elapsed >= dwell * frac)

    def process(self, tracks, faces, shape):
        h, w = shape
        zones = self.config.get("zones") or []
        dwell = float(self.config.get("dwell_seconds") or 60)
        mode = self.config.get("idle_mode") or "away"
        armed = within_schedule(self.config.get("schedule"))
        now = time.time()

        def in_zone(t):
            return (not zones) or in_any_zone(
                t["centroid"][0] / w, t["centroid"][1] / h, zones
            )

        occupants = [t for t in tracks if in_zone(t)]

        # ── away-from-station: idle when the station is unmanned ──────────────
        if mode == "away":
            events = []
            empty_for = 0.0
            if not occupants:
                if self.empty_since is None:
                    self.empty_since = now
                empty_for = now - self.empty_since
                if armed and empty_for >= dwell and not self.flagged_empty:
                    self.flagged_empty = True
                    events.append(
                        {
                            "event_type": "idle",
                            "value": round(empty_for, 1),
                            "data": {"reason": "station_empty"},
                            "snapshot": True,
                        }
                    )
            else:
                self.empty_since = None
                self.flagged_empty = False
            alerting = self.flagged_empty
            overlays = self._zone_overlays(
                zones,
                "idle" if alerting else "online",
                "STATION EMPTY" if alerting else "MANNED",
            )
            if alerting:
                overlays.append(
                    {
                        "kind": "label",
                        "text": "STATION EMPTY",
                        "color": "idle",
                        "snapshot": True,
                    }
                )
            return events, overlays

        # ── still / loiter: per-track timers ─────────────────────────────────
        events, idle_ids, seen = [], set(), set()
        for t in occupants:
            tid = t["id"]
            nx, ny = t["centroid"][0] / w, t["centroid"][1] / h
            seen.add(tid)
            st = self.state.get(tid)
            if st is None:
                self.state[tid] = {"since": now, "anchor": (nx, ny), "level": 0}
                continue

            if mode == "still":
                moved = abs(nx - st["anchor"][0]) + abs(ny - st["anchor"][1])
                if moved > MOVE_EPS:
                    st["since"] = now
                    st["anchor"] = (nx, ny)
                    st["level"] = 0

            elapsed = now - st["since"]
            if armed:
                lvl = self._level_for(elapsed, dwell)
                if lvl > st["level"]:
                    st["level"] = lvl
                    reason = "stationary" if mode == "still" else "loitering"
                    events.append({"event_type": "idle", "value": round(elapsed, 1),
                                   "data": {"track": tid, "reason": reason,
                                            "level": lvl, "pct": int(LEVELS[lvl - 1] * 100)},
                                   "snapshot": True})
            if st["level"] >= 1:
                idle_ids.add(tid)
        for tid in list(self.state):
            if tid not in seen:
                self.state.pop(tid, None)

        overlays = self._zone_overlays(zones, "idle")
        label = "IDLE" if mode == "still" else "LOITERING"
        for t in occupants:
            if t["id"] in idle_ids:
                x1, y1, x2, y2 = t["box"]
                overlays.append({"kind": "box", "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                                 "color": "idle", "label": label, "snapshot": True})
        return events, overlays
