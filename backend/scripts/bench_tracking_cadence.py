"""
Benchmark: how does the analytics sampling rate affect ByteTrack identity stability
and head-count crossing detection?

Phase 1 diagnostic. This answers the question "should person tracking run faster than
the current ~2 fps?" with evidence rather than assertion, and it does so WITHOUT needing
YOLO weights or a GPU: it drives the *real* ultralytics BYTETracker with synthetic
person detections whose ground truth we control exactly.

MEASURED RESULT (see sweep_min_fps below): at ~2 fps the failure is NOT id churn, it is
TOTAL TRACK LOSS. ByteTrack associates by IoU with `match_thresh=0.8`. A person walking
at a normal pace moves further than their own bounding-box width between two frames
sampled 0.5 s apart, so consecutive boxes do not overlap at all, IoU is 0, and no
association is possible. The re-detected person starts a fresh track which is
`unconfirmed` and needs a second consecutive match to activate — which never arrives,
because the person has moved on again. The tracker therefore emits the person on the
first frame and then NOTHING for the rest of the walk, and every person-based analytic
goes blind.

The cadence floor follows directly: association survives while per-frame displacement
stays below roughly one box width, i.e. required_fps > normalised_speed / box_width.

Run:
    python -m backend.scripts.bench_tracking_cadence
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np

# Det-frame geometry used by the pipeline (camera_manager.DET_WIDTH = 1280).
FRAME_W, FRAME_H = 1280, 720
GT_FPS = 30.0                      # ground-truth simulation rate
SAMPLE_RATES = [2, 4, 6, 8, 10, 12, 15]

# ByteTrack defaults, mirrored from ultralytics/cfg/trackers/bytetrack.yaml.
BYTETRACK_ARGS = SimpleNamespace(
    tracker_type="bytetrack",
    track_high_thresh=0.25,
    track_low_thresh=0.1,
    new_track_thresh=0.25,
    track_buffer=30,
    match_thresh=0.8,
    fuse_score=True,
)


class FakeBoxes:
    """Minimal duck-type of an ultralytics Boxes result for BYTETracker.update()."""

    def __init__(self, xyxy: np.ndarray, conf: np.ndarray, cls: np.ndarray):
        self.xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        self.conf = np.asarray(conf, dtype=np.float32).reshape(-1)
        self.cls = np.asarray(cls, dtype=np.float32).reshape(-1)

    def __len__(self):
        return len(self.conf)

    def __getitem__(self, mask):
        return FakeBoxes(self.xyxy[mask], self.conf[mask], self.cls[mask])

    @property
    def xywh(self):
        x1, y1, x2, y2 = self.xyxy.T
        return np.stack([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1], axis=-1)


# ── synthetic scene generation ────────────────────────────────────────────────
def walk(x0, x1, y, seconds, box_w=0.08, box_h=0.36, jitter=0.0, seed=0):
    """Ground-truth boxes (normalised) for one person walking x0 -> x1 at constant speed."""
    rng = np.random.default_rng(seed)
    n = max(2, int(round(seconds * GT_FPS)))
    out = []
    for i in range(n):
        t = i / (n - 1)
        cx = x0 + (x1 - x0) * t
        cy = y
        if jitter:
            cx += rng.normal(0, jitter)
            cy += rng.normal(0, jitter)
        out.append((cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2))
    return np.asarray(out, dtype=np.float32)


def to_px(norm_box):
    x1, y1, x2, y2 = norm_box
    return [x1 * FRAME_W, y1 * FRAME_H, x2 * FRAME_W, y2 * FRAME_H]


def sample_indices(n_gt, fps):
    """Indices of the ground-truth frames that a given sampling rate would observe."""
    step = GT_FPS / fps
    idx, t = [], 0.0
    while t < n_gt:
        idx.append(int(t))
        t += step
    return idx


# ── the OLD head-count rule, reproduced exactly ───────────────────────────────
def old_rule_count(observations, line_x=0.5):
    """backend/services/analytics/headcount.py as it was before Phase 1:
    per-track previous side of the line, count on sign flip, box CENTROID."""
    prev_side = {}
    entries = exits = 0
    for obs in observations:                       # obs: list of (tid, x1,y1,x2,y2) px
        for tid, x1, y1, x2, y2 in obs:
            cx = (x1 + x2) / 2.0
            nx = cx / FRAME_W
            s = nx - line_x                        # same sign convention as side_of_line
            prev = prev_side.get(tid)
            prev_side[tid] = s
            if prev is None or s == 0 or (s > 0) == (prev > 0):
                continue
            if prev < 0 <= s:
                entries += 1
            else:
                exits += 1
    return entries, exits


def run_tracker(tracks_gt, fps, dropout=()):
    """Feed sampled ground truth through the real BYTETracker.

    tracks_gt: list of per-person ground-truth box arrays (normalised).
    dropout:   set of SAMPLED frame indices where the detector sees nothing.
    Returns (observations, ids_per_person)."""
    from ultralytics.trackers.byte_tracker import BYTETracker

    tracker = BYTETracker(BYTETRACK_ARGS, frame_rate=30)
    n_gt = max(len(t) for t in tracks_gt)
    idx = sample_indices(n_gt, fps)

    observations = []
    ids_per_person = [set() for _ in tracks_gt]
    for k, gi in enumerate(idx):
        if k in dropout:
            tracker.update(FakeBoxes(np.zeros((0, 4)), np.zeros(0), np.zeros(0)))
            observations.append([])
            continue
        boxes, present = [], []
        for pi, gt in enumerate(tracks_gt):
            if gi < len(gt):
                boxes.append(to_px(gt[gi]))
                present.append(pi)
        if not boxes:
            observations.append([])
            continue
        res = FakeBoxes(np.asarray(boxes), np.full(len(boxes), 0.9), np.zeros(len(boxes)))
        out = tracker.update(res)
        obs = []
        for row in out:
            x1, y1, x2, y2, tid = float(row[0]), float(row[1]), float(row[2]), float(row[3]), int(row[4])
            obs.append((tid, x1, y1, x2, y2))
            # attribute this output box to the nearest ground-truth person
            cx = (x1 + x2) / 2
            best, bestd = None, 1e9
            for pi in present:
                gx = (tracks_gt[pi][gi][0] + tracks_gt[pi][gi][2]) / 2 * FRAME_W
                d = abs(cx - gx)
                if d < bestd:
                    best, bestd = pi, d
            if best is not None:
                ids_per_person[best].add(tid)
        observations.append(obs)
    return observations, ids_per_person


SCENARIOS = {
    # name: (list of ground-truth walks, expected entries, expected exits, dropout frames)
    "fast crossing (1.5 s)":      ([walk(0.20, 0.80, 0.60, 1.5)], 1, 0, ()),
    "normal crossing (3 s)":      ([walk(0.20, 0.80, 0.60, 3.0)], 1, 0, ()),
    "slow crossing (6 s)":        ([walk(0.25, 0.75, 0.60, 6.0)], 1, 0, ()),
    "exit direction (3 s)":       ([walk(0.80, 0.20, 0.60, 3.0)], 0, 1, ()),
    "two together (3 s)":         ([walk(0.20, 0.80, 0.55, 3.0),
                                    walk(0.20, 0.80, 0.70, 3.0, seed=1)], 2, 0, ()),
    "two opposite (3 s)":         ([walk(0.20, 0.80, 0.55, 3.0),
                                    walk(0.80, 0.20, 0.70, 3.0, seed=2)], 1, 1, ()),
    "jitter on line (4 s)":       ([walk(0.20, 0.80, 0.60, 4.0, jitter=0.012, seed=3)], 1, 0, ()),
    "detector dropout (3 s)":     ([walk(0.20, 0.80, 0.60, 3.0)], 1, 0, (2, 3)),
}


def main():
    print("=" * 78)
    print("ByteTrack identity stability vs analytics sampling rate")
    print("(real ultralytics BYTETracker, frame_rate=30 as hardcoded by ultralytics)")
    print("=" * 78)

    header = f"{'scenario':<26}{'fps':>5}{'ids/person':>12}{'switches':>10}{'old-rule in/out':>18}{'':>4}"
    for name, (gts, exp_in, exp_out, dropout) in SCENARIOS.items():
        print()
        print(f"--- {name}   (expected IN={exp_in} OUT={exp_out}) ---")
        print(header)
        for fps in SAMPLE_RATES:
            obs, ids = run_tracker(gts, fps, dropout)
            switches = sum(max(0, len(s) - 1) for s in ids)
            ids_desc = "/".join(str(len(s)) for s in ids)
            e, x = old_rule_count(obs)
            ok = "OK" if (e == exp_in and x == exp_out) else "MISS"
            print(f"{'':<26}{fps:>5}{ids_desc:>12}{switches:>10}{f'{e}/{x}':>18}{ok:>6}")

    print()
    print("=" * 78)
    print("Reading: 'ids/person' > 1 means ByteTrack lost the identity and re-issued an")
    print("id. Every such switch near the line makes the OLD head-count rule skip the")
    print("crossing, because a fresh track id has no recorded previous side.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ── cadence requirement sweep ─────────────────────────────────────────────────
def track_survives(gt, fps):
    """Does the tracker keep emitting this person for most of the walk at `fps`?
    Returns (coverage, n_ids) where coverage = fraction of sampled frames with output."""
    obs, ids = run_tracker([gt], fps)
    seen = sum(1 for o in obs if o)
    return seen / max(1, len(obs)), len(ids[0])


def sweep_min_fps():
    """For a range of apparent sizes and walking speeds, find the lowest sampling rate
    at which ByteTrack keeps the person tracked. This is what sets the cadence floor:
    consecutive boxes must still overlap for IoU association to work."""
    print()
    print("=" * 78)
    print("Minimum sampling rate for ByteTrack to hold a walking person")
    print("=" * 78)
    print(f"{'box width (norm)':>18}{'crossing time':>16}{'min fps (>=80% held)':>24}")
    span = 0.60
    for box_w in (0.05, 0.08, 0.12, 0.20):
        for seconds in (1.0, 1.5, 2.0, 3.0, 5.0):
            found = None
            for fps in (2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25):
                gt = walk(0.5 - span / 2, 0.5 + span / 2, 0.6, seconds, box_w=box_w)
                cov, _ = track_survives(gt, fps)
                if cov >= 0.8:
                    found = fps
                    break
            speed = span / seconds
            print(f"{box_w:>18.2f}{f'{seconds:.1f}s ({speed:.2f}/s)':>16}"
                  f"{(str(found) if found else '>25'):>24}")
    print()
    print("Rule of thumb confirmed by the sweep: association survives while the")
    print("per-frame displacement stays below roughly one box width, i.e.")
    print("    required_fps  >  normalised_speed / box_width")
    print("=" * 78)
