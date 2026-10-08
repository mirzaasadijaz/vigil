"""
Object dropped and left (Kohat POC #5).

Alerts when something appears inside a watched zone, stops moving, and is still there
`abandon_seconds` later. A bag, a box, a carton, a sack, a crate, a cylinder, a chair
somebody dragged over — the mechanism does not care what it is.

WHY THIS IS NOT A BAG DETECTOR
------------------------------
COCO gives `24 backpack`, `26 handbag`, `28 suitcase` free off weights already in VRAM.
Two reasons that is the wrong mechanism for this requirement:

  1. **Most abandoned objects are not bags.** A box, a sack, a crate, a gas cylinder, a
     bundle of cable. None are COCO classes, so a bag detector is simply blind to them.

  2. **A detector cannot tell DROPPED from PERMANENT.** A camera on this deployment has
     a backpack on the table and a handbag on the desk in every single frame. A
     detector-driven rule alerts on both, forever, from the first minute.

So the mechanism is background subtraction, and the classifier — if it is ever added —
supplies a LABEL, not the decision.

THE DUAL BACKGROUND, WHICH IS THE WHOLE TRICK
---------------------------------------------
One MOG2 model does not work, and it fails in a way that looks like the technique being
wrong: the model absorbs the abandoned object into its own background within seconds, so
the region stops being foreground long before a 30 s dwell timer expires, and nothing
ever fires.

Two models at different learning rates:

    LONG  (alpha ~0.0002)  remembers the scene as it WAS. A newly-placed object stays
                           foreground here for minutes.
    SHORT (alpha ~0.02)    tracks the scene as it IS. A stationary object is absorbed
                           within a second or two and becomes background.

    static candidate  =  foreground in LONG  AND  background in SHORT

That conjunction is what separates the three cases:

    a moving person      foreground in BOTH          (still moving; not a candidate)
    permanent furniture  background in BOTH          (it IS the background — never fires)
    a dropped object     foreground LONG, bg SHORT   (new, and has stopped moving)

The middle row is why this approach beats a detector outright: **furniture cannot raise
an alert, because furniture is the background.** The bin that has stood in the corner for
a year was never dropped, and no dwell timer will ever start on it.

RUNS IN THE MAIN PROCESS
------------------------
Like `tamper.py`, and for the same reasons: it is classical CV with no GPU work, it needs
every frame rather than a sampled one, and the main process has headroom (~35% of a core)
while the perception child is the constrained one (~78%). Events reach the database
through `perception.submit_events()`, the same path tamper uses, so they inherit the
false-detection layer, severity mapping and snapshot handling rather than re-implementing
all three.

PEOPLE
------
A person standing still is a static region too, and would otherwise be reported as an
abandoned object. Candidates overlapping a live person track are suppressed — the person
boxes come from the overlay set the perception child already publishes, so this costs no
inference. See `_person_boxes`.

CONFIG (per camera, from the admin panel)
    zones             where to watch; empty = the whole frame
    abandon_seconds   how long it must sit still before alerting (default 30)
    min_area          smallest region worth reporting, fraction of frame (default 0.002)
    settle_seconds    how long a region must be static before its dwell timer starts
    cooldown          seconds between repeat alerts for the same object

TUNING
    UNATTENDED_SAMPLE_INTERVAL   seconds between evaluations (default 0.5)
    UNATTENDED_LONG_ALPHA        long-model learning rate (default 0.0002)
    UNATTENDED_SHORT_ALPHA       short-model learning rate (default 0.02)
    UNATTENDED_MATCH_IOU         IoU that counts as "the same object" (default 0.3)
    UNATTENDED_MISS_GRACE        evaluations a slot survives unseen (default 6)
"""

from __future__ import annotations

import os
import time

from backend.services.analytics import unattended_debug as dbg

# ── Cadence ──────────────────────────────────────────────────────────────────────
# Twice a second. The thing being detected is by definition not moving, so a high rate
# buys nothing, and this runs in the capture loop for every camera with the module on.
SAMPLE_INTERVAL = float(os.environ.get("UNATTENDED_SAMPLE_INTERVAL", "0.5"))

# ── The two background models ────────────────────────────────────────────────────
# LONG must be slow enough that an object stays foreground for well over abandon_seconds.
# At 2 evaluations/sec, alpha 0.0002 gives a half-life of order an hour, so a 30 s or even
# 5-minute dwell is comfortable. SHORT must be fast enough to absorb a stopped object
# within a second or two, but not so fast it absorbs a slow walker.
LONG_ALPHA = float(os.environ.get("UNATTENDED_LONG_ALPHA", "0.0002"))
SHORT_ALPHA = float(os.environ.get("UNATTENDED_SHORT_ALPHA", "0.02"))
HISTORY = int(os.environ.get("UNATTENDED_HISTORY", "500"))
VAR_THRESHOLD = float(os.environ.get("UNATTENDED_VAR_THRESHOLD", "16"))

# Work at this width. Background subtraction does not need detail, and the cost is
# quadratic in resolution - this runs on every camera in the capture loop.
WORK_WIDTH = int(os.environ.get("UNATTENDED_WIDTH", "320"))

# ── Region filtering ─────────────────────────────────────────────────────────────
DEF_MIN_AREA = float(os.environ.get("UNATTENDED_MIN_AREA", "0.002"))
# An abandoned object is compact. A lighting change or a camera nudge produces a large,
# ragged, frame-spanning region; requiring the blob to fill a reasonable share of its own
# bounding box rejects those without rejecting a bag.
MIN_FILL = float(os.environ.get("UNATTENDED_MIN_FILL", "0.35"))
MAX_AREA = float(os.environ.get("UNATTENDED_MAX_AREA", "0.25"))

# Reject regions that touch the frame border.
#
# MEASURED, from a real capture (unattended_debug.log, 2026-09-01, camera 25): of 59
# alerts, 41 - SIXTY-NINE PER CENT - had a box touching an edge. Left 15, bottom 16,
# top 13, right 6. Only 18 were fully interior.
#
# That is not what an abandoned object looks like. It is the signature of the periphery
# itself changing: auto-exposure hunting, vignetting, compression artefacts along the
# border, a strip of sky or road at the frame limit shifting brightness. All of it
# reads as "new, and now static" to a background model, which is exactly the test this
# module applies.
#
# A genuine dropped object is somewhere IN the scene. Something clinging to the frame
# edge is either an artefact or half out of view, and half an object is not evidence.
# A 1% margin is enough - the artefacts hug the border, they do not sit near it.
EDGE_MARGIN = float(os.environ.get("UNATTENDED_EDGE_MARGIN", "0.01"))

# ── Slot matching ────────────────────────────────────────────────────────────────
# The object does not move, so IoU across evaluations is the right primitive - the same
# reasoning as no_stopping.py, where ByteTrack was the wrong tool for a stationary car.
MATCH_IOU = float(os.environ.get("UNATTENDED_MATCH_IOU", "0.3"))
# Evaluations a slot survives without being seen. Somebody walking in front of the object
# must not restart its timer, which is the failure that makes these modules never fire.
MISS_GRACE = int(os.environ.get("UNATTENDED_MISS_GRACE", "6"))

DEF_ABANDON_SECONDS = float(os.environ.get("UNATTENDED_ABANDON_SECONDS", "30"))
DEF_SETTLE_SECONDS = float(os.environ.get("UNATTENDED_SETTLE_SECONDS", "2"))
DEF_COOLDOWN = float(os.environ.get("UNATTENDED_COOLDOWN", "120"))

# How long a PLACE stays alerted, independent of the slot that alerted it.
#
# The cooldown used to live on the slot, and that does not work: a capture produced 5915
# APPEARED / 5915 GONE pairs, because a static region flickers in and out of the mask as
# light shifts. Every time a slot was dropped and re-created the new one arrived with
# alerted=False and a fresh cooldown, so the SAME object alerted again and again - which
# is exactly what was reported. The memory has to outlive the slot, so it is keyed on
# the LOCATION instead.
# ONE ALERT PER OBJECT. Not one per N minutes.
#
# This is how long a reported place must be CLEAR before it is forgotten - not how long
# the report lasts. While the object is still sitting there, every evaluation that still
# sees it REFRESHES the entry, so the timer never starts and it never alerts twice.
#
# The difference matters. A plain expiry re-alerts a bag that has not moved, which is
# the behaviour that was reported as wrong. This only fires again if the object actually
# leaves and something new appears in the same place later - which IS a new event and
# should be reported.
DEF_FORGET_SECONDS = float(os.environ.get("UNATTENDED_FORGET_SECONDS", "60"))
# How much two boxes must overlap to count as "the same place".
MEMORY_IOU = float(os.environ.get("UNATTENDED_MEMORY_IOU", "0.3"))

# How much of a candidate may overlap a person before it is treated as that person
# rather than as an object they left.
PERSON_OVERLAP = float(os.environ.get("UNATTENDED_PERSON_OVERLAP", "0.30"))

EVENT_TYPE = "unattended_object"


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def _overlap_frac(box, other) -> float:
    """Fraction of `box` covered by `other`."""
    bx1, by1, bx2, by2 = box
    ix1, iy1 = max(bx1, other[0]), max(by1, other[1])
    ix2, iy2 = min(bx2, other[2]), min(by2, other[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = (bx2 - bx1) * (by2 - by1)
    return inter / area if area > 0 else 0.0


class _Slot:
    """One candidate object being watched."""

    __slots__ = ("box", "first_seen", "static_since", "last_seen", "misses",
                 "alerted", "last_alert", "hits")

    def __init__(self, box, now: float):
        self.box = box
        self.first_seen = now
        self.static_since = now
        self.last_seen = now
        self.misses = 0
        self.alerted = False
        self.last_alert = 0.0
        self.hits = 1

    def dwell(self, now: float) -> float:
        return now - self.static_since


class UnattendedMonitor:
    """Per-camera state. Constructed by camera_manager, fed every frame."""

    def __init__(self, cam_id, config: dict | None = None):
        self.cam_id = cam_id
        self.config = config or {}
        self._long = None
        self._short = None
        self._slots: list[_Slot] = []
        # (box, ts) of places already reported, surviving slot churn. See DEF_ALERT_MEMORY.
        self._alerted_places: list = []
        self._last_sample = 0.0
        self._samples = 0
        self._warm_until = 0.0
        self.metrics = dbg.Metrics(cam_id)

    # ── config helpers, matching tamper.py ───────────────────────────────────
    def _num(self, key: str, default: float) -> float:
        try:
            v = self.config.get(key)
            return default if v is None or v == "" else float(v)
        except (TypeError, ValueError):
            return default

    def _zones(self) -> list:
        z = self.config.get("zones")
        return z if isinstance(z, list) else []

    # ── people, from the overlay set the child already publishes ─────────────
    def _person_boxes(self) -> list:
        """Live person boxes in normalised coords, or [] if none are available.

        Read from the overlay store rather than by running a detector: the perception
        child already publishes them for drawing, so this costs nothing. They lag the
        frame slightly, which is harmless here — a person who has just moved away still
        suppresses their own residual region for a moment, which is the safe direction."""
        try:
            from backend.services.perception import get_overlay
            ov = get_overlay(self.cam_id)
        except Exception:
            return []
        if not ov:
            return []
        _scale, items = ov
        out = []
        for it in items or []:
            if it.get("kind") == "box" and it.get("box"):
                b = it["box"]
                if len(b) == 4:
                    out.append(tuple(float(v) for v in b))
        return out

    # ── the frame pipeline ───────────────────────────────────────────────────
    def check(self, frame) -> list[dict]:
        """Feed one frame. Returns events to submit (usually empty)."""
        now = time.time()
        if now - self._last_sample < SAMPLE_INTERVAL:
            return []
        self._last_sample = now

        import cv2
        import numpy as np

        abandon = self._num("abandon_seconds", DEF_ABANDON_SECONDS)
        settle = self._num("settle_seconds", DEF_SETTLE_SECONDS)
        min_area = self._num("min_area", DEF_MIN_AREA)
        cooldown = self._num("cooldown", DEF_COOLDOWN)
        zones = self._zones()

        dbg.session(self.cam_id, {
            "abandon_seconds": abandon, "settle_seconds": settle,
            "min_area": min_area, "cooldown": cooldown, "zones": len(zones),
            "long_alpha": LONG_ALPHA, "short_alpha": SHORT_ALPHA,
            "sample_interval": SAMPLE_INTERVAL, "match_iou": MATCH_IOU,
            "edge_margin": EDGE_MARGIN,
            "forget_seconds": self._num("forget_seconds", DEF_FORGET_SECONDS),
            "miss_grace": MISS_GRACE, "work_width": WORK_WIDTH,
        })

        h0, w0 = frame.shape[:2]
        if w0 > WORK_WIDTH:
            small = cv2.resize(frame, (WORK_WIDTH, int(h0 * WORK_WIDTH / w0)))
        else:
            small = frame
        h, w = small.shape[:2]

        if self._long is None:
            self._long = cv2.createBackgroundSubtractorMOG2(
                history=HISTORY, varThreshold=VAR_THRESHOLD, detectShadows=True)
            self._short = cv2.createBackgroundSubtractorMOG2(
                history=HISTORY, varThreshold=VAR_THRESHOLD, detectShadows=True)
            # Nothing may fire until the LONG model has seen enough of the scene to know
            # what "normal" is. Without this every object present at start-up is "new".
            self._warm_until = now + max(5.0, 1.0 / max(LONG_ALPHA, 1e-6) * 0.0
                                         + HISTORY * SAMPLE_INTERVAL * 0.1)
            dbg.info(event="START", camera=self.cam_id,
                     warmup_seconds=round(self._warm_until - now, 1))

        fg_long = self._long.apply(small, learningRate=LONG_ALPHA)
        fg_short = self._short.apply(small, learningRate=SHORT_ALPHA)
        self._samples += 1
        self.metrics.bump("samples")

        # 255 = foreground, 127 = shadow (MOG2 marks shadows separately, and a shadow is
        # never an abandoned object).
        long_fg = (fg_long == 255)
        short_fg = (fg_short == 255)
        # THE conjunction: new against the old scene, but no longer moving.
        static = (long_fg & ~short_fg).astype("uint8") * 255
        static = cv2.morphologyEx(static, cv2.MORPH_OPEN,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        static = cv2.morphologyEx(static, cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))

        warming = now < self._warm_until
        candidates = self._regions(static, w, h, min_area, zones, warming)

        if dbg.tracing():
            dbg.trace(event="SAMPLE", camera=self.cam_id,
                      long_fg=round(float(long_fg.mean()), 4),
                      short_fg=round(float(short_fg.mean()), 4),
                      static_frac=round(float((static > 0).mean()), 4),
                      candidates=len(candidates), slots=len(self._slots),
                      warming=warming, samples=self._samples)

        events = self._advance(candidates, now, abandon, settle, cooldown, warming)
        self.metrics.maybe_report(now)
        return events

    # ── candidate regions ────────────────────────────────────────────────────
    def _regions(self, mask, w, h, min_area, zones, warming) -> list:
        import cv2
        from backend.services.analytics.base import in_any_zone

        n, _labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
        frame_area = float(w * h)
        persons = self._person_boxes()
        out = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            frac = area / frame_area
            if frac < min_area:
                continue
            if frac > MAX_AREA:
                self.metrics.note("rejected", "too_large")     # lighting, camera nudge
                continue
            fill = area / float(max(1, bw * bh))
            if fill < MIN_FILL:
                self.metrics.note("rejected", "ragged")        # not a compact object
                continue
            box = (x / w, y / h, (x + bw) / w, (y + bh) / h)
            if EDGE_MARGIN > 0 and (box[0] <= EDGE_MARGIN or box[1] <= EDGE_MARGIN
                                    or box[2] >= 1.0 - EDGE_MARGIN
                                    or box[3] >= 1.0 - EDGE_MARGIN):
                self.metrics.note("rejected", "touches_frame_edge")
                continue
            cx, cy = cents[i][0] / w, cents[i][1] / h
            if zones and not in_any_zone(cx, cy, zones):
                self.metrics.note("rejected", "outside_zone")
                continue
            # A person standing still is a static region too.
            covered = max((_overlap_frac(box, p) for p in persons), default=0.0)
            if covered >= PERSON_OVERLAP:
                self.metrics.note("rejected", "is_a_person")
                if dbg.verbose():
                    dbg.debug(event="SUPPRESSED", camera=self.cam_id,
                              box=tuple(round(v, 3) for v in box),
                              person_overlap=round(covered, 2),
                              reason="overlaps_a_live_person")
                continue
            out.append({"box": box, "area": frac, "fill": fill})
        if out and not warming:
            self.metrics.bump("candidates", len(out))
        return out

    # ── slot state machine ───────────────────────────────────────────────────
    def _advance(self, candidates, now, abandon, settle, cooldown, warming) -> list:
        events = []
        used = set()

        # Keep a reported place alive for as long as the object is STILL THERE. Any
        # candidate overlapping it counts as still seeing it, whether or not a slot
        # currently exists - slots churn, the object does not. Only once nothing has
        # overlapped for `forget_seconds` is the place forgotten, so that a genuinely
        # new object arriving there later is reported as the new event it is.
        forget = self._num("forget_seconds", DEF_FORGET_SECONDS)
        for e in self._alerted_places:
            if any(_iou(e["box"], c["box"]) >= MEMORY_IOU for c in candidates):
                e["seen"] = now
        before = len(self._alerted_places)
        self._alerted_places = [e for e in self._alerted_places
                                if now - e["seen"] < forget]
        if dbg.verbose() and len(self._alerted_places) < before:
            dbg.debug(event="FORGOTTEN", camera=self.cam_id,
                      places=before - len(self._alerted_places), forget=forget,
                      reason="place clear - a new object here would be a new alert")
        for slot in self._slots:
            best, best_iou = None, 0.0
            for idx, c in enumerate(candidates):
                if idx in used:
                    continue
                v = _iou(slot.box, c["box"])
                if v > best_iou:
                    best, best_iou = idx, v
            if best is not None and best_iou >= MATCH_IOU:
                used.add(best)
                slot.box = candidates[best]["box"]
                slot.last_seen = now
                slot.misses = 0
                slot.hits += 1
            else:
                slot.misses += 1

        # New candidates become slots.
        for idx, c in enumerate(candidates):
            if idx not in used:
                self._slots.append(_Slot(c["box"], now))
                if dbg.verbose():
                    dbg.debug(event="APPEARED", camera=self.cam_id,
                              box=tuple(round(v, 3) for v in c["box"]),
                              area=round(c["area"], 4), fill=round(c["fill"], 2),
                              verdict="TIMER_STARTED")

        # Drop slots gone too long, and decide the survivors.
        alive = []
        for slot in self._slots:
            if slot.misses > MISS_GRACE:
                if dbg.verbose():
                    dbg.debug(event="GONE", camera=self.cam_id,
                              box=tuple(round(v, 3) for v in slot.box),
                              dwell=round(slot.dwell(now), 1),
                              alerted=slot.alerted, verdict="REMOVED")
                self.metrics.bump("removed")
                continue
            alive.append(slot)
            if warming or slot.misses:
                continue
            dwell = slot.dwell(now)
            if dwell < settle:
                continue
            if slot.alerted:
                continue
            if dwell < abandon:
                if dbg.verbose():
                    dbg.debug(event="PENDING", camera=self.cam_id,
                              box=tuple(round(v, 3) for v in slot.box),
                              dwell=round(dwell, 1), abandon=abandon,
                              hits=slot.hits, verdict="BELOW_DWELL")
                continue
            # Has this PLACE already been reported? Keyed on location, not on the slot,
            # because the slot is destroyed and rebuilt every time the region flickers.
            hit = next((e for e in self._alerted_places
                        if _iou(e["box"], slot.box) >= MEMORY_IOU), None)
            if hit is not None:
                slot.alerted = True          # stop this slot re-testing every frame
                self.metrics.note("rejected", "already_reported")
                if dbg.verbose():
                    dbg.debug(event="SUPPRESSED", camera=self.cam_id,
                              box=tuple(round(v, 3) for v in slot.box),
                              first_reported=round(now - hit["first"], 1),
                              reason="already reported - one alert per object")
                continue
            slot.alerted = True
            slot.last_alert = now
            self._alerted_places.append({"box": slot.box, "first": now, "seen": now})
            self.metrics.bump("alerted")
            dbg.info(event="UNATTENDED", camera=self.cam_id,
                     box=tuple(round(v, 3) for v in slot.box),
                     dwell=round(dwell, 1), abandon=abandon,
                     hits=slot.hits, verdict="ALERT")
            events.append({
                "event_type": EVENT_TYPE,
                "module": "unattended",
                "label": f"Object left unattended ({dwell:.0f}s)",
                "value": round(dwell, 1),
                "data": {"dwell_seconds": round(dwell, 1),
                         "abandon_seconds": abandon,
                         "box": [round(v, 4) for v in slot.box],
                         "observations": slot.hits},
                "snapshot": True,
                "overlays": [{"kind": "box", "box": list(slot.box),
                              "color": "dnd",
                              "label": f"UNATTENDED {dwell:.0f}s",
                              "snapshot": True}],
            })
        self._slots = alive
        return events
