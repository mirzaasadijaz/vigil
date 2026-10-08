"""
Phone detector backbone (Phase 2).

WHY THIS IS NOT JUST "run YOLO on the frame"
--------------------------------------------
Measured from real deployment footage (backend/scripts/bench_phone_scale.py):

    camera 50   det frame 1280x720   median person box  61 x 176 px
    camera 47   det frame  960x1080  median person box 137 x 596 px

A hand-held phone is roughly 0.18x a person's shoulder width, so on camera 50 the median
phone is about 11 px in the det frame — and ultralytics letterboxes to imgsz=640, which
takes it to about 5.5 px. YOLO's finest head is stride 8; below roughly 16 px detection
collapses. Full-frame phone detection on that camera is not a tuning problem, it is
physically below the detector's resolution floor.

So this detector supports two modes:

  "crop"  (default) — run inference on the UPPER-BODY REGION of each person track,
          letterboxed up to `crop_size`. A 61x88 upper-body crop scaled to 320 px is a
          ~3.6x magnification, putting that same 11 px phone at ~40 px: comfortably
          inside the detector's range. Cost scales with the number of PEOPLE, not with
          frame size, and the crops are batched into a single inference call.

  "frame" — the original whole-frame pass. Kept for cameras where people are large in
          frame, and as the baseline for benchmarking.

The person backbone is NOT duplicated: crops are derived from the tracks the shared
YOLOv11s + ByteTrack pass already produced.

WEIGHTS
-------
Defaults to the shared `models/yolo11s.pt` and COCO class 67 (`cell phone`). Point
PHONE_WEIGHTS at a dedicated phone model to swap it, and PHONE_CLASSES at its class ids
(or `all`). Missing weights log once and return nothing, so the module degrades to
"detects no phones" instead of crashing the perception child — same contract as
detectors/smoking.py and detectors/reid.py.

Runs inside the perception child process (PyTorch / GPU).
"""

from __future__ import annotations

import os

# ── configuration ─────────────────────────────────────────────────────────────
MODE = os.environ.get("PHONE_MODE", "crop").strip().lower()          # "crop" | "frame"
# Measured on a real office capture: the median detection scored 0.32 and only 5.5%
# exceeded 0.70 - at 0.20 the detector was reporting keyboards, mice and wallets. Raised,
# but NOT relied on alone: a floor of 0.50 still left 42% of survivors as furniture, so
# the static-object and motion filters in analytics/mobile.py do the real work.
# The DETECTOR floor is deliberately low. Judging a detection is the consumer's job,
# and it does it on detector confidence COMBINED with association quality - a weak
# detection in exactly the right place beats a strong one in the wrong place. Filtering
# hard here would throw that evidence away before it could be weighed.
CONF = float(os.environ.get("PHONE_CONF", "0.15"))
# When diagnostics are on, run the detector at a LOWER floor than the one we act on and
# let the consumer discard the difference. A phone that scored 0.28 against a 0.35 floor
# otherwise leaves no trace at all, making "the model never saw it" look identical to
# "the threshold was too high" - which are opposite fixes.
LOG_CONF = float(os.environ.get("PHONE_LOG_CONF", "0.15"))
# Must be at least as large as a typical upper-body crop, or letterboxing SHRINKS the
# crop and the whole point is lost. Measured on real footage: camera 47's people are
# ~596 px tall, so a 0.70 band is ~417 px — at CROP_SIZE=320 that was a 0.8x DOWNSCALE,
# worse than whole-frame. 448 keeps both deployment cameras at >=1x.
# Check yours with: python -m backend.scripts.bench_phone_scale --log <headcount log>
CROP_SIZE = int(os.environ.get("PHONE_CROP_SIZE", "448"))
# Upper bound on the adaptive crop size (see _crop_imgsz). Bounds GPU cost when somebody
# stands very close to the camera.
CROP_MAX = int(os.environ.get("PHONE_CROP_MAX", "640"))
FRAME_IMGSZ = int(os.environ.get("PHONE_FRAME_IMGSZ", "640"))
# Upper-body slice of the person box that a hand-held phone can occupy: from the top of
# the head (a phone raised to the ear) down to roughly the hip (a phone held at waist).
# Padded sideways because hands extend beyond the torso box.
CROP_TOP = float(os.environ.get("PHONE_CROP_TOP", "0.00"))
CROP_BOTTOM = float(os.environ.get("PHONE_CROP_BOTTOM", "0.70"))
CROP_PAD = float(os.environ.get("PHONE_CROP_PAD", "0.20"))
MIN_CROP_PX = int(os.environ.get("PHONE_MIN_CROP_PX", "24"))
MAX_CROPS = int(os.environ.get("PHONE_MAX_CROPS", "12"))

_model = None
_loaded = False          # attempted to load yet? (load-once, even on failure)


def _weights_path() -> str:
    env = os.environ.get("PHONE_WEIGHTS")
    if env:
        return env
    from backend.core.config import settings
    return os.path.join(settings.BASE_DIR, "models", "yolo11s.pt")


def _classes():
    """Class ids to keep. Default COCO 67 = cell phone; `all` disables filtering."""
    raw = os.environ.get("PHONE_CLASSES", "67").strip().lower()
    if raw in ("", "all", "none", "*"):
        return None
    try:
        return [int(v) for v in raw.split(",") if v.strip() != ""]
    except ValueError:
        return [67]


def _diagnostics_on() -> bool:
    try:
        from backend.services.analytics import phone_debug
        return phone_debug.tracing()
    except Exception:
        return False


def _get_model():
    global _model, _loaded
    if _loaded:
        return _model
    _loaded = True
    path = _weights_path()
    if not os.path.exists(path):
        print(f"[PHONE] no weights at {path} — phone detection disabled "
              f"(set PHONE_WEIGHTS or install models/yolo11s.pt).", flush=True)
        return None
    try:
        from ultralytics import YOLO
        _model = YOLO(path)
        print(f"[PHONE] loaded {path} (mode={MODE}, classes={_classes()}).", flush=True)
    except Exception as e:
        print(f"[PHONE] failed to load {path}: {e}", flush=True)
        _model = None
    return _model


def available() -> bool:
    return _get_model() is not None


def reset_for_tests() -> None:
    """Drop the cached model so a test can re-exercise the load path."""
    global _model, _loaded
    _model, _loaded = None, False


# ── crop geometry ─────────────────────────────────────────────────────────────
def upper_body_box(person_box, shape, top: float = None, bottom: float = None,
                   pad: float = None) -> tuple[int, int, int, int] | None:
    """The region of a person box a hand-held phone can plausibly occupy, in pixels.

    Returned clipped to the frame. None if the result is too small to be worth an
    inference pass."""
    h, w = shape
    top = CROP_TOP if top is None else top
    bottom = CROP_BOTTOM if bottom is None else bottom
    pad = CROP_PAD if pad is None else pad

    x1, y1, x2, y2 = person_box
    bw, bh = max(0.0, x2 - x1), max(0.0, y2 - y1)
    cx1 = int(max(0, round(x1 - bw * pad)))
    cx2 = int(min(w, round(x2 + bw * pad)))
    cy1 = int(max(0, round(y1 + bh * top)))
    cy2 = int(min(h, round(y1 + bh * bottom)))
    if cx2 - cx1 < MIN_CROP_PX or cy2 - cy1 < MIN_CROP_PX:
        return None
    return cx1, cy1, cx2, cy2


def _crop_imgsz(crops, floor: int) -> int:
    """Inference size for a batch of crops: never DOWNSCALE them.

    Letterboxing a 417 px crop into a 320 px square shrinks the phone, which defeats the
    whole point of cropping — measured on camera 47, where it turned a 1.5x magnification
    into a 0.8x reduction. So the target is at least the largest crop's long side
    (rounded up to the stride-32 grid), floored at `floor` so small crops are still
    magnified and capped at CROP_MAX so a close-up person cannot blow up the cost."""
    longest = 0
    for c in crops:
        hh, ww = c.shape[:2]
        longest = max(longest, hh, ww)
    want = ((longest + 31) // 32) * 32
    return max(floor, min(CROP_MAX, want))


# ── inference ─────────────────────────────────────────────────────────────────
def _boxes_from(result, offset=(0, 0)) -> list[dict]:
    ox, oy = offset
    out = []
    for b in getattr(result, "boxes", []) or []:
        try:
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            out.append({"box": (x1 + ox, y1 + oy, x2 + ox, y2 + oy),
                        "conf": float(b.conf[0])})
        except Exception:
            continue
    return out


def detect_phones(frame, tracks=None, shape=None, conf: float = None,
                  mode: str = None, crop_size: int = None) -> list[dict]:
    """Detect phones and return [{box:(x1,y1,x2,y2), conf, source}] in FRAME pixels.

    `tracks` are the person tracks from the shared backbone. In crop mode they define
    where to look; without them crop mode has nothing to crop and returns nothing, which
    is correct — a phone with nobody near it is not phone *use*."""
    model = _get_model()
    if model is None:
        return []
    conf = CONF if conf is None else conf
    if _diagnostics_on():
        conf = min(conf, LOG_CONF)
    mode = (mode or MODE).lower()
    crop_size = int(crop_size or CROP_SIZE)
    classes = _classes()
    kw = {"conf": conf, "verbose": False}
    if classes is not None:
        kw["classes"] = classes

    if mode == "frame" or not tracks:
        if mode == "crop":
            return []          # crop mode with no people: nothing to inspect
        try:
            results = model.predict(frame, imgsz=FRAME_IMGSZ, **kw)
        except Exception as e:
            print(f"[PHONE] detect error: {e}", flush=True)
            return []
        out = []
        for r in results:
            for d in _boxes_from(r):
                d["source"] = "frame"
                out.append(d)
        return out

    # ── crop mode ────────────────────────────────────────────────────────────
    if shape is None:
        shape = frame.shape[:2]
    crops, offsets = [], []
    # Largest people first: if the crop budget is exceeded, the ones most likely to
    # yield a detectable phone are kept.
    ordered = sorted(tracks, key=lambda t: -( (t["box"][2] - t["box"][0])
                                              * (t["box"][3] - t["box"][1]) ))
    for t in ordered[:MAX_CROPS]:
        region = upper_body_box(t["box"], shape)
        if region is None:
            continue
        cx1, cy1, cx2, cy2 = region
        crop = frame[cy1:cy2, cx1:cx2]
        if getattr(crop, "size", 0) == 0:
            continue
        crops.append(crop)
        offsets.append((cx1, cy1))
    if not crops:
        return []

    try:
        # One batched call: cost scales with the number of people, not frame size.
        results = model.predict(crops, imgsz=_crop_imgsz(crops, crop_size), **kw)
    except Exception as e:
        print(f"[PHONE] detect error: {e}", flush=True)
        return []

    out = []
    for r, off in zip(results, offsets):
        for d in _boxes_from(r, off):
            d["source"] = "crop"
            out.append(d)
    return _dedupe(out)


def _dedupe(dets: list[dict], iou_thresh: float = 0.55) -> list[dict]:
    """Overlapping person crops can each report the same phone. Keep the most confident
    of any heavily-overlapping pair."""
    kept: list[dict] = []
    for d in sorted(dets, key=lambda x: -x["conf"]):
        if all(_iou(d["box"], k["box"]) < iou_thresh for k in kept):
            kept.append(d)
    return kept


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
