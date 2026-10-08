"""
Camera tampering detection (Kohat POC #4) — no neural network, by design.

WHY CLASSICAL CV AND NOT A MODEL
--------------------------------
Learned tampering classifiers exist. They are the wrong tool here on four counts:

  * **Interpretability.** "Variance of Laplacian fell from 340 to 12" is a number an
    operator can act on and an engineer can tune. "The classifier said 0.87" is not.
  * **Cost.** These three signals are a few milliseconds of NumPy on a frame that has
    already been decoded. A model would be orders of magnitude more expensive for a
    check that wants to run continuously on every camera.
  * **Placement.** See below — this must run where there is no GPU.
  * **Accuracy.** Defocus, occlusion and scene change are *directly measurable*. There
    is nothing for a model to learn that the measurement does not already state.

WHY IT RUNS IN THE MAIN PROCESS
-------------------------------
This is the load-bearing decision in the file. Tampering detection lives in
`camera_manager` (main process), NOT in the perception child, because **a dead child
process looks exactly like a blinded camera**. If tampering detection shared the child's
fate, the one failure it most needs to report — "this camera has stopped showing me the
scene" — is the failure that would silence it.

It also needs no GPU and no model, so there is no reason to pay the cost of shipping
frames to the child for it.

THE THREE SIGNALS
-----------------
    blur      Variance of the Laplacian. Sharp scenes have high edge energy; a sprayed,
              defocused or heavily-degraded lens collapses it.

    moved     Chi-square-style divergence of a coarse grayscale histogram against a
              slowly-updated reference. Catches a camera physically turned to the wall.
              It ALSO fires on a genuine scene change — lights off, shutter down — and
              that is deliberate: operationally those are the same alert.

    covered   Sustained very low luminance variance. A bag over the lens, a hand, a
              sticker. Distinct from blur: a covered lens has almost no structure at
              all, where a defocused one still has gradients.

RELATIVE, NOT ABSOLUTE - and this was learned the hard way
----------------------------------------------------------
blur and luminance-variance are compared against a ROLLING BASELINE of the camera's own
normal scene, not against fixed numbers. The first version of this file used fixed
numbers and it did not work, for a reason that is obvious in hindsight: what counts as
"blurred" is entirely scene-dependent.

Measured on a real camera, a hand fully covering the lens produced:

    normal scene   blur 460-620   luminance std 25-31
    covered        blur 130       luminance std 14.5

A dramatic collapse - roughly a quarter of the blur, half the variance - and yet it
tripped neither a `blur < 45` nor a `std < 12` absolute threshold. Another camera in a
dim corridor might sit at blur 80 normally, where those same constants would fire
constantly. No single pair of numbers can serve both.

So the signals fire on a PROPORTIONAL drop from that camera's own baseline
(`blur_drop_ratio`, `cover_drop_ratio`). The absolute thresholds remain available as an
optional hard floor for operators who want one, but default to 0 = disabled.

The baselines are EMAs updated by the same rule as the histogram reference: frozen
whenever any signal is over threshold, so a slowly-degrading lens cannot drag its own
baseline down and hide from itself.

SUSTAIN IS NOT OPTIONAL
-----------------------
A single dark or blurry frame is a compression artefact, a keyframe boundary, or someone
walking close past the lens. Ten seconds of them is an intervention. Every signal must
hold for `sustain_seconds` before it alerts. Without that this class is a false-positive
generator, which is why the sustain check is in one place and applies to all three.

After an alert clears, the reference histogram is REBUILT. A camera legitimately
repositioned should settle into its new view rather than alert forever.
"""
import os
import time

import numpy as np

from backend.services.analytics import tamper_debug as dbg

# Sampling cadence. Tampering persists for as long as it takes someone to notice, so
# there is nothing to gain from checking every frame — and a lot of CPU to save across
# many cameras.
# 0.5 s rather than 1 s: a 2-second sustain window cannot be resolved by 1-second
# samples without rounding the response time up to 3 s. The check is a 160x120 resize
# plus a Laplacian and a 32-bin histogram - well under a millisecond - so twice as often
# is still nothing next to the decode that already happened.
SAMPLE_INTERVAL = float(os.environ.get("TAMPER_SAMPLE_INTERVAL", "0.5"))
# How quickly the reference adapts. Slow, so gradual daylight change is absorbed but a
# sudden turn to the wall is not.
REF_ALPHA = float(os.environ.get("TAMPER_REF_ALPHA", "0.02"))
# Frames of reference history before any judgement. Without this the first frame after a
# restart is compared against nothing and everything looks like a change.
WARMUP_SAMPLES = int(os.environ.get("TAMPER_WARMUP_SAMPLES", "5"))

# Absolute floors. 0 = disabled, which is the default: see the docstring for why fixed
# numbers cannot work across cameras. Set one only if you specifically want a hard floor
# in addition to the proportional test.
DEF_BLUR = float(os.environ.get("TAMPER_BLUR_THRESHOLD", "0"))
DEF_SCENE = float(os.environ.get("TAMPER_SCENE_THRESHOLD", "0.45"))
DEF_COVER = float(os.environ.get("TAMPER_COVER_THRESHOLD", "0"))
# Proportional drop from this camera's own rolling baseline. These are the knobs that
# actually matter. 0.35 means "blur fell to a third of normal"; the measured covered-lens
# case was 130/512 = 0.25, and a normal scene varies by only a few percent, so there is a
# wide margin either side.
DEF_BLUR_RATIO = float(os.environ.get("TAMPER_BLUR_DROP_RATIO", "0.35"))
DEF_COVER_RATIO = float(os.environ.get("TAMPER_COVER_DROP_RATIO", "0.60"))
# Baselines adapt slowly - fast enough for daylight change, far too slow to follow a lens
# being sprayed.
BASE_ALPHA = float(os.environ.get("TAMPER_BASE_ALPHA", "0.05"))
# ASYMMETRY, and it is the whole point. Tampering only ever makes the picture WORSE:
# less sharp, less varied, less informative. Legitimate change - lights coming on, a
# room filling up, the sun moving - is as likely to raise these numbers as lower them.
#
# So the baseline may rise at BASE_ALPHA but must fall far more slowly. A symmetric EMA
# has a fatal flaw that a real capture demonstrated: an object held in front of the lens
# dropped blur from 530 to 240 - a 55% collapse - which was not past the 35% trip, so no
# signal was "over", so the baseline kept adapting. Within eight seconds it had chased
# the obstruction down from 530 to 380, taking the trip point with it, and the alert
# became unreachable. The camera had learned to accept being blocked.
#
# Falling 20x slower means a genuine dimming still settles within minutes, while an
# obstruction cannot outrun the detector.
BASE_FALL_DIVISOR = float(os.environ.get("TAMPER_BASE_FALL_DIVISOR", "20"))

# ── Regional coverage ────────────────────────────────────────────────────────────
# Whole-frame variance answers "is the ENTIRE picture uniform", which is true only when
# the lens is completely blocked. Somebody holding a book in front of the camera covers
# PART of the view: in the capture that prompted this, the object dropped blur by 55%
# while whole-frame std did not move at all, because everything around the object still
# had plenty of structure.
#
# So the frame is also split into a grid and each cell judged against its OWN baseline.
# A cell whose variance collapses is "dead"; enough dead cells at once is an obstruction,
# wherever in the frame it sits and whatever shape it is.
GRID = int(os.environ.get("TAMPER_GRID", "4"))                       # 4x4 = 16 cells
CELL_DROP_RATIO = float(os.environ.get("TAMPER_CELL_DROP_RATIO", "0.45"))
COVER_AREA_RATIO = float(os.environ.get("TAMPER_COVER_AREA_RATIO", "0.30"))
DEF_SUSTAIN = float(os.environ.get("TAMPER_SUSTAIN_SECONDS", "10"))

# PER-SIGNAL sustain windows. One window for all three was wrong, and a real capture
# showed why. Across 279 samples of a live camera:
#
#     covered   fired 2x   BOTH genuine (a hand on the lens; std 24.4 -> 5.5)
#     blur      fired 1x   genuine
#     moved     fired 6x   TWO of them spurious, with nobody testing
#
# The signals differ in how ambiguous they are, so they should differ in how long they
# must hold:
#
#   covered  Almost nothing except an object ON the lens makes a whole frame uniform.
#            A person walking past does not. So this can be FAST - which matters,
#            because "someone is covering the camera" is the one tampering event where
#            two seconds of delay is the difference between catching it and not.
#
#   blur     A hand or body close to the lens defocuses it in passing, so a little more
#            patience is warranted, but it is still fairly specific.
#
#   moved    Histogram divergence fires on ANY large scene change, including somebody
#            walking close past the camera - which is what the two spurious fires were.
#            It keeps the long window.
#
# A per-camera `<signal>_sustain_seconds` overrides these.
SIGNAL_SUSTAIN = {
    "covered": float(os.environ.get("TAMPER_COVER_SUSTAIN", "2")),
    "blur": float(os.environ.get("TAMPER_BLUR_SUSTAIN", "4")),
    "moved": float(os.environ.get("TAMPER_MOVED_SUSTAIN", str(DEF_SUSTAIN))),
}
DEF_COOLDOWN = float(os.environ.get("TAMPER_COOLDOWN", "300"))

# Require the frame to have lost sharpness before whole-frame flatness counts as
# "covered". Set TAMPER_COVER_NEEDS_BLUR=0 to go back to flatness alone - only sensible
# on a camera whose scene can genuinely go flat AND stay in focus.
COVER_NEEDS_BLUR = os.environ.get("TAMPER_COVER_NEEDS_BLUR", "1") not in ("0", "", "false", "False")

def covered_signal(flat: bool, sharp: bool, dead_frac: float,
                   area_trip: float | None = None,
                   needs_blur: bool | None = None) -> bool:
    """Is the lens covered?

    Pulled out of `check()` so it can be tested against the numbers a real camera
    produced rather than against a synthetic frame - a flat patch pasted into random
    noise RAISES luminance variance instead of lowering it, so the field case cannot be
    reproduced that way. See tests/test_kohat_poc.py::test_34b.

    `flat`  whole-frame luminance variance is under the trip
    `sharp` the frame still has normal edge energy - a covered lens never does
    `dead_frac` fraction of the 4x4 grid whose local variance collapsed (partial cover)
    """
    area = COVER_AREA_RATIO if area_trip is None else area_trip
    nb = COVER_NEEDS_BLUR if needs_blur is None else needs_blur
    return (flat and not (sharp and nb)) or (dead_frac >= area)


SIGNALS = ("blur", "moved", "covered")
EVENT_FOR = {"blur": "tamper_blur", "moved": "tamper_moved", "covered": "tamper_covered"}
LABEL_FOR = {"blur": "Camera Defocused / Sprayed",
             "moved": "Camera Moved or Scene Changed",
             "covered": "Camera Covered"}


class TamperMonitor:
    """One monitor per camera. Owned by the CameraInstance that feeds it."""

    def __init__(self, cam_id, config: dict | None = None):
        self.cam_id = cam_id
        self.config = config or {}
        self.metrics = dbg.Metrics(cam_id)
        self._announced = False
        self._last_sample = 0.0
        self._ref_hist = None
        self._blur_ref: float | None = None
        self._std_ref: float | None = None
        self._cell_ref = None                  # per-cell variance baseline
        self._samples = 0
        # signal -> timestamp it first went over threshold (None = currently clean)
        self._since: dict[str, float | None] = {s: None for s in SIGNALS}
        self._alerted: dict[str, bool] = {s: False for s in SIGNALS}
        self._last_alert: dict[str, float] = {s: 0.0 for s in SIGNALS}

    # ── config ───────────────────────────────────────────────────────────────
    def _num(self, key: str, default: float) -> float:
        try:
            v = self.config.get(key)
            return default if v is None or v == "" else float(v)
        except (TypeError, ValueError):
            return default

    def _sustain_for(self, signal: str, fallback: float) -> float:
        """How long THIS signal must hold. Per-camera config first, then the per-signal
        default, then the global one."""
        v = self.config.get(f"{signal}_sustain_seconds")
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
        return SIGNAL_SUSTAIN.get(signal, fallback)

    def reset_reference(self) -> None:
        """Forget the baseline. Called after an alert clears, so a legitimately moved
        camera settles into its new view instead of alerting forever."""
        self._ref_hist = None
        self._blur_ref = None
        self._std_ref = None
        self._cell_ref = None
        self._samples = 0

    # ── signal measurement ───────────────────────────────────────────────────
    @staticmethod
    def _measure(frame) -> dict | None:
        """Blur, luminance spread and a coarse histogram. Returns None if unusable."""
        try:
            import cv2
            import numpy as np
        except Exception:
            return None
        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Downscale first: the signals are global, and this makes the check cheap
            # enough to run on every camera without thinking about it.
            small = cv2.resize(gray, (160, 120), interpolation=cv2.INTER_AREA)
            lap_var = float(cv2.Laplacian(small, cv2.CV_64F).var())
            std = float(np.std(small))
            hist = cv2.calcHist([small], [0], None, [32], [0, 256]).flatten()
            total = float(hist.sum()) or 1.0
            # Per-cell variance, for the regional check. Cheap: a reshape and two means
            # over a 160x120 array.
            h, w = small.shape
            ch, cw = h // GRID, w // GRID
            cells = (small[:ch * GRID, :cw * GRID]
                     .reshape(GRID, ch, GRID, cw)
                     .swapaxes(1, 2)
                     .reshape(GRID * GRID, ch * cw))
            cell_std = cells.std(axis=1).astype(float)
            return {"blur": lap_var, "std": std, "hist": hist / total,
                    "cells": cell_std}
        except Exception:
            return None

    @staticmethod
    def _hist_divergence(a, b) -> float:
        """Symmetric chi-square distance between two normalised histograms, 0..1-ish.

        Chi-square rather than correlation: it is sensitive to a bin moving from
        populated to empty, which is exactly what happens when a camera turns to face a
        blank wall."""
        num = (a - b) ** 2
        den = a + b
        with_den = num[den > 1e-9] / den[den > 1e-9]
        return float(with_den.sum())

    # ── main entry point ─────────────────────────────────────────────────────
    def check(self, frame) -> list[dict]:
        """Sample one frame. Returns events (possibly empty) in the module-event shape
        the perception writer already understands.

        Never raises: a monitor that throws would take down the capture loop, and losing
        the video feed to protect against tampering would be an absurd trade."""
        try:
            return self._check(frame)
        except Exception as e:
            print(f"[TAMPER] check error on camera {self.cam_id}: {e}", flush=True)
            return []

    def _check(self, frame) -> list[dict]:
        now = time.time()
        if now - self._last_sample < SAMPLE_INTERVAL:
            return []
        self._last_sample = now

        blur_th = self._num("blur_threshold", DEF_BLUR)
        scene_th = self._num("scene_threshold", DEF_SCENE)
        cover_th = self._num("cover_threshold", DEF_COVER)
        sustain = self._num("sustain_seconds", DEF_SUSTAIN)
        cooldown = self._num("cooldown", DEF_COOLDOWN)

        if not self._announced:
            dbg.session(self.cam_id, {
                "blur_drop_ratio": self._num("blur_drop_ratio", DEF_BLUR_RATIO),
                "cover_drop_ratio": self._num("cover_drop_ratio", DEF_COVER_RATIO),
                "scene_threshold": scene_th,
                "blur_floor": blur_th, "cover_floor": cover_th,
                "sustain_covered": self._sustain_for("covered", sustain),
                "cover_needs_blur": COVER_NEEDS_BLUR,
                "sustain_blur": self._sustain_for("blur", sustain),
                "sustain_moved": self._sustain_for("moved", sustain),
                "cooldown": cooldown,
                "sample_interval": SAMPLE_INTERVAL, "base_alpha": BASE_ALPHA,
            })
            self._announced = True

        m = self._measure(frame)
        if m is None:
            return []
        self.metrics.bump("frames_sampled")

        # Reference histogram: EMA, seeded on first use.
        if self._ref_hist is None:
            self._ref_hist = m["hist"].copy()
            self._blur_ref = m["blur"]
            self._std_ref = m["std"]
            self._cell_ref = m["cells"].copy() if m.get("cells") is not None else None
            self._samples = 1
            dbg.debug(event="REFERENCE", camera=self.cam_id, blur=m["blur"],
                      std=m["std"], verdict="SEEDED")
            return []
        divergence = self._hist_divergence(m["hist"], self._ref_hist)
        self._samples += 1

        # Proportional test against this camera's own baseline, plus the optional
        # absolute floor. A signal fires if EITHER says so, and the floor is off by
        # default.
        blur_ratio = self._num("blur_drop_ratio", DEF_BLUR_RATIO)
        cover_ratio = self._num("cover_drop_ratio", DEF_COVER_RATIO)
        blur_trip = (self._blur_ref or 0) * blur_ratio
        cover_trip = (self._std_ref or 0) * cover_ratio
        # Regional coverage: what fraction of the frame's cells have gone dead against
        # their own baseline. Catches a PARTIAL obstruction, which whole-frame variance
        # cannot see at all.
        dead_frac = 0.0
        if self._cell_ref is not None and m.get("cells") is not None:
            ref = self._cell_ref
            cur = m["cells"]
            if len(ref) == len(cur):
                dead = (cur < ref * CELL_DROP_RATIO).sum()
                dead_frac = float(dead) / float(len(cur))

        # A COVERED LENS CANNOT BE SHARP. Whole-frame std alone is not enough evidence
        # of covering, because anything large and plainly-coloured entering the scene
        # flattens it: a person standing near the camera occludes a textured background
        # with a fairly uniform body and drops global std well past the trip.
        #
        # Measured, from logs/tamper_debug.log on 2026-08-28:
        #
        #             blur (trip)      std (trip)     dead_frac (trip 0.30)
        #   cam 49    253.8 (574)  <   7.96 (27.9)      0.9375     genuine cover
        #   cam 47    116.5 (327)  <   8.04 (27.6)      0.9375     genuine cover
        #   cam 50    514.6 (308)  >  25.01 (26.4)      0.1250     PERSON STANDING
        #
        # Both genuine covers were BELOW their blur trip and lit up 15 of 16 regions.
        # The false positive was well ABOVE its blur trip with 2 of 16 regions dark - the
        # regional evidence already said "not covered", it just was not being consulted
        # before the whole-frame path fired.
        #
        # So the whole-frame path now requires the frame to have lost sharpness too. The
        # REGIONAL path is deliberately left unvetoed: a partial cover leaves the rest of
        # the frame in focus, which is exactly why that check exists, and it demands 30%
        # of cells go dark - a bar the standing person came nowhere near.
        sharp = m["blur"] >= blur_trip
        flat = (m["std"] < cover_trip) or (cover_th > 0 and m["std"] < cover_th)
        over = {
            "blur": (m["blur"] < blur_trip) or (blur_th > 0 and m["blur"] < blur_th),
            "moved": divergence > scene_th,
            "covered": covered_signal(flat, sharp, dead_frac),
        }

        if dbg.tracing():
            dbg.trace(event="SAMPLE", camera=self.cam_id, blur=m["blur"],
                      blur_ref=self._blur_ref, blur_trip=blur_trip,
                      divergence=divergence, scene_th=scene_th,
                      std=m["std"], std_ref=self._std_ref, cover_trip=cover_trip,
                      dead_frac=dead_frac, area_trip=COVER_AREA_RATIO,
                      samples=self._samples,
                      over=",".join(k for k, v in over.items() if v) or "-")

        for sig in SIGNALS:
            if over[sig]:
                self.metrics.bump({"blur": "blur_over_threshold",
                                   "moved": "scene_over_threshold",
                                   "covered": "coverage_over_threshold"}[sig])

        # Warm-up: refuse to judge until there is enough reference history. A restart
        # must not look like an intervention.
        if self._samples <= WARMUP_SAMPLES:
            self._advance_reference(m, any(over.values()))
            return []

        events = []
        for sig in SIGNALS:
            if over[sig]:
                if self._since[sig] is None:
                    self._since[sig] = now
                    dbg.debug(event="RISING", camera=self.cam_id, signal=sig,
                              verdict="TIMER_STARTED")
                    continue
                held = now - self._since[sig]
                if self._alerted[sig]:
                    continue
                sig_sustain = self._sustain_for(sig, sustain)
                if held < sig_sustain:
                    self.metrics.bump("filtered_below_sustain")
                    dbg.debug(event="PENDING", camera=self.cam_id, signal=sig,
                              held=held, sustain=sig_sustain, verdict="BELOW_SUSTAIN")
                    continue
                if cooldown > 0 and (now - self._last_alert[sig]) < cooldown:
                    self.metrics.bump("filtered_cooldown")
                    dbg.debug(event="FILTER", camera=self.cam_id, signal=sig,
                              age=now - self._last_alert[sig], cooldown=cooldown,
                              verdict="COOLDOWN")
                    continue
                self._alerted[sig] = True
                self._last_alert[sig] = now
                self.metrics.bump(f"{'moved' if sig == 'moved' else sig}_alerted")
                dbg.info(event="TAMPER", camera=self.cam_id, signal=sig, held=held,
                         sustain=sig_sustain, blur=m["blur"], divergence=divergence,
                         std=m["std"], verdict="ALERT")
                events.append({
                    "module": "tamper",
                    "event_type": EVENT_FOR[sig],
                    "label": LABEL_FOR[sig],
                    "value": round({"blur": m["blur"], "moved": divergence,
                                    "covered": m["std"]}[sig], 3),
                    "data": {"signal": sig, "held_seconds": round(held, 1),
                             "blur": round(m["blur"], 2),
                             "divergence": round(divergence, 4),
                             "luminance_std": round(m["std"], 2),
                             "obscured_fraction": round(dead_frac, 3)},
                    "snapshot": True,
                })
            else:
                if self._alerted[sig]:
                    dbg.info(event="CLEARED", camera=self.cam_id, signal=sig,
                             verdict="RECOVERED")
                    # The scene it recovered INTO may be a new legitimate view.
                    self.reset_reference()
                self._since[sig] = None
                self._alerted[sig] = False

        self._advance_reference(m, any(over.values()))
        self.metrics.maybe_report()
        return events

    def _advance_reference(self, m, suspicious: bool) -> None:
        """Update the rolling reference — but never while a signal is over threshold.

        This is the subtle one. If the reference kept adapting during tampering, a
        camera slowly turned to the wall would drag its own baseline along with it and
        never register a change at all."""
        if suspicious or self._ref_hist is None:
            return
        self._ref_hist = (1.0 - REF_ALPHA) * self._ref_hist + REF_ALPHA * m["hist"]

        def _adapt(ref, value):
            """Rise at BASE_ALPHA, fall far more slowly. See BASE_FALL_DIVISOR."""
            if ref is None:
                return value
            a = BASE_ALPHA if value >= ref else BASE_ALPHA / BASE_FALL_DIVISOR
            return (1.0 - a) * ref + a * value

        self._blur_ref = _adapt(self._blur_ref, m["blur"])
        self._std_ref = _adapt(self._std_ref, m["std"])
        if m.get("cells") is not None:
            if self._cell_ref is None or len(self._cell_ref) != len(m["cells"]):
                self._cell_ref = m["cells"].copy()
            else:
                cur = m["cells"]
                rising = cur >= self._cell_ref
                a = np.where(rising, BASE_ALPHA, BASE_ALPHA / BASE_FALL_DIVISOR)
                self._cell_ref = (1.0 - a) * self._cell_ref + a * cur
