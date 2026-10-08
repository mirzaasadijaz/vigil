"""
Benchmark: is phone detection even possible at this camera's resolution?

Phase 2 starts here, before any model or threshold decision, because the answer changes
what the right fix is. A phone is only detectable if it is large enough in the image the
detector actually sees — and that is NOT the camera resolution, it is the frame after
ultralytics letterboxes it down to `imgsz`.

Two sources of measurement:

  --log PATH   a captured head-count TRACE log. It records every person box on every
               frame, which gives the real distribution of apparent person size in
               deployment. A hand-held phone is roughly PHONE_TO_PERSON x shoulder
               width, so the phone size follows.

  (no args)    the same arithmetic over a table of hypothetical person sizes, for
               planning a camera placement before anything is installed.

The output compares the implied phone size against two thresholds:

    ~16 px   below this, YOLO's finest head (stride 8) cannot reliably resolve an object
    ~32 px   comfortable detection

and shows what the "crop" mode of detectors/phone.py does to those numbers, since
running inference on an upscaled upper-body crop is the lever that resolution-limited
cameras actually have.

Run:
    python -m backend.scripts.bench_phone_scale --log logs/headcount_debug.log
    python -m backend.scripts.bench_phone_scale
"""

from __future__ import annotations

import argparse
import statistics
import sys

# A phone held in the hand is about this fraction of a person's shoulder width.
PHONE_TO_PERSON = 0.18
DETECTION_FLOOR = 16.0            # px: below this, stride-8 detection collapses
COMFORTABLE = 32.0                # px

try:
    from backend.services.detectors.phone import (
        CROP_BOTTOM, CROP_MAX, CROP_PAD, CROP_SIZE, CROP_TOP, FRAME_IMGSZ,
    )
except Exception:                                     # keep the script standalone
    CROP_TOP, CROP_BOTTOM, CROP_PAD = 0.0, 0.70, 0.20
    CROP_SIZE, CROP_MAX, FRAME_IMGSZ = 448, 640, 640


def verdict(px: float) -> str:
    if px >= COMFORTABLE:
        return "comfortable"
    if px >= DETECTION_FLOOR:
        return "marginal"
    return "BELOW FLOOR"


def analyse(label, frame_w, frame_h, person_w, person_h):
    """Report the implied phone size in frame mode vs crop mode for one person size."""
    phone_px = person_w * PHONE_TO_PERSON

    # frame mode: the whole det frame is letterboxed down to FRAME_IMGSZ
    frame_scale = FRAME_IMGSZ / max(frame_w, frame_h)
    frame_px = phone_px * min(1.0, frame_scale)

    # crop mode: the upper-body region is letterboxed to an ADAPTIVE size that never
    # downscales it (see detectors/phone._crop_imgsz).
    crop_w = person_w * (1 + 2 * CROP_PAD)
    crop_h = person_h * (CROP_BOTTOM - CROP_TOP)
    longest = max(crop_w, crop_h)
    imgsz = max(CROP_SIZE, min(CROP_MAX, ((int(longest) + 31) // 32) * 32))
    crop_scale = imgsz / max(1e-6, longest)
    crop_px = phone_px * crop_scale

    flag = "  <- crop target TOO SMALL" if crop_scale < 1.0 else ""
    print(f"  {label:<18}{person_w:>7.0f}{phone_px:>10.1f}"
          f"{frame_px:>10.1f}  {verdict(frame_px):<12}"
          f"{crop_px:>9.1f}  {verdict(crop_px):<12}{crop_scale:>7.1f}x{flag}")
    return crop_scale


def header(frame_w, frame_h):
    print(f"\ndet frame {frame_w}x{frame_h}   "
          f"frame mode imgsz={FRAME_IMGSZ}   crop mode imgsz={CROP_SIZE}-{CROP_MAX} "
          f"(band {CROP_TOP:.2f}-{CROP_BOTTOM:.2f}, pad {CROP_PAD:.2f})")
    print(f"  {'':<18}{'person':>7}{'phone':>10}{'frame':>10}{'':<14}"
          f"{'crop':>9}{'':<14}{'gain':>8}")
    print(f"  {'':<18}{'px':>7}{'px':>10}{'px':>10}{'':<14}{'px':>9}")


def from_log(path):
    from backend.services.analytics.diagnostics import parse_log

    recs = parse_log(path)
    sessions = {f.get("camera"): f for _, f in recs if f["event"] == "SESSION"}
    obs = [f for _, f in recs if f["event"] == "OBS"]
    if not obs:
        print(f"{path}: no OBS records — capture with HEADCOUNT_DEBUG=trace")
        return 2

    by_cam = {}
    for f in obs:
        by_cam.setdefault(f["camera"], []).append(f)

    print("=" * 88)
    print("APPARENT PHONE SIZE IN REAL DEPLOYMENT FOOTAGE")
    print("=" * 88)
    for cam, rows in sorted(by_cam.items(), key=lambda kv: str(kv[0])):
        shape = str(sessions.get(cam, {}).get("shape", "1280x720"))
        try:
            w, h = (int(v) for v in shape.split("x"))
        except ValueError:
            w, h = 1280, 720
        widths = sorted(abs(f["box"][2] - f["box"][0]) for f in rows)
        heights = sorted(abs(f["box"][3] - f["box"][1]) for f in rows)
        print(f"\ncamera {cam}   {len(rows)} person observations")
        header(w, h)
        gains = []
        for name, idx in (("p10 (distant)", len(widths) // 10),
                          ("median", len(widths) // 2),
                          ("p90 (near)", 9 * len(widths) // 10)):
            gains.append(analyse(name, w, h, widths[idx], heights[idx]))
        print(f"  person width: median {statistics.median(widths):.0f} px, "
              f"height median {statistics.median(heights):.0f} px")
        if gains and min(gains) < 1.0:
            print("  ** raise PHONE_CROP_SIZE for this camera: a crop target smaller")
            print("     than the crop itself DOWNSCALES it, which is worse than")
            print("     whole-frame inference. **")
    _conclusion()
    return 0


def hypothetical():
    print("=" * 88)
    print("IMPLIED PHONE SIZE FOR HYPOTHETICAL PERSON SIZES")
    print("=" * 88)
    for frame_w, frame_h in ((1280, 720), (1920, 1080)):
        header(frame_w, frame_h)
        for label, pw, ph in (("far (40 px)", 40, 120),
                              ("distant (61 px)", 61, 176),
                              ("mid (137 px)", 137, 400),
                              ("near (250 px)", 250, 700)):
            analyse(label, frame_w, frame_h, pw, ph)
    _conclusion()
    return 0


def _conclusion():
    print()
    print("-" * 88)
    print("Reading this: 'frame px' is how big the phone is in the image the detector")
    print("actually sees in whole-frame mode. Below ~16 px, no threshold or model swap")
    print("recovers it — the information is not in the pixels. 'crop px' is the same")
    print("phone after the upper-body crop is letterboxed up, which is the lever a")
    print("resolution-limited camera has (PHONE_MODE=crop, the default).")
    print("-" * 88)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default=None,
                    help="a head-count TRACE log to measure real person sizes from")
    args = ap.parse_args(argv)
    if args.log:
        try:
            return from_log(args.log)
        except OSError as e:
            print(f"cannot read {args.log}: {e}")
            return 2
    return hypothetical()


if __name__ == "__main__":
    sys.exit(main())
