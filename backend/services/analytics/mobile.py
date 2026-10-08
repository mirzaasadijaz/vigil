"""
Mobile Phone Usage Monitoring — phone-in-hand detection (Phase 2 rewrite).

The previous rule was: if a phone box's centre falls anywhere inside a person's bounding
box, flag that person as holding a phone; emit an event, rate-limited per track. Three
problems with that:

  association   A standing person's box is roughly 0.4 x 1.8 m of image. A phone on the
                desk in front of them, on a shelf behind them, or in the hand of somebody
                walking past all land "inside" it. Containment in a box that large is
                barely evidence of anything.

  temporal      One detection produced an event. Small-object detection on CCTV flickers
                constantly, so single-frame noise became alerts.

  hysteresis    There was no notion of an episode. The `holding_phone` flag was carried
                forward for 1.5 s and that was the entire memory of the system.

What replaces it:

  hand region   A phone is associated with a person only if it falls in the part of their
                box a hand-held phone can occupy — head down to hip, padded sideways
                because hands extend past the torso. Scored, not boolean, so the best
                candidate wins when two people are close and the score can be logged.

  size sanity   A "phone" that is large relative to the person is a foreground object or
                a bad box, not a phone in their hand.

  state machine NO_PHONE -> CANDIDATE -> CONFIRMED -> RELEASED, per TRACK, with evidence
                accumulated over a sliding window (`confirm_hits` of the last
                `confirm_window` evaluations) rather than a single frame.

  hysteresis    Separate confirm and release thresholds. Losing the phone for a frame or
                two does not end the episode; `release_misses` consecutive misses does.

  one alert     The event fires once, on the transition into CONFIRMED, with a per-track
                cooldown — not once per frame of a five-minute phone call.

Identity is optional. The consumer works from person tracks alone; if face recognition
happens to have bound an identity to the track, the perception worker attaches it to the
event afterwards. Somebody facing away, or too far for recognition, is still eligible.

State is per TRACK. There is deliberately no camera-level "phone in use" flag: several
people can be on their phones at once and each is tracked independently.
"""

from __future__ import annotations

import os
import time
from collections import deque

from backend.services.analytics import phone_debug as pdbg
from backend.services.analytics.base import Analytic


def phone_det_conf() -> float:
    """The detector's configured confidence floor, read lazily so this module does not
    import torch/ultralytics at analytics-import time."""
    try:
        from backend.services.detectors.phone import CONF
        return CONF
    except Exception:
        return 0.35

# ── defaults (env-overridable globally, per-camera via module_config) ─────────
# Association: the slice of a person's box a hand-held phone can occupy, as fractions of
# box height, plus a sideways pad as a fraction of box width.
# The band starts BELOW the top of the head. Starting at 0.00 admitted the face - and
# with it spectacles, which the detector readily calls a phone. A phone raised to the ear
# still sits below the crown, so this costs nothing for calls.
DEF_HAND_TOP = float(os.environ.get("PHONE_HAND_TOP", "0.08"))
DEF_HAND_BOTTOM = float(os.environ.get("PHONE_HAND_BOTTOM", "0.70"))
DEF_HAND_PAD = float(os.environ.get("PHONE_HAND_PAD", "0.20"))
# How far outside the hand band the score decays to zero, as a fraction of box height.
# The two sides are deliberately asymmetric. BELOW the band is the desk and the floor,
# where a hand can legitimately hang low, so the edge is soft. ABOVE it is the face,
# where the only things to find are spectacles and the detector's imagination - so that
# edge is nearly hard. A soft top edge scored a detection ON someone's face at 0.58,
# comfortably past the 0.35 association threshold.
DEF_HAND_FALLOFF = float(os.environ.get("PHONE_HAND_FALLOFF", "0.12"))
DEF_HAND_FALLOFF_TOP = float(os.environ.get("PHONE_HAND_FALLOFF_TOP", "0.03"))
# Minimum association score for a detection to belong to a person at all.
DEF_ASSOC_MIN = float(os.environ.get("PHONE_ASSOC_MIN", "0.35"))

# ── Evidence gate ────────────────────────────────────────────────────────────
# A flat confidence floor is the wrong instrument, and a field capture showed why:
# 41 of 49 discarded detections would have associated with a person - plausible phones
# in somebody's hand, scoring 0.15-0.33 against a 0.35 floor. Raising the floor lost
# calls, voice notes and covert photography; lowering it let furniture back in.
#
# The two signals are not independent. A WEAK detection sitting exactly where a hand is,
# at the right size and shape, is better evidence than a STRONG one at the edge of the
# body box. So the gate is the product: detector confidence x association quality.
#
#   conf 0.22, association 1.00 -> 0.22  admitted (a real phone, seen poorly)
#   conf 0.60, association 0.30 -> 0.18  rejected (a confident detection, wrong place)
#
# `min_conf` remains as a hard floor purely to discard pure noise before the geometry is
# even worth computing.
DEF_MIN_CONF = float(os.environ.get("PHONE_MIN_CONF", "0.15"))
DEF_MIN_COMBINED = float(os.environ.get("PHONE_MIN_COMBINED", "0.20"))
# A phone occupying more than this fraction of the person's box area is not a phone in
# their hand — it is a foreground object or a bad box.
DEF_MAX_SIZE_RATIO = float(os.environ.get("PHONE_MAX_SIZE_RATIO", "0.06"))
# Shape sanity. Measured on a field capture: 41% of accepted detections were WIDER than
# tall, and the operator reported spectacles being reported as phones. Glasses are a wide
# flat bar; so is a keyboard edge-on or a watch strap. A phone is portrait, square-ish
# when angled, or landscape when filming - but never a long thin bar. The bounds are
# deliberately generous so a landscape phone (photo/video, which we DO want) still fits.
# ...and the floor. `s_score` above only ever punished boxes that were too BIG, so
# anything tiny sailed through at full marks - which is exactly what a finger, a
# cigarette, or plain detector noise looks like.
#
# The floor is in DETECTION-FRAME PIXELS, not an area ratio like its sibling above. Area
# ratio is relative to the person's box, so the SAME 23x24 px phone measures 0.0119
# against a distant person and 0.0018 against one near the camera - a 6.7x swing for an
# identical object. It works as a CEILING; it is meaningless as a floor. Apparent size in
# pixels is stable, and it is what decides whether the detector could resolve the thing.
#
# 8 px, and this default is MEASURED, not guessed. From logs/phone_debug.log on
# 2026-08-28 - 33 confirmed phone_use events across four cameras, sized by the detection
# that confirmed each one:
#
#     4.1  4.5  4.8  6.6 px   <- removed by this floor. Physically impossible: the
#                                detector's finest head is stride 8 and
#                                backend/scripts/bench_phone_scale.py puts the collapse
#                                point near 16 px. A 4 px box is a quarter of that.
#     8.1  8.6 ... 64.4 px    <- kept, all 29 of them, including camera 50's genuinely
#                                small ~11-12 px phones, which is the case that made an
#                                earlier 12 px floor unusable (it would have cut 8 of 33).
#
# The removed four also carried the lowest confidences in the set (0.204-0.307), so two
# independent signals agree about them.
#
# Sweep from the same capture, if you want to move it:
#     min_px=6  removes 3/33     min_px=10 removes 7/33
#     min_px=8  removes 4/33     min_px=12 removes 8/33
#
# PER CAMERA in the admin panel, which matters here: camera 53's smallest genuine phone
# was 63 px, so it can safely run a floor of 20, while camera 50 needs it low. 0 = off.
DEF_MIN_PX = float(os.environ.get("PHONE_MIN_PX", "8"))
DEF_MAX_ASPECT = float(os.environ.get("PHONE_MAX_ASPECT", "2.2"))    # w/h
DEF_MAX_INV_ASPECT = float(os.environ.get("PHONE_MAX_INV_ASPECT", "3.2"))  # h/w

# Temporal confirmation: hits needed within a sliding window of evaluations.
# Chosen from a threshold sweep over a real capture, not by feel. The medium lane was
# measured running at 2.2 evaluations/second per camera (below its 5/s target, with five
# cameras on one GPU), so demanding 3 hits meant the phone had to stay visible for about
# two seconds of CONTINUOUS detection. On that capture 3 hits produced 1 alert and 2 hits
# produced 4, from the same footage. The brief asks for a QUICK alert on texting,
# scrolling and photographing, all of which are brief - so 2 is the better trade, with
# the association, shape and static gates carrying the precision.
DEF_CONFIRM_HITS = int(os.environ.get("PHONE_CONFIRM_HITS", "2"))
DEF_CONFIRM_WINDOW = int(os.environ.get("PHONE_CONFIRM_WINDOW", "5"))
# Consecutive misses that end an episode. Higher = stickier state.
DEF_RELEASE_MISSES = int(os.environ.get("PHONE_RELEASE_MISSES", "4"))
# Minimum seconds between phone_use events for the SAME track.
DEF_COOLDOWN = float(os.environ.get("PHONE_COOLDOWN", "30.0"))
# How long per-track state survives after the track disappears.
DEF_STATE_TTL = float(os.environ.get("PHONE_STATE_TTL", "5.0"))
# Draw the hand region on the live view. On by default, for the same reason the
# head-count band is: without it there is NO on-screen sign the module is running, and
# an operator cannot see where the system is actually looking for a phone. It is also
# the only way to judge hand_top / hand_bottom / hand_pad against a real scene.
DEF_SHOW_REGION = os.environ.get("PHONE_SHOW_REGION", "1").strip().lower() not in (
    "0", "false", "no", "off")

# ── Static-object suppression ────────────────────────────────────────────────
# Measured on a real office capture: 65% of all phone detections came from FIFTEEN fixed
# image locations, each hit 8-157 times over minutes and attributed to 36 different
# track ids between them. One object cannot be in 36 people's hands - those are
# keyboards, mice, wallets and dark rectangles on desks. A confidence floor alone does
# not fix it: even at conf >= 0.50, 42% of surviving detections were still furniture.
#
# The discriminator is MOVEMENT. A phone in use travels with the hand holding it; a
# desk object has a fixed address. Two filters, at two timescales:
#
#   static map      a grid cell hit `static_hits` times over `static_window` seconds is
#                   furniture, and detections there are dropped from then on. Catches
#                   persistent objects, but only after it has seen them for a while.
#   episode motion  within one phone-use episode the detections must span at least
#                   `min_motion_px`. Catches the FIRST alert, before the map has learned.
DEF_STATIC_CELL = float(os.environ.get("PHONE_STATIC_CELL", "32"))
DEF_STATIC_HITS = int(os.environ.get("PHONE_STATIC_HITS", "20"))
DEF_STATIC_WINDOW = float(os.environ.get("PHONE_STATIC_WINDOW", "90"))
DEF_STATIC_TTL = float(os.environ.get("PHONE_STATIC_TTL", "600"))
# A desk object OUTLIVES the people around it: in the field capture the fifteen furniture
# cells were hit by 36 different track ids between them. One person using a phone is one
# track, so requiring the address to have served several people protects genuine use.
# A cell that persists past `static_hard_window` is furniture regardless - nobody holds a
# phone at one pixel address for that long.
DEF_STATIC_MIN_TRACKS = int(os.environ.get("PHONE_STATIC_MIN_TRACKS", "3"))
DEF_STATIC_HARD_WINDOW = float(os.environ.get("PHONE_STATIC_HARD_WINDOW", "240"))
# Motion is OFF by default, and that is a correction of a mistake.
#
# Requiring the phone to move was introduced to reject desk objects, and it worked for
# them - but the behaviours that matter for security are texting, scrolling, reading and
# photographing, and ALL of those are performed with the phone held still. A field
# capture showed 61 evaluations with full temporal evidence (up to 5/5) and perfect
# association blocked solely by this gate, because at 10% of a 480 px person box it was
# demanding 48 px of travel from a hand that moved 0.2 px.
#
# Rejecting furniture is the static map's job (below): a desk object holds ONE address
# for minutes on end and outlives the people near it, which is a far safer signal than
# asking a hand to wave. Set min_motion_frac / min_motion_px above zero to re-enable.
# ── Printed / worn objects: stability RELATIVE TO THE BODY ──────────────────────
# A phone printed on a t-shirt defeats every gate above, and it is not a tuning failure:
# a printed phone IS phone-shaped, phone-sized and sits in the hand band. A field capture
# on 2026-08-28 shows one confirmed at conf 0.55, aspect 0.65, area_ratio 0.0149,
# association 1.00 - values indistinguishable from the genuine detections around it.
#
# Neither existing motion signal can see it:
#
#   motion()      absolute travel across the FRAME. A print on a walking person travels
#                 exactly as far as the person does, so it looks like vigorous movement.
#   static map    keyed on frame ADDRESS, which catches furniture. A print never holds
#                 one address, because the person carrying it is moving.
#
# What IS different is the offset WITHIN the person's box. A print is pinned to the
# torso: the same (dx, dy) on every frame for the whole track. A hand-held phone wanders
# - raised to read, lowered, tilted, swapped hands - and the person's own box changes
# shape as they walk, so even a phone "held still" while texting drifts relative to it.
#
# OFF by default (0). The mechanism and the measurement are here, but the threshold has
# not been calibrated on your footage and a guessed one would suppress real alerts -
# which is exactly how the size floor went wrong twice before it was measured. Every
# EVAL and STATE line now logs `body_drift`; capture a run containing the t-shirt case,
# compare its drift against the genuine ones, and set this between them.
DEF_BODY_DRIFT_MIN = float(os.environ.get("PHONE_BODY_DRIFT_MIN", "0"))
# ...and how many observations before that judgement may be made at all. A phone seen
# twice has not had a chance to drift; rejecting on that would just be a slower size gate.
DEF_BODY_DRIFT_OBS = int(os.environ.get("PHONE_BODY_DRIFT_OBS", "6"))

DEF_MIN_MOTION_FRAC = float(os.environ.get("PHONE_MIN_MOTION_FRAC", "0"))
DEF_MIN_MOTION_PX = float(os.environ.get("PHONE_MIN_MOTION_PX", "0"))

NO_PHONE = "NO_PHONE"
CANDIDATE = "CANDIDATE"
CONFIRMED = "CONFIRMED"
RELEASED = "RELEASED"

WINDOW_CAP = 32


class _PhoneState:
    """Per-track phone evidence. Lives in the consumer, never in the DB."""

    __slots__ = ("tid", "state", "window", "consecutive_misses", "first_seen",
                 "confirmed_at", "last_event_ts", "last_seen", "last_eval_frame",
                 "hits", "episodes", "best_conf", "best_assoc", "last_reason",
                 "ext", "positions", "best_hits", "body_ext", "body_obs",
                 "best_body_drift")

    def __init__(self, tid, now: float, window: int):
        self.tid = tid
        self.state = NO_PHONE
        self.window: deque = deque(maxlen=min(WINDOW_CAP, max(1, window)))
        self.consecutive_misses = 0
        self.first_seen: float | None = None      # first detection of this episode
        self.confirmed_at: float | None = None
        self.last_event_ts = 0.0
        self.last_seen = now
        self.last_eval_frame = -1
        self.hits = 0                              # lifetime, for the summary line
        self.episodes = 0
        self.best_conf = 0.0
        self.best_assoc = 0.0
        self.last_reason: str | None = None
        # Bounding extent of this episode's detections: [minx, miny, maxx, maxy].
        self.ext: list | None = None
        self.positions = 0        # observations contributing to `ext`
        # ...and the same extent measured INSIDE the person's box, which is what
        # separates a printed phone from a held one. See body_drift().
        self.body_ext: list | None = None
        self.body_obs = 0
        # Peak drift seen across the WHOLE track, kept past the per-episode reset.
        # Without it TRACK_END reported 0.0000 for every released track - the reset runs
        # before the line is written - so a capture could never show what a genuine
        # phone's drift looks like, which is the number needed to set the threshold.
        self.best_body_drift = 0.0
        self.best_hits = 0        # most evidence this track ever gathered

    def note_position(self, cx: float, cy: float) -> None:
        self.positions += 1
        if self.ext is None:
            self.ext = [cx, cy, cx, cy]
        else:
            self.ext[0] = min(self.ext[0], cx)
            self.ext[1] = min(self.ext[1], cy)
            self.ext[2] = max(self.ext[2], cx)
            self.ext[3] = max(self.ext[3], cy)

    def motion(self) -> float:
        """How far this episode's detections travelled, in pixels."""
        if self.ext is None:
            return 0.0
        return max(self.ext[2] - self.ext[0], self.ext[3] - self.ext[1])

    def note_body_offset(self, rx: float, ry: float) -> None:
        """Record where the phone sat INSIDE the person's box, 0-1 on each axis."""
        self.body_obs += 1
        if self.body_ext is None:
            self.body_ext = [rx, ry, rx, ry]
        else:
            self.body_ext[0] = min(self.body_ext[0], rx)
            self.body_ext[1] = min(self.body_ext[1], ry)
            self.body_ext[2] = max(self.body_ext[2], rx)
            self.body_ext[3] = max(self.body_ext[3], ry)

    def body_drift(self) -> float:
        """Spread of that offset over the episode, as a fraction of the person's box.

        Near zero means the object is PINNED to the body - printed on clothing, a badge,
        a lanyard. A held phone wanders."""
        if self.body_ext is None:
            return 0.0
        return max(self.body_ext[2] - self.body_ext[0],
                   self.body_ext[3] - self.body_ext[1])

    def hit_count(self) -> int:
        return sum(self.window)


class MobileUsage(Analytic):
    module_id = "mobile"

    def __init__(self, cam_id, config):
        super().__init__(cam_id, config)
        self.tracks: dict[object, _PhoneState] = {}
        self.frame_no = 0
        self.metrics = pdbg.Metrics(cam_id)
        # grid cell -> [hits, first_seen, last_seen]; see the static-object note above.
        self._static: dict = {}

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

    # ── the base-class entry point is deliberately inert ─────────────────────
    def process(self, tracks, faces, shape):
        """Phone detections do not arrive through the generic consumer path — they need
        the detector output, so the perception worker calls `process_phones` instead.
        Mirrors how the fire/smoke consumer is driven."""
        return [], []

    # ── static-object map ────────────────────────────────────────────────────
    def _static_key(self, cx: float, cy: float, cell: float):
        return (int(cx // cell), int(cy // cell))

    def is_static(self, cx: float, cy: float, now: float) -> bool:            # noqa: C901
        """Has this image location produced detections for long enough to be furniture?

        Read-only: recording is a separate step (`note_location`) that deliberately
        SKIPS detections belonging to a confirmed episode. Otherwise a genuine phone
        call held reasonably still would accumulate in its own cell and eventually
        blacklist itself — turning the false-positive fix into a false-negative one."""
        cell = self._num("static_cell", DEF_STATIC_CELL)
        hits_needed = self._int("static_hits", DEF_STATIC_HITS)
        window = self._num("static_window", DEF_STATIC_WINDOW)
        ttl = self._num("static_ttl", DEF_STATIC_TTL)

        min_tracks = self._int("static_min_tracks", DEF_STATIC_MIN_TRACKS)
        hard = self._num("static_hard_window", DEF_STATIC_HARD_WINDOW)

        ent = self._static.get(self._static_key(cx, cy, cell))
        if ent is None or now - ent[2] > ttl:
            return False
        hits, first, _last, tracks = ent
        span = now - first
        if hits < hits_needed or span < window:
            return False
        # Either several different people have "held" it - impossible for one phone -
        # or it has simply been there too long to be in anybody's hand.
        return len(tracks) >= min_tracks or span >= hard

    def note_location(self, cx: float, cy: float, now: float, track=None) -> None:
        """Record one detection into the furniture map, with who it was attributed to."""
        cell = self._num("static_cell", DEF_STATIC_CELL)
        ttl = self._num("static_ttl", DEF_STATIC_TTL)
        key = self._static_key(cx, cy, cell)
        ent = self._static.get(key)
        if ent is None or now - ent[2] > ttl:
            self._static[key] = [1, now, now, {track} if track is not None else set()]
            return                                  # new, or gone quiet long enough
        ent[0] += 1
        ent[2] = now
        if track is not None:
            ent[3].add(track)

    def _purge_static(self, now: float) -> None:
        ttl = self._num("static_ttl", DEF_STATIC_TTL)
        for k in [k for k, v in self._static.items() if now - v[2] > ttl]:
            self._static.pop(k, None)

    # ── association ──────────────────────────────────────────────────────────
    def _association(self, phone_box, person_box) -> tuple[float, dict]:
        """Score in [0,1] that this phone is in this person's hand, plus the components
        that produced it. Multiplicative, so any single hard failure zeroes the score —
        and the breakdown is logged so a decision is never opaque."""
        hand_top = self._num("hand_top", DEF_HAND_TOP)
        hand_bottom = self._num("hand_bottom", DEF_HAND_BOTTOM)
        hand_pad = self._num("hand_pad", DEF_HAND_PAD)
        falloff = self._num("hand_falloff", DEF_HAND_FALLOFF)
        max_ratio = self._num("max_size_ratio", DEF_MAX_SIZE_RATIO)

        px1, py1, px2, py2 = person_box
        pw, ph = max(1e-6, px2 - px1), max(1e-6, py2 - py1)
        fx1, fy1, fx2, fy2 = phone_box
        fx, fy = (fx1 + fx2) / 2.0, (fy1 + fy2) / 2.0

        # Vertical: full score inside the hand band, decaying over `falloff` beyond it.
        vy = (fy - py1) / ph
        if hand_top <= vy <= hand_bottom:
            v_score = 1.0
        elif vy < hand_top:
            top_falloff = self._num("hand_falloff_top", DEF_HAND_FALLOFF_TOP)
            v_score = max(0.0, 1.0 - (hand_top - vy) / max(1e-6, top_falloff))
        else:
            v_score = max(0.0, 1.0 - (vy - hand_bottom) / max(1e-6, falloff))

        # Horizontal: full score inside the box, decaying across the sideways pad.
        if px1 <= fx <= px2:
            h_score = 1.0
        else:
            over = (px1 - fx) if fx < px1 else (fx - px2)
            h_score = max(0.0, 1.0 - over / max(1e-6, pw * hand_pad))

        # Size plausibility: a phone is small relative to the person holding it.
        area_ratio = (max(0.0, fx2 - fx1) * max(0.0, fy2 - fy1)) / (pw * ph)
        s_score = 1.0 if area_ratio <= max_ratio else max(
            0.0, 1.0 - (area_ratio - max_ratio) / max(1e-6, max_ratio))
        # Too small to be a phone at all. Hard reject, like the shape gate: there is no
        # "slightly a phone" reading of a finger-sized box.
        min_px = self._num("min_px", DEF_MIN_PX)
        short_side = min(max(0.0, fx2 - fx1), max(0.0, fy2 - fy1))
        too_small = min_px > 0 and short_side < min_px
        if too_small:
            s_score = 0.0

        # Shape plausibility: reject long thin bars (spectacles, straps, keyboard edges).
        dw, dh = max(1e-6, fx2 - fx1), max(1e-6, fy2 - fy1)
        aspect = dw / dh
        a_score = 1.0
        if aspect > self._num("max_aspect", DEF_MAX_ASPECT):
            a_score = 0.0
        elif (1.0 / aspect) > self._num("max_inv_aspect", DEF_MAX_INV_ASPECT):
            a_score = 0.0

        return v_score * h_score * s_score * a_score, {
            "v": round(v_score, 3), "h": round(h_score, 3), "s": round(s_score, 3),
            "a": round(a_score, 3), "vy": round(vy, 3),
            "aspect": round(aspect, 2), "area_ratio": round(area_ratio, 4),
            "short_px": round(short_side, 1), "min_px": min_px,
            "too_small": too_small,
        }

    def _best_person(self, phone_box, tracks):
        """Assign a detection to at most one person: the best-scoring candidate.

        Returns the argmax even when it scores ZERO, so a rejection can say WHICH person
        was closest and why the phone was not theirs. Reporting "not associated with any
        person" for a phone lying on the desk in front of somebody would hide the actual
        reason, and the reason is the whole point of the log."""
        best, best_score, best_parts = None, -1.0, {}
        runner_up = 0.0
        for t in tracks:
            score, parts = self._association(phone_box, t["box"])
            if score > best_score:
                runner_up = max(0.0, best_score)
                best, best_score, best_parts = t, score, parts
            elif score > runner_up:
                runner_up = score
        return best, max(0.0, best_score), best_parts, runner_up

    # ── main entry point ─────────────────────────────────────────────────────
    def process_phones(self, tracks, phone_dets, shape):
        """One phone evaluation. `phone_dets` is [{box, conf, source}] in det-frame
        pixels; `tracks` are the person tracks from the shared backbone.

        Returns (events, overlays). Called only on frames where phone inference ran, so
        the temporal window counts PHONE EVALUATIONS, not camera frames."""
        h, w = shape
        now = self._now()
        self.frame_no += 1
        self.metrics.bump("frames_evaluated")
        self.metrics.bump("raw_detections", len(phone_dets or []))

        assoc_min = self._num("association_min", DEF_ASSOC_MIN)
        confirm_hits = self._int("confirm_hits", DEF_CONFIRM_HITS)
        confirm_window = self._int("confirm_window", DEF_CONFIRM_WINDOW)
        release_misses = self._int("release_misses", DEF_RELEASE_MISSES)
        cooldown = self._num("cooldown", DEF_COOLDOWN)
        state_ttl = self._num("state_ttl", DEF_STATE_TTL)
        motion_floor = self._num("min_motion_px", DEF_MIN_MOTION_PX)
        motion_frac = self._num("min_motion_frac", DEF_MIN_MOTION_FRAC)
        drift_min = self._num("body_drift_min", DEF_BODY_DRIFT_MIN)
        drift_obs = self._int("body_drift_obs", DEF_BODY_DRIFT_OBS)

        # The acting confidence floor. It MUST default to the detector's own configured
        # floor, not to zero: with diagnostics on, detectors/phone.py deliberately runs
        # below that floor so near-misses are visible in the log, and the consumer is
        # what puts the real threshold back. Defaulting to 0 here meant switching
        # diagnostics ON silently lowered the live threshold from 0.35 to 0.15 - the
        # system got worse precisely while it was being debugged.
        # `phone_conf` is kept as an alias for the hard floor so existing per-camera
        # configuration keeps working.
        min_conf = self._num("phone_conf", self._num("min_conf", DEF_MIN_CONF))
        min_combined = self._num("min_combined", DEF_MIN_COMBINED)
        if pdbg.debug_enabled():
            pdbg.session(self.cam_id, {
                "shape": f"{w}x{h}", "association_min": assoc_min,
                # Without these a captured log cannot say what threshold was in force.
                "min_conf": min_conf, "min_combined": min_combined,
                "detector_conf": phone_det_conf(),
                "max_aspect": self._num("max_aspect", DEF_MAX_ASPECT),
                "max_inv_aspect": self._num("max_inv_aspect", DEF_MAX_INV_ASPECT),
                "hand_top": self._num("hand_top", DEF_HAND_TOP),
                "hand_bottom": self._num("hand_bottom", DEF_HAND_BOTTOM),
                "hand_pad": self._num("hand_pad", DEF_HAND_PAD),
                "hand_falloff": self._num("hand_falloff", DEF_HAND_FALLOFF),
                "max_size_ratio": self._num("max_size_ratio", DEF_MAX_SIZE_RATIO),
                "min_px": self._num("min_px", DEF_MIN_PX),
                "body_drift_min": self._num("body_drift_min", DEF_BODY_DRIFT_MIN),
                "body_drift_obs": self._int("body_drift_obs", DEF_BODY_DRIFT_OBS),
                "confirm_hits": confirm_hits, "confirm_window": confirm_window,
                "release_misses": release_misses, "cooldown": cooldown,
                "state_ttl": state_ttl,
            })

        # ── 1. assign each detection to at most one person ───────────────────
        assigned: dict[object, dict] = {}          # track id -> best detection for it
        for det in (phone_dets or []):
            if det.get("conf", 1.0) < min_conf:
                # Seen by the detector, below the acting threshold. Recorded so a MISS
                # is visible: without this a near-miss leaves no trace and "the detector
                # never saw it" is indistinguishable from "the floor was set too high".
                #
                # The association is scored ANYWAY, purely for the log. It is free
                # geometry, and it is the difference between "we discarded 110 pieces of
                # junk" and "we discarded 110 plausible phones in people's hands" - which
                # point in opposite directions when choosing the threshold.
                self.metrics.bump("below_confidence")
                if pdbg.tracing():
                    who, sc, pt, _ru = self._best_person(det["box"], tracks)
                    pdbg.trace(event="DET", camera=self.cam_id, frame=self.frame_no,
                               phone=tuple(round(v, 1) for v in det["box"]),
                               phone_conf=det.get("conf", 0.0), decision="IGNORED",
                               reason="below_confidence", threshold=min_conf,
                               best_track=(who or {}).get("id", "-"),
                               association=round(sc, 3), **pt)
                continue
            bx1, by1, bx2, by2 = det["box"]
            dcx, dcy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
            det["_centre"] = (dcx, dcy)
            if self.is_static(dcx, dcy, now):
                self.note_location(dcx, dcy, now)      # keep the cell alive
                # A fixed address in the image. Not somebody's hand.
                self.metrics.bump("detections_unassociated")
                self.metrics.note("static_rejections", "static_object")
                pdbg.info(event="REJECT", camera=self.cam_id, frame=self.frame_no,
                          phone=tuple(round(v, 1) for v in det["box"]),
                          phone_conf=det.get("conf", 0.0), decision="REJECT",
                          reason="static_object", cell=self._static_key(
                              dcx, dcy, self._num("static_cell", DEF_STATIC_CELL)))
                continue
            person, score, parts, runner_up = self._best_person(det["box"], tracks)
            combined = score * det.get("conf", 0.0)
            if pdbg.tracing():
                pdbg.trace(event="DET", camera=self.cam_id, frame=self.frame_no,
                           phone=tuple(round(v, 1) for v in det["box"]),
                           phone_conf=det.get("conf", 0.0),
                           source=det.get("source", "?"),
                           best_track=(person or {}).get("id", "-"),
                           association=round(score, 3),
                           combined=round(combined, 3),
                           runner_up=round(runner_up, 3), **parts)
            if person is None or score < assoc_min or combined < min_combined:
                self.metrics.bump("detections_unassociated")
                if person is None:
                    reason = "phone_not_associated_with_person"
                elif parts.get("v", 1) <= 0:
                    reason = "phone_outside_upper_body"
                elif parts.get("a", 1) <= 0:
                    reason = "implausible_shape"
                elif score >= assoc_min:
                    # Well placed, but the detector was not confident enough for the
                    # combination to carry it.
                    reason = "weak_evidence"
                elif parts.get("s", 1) <= 0:
                    reason = "phone_too_large_for_hand"
                else:
                    reason = "association_below_threshold"
                self.metrics.reject(reason)
                pdbg.info(event="REJECT", camera=self.cam_id, frame=self.frame_no,
                          track=(person or {}).get("id", "-"),
                          phone=tuple(round(v, 1) for v in det["box"]),
                          phone_conf=det.get("conf", 0.0),
                          association=round(score, 3),
                          combined=round(combined, 3), decision="REJECT",
                          reason=reason, **parts)
                continue
            self.metrics.bump("detections_associated")
            tid = person["id"]
            det["_track"] = tid
            prev = assigned.get(tid)
            # One phone per person per evaluation: keep the strongest evidence.
            if prev is None or (score * det.get("conf", 0.0)) > (
                    prev["assoc"] * prev["det"].get("conf", 0.0)):
                assigned[tid] = {"det": det, "assoc": score, "parts": parts,
                                 "runner_up": runner_up}

        # ── 2. advance the per-track state machine ───────────────────────────
        events, overlays = [], []
        for t in tracks:
            tid = t["id"]
            st = self.tracks.get(tid)
            if st is None:
                st = _PhoneState(tid, now, confirm_window)
                self.tracks[tid] = st
                self.metrics.bump("tracks_seen")
            if st.window.maxlen != min(WINDOW_CAP, max(1, confirm_window)):
                # config edited live: keep the evidence, resize the window
                st.window = deque(st.window,
                                  maxlen=min(WINDOW_CAP, max(1, confirm_window)))
            st.last_seen = now
            st.last_eval_frame = self.frame_no

            hit = assigned.get(tid)
            st.window.append(1 if hit else 0)
            prev_state = st.state

            if hit:
                st.hits += 1
                st.consecutive_misses = 0
                hb = hit["det"]["box"]
                st.note_position((hb[0] + hb[2]) / 2.0, (hb[1] + hb[3]) / 2.0)
                # Where the phone sat inside the person, 0-1 per axis.
                _px1, _py1, _px2, _py2 = t["box"]
                _pw, _ph = max(1e-6, _px2 - _px1), max(1e-6, _py2 - _py1)
                st.note_body_offset(((hb[0] + hb[2]) / 2.0 - _px1) / _pw,
                                    ((hb[1] + hb[3]) / 2.0 - _py1) / _ph)
                st.best_body_drift = max(st.best_body_drift, st.body_drift())
                st.best_conf = max(st.best_conf, hit["det"].get("conf", 0.0))
                st.best_assoc = max(st.best_assoc, hit["assoc"])
                if st.first_seen is None:
                    st.first_seen = now
                if st.state in (NO_PHONE, RELEASED):
                    st.state = CANDIDATE
                    self.metrics.bump("phone_candidates")
            else:
                st.consecutive_misses += 1

            hits = st.hit_count()
            st.best_hits = max(st.best_hits, hits)

            # CANDIDATE -> CONFIRMED once the sliding window carries enough evidence
            # AND the phone has actually moved. A phone being USED travels with the hand
            # holding it; something that never shifts is on the desk. The episode stays
            # CANDIDATE rather than being thrown away, so it confirms as soon as it does
            # move.
            px1, _py1, px2, _py2 = t["box"]
            min_motion = max(motion_floor, (px2 - px1) * motion_frac)
            # Motion is only measurable once there are at least two observations; with
            # confirm_hits=1 the operator has opted out of temporal evidence entirely
            # and there is nothing to measure.
            # Pinned to the body: printed on clothing, a badge, a lanyard.
            #
            # HOLDING the decision is the whole mechanism, and it costs latency. Drift is
            # only meaningful over several observations, but confirmation normally fires
            # at `confirm_hits` (2) - which arrives first, so a gate that merely CHECKED
            # drift could never speak: the episode was already CONFIRMED and the event
            # already emitted. So while the gate is on and the evidence is not yet in,
            # the candidate waits rather than confirming.
            #
            # The cost is real: an alert needs `body_drift_obs` detections instead of
            # `confirm_hits`, which at ~5 fps is roughly a second longer. That is the
            # price of telling a printed phone from a held one, and it is why the gate
            # is opt-in rather than on by default.
            if (drift_min > 0 and st.state == CANDIDATE and hits >= confirm_hits
                    and st.body_obs < drift_obs):
                pass                      # not enough evidence yet - hold, do not confirm
            elif (drift_min > 0 and st.state == CANDIDATE and hits >= confirm_hits
                    and st.body_obs >= drift_obs and st.body_drift() < drift_min):
                self.metrics.note("motion_rejections", "fixed_to_body")
                if st.last_reason != "fixed_to_body":
                    st.last_reason = "fixed_to_body"
                    pdbg.info(event="REJECT", camera=self.cam_id, frame=self.frame_no,
                              track=tid, decision="REJECT", reason="fixed_to_body",
                              body_drift=round(st.body_drift(), 4),
                              needed=drift_min, observations=st.body_obs,
                              temporal=f"{hits}/{len(st.window)}")
            elif (st.state == CANDIDATE and hits >= confirm_hits
                    and st.positions >= 2 and st.motion() < min_motion):
                self.metrics.note("motion_rejections", "phone_did_not_move")
                if st.last_reason != "phone_did_not_move":
                    st.last_reason = "phone_did_not_move"
                    pdbg.info(event="REJECT", camera=self.cam_id, frame=self.frame_no,
                              track=tid, decision="REJECT", reason="phone_did_not_move",
                              motion=round(st.motion(), 1),
                              needed=round(min_motion, 1),
                              temporal=f"{hits}/{len(st.window)}")
            elif st.state == CANDIDATE and hits >= confirm_hits:
                st.state = CONFIRMED
                st.confirmed_at = now
                st.episodes += 1
                self.metrics.bump("phone_confirmed")
                latency = (now - st.first_seen) if st.first_seen else 0.0
                if now - st.last_event_ts >= cooldown:
                    st.last_event_ts = now
                    self.metrics.bump("phone_use_events")
                    ev_conf = round(st.best_conf * st.best_assoc, 4)
                    events.append({
                        "event_type": "phone_use",
                        "value": ev_conf,
                        # `track` is load-bearing: the perception writer uses it to
                        # attribute the event to a recognised employee when one is bound.
                        "data": {"track": tid,
                                 "phone_confidence": round(st.best_conf, 4),
                                 "association_confidence": round(st.best_assoc, 4),
                                 "confidence": ev_conf,
                                 "evidence": f"{hits}/{len(st.window)}",
                                 "motion_px": round(st.motion(), 1),
                                 "body_drift": round(st.body_drift(), 4),
                                 "duration": round(latency, 2)},
                        "snapshot": True,
                    })
                else:
                    self.metrics.bump("duplicate_events_prevented")
                    pdbg.info(event="SUPPRESSED", camera=self.cam_id, track=tid,
                              decision="NO_EVENT", reason="cooldown",
                              since_last=round(now - st.last_event_ts, 2),
                              cooldown=cooldown)
                pdbg.info(event="STATE", camera=self.cam_id, frame=self.frame_no,
                          track=tid, state=f"{prev_state}->{CONFIRMED}",
                          decision="PHONE_USE",
                          phone_conf=round(st.best_conf, 3),
                          association=round(st.best_assoc, 3),
                          temporal=f"{hits}/{len(st.window)}",
                          motion=round(st.motion(), 1),
                          body_drift=round(st.body_drift(), 4),
                          body_obs=st.body_obs, latency=round(latency, 2))

            # CONFIRMED -> RELEASED after enough consecutive misses (hysteresis).
            elif st.state == CONFIRMED and st.consecutive_misses >= release_misses:
                st.state = RELEASED
                st.first_seen = None
                st.ext = None
                st.positions = 0
                st.body_ext = None
                st.body_obs = 0
                self.metrics.bump("phone_released")
                pdbg.info(event="STATE", camera=self.cam_id, frame=self.frame_no,
                          track=tid, state=f"{CONFIRMED}->{RELEASED}",
                          decision="RELEASE",
                          duration=round(now - (st.confirmed_at or now), 2),
                          misses=st.consecutive_misses)

            # A candidate that never gathered enough evidence and has gone quiet.
            elif (st.state == CANDIDATE and st.consecutive_misses >= release_misses):
                st.state = NO_PHONE
                st.first_seen = None
                st.ext = None
                st.positions = 0
                st.body_ext = None
                st.body_obs = 0
                st.best_conf = st.best_assoc = 0.0
                self.metrics.bump("temporal_rejections")
                if st.last_reason != "insufficient_evidence":
                    st.last_reason = "insufficient_evidence"
                    pdbg.info(event="REJECT", camera=self.cam_id, frame=self.frame_no,
                              track=tid, decision="REJECT",
                              reason="insufficient_evidence",
                              temporal=f"{hits}/{len(st.window)}",
                              needed=confirm_hits)

            if st.state == CONFIRMED:
                # Label EVERYTHING the decision was made from, on the alert picture as
                # well as the live view. A screenshot showing only an unlabelled box
                # around a person cannot answer the question an operator actually asks:
                # "what did it think was a phone, and how sure was it?" - which is the
                # only way to tell a real phone from the finger or cigarette that the
                # size gate above now exists to reject.
                x1, y1, x2, y2 = t["box"]
                assoc = round(hit["assoc"], 2) if hit else 0.0
                overlays.append({"kind": "box",
                                 "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                                 "color": "idle",
                                 "label": f"PHONE USE  assoc {assoc:.2f}",
                                 "snapshot": True})
                if hit:
                    bx1, by1, bx2, by2 = hit["det"]["box"]
                    det = hit["det"]
                    parts = hit.get("parts") or {}
                    # The phone box itself, labelled with the detector's confidence and
                    # the two numbers that decide plausibility.
                    overlays.append({
                        "kind": "box",
                        "box": [bx1 / w, by1 / h, bx2 / w, by2 / h],
                        "color": "dnd",
                        "label": (f"PHONE {det.get('conf', 0.0):.2f}  "
                                  f"area {parts.get('area_ratio', 0):.4f}  "
                                  f"ar {parts.get('aspect', 0):.2f}"),
                        "snapshot": True,
                    })

            if pdbg.verbose() and (hit or st.state != NO_PHONE):
                pdbg.debug(event="EVAL", camera=self.cam_id, frame=self.frame_no,
                           track=tid,
                           person=tuple(round(v, 1) for v in t["box"]),
                           phone=(tuple(round(v, 1) for v in hit["det"]["box"])
                                  if hit else "-"),
                           phone_conf=(hit["det"].get("conf", 0.0) if hit else 0.0),
                           association=(round(hit["assoc"], 3) if hit else 0.0),
                           state=st.state, prev_state=prev_state,
                           temporal=f"{hits}/{len(st.window)}",
                           body_drift=round(st.body_drift(), 4), body_obs=st.body_obs,
                           misses=st.consecutive_misses)

        # Feed the furniture map from EVERY detection, including confirmed ones.
        #
        # An earlier version skipped confirmed detections so a phone in use could not
        # blacklist itself — but that only worked while the motion gate kept furniture
        # out of CONFIRMED. With motion off (see the note on DEF_MIN_MOTION_FRAC) a desk
        # object confirms too, and a map that ignores confirmed detections never learns
        # anything at all.
        #
        # The protection for a genuine user is `static_min_tracks` instead, which is the
        # stronger signal anyway: one person using a phone is ONE track, however long
        # they hold it, whereas a desk object gets credited to everybody who sits near it.
        for det in (phone_dets or []):
            centre = det.get("_centre")
            if centre:
                self.note_location(centre[0], centre[1], now, det.get("_track"))

        self._purge(now, state_ttl)
        self._purge_static(now)
        self.metrics.maybe_report(now)
        # Regions first, so the confirmed boxes and phone boxes draw on top of them.
        return events, self._region_overlays(tracks, shape) + overlays

    # ── overlays ─────────────────────────────────────────────────────────────
    def _region_overlays(self, tracks, shape) -> list:
        """The hand region the module searches, drawn on every tracked person.

        This is the phone module's equivalent of the head-count line and band: proof on
        screen that the module is running, and the only way to judge the association
        geometry against a real scene."""
        if not self._bool("show_region", DEF_SHOW_REGION):
            return []
        h, w = shape
        top = self._num("hand_top", DEF_HAND_TOP)
        bottom = self._num("hand_bottom", DEF_HAND_BOTTOM)
        pad = self._num("hand_pad", DEF_HAND_PAD)
        out = [{"kind": "label", "text": "PHONE MONITORING", "color": "accent-soft"}]
        for i, t in enumerate(tracks):
            x1, y1, x2, y2 = t["box"]
            bw, bh = x2 - x1, y2 - y1
            out.append({
                "kind": "box",
                "box": [max(0.0, (x1 - bw * pad) / w), max(0.0, (y1 + bh * top) / h),
                        min(1.0, (x2 + bw * pad) / w), min(1.0, (y1 + bh * bottom) / h)],
                "color": "accent-soft",
                # Label one only, so a crowd does not turn into a wall of text.
                "label": "PHONE AREA" if i == 0 else None,
            })
        return out

    # ── read-only views, used by the slow lane ───────────────────────────────
    def confirmed_tracks(self) -> set:
        """Track ids currently in a confirmed phone-use episode.

        The medium lane owns phone inference, so the slow lane cannot recompute this —
        it reads the state instead. Per-track by construction: there is no camera-level
        flag, and several tracks can be confirmed at once."""
        return {tid for tid, st in self.tracks.items() if st.state == CONFIRMED}

    def current_overlays(self, tracks, shape) -> list:
        """Overlays for confirmed tracks, for the slow lane to draw. The medium lane
        returns overlays=None so it never clobbers the live overlay set."""
        h, w = shape
        confirmed = self.confirmed_tracks()
        out = self._region_overlays(tracks, shape)
        for t in tracks:
            if t["id"] in confirmed:
                x1, y1, x2, y2 = t["box"]
                st = self.tracks.get(t["id"])
                # Same label as the medium lane draws, so the live view and the saved
                # alert picture agree. `best_assoc` is the strongest association seen
                # during this episode, which is the number the confirmation rests on.
                assoc = getattr(st, "best_assoc", 0.0) if st else 0.0
                out.append({"kind": "box",
                            "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                            "color": "idle",
                            "label": f"PHONE USE  assoc {assoc:.2f}"})
        return out

    def _purge(self, now: float, state_ttl: float) -> None:
        """Drop state for tracks long gone. Bounded memory, no ghost episodes."""
        for tid in [k for k, st in self.tracks.items()
                    if now - st.last_seen > state_ttl]:
            st = self.tracks.pop(tid, None)
            if st is None or not pdbg.debug_enabled():
                continue
            pdbg.info(event="TRACK_END", camera=self.cam_id, track=tid,
                      final_state=st.state, episodes=st.episodes,
                      detections=st.hits, best_conf=round(st.best_conf, 3),
                      best_association=round(st.best_assoc, 3),
                      best_evidence=f"{st.best_hits}/{st.window.maxlen}",
                      motion=round(st.motion(), 1),
                      body_drift=round(st.best_body_drift, 4), body_obs=st.body_obs,
                      blocked_by=st.last_reason or ("-" if st.episodes else "no_evidence"))
