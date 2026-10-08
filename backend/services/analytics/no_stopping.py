"""
Vehicle stopped in a no-stopping zone (Kohat POC #6).

THE HARD PART IS STATIONARITY, NOT DETECTION
--------------------------------------------
Detecting a car is free — COCO class 2 off weights already in VRAM. Deciding that the car
has STOPPED is where this module earns its keep, and there are two traps:

  1. **Detector jitter.** A parked car's box wobbles a few pixels every frame. Compare
     raw positions between consecutive frames and the dwell timer resets forever, so the
     alert never fires. `move_tolerance` exists for exactly this, and it is compared
     against a SMOOTHED displacement rather than a single-frame delta.
  2. **A car waiting is not a car parked — for the first few seconds.** They are
     genuinely indistinguishable, which is why `dwell_seconds` is the operator's knob and
     not a constant. At a barrier you want 60 s; on a yellow line you want 15 s.

TRACKING WITHOUT A TRACKER
    Vehicle detections carry no ByteTrack id (see `detectors/vehicle.py` for why). This
    module matches detections to its own slots by BOX IoU across evaluations, which is
    the right primitive here: a stationary object has near-perfect frame-to-frame IoU,
    which is precisely the case a motion tracker handles worst.

    A slot survives `MISS_GRACE` consecutive misses before being dropped, so a vehicle
    briefly occluded by a passing person does not restart its timer.

REFERENCE POINT
    The box centre, not the foot point. A vehicle's "foot" is ambiguous at the angles
    CCTV sees them from, and the whole box is rigid, so its centre is the stable choice.

CONFIG
    zones            where stopping is prohibited; empty = whole frame
    dwell_seconds    how long stationary before it is an offence (default 30)
    move_tolerance   normalised displacement treated as noise (default 0.02)
    vehicle_types    subset of car|motorcycle|bus|truck; empty = all
    schedule         optional armed window, e.g. only during working hours
    cooldown         seconds between alerts for the same slot (default 120)
"""
import os
import time

from backend.services.analytics import vehicle_debug as dbg
from backend.services.analytics.base import Analytic, in_any_zone, within_schedule


# ── Tunable defaults ─────────────────────────────────────────────────────────────
# These have env fallbacks because the camera editor has no renderer for them yet, so
# without this they are reachable by no means at all - not from the UI, not from a
# config file, not from the environment. A parameter nobody can change is a parameter
# that is always wrong somewhere.
#
# Precedence is the same as everywhere else in this codebase: per-camera config wins,
# then the environment, then the constant.
DEF_DWELL = float(os.environ.get("NOSTOP_DWELL_SECONDS", "30"))
# The knob that matters. A stationary vehicle's box still wobbles a few pixels per frame,
# so too tight a value resets the dwell timer forever and the alert never fires.
DEF_MOVE_TOL = float(os.environ.get("NOSTOP_MOVE_TOLERANCE", "0.02"))
DEF_COOLDOWN = float(os.environ.get("NOSTOP_COOLDOWN", "120"))

MISS_GRACE = 3          # evaluations a slot survives without a matching detection
IOU_MATCH = 0.35        # minimum IoU to consider a detection the same vehicle
SMOOTH = 0.6            # EMA weight on displacement; damps single-frame jitter


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class _Slot:
    """One tracked vehicle, matched by overlap rather than by tracker id."""
    __slots__ = ("sid", "box", "cx", "cy", "kind", "since", "still_since",
                 "disp", "misses", "alerted", "last_alert")

    def __init__(self, sid: int, box, kind: str, now: float):
        self.sid = sid
        self.box = box
        self.cx = (box[0] + box[2]) / 2.0
        self.cy = (box[1] + box[3]) / 2.0
        self.kind = kind
        self.since = now
        self.still_since = now
        self.disp = 0.0
        self.misses = 0
        self.alerted = False
        self.last_alert = 0.0


class NoStopping(Analytic):
    module_id = "no_stopping"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        self.metrics = dbg.Metrics(cam_id)
        self._slots: list[_Slot] = []
        self._next_sid = 1

    def _num(self, key: str, default: float) -> float:
        try:
            v = self.config.get(key)
            return default if v is None or v == "" else float(v)
        except (TypeError, ValueError):
            return default

    def _wanted_kinds(self) -> set:
        raw = self.config.get("vehicle_types")
        if not raw:
            return set()
        if isinstance(raw, str):
            raw = [p.strip() for p in raw.split(",")]
        return {str(v).strip().lower() for v in raw if str(v).strip()}

    def process(self, tracks, faces, shape):
        """Inert. Vehicle detections arrive through `process_detections` instead, the
        same way fire/smoke does — the person-track signature does not fit."""
        return [], []

    def process_detections(self, detections, shape):
        h, w = shape
        zones = self.config.get("zones") or []
        dwell = self._num("dwell_seconds", DEF_DWELL)
        tol = self._num("move_tolerance", DEF_MOVE_TOL)
        cooldown = self._num("cooldown", DEF_COOLDOWN)
        kinds = self._wanted_kinds()
        now = time.time()

        # NO _announced guard here on purpose. DiagnosticLog.session() already
        # fingerprints on the config and re-emits only when it CHANGES, which is what
        # makes a mid-run retune visible. Guarding it with a one-shot flag threw that
        # away: an operator could edit a value in the admin panel and the log would keep
        # reporting the value from start-up, with no way to tell whether the edit had
        # reached the pipeline at all.
        dbg.session(self.cam_id, {
            "dwell_seconds": dwell, "move_tolerance": tol, "cooldown": cooldown,
            "zones": len(zones), "vehicle_types": ",".join(sorted(kinds)) or "all",
        })

        self.metrics.bump("evaluations")

        if not within_schedule(self.config.get("schedule")):
            self._slots.clear()
            return [], []

        # Normalise and filter to the vehicles this camera cares about.
        cands = []
        for d in (detections or []):
            if kinds and d.get("kind") not in kinds:
                continue
            x1, y1, x2, y2 = d["box"]
            nbox = [x1 / w, y1 / h, x2 / w, y2 / h]
            ncx, ncy = (nbox[0] + nbox[2]) / 2.0, (nbox[1] + nbox[3]) / 2.0
            if zones and not in_any_zone(ncx, ncy, zones):
                continue
            cands.append((nbox, d.get("kind", "vehicle"), d.get("confidence", 0.0)))
        self.metrics.bump("vehicles_detected", len(detections or []))
        self.metrics.bump("vehicles_in_zone", len(cands))

        # Greedy IoU match: best pair first, so two vehicles close together do not swap
        # slots and reset each other's timers.
        pairs = []
        for ci, (nbox, _, _) in enumerate(cands):
            for si, slot in enumerate(self._slots):
                iou = _iou(nbox, slot.box)
                if iou >= IOU_MATCH:
                    pairs.append((iou, ci, si))
        pairs.sort(reverse=True)
        used_c, used_s, matched = set(), set(), {}
        for iou, ci, si in pairs:
            if ci in used_c or si in used_s:
                continue
            used_c.add(ci); used_s.add(si); matched[si] = (ci, iou)

        events = []
        overlays = [{"kind": "zone", "polygon": z, "color": "accent-soft",
                     "label": "NO STOPPING" if i == 0 else None}
                    for i, z in enumerate(zones)]

        # Update matched slots.
        for si, slot in enumerate(self._slots):
            if si not in matched:
                slot.misses += 1
                continue
            ci, iou = matched[si]
            nbox, kind, conf = cands[ci]
            slot.misses = 0
            ncx, ncy = (nbox[0] + nbox[2]) / 2.0, (nbox[1] + nbox[3]) / 2.0
            step = ((ncx - slot.cx) ** 2 + (ncy - slot.cy) ** 2) ** 0.5
            # EMA, so one jittery frame cannot reset a legitimate dwell.
            slot.disp = SMOOTH * slot.disp + (1.0 - SMOOTH) * step
            slot.box, slot.cx, slot.cy, slot.kind = nbox, ncx, ncy, kind

            if slot.disp > tol:
                if slot.still_since != now:
                    self.metrics.bump("dwell_reset_by_motion")
                slot.still_since = now
                slot.alerted = False

            still_for = now - slot.still_since

            if dbg.tracing():
                dbg.trace(event="VEHICLE", camera=self.cam_id, slot=slot.sid,
                          kind=slot.kind, iou=iou, step=step, disp=slot.disp,
                          tol=tol, still_for=still_for, dwell=dwell, conf=conf)

            if not slot.alerted and still_for >= dwell:
                if cooldown > 0 and (now - slot.last_alert) < cooldown:
                    self.metrics.bump("filtered_cooldown")
                    dbg.debug(event="FILTER", camera=self.cam_id, slot=slot.sid,
                              age=now - slot.last_alert, cooldown=cooldown,
                              verdict="COOLDOWN")
                else:
                    slot.alerted = True
                    slot.last_alert = now
                    self.metrics.bump("stops_alerted")
                    dbg.info(event="STOPPED", camera=self.cam_id, slot=slot.sid,
                             kind=slot.kind, still_for=still_for, dwell=dwell,
                             disp=slot.disp, verdict="ALERT")
                    events.append({
                        "event_type": "no_stopping",
                        "label": f"Vehicle Stopped ({slot.kind})",
                        "value": round(still_for, 1),
                        "data": {"slot": slot.sid, "kind": slot.kind,
                                 "stopped_seconds": round(still_for, 1),
                                 "displacement": round(slot.disp, 4)},
                        "snapshot": True,
                    })

            overlays.append({
                "kind": "box", "box": slot.box,
                "color": "dnd" if slot.alerted else "idle",
                "label": (f"{slot.kind.upper()} STOPPED {int(still_for)}s"
                          if slot.alerted else
                          f"{slot.kind.upper()} {int(still_for)}s"),
            })

        # New slots for unmatched detections.
        for ci, (nbox, kind, conf) in enumerate(cands):
            if ci in used_c:
                continue
            slot = _Slot(self._next_sid, nbox, kind, now)
            self._next_sid += 1
            self._slots.append(slot)
            self.metrics.bump("dwell_started")
            dbg.debug(event="NEW", camera=self.cam_id, slot=slot.sid, kind=kind,
                      conf=conf, verdict="TIMER_STARTED")
            overlays.append({"kind": "box", "box": nbox, "color": "idle",
                             "label": f"{kind.upper()} 0s"})

        # Retire slots that have been missing too long.
        self._slots = [s for s in self._slots if s.misses <= MISS_GRACE]

        self.metrics.maybe_report()
        return events, overlays
