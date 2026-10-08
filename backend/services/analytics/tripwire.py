"""
Virtual Trip Wire — alert on any person crossing a drawn line (Kohat POC #1).

WHY THIS SUBCLASSES HeadCount RATHER THAN REIMPLEMENTING A LINE TEST
--------------------------------------------------------------------
A naive trip wire is four lines of code: take the centroid, test which side of the line
it is on, alert when the sign flips. That version was measured during Phase 1 and scored
0.62 against synthetic ground truth. The hardened head-count crossing machine scored
1.00, and the difference is entirely in the things the naive version does not do:

  * foot-point rather than centroid reference, so a person's box growing as they
    approach the camera does not read as movement across the line
  * a hysteresis BAND around the line, so a detection jittering on the boundary does
    not alert repeatedly
  * movement validation — minimum displacement and direction consistency, so a
    stationary person whose box wobbles never crosses anything
  * finite-SEGMENT bounds, so somebody walking past the END of the drawn line does not
    trip its mathematical extension
  * track id hand-off, so a ByteTrack id switch mid-crossing is not counted twice or
    lost entirely
  * a debounce cooldown per track

Every one of those is a real false-positive or missed-detection source on real footage,
and every one is already solved in `HeadCount`. Reimplementing them here would mean
reintroducing the same bugs and re-tuning against the same footage.

So the inheritance is deliberate: `HeadCount` decides WHETHER a crossing happened, and
this class decides whether that crossing is worth ALERTING about. The semantic
difference between the two modules is small and lives entirely in this file:

    head-count   "which direction, and how many so far"   -> entry / exit, low severity
    trip wire    "did anyone cross at all"                -> crossing, HIGH severity

CONFIG
    line        [[x1,y1],[x2,y2]]  normalised, as head-count
    direction   "any" (default) | "in" | "out"
    cooldown    seconds before the SAME PERSON can alert again (default 5)
    cooldown_scope
                "track" (default) | "wire"

                This default was changed after a real miss. It was originally per-WIRE,
                on the reasoning that a queue filing through a door should produce one
                alert rather than eight. That reasoning is wrong for a security trip
                wire: two people walking through together is the ordinary case at a
                gate, and collapsing them means the alert says one person crossed when
                two did. Under-reporting a perimeter breach is the worst failure this
                module has.

                So the cooldown is now per TRACK: every distinct person gets their own
                alert, while the same person re-crossing (or a detection jittering) is
                still debounced. `wire` remains available for a genuinely high-traffic
                doorway where one alert per burst is what you want.

DIAGNOSTICS
    logs/tripwire_debug.log   what this module did with each detected crossing
    logs/headcount_debug.log  why a crossing was or was not detected in the first place
See `tripwire_debug.py` — the distinction matters when diagnosing a miss.
"""
import os

from backend.services.analytics import tripwire_debug as dbg
from backend.services.analytics.headcount import HeadCount

DIRECTIONS = ("any", "in", "out")

# How far beyond each END of the drawn line a crossing still counts, as a fraction of the
# line's length. Head-count uses 0.05 because counting people through a doorway should
# not count somebody walking past its frame.
#
# A trip wire is the opposite case. It is drawn across an opening a person can go round
# the edge of, and somebody stepping across at the very corner has crossed the perimeter
# just as much as somebody crossing the middle - reported as "outside_line_segment" in
# headcount_debug.log, which is what a corner miss looks like. Under-reporting a breach
# is this module's worst failure, so its default is wider.
#
# Raise it further if corners are still missed; lower it if people walking PAST the end
# of the wire start alerting. Set `segment_margin` per camera to override.
DEF_TW_SEGMENT_MARGIN = float(os.environ.get("TRIPWIRE_SEGMENT_MARGIN", "0.20"))
DEF_TW_COOLDOWN = float(os.environ.get("TRIPWIRE_COOLDOWN", "5"))
DEF_TW_DIRECTION = os.environ.get("TRIPWIRE_DIRECTION", "any").strip().lower()
DEF_TW_SCOPE = os.environ.get("TRIPWIRE_COOLDOWN_SCOPE", "track").strip().lower()


class TripWire(HeadCount):
    module_id = "tripwire"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        # Own metrics object. The inherited head-count metrics keep counting detection
        # quality, which is what they are for; these count alerting decisions.
        self.tw_metrics = dbg.Metrics(cam_id)
        self._last_alert = 0.0                 # scope="wire"
        # Running total since this consumer was created. Head-count's in/out totals are
        # deliberately stripped from the payload (they mean something different), but
        # "how many people crossed this wire" is the question the module exists to
        # answer, so it is carried explicitly.
        self.crossings = 0
        # Logged HERE rather than in process(), deliberately. See the note in
        # tripwire_debug.py: if the inherited crossing machine raises, run_consumers
        # swallows it to stdout and this module would never appear in its own log at
        # all - making "never assigned to this camera" indistinguishable from "assigned
        # and failing". This line proves the consumer was built.
        dbg.info(event="CREATED", camera=cam_id,
                 has_line=bool((config or {}).get("line")),
                 restored_total=self.crossings)
        self._last_alert_by_track: dict = {}   # scope="track"

    def _scope(self) -> str:
        v = str(self.config.get("cooldown_scope") or DEF_TW_SCOPE).strip().lower()
        return v if v in ("track", "wire") else "track"

    def _prune(self, now: float, cooldown: float) -> None:
        """Drop per-track timestamps that can no longer suppress anything.

        Without this the map grows for the lifetime of the process — ByteTrack ids climb
        continuously on a busy camera (observed reaching 179 in three minutes on a real
        feed), so an unbounded dict here would be a slow leak."""
        if len(self._last_alert_by_track) < 256:
            return
        horizon = max(cooldown, 1.0) * 4
        self._last_alert_by_track = {
            t: ts for t, ts in self._last_alert_by_track.items()
            if now - ts < horizon
        }

    def _direction(self) -> str:
        d = str(self.config.get("direction") or DEF_TW_DIRECTION).strip().lower()
        return d if d in DIRECTIONS else "any"

    def process(self, tracks, faces, shape):
        # Widen the segment bound before the inherited machine reads it, unless the
        # operator set one explicitly. setdefault rather than assignment: a per-camera
        # value must always win, and `config` is replaced wholesale on every edit so this
        # has to be re-applied rather than done once in __init__.
        if self.config is not None:
            self.config.setdefault("segment_margin", DEF_TW_SEGMENT_MARGIN)

        # Let the hardened machine do the detection. It emits entry/exit and keeps its
        # own counters; we consume those events and emit our own.
        raw_events, overlays = super().process(tracks, faces, shape)

        want = self._direction()
        cooldown = self._num("cooldown", DEF_TW_COOLDOWN)

        # NO _announced guard here on purpose. DiagnosticLog.session() already
        # fingerprints on the config and re-emits only when it CHANGES, which is what
        # makes a mid-run retune visible. Guarding it with a one-shot flag threw that
        # away: an operator could edit a value in the admin panel and the log would keep
        # reporting the value from start-up, with no way to tell whether the edit had
        # reached the pipeline at all.
        dbg.session(self.cam_id, {
            "direction": want, "cooldown": cooldown,
            "cooldown_scope": self._scope(),
            "segment_margin": self._num("segment_margin", DEF_TW_SEGMENT_MARGIN),
            "restored_total": self.crossings,
            "line": bool(self.config.get("line"))})

        events = []
        for ev in raw_events:
            et = ev.get("event_type")
            if et not in ("entry", "exit"):
                continue                      # not a crossing; nothing to relay
            crossed = "in" if et == "entry" else "out"
            data = dict(ev.get("data") or {})
            tid = data.get("track")

            self.tw_metrics.bump("crossings_detected")

            if want != "any" and want != crossed:
                self.tw_metrics.bump("filtered_wrong_direction")
                dbg.debug(event="FILTER", camera=self.cam_id, track=tid,
                          crossed=crossed, want=want, verdict="WRONG_DIRECTION")
                continue

            now = self._now()
            scope = self._scope()
            last = (self._last_alert if scope == "wire"
                    else self._last_alert_by_track.get(tid, 0.0))
            if cooldown > 0 and last and (now - last) < cooldown:
                self.tw_metrics.bump("filtered_cooldown")
                dbg.debug(event="FILTER", camera=self.cam_id, track=tid,
                          crossed=crossed, age=now - last, cooldown=cooldown,
                          scope=scope, verdict="COOLDOWN")
                continue
            self._last_alert = now
            if tid is not None:
                self._last_alert_by_track[tid] = now
                self._prune(now, cooldown)

            # Drop head-count's running totals — they are meaningless for a trip wire
            # and would show up in the alert payload as if they meant something.
            data.pop("in", None)
            data.pop("out", None)
            data["direction"] = crossed

            self.crossings += 1
            data["crossings"] = self.crossings
            self.tw_metrics.bump("crossings_alerted")
            dbg.info(event="CROSSING", camera=self.cam_id, track=tid,
                     direction=crossed, want=want, total=self.crossings,
                     verdict="ALERT")

            events.append({
                "event_type": "crossing",
                "label": f"Trip Wire Crossed ({crossed})",
                "value": 1,
                "data": data,
                "snapshot": True,
            })

        self.tw_metrics.maybe_report()

        # Relabel the inherited overlay: head-count draws a running IN/OUT tally, which
        # is not what this module means.
        for ov in overlays:
            if ov.get("kind") == "line":
                ov["color"] = "dnd"
                ov["label"] = ("TRIP WIRE"
                               + ("" if want == "any" else f" ({want})")
                               + f"  {self.crossings}")
        return events, overlays
