"""
Phase 1 head-count regression suite.

No ground-truth CCTV recordings exist in this repository (media/ is git-ignored and
empty), so objective accuracy on real footage cannot be measured here. What this suite
does instead is drive the head-count consumer with DETERMINISTIC synthetic input where
the ground truth is known by construction, at two levels of realism:

  Tier A  tracks injected directly into HeadCount.process(). Exercises the crossing
          state machine with exact control over id switches, dropped frames and jitter.

  Tier B  synthetic person boxes pushed through the REAL ultralytics BYTETracker at a
          given sampling rate, then into HeadCount. Exercises cadence and tracker
          behaviour together — this is what actually regressed at ~2 fps.

Run as a report:   python -m tests.test_headcount
Run as tests:      pytest tests/test_headcount.py
"""

from __future__ import annotations

import sys

import pytest

from backend.services.analytics.headcount import HeadCount

FRAME_W, FRAME_H = 1280, 720
SHAPE = (FRAME_H, FRAME_W)

# Horizontal counting line across the middle of the frame. With this orientation,
# walking DOWN the frame (increasing y) is an `entry` and walking UP is an `exit`.
LINE = [[0.0, 0.5], [1.0, 0.5]]

BOX_W, BOX_H = 0.08, 0.36          # normalised person box


class Clock:
    """Deterministic clock injected into the consumer."""

    def __init__(self, fps: float):
        self.t = 1000.0
        self.dt = 1.0 / fps

    def tick(self):
        self.t += self.dt

    def __call__(self):
        return self.t


def make_consumer(fps: float = 10.0, **config):
    cfg = {"module": "headcount", "enabled": True, "line": LINE}
    cfg.update(config)
    hc = HeadCount(cam_id=1, config=cfg)
    clock = Clock(fps)
    hc._now = clock
    return hc, clock


def track(tid, foot_x, foot_y, box_w=BOX_W, box_h=BOX_H, conf=0.9):
    """Build a perception-style track dict (box in det-frame PIXELS) from a foot point."""
    x1 = (foot_x - box_w / 2) * FRAME_W
    x2 = (foot_x + box_w / 2) * FRAME_W
    y2 = foot_y * FRAME_H
    y1 = (foot_y - box_h) * FRAME_H
    return {"id": tid, "box": (x1, y1, x2, y2),
            "centroid": ((x1 + x2) / 2, (y1 + y2) / 2), "conf": conf}


def run(hc, clock, frames):
    """Feed a list of per-frame track lists; return all emitted events."""
    events = []
    for tracks in frames:
        evs, _ = hc.process(tracks, [], SHAPE)
        events.extend(evs)
        clock.tick()
    return events


def counts(events):
    return (sum(1 for e in events if e["event_type"] == "entry"),
            sum(1 for e in events if e["event_type"] == "exit"))


def linear(y0, y1, n):
    """n evenly spaced y positions from y0 to y1 inclusive."""
    if n == 1:
        return [y0]
    return [y0 + (y1 - y0) * i / (n - 1) for i in range(n)]


# ══════════════════════════════════════════════════════════════════════════════
# Tier A — crossing state machine
# ══════════════════════════════════════════════════════════════════════════════

def test_01_single_person_enters():
    hc, clock = make_consumer()
    frames = [[track(1, 0.5, y)] for y in linear(0.20, 0.80, 12)]
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_02_single_person_exits():
    hc, clock = make_consumer()
    frames = [[track(1, 0.5, y)] for y in linear(0.80, 0.20, 12)]
    assert counts(run(hc, clock, frames)) == (0, 1)


def test_03_approach_then_turn_back():
    hc, clock = make_consumer()
    ys = linear(0.20, 0.48, 8) + linear(0.48, 0.20, 8)
    frames = [[track(1, 0.5, y)] for y in ys]
    assert counts(run(hc, clock, frames)) == (0, 0)


def test_04_stands_on_the_line():
    hc, clock = make_consumer()
    ys = linear(0.20, 0.50, 6) + [0.500, 0.501, 0.499, 0.500, 0.502, 0.498] * 4
    frames = [[track(1, 0.5, y)] for y in ys]
    assert counts(run(hc, clock, frames)) == (0, 0)


def test_05_slow_crossing():
    hc, clock = make_consumer()
    frames = [[track(1, 0.5, y)] for y in linear(0.35, 0.65, 40)]
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_06_fast_crossing_skips_the_band_entirely():
    """A fast walker can jump from one side to the other between two frames without
    ever being observed inside the band. That must still count."""
    hc, clock = make_consumer()
    frames = [[track(1, 0.5, y)] for y in (0.20, 0.32, 0.44, 0.58, 0.70, 0.82)]
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_07_two_people_cross_together():
    hc, clock = make_consumer()
    frames = [[track(1, 0.35, y), track(2, 0.65, y)] for y in linear(0.20, 0.80, 12)]
    assert counts(run(hc, clock, frames)) == (2, 0)


def test_08_two_people_cross_in_opposite_directions():
    hc, clock = make_consumer()
    down = linear(0.20, 0.80, 12)
    up = linear(0.80, 0.20, 12)
    frames = [[track(1, 0.35, a), track(2, 0.65, b)] for a, b in zip(down, up)]
    assert counts(run(hc, clock, frames)) == (1, 1)


def test_09_partial_occlusion():
    """Box narrows and the foot point wobbles while the person is partly hidden."""
    hc, clock = make_consumer()
    frames = []
    for i, y in enumerate(linear(0.20, 0.80, 12)):
        occluded = 4 <= i <= 7
        frames.append([track(1, 0.5 + (0.01 if occluded else 0.0), y,
                             box_w=BOX_W * (0.5 if occluded else 1.0),
                             conf=0.45 if occluded else 0.9)])
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_10_temporary_detector_loss():
    """Two consecutive frames with no detection at all, right at the line."""
    hc, clock = make_consumer()
    frames = []
    for i, y in enumerate(linear(0.20, 0.80, 12)):
        frames.append([] if i in (5, 6) else [track(1, 0.5, y)])
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_11_jitter_around_the_line():
    """Box oscillating across the line must not manufacture crossings."""
    hc, clock = make_consumer()
    ys = linear(0.20, 0.49, 6)
    for _ in range(10):
        ys += [0.515, 0.485]          # oscillation wider than the 0.03 band
    frames = [[track(1, 0.5, y)] for y in ys]
    entries, exits = counts(run(hc, clock, frames))
    assert entries + exits <= 1, f"jitter produced {entries} entries / {exits} exits"


def test_12_walking_parallel_to_the_line():
    hc, clock = make_consumer()
    frames = [[track(1, x, 0.56)] for x in linear(0.10, 0.90, 16)]
    assert counts(run(hc, clock, frames)) == (0, 0)


# ── additional Phase 1 requirements ──────────────────────────────────────────

def test_13_bytetrack_id_switch_is_recovered_by_handoff():
    """The id changes mid-crossing, exactly as ByteTrack does when association fails.
    The OLD rule lost this crossing outright; the hand-off must recover it."""
    hc, clock = make_consumer()
    ys = linear(0.20, 0.80, 12)
    frames = [[track(1 if i < 5 else 99, 0.5, y)] for i, y in enumerate(ys)]
    assert counts(run(hc, clock, frames)) == (1, 0)
    assert hc.metrics.track_id_switches_near_line >= 1


def test_14_duplicate_crossings_are_debounced_per_track():
    """One person bouncing over the line repeatedly counts at most once per cooldown."""
    hc, clock = make_consumer(fps=10.0)
    ys = []
    for _ in range(6):
        ys += linear(0.40, 0.60, 4) + linear(0.60, 0.40, 4)
    frames = [[track(1, 0.5, y)] for y in ys]
    entries, exits = counts(run(hc, clock, frames))
    assert entries + exits <= 3, f"debounce failed: {entries} in / {exits} out"
    assert hc.metrics.duplicate_crossings_prevented >= 1


def test_15_debounce_is_per_track_not_global():
    """A second person crossing at the same moment must still be counted."""
    hc, clock = make_consumer()
    ys = linear(0.20, 0.80, 12)
    frames = [[track(1, 0.35, y), track(2, 0.65, y)] for y in ys]
    assert counts(run(hc, clock, frames)) == (2, 0)


def test_16_same_track_may_cross_again_after_the_cooldown():
    hc, clock = make_consumer(fps=10.0, crossing_cooldown=1.0)
    ys = linear(0.20, 0.80, 12) + [0.80] * 15 + linear(0.80, 0.20, 12)
    frames = [[track(1, 0.5, y)] for y in ys]
    assert counts(run(hc, clock, frames)) == (1, 1)


def test_17_direction_semantics_match_the_old_implementation():
    """The pre-Phase-1 rule counted `entry` when side_of_line went negative -> positive.
    That must not have silently reversed."""
    from backend.services.analytics.base import side_of_line

    (ax, ay), (bx, by) = LINE[0], LINE[1]
    old_entries = old_exits = 0
    prev = None
    for y in linear(0.20, 0.80, 40):
        s = side_of_line(0.5, y, ax, ay, bx, by)
        if prev is not None and s != 0 and (s > 0) != (prev > 0):
            if prev < 0 <= s:
                old_entries += 1
            else:
                old_exits += 1
        prev = s

    hc, clock = make_consumer()
    frames = [[track(1, 0.5, y)] for y in linear(0.20, 0.80, 40)]
    new_entries, new_exits = counts(run(hc, clock, frames))
    assert (old_entries, old_exits) == (1, 0)
    assert (new_entries, new_exits) == (old_entries, old_exits)


def test_18_event_payload_contract_is_preserved():
    """The perception writer reads data['track']; the module restores entries/exits."""
    hc, clock = make_consumer()
    frames = [[track(7, 0.5, y)] for y in linear(0.20, 0.80, 12)]
    evs = run(hc, clock, frames)
    assert len(evs) == 1
    ev = evs[0]
    assert ev["event_type"] == "entry"
    assert ev["value"] == 1
    assert ev["data"]["track"] == 7
    assert ev["data"]["in"] == 1 and ev["data"]["out"] == 0
    assert hasattr(hc, "entries") and hasattr(hc, "exits")


def test_19_seeded_totals_are_preserved():
    """perception.py restores persisted IN/OUT by assigning to .entries / .exits."""
    hc, clock = make_consumer()
    hc.entries, hc.exits = 40, 17
    frames = [[track(1, 0.5, y)] for y in linear(0.20, 0.80, 12)]
    evs = run(hc, clock, frames)
    assert hc.entries == 41 and hc.exits == 17
    assert evs[0]["data"]["in"] == 41


def test_20_no_line_configured_is_a_no_op():
    hc, clock = make_consumer()
    hc.config = {"module": "headcount", "enabled": True}
    evs, ovs = hc.process([track(1, 0.5, 0.5)], [], SHAPE)
    assert evs == [] and ovs == []


def test_21_track_state_is_purged():
    hc, clock = make_consumer(fps=10.0, state_ttl=1.0)
    run(hc, clock, [[track(1, 0.5, 0.2)]])
    run(hc, clock, [[] for _ in range(30)])
    assert hc.tracks == {}


def test_22_foot_point_beats_centroid_under_camera_tilt():
    """Boxes grow as a person approaches the camera. The centroid then drifts opposite
    to the feet, which is precisely the perspective error Phase 1 set out to remove."""
    hc, clock = make_consumer()
    frames = []
    for i, y in enumerate(linear(0.42, 0.58, 10)):
        frames.append([track(1, 0.5, y, box_h=0.25 + 0.03 * i)])
    assert counts(run(hc, clock, frames)) == (1, 0)

    hc2, clock2 = make_consumer(reference_point="centroid")
    frames2 = []
    for i, y in enumerate(linear(0.42, 0.58, 10)):
        frames2.append([track(1, 0.5, y, box_h=0.25 + 0.03 * i)])
    centroid_result = counts(run(hc2, clock2, frames2))
    assert centroid_result == (0, 0)     # the growing box masks the crossing


# ══════════════════════════════════════════════════════════════════════════════
# Tier B — end to end through the real ByteTrack at a given cadence
# ══════════════════════════════════════════════════════════════════════════════

def _bytetrack_scenario(gts, fps, dropout=()):
    """Push synthetic boxes through real ByteTrack, then into HeadCount."""
    from backend.scripts.bench_tracking_cadence import run_tracker

    obs, _ = run_tracker(gts, fps, dropout)
    hc, clock = make_consumer(fps=fps)
    frames = []
    for o in obs:
        frames.append([{"id": tid, "box": (x1, y1, x2, y2),
                        "centroid": ((x1 + x2) / 2, (y1 + y2) / 2), "conf": 0.9}
                       for tid, x1, y1, x2, y2 in o])
    return counts(run(hc, clock, frames))


def _vertical_walk(y0, y1, seconds, x=0.5, seed=0, jitter=0.0):
    """Ground-truth boxes for a person walking vertically across the horizontal line."""
    import numpy as np
    from backend.scripts.bench_tracking_cadence import GT_FPS

    rng = np.random.default_rng(seed)
    n = max(2, int(round(seconds * GT_FPS)))
    out = []
    for i in range(n):
        t = i / (n - 1)
        fy = y0 + (y1 - y0) * t
        fx = x
        if jitter:
            fx += rng.normal(0, jitter)
            fy += rng.normal(0, jitter)
        out.append((fx - BOX_W / 2, fy - BOX_H, fx + BOX_W / 2, fy))
    return np.asarray(out, dtype=np.float32)


@pytest.mark.parametrize("fps", [10])
def test_e2e_normal_crossing(fps):
    assert _bytetrack_scenario([_vertical_walk(0.20, 0.85, 3.0)], fps) == (1, 0)


@pytest.mark.parametrize("fps", [10])
def test_e2e_fast_crossing(fps):
    assert _bytetrack_scenario([_vertical_walk(0.20, 0.85, 1.5)], fps) == (1, 0)


@pytest.mark.parametrize("fps", [10])
def test_e2e_two_opposite(fps):
    gts = [_vertical_walk(0.20, 0.85, 3.0, x=0.35),
           _vertical_walk(0.85, 0.20, 3.0, x=0.65, seed=2)]
    assert _bytetrack_scenario(gts, fps) == (1, 1)


@pytest.mark.parametrize("fps", [10])
def test_e2e_detector_dropout(fps):
    assert _bytetrack_scenario([_vertical_walk(0.20, 0.85, 3.0)], fps, dropout=(4, 5)) == (1, 0)


# ══════════════════════════════════════════════════════════════════════════════
# Accuracy report — old rule vs new rule across the sampling rates
# ══════════════════════════════════════════════════════════════════════════════

def _vertical_path(waypoints, x=0.5, seed=0, jitter=0.0, grow=0.0):
    """Ground truth for a piecewise vertical path: [(y, seconds_to_reach_it), ...].

    `grow` makes the box taller as the walk proceeds, which is what a real camera sees
    as somebody approaches it — the case where the box centroid drifts away from the
    feet and the old centroid-based rule mistimes the crossing."""
    import numpy as np
    from backend.scripts.bench_tracking_cadence import GT_FPS

    rng = np.random.default_rng(seed)
    ys = []
    cur = waypoints[0][0]
    for target, secs in waypoints[1:]:
        n = max(1, int(round(secs * GT_FPS)))
        for i in range(n):
            ys.append(cur + (target - cur) * (i + 1) / n)
        cur = target
    out = []
    for i, fy in enumerate(ys):
        fx = x
        if jitter:
            fx += rng.normal(0, jitter)
            fy += rng.normal(0, jitter)
        bh = BOX_H + grow * (i / max(1, len(ys) - 1))
        out.append((fx - BOX_W / 2, fy - bh, fx + BOX_W / 2, fy))
    return np.asarray(out, dtype=np.float32)


def _oscillate(centre, amp, cycles, secs_per_cycle):
    """Waypoints for someone hovering across the line."""
    wp = [(centre, 0.0)]
    for _ in range(cycles):
        wp.append((centre + amp, secs_per_cycle / 2))
        wp.append((centre - amp, secs_per_cycle / 2))
    return wp


REPORT_SCENARIOS = [
    ("single enters",           [_vertical_walk(0.20, 0.85, 3.0)],                    1, 0, ()),
    ("single exits",            [_vertical_walk(0.85, 0.20, 3.0)],                    0, 1, ()),
    ("fast crossing 1.5s",      [_vertical_walk(0.20, 0.85, 1.5)],                    1, 0, ()),
    ("very fast crossing 1.0s", [_vertical_walk(0.20, 0.85, 1.0)],                    1, 0, ()),
    ("slow crossing 6s",        [_vertical_walk(0.30, 0.75, 6.0)],                    1, 0, ()),
    ("two together",            [_vertical_walk(0.20, 0.85, 3.0, x=0.35),
                                 _vertical_walk(0.20, 0.85, 3.0, x=0.65, seed=1)],    2, 0, ()),
    ("two opposite",            [_vertical_walk(0.20, 0.85, 3.0, x=0.35),
                                 _vertical_walk(0.85, 0.20, 3.0, x=0.65, seed=2)],    1, 1, ()),
    ("jitter while crossing",   [_vertical_walk(0.20, 0.85, 4.0, jitter=0.012, seed=3)], 1, 0, ()),
    ("detector dropout",        [_vertical_walk(0.20, 0.85, 3.0)],                    1, 0, (4, 5)),
    ("turn back at line",       [_vertical_walk(0.20, 0.47, 2.0)],                    0, 0, ()),
    ("parallel walk",           [_vertical_walk(0.60, 0.62, 3.0)],                    0, 0, ()),
    # ── cases the Phase 1 rule exists for (smooth walks do not exercise them) ──
    ("stands on the line",      [_vertical_path([(0.30, 0)] + _oscillate(0.50, 0.012, 8, 0.8)[1:]
                                                + [(0.30, 1.5)])],                    0, 0, ()),
    ("oscillates then crosses", [_vertical_path([(0.25, 0), (0.48, 1.2)]
                                                + _oscillate(0.50, 0.035, 6, 0.7)[1:]
                                                + [(0.85, 1.2)])],                    1, 0, ()),
    ("hovers then turns back",  [_vertical_path([(0.25, 0), (0.48, 1.0)]
                                                + _oscillate(0.50, 0.030, 5, 0.6)[1:]
                                                + [(0.20, 1.2)])],                    0, 0, ()),
    ("approach under perspective", [_vertical_path([(0.30, 0), (0.80, 3.0)], grow=0.28)], 1, 0, ()),
]


def _old_rule(obs):
    """The pre-Phase-1 head-count rule, reproduced exactly (centroid + sign flip)."""
    from backend.services.analytics.base import side_of_line
    (ax, ay), (bx, by) = LINE[0], LINE[1]
    prev_side, entries, exits = {}, 0, 0
    for frame in obs:
        for tid, x1, y1, x2, y2 in frame:
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            s = side_of_line(cx / FRAME_W, cy / FRAME_H, ax, ay, bx, by)
            prev = prev_side.get(tid)
            prev_side[tid] = s
            if prev is None or s == 0 or (s > 0) == (prev > 0):
                continue
            if prev < 0 <= s:
                entries += 1
            else:
                exits += 1
    return entries, exits


def accuracy_report():
    from backend.scripts.bench_tracking_cadence import run_tracker

    print("=" * 96)
    print("HEAD-COUNT ACCURACY — synthetic ground truth through real ByteTrack")
    print("=" * 96)

    for label, fps in (("OLD rule @ 2 fps (current production)", 2),
                       ("OLD rule @ 10 fps", 10),
                       ("NEW rule @ 2 fps", 2),
                       ("NEW rule @ 10 fps (Phase 1 default)", 10)):
        is_new = label.startswith("NEW")
        tp = fp = fn = 0
        act_in = act_out = det_in = det_out = 0
        failures = []
        print(f"\n--- {label} ---")
        print(f"{'scenario':<26}{'expected':>12}{'detected':>12}{'result':>28}")
        for name, gts, exp_in, exp_out, dropout in REPORT_SCENARIOS:
            obs, _ = run_tracker(gts, fps, dropout)
            if is_new:
                hc, clock = make_consumer(fps=fps)
                frames = [[{"id": tid, "box": (x1, y1, x2, y2),
                            "centroid": ((x1 + x2) / 2, (y1 + y2) / 2), "conf": 0.9}
                           for tid, x1, y1, x2, y2 in o] for o in obs]
                got_in, got_out = counts(run(hc, clock, frames))
            else:
                got_in, got_out = _old_rule(obs)

            act_in += exp_in
            act_out += exp_out
            det_in += got_in
            det_out += got_out
            tp += min(exp_in, got_in) + min(exp_out, got_out)
            fp += max(0, got_in - exp_in) + max(0, got_out - exp_out)
            fn += max(0, exp_in - got_in) + max(0, exp_out - got_out)

            if (got_in, got_out) == (exp_in, exp_out):
                verdict = "OK"
            elif got_in + got_out < exp_in + exp_out:
                verdict = "MISS (false negative)"
                failures.append(f"{name}: missed")
            elif (got_in, got_out) != (exp_in, exp_out) and got_in + got_out == exp_in + exp_out:
                verdict = "WRONG DIRECTION"
                failures.append(f"{name}: wrong direction")
            else:
                verdict = "FALSE POSITIVE"
                failures.append(f"{name}: false positive")
            print(f"{name:<26}{f'{exp_in}/{exp_out}':>12}{f'{got_in}/{got_out}':>12}{verdict:>28}")

        recall_in = (min(det_in, act_in) / act_in) if act_in else 1.0
        recall_out = (min(det_out, act_out) / act_out) if act_out else 1.0
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        print(f"  actual   in={act_in} out={act_out}")
        print(f"  detected in={det_in} out={det_out}")
        print(f"  true positives={tp}  false positives={fp}  false negatives={fn}")
        print(f"  entry recall={recall_in:.2f}  exit recall={recall_out:.2f}  "
              f"precision={precision:.2f}  overall crossing accuracy={recall:.2f}")
        if failures:
            print(f"  failure patterns: {'; '.join(failures)}")
    print()
    print("=" * 96)


if __name__ == "__main__":
    accuracy_report()
    sys.exit(0)


def test_23_handoff_does_not_steal_a_different_persons_state():
    """A new track appearing where someone stood a while ago must NOT inherit their
    crossing state. Person A stops just short of the line and leaves; person B later
    materialises just PAST it and walks away. B never crossed. If B inherited A's
    latched origin side, B would be committed as an entry — a phantom count."""
    hc, clock = make_consumer()
    run(hc, clock, [[track(1, 0.5, y)] for y in linear(0.20, 0.46, 8)])   # A: stops short
    run(hc, clock, [[] for _ in range(10)])                               # long gap
    events = run(hc, clock, [[track(2, 0.5, y)] for y in linear(0.52, 0.62, 6)])

    assert counts(events) == (0, 0), "stale hand-off manufactured a crossing"
    assert hc.tracks[2].obs == 6, "track 2 inherited another track's observation history"
    assert hc.metrics.track_id_switches_near_line == 0


def test_24_short_gap_handoff_still_works():
    """The counterpart to test_23: a one-frame gap IS a plausible ByteTrack id switch
    and must still hand over, otherwise the crossing is lost."""
    hc, clock = make_consumer()
    ys = linear(0.20, 0.80, 12)
    frames = []
    for i, y in enumerate(ys):
        if i == 5:
            frames.append([])                       # one dropped frame
        elif i < 5:
            frames.append([track(1, 0.5, y)])
        else:
            frames.append([track(42, 0.5, y)])      # id changed across the gap
    assert counts(run(hc, clock, frames)) == (1, 0)


# ── the drawn line is a SEGMENT, not an infinite line ────────────────────────
SHORT_LINE = [[0.40, 0.50], [0.60, 0.50]]     # a doorway spanning only the middle 20%


def test_25_crossing_past_the_end_of_the_drawn_line_is_not_counted():
    """Found in a real capture: 4 of 29 crossings had happened beyond the operator's
    drawn endpoints, because the geometry maths describes an infinite line. Someone
    walking around the end of a doorway line must not register as passing through it."""
    hc, clock = make_consumer()
    hc.config = dict(hc.config, line=SHORT_LINE)
    frames = [[track(1, 0.85, y)] for y in linear(0.20, 0.80, 12)]   # well past x=0.60
    assert counts(run(hc, clock, frames)) == (0, 0)
    assert hc.metrics.reject_reasons.get("outside_line_segment", 0) >= 1


def test_26_crossing_within_the_drawn_line_still_counts():
    hc, clock = make_consumer()
    hc.config = dict(hc.config, line=SHORT_LINE)
    frames = [[track(1, 0.50, y)] for y in linear(0.20, 0.80, 12)]
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_27_small_margin_tolerates_an_imprecisely_drawn_line():
    """Just past the endpoint, within the drawing-tolerance margin, still counts."""
    hc, clock = make_consumer()
    hc.config = dict(hc.config, line=SHORT_LINE)      # margin 0.05 of a 0.20 line = 0.01
    frames = [[track(1, 0.607, y)] for y in linear(0.20, 0.80, 12)]
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_28_segment_margin_is_configurable():
    """Setting a huge margin restores the old infinite-line behaviour."""
    hc, clock = make_consumer()
    hc.config = dict(hc.config, line=SHORT_LINE, segment_margin=100.0)
    frames = [[track(1, 0.85, y)] for y in linear(0.20, 0.80, 12)]
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_29_full_width_line_is_unaffected_by_the_segment_bound():
    """The common case — a line drawn right across the frame — must not regress."""
    hc, clock = make_consumer()
    for x in (0.05, 0.5, 0.95):
        hc2, clock2 = make_consumer()
        frames = [[track(1, x, y)] for y in linear(0.20, 0.80, 12)]
        assert counts(run(hc2, clock2, frames)) == (1, 0), f"failed at x={x}"


def test_30_brief_excursion_outside_the_segment_does_not_discard_the_crossing():
    """Ordering guard: a track that steps momentarily beyond the line's end WHILE it is
    still confirming must stay pending, not be thrown away. The segment bound is judged
    on where the track actually crossed, not on where it drifts to afterwards."""
    hc, clock = make_consumer()
    hc.config = dict(hc.config, line=SHORT_LINE)
    # crosses squarely at x=0.50, then drifts sideways past the end of the line
    xs = [0.50, 0.50, 0.50, 0.50, 0.50, 0.50, 0.62, 0.75, 0.85, 0.90, 0.92, 0.94]
    frames = [[track(1, x, y)] for x, y in zip(xs, linear(0.20, 0.80, 12))]
    assert counts(run(hc, clock, frames)) == (1, 0)


def test_31_crossing_point_not_end_point_decides_the_segment_bound():
    """The mirror image: crossed OUTSIDE the line, then drifted back in front of it.
    They never passed through the doorway, so nothing may be counted."""
    hc, clock = make_consumer()
    hc.config = dict(hc.config, line=SHORT_LINE)
    xs = [0.90, 0.90, 0.90, 0.90, 0.90, 0.90, 0.80, 0.70, 0.60, 0.55, 0.52, 0.50]
    frames = [[track(1, x, y)] for x, y in zip(xs, linear(0.20, 0.80, 12))]
    assert counts(run(hc, clock, frames)) == (0, 0)


# ── the crossing band is operator-facing, not a debug aid ────────────────────
def _band_lines(overlays):
    """The band is drawn as the amber ('idle') line pair either side of the counter."""
    return [o for o in overlays if o.get("kind") == "line" and o.get("color") == "idle"]


def test_32_band_is_drawn_whenever_headcount_is_enabled():
    """It must NOT require HEADCOUNT_DEBUG: an operator positioning a line needs to see
    where the counting tolerance actually sits."""
    hc, clock = make_consumer()
    _, overlays = hc.process([track(1, 0.5, 0.3)], [], SHAPE)

    counter = [o for o in overlays if o.get("color") == "online"]
    band = _band_lines(overlays)
    assert len(counter) == 1, "the counting line itself must still be drawn"
    assert len(band) == 2, "both edges of the crossing band must be drawn"
    assert [o.get("label") for o in band] == ["BAND", None], "label one edge only"


def test_33_band_edges_straddle_the_line_by_the_band_width():
    hc, clock = make_consumer(crossing_band=0.05)
    _, overlays = hc.process([track(1, 0.5, 0.3)], [], SHAPE)

    # LINE is horizontal at y=0.5, so the edges sit at y = 0.5 +/- 0.05
    ys = sorted(round(o["points"][0][1], 4) for o in _band_lines(overlays))
    assert ys == [0.45, 0.55]


def test_34_band_can_be_switched_off_per_camera():
    for value in (False, "false", "0", "off", "no"):
        hc, clock = make_consumer(show_band=value)
        _, overlays = hc.process([track(1, 0.5, 0.3)], [], SHAPE)
        assert _band_lines(overlays) == [], f"show_band={value!r} should hide the band"


def test_35_band_survives_a_zero_width_band_setting():
    hc, clock = make_consumer(crossing_band=0)
    _, overlays = hc.process([track(1, 0.5, 0.3)], [], SHAPE)
    assert _band_lines(overlays) == [], "a zero-width band has nothing to draw"


def test_36_band_follows_a_diagonal_line():
    """Camera 47's line is diagonal — the edges must be offset along the normal, not
    along an axis."""
    hc, clock = make_consumer(crossing_band=0.04)
    hc.config = dict(hc.config, line=[[0.1572, 0.3148], [0.3366, 0.7077]])
    _, overlays = hc.process([track(1, 0.5, 0.3)], [], SHAPE)

    band = _band_lines(overlays)
    assert len(band) == 2
    import math
    for o in band:
        (x1, y1), (x2, y2) = o["points"]
        # each edge is parallel to the counter and offset by exactly the band width
        assert abs(math.hypot(x1 - 0.1572, y1 - 0.3148) - 0.04) < 1e-6
        assert abs(math.hypot(x2 - 0.3366, y2 - 0.7077) - 0.04) < 1e-6
