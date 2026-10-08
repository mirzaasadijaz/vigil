"""
Head Counting / Entry-Exit — directional people counting across a line.

PHASE 1 REWRITE. The previous rule was: take the bounding-box CENTROID, compute its
side of the line, and count a crossing the moment that sign flipped between two
consecutive observations of the same track id. That is one frame of evidence, from a
reference point that does not sit on the floor, keyed on an id that ByteTrack is not
guaranteed to preserve. It under-counted (any id change or dropped frame near the line
silently discarded the crossing, because a fresh id has no recorded previous side) and
over-counted (jitter across the line, or someone standing on it, produced repeats).

What replaces it:

  reference point   The FOOT POINT (bottom-centre of the box) instead of the centroid.
                    A person's floor position is what actually crosses a doorway line;
                    the centroid sits at chest height and, under any camera tilt, leads
                    or lags the feet — and it jumps when the box is clipped at a frame
                    edge or by an occluder.

  crossing band     A configurable hysteresis band of half-width `crossing_band` around
                    the line, expressed as a normalised perpendicular distance so it is
                    resolution-independent like the rest of the geometry. A track is
                    "firmly" on a side only outside the band; inside it, it is NEAR_LINE
                    and uncommitted.

  state machine     BEFORE -> NEAR_LINE -> AFTER, per track, with the origin side latched
                    on entry. A count is committed only on a genuine side change relative
                    to the latched origin side.

  confirmation      The far side must be observed for `confirm_observations` consecutive
                    frames. This is what rejects jitter, one-frame detector artifacts and
                    a person oscillating on the line, without needing a large band.

  id hand-off       When a new track id appears close to where a recently lost track was,
                    it inherits that track's crossing state. This recovers the crossings
                    that ByteTrack id churn used to discard outright.

  debounce          A per-TRACK cooldown, never a global one, so two people crossing at
                    the same moment are both counted while one person bouncing on the
                    line is not.

Direction semantics are UNCHANGED from the previous implementation: moving from the
negative side of `side_of_line` to the positive side is an `entry`, the reverse is an
`exit`. `entries` / `exits` remain plain attributes because the perception worker
restores persisted totals by assigning to them directly.
"""

from __future__ import annotations

import os
import time
from collections import deque

from backend.services.analytics import headcount_debug as hcdbg
from backend.services.analytics.base import (
    Analytic,
    line_normal,
    segment_position,
    signed_distance_to_line,
)

# ── defaults (env-overridable globally, per-camera-overridable via module_config) ──
# Every one of these can be set per camera by adding the same key to that camera's
# headcount entry in camera_devices.module_config, e.g.
#     {"module": "headcount", "enabled": true, "line": [...], "crossing_band": 0.04}
DEF_BAND = float(os.environ.get("HC_CROSSING_BAND", "0.03"))
DEF_CONFIRM = int(os.environ.get("HC_CONFIRM_OBSERVATIONS", "2"))
DEF_MIN_AGE = int(os.environ.get("HC_MIN_TRACK_AGE", "2"))
DEF_MIN_DISP = float(os.environ.get("HC_MIN_DISPLACEMENT", "0.05"))
DEF_DIR_CONSIST = float(os.environ.get("HC_MIN_DIRECTION_CONSISTENCY", "0.6"))
DEF_COOLDOWN = float(os.environ.get("HC_CROSSING_COOLDOWN", "2.0"))
DEF_HANDOFF_RADIUS = float(os.environ.get("HC_HANDOFF_RADIUS", "0.10"))
DEF_HANDOFF_TTL = float(os.environ.get("HC_HANDOFF_TTL", "1.5"))
# A genuine ByteTrack id switch leaves a gap of a frame or three. Anything longer is a
# DIFFERENT person who happens to be standing where the last one was — inheriting their
# crossing state would fabricate or suppress a count. Proximity alone is not enough.
DEF_HANDOFF_MAX_GAP = int(os.environ.get("HC_HANDOFF_MAX_GAP", "3"))
# The search radius has to scale with how fast the lost track was moving and how many
# frames it has been gone: a walker covers real ground across a dropped frame. A flat
# radius either loses fast walkers or adopts strangers, so budget speed * gap instead.
DEF_HANDOFF_SPEED_TOL = float(os.environ.get("HC_HANDOFF_SPEED_TOL", "1.5"))
DEF_STATE_TTL = float(os.environ.get("HC_STATE_TTL", "3.0"))
DEF_REFERENCE = os.environ.get("HC_REFERENCE_POINT", "foot")     # "foot" | "centroid"
# How far past the drawn endpoints a crossing may still count, as a fraction of the
# line's length. The line is a finite segment the operator drew across a doorway, but
# the maths behind it is an infinite line — without this bound, someone walking around
# the END of the line is counted as crossing it. A small margin absorbs imprecision in
# where the line was drawn; set to a large number to restore the old infinite behaviour.
DEF_SEGMENT_MARGIN = float(os.environ.get("HC_SEGMENT_MARGIN", "0.05"))
# Draw the crossing band on the live view. On by default: the band is where the counting
# tolerance actually sits, so an operator positioning a line needs to see it — it is not
# a debugging aid. Set show_band=false per camera (or HC_SHOW_BAND=0) to hide it.
DEF_SHOW_BAND = os.environ.get("HC_SHOW_BAND", "1").strip().lower() not in (
    "0", "false", "no", "off")

# A picture on every entry and exit. ON by default: an entry-exit log without pictures
# cannot answer "who was that", which is usually the reason for keeping one. Turn it off
# per camera on a busy door if the disk cost outweighs the evidence.
DEF_SNAPSHOT = os.environ.get("HEADCOUNT_SNAPSHOT", "1").strip().lower() not in (
    "0", "false", "no", "off")

# Swap which side of the line counts as IN.
#
# `side_of_line` is signed, and its sign depends on the ORDER the two points were drawn
# in - which the operator cannot see and has no reason to think about. So half the time
# a correctly-drawn line counts the door backwards, and there is no way to tell from the
# camera editor which half you are in. The fix is a toggle, not a convention: the
# operator watches one person walk in, sees IN or OUT increment, and flips this if it
# was wrong. Per camera, because every line is drawn independently.
DEF_INVERT = os.environ.get("HC_INVERT", "0").strip().lower() not in (
    "0", "", "false", "no", "off")

# Swap the words IN and OUT on the LIVE-FEED OVERLAY only.
#
# This is the server-side half of the display swap that `frontend/src/lib/modules.ts`
# applies to the alerts list. It exists because the overlay is burned onto the video by
# the capture thread, so it never sees a frontend relabelling and would otherwise
# contradict the alerts screen about the same crossing.
#
# It changes NOTHING else: the counters, the stored event type, the totals rebuilt from
# alert_logs and /api/analytics/summary all keep the original sense. It moves two words.
#
# !! THIS AND `invert_direction` ARE ALTERNATIVES, NOT COMPANIONS.
#    invert_direction flips the EVENT, so the label, the counters, the overlay and the
#    analytics all move together - it is the correct fix and needs nothing here.
#    This swaps only what is written on the screen, and leaves the data saying the
#    opposite. Enable both and they cancel out.
DEF_SWAP_LABELS = os.environ.get("HC_SWAP_LABELS", "0").strip().lower() not in (
    "0", "", "false", "no", "off")

# How near the line (in band multiples) a track must be before it is worth tracing.
INTEREST_FACTOR = 4.0
HIST_LEN = 12

BEFORE = "BEFORE"
NEAR_LINE = "NEAR_LINE"
AFTER = "AFTER"

# Not a rejection — the track is on the far side but has not yet been seen there for
# `confirm_observations` frames. It is the normal state one frame before a commit, so it
# is traced at DEBUG and must NOT inflate the rejected_crossings metric.
PENDING_REASONS = {"unconfirmed_far_side"}


class _TrackState:
    """Per-track crossing state. Kept in the consumer, never in the DB."""

    __slots__ = ("tid", "state", "origin_side", "origin_d", "far_streak", "far_side",
                 "obs", "first_frame", "last_frame", "last_seen", "last_foot", "last_d",
                 "d_hist", "last_commit_ts", "last_reject", "conf", "speed",
                 "crossings", "min_abs_d", "reached", "cross_seg_t", "latch_min_d")

    def __init__(self, tid, frame_no: int, now: float):
        self.tid = tid
        self.state = BEFORE
        self.origin_side: int | None = None
        self.origin_d: float = 0.0
        self.far_streak = 0
        self.far_side: int | None = None
        self.obs = 0
        self.first_frame = frame_no
        self.last_frame = frame_no
        self.last_seen = now
        self.last_foot = (0.0, 0.0)
        self.last_d = 0.0
        self.d_hist: deque = deque(maxlen=HIST_LEN)
        self.last_commit_ts = 0.0
        self.last_reject: str | None = None
        self.conf = 0.0
        self.speed = 0.0          # EMA of per-frame foot displacement (normalised)
        self.crossings = 0        # commits made by this track (for the TRACK_END line)
        self.min_abs_d = 1e9      # closest approach to the line, ever
        self.reached = BEFORE     # furthest state this track ever reached
        self.cross_seg_t = None   # position along the drawn line where it crossed
        self.latch_min_d = None   # closest approach since the origin side was latched

    def inherit(self, other: "_TrackState") -> None:
        """Adopt a lost track's crossing context after an id change."""
        self.state = other.state
        self.origin_side = other.origin_side
        self.origin_d = other.origin_d
        self.far_streak = other.far_streak
        self.far_side = other.far_side
        self.obs = other.obs
        self.first_frame = other.first_frame
        self.d_hist = deque(other.d_hist, maxlen=HIST_LEN)
        self.last_commit_ts = other.last_commit_ts
        self.speed = other.speed
        self.crossings = other.crossings
        self.min_abs_d = other.min_abs_d
        self.reached = other.reached
        self.cross_seg_t = other.cross_seg_t
        self.latch_min_d = other.latch_min_d


class HeadCount(Analytic):
    module_id = "headcount"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        # Restored from AlertLog history by the perception worker — keep the names.
        self.entries = 0
        self.exits = 0
        self.tracks: dict[object, _TrackState] = {}
        self.frame_no = 0
        self.metrics = hcdbg.Metrics(cam_id)

    # ── injectable clock so the test harness is deterministic ────────────────
    def _now(self) -> float:
        return time.time()

    # ── config access: per-camera value if present, else the global default ──
    def _num(self, key: str, default: float) -> float:
        v = self.config.get(key)
        if v is None or v == "":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _int(self, key: str, default: int) -> int:
        return int(self._num(key, default))

    def _bool(self, key: str, default: bool) -> bool:
        v = self.config.get(key)
        if v is None or v == "":
            return default
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in ("0", "false", "no", "off")

    def _reference_point(self, box) -> tuple[float, float]:
        """Where on the person do we consider them to 'be'?

        Foot point (bottom-centre) by default: it is the person's position on the floor,
        which is what a doorway line actually divides. `centroid` restores the old
        behaviour for a camera where the feet are never visible (e.g. a waist-height
        counter blocking the bottom of frame)."""
        x1, y1, x2, y2 = box
        if str(self.config.get("reference_point") or DEF_REFERENCE).lower() == "centroid":
            return (x1 + x2) / 2.0, (y1 + y2) / 2.0
        return (x1 + x2) / 2.0, y2

    # ── validation of a candidate crossing ───────────────────────────────────
    def _direction_consistency(self, st: _TrackState, want_sign: float) -> float:
        """Fraction of recent per-observation steps that moved in the committing
        direction. Rejects a track whose distance-to-line is oscillating rather than
        travelling."""
        hist = list(st.d_hist)
        if len(hist) < 3:
            return 1.0
        steps = [hist[i + 1] - hist[i] for i in range(len(hist) - 1)]
        moving = [s for s in steps if abs(s) > 1e-6]
        if not moving:
            return 0.0
        agree = sum(1 for s in moving if (s > 0) == (want_sign > 0))
        return agree / len(moving)

    def _validate(self, st: _TrackState, d: float, now: float,
                  min_disp: float, dir_consist: float,
                  confirm: int, min_age: int, cooldown: float,
                  seg_t: float, seg_margin: float) -> str | None:
        """None if the crossing may be committed, else the rejection reason."""
        # Order matters: everything that leaves the track PENDING must be tested before
        # anything that re-latches it. `outside_line_segment` re-latches, so testing it
        # first would discard a track that is briefly out of range while still
        # confirming — even though it crossed inside the drawn line.
        if st.far_streak < confirm:
            return "unconfirmed_far_side"
        if st.obs < min_age:
            return "track_too_young"
        where = st.cross_seg_t if st.cross_seg_t is not None else seg_t
        if not (-seg_margin <= where <= 1.0 + seg_margin):
            # Crossed the infinite extension of the line, not the drawn segment.
            return "outside_line_segment"
        if abs(d - st.origin_d) < min_disp:
            return "insufficient_movement"
        want = 1.0 if d > st.origin_d else -1.0
        if self._direction_consistency(st, want) < dir_consist:
            return "inconsistent_direction"
        if now - st.last_commit_ts < cooldown:
            return "cooldown"
        return None

    # ── id hand-off across a ByteTrack id change ─────────────────────────────
    def _adopt_lost_track(self, st: _TrackState, foot, now: float,
                          radius: float, ttl: float, band: float, max_gap: int,
                          speed_tol: float) -> bool:
        best, best_d, best_allow = None, None, 0.0
        for other in self.tracks.values():
            if other is st or other.last_frame >= self.frame_no:
                continue                                   # still alive this frame
            if now - other.last_seen > ttl:
                continue
            gap = self.frame_no - other.last_frame
            if gap > max_gap:
                continue                                   # too long a gap to be the same person
            allow = max(radius, other.speed * gap * speed_tol)
            dist = ((other.last_foot[0] - foot[0]) ** 2 + (other.last_foot[1] - foot[1]) ** 2) ** 0.5
            if dist <= allow and (best is None or dist < best_d):
                best, best_d, best_allow = other, dist, allow
        if best is None:
            return False
        st.inherit(best)
        self.tracks.pop(best.tid, None)
        if abs(best.last_d) <= band * INTEREST_FACTOR:
            self.metrics.bump("track_id_switches_near_line")
        hcdbg.info(event="ID_HANDOFF", camera=self.cam_id, track=st.tid,
                   from_track=best.tid, distance=best_d, allowed=best_allow,
                   gap=self.frame_no - best.last_frame, state=st.state,
                   origin_side=st.origin_side)
        return True

    # ── main entry point ─────────────────────────────────────────────────────
    def process(self, tracks, faces, shape):
        h, w = shape
        line = self.config.get("line")
        if not line or len(line) < 2:
            return [], []
        (ax, ay), (bx, by) = line[0], line[1]

        band = self._num("crossing_band", DEF_BAND)
        confirm = self._int("confirm_observations", DEF_CONFIRM)
        min_age = self._int("min_track_age", DEF_MIN_AGE)
        min_disp = self._num("min_displacement", DEF_MIN_DISP)
        dir_consist = self._num("min_direction_consistency", DEF_DIR_CONSIST)
        cooldown = self._num("crossing_cooldown", DEF_COOLDOWN)
        handoff_r = self._num("handoff_radius", DEF_HANDOFF_RADIUS)
        handoff_ttl = self._num("handoff_ttl", DEF_HANDOFF_TTL)
        handoff_gap = self._int("handoff_max_gap", DEF_HANDOFF_MAX_GAP)
        handoff_speed_tol = self._num("handoff_speed_tol", DEF_HANDOFF_SPEED_TOL)
        seg_margin = self._num("segment_margin", DEF_SEGMENT_MARGIN)
        state_ttl = self._num("state_ttl", DEF_STATE_TTL)

        now = self._now()
        self.frame_no += 1
        events = []

        # Record exactly how this camera is configured, so a captured log can be
        # interpreted (and replayed) without also needing the database row.
        if hcdbg.debug_enabled():
            hcdbg.session(self.cam_id, {
                "shape": f"{w}x{h}", "line": line, "reference_point":
                    str(self.config.get("reference_point") or DEF_REFERENCE).lower(),
                "crossing_band": band, "confirm_observations": confirm,
                "min_track_age": min_age, "min_displacement": min_disp,
                "min_direction_consistency": dir_consist,
                "crossing_cooldown": cooldown, "handoff_radius": handoff_r,
                "handoff_max_gap": handoff_gap, "handoff_ttl": handoff_ttl,
                "handoff_speed_tol": handoff_speed_tol, "state_ttl": state_ttl,
                "segment_margin": seg_margin,
            }, extra={"totals_in": self.entries, "totals_out": self.exits})

        for t in tracks:
            tid = t["id"]
            fx, fy = self._reference_point(t["box"])
            foot = (fx / w, fy / h)
            d = signed_distance_to_line(foot[0], foot[1], ax, ay, bx, by)
            seg_t = segment_position(foot[0], foot[1], ax, ay, bx, by)
            side = 1 if d > band else (-1 if d < -band else 0)

            st = self.tracks.get(tid)
            if st is None:
                st = _TrackState(tid, self.frame_no, now)
                self.tracks[tid] = st
                self.metrics.bump("tracks_seen")
                # A brand-new id near where a track was just lost is almost certainly
                # the same person re-issued: inherit rather than start blind.
                self._adopt_lost_track(st, foot, now, handoff_r, handoff_ttl, band,
                                       handoff_gap, handoff_speed_tol)
            elif st.last_frame < self.frame_no - 1:
                self.metrics.bump("temporary_track_loss")
                hcdbg.trace(event="TRACK_REAPPEARED", camera=self.cam_id, track=tid,
                            missed_frames=self.frame_no - st.last_frame - 1)

            if st.obs:
                step = ((foot[0] - st.last_foot[0]) ** 2
                        + (foot[1] - st.last_foot[1]) ** 2) ** 0.5
                gap = max(1, self.frame_no - st.last_frame)
                st.speed = 0.7 * st.speed + 0.3 * (step / gap) if st.speed else step / gap
            st.obs += 1
            st.last_frame = self.frame_no
            st.last_seen = now
            st.last_foot = foot
            st.last_d = d
            st.conf = float(t.get("conf") or 0.0)
            st.d_hist.append(d)

            near = abs(d) <= band * INTEREST_FACTOR
            if near:
                self.metrics.bump("tracks_near_line")
            st.min_abs_d = min(st.min_abs_d, abs(d))
            # Where along the drawn line this track passed = where it came CLOSEST to
            # the line. Recording it at the first far-side sighting instead would be
            # wrong for anyone moving diagonally: by then they have already drifted.
            if st.latch_min_d is None or abs(d) < st.latch_min_d:
                st.latch_min_d = abs(d)
                st.cross_seg_t = seg_t
            prev_state = st.state

            if side == 0:
                # Inside the band — uncommitted. Deliberately does NOT clear the
                # crossing point: being in the band IS the moment of crossing, and it
                # is the position we want to judge the segment bound on.
                st.state = NEAR_LINE
                st.far_streak = 0
                st.far_side = None
            elif st.origin_side is None:
                # First firm observation: this is simply where the track started.
                st.origin_side = side
                st.origin_d = d
                st.state = BEFORE
            elif side == st.origin_side:
                # Same side as the latched origin: never crossed, or turned back.
                if prev_state == NEAR_LINE:
                    hcdbg.info(event="TURNED_BACK", camera=self.cam_id, track=tid,
                               side=side, foot=foot, line_distance=d)
                # Stay AFTER once a crossing has been committed — the person really is
                # past the line. Only a fresh approach through the band re-arms them.
                st.state = AFTER if prev_state == AFTER else BEFORE
                st.origin_d = d if abs(d) > abs(st.origin_d) else st.origin_d
                st.far_streak = 0
                st.far_side = None
                # Back on the side they started: whatever approach they made is over,
                # so the next one measures its own crossing point from scratch.
                st.cross_seg_t = None
                st.latch_min_d = None
            else:
                # Opposite side of the latched origin — a crossing candidate.
                if st.far_side == side:
                    st.far_streak += 1
                else:
                    st.far_side = side
                    st.far_streak = 1
                    self.metrics.bump("crossing_candidates")
                st.state = AFTER

                reason = self._validate(st, d, now, min_disp, dir_consist,
                                        confirm, min_age, cooldown,
                                        seg_t, seg_margin)
                if reason is None:
                    into = side > 0
                    if self._bool("invert_direction", DEF_INVERT):
                        into = not into
                    direction = "entry" if into else "exit"
                    if direction == "entry":
                        self.entries += 1
                    else:
                        self.exits += 1
                    st.last_commit_ts = now
                    st.last_reject = None
                    st.crossings += 1
                    self.metrics.bump("crossings_committed")
                    self.metrics.bump("entries" if direction == "entry" else "exits")
                    hcdbg.info(event="COMMIT", camera=self.cam_id, track=tid,
                               state=f"{prev_state}->{AFTER}", decision=direction.upper(),
                               foot=foot, line_distance=d, origin_d=st.origin_d,
                               movement=abs(d - st.origin_d), track_age=st.obs,
                               far_streak=st.far_streak, direction=side,
                               seg_t=seg_t,
                               total_in=self.entries, total_out=self.exits)
                    events.append({
                        "event_type": direction,
                        "value": 1,
                        # A picture of every entry and exit.
                        #
                        # Head-count rides the FAST lane, and fast-lane tasks carry no
                        # JPEG - encoding one 10x a second per camera to serve an event
                        # that fires a few times an hour would be pure waste. Instead the
                        # task carries the ring sequence of the frame it was built from,
                        # and the writer encodes THAT EXACT FRAME on demand, falling back
                        # to latest_jpeg() if the ring has moved on. So the cost is paid
                        # per EVENT, not per frame. Trip wire has used this path since it
                        # shipped; this is the same mechanism.
                        #
                        # Per camera, because the cost is real on a busy door: a full-
                        # resolution encode and a file per crossing, both ways. A door
                        # doing 1000 crossings a day writes a few hundred MB a day.
                        "snapshot": self._bool("snapshot", DEF_SNAPSHOT),
                        # `track` is load-bearing: the perception writer uses it to
                        # attribute the event to a recognised employee.
                        "data": {"track": tid, "in": self.entries, "out": self.exits,
                                 "line_distance": round(d, 4),
                                 "movement": round(abs(d - st.origin_d), 4),
                                 "track_age": st.obs},
                    })
                    # Re-latch on the far side so the same track can legitimately
                    # cross back later, subject to the per-track cooldown.
                    st.origin_side = side
                    st.origin_d = d
                    st.far_streak = 0
                    st.far_side = None
                    st.cross_seg_t = None; st.latch_min_d = None
                else:
                    if reason in ("cooldown", "outside_line_segment"):
                        # Both settle the track rather than leaving it pending: accept
                        # the position without counting, so the same move is not
                        # re-evaluated (and re-logged) on every subsequent frame.
                        # They are counted separately though — an out-of-segment
                        # crossing is not a prevented duplicate, and conflating them
                        # made a short line look like a debounce problem.
                        if reason == "cooldown":
                            self.metrics.bump("duplicate_crossings_prevented")
                        st.origin_side = side
                        st.origin_d = d
                        st.far_streak = 0
                        st.far_side = None
                    fields = dict(camera=self.cam_id, track=tid,
                                  state=f"{prev_state}->{st.state}", reason=reason,
                                  foot=foot, line_distance=d, origin_d=st.origin_d,
                                  movement=abs(d - st.origin_d), track_age=st.obs,
                                  far_streak=st.far_streak, direction=side,
                                  seg_t=seg_t)
                    if reason in PENDING_REASONS:
                        # Still accumulating evidence — trace it, do not score it.
                        hcdbg.trace(event="PENDING", decision="PENDING", **fields)
                    elif st.last_reject != reason:
                        # Log a genuine rejection once per reason, not once per frame.
                        st.last_reject = reason
                        # A cooldown rejection is a prevented duplicate, already counted
                        # above; counting it again as a rejection would double-count it.
                        if reason != "cooldown":
                            self.metrics.reject(reason)
                        hcdbg.info(event="REJECT", decision="REJECT", **fields)

            if st.state == NEAR_LINE or st.state == AFTER:
                st.reached = st.state if st.reached != AFTER else AFTER

            if prev_state != st.state and st.state != AFTER:
                hcdbg.info(event="STATE", camera=self.cam_id, track=tid,
                           state=f"{prev_state}->{st.state}", foot=foot,
                           line_distance=d, track_age=st.obs, decision="PENDING")
            # OBS is the replayable trajectory record. At TRACE every track on every
            # frame is written (so a captured log reconstructs the whole scene); at
            # DEBUG only tracks near the line, to keep tuning captures readable.
            if hcdbg.tracing() or (near and hcdbg.verbose()):
                emit = hcdbg.trace if not near else hcdbg.debug
                emit(event="OBS", camera=self.cam_id, track=tid, frame=self.frame_no,
                     box=tuple(round(v, 1) for v in t["box"]), foot=foot,
                     line_distance=d, seg_t=seg_t, side=side, state=st.state,
                     origin_side=st.origin_side, origin_d=st.origin_d,
                     far_streak=st.far_streak, track_age=st.obs, speed=st.speed,
                     conf=st.conf)

        self._purge(now, state_ttl)
        self.metrics.maybe_report(now)
        return events, self._overlays(line, band,
                                      self._bool("show_band", DEF_SHOW_BAND))

    def _purge(self, now: float, state_ttl: float) -> None:
        """Drop state for tracks that have been gone long enough that a reappearing id
        cannot plausibly be the same person. Bounded memory, no ghost tracks."""
        for tid in [k for k, st in self.tracks.items() if now - st.last_seen > state_ttl]:
            st = self.tracks.pop(tid, None)
            if st is None or not hcdbg.debug_enabled():
                continue
            # The single most useful line for post-hoc analysis: a track that got close
            # to the line, was observed plenty of times, and still counted nothing is
            # exactly the "why was this person missed?" case worth investigating.
            hcdbg.info(event="TRACK_END", camera=self.cam_id, track=tid,
                       observations=st.obs, crossings=st.crossings,
                       closest_approach=st.min_abs_d, reached=st.reached,
                       final_state=st.state, origin_side=st.origin_side,
                       last_reject=st.last_reject or "-", speed=st.speed,
                       frames=f"{st.first_frame}-{st.last_frame}")

    def _overlays(self, line, band: float, show_band: bool):
        # Display-only: show the counters under swapped headings. The values themselves
        # are untouched - see DEF_SWAP_LABELS for why this exists and what it does not do.
        if self._bool("swap_labels", DEF_SWAP_LABELS):
            label = f"IN {self.exits}  OUT {self.entries}"
        else:
            label = f"IN {self.entries}  OUT {self.exits}"
        ovs = [{"kind": "line", "points": line, "color": "online", "label": label}]
        if show_band and band > 0:
            # The two lines either side of the counting line are the crossing band: a
            # track is only "firmly" on a side once it is outside them. Drawing it makes
            # the counter's tolerance visible, which is what an operator needs when
            # deciding where to put the line — so it is NOT gated on diagnostic logging.
            (ax, ay), (bx, by) = line[0], line[1]
            nx, ny = line_normal(ax, ay, bx, by)
            for i, sign in enumerate((1, -1)):
                ovs.append({
                    "kind": "line", "color": "idle",
                    "points": [[ax + nx * band * sign, ay + ny * band * sign],
                               [bx + nx * band * sign, by + ny * band * sign]],
                    # Label one edge only, so the pair reads as a band rather than as
                    # two more counting lines.
                    "label": "BAND" if i == 0 else None,
                })
        return ovs
