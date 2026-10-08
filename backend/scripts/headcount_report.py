"""
Analyse a captured head-count diagnostic log — and replay it.

Two jobs:

  REPORT   Summarise what the head-count module did: how many crossings it committed,
           what it rejected and why, which tracks came near the line and counted nothing.
           That last group is the interesting one — it is the "why was this person
           missed?" list.

  REPLAY   Rebuild the person tracks from the log's OBS trajectory records and push them
           back through the real HeadCount consumer. Because the log carries every box on
           every frame (at TRACE level), a real-world miss becomes a reproducible test
           case WITHOUT the original video — and thresholds can be re-tuned against the
           actual scene by overriding them on the command line.

Usage:
    python -m backend.scripts.headcount_report logs/headcount_debug.log
    python -m backend.scripts.headcount_report logs/headcount_debug.log --tracks
    python -m backend.scripts.headcount_report logs/headcount_debug.log --replay
    python -m backend.scripts.headcount_report logs/headcount_debug.log --replay \\
        --set crossing_band=0.05 --set confirm_observations=3

Capture the input with:
    HEADCOUNT_DEBUG=trace bash start.sh
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict



# ── parsing ───────────────────────────────────────────────────────────────────
# The format and its parser live with the writer, in analytics/diagnostics.py, so the
# head-count and phone tools cannot drift apart.
from backend.services.analytics.diagnostics import (            # noqa: E402
    parse_log as parse, parse_value as _parse_value,
)


# ── report ────────────────────────────────────────────────────────────────────
def report(records, show_tracks: bool):
    sessions = [f for _, f in records if f["event"] == "SESSION"]
    commits = [f for _, f in records if f["event"] == "COMMIT"]
    rejects = [f for _, f in records if f["event"] == "REJECT"]
    handoffs = [f for _, f in records if f["event"] == "ID_HANDOFF"]
    turnbacks = [f for _, f in records if f["event"] == "TURNED_BACK"]
    ends = [f for _, f in records if f["event"] == "TRACK_END"]
    obs = [f for _, f in records if f["event"] == "OBS"]
    metrics = [f for _, f in records if f["event"] == "METRICS"]

    print("=" * 78)
    print("HEAD-COUNT DIAGNOSTIC REPORT")
    print("=" * 78)

    if not sessions:
        print("\n!! No SESSION header found. The log was written by an older build, or")
        print("   truncated by rotation. Thresholds below cannot be verified.")
    for s in sessions:
        print(f"\n--- configuration (camera {s.get('camera')}) ---")
        for k, v in s.items():
            if k in ("event", "camera"):
                continue
            print(f"    {k:<28} {v}")

    if records:
        span = records[-1][0] - records[0][0]
        print("\n--- capture ---")
        print(f"    duration                     {span:.1f} s")
        print(f"    lines parsed                 {len(records)}")
        print(f"    trajectory records (OBS)     {len(obs)}"
              f"{'  <- replayable' if obs else '  <- run at TRACE for replay'}")

        # Effective analytics rate. This is the single number that says whether the
        # fast tracking lane is actually running on this machine: ~10/s means it is,
        # ~2/s means frames are only arriving on the slow lane (module not in
        # FAST_MODULES, TRACK_INTERVAL overridden, or the perception queue is
        # saturated and dropping frames).
        frame_nos = [f["frame"] for f in obs if isinstance(f.get("frame"), int)]
        if frame_nos and span > 1.0:
            fps = (max(frame_nos) - min(frame_nos)) / span
            if fps >= 6:
                verdict = "fast lane active"
            elif fps >= 3:
                verdict = "degraded — check GPU load / queue drops"
            else:
                verdict = "SLOW LANE ONLY — the cadence fix is not in effect"
            print(f"    analytics rate               {fps:.1f} fps  <- {verdict}")

    print("\n--- outcome ---")
    entries = sum(1 for c in commits if c.get("decision") == "ENTRY")
    exits = sum(1 for c in commits if c.get("decision") == "EXIT")
    print(f"    crossings committed          {len(commits)}  (entry={entries} exit={exits})")
    print(f"    id hand-offs                 {len(handoffs)}")
    print(f"    turn-backs at the line       {len(turnbacks)}")
    print(f"    genuine rejections           {len(rejects)}")
    if rejects:
        for reason, n in Counter(r.get("reason") for r in rejects).most_common():
            print(f"        {reason:<28} {n}")
    if metrics:
        print("\n--- last metrics snapshot ---")
        for k, v in metrics[-1].items():
            if k not in ("event", "note"):
                print(f"    {k:<28} {v}")

    # Where along the DRAWN segment did the crossings happen? t outside [0,1] means the
    # crossing was of the line's infinite extension, past the endpoint the operator drew.
    seg_ts = [(c.get("camera"), c.get("track"), c.get("decision"), c["seg_t"])
              for c in commits if isinstance(c.get("seg_t"), (int, float))]
    if seg_ts:
        outside = [x for x in seg_ts if not (0.0 <= x[3] <= 1.0)]
        print("\n--- crossing position along the drawn line (0=start, 1=end) ---")
        print(f"    within the drawn segment     {len(seg_ts) - len(outside)} / {len(seg_ts)}")
        if outside:
            print(f"    BEYOND an endpoint           {len(outside)}")
            for cam, tid, dec, t in outside:
                print(f"        camera={cam} track={tid} {dec} t={t:.3f}")
            print("    These crossed the line's extension, not the line. Either the line")
            print("    is drawn too short for the doorway, or people are walking around")
            print("    its end. segment_margin controls the tolerance.")

    # The diagnosis: tracks that approached the line and produced nothing.
    near_misses = [e for e in ends
                   if e.get("crossings") == 0
                   and isinstance(e.get("closest_approach"), (int, float))
                   and e["closest_approach"] < 0.08]
    print("\n--- tracks that came close and counted NOTHING ---")
    if not near_misses:
        print("    none — every track that approached the line was resolved")
    else:
        print(f"    {len(near_misses)} of {len(ends)} completed tracks")
        print(f"    {'track':>8}{'obs':>6}{'closest':>10}{'reached':>12}"
              f"{'last_reject':>26}{'frames':>14}")
        for e in near_misses[:40]:
            print(f"    {e.get('track'):>8}{e.get('observations'):>6}"
                  f"{e.get('closest_approach', 0):>10.4f}{str(e.get('reached')):>12}"
                  f"{str(e.get('last_reject')):>26}{str(e.get('frames')):>14}")
        if len(near_misses) > 40:
            print(f"    ... and {len(near_misses) - 40} more")
        print("\n    Reading this table:")
        print("      reached=BEFORE    never entered the band — the line may be drawn")
        print("                        where people do not actually walk")
        print("      reached=NEAR_LINE entered the band but never came out the far side")
        print("                        — a genuine turn-back, or the track was lost")
        print("      reached=AFTER     got to the far side but failed validation; the")
        print("                        last_reject column says which rule stopped it")

    if show_tracks and ends:
        print("\n--- all completed tracks ---")
        print(f"    {'track':>8}{'obs':>6}{'cross':>7}{'closest':>10}"
              f"{'reached':>12}{'speed':>9}{'frames':>14}")
        for e in ends:
            print(f"    {e.get('track'):>8}{e.get('observations'):>6}"
                  f"{e.get('crossings'):>7}{e.get('closest_approach', 0):>10.4f}"
                  f"{str(e.get('reached')):>12}{e.get('speed', 0):>9.4f}"
                  f"{str(e.get('frames')):>14}")
    print()


# ── replay ────────────────────────────────────────────────────────────────────
def replay(records, overrides: dict, only_camera=None):
    """Rebuild the scene from OBS records and re-run the real consumer over it."""
    from backend.services.analytics.headcount import HeadCount

    sessions = [f for _, f in records if f["event"] == "SESSION"]
    if not sessions:
        print("Cannot replay: no SESSION header, so the line geometry is unknown.")
        return 1
    obs = [(ts, f) for ts, f in records if f["event"] == "OBS"]
    if not obs:
        print("Cannot replay: no OBS records. Capture with HEADCOUNT_DEBUG_LEVEL=TRACE.")
        return 1

    by_camera = defaultdict(list)
    for ts, f in obs:
        by_camera[f.get("camera")].append((ts, f))

    rc = 0
    for cam, rows in sorted(by_camera.items(), key=lambda kv: str(kv[0])):
        if only_camera is not None and cam != only_camera:
            continue
        sess = next((s for s in sessions if s.get("camera") == cam), sessions[0])
        shape_raw = str(sess.get("shape", "1280x720"))
        try:
            w, h = (int(v) for v in shape_raw.split("x"))
        except ValueError:
            w, h = 1280, 720

        line = sess.get("line")
        if not (isinstance(line, list) and len(line) >= 2):
            print(f"camera {cam}: unusable line geometry {line!r} — skipped")
            rc = 1
            continue

        config = {"module": "headcount", "enabled": True, "line": line}
        for key in ("crossing_band", "confirm_observations", "min_track_age",
                    "min_displacement", "min_direction_consistency",
                    "crossing_cooldown", "handoff_radius", "handoff_max_gap",
                    "handoff_ttl", "handoff_speed_tol", "state_ttl",
                    "reference_point"):
            if key in sess:
                config[key] = sess[key]
        config.update(overrides)

        # Group observations into frames, preserving the log's own wall clock so that
        # cooldowns and TTLs behave exactly as they did live.
        frames: dict = {}
        for ts, f in rows:
            frames.setdefault(f.get("frame"), (ts, []))[1].append(f)

        hc = HeadCount(cam_id=cam, config=config)
        clock = {"t": 0.0}
        hc._now = lambda: clock["t"]

        # Frames with no person produce no OBS records, so the log is sparse. Replaying
        # only the populated frames would compress those gaps away, and the consumer's
        # frame-difference logic (id hand-off gap, track re-appearance) would see people
        # as adjacent who were really minutes apart. Re-insert the empty frames so the
        # frame numbering matches the live run exactly.
        ordered = sorted(frames)
        got_in = got_out = 0
        expected = ordered[0]
        MAX_FILL = 2000                 # don't materialise an idle hour frame by frame
        for frame_no in ordered:
            ts, rowset = frames[frame_no]
            missing = frame_no - expected
            if missing > MAX_FILL:
                # Long idle period: jump the counter and run one empty frame so state
                # purging still happens, rather than iterating for minutes.
                hc.frame_no = frame_no - 2
                missing = 1
            for _ in range(max(0, missing)):
                clock["t"] = ts
                hc.process([], [], (h, w))
            expected = frame_no + 1

            clock["t"] = ts
            tracks = []
            for f in rowset:
                box = f.get("box")
                if not (isinstance(box, list) and len(box) == 4):
                    continue
                x1, y1, x2, y2 = (float(v) for v in box)
                tracks.append({"id": f.get("track"), "box": (x1, y1, x2, y2),
                               "centroid": ((x1 + x2) / 2, (y1 + y2) / 2),
                               "conf": float(f.get("conf") or 0.0)})
            evs, _ = hc.process(tracks, [], (h, w))
            for e in evs:
                if e["event_type"] == "entry":
                    got_in += 1
                else:
                    got_out += 1

        logged = [f for _, f in records
                  if f["event"] == "COMMIT" and f.get("camera") == cam]
        log_in = sum(1 for c in logged if c.get("decision") == "ENTRY")
        log_out = sum(1 for c in logged if c.get("decision") == "EXIT")

        print(f"\n--- replay: camera {cam} ---")
        print(f"    frames replayed              {len(frames)} populated "
              f"({ordered[-1] - ordered[0] + 1} including gaps)")
        print(f"    config overrides             {overrides or 'none'}")
        print(f"    counted when live            in={log_in} out={log_out}")
        print(f"    counted on replay            in={got_in} out={got_out}")
        if not overrides:
            ok = (got_in, got_out) == (log_in, log_out)
            print(f"    fidelity                     "
                  f"{'MATCH' if ok else 'MISMATCH — replay is not faithful'}")
            if not ok:
                rc = 1
                print("    (a mismatch usually means the log was captured at DEBUG, so")
                print("     tracks away from the line are missing from the trajectory)")
    print()
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile", nargs="?", default=None,
                    help="path to headcount_debug.log "
                         "(default: the location the logger writes to)")
    ap.add_argument("--tracks", action="store_true", help="list every completed track")
    ap.add_argument("--replay", action="store_true",
                    help="re-run the captured scene through the real consumer")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a threshold during replay (repeatable)")
    ap.add_argument("--camera", type=int, default=None, metavar="ID",
                    help="replay only this camera — overrides apply to every camera, "
                         "so per-camera tuning (e.g. a different line) needs this")
    args = ap.parse_args(argv)

    from backend.services.analytics.headcount_debug import log_path

    path = args.logfile
    if path is None:
        path = log_path()
        print(f"reading {path}")

    try:
        records = parse(path)
    except OSError as e:
        print(f"cannot read {path}: {e}")
        # Checking the argument for a backslash would be pointless: a POSIX shell
        # consumes it as an escape before Python ever sees it, which is exactly how
        # "logs\headcount_debug.log" arrives here as "logsheadcount_debug.log". The
        # advice that always works is to let the tool find the file itself.
        print(f"hint: run with no argument to read the default location "
              f"({log_path()}), or pass a path using forward slashes.")
        return 2
    if not records:
        print(f"{path}: no diagnostic records found "
              f"(was HEADCOUNT_DEBUG=trace set when the server ran?)")
        return 2

    report(records, args.tracks)

    if args.replay:
        overrides = {}
        for item in args.set:
            k, _, v = item.partition("=")
            overrides[k.strip()] = _parse_value(v.strip())
        return replay(records, overrides, args.camera)
    return 0


if __name__ == "__main__":
    sys.exit(main())
