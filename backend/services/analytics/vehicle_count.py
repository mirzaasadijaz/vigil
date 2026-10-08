"""
Vehicle counting across a line, broken down by type (Kohat POC).

Counts vehicles crossing a drawn line and keeps a separate IN/OUT tally per type — car,
motorcycle, bus, truck, bicycle — as well as a combined total.

WHY THIS SUBCLASSES HEADCOUNT RATHER THAN REIMPLEMENTING IT
-----------------------------------------------------------
Deciding that something crossed a line is a solved problem in this codebase, and it was
not solved cheaply. `HeadCount` carries a foot-point reference, a hysteresis band so a
box jittering on the line does not count twice, movement validation, direction
consistency, id hand-off and debounce — every one of which exists because a real capture
showed it was needed. A vehicle crossing a line is the same geometry as a person crossing
a line, so writing a second crossing machine would mean rediscovering all of it.

What differs is only the SUBJECT and the TALLY:

    HeadCount     one pair of counters, fed person tracks
    VehicleCount  a pair of counters PER TYPE, fed vehicle tracks

`TripWire` already subclasses `HeadCount` the same way, so this is the established shape.

WHAT IT CAN AND CANNOT COUNT
----------------------------
The types come from COCO, off weights already in VRAM — no new model:

    car   motorcycle   bus   truck   bicycle

**There is no rickshaw class, and no three-wheeler class of any kind.** A COCO model
shown an auto-rickshaw emits something — usually car, motorcycle or truck, and not
consistently the same one from frame to frame. So a rickshaw will be counted as a
vehicle, but not AS a rickshaw. Counting it under a wrong label would be worse than not
counting it, so this module does not pretend otherwise. Counting rickshaws properly
needs a model fine-tuned to know what one is; see plan_kohat_poc.md.

TRACKING
--------
Fed by `detectors/vehicle.detect_and_track_vehicles`, which attaches a ByteTrack id.
The no-stopping module deliberately uses the UNTRACKED path instead, because a
stationary vehicle is matched better by box overlap than by a motion tracker. Counting
needs the opposite: a moving vehicle's frame-to-frame IoU is far too low to identify it,
so identity has to come from the tracker.

CONFIG
    line              the counting line, normalised 0-1
    vehicle_types     subset of car|motorcycle|bus|truck|bicycle; empty = all
    invert_direction  swap which side counts as IN (see HeadCount — the sign depends on
                      the order the line's points were drawn, which nobody can see)
    swap_labels       relabel the overlay only
    snapshot          picture on every crossing
"""

from __future__ import annotations

import os

from backend.services.analytics.headcount import HeadCount

# Types counted when the operator has not narrowed it. Bicycle is off by default: on a
# road camera it is mostly a source of confusion with motorcycle, and a site that wants
# it can ask for it.
DEF_TYPES = os.environ.get("VEHICLE_COUNT_TYPES", "car,motorcycle,bus,truck")


class VehicleCount(HeadCount):
    """Line crossing with a per-type tally."""

    module_id = "vehicle_count"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        # {kind: [in, out]} — built lazily so a type nobody has seen does not appear in
        # the payload as a permanent zero and imply the module is watching for it.
        self.by_kind: dict = {}
        self._kind_of: dict = {}      # track id -> kind, see process()

    # ── configuration ────────────────────────────────────────────────────────
    def _wanted_types(self) -> set:
        raw = self.config.get("vehicle_types")
        if isinstance(raw, (list, tuple)):
            vals = [str(v).strip().lower() for v in raw if str(v).strip()]
        else:
            vals = [v.strip().lower() for v in str(raw or DEF_TYPES).split(",")
                    if v.strip()]
        return set(vals)

    # ── the one thing that differs from HeadCount ────────────────────────────
    def process(self, tracks, faces, shape):
        """`tracks` are VEHICLE tracks, already carrying `id` and `kind`.

        The crossing decision is entirely inherited. All this adds is remembering which
        type each track was, and splitting the resulting entry/exit events into per-type
        counters."""
        wanted = self._wanted_types()
        if wanted:
            tracks = [t for t in tracks if (t.get("kind") or "") in wanted]

        # A crossing event names only the track id, and by the time it is emitted the
        # detection that carried the type is gone. So the mapping is recorded on every
        # frame the track is seen, BEFORE the crossing machine runs.
        for t in tracks:
            if t.get("id") is not None and t.get("kind"):
                self._kind_of[t["id"]] = t["kind"]

        events, overlays = super().process(tracks, faces, shape)

        for ev in events:
            tid = (ev.get("data") or {}).get("track")
            kind = self._kind_of.get(tid, "vehicle")
            tally = self.by_kind.setdefault(kind, [0, 0])
            if ev.get("event_type") == "entry":
                tally[0] += 1
            else:
                tally[1] += 1
            # Relabel so the unified event store stays readable: `entry` on this module
            # means a vehicle, not a person, and the dashboard groups on event_type.
            inbound = ev["event_type"] == "entry"
            ev["event_type"] = "vehicle_entry" if inbound else "vehicle_exit"
            ev["label"] = f"{kind.title()} {'in' if inbound else 'out'}"
            data = ev.setdefault("data", {})
            data["vehicle_type"] = kind
            # The running per-type totals travel WITH the event, so a dashboard can show
            # the breakdown without replaying the whole table.
            data["by_type"] = {k: {"in": v[0], "out": v[1]}
                               for k, v in sorted(self.by_kind.items())}

        # Bound the id->type map. Track ids are process-global and monotonic, so without
        # this it grows for as long as the service runs.
        if len(self._kind_of) > 4096:
            for tid in sorted(self._kind_of)[:2048]:
                self._kind_of.pop(tid, None)

        return events, overlays

    # ── overlay ──────────────────────────────────────────────────────────────
    def _overlays(self, line, band: float, show_band: bool):
        """The inherited line and band, relabelled with the per-type breakdown.

        A single IN/OUT pair on a road camera answers almost nothing — the question is
        always "how many of what", and an operator should be able to read that off the
        live view rather than going to the database for it."""
        ovs = super()._overlays(line, band, show_band)
        if ovs and self.by_kind:
            parts = " ".join(f"{k[:4].upper()} {v[0]}/{v[1]}"
                             for k, v in sorted(self.by_kind.items()))
            ovs[0]["label"] = f"IN {self.entries} OUT {self.exits}  |  {parts}"
        return ovs
