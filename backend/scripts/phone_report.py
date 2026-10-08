"""
Analyse a captured phone-use diagnostic log — and replay it.

Same two jobs as the head-count tool, and the same philosophy: a captured log should be
enough to answer both "why did VIGIL say this person was using a phone?" and "why did it
miss that one?", without needing the original video.

  REPORT   What phone perception did: raw detections, how many associated with a person,
           which were rejected and why, which tracks reached a confirmed episode. The
           rejection breakdown is the interesting part — it separates a DETECTOR failure
           (nothing was detected at all) from an ASSOCIATION failure (a phone was seen
           but did not belong to anybody) from a TEMPORAL failure (seen, associated, but
           never persistent enough).

  REPLAY   Rebuild the detections and person boxes from the log's DET/EVAL records and
           push them back through the real MobileUsage consumer, so association and
           confirmation thresholds can be retuned against real footage.

Note on replay scope: it re-runs association and the state machine, NOT the detector. A
phone the model never saw cannot be recovered by replay — that is a detector or camera
resolution problem, and `--scale` reports on it separately.

Usage (the log path defaults to wherever the logger writes, so it can be omitted):
    python -m backend.scripts.phone_report
    python -m backend.scripts.phone_report --tracks
    python -m backend.scripts.phone_report --scale
    python -m backend.scripts.phone_report --replay
    python -m backend.scripts.phone_report --replay --camera 3 \\
        --set association_min=0.25 --set confirm_hits=2

Capture the input with:
    PHONE_DEBUG=trace bash start.sh
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict

from backend.services.analytics.diagnostics import parse_log as parse, parse_value


# ── report ────────────────────────────────────────────────────────────────────
def report(records, show_tracks: bool, show_scale: bool):
    sessions = [f for _, f in records if f["event"] == "SESSION"]
    dets = [f for _, f in records if f["event"] == "DET"]
    evals = [f for _, f in records if f["event"] == "EVAL"]
    states = [f for _, f in records if f["event"] == "STATE"]
    rejects = [f for _, f in records if f["event"] == "REJECT"]
    ends = [f for _, f in records if f["event"] == "TRACK_END"]
    metrics = [f for _, f in records if f["event"] == "METRICS"]
    suppressed = [f for _, f in records if f["event"] == "SUPPRESSED"]

    print("=" * 78)
    print("PHONE-USE DIAGNOSTIC REPORT")
    print("=" * 78)

    if not sessions:
        print("\n!! No SESSION header found. The log came from an older build, or was")
        print("   truncated by rotation. The thresholds below cannot be verified.")
    for s in sessions:
        print(f"\n--- configuration (camera {s.get('camera')}) ---")
        for k, v in s.items():
            if k not in ("event", "camera"):
                print(f"    {k:<28} {v}")

    if records:
        span = records[-1][0] - records[0][0]
        print("\n--- capture ---")
        print(f"    duration                     {span:.1f} s")
        print(f"    lines parsed                 {len(records)}")
        print(f"    raw detections (DET)         {len(dets)}"
              f"{'' if dets else '  <- run at TRACE to record these'}")
        print(f"    evaluations (EVAL)           {len(evals)}"
              f"{'  <- replayable' if evals else '  <- run at DEBUG or TRACE'}")
        frames = [f["frame"] for f in (dets + evals) if isinstance(f.get("frame"), int)]
        if frames and span > 1.0:
            fps = (max(frames) - min(frames)) / span
            verdict = ("medium lane active" if fps >= 3
                       else "SLOW ONLY — phone cadence is not in effect")
            print(f"    phone evaluation rate        {fps:.1f} fps  <- {verdict}")

    confirmed = [s for s in states if s.get("decision") == "PHONE_USE"]
    released = [s for s in states if s.get("decision") == "RELEASE"]
    print("\n--- outcome ---")
    print(f"    confirmed phone use          {len(confirmed)}")
    print(f"    episodes released            {len(released)}")
    print(f"    events suppressed (cooldown) {len(suppressed)}")
    print(f"    rejected detections          {len(rejects)}")
    if rejects:
        for reason, n in Counter(r.get("reason") for r in rejects).most_common():
            print(f"        {reason:<32} {n}")
        print("\n    Reading the reasons:")
        print("      phone_not_associated_with_person  a phone with nobody near it")
        print("      phone_outside_upper_body          on a desk/floor, or below the")
        print("                                        hand band — tune hand_bottom")
        print("      phone_too_large_for_hand          a foreground object, not a phone")
        print("      association_below_threshold       near a person but not convincing")
        print("                                        — tune association_min")
        print("      insufficient_evidence             seen and associated, but never")
        print("                                        persistent enough to confirm")

    if metrics:
        print("\n--- last metrics snapshot ---")
        for k, v in metrics[-1].items():
            if k not in ("event", "note"):
                print(f"    {k:<28} {v}")

    if dets:
        assoc = [d for d in dets if isinstance(d.get("association"), (int, float))
                 and d["association"] > 0]
        print("\n--- association quality ---")
        print(f"    detections with a candidate  {len(assoc)} / {len(dets)}")
        if assoc:
            vals = sorted(d["association"] for d in assoc)
            print(f"    association score            min={vals[0]:.2f} "
                  f"median={statistics.median(vals):.2f} max={vals[-1]:.2f}")
        confs = [d["phone_conf"] for d in dets
                 if isinstance(d.get("phone_conf"), (int, float))]
        if confs:
            c = sorted(confs)
            print(f"    detector confidence          min={c[0]:.2f} "
                  f"median={statistics.median(c):.2f} max={c[-1]:.2f}")

    if show_scale:
        _scale_report(dets, evals)

    # What each gate actually COST. This is the tuning table: it says which filter to
    # relax when real phone use is being missed, instead of guessing.
    blocked = Counter(str(e.get("blocked_by")) for e in ends
                      if e.get("episodes") == 0 and e.get("detections", 0) > 0)
    if blocked:
        print("\n--- what stopped each track that DID have detections ---")
        for reason, n in blocked.most_common():
            hint = {
                "phone_did_not_move": "the motion gate - OFF by default; someone "
                                      "texting holds the phone still",
                "insufficient_evidence": "never reached confirm_hits - detector recall, "
                                         "or lower confirm_hits",
                "static_object": "suppressed as furniture - raise static_min_tracks "
                                 "or static_hard_window if this was a real phone",
                "no_evidence": "detections seen but none associated with a person",
                "-": "resolved normally",
            }.get(reason, "")
            print(f"    {reason:<26} {n:>4}   {hint}")

    # Detections the model DID see but the acting threshold discarded. Without this the
    # log cannot distinguish "the detector never saw it" from "the floor was too high",
    # and those have opposite fixes.
    ignored = [f for _, f in records
               if f["event"] == "DET" and f.get("reason") == "below_confidence"]
    if ignored:
        confs = sorted(f["phone_conf"] for f in ignored
                       if isinstance(f.get("phone_conf"), (int, float)))
        thr = ignored[0].get("threshold")
        print(f"\n--- seen by the detector, discarded by the confidence floor ({thr}) ---")
        print(f"    count                        {len(ignored)}")
        if confs:
            print(f"    their confidence             min={confs[0]:.2f} "
                  f"median={statistics.median(confs):.2f} max={confs[-1]:.2f}")
        # The number that matters is not how many were discarded, but how many of them
        # were plausibly a phone IN SOMEBODY'S HAND. Junk points at a floor that is fine;
        # good associations point at a floor that is too high.
        plausible = [f for f in ignored
                     if isinstance(f.get("association"), (int, float))
                     and f["association"] >= 0.35]
        print(f"    ...that WOULD have associated with a person: {len(plausible)}")
        if plausible:
            print(f"    {'floor':>7}{'admitted':>10}{'of those, associated':>22}")
            allc = [f for _, f in records if f["event"] == "DET"]
            for step in (0.15, 0.20, 0.25, 0.30, 0.35):
                n = sum(1 for f in allc
                        if isinstance(f.get("phone_conf"), (int, float))
                        and f["phone_conf"] >= step)
                m = sum(1 for f in plausible if f["phone_conf"] >= step)
                print(f"    {step:>7.2f}{n:>10}{m:>22}")
            print("    A floor with plausible associations still below it is too high.")

    print("\n--- tracks that saw a phone but never confirmed ---")
    misses = [e for e in ends if e.get("episodes") == 0 and e.get("detections", 0) > 0]
    if not misses:
        print("    none — every track with an associated detection reached a decision")
    else:
        print(f"    {len(misses)} of {len(ends)} completed tracks")
        print(f"    {'track':>8}{'detections':>12}{'best_conf':>11}"
              f"{'best_assoc':>12}{'final':>12}")
        for e in misses[:30]:
            print(f"    {e.get('track'):>8}{e.get('detections'):>12}"
                  f"{e.get('best_conf', 0):>11.2f}{e.get('best_association', 0):>12.2f}"
                  f"{str(e.get('final_state')):>12}")

    if show_tracks and ends:
        print("\n--- all completed tracks ---")
        print(f"    {'track':>8}{'episodes':>10}{'detections':>12}"
              f"{'best_conf':>11}{'best_assoc':>12}{'final':>12}")
        for e in ends:
            print(f"    {e.get('track'):>8}{e.get('episodes'):>10}"
                  f"{e.get('detections'):>12}{e.get('best_conf', 0):>11.2f}"
                  f"{e.get('best_association', 0):>12.2f}{str(e.get('final_state')):>12}")
    print()


def _scale_report(dets, evals):
    """Apparent phone and person size — the camera-resolution constraint (§26).

    If phones are consistently tiny here, no amount of threshold tuning will help and a
    model upgrade will not either; the crop strategy or the camera has to change."""
    print("\n--- apparent scale ---")
    phone_w = [abs(d["phone"][2] - d["phone"][0]) for d in dets
               if isinstance(d.get("phone"), list) and len(d["phone"]) == 4]
    person_w = [abs(e["person"][2] - e["person"][0]) for e in evals
                if isinstance(e.get("person"), list) and len(e["person"]) == 4]
    for label, vals in (("phone width (px)", phone_w), ("person width (px)", person_w)):
        if not vals:
            print(f"    {label:<22} no samples")
            continue
        q = sorted(vals)
        print(f"    {label:<22} min={q[0]:6.0f}  p10={q[len(q) // 10]:6.0f}  "
              f"median={statistics.median(q):6.0f}  max={q[-1]:6.0f}")
    if phone_w:
        tiny = sum(1 for v in phone_w if v < 16)
        print(f"    below the ~16 px detection floor: {tiny}/{len(phone_w)}")
        if tiny > len(phone_w) * 0.3:
            print("    WARNING: a large share of detections are at or below the size")
            print("    where YOLO's finest head can resolve them. Recall is limited by")
            print("    camera resolution, not by thresholds.")


# ── replay ────────────────────────────────────────────────────────────────────
def replay(records, overrides: dict, only_camera=None, quiet: bool = False):
    """Re-run association and the state machine over a captured scene.

    Rebuilds each evaluation from the EVAL lines (person boxes and, when present, the
    detection assigned to them) plus the DET lines (every detection considered)."""
    from backend.services.analytics.mobile import MobileUsage

    sessions = {f.get("camera"): f for _, f in records if f["event"] == "SESSION"}
    evals = [(ts, f) for ts, f in records if f["event"] == "EVAL"]
    dets = [(ts, f) for ts, f in records if f["event"] == "DET"]
    if not evals:
        print("Cannot replay: no EVAL records. Capture with PHONE_DEBUG=trace "
              "(or at least =debug).")
        return 1

    by_camera = defaultdict(list)
    for ts, f in evals:
        by_camera[f.get("camera")].append((ts, f))
    dets_by_camera = defaultdict(list)
    for ts, f in dets:
        dets_by_camera[f.get("camera")].append((ts, f))

    rc = 0
    total_quiet = [0]
    for cam, rows in sorted(by_camera.items(), key=lambda kv: str(kv[0])):
        if only_camera is not None and cam != only_camera:
            continue
        sess = sessions.get(cam, {})
        try:
            w, h = (int(v) for v in str(sess.get("shape", "1280x720")).split("x"))
        except ValueError:
            w, h = 1280, 720

        config = {"module": "mobile", "enabled": True}
        for key in ("association_min", "hand_top", "hand_bottom", "hand_pad",
                    "hand_falloff", "max_size_ratio", "confirm_hits",
                    "confirm_window", "release_misses", "cooldown", "state_ttl"):
            if key in sess:
                config[key] = sess[key]
        config.update(overrides)

        frames: dict = {}
        for ts, f in rows:
            frames.setdefault(f.get("frame"), (ts, [], []))[1].append(f)
        for ts, f in dets_by_camera.get(cam, []):
            slot = frames.get(f.get("frame"))
            if slot is not None:
                slot[2].append(f)

        mc = MobileUsage(cam_id=cam, config=config)
        clock = {"t": 0.0}
        mc._now = lambda: clock["t"]

        events = 0
        for frame_no in sorted(frames):
            ts, ev_rows, det_rows = frames[frame_no]
            clock["t"] = ts
            tracks = []
            for f in ev_rows:
                box = f.get("person")
                if not (isinstance(box, list) and len(box) == 4):
                    continue
                tracks.append({"id": f.get("track"),
                               "box": tuple(float(v) for v in box),
                               "centroid": (0, 0), "conf": 0.9})
            detections = []
            for f in det_rows:
                box = f.get("phone")
                if not (isinstance(box, list) and len(box) == 4):
                    continue
                detections.append({"box": tuple(float(v) for v in box),
                                   "conf": float(f.get("phone_conf") or 0.0),
                                   "source": str(f.get("source", "?"))})
            evs, _ = mc.process_phones(tracks, detections, (h, w))
            events += len(evs)

        if quiet:
            total_quiet[0] += events         # sweeping: only the totals matter
            continue

        logged = sum(1 for _, f in records
                     if f["event"] == "STATE" and f.get("camera") == cam
                     and f.get("decision") == "PHONE_USE")
        print(f"\n--- replay: camera {cam} ---")
        print(f"    evaluations replayed         {len(frames)}")
        print(f"    config overrides             {overrides or 'none'}")
        print(f"    confirmed when live          {logged}")
        print(f"    confirmed on replay          {events}")
        if not overrides:
            ok = events == logged
            print(f"    fidelity                     "
                  f"{'MATCH' if ok else 'MISMATCH — replay is not faithful'}")
            if not ok:
                rc = 1
                print("    (a mismatch usually means the log was captured below TRACE,")
                print("     so not every detection considered was recorded)")
    if quiet:
        return total_quiet[0]
    print()
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile", nargs="?", default=None,
                    help="path to phone_debug.log (default: the logger's location)")
    ap.add_argument("--tracks", action="store_true", help="list every completed track")
    ap.add_argument("--scale", action="store_true",
                    help="report apparent phone/person size vs the detection floor")
    ap.add_argument("--replay", action="store_true",
                    help="re-run association + confirmation over the captured scene")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a threshold during replay (repeatable)")
    ap.add_argument("--camera", type=int, default=None, metavar="ID",
                    help="replay only this camera")
    ap.add_argument("--sweep", metavar="KEY",
                    help="replay repeatedly, varying KEY, and report alerts at each "
                         "value (e.g. --sweep phone_conf). Picks a threshold from "
                         "evidence instead of one guess at a time.")
    ap.add_argument("--sweep-values", default=None,
                    help="comma-separated values for --sweep")
    args = ap.parse_args(argv)

    from backend.services.analytics.phone_debug import log_path

    path = args.logfile
    if path is None:
        path = log_path()
        print(f"reading {path}")

    try:
        records = parse(path)
    except OSError as e:
        print(f"cannot read {path}: {e}")
        print(f"hint: run with no argument to read the default location ({log_path()}), "
              f"or pass a path using forward slashes.")
        return 2
    if not records:
        print(f"{path}: no diagnostic records found "
              f"(was PHONE_DEBUG=trace set when the server ran?)")
        return 2

    report(records, args.tracks, args.scale)

    overrides = {}
    for item in args.set:
        k, _, v = item.partition("=")
        overrides[k.strip()] = parse_value(v.strip())

    if args.sweep:
        defaults = {"phone_conf": "0.15,0.20,0.25,0.30,0.35",
                    "association_min": "0.20,0.30,0.35,0.45,0.60",
                    "confirm_hits": "2,3,4,5"}
        raw = args.sweep_values or defaults.get(args.sweep)
        if not raw:
            print(f"--sweep {args.sweep} needs --sweep-values")
            return 2
        print(f"\n--- sweeping {args.sweep} ---")
        print(f"    {args.sweep:>18}{'alerts':>10}")
        for v in [parse_value(x.strip()) for x in raw.split(",")]:
            n = replay(records, {**overrides, args.sweep: v}, args.camera, quiet=True)
            print(f"    {str(v):>18}{n:>10}")
        print("    Choose from the shape of this curve, not from one value at a time.")
        return 0

    if args.replay:
        return replay(records, overrides, args.camera)
    return 0


if __name__ == "__main__":
    sys.exit(main())
