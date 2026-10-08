"""
Tests for the four Kohat POC capabilities: trip wire, crowd, no-stopping, tampering.

This laptop has no camera, no GPU and no NVIDIA driver, so these tests drive the rule
layer directly with synthetic primitives — which is where the logic lives anyway. The
detectors are thin wrappers around YOLO and are covered only for their degradation
contract (missing weights must no-op, never crash).

What is NOT claimed here: that any threshold default is right for Kohat. There is no
footage yet. These prove the rules behave as specified, which is a different and much
weaker claim — and the one worth automating.

Run:  pytest tests/test_kohat_poc.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.services.analytics.crowd import Crowd
from backend.services.analytics.no_stopping import NoStopping
from backend.services.analytics.tripwire import TripWire

SHAPE = (720, 1280)          # h, w
H, W = SHAPE


def person(tid, nx, ny, h=0.25):
    """A person track whose FOOT point sits at normalised (nx, ny)."""
    ph = h * H
    pw = ph * 0.4
    cx, foot = nx * W, ny * H
    return {"id": tid, "box": [cx - pw / 2, foot - ph, cx + pw / 2, foot],
            "centroid": (cx, foot - ph / 2)}


def vehicle(nx, ny, kind="car", w=0.12, h=0.10, conf=0.8):
    cx, cy = nx * W, ny * H
    bw, bh = w * W, h * H
    return {"box": [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2],
            "kind": kind, "confidence": conf, "cls": 2}


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def tick(self, s=1.0):
        self.t += s

    def __call__(self):
        return self.t


# ══════════════════════════════════════════════════════════════════════════════
# #1 Virtual trip wire
# ══════════════════════════════════════════════════════════════════════════════

VLINE = [[0.5, 0.0], [0.5, 1.0]]      # vertical line down the middle


def walk(consumer, xs, tid=1, ny=0.8, clock=None):
    """Walk one person along a path of x positions, returning every event raised."""
    events = []
    for i, x in enumerate(xs):
        if clock:
            clock.tick(0.1)
        evs, _ = consumer.process([person(tid, x, ny)], [], SHAPE)
        events += evs
    return events


def make_tripwire(**cfg):
    base = {"line": VLINE, "direction": "any", "cooldown": 0}
    base.update(cfg)
    tw = TripWire(1, base)
    clock = Clock()
    tw._now = clock
    return tw, clock


def test_01_tripwire_fires_on_a_crossing():
    tw, clock = make_tripwire()
    evs = walk(tw, [0.20, 0.30, 0.40, 0.48, 0.52, 0.60, 0.70, 0.80], clock=clock)
    assert [e["event_type"] for e in evs] == ["crossing"]
    assert evs[0]["data"]["direction"] in ("in", "out")
    assert evs[0]["snapshot"] is True


def test_02_no_crossing_when_nobody_crosses():
    tw, clock = make_tripwire()
    evs = walk(tw, [0.10, 0.15, 0.20, 0.25, 0.30], clock=clock)
    assert evs == []


def test_03_a_stationary_person_never_crosses():
    """The single most important false positive to prevent: a person standing on the
    line whose box jitters must not generate a stream of alerts."""
    tw, clock = make_tripwire()
    jitter = [0.499, 0.501, 0.4995, 0.5005, 0.499, 0.501] * 4
    assert walk(tw, jitter, clock=clock) == []


def test_04_direction_filter_suppresses_the_wrong_way():
    """Whichever way the first wire fires, a wire configured for the opposite direction
    must stay silent on the same walk."""
    tw, clock = make_tripwire(direction="any")
    got = walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], clock=clock)
    assert len(got) == 1
    fired = got[0]["data"]["direction"]
    other = "out" if fired == "in" else "in"

    tw2, clock2 = make_tripwire(direction=other)
    assert walk(tw2, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], clock=clock2) == [], \
        "a wire armed for the other direction must not fire"


def test_05_direction_filter_passes_the_right_way():
    tw, clock = make_tripwire(direction="any")
    fired = walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], clock=clock)[0]
    want = fired["data"]["direction"]

    tw2, clock2 = make_tripwire(direction=want)
    assert len(walk(tw2, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], clock=clock2)) == 1


def test_06_every_person_in_a_group_gets_their_own_alert():
    """THE REGRESSION. This test previously asserted the opposite — that a 60 s cooldown
    collapses a group into ONE alert — and that behaviour caused a real miss: two people
    walked through a trip wire together and only one alert was raised.

    Under-reporting a perimeter breach is the worst failure this module has, so the
    cooldown is now per TRACK. Four distinct people crossing must produce four alerts no
    matter how tight the cooldown is."""
    tw, clock = make_tripwire(cooldown=60)
    path = [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8]
    total = []
    for tid in range(1, 5):
        total += walk(tw, path, tid=tid, clock=clock)
    assert len(total) == 4, f"one alert per person, got {len(total)}"
    assert len({e["data"]["track"] for e in total}) == 4


def test_06b_the_same_person_is_still_debounced():
    """The cooldown must still do its actual job: one person, not one alert per frame."""
    tw, clock = make_tripwire(cooldown=60)
    total = walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], tid=1, clock=clock)
    total += walk(tw, [0.8, 0.7, 0.6, 0.52, 0.48, 0.4, 0.3, 0.2], tid=1, clock=clock)
    assert len(total) == 1, "the same track re-crossing inside the cooldown is suppressed"


def test_06c_wire_scope_is_still_available():
    """A genuinely busy doorway may still want one alert per burst."""
    tw, clock = make_tripwire(cooldown=60, cooldown_scope="wire")
    path = [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8]
    total = []
    for tid in range(1, 5):
        total += walk(tw, path, tid=tid, clock=clock)
    assert len(total) == 1


def test_06d_an_unknown_scope_falls_back_to_track():
    """A typo must fail towards reporting MORE, never towards silence."""
    tw, _ = make_tripwire(cooldown_scope="nonsense")
    assert tw._scope() == "track"


def test_06e_the_per_track_map_does_not_grow_without_bound():
    """ByteTrack ids climb continuously - 179 in three minutes on the real feed - so an
    unpruned dict here would be a slow leak."""
    tw, clock = make_tripwire(cooldown=1)
    path = [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8]
    for tid in range(1, 400):
        walk(tw, path, tid=tid, clock=clock)
    assert len(tw._last_alert_by_track) < 400, "stale track timestamps must be pruned"


def test_07_zero_cooldown_reports_every_person():
    tw, clock = make_tripwire(cooldown=0)
    path = [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8]
    total = []
    for tid in range(1, 4):
        total += walk(tw, path, tid=tid, clock=clock)
    assert len(total) == 3


def test_08_headcount_totals_are_stripped_from_the_payload():
    """A trip wire has no running total, and leaving head-count's in/out in the payload
    would present a meaningless number as if it meant something."""
    tw, clock = make_tripwire()
    ev = walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], clock=clock)[0]
    assert "in" not in ev["data"] and "out" not in ev["data"]
    assert "track" in ev["data"]


def test_09_overlay_is_relabelled():
    tw, clock = make_tripwire()
    _, ovs = tw.process([person(1, 0.2, 0.8)], [], SHAPE)
    lines = [o for o in ovs if o.get("kind") == "line"]
    assert lines, "the wire must be drawn"
    assert "TRIP WIRE" in lines[0]["label"]
    assert "IN " not in lines[0]["label"], "head-count's tally must not leak through"


def test_10_an_invalid_direction_falls_back_to_any():
    tw, _ = make_tripwire(direction="sideways")
    assert tw._direction() == "any"


# ══════════════════════════════════════════════════════════════════════════════
# #8 Crowd / unusual gathering
# ══════════════════════════════════════════════════════════════════════════════

def make_crowd(**cfg):
    base = {"min_people": 4, "radius": 0.12, "sustain_seconds": 5, "cooldown": 0}
    base.update(cfg)
    return Crowd(1, base)


def huddle(n, x=0.5, y=0.8, spread=0.03):
    return [person(i, x + (i - n / 2) * spread, y) for i in range(n)]


def spread_out(n, y=0.8):
    return [person(i, 0.05 + i * (0.9 / max(1, n - 1)), y) for i in range(n)]


def run_for(consumer, tracks, seconds, step=1.0, monkeypatch=None, clock=None):
    """Feed the same scene repeatedly while advancing a patched clock."""
    events = []
    for _ in range(int(seconds / step)):
        clock.tick(step)
        evs, _ = consumer.process(tracks, [], SHAPE)
        events += evs
    return events


@pytest.fixture
def crowd_clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("backend.services.analytics.crowd.time.time", clock)
    return clock


def test_11_a_huddle_held_long_enough_alerts(crowd_clock):
    c = make_crowd(sustain_seconds=5)
    evs = run_for(c, huddle(5), 10, clock=crowd_clock)
    assert [e["event_type"] for e in evs] == ["gathering"]
    assert evs[0]["value"] == 5


def test_12_people_spread_out_are_not_a_gathering(crowd_clock):
    """The exact case occupancy gets wrong: enough people, but not clustered."""
    c = make_crowd(min_people=4, radius=0.12)
    assert run_for(c, spread_out(6), 20, clock=crowd_clock) == []


def test_13_a_brief_cluster_below_the_sustain_window_does_not_alert(crowd_clock):
    """People walking past each other form transient clusters constantly."""
    c = make_crowd(sustain_seconds=30)
    assert run_for(c, huddle(5), 10, clock=crowd_clock) == []


def test_14_below_min_people_never_alerts(crowd_clock):
    c = make_crowd(min_people=6)
    assert run_for(c, huddle(4), 30, clock=crowd_clock) == []


def test_15_dispersal_is_reported(crowd_clock):
    """Note the wait: dispersal is deliberately reported only after `grace_seconds` of
    absence, because a cluster vanishing for a frame is usually a dropped frame rather
    than people leaving. So an empty view for less than the grace window is NOT a
    dispersal — see test_112."""
    c = make_crowd(sustain_seconds=3, grace_seconds=3)
    got = run_for(c, huddle(5), 8, clock=crowd_clock)
    assert [e["event_type"] for e in got] == ["gathering"]
    after = run_for(c, [], 10, clock=crowd_clock)      # past the grace window
    assert [e["event_type"] for e in after] == ["dispersed"]
    assert after[0]["data"]["peak_size"] >= 5


def test_16_it_does_not_re_alert_while_the_crowd_persists(crowd_clock):
    c = make_crowd(sustain_seconds=2)
    got = run_for(c, huddle(6), 60, clock=crowd_clock)
    assert len(got) == 1, f"one gathering, one alert - got {len(got)}"


def test_17_a_zone_masks_people_outside_it(crowd_clock):
    left = [[0.0, 0.0], [0.4, 0.0], [0.4, 1.0], [0.0, 1.0]]
    c = make_crowd(zones=[left], sustain_seconds=2)
    # A tight huddle, but on the right-hand side, outside the mask.
    assert run_for(c, huddle(6, x=0.8), 20, clock=crowd_clock) == []


def test_18_frame_mode_radius_still_works(crowd_clock):
    """`radius_mode="frame"` restores the fixed-fraction behaviour, for a camera where
    box heights are unreliable — very oblique angles, or heavy occlusion."""
    tracks = [person(i, 0.30 + i * 0.10, 0.8) for i in range(5)]   # 0.10 apart
    tight = make_crowd(radius_mode="frame", radius=0.05, sustain_seconds=2)
    assert run_for(tight, tracks, 10, clock=crowd_clock) == [], \
        "0.05 radius must not link people 0.10 apart"
    loose = make_crowd(radius_mode="frame", radius=0.15, sustain_seconds=2)
    assert len(run_for(loose, tracks, 10, clock=crowd_clock)) == 1


def test_18b_person_mode_adapts_to_distance_from_the_camera(crowd_clock):
    """THE POINT of person-relative linking. The same physical grouping, once close to
    the camera and once far away, must read the same — a fixed frame-fraction cannot do
    both, which is why a single `radius` kept being wrong."""
    # Near: tall boxes, proportionally large pixel gaps.
    near = [person(i, 0.45 + i * 0.06, 0.9, h=0.50) for i in range(3)]
    # Far: short boxes, proportionally small pixel gaps — same gap in body-heights.
    far = [person(i, 0.45 + i * 0.018, 0.35, h=0.15) for i in range(3)]

    a = make_crowd(min_people=3, sustain_seconds=2)
    assert len(run_for(a, near, 10, clock=crowd_clock)) == 1, "near group must alert"

    b = make_crowd(min_people=3, sustain_seconds=2)
    assert len(run_for(b, far, 10, clock=crowd_clock)) == 1, "far group must alert too"


def test_18c_the_distance_metric_is_isotropic():
    """x was normalised by width and y by height, so a horizontal gap of 0.2 compared
    equal to a vertical gap of 0.2 — on a 16:9 frame the second is barely half the
    first. Distance between people is physical and must not depend on frame shape."""
    c = make_crowd(min_people=2, radius_mode="frame", radius=0.10)
    horiz = [person(0, 0.40, 0.80), person(1, 0.55, 0.80)]      # 0.15 of width apart
    vert = [person(0, 0.50, 0.60), person(1, 0.50, 0.60 + 0.15 * W / H)]
    gh = c._clusters([(p["box"][0] / W, p["box"][3] / W) for p in horiz], 0.10)
    gv = c._clusters([(p["box"][0] / W, p["box"][3] / W) for p in vert], 0.10)
    assert [len(g) for g in gh] == [len(g) for g in gv], \
        "an equal physical gap must cluster the same way in either direction"


def test_19_clustering_is_by_foot_point_not_centroid(crowd_clock):
    """Two people at different depths have very different box heights. Centroids would
    be far apart vertically; feet are on the ground plane and cluster correctly."""
    tracks = [person(0, 0.50, 0.80, h=0.45), person(1, 0.52, 0.80, h=0.15),
              person(2, 0.54, 0.80, h=0.30), person(3, 0.56, 0.80, h=0.20)]
    c = make_crowd(min_people=4, radius=0.10, sustain_seconds=2)
    assert len(run_for(c, tracks, 10, clock=crowd_clock)) == 1


def test_20_two_separate_small_groups_do_not_merge(crowd_clock):
    tracks = ([person(i, 0.15 + i * 0.02, 0.8) for i in range(3)] +
              [person(10 + i, 0.80 + i * 0.02, 0.8) for i in range(3)])
    c = make_crowd(min_people=5, radius=0.06, sustain_seconds=2)
    assert run_for(c, tracks, 10, clock=crowd_clock) == [], \
        "3 + 3 far apart is not a group of 6"


# ══════════════════════════════════════════════════════════════════════════════
# #6 Vehicle no-stopping
# ══════════════════════════════════════════════════════════════════════════════

ZONE_ALL = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


@pytest.fixture
def veh_clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("backend.services.analytics.no_stopping.time.time", clock)
    return clock


def make_nostop(**cfg):
    base = {"zones": [ZONE_ALL], "dwell_seconds": 10, "move_tolerance": 0.02,
            "cooldown": 0}
    base.update(cfg)
    return NoStopping(1, base)


def feed(consumer, dets_per_step, clock, step=1.0):
    events = []
    for dets in dets_per_step:
        clock.tick(step)
        evs, _ = consumer.process_detections(dets, SHAPE)
        events += evs
    return events


def test_21_a_parked_vehicle_alerts_after_dwell(veh_clock):
    ns = make_nostop(dwell_seconds=5)
    evs = feed(ns, [[vehicle(0.5, 0.5)] for _ in range(10)], veh_clock)
    assert [e["event_type"] for e in evs] == ["no_stopping"]
    assert evs[0]["data"]["kind"] == "car"


def test_22_a_moving_vehicle_never_alerts(veh_clock):
    ns = make_nostop(dwell_seconds=5)
    steps = [[vehicle(0.10 + i * 0.06, 0.5)] for i in range(14)]
    assert feed(ns, steps, veh_clock) == []


def test_23_detector_jitter_does_not_reset_the_dwell_timer(veh_clock):
    """The failure that makes a naive implementation never fire: a parked car's box
    wobbles a few pixels every frame."""
    ns = make_nostop(dwell_seconds=6, move_tolerance=0.02)
    rng = np.random.default_rng(7)
    steps = [[vehicle(0.5 + rng.normal(0, 0.004), 0.5 + rng.normal(0, 0.004))]
             for _ in range(14)]
    assert len(feed(ns, steps, veh_clock)) == 1, "jitter must not be read as motion"


def test_24_it_does_not_re_alert_for_the_same_stationary_vehicle(veh_clock):
    ns = make_nostop(dwell_seconds=3)
    evs = feed(ns, [[vehicle(0.5, 0.5)] for _ in range(60)], veh_clock)
    assert len(evs) == 1


def test_25_a_vehicle_outside_the_zone_is_ignored(veh_clock):
    left = [[0.0, 0.0], [0.3, 0.0], [0.3, 1.0], [0.0, 1.0]]
    ns = make_nostop(zones=[left], dwell_seconds=3)
    assert feed(ns, [[vehicle(0.8, 0.5)] for _ in range(12)], veh_clock) == []


def test_26_vehicle_type_filter(veh_clock):
    ns = make_nostop(dwell_seconds=3, vehicle_types=["truck"])
    assert feed(ns, [[vehicle(0.5, 0.5, kind="car")] for _ in range(12)],
                veh_clock) == []
    ns2 = make_nostop(dwell_seconds=3, vehicle_types=["truck"])
    assert len(feed(ns2, [[vehicle(0.5, 0.5, kind="truck")] for _ in range(12)],
                    veh_clock)) == 1


def test_27_a_brief_occlusion_does_not_restart_the_timer(veh_clock):
    """Someone walks in front of the car for two evaluations."""
    ns = make_nostop(dwell_seconds=8)
    steps = ([[vehicle(0.5, 0.5)]] * 5 + [[]] * 2 + [[vehicle(0.5, 0.5)]] * 5)
    assert len(feed(ns, steps, veh_clock)) == 1, \
        "MISS_GRACE must carry the slot through a short occlusion"


def test_28_two_vehicles_are_tracked_separately(veh_clock):
    ns = make_nostop(dwell_seconds=4)
    steps = [[vehicle(0.25, 0.5), vehicle(0.75, 0.5)] for _ in range(10)]
    evs = feed(ns, steps, veh_clock)
    assert len(evs) == 2
    assert len({e["data"]["slot"] for e in evs}) == 2


def test_29_process_is_inert_because_detections_arrive_elsewhere(veh_clock):
    ns = make_nostop()
    assert ns.process([person(1, 0.5, 0.5)], [], SHAPE) == ([], [])


def test_30_schedule_disarms_the_rule(veh_clock, monkeypatch):
    import backend.services.analytics.no_stopping as mod
    monkeypatch.setattr(mod, "within_schedule", lambda _s: False)
    ns = make_nostop(dwell_seconds=2, schedule={"start": "09:00", "end": "17:00"})
    assert feed(ns, [[vehicle(0.5, 0.5)] for _ in range(12)], veh_clock) == []


# ══════════════════════════════════════════════════════════════════════════════
# #4 Camera tampering
# ══════════════════════════════════════════════════════════════════════════════

def scene(seed=0, bright=128, noise=60):
    """A textured frame — high Laplacian variance, wide histogram."""
    rng = np.random.default_rng(seed)
    img = rng.normal(bright, noise, (240, 320, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def blurred():
    """Low edge energy but still varied luminance — a defocused lens."""
    import cv2
    return cv2.GaussianBlur(scene(1), (31, 31), 0)


def covered():
    """Almost no structure at all — a bag over the lens."""
    return np.full((240, 320, 3), 8, dtype=np.uint8)


@pytest.fixture
def tamper_mod(monkeypatch, tmp_path):
    import backend.services.tamper as t
    from backend.services.analytics import tamper_debug
    monkeypatch.setenv("TAMPER_DEBUG", "trace")
    # DiagnosticLog takes a FULL FILE PATH in {PREFIX}_LOG, not a directory. Getting this
    # wrong sent the first run of this test into the placeholder BASE_DIR from .env -
    # which is how that misconfiguration was found, so the mistake was worth making once.
    monkeypatch.setenv("TAMPER_DEBUG_LOG", str(tmp_path / "tamper_debug.log"))
    tamper_debug.reset()
    clock = Clock()
    monkeypatch.setattr(t.time, "time", clock)
    monkeypatch.setattr(t, "SAMPLE_INTERVAL", 0.0)
    yield t, clock
    tamper_debug.reset()


def soak(mon, frame, clock, seconds, step=1.0):
    events = []
    for _ in range(int(seconds / step)):
        clock.tick(step)
        events += mon.check(frame)
    return events


def test_31_a_normal_scene_raises_nothing(tamper_mod):
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 5})
    assert soak(mon, scene(2), clock, 30) == []


def test_32_a_covered_lens_alerts(tamper_mod):
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 5})
    soak(mon, scene(3), clock, 10)          # establish the baseline
    evs = soak(mon, covered(), clock, 30)
    kinds = {e["event_type"] for e in evs}
    assert "tamper_covered" in kinds, kinds


def test_33_a_defocused_lens_alerts(tamper_mod):
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 5})
    soak(mon, scene(4), clock, 10)
    evs = soak(mon, blurred(), clock, 30)
    assert "tamper_blur" in {e["event_type"] for e in evs}


# The three events below are REAL, read off logs/tamper_debug.log on 2026-08-28.
# Values are (blur, blur_trip, std, cover_trip, dead_frac).
#
#   two genuine covers: below the blur trip, 15 of 16 regions dark
#   one false positive: somebody standing near the lens - still sharp, 2 of 16 regions
FIELD_COVER_CASES = [
    ("cam49 genuine cover", 253.8319, 574.0424, 7.9606, 27.9446, 0.9375, True),
    ("cam47 genuine cover", 116.4690, 327.0410, 8.0357, 27.6026, 0.9375, True),
    ("cam50 person standing", 514.5702, 308.2746, 25.0123, 26.4144, 0.1250, False),
]


@pytest.mark.parametrize("name,blur,blur_trip,std,cover_trip,dead_frac,expected",
                         FIELD_COVER_CASES,
                         ids=[c[0] for c in FIELD_COVER_CASES])
def test_34b_covered_matches_the_field(name, blur, blur_trip, std, cover_trip,
                                       dead_frac, expected):
    """A person standing near the lens must not read as a covered lens.

    Camera 50 raised tamper_covered while somebody simply stood there: global std had
    fallen from 44.0 to 25.0, under its 26.4 trip. But blur was 514 against a trip of
    308 - the frame was still sharp - and only 2 of 16 regions had gone dark against a
    trip of 30%. Both genuine covers that day sat BELOW their blur trip with 15 of 16
    regions dark. A covered lens cannot be sharp; whole-frame flatness alone was not
    evidence of covering."""
    from backend.services.tamper import covered_signal
    got = covered_signal(flat=std < cover_trip, sharp=blur >= blur_trip,
                         dead_frac=dead_frac)
    assert got is expected, f"{name}: covered={got}, expected {expected}"


def test_34b2_without_the_veto_the_false_positive_returns():
    """Guards the guard: with TAMPER_COVER_NEEDS_BLUR=0 the field case fires again.

    If this ever passes, the veto has stopped doing anything and test_34b above is
    green for the wrong reason."""
    from backend.services.tamper import covered_signal
    assert covered_signal(flat=True, sharp=True, dead_frac=0.125,
                          needs_blur=False) is True
    assert covered_signal(flat=True, sharp=True, dead_frac=0.125,
                          needs_blur=True) is False


def test_34c_the_veto_does_not_weaken_a_real_cover(tamper_mod):
    """The guard above must not cost a genuine detection: a covered lens is flat AND
    out of focus, so it fails the sharpness test and alerts by both routes."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 2})
    soak(mon, scene(8), clock, 15)
    evs = soak(mon, covered(), clock, 30)
    assert "tamper_covered" in {e["event_type"] for e in evs}


def test_34_a_single_bad_frame_is_not_tampering(tamper_mod):
    """A dropped or artefacted frame must never alert. This is the check that separates
    a usable detector from a false-positive generator."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 20})
    soak(mon, scene(5), clock, 10)
    got = []
    for i in range(20):
        clock.tick(1.0)
        got += mon.check(covered() if i % 7 == 0 else scene(5))
    assert got == [], "intermittent bad frames must not sustain"


def test_35_warmup_suppresses_judgement_after_a_restart(tamper_mod):
    """The first frames after a restart have no reference to compare against."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 0})
    got = []
    for _ in range(t.WARMUP_SAMPLES):
        clock.tick(1.0)
        got += mon.check(covered())
    assert got == [], "no judgement before the reference has history"


def test_36_recovery_clears_the_alert_state(tamper_mod):
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 3, "cooldown": 0})
    soak(mon, scene(6), clock, 8)
    assert soak(mon, covered(), clock, 20), "should have alerted"
    soak(mon, scene(6), clock, 10)
    assert all(not v for v in mon._alerted.values()), \
        "signals must clear once the scene recovers"


def test_37_the_reference_does_not_drift_during_tampering(tamper_mod):
    """The subtle bug: if the baseline kept adapting while a camera was being turned,
    it would follow the camera and never register a change."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 2})
    soak(mon, scene(7), clock, 8)
    ref_before = mon._ref_hist.copy()
    soak(mon, covered(), clock, 20)
    assert np.allclose(ref_before, mon._ref_hist), \
        "the reference must freeze while a signal is over threshold"


def test_38_check_never_raises_on_a_bad_frame(tamper_mod):
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {})
    for bad in (None, np.zeros((0, 0, 3), dtype=np.uint8), "not a frame"):
        clock.tick(1.0)
        assert mon.check(bad) == []


def test_39_events_carry_the_module_tag(tamper_mod):
    """These events bypass the perception child, so nothing downstream stamps `module`
    for them — the monitor must do it itself or the writer files them as unknown."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 2})
    soak(mon, scene(8), clock, 8)
    evs = soak(mon, covered(), clock, 20)
    assert evs and all(e["module"] == "tamper" for e in evs)
    assert all(e["snapshot"] is True for e in evs)


def test_40_the_diagnostic_log_is_written(tamper_mod, tmp_path):
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 2})
    soak(mon, scene(9), clock, 8)
    soak(mon, covered(), clock, 20)
    text = (tmp_path / "tamper_debug.log").read_text(encoding="utf-8")
    assert "event=SESSION" in text, "the resolved config must be recorded"
    assert "event=TAMPER" in text and "verdict=ALERT" in text


# ══════════════════════════════════════════════════════════════════════════════
# Registry, severity and toggles — the wiring that makes the four usable
# ══════════════════════════════════════════════════════════════════════════════

NEW_MODULES = ("tripwire", "crowd", "no_stopping", "tamper")


@pytest.mark.parametrize("mid", NEW_MODULES)
def test_41_registered_in_the_backend_registry(mid):
    from backend.services.modules.registry import MODULES
    assert mid in MODULES
    meta = MODULES[mid]
    assert meta["event_types"], "a module with no event types can never alert"


@pytest.mark.parametrize("mid", NEW_MODULES)
def test_42_mirrored_in_the_frontend_registry(mid):
    """`modules.ts` is hand-maintained. A module missing there is invisible in the
    camera editor even though the backend runs it."""
    import io
    ts = io.open("frontend/src/lib/modules.ts", encoding="utf-8").read()
    assert f'id: "{mid}"' in ts


def test_43_every_new_event_type_has_a_severity():
    """An unmapped type silently falls back to `medium`, which is a real classification
    nobody chose."""
    from backend.services.false_detection.config import DEFAULT_SEVERITY_MAP
    from backend.services.modules.registry import MODULES
    for mid in NEW_MODULES:
        for et in MODULES[mid]["event_types"]:
            assert et in DEFAULT_SEVERITY_MAP, f"{mid}:{et} unclassified"


def test_44_tampering_is_critical():
    from backend.services.false_detection.config import DEFAULT_SEVERITY_MAP as M
    assert all(M[e] == "critical"
               for e in ("tamper_blur", "tamper_moved", "tamper_covered"))


def test_45_tripwire_rides_the_fast_lane():
    """It inherits head-count's crossing machine, which needs ~10 fps. On the slow lane
    it would miss people outright — measured in Phase 1."""
    from backend.services.perception import FAST_MODULES
    assert "tripwire" in FAST_MODULES


def test_46_tamper_declares_no_backbone():
    from backend.services.modules.registry import MODULES, backbones_for
    assert MODULES["tamper"]["backbone"] is None
    assert None not in backbones_for(list(MODULES)), \
        "a None backbone must never reach the child as a detector name"


def test_47_vehicle_backbone_is_declared():
    from backend.services.modules.registry import backbones_for
    assert "vehicle" in backbones_for(["no_stopping"])


def test_48_consumers_are_constructible():
    from backend.services.analytics import make_consumer
    for mid in ("tripwire", "crowd", "no_stopping"):
        assert make_consumer(mid, 1, {}) is not None


# ── global toggles ───────────────────────────────────────────────────────────

@pytest.fixture
def toggles():
    from backend.services import module_toggles as mt
    mt.reset()
    yield mt
    mt.reset()


def test_49_absent_means_enabled(toggles):
    """The safety property: deploying this feature must not switch anything off."""
    assert toggles.enabled("tripwire") is True
    assert all(toggles.effective().values())


def test_50_disabling_filters_the_module_out(toggles):
    toggles.set_toggles({"crowd": False})
    mods = [{"module": "crowd"}, {"module": "tripwire"}]
    assert [m["module"] for m in toggles.filter_modules(mods)] == ["tripwire"]


def test_51_unknown_module_ids_are_dropped(toggles):
    """A stale name from an older build must not linger in the panel forever."""
    assert toggles.set_toggles({"crowd": False, "not_a_module": True}) == {"crowd": False}


def test_52_a_toggle_cannot_switch_a_module_on(toggles):
    """Toggles only ever SUBTRACT. Enabling one here must not add it to a camera that
    was never assigned it."""
    toggles.set_toggles({"crowd": True})
    assert [m["module"] for m in toggles.filter_modules([{"module": "tripwire"}])] == \
        ["tripwire"]


def test_53_filter_is_a_no_op_when_nothing_is_toggled(toggles):
    mods = [{"module": "crowd"}, {"module": "tamper"}]
    assert toggles.filter_modules(mods) is mods, "hot path must not copy needlessly"


def test_54_empty_input_is_safe(toggles):
    toggles.set_toggles({"crowd": False})
    assert toggles.filter_modules([]) == []
    assert toggles.filter_modules(None) == []


# ── detector degradation contract ────────────────────────────────────────────

def test_55_vehicle_detector_no_ops_without_weights(monkeypatch, tmp_path):
    """Every detector in this project degrades to "detects nothing" rather than
    crashing the perception child. The new one must keep that contract."""
    from backend.services.detectors import vehicle as vd
    monkeypatch.setenv("VEHICLE_WEIGHTS", str(tmp_path / "absent.pt"))
    vd.reset()
    try:
        assert vd.available() is False
        assert vd.detect_vehicles(np.zeros((64, 64, 3), dtype=np.uint8)) == []
    finally:
        vd.reset()


def test_56_vehicle_detector_handles_an_empty_frame(monkeypatch, tmp_path):
    from backend.services.detectors import vehicle as vd
    monkeypatch.setenv("VEHICLE_WEIGHTS", str(tmp_path / "absent.pt"))
    vd.reset()
    try:
        assert vd.detect_vehicles(None) == []
    finally:
        vd.reset()


def test_57_vehicle_classes_default_to_the_coco_four(monkeypatch):
    from backend.services.detectors import vehicle as vd
    monkeypatch.delenv("VEHICLE_CLASSES", raising=False)
    assert vd._classes() == [2, 3, 5, 7]
    monkeypatch.setenv("VEHICLE_CLASSES", "all")
    assert vd._classes() is None
    monkeypatch.setenv("VEHICLE_CLASSES", "rubbish")
    assert vd._classes() == [2, 3, 5, 7], "a bad value must fall back, not crash"


# ══════════════════════════════════════════════════════════════════════════════
# Regression: the real covered-lens capture from the Kohat camera
# ══════════════════════════════════════════════════════════════════════════════

def _fake_measure(blur, std, hist_shift=0.0):
    """Bypass image synthesis and inject measured signal values directly."""
    import numpy as _np
    h = _np.zeros(32, dtype=float)
    h[8] = 0.5 + hist_shift
    h[16] = 0.5 - hist_shift
    return {"blur": blur, "std": std, "hist": h / h.sum()}


def test_58_the_measured_covered_lens_case_now_fires(tamper_mod, monkeypatch):
    """Captured on camera 50 on 2026-08-27 at 11:43:39, with a hand over the lens:

        normal scene   blur 460-620   luminance std 25-31
        covered        blur 130       luminance std 14.5

    The original absolute thresholds (blur < 45, std < 12) did not fire on ANY of it,
    even though blur had fallen to a quarter and variance to a half. This test pins the
    proportional test that replaced them, using the real numbers."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 5, "cooldown": 0})

    normal = _fake_measure(512.0, 26.0)
    covered = _fake_measure(130.0, 14.5)

    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))

    for _ in range(15):                      # establish the baseline
        clock.tick(1.0)
        assert mon.check(normal) == [], "a steady normal scene must never alert"

    fired = []
    for _ in range(20):
        clock.tick(1.0)
        fired += mon.check(covered)

    kinds = {e["event_type"] for e in fired}
    assert "tamper_covered" in kinds or "tamper_blur" in kinds, \
        f"the measured covered-lens values must trip a signal, got {kinds}"


def test_59_a_dim_camera_is_not_permanently_tampered(tamper_mod, monkeypatch):
    """The converse failure of absolute thresholds: a camera whose normal scene sits at
    blur 80 would have been below a `blur < 45`-style constant's neighbourhood forever.
    A proportional test judges it against its own normal."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 3, "cooldown": 0})
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    dim = _fake_measure(80.0, 9.0)
    fired = []
    for _ in range(40):
        clock.tick(1.0)
        fired += mon.check(dim)
    assert fired == [], "a consistently dim scene is normal FOR THIS CAMERA"


def test_60_a_proportional_drop_fires_on_the_dim_camera_too(tamper_mod, monkeypatch):
    """...and the same dim camera must still detect being covered."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 3, "cooldown": 0})
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    for _ in range(15):
        clock.tick(1.0)
        mon.check(_fake_measure(80.0, 9.0))
    fired = []
    for _ in range(15):
        clock.tick(1.0)
        fired += mon.check(_fake_measure(18.0, 4.0))     # same proportional collapse
    assert fired, "a dim camera must still detect a proportional collapse"


def test_61_baselines_freeze_while_a_signal_is_over(tamper_mod, monkeypatch):
    """If the blur baseline kept adapting during a slow lens degradation, it would
    follow the degradation down and the alert would never fire."""
    t, clock = tamper_mod
    mon = t.TamperMonitor(1, {"sustain_seconds": 2, "cooldown": 0})
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    for _ in range(15):
        clock.tick(1.0)
        mon.check(_fake_measure(512.0, 26.0))
    ref = mon._blur_ref
    for _ in range(30):
        clock.tick(1.0)
        mon.check(_fake_measure(100.0, 12.0))
    assert abs(mon._blur_ref - ref) < 1e-6, "the baseline must freeze while over threshold"


# ══════════════════════════════════════════════════════════════════════════════
# Regression: fast-lane events must still get a picture
# ══════════════════════════════════════════════════════════════════════════════

def test_62_tripwire_events_request_a_snapshot():
    """If this is ever set False the alert loses its evidence silently."""
    tw, clock = make_tripwire()
    ev = walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], clock=clock)[0]
    assert ev["snapshot"] is True


def test_63_headcount_snapshots_do_not_break_the_no_jpeg_optimisation():
    """Head-count NOW asks for a picture on every crossing, and this guards the reason
    that is still compatible with the fast lane carrying no JPEG.

    The optimisation is "do not encode SPECULATIVELY on every frame" - at 10 fps per
    camera that was 18 ms of pure waste per frame to serve an event that fires a few
    times an hour. It is not "head-count never has a picture". The task carries the ring
    sequence of the frame it was built from, and the writer encodes THAT EXACT FRAME on
    demand only when an event needs one, falling back to latest_jpeg() if the ring has
    moved on. Trip wire has used this path since it shipped.

    So the cost is paid per EVENT, not per frame, and the two are compatible. What must
    stay true is that the task still carries no frame_buf - if that ever changes, the
    speculative encode is back and this optimisation really has been lost."""
    from backend.services.analytics.headcount import HeadCount
    hc = HeadCount(1, {"line": VLINE})
    clock = Clock()
    hc._now = clock
    evs = walk(hc, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], clock=clock)
    assert evs, "fixture must produce a crossing"
    assert all(e.get("snapshot") for e in evs), "a crossing must carry a picture"

    # The fast lane must still ship no JPEG; the writer encodes on demand instead.
    import inspect as _inspect
    import sys
    src = _inspect.getsource(sys.modules["backend.services.camera_manager"])
    assert '"frame_buf": None' in src,         "the fast lane started shipping a speculative JPEG again"


def test_64_latest_jpeg_is_none_for_an_unknown_camera():
    """The writer treats None as 'no snapshot'; it must never raise into the DB loop."""
    from backend.services.camera_manager import latest_jpeg
    assert latest_jpeg(999999) is None


def test_65_latest_jpeg_encodes_a_running_camera(monkeypatch):
    import numpy as _np
    # NOTE: `from backend.services import camera_manager` yields the SINGLETON, not the
    # module — services/__init__.py re-exports it under the same name and shadows the
    # submodule. Go through sys.modules to reach the real module.
    import sys
    cm = sys.modules["backend.services.camera_manager"]

    class FakeInst:
        cam_id = 4242
        frame = _np.full((64, 64, 3), 128, dtype=_np.uint8)

    monkeypatch.setitem(cm.camera_manager.instances, "fake://x", FakeInst())
    buf = cm.latest_jpeg(4242)
    assert buf and buf[:2] == b"\xff\xd8", "must be a JPEG"


def test_66_latest_jpeg_handles_a_camera_with_no_frame_yet(monkeypatch):
    # NOTE: `from backend.services import camera_manager` yields the SINGLETON, not the
    # module — services/__init__.py re-exports it under the same name and shadows the
    # submodule. Go through sys.modules to reach the real module.
    import sys
    cm = sys.modules["backend.services.camera_manager"]

    class FakeInst:
        cam_id = 4343
        frame = None

    monkeypatch.setitem(cm.camera_manager.instances, "fake://y", FakeInst())
    assert cm.latest_jpeg(4343) is None


# ══════════════════════════════════════════════════════════════════════════════
# Regression: a stale instance must not cost an alert its picture
# ══════════════════════════════════════════════════════════════════════════════

def _cm():
    import sys
    return sys.modules["backend.services.camera_manager"]


class _Inst:
    def __init__(self, cam_id, frame):
        self.cam_id = cam_id
        self.frame = frame


def _frame(v=128):
    import numpy as _np
    return _np.full((48, 64, 3), v, dtype=_np.uint8)


@pytest.fixture
def clean_jpeg_cache():
    cm = _cm()
    cm._last_jpeg.clear()
    yield cm
    cm._last_jpeg.clear()


def test_67_a_stale_instance_does_not_shadow_the_live_one(clean_jpeg_cache, monkeypatch):
    """`instances` is keyed by source string and never pruned, so one camera id can map
    to several objects — an old entry left behind when the RTSP URL was edited, plus the
    live one. Returning None on the first match with no frame is what produced 'some
    alerts have images and some do not'."""
    cm = clean_jpeg_cache
    monkeypatch.setitem(cm.camera_manager.instances, "rtsp://old", _Inst(7001, None))
    monkeypatch.setitem(cm.camera_manager.instances, "rtsp://new", _Inst(7001, _frame()))
    buf = cm.latest_jpeg(7001)
    assert buf and buf[:2] == b"\xff\xd8", "the live instance must be found"


def test_68_order_does_not_matter(clean_jpeg_cache, monkeypatch):
    cm = clean_jpeg_cache
    monkeypatch.setitem(cm.camera_manager.instances, "rtsp://a", _Inst(7002, _frame()))
    monkeypatch.setitem(cm.camera_manager.instances, "rtsp://b", _Inst(7002, None))
    assert cm.latest_jpeg(7002)


def test_69_a_reconnecting_camera_falls_back_to_the_last_good_frame(clean_jpeg_cache,
                                                                    monkeypatch):
    """The live instance has frame=None for a moment while it reconnects. An alert in
    that window still deserves evidence."""
    cm = clean_jpeg_cache
    inst = _Inst(7003, _frame())
    monkeypatch.setitem(cm.camera_manager.instances, "rtsp://c", inst)
    first = cm.latest_jpeg(7003)
    assert first
    inst.frame = None                      # reconnecting
    again = cm.latest_jpeg(7003)
    assert again == first, "must reuse the cached frame"


def test_70_a_stale_cache_is_refused_not_attached(clean_jpeg_cache, monkeypatch):
    """A picture from minutes ago is not evidence of THIS event. Refusing it is more
    honest than attaching something misleading."""
    cm = clean_jpeg_cache
    inst = _Inst(7004, _frame())
    monkeypatch.setitem(cm.camera_manager.instances, "rtsp://d", inst)
    assert cm.latest_jpeg(7004)
    inst.frame = None
    # age the cache well past the bound
    ts, data = cm._last_jpeg["7004"]
    cm._last_jpeg["7004"] = (ts - cm.SNAPSHOT_FALLBACK_MAX_AGE - 60, data)
    assert cm.latest_jpeg(7004) is None


def test_71_unknown_camera_still_returns_none(clean_jpeg_cache):
    assert clean_jpeg_cache.latest_jpeg(999999) is None


# ══════════════════════════════════════════════════════════════════════════════
# "How many people crossed" — the question the module exists to answer
# ══════════════════════════════════════════════════════════════════════════════

def test_72_the_payload_carries_a_running_count():
    tw, clock = make_tripwire(cooldown=0)
    path = [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8]
    totals = []
    for tid in range(1, 6):
        for ev in walk(tw, path, tid=tid, clock=clock):
            totals.append(ev["data"]["crossings"])
    assert totals == [1, 2, 3, 4, 5], totals


def test_73_the_count_is_visible_on_the_overlay():
    """An operator watching the live view should be able to read the number off it."""
    tw, clock = make_tripwire(cooldown=0)
    walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], tid=1, clock=clock)
    _, ovs = tw.process([person(9, 0.1, 0.8)], [], SHAPE)
    line = next(o for o in ovs if o.get("kind") == "line")
    assert line["label"].strip().endswith("1"), line["label"]


def test_74_a_suppressed_duplicate_does_not_inflate_the_count():
    """The count must be people, not events."""
    tw, clock = make_tripwire(cooldown=60)
    walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], tid=1, clock=clock)
    walk(tw, [0.8, 0.7, 0.6, 0.52, 0.48, 0.4, 0.3, 0.2], tid=1, clock=clock)
    assert tw.crossings == 1


def test_75_detector_recall_knobs_are_exposed(monkeypatch):
    """PERSON_IOU is the knob that decides whether two overlapping people are seen as
    two. It must be reachable without editing code."""
    import importlib
    monkeypatch.setenv("PERSON_CONF", "0.22")
    monkeypatch.setenv("PERSON_IOU", "0.85")
    import backend.services.detectors.person as pd
    importlib.reload(pd)
    try:
        assert pd.PERSON_CONF == 0.22
        assert pd.PERSON_IOU == 0.85
    finally:
        monkeypatch.undo()
        importlib.reload(pd)


def test_76_detector_defaults_are_unchanged():
    """Exposing the knobs must not silently alter behaviour for existing deployments."""
    import backend.services.detectors.person as pd
    assert pd.PERSON_CONF == 0.3
    assert pd.PERSON_IOU == 0.7


# ══════════════════════════════════════════════════════════════════════════════
# Tampering must keep watching when nobody is
# ══════════════════════════════════════════════════════════════════════════════

def test_77_a_tamper_camera_is_exempt_from_the_idle_timeout():
    """The capture loop normally self-terminates 30 s after the last viewer leaves, and
    tampering runs inside that loop. A lens is sprayed or turned precisely when nobody is
    watching, so a tamper camera must keep capturing regardless."""
    cm = _cm()
    inst = cm.CameraInstance("rtsp://x", cam_id=1,
                             modules=[{"module": "tamper", "enabled": True}])
    assert inst._always_on() is True


def test_78_any_analytics_camera_keeps_capturing():
    """CHANGED, and this is the fix for a serious gap: only `tamper` used to be exempt
    from the idle timeout, so an auto-started trip-wire camera would open, run for thirty
    seconds, decide nobody was watching and stop. Detection ceased shortly after boot and
    no alert was ever produced until an operator opened a live feed.

    A camera assigned an analytic is doing work whether or not a human is watching."""
    cm = _cm()
    inst = cm.CameraInstance("rtsp://y", cam_id=2,
                             modules=[{"module": "tripwire", "enabled": True},
                                      {"module": "headcount", "enabled": True}])
    assert inst._always_on() is True


def test_78b_a_camera_with_no_modules_still_idles_out():
    """The exemption must stay narrow. A pure viewing source has nothing to detect, so
    it should still stop when the last viewer leaves - continuous decode is not free."""
    cm = _cm()
    assert cm.CameraInstance("rtsp://none", cam_id=21, modules=[])._always_on() is False
    assert cm.CameraInstance("rtsp://off", cam_id=22,
                             modules=[{"module": "crowd", "enabled": False}]
                             )._always_on() is False


def test_79_a_disabled_tamper_module_does_not_pin_the_camera():
    cm = _cm()
    inst = cm.CameraInstance("rtsp://z", cam_id=3,
                             modules=[{"module": "tamper", "enabled": False}])
    assert inst._always_on() is False


def test_80_no_modules_at_all_is_safe():
    cm = _cm()
    assert cm.CameraInstance("rtsp://w", cam_id=4)._always_on() is False


# ══════════════════════════════════════════════════════════════════════════════
# Persistence and corner crossings
# ══════════════════════════════════════════════════════════════════════════════

def test_81_the_count_can_be_restored_after_a_restart():
    """The total lives in memory in the consumer, so without seeding it resets to zero
    on every restart. It is rebuilt from alert_logs (one row per crossing) rather than
    stored separately — the unified event store rule."""
    tw, clock = make_tripwire(cooldown=0)
    tw.crossings = 42                          # what the writer seeds
    ev = walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], tid=1, clock=clock)[0]
    assert ev["data"]["crossings"] == 43, "counting must continue from the restored total"


def test_82_the_child_accepts_the_seed_command():
    """Guards the wiring: writer -> queue -> child -> consumer."""
    import inspect
    from backend.services import perception
    src = inspect.getsource(perception._child_main)
    assert 'cmd == "seed_crossings"' in src
    assert "tripwire_seed" in src
    apply_src = inspect.getsource(perception._child_main)
    assert 'mid == "tripwire"' in apply_src, "the seed must be applied when built"


def test_83_the_writer_rebuilds_the_total_from_alert_logs():
    import inspect
    from backend.services import perception
    src = inspect.getsource(perception._result_writer)
    assert '"seed_crossings"' in src
    assert 'AlertLog.module == "tripwire"' in src


def test_84_a_corner_crossing_is_not_rejected():
    """Somebody stepping across at the very END of the wire has crossed the perimeter
    just as much as somebody crossing the middle. Head-count rejects that as
    `outside_line_segment` (correctly, for counting through a doorway); a trip wire
    must not."""
    from backend.services.analytics.tripwire import DEF_TW_SEGMENT_MARGIN
    from backend.services.analytics.headcount import DEF_SEGMENT_MARGIN
    assert DEF_TW_SEGMENT_MARGIN > DEF_SEGMENT_MARGIN

    # A wire drawn across the middle of the frame; walk across near its top end.
    tw, clock = make_tripwire()
    tw.config.setdefault("segment_margin", DEF_TW_SEGMENT_MARGIN)
    evs = walk(tw, [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8], tid=1, ny=0.03,
               clock=clock)
    assert evs, "a crossing at the end of the wire must still alert"


def test_85_a_per_camera_segment_margin_still_wins():
    """setdefault, not assignment — an explicit per-camera value must not be clobbered."""
    tw, clock = make_tripwire(segment_margin=0.01)
    tw.process([person(1, 0.2, 0.8)], [], SHAPE)
    assert tw.config["segment_margin"] == 0.01


def test_86_the_default_is_applied_when_absent():
    from backend.services.analytics.tripwire import DEF_TW_SEGMENT_MARGIN
    tw, _ = make_tripwire()
    tw.process([person(1, 0.2, 0.8)], [], SHAPE)
    assert tw.config["segment_margin"] == DEF_TW_SEGMENT_MARGIN


# ══════════════════════════════════════════════════════════════════════════════
# Per-signal sustain: a covered lens must alert FAST
# ══════════════════════════════════════════════════════════════════════════════

def test_87_a_covered_lens_alerts_within_about_two_seconds(tamper_mod, monkeypatch):
    """The operational requirement: someone holding a book over the lens must be
    reported in seconds, not after ten. Justified by a real capture in which `covered`
    fired twice across 279 samples and both were genuine."""
    t, clock = tamper_mod
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    mon = t.TamperMonitor(1, {"cooldown": 0})

    for _ in range(20):                          # baseline
        clock.tick(0.5)
        mon.check(_fake_measure(512.0, 26.0))

    elapsed, fired = 0.0, []
    while elapsed < 4.0 and not fired:
        clock.tick(0.5)
        elapsed += 0.5
        fired = [e for e in mon.check(_fake_measure(130.0, 5.5))
                 if e["event_type"] == "tamper_covered"]
    assert fired, "a covered lens must alert inside 4s"
    assert elapsed <= 3.0, f"took {elapsed}s — too slow for a cover attempt"


def test_88_moved_keeps_the_long_window(tamper_mod, monkeypatch):
    """`moved` fires on ANY large scene change, including somebody walking close past —
    two of its six fires in the real capture were spurious. It must stay patient."""
    t, clock = tamper_mod
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    mon = t.TamperMonitor(1, {"cooldown": 0})
    for _ in range(20):
        clock.tick(0.5)
        mon.check(_fake_measure(512.0, 26.0, hist_shift=0.0))

    fired = []
    for _ in range(8):                            # 4 seconds of a changed scene
        clock.tick(0.5)
        fired += [e for e in mon.check(_fake_measure(500.0, 25.0, hist_shift=0.49))
                  if e["event_type"] == "tamper_moved"]
    assert fired == [], "4s of scene change must not alert; moved needs 10s"


def test_89_a_person_walking_past_does_not_trip_covered(tamper_mod, monkeypatch):
    """The false positive a 2 s window would risk. One sample of darkness as somebody
    passes close must not be enough."""
    t, clock = tamper_mod
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    mon = t.TamperMonitor(1, {"cooldown": 0})
    for _ in range(20):
        clock.tick(0.5)
        mon.check(_fake_measure(512.0, 26.0))

    fired = []
    for i in range(40):                           # one dark sample every 2 s
        clock.tick(0.5)
        fired += mon.check(_fake_measure(130.0, 5.5) if i % 4 == 0
                           else _fake_measure(512.0, 26.0))
    assert fired == [], "intermittent darkness must not sustain"


def test_90_per_camera_sustain_overrides_the_signal_default(tamper_mod):
    t, _ = tamper_mod
    mon = t.TamperMonitor(1, {"covered_sustain_seconds": 7})
    assert mon._sustain_for("covered", 10) == 7.0
    assert mon._sustain_for("blur", 10) == t.SIGNAL_SUSTAIN["blur"]


def test_91_a_bad_override_falls_back_rather_than_crashing(tamper_mod):
    t, _ = tamper_mod
    mon = t.TamperMonitor(1, {"covered_sustain_seconds": "soon"})
    assert mon._sustain_for("covered", 10) == t.SIGNAL_SUSTAIN["covered"]


def test_92_sampling_is_fast_enough_to_resolve_the_window(tamper_mod):
    """A 2 s window sampled once a second rounds the response up to 3 s."""
    t, _ = tamper_mod
    assert t.SAMPLE_INTERVAL <= t.SIGNAL_SUSTAIN["covered"] / 2.0


# ══════════════════════════════════════════════════════════════════════════════
# Regression: the baseline must not chase an obstruction downward
# ══════════════════════════════════════════════════════════════════════════════

def _measure_with_cells(blur, std, n_dead=0, grid=4):
    """A measurement with a controllable number of dead grid cells."""
    import numpy as _np
    h = _np.zeros(32, dtype=float)
    h[8], h[16] = 0.5, 0.5
    cells = _np.full(grid * grid, std, dtype=float)
    if n_dead:
        cells[:n_dead] = std * 0.1
    return {"blur": blur, "std": std, "hist": h / h.sum(), "cells": cells}


def test_93_the_blur_baseline_does_not_chase_an_obstruction(tamper_mod, monkeypatch):
    """THE REGRESSION, replayed from a real capture on 2026-08-27 13:01.

    An object in front of the lens dropped blur 530 -> 240. That was not past the 35%
    trip, so nothing was "over", so the symmetric baseline kept adapting and chased the
    obstruction down: trip fell 193 -> 133 over eight seconds and the alert became
    unreachable. The camera had learned to accept being blocked."""
    t, clock = tamper_mod
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    mon = t.TamperMonitor(1, {"cooldown": 0})

    for _ in range(40):                                   # settle at ~530
        clock.tick(0.5)
        mon.check(_measure_with_cells(530.0, 24.0))
    ref_before = mon._blur_ref
    assert ref_before > 450

    for _ in range(40):                                   # 20s of obstruction
        clock.tick(0.5)
        mon.check(_measure_with_cells(240.0, 24.0))

    assert mon._blur_ref > ref_before * 0.85, (
        f"baseline fell {ref_before:.0f} -> {mon._blur_ref:.0f}; it chased the "
        f"obstruction, which is exactly the bug")


def test_94_the_baseline_still_rises_normally(tamper_mod, monkeypatch):
    """Asymmetry must not stop the baseline tracking a genuinely brighter scene."""
    t, clock = tamper_mod
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    mon = t.TamperMonitor(1, {"cooldown": 0})
    for _ in range(20):
        clock.tick(0.5)
        mon.check(_measure_with_cells(300.0, 20.0))
    start = mon._blur_ref
    for _ in range(60):
        clock.tick(0.5)
        mon.check(_measure_with_cells(600.0, 20.0))
    assert mon._blur_ref > start * 1.5, "a brighter scene must still be learned"


def test_95_a_partial_obstruction_is_detected(tamper_mod, monkeypatch):
    """The other half of the real failure: the object covered PART of the frame, so
    whole-frame std did not move at all (24 throughout). Regional cells catch it."""
    t, clock = tamper_mod
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    mon = t.TamperMonitor(1, {"cooldown": 0})
    for _ in range(30):
        clock.tick(0.5)
        mon.check(_measure_with_cells(530.0, 24.0))

    fired = []
    for _ in range(20):
        clock.tick(0.5)
        # 6 of 16 cells dead (37% of frame); whole-frame std deliberately UNCHANGED
        fired += mon.check(_measure_with_cells(530.0, 24.0, n_dead=6))
    assert any(e["event_type"] == "tamper_covered" for e in fired), \
        "a partial obstruction must alert even when whole-frame variance is unchanged"


def test_96_a_small_object_does_not_trip_the_area_rule(tamper_mod, monkeypatch):
    """A bird, a leaf, someone's shoulder at the frame edge. Two cells of sixteen is
    12% of the view — not an obstruction."""
    t, clock = tamper_mod
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    mon = t.TamperMonitor(1, {"cooldown": 0})
    for _ in range(30):
        clock.tick(0.5)
        mon.check(_measure_with_cells(530.0, 24.0))
    fired = []
    for _ in range(30):
        clock.tick(0.5)
        fired += mon.check(_measure_with_cells(530.0, 24.0, n_dead=2))
    assert fired == [], "12% of the frame is not a cover attempt"


def test_97_the_obscured_fraction_is_reported(tamper_mod, monkeypatch):
    """An operator needs to know HOW MUCH of the view was blocked."""
    t, clock = tamper_mod
    monkeypatch.setattr(t.TamperMonitor, "_measure", staticmethod(lambda f: f))
    mon = t.TamperMonitor(1, {"cooldown": 0})
    for _ in range(30):
        clock.tick(0.5)
        mon.check(_measure_with_cells(530.0, 24.0))
    fired = []
    for _ in range(20):
        clock.tick(0.5)
        fired += mon.check(_measure_with_cells(530.0, 24.0, n_dead=8))
    assert fired and fired[0]["data"]["obscured_fraction"] >= 0.5


# ══════════════════════════════════════════════════════════════════════════════
# Two cameras, two independent trip wires
# ══════════════════════════════════════════════════════════════════════════════

def test_98_two_cameras_keep_separate_state():
    """Reported symptom: a trip wire on two cameras alerted on only one. Whatever the
    cause turns out to be, shared state between consumers would produce exactly that, so
    it is worth pinning that they are independent."""
    a = TripWire(47, {"line": VLINE, "cooldown": 0})
    b = TripWire(50, {"line": VLINE, "cooldown": 0})
    ca, cb = Clock(), Clock()
    a._now, b._now = ca, cb

    path = [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8]
    assert len(walk(a, path, tid=1, clock=ca)) == 1
    assert a.crossings == 1 and b.crossings == 0, "camera B must be unaffected"

    assert len(walk(b, path, tid=1, clock=cb)) == 1, "camera B must alert on its own"
    assert a.crossings == 1 and b.crossings == 1


def test_99_a_cooldown_on_one_camera_does_not_gag_another():
    """The per-track map and the wire timestamp must not be shared across instances."""
    a = TripWire(47, {"line": VLINE, "cooldown": 999, "cooldown_scope": "wire"})
    b = TripWire(50, {"line": VLINE, "cooldown": 999, "cooldown_scope": "wire"})
    ca, cb = Clock(), Clock()
    a._now, b._now = ca, cb
    path = [0.2, 0.3, 0.4, 0.48, 0.52, 0.6, 0.7, 0.8]
    walk(a, path, tid=1, clock=ca)
    assert len(walk(b, path, tid=1, clock=cb)) == 1, \
        "camera A's wire cooldown must not suppress camera B"


def test_100_construction_is_logged_before_any_frame(tmp_path, monkeypatch):
    """A module that is assigned but whose inherited machine throws would otherwise be
    invisible in its own log — indistinguishable from one that was never assigned."""
    from backend.services.analytics import tripwire_debug
    monkeypatch.setenv("TRIPWIRE_DEBUG", "trace")
    monkeypatch.setenv("TRIPWIRE_DEBUG_LOG", str(tmp_path / "tw.log"))
    tripwire_debug.reset()
    try:
        TripWire(50, {"line": VLINE})
        text = (tmp_path / "tw.log").read_text(encoding="utf-8")
        assert "event=CREATED" in text and "camera=50" in text
    finally:
        tripwire_debug.reset()


def test_101_a_missing_line_is_visible_in_the_log(tmp_path, monkeypatch):
    """A wire with no line drawn can never fire. That must be findable in the log rather
    than looking like a detection failure."""
    from backend.services.analytics import tripwire_debug
    monkeypatch.setenv("TRIPWIRE_DEBUG", "trace")
    monkeypatch.setenv("TRIPWIRE_DEBUG_LOG", str(tmp_path / "tw2.log"))
    tripwire_debug.reset()
    try:
        TripWire(51, {})
        assert "has_line=0" in (tmp_path / "tw2.log").read_text(encoding="utf-8")
    finally:
        tripwire_debug.reset()


# ══════════════════════════════════════════════════════════════════════════════
# Capacity: the fast lane must not pay for pixels the detector discards
# ══════════════════════════════════════════════════════════════════════════════

def test_102_the_fast_lane_uses_a_smaller_det_frame():
    """ultralytics letterboxes to imgsz=640 regardless, so a 1280-wide fast-lane frame
    is scaled to 640 inside the detector anyway — the extra pixels are discarded before
    any inference, but they are still pickled through a multiprocessing queue first.

    On a machine reporting 14,198 dropped frames on one camera, that transport is the
    cheapest capacity available."""
    cm = _cm()
    assert cm.DET_WIDTH_FAST < cm.DET_WIDTH
    assert cm.DET_WIDTH_FAST >= 640, "below the detector's own imgsz would lose recall"


def test_103_the_slow_lane_keeps_full_width():
    """Face recognition and Re-ID crops genuinely need it, and the phone detector takes
    crops from this frame. Only the fast lane is person-only and therefore safe."""
    cm = _cm()
    assert cm.DET_WIDTH == 1280


def test_104_dropped_frames_are_counted_not_silent():
    """A full queue used to skip the frame with no counter and no log, so a starved
    camera looked like a broken module."""
    cm = _cm()
    assert callable(cm.dropped_frames)
    assert isinstance(cm.dropped_frames(), dict)


def test_105_no_fast_lane_module_needs_the_wider_frame():
    """The justification for the smaller fast-lane frame, stated as what it actually is.

    This asserted `backbone == "person"`, which was true when only head-count and the
    trip wire were on the lane but was never the real constraint. camera_manager says
    plainly what the extra width buys: ultralytics re-letterboxes to imgsz=640 anyway,
    so DET_WIDTH=1280 "mainly benefits the FACE backbone (det_size 640x640 over a wider
    frame) and the Re-ID crops".

    So the invariant is that no fast-lane module may need `face`. `vehicle` is fine at
    640 - a vehicle is larger in frame than a person, and if a walker is resolvable there
    a car certainly is."""
    from backend.services.modules.registry import MODULES
    cm = _cm()
    for mid in cm.FAST_MODULES:
        bb = MODULES[mid]["backbone"]
        assert bb != "face", (
            f"{mid} is on the fast lane but needs the face backbone; DET_WIDTH_FAST "
            f"halves the width that backbone depends on")
        assert bb in ("person", "vehicle"), (
            f"{mid} needs {bb} - check it survives DET_WIDTH_FAST before adding it")


def test_105b_vehicle_count_is_not_fed_person_tracks():
    """`_CONSUMERS` membership says "this is a track-fed rule", not "these are the right
    tracks". vehicle_count is such a rule, and run_consumers would have handed it people
    - counting pedestrians as cars, silently, because the shapes are identical."""
    import inspect

    from backend.services import perception
    src = inspect.getsource(perception._child_main)
    assert 'get("backbone") not in (None, "person")' in src,         "run_consumers must skip modules whose backbone is not person"
    assert "run_vehicle_count" in src


# ══════════════════════════════════════════════════════════════════════════════
# Every registry parameter must be editable in the UI
# ══════════════════════════════════════════════════════════════════════════════

def test_106_min_people_may_be_set_to_two():
    """Two people standing together is a gathering if the operator says it is."""
    c = Crowd(1, {"min_people": 2})
    assert c._int("min_people", 4) == 2


def test_106b_person_recall_knobs_are_per_camera(crowd_clock):
    """The reason a real gathering failed to fire, and it was not the crowd rule.

    logs/crowd_debug.log, camera 47, 2026-08-28: two people 0.25 body-heights apart with
    STABLE track ids 862 and 941. 941 was detected continuously; 862 vanished and
    returned over and over. Stable ids rule out tracking churn, and min_pair_persons of
    0.25 against a 1.2 radius rules out the clustering rule - when both WERE seen they
    always clustered. The person detector was dropping the one behind, which is what
    PERSON_IOU documents. Those knobs were global environment variables, so an operator
    could not reach them for the one camera that needed them."""
    from backend.services.modules.registry import MODULES
    for mid in ("crowd", "headcount"):
        assert "person_iou" in MODULES[mid]["config"], mid
        assert "person_conf" in MODULES[mid]["config"], mid


def test_106c_the_detector_honours_a_per_camera_override(monkeypatch):
    """And the value actually reaches the inference call rather than being cosmetic."""
    import backend.services.detectors.person as person
    seen = {}

    class FakeModel:
        def predict(self, frame, **kw):
            seen.update(kw)
            return []

    monkeypatch.setattr(person, "_get_model", lambda k: FakeModel())
    monkeypatch.setattr(person, "SHARED_MODEL", True)
    person.detect_and_track(object(), "1", conf=0.2, iou=0.85)
    assert seen["conf"] == 0.2 and seen["iou"] == 0.85
    seen.clear()
    person.detect_and_track(object(), "1")          # defaults when unset
    assert seen["conf"] == person.PERSON_CONF and seen["iou"] == person.PERSON_IOU


def test_107_min_people_floor_is_one_and_one_is_honoured(crowd_clock):
    """One person is not a "gathering" in any real sense, and the DEFAULT is still 4.

    But the FLOOR is 1, not 2, because setting 1 is how an operator proves the camera,
    the zone and the whole chain work with a single person in front of the lens. Both
    this clamp and the UI's `min` used to force 2 silently, which made a reported
    "I set 1 and nothing happened" impossible to answer."""
    c = make_crowd(min_people=1, sustain_seconds=1)
    evs = run_for(c, [person(1, 0.5, 0.8)], 10, clock=crowd_clock)
    assert evs, "min_people=1 must alert on a single person - it is the test setting"
    assert any(e["event_type"] == "gathering" for e in evs)

def test_108_two_people_together_alert_when_configured(crowd_clock):
    c = make_crowd(min_people=2, radius=0.12, sustain_seconds=3)
    evs = run_for(c, huddle(2), 10, clock=crowd_clock)
    assert [e["event_type"] for e in evs] == ["gathering"]
    assert evs[0]["value"] == 2


def test_109_every_config_field_is_reachable_in_the_ui():
    """A parameter the registry declares but the editor cannot render is reachable only
    by environment variable, and only with a restart. That was the situation for every
    Kohat POC parameter until the spec-driven renderer existed."""
    import io as _io
    import re as _re
    from backend.services.modules.registry import MODULES

    ts = _io.open("frontend/src/lib/modules.ts", encoding="utf-8").read()
    specced = set(_re.findall(r'^  ([a-z_]+): \{', ts, _re.M))
    handcoded = set(_re.findall(r'"([a-z_]+)"',
                                ts.split("HANDCODED_FIELDS")[1].split("]")[0]))

    missing = []
    for mid in ("tripwire", "crowd", "no_stopping", "tamper"):
        for f in MODULES[mid]["config"]:
            if f not in specced and f not in handcoded:
                missing.append(f"{mid}.{f}")
    assert not missing, f"no UI renderer for: {missing}"


def test_110_per_camera_config_is_applied_without_a_restart():
    """The consumer re-reads `config` every frame, and PUT /api/cameras refreshes the
    running instance — so an edit takes effect live. Guard the re-read."""
    import inspect
    from backend.services import perception
    src = inspect.getsource(perception._child_main)
    assert "c.config = m_" in src, "consumers must pick up an edited config each frame"


# ══════════════════════════════════════════════════════════════════════════════
# Crowd must survive the detector losing sight for a moment
# ══════════════════════════════════════════════════════════════════════════════

def test_111_a_dropped_frame_does_not_reset_the_sustain_timer(crowd_clock):
    """Measured on a real camera: 481 of 523 evaluations detected NOBODY, on a camera
    where people were demonstrably present, because the perception queue was saturated.

    Against that, a strict state machine can never accumulate a sustain window — the
    cluster is seen, a frame is lost, the timer resets, and a genuine ten-second
    gathering is reported as a series of half-second ones."""
    c = make_crowd(min_people=2, sustain_seconds=5, grace_seconds=3)
    group = huddle(2)
    fired = []
    for i in range(24):                       # 12s, every 4th evaluation sees nobody
        crowd_clock.tick(0.5)
        evs, _ = c.process([] if i % 4 == 3 else group, [], SHAPE)
        fired += evs
    assert any(e["event_type"] == "gathering" for e in fired), \
        "intermittent detection must still accumulate a sustain window"


def test_112_a_genuine_dispersal_is_still_reported(crowd_clock):
    """Grace must bridge a gap, not mask people actually leaving."""
    c = make_crowd(min_people=2, sustain_seconds=2, grace_seconds=3)
    got = run_for(c, huddle(2), 8, clock=crowd_clock)
    assert [e["event_type"] for e in got] == ["gathering"]
    after = run_for(c, [], 12, clock=crowd_clock)     # well past the grace window
    assert [e["event_type"] for e in after] == ["dispersed"]


def test_113_grace_does_not_manufacture_a_gathering(crowd_clock):
    """Two people who are together for one frame and then genuinely apart must not
    accumulate a window from nothing."""
    c = make_crowd(min_people=2, sustain_seconds=5, grace_seconds=3)
    fired = []
    for i in range(40):
        crowd_clock.tick(0.5)
        # together for a single evaluation every 10s, apart otherwise
        evs, _ = c.process(huddle(2) if i % 20 == 0 else spread_out(2), [], SHAPE)
        fired += evs
    assert fired == [], "a momentary pairing every 10s is not a gathering"


def test_114_the_measured_radius_links_people_who_are_together():
    """From the capture: closest-pair distances of 0.133-0.243 when people stood
    together. The old 0.12 default linked NONE of them."""
    import backend.services.analytics.crowd as cm
    assert cm.DEF_RADIUS >= 0.18, "0.12 was measured to link 0% of real groupings"


def test_115_closest_pair_distance_is_logged(tmp_path, monkeypatch, crowd_clock):
    """The measurement needed to choose `radius`. Without it the log says only that a
    cluster failed to form, not how far apart people actually were."""
    from backend.services.analytics import crowd_debug
    monkeypatch.setenv("CROWD_DEBUG", "trace")
    monkeypatch.setenv("CROWD_DEBUG_LOG", str(tmp_path / "c.log"))
    crowd_debug.reset()
    try:
        c = make_crowd(min_people=9)
        run_for(c, spread_out(3), 2, clock=crowd_clock)
        assert "min_pair=" in (tmp_path / "c.log").read_text(encoding="utf-8")
    finally:
        crowd_debug.reset()


# ══════════════════════════════════════════════════════════════════════════════
# Log volume and alert evidence
# ══════════════════════════════════════════════════════════════════════════════

def test_116_an_empty_frame_is_not_logged(tmp_path, monkeypatch, crowd_clock):
    """78% of a real capture was `people=0` lines, burying the handful that mattered."""
    from backend.services.analytics import crowd_debug
    monkeypatch.setenv("CROWD_DEBUG", "trace")
    monkeypatch.setenv("CROWD_DEBUG_LOG", str(tmp_path / "quiet.log"))
    crowd_debug.reset()
    try:
        c = make_crowd()
        run_for(c, [], 30, clock=crowd_clock)
        text = (tmp_path / "quiet.log").read_text(encoding="utf-8")
        assert "event=CLUSTER" not in text, "an empty frame has nothing to say"
    finally:
        crowd_debug.reset()


def test_117_people_present_are_still_logged(tmp_path, monkeypatch, crowd_clock):
    """Quietening must not hide the observations needed to tune `radius_persons`."""
    from backend.services.analytics import crowd_debug
    monkeypatch.setenv("CROWD_DEBUG", "trace")
    monkeypatch.setenv("CROWD_DEBUG_LOG", str(tmp_path / "busy.log"))
    crowd_debug.reset()
    try:
        c = make_crowd(min_people=9)         # never alerts; we only want the traces
        run_for(c, spread_out(3), 6, clock=crowd_clock)
        text = (tmp_path / "busy.log").read_text(encoding="utf-8")
        assert "event=CLUSTER" in text and "min_pair_persons=" in text
    finally:
        crowd_debug.reset()


def test_118_a_pending_cluster_is_logged_even_against_an_empty_view(
        tmp_path, monkeypatch, crowd_clock):
    """The timer running down while nobody is visible IS information — that is the
    grace window at work, and hiding it would make a bridged gap invisible."""
    from backend.services.analytics import crowd_debug
    monkeypatch.setenv("CROWD_DEBUG", "trace")
    monkeypatch.setenv("CROWD_DEBUG_LOG", str(tmp_path / "gap.log"))
    crowd_debug.reset()
    try:
        c = make_crowd(min_people=2, sustain_seconds=10, grace_seconds=5)
        run_for(c, huddle(2), 2, clock=crowd_clock)     # form a cluster
        run_for(c, [], 2, clock=crowd_clock)            # then vanish, inside grace
        text = (tmp_path / "gap.log").read_text(encoding="utf-8")
        assert "event=GAP" in text
    finally:
        crowd_debug.reset()


def test_119_both_gathering_and_dispersal_carry_a_snapshot(crowd_clock):
    """An alert row with no thumbnail reads as broken next to one that has it, and the
    view AFTER a group leaves is the evidence the situation resolved."""
    c = make_crowd(min_people=2, sustain_seconds=2, grace_seconds=0)
    got = run_for(c, huddle(2), 8, clock=crowd_clock)
    after = run_for(c, [], 8, clock=crowd_clock)
    kinds = {e["event_type"]: e for e in got + after}
    assert kinds["gathering"]["snapshot"] is True
    assert kinds["dispersed"]["snapshot"] is True


# ══════════════════════════════════════════════════════════════════════════════
# #5 Object dropped and left
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def unattended_mod(monkeypatch, tmp_path):
    import backend.services.unattended as u
    from backend.services.analytics import unattended_debug
    monkeypatch.setenv("UNATTENDED_DEBUG", "trace")
    monkeypatch.setenv("UNATTENDED_DEBUG_LOG", str(tmp_path / "unattended_debug.log"))
    unattended_debug.reset()
    clock = Clock()
    monkeypatch.setattr(u.time, "time", clock)
    monkeypatch.setattr(u, "SAMPLE_INTERVAL", 0.0)
    # No overlay store in a unit test, so no person boxes - exercised separately.
    monkeypatch.setattr(u.UnattendedMonitor, "_person_boxes", lambda self: [])
    yield u, clock
    unattended_debug.reset()


def room(seed=0):
    """A textured static scene."""
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(120, 25, (240, 320, 3)), 0, 255).astype(np.uint8)


def with_object(base, x=140, y=100, w=44, h=44, val=20):
    """The same scene with a dark compact object placed in it."""
    img = base.copy()
    img[y:y + h, x:x + w] = val
    return img


def run_frames(mon, frame, clock, seconds, step=0.5):
    evs = []
    for _ in range(max(1, int(seconds / step))):
        clock.tick(step)
        evs += mon.check(frame)
    return evs


def test_120_an_object_left_in_the_zone_alerts_after_the_dwell(unattended_mod):
    """The headline behaviour: something appears, stops moving, and is still there
    `abandon_seconds` later."""
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 30, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    base = room(1)
    run_frames(mon, base, clock, 60)                       # learn the empty scene
    evs = run_frames(mon, with_object(base), clock, 90)    # drop it, wait past 30 s
    kinds = {e["event_type"] for e in evs}
    assert "unattended_object" in kinds, kinds
    ev = next(e for e in evs if e["event_type"] == "unattended_object")
    assert ev["data"]["dwell_seconds"] >= 30
    assert ev["snapshot"] is True


def test_121_nothing_alerts_before_the_dwell_expires(unattended_mod):
    """A 30 s threshold must not fire at 10 s - the timer is the whole feature."""
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 30, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    base = room(2)
    run_frames(mon, base, clock, 60)
    assert run_frames(mon, with_object(base), clock, 10) == []


def test_122_furniture_present_from_the_start_never_alerts(unattended_mod):
    """THE argument for background subtraction over a bag detector.

    An object that was there before the module started IS the background. It was never
    dropped, so no timer may ever start on it - however abandoned it looks. A detector
    cannot make this distinction at all, which is why the permanent handbag on a real
    camera would alert forever."""
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 30, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    scene = with_object(room(3))          # the object is present from the first frame
    assert run_frames(mon, scene, clock, 200) == [],         "permanent furniture must never be reported as abandoned"


def test_123_an_object_removed_before_the_dwell_does_not_alert(unattended_mod):
    """Somebody sets a bag down, picks it up again, and walks off."""
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 30, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    base = room(4)
    run_frames(mon, base, clock, 60)
    run_frames(mon, with_object(base), clock, 8)      # put down
    assert run_frames(mon, base, clock, 40) == []     # taken away again


def test_124_the_dwell_threshold_is_per_camera(unattended_mod):
    """30 s in a corridor, longer where people legitimately set things down."""
    u, clock = unattended_mod
    base = room(5)
    slow = u.UnattendedMonitor(1, {"abandon_seconds": 300, "settle_seconds": 1,
                                   "min_area": 0.002, "zones": []})
    run_frames(slow, base, clock, 60)
    assert run_frames(slow, with_object(base), clock, 60) == [],         "a 300 s threshold must not fire at 60 s"


def test_125_a_zone_confines_where_it_watches(unattended_mod):
    """An object dropped outside the drawn zone is not this camera's problem."""
    u, clock = unattended_mod
    # zone covers the RIGHT half only; the object is placed on the left.
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 20, "settle_seconds": 1,
                                  "min_area": 0.002,
                                  "zones": [[[0.6, 0.0], [1.0, 0.0], [1.0, 1.0], [0.6, 1.0]]]})
    base = room(6)
    run_frames(mon, base, clock, 60)
    assert run_frames(mon, with_object(base, x=20, y=100), clock, 60) == []


def test_126_a_person_standing_still_is_not_an_abandoned_object(unattended_mod,
                                                                monkeypatch):
    """A motionless person is a static region too, and would otherwise be reported.

    Suppressed against the live person boxes the perception child already publishes, so
    it costs no inference.

    Uses the shared fixture and only overrides `_person_boxes`. An earlier version set up
    its own logging instead, which left a handler open on ITS tmp_path and made the next
    test read a file that was never written - a self-inflicted version of exactly the
    isolation problem this file already has elsewhere."""
    u, clock = unattended_mod
    # A person box covering exactly where the "object" appears.
    monkeypatch.setattr(u.UnattendedMonitor, "_person_boxes",
                        lambda self: [(0.40, 0.35, 0.62, 0.65)])
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 20, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    base = room(7)
    run_frames(mon, base, clock, 60)
    assert run_frames(mon, with_object(base), clock, 60) == [],         "a static region covered by a live person track must be suppressed"


def test_127_the_diagnostic_log_records_the_decision(unattended_mod, tmp_path):
    """The log must name what happened, not just that nothing did."""
    import io as _io
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 20, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    base = room(8)
    run_frames(mon, base, clock, 60)
    run_frames(mon, with_object(base), clock, 60)
    from backend.services.analytics import unattended_debug
    text = _io.open(unattended_debug.log_path(), encoding="utf-8",
                    errors="replace").read()
    assert "event=SESSION" in text, "the config in force must be recorded"
    assert "event=UNATTENDED" in text and "verdict=ALERT" in text
    assert "abandon=" in text and "dwell=" in text



# ══════════════════════════════════════════════════════════════════════════════
# Head-count: a picture on every entry and exit
# ══════════════════════════════════════════════════════════════════════════════

def test_140_entry_and_exit_events_ask_for_a_snapshot():
    """A crossing log without pictures cannot answer "who was that".

    Head-count rides the FAST lane, whose tasks carry no JPEG - encoding one 10x a second
    per camera would be waste. The task carries the ring sequence instead and the writer
    encodes THAT frame on demand, so the cost is paid per EVENT, not per frame. Trip wire
    has used this path since it shipped."""
    from backend.services.analytics.headcount import HeadCount
    c = HeadCount(1, {"line": [[0.5, 0.0], [0.5, 1.0]]})
    evs = walk(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70])
    assert evs, "the fixture must actually produce a crossing"
    for e in evs:
        assert e["event_type"] in ("entry", "exit")
        assert e.get("snapshot") is True, "every crossing must carry a picture"


def test_141_the_picture_can_be_switched_off_per_camera():
    """The cost is real on a busy door - a full-resolution image per crossing, both
    ways - so a camera where the count matters more than the evidence can opt out."""
    from backend.services.analytics.headcount import HeadCount
    c = HeadCount(1, {"line": [[0.5, 0.0], [0.5, 1.0]], "snapshot": False})
    evs = walk(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70])
    assert evs
    assert all(e.get("snapshot") is False for e in evs)


def test_142_the_toggle_is_reachable_from_the_admin_panel():
    """An env var is not a per-camera setting. A busy door and a quiet one need
    different answers, and the operator is the one who knows which is which."""
    from backend.services.modules.registry import MODULES
    assert "snapshot" in MODULES["headcount"]["config"]


def test_143_swapping_in_and_out_reverses_the_verdict():
    """Which side of the line counts as IN depends on the ORDER the two points were
    drawn in - which the operator cannot see and has no reason to think about. So half
    the time a correctly-drawn line counts the door backwards, with nothing in the editor
    to reveal it. This is the toggle that fixes it without redrawing the line."""
    from backend.services.analytics.headcount import HeadCount
    path = [0.30, 0.40, 0.48, 0.52, 0.60, 0.70]
    line = [[0.5, 0.0], [0.5, 1.0]]

    normal = walk(HeadCount(1, {"line": line}), path)
    swapped = walk(HeadCount(1, {"line": line, "invert_direction": True}), path)

    assert normal and swapped, "the fixture must produce a crossing both ways"
    assert {e["event_type"] for e in normal} == {"entry"} or \
           {e["event_type"] for e in normal} == {"exit"}
    got_n = {e["event_type"] for e in normal}
    got_s = {e["event_type"] for e in swapped}
    assert got_n != got_s, f"swapping must reverse the verdict, got {got_n} both ways"
    assert got_s == ({"exit"} if got_n == {"entry"} else {"entry"})


def test_144_the_totals_follow_the_swap():
    """The IN/OUT overlay counts the same way the events do, or the screen and the
    database disagree about the same crossing."""
    from backend.services.analytics.headcount import HeadCount
    path = [0.30, 0.40, 0.48, 0.52, 0.60, 0.70]
    line = [[0.5, 0.0], [0.5, 1.0]]

    n = HeadCount(1, {"line": line}); walk(n, path)
    s = HeadCount(1, {"line": line, "invert_direction": True}); walk(s, path)
    assert (n.entries, n.exits) == (s.exits, s.entries), \
        f"totals must mirror: normal={n.entries}/{n.exits} swapped={s.entries}/{s.exits}"


# ══════════════════════════════════════════════════════════════════════════════
# #5 the three faults a real capture exposed
# ══════════════════════════════════════════════════════════════════════════════

# Real alert boxes from unattended_debug.log, camera 25, 2026-09-01. 41 of the 59 alerts
# had a box touching a frame edge; these are a representative sample of each side.
FIELD_EDGE_BOXES = [
    (0.0000, 0.7830, 0.0620, 1.0000),   # left AND bottom
    (0.0160, 0.0000, 0.1120, 0.0440),   # top
    (0.5470, 0.0000, 0.6690, 0.0670),   # top
    (0.7940, 0.2390, 1.0000, 0.9060),   # right
    (0.9750, 0.0330, 1.0000, 0.1720),   # right
    (0.0000, 0.1940, 0.0590, 0.2780),   # left
]
FIELD_INTERIOR_BOXES = [
    (0.5340, 0.1390, 0.6120, 0.2000),
    (0.5720, 0.5610, 0.6810, 0.6440),
    (0.7840, 0.7780, 0.8250, 0.8720),
]


def test_150_regions_touching_the_frame_edge_are_rejected():
    """69% of a real capture's alerts hugged a frame border.

    Auto-exposure hunting, vignetting and compression artefacts along the edge all read
    as "new, and now static" to a background model - which is precisely the test this
    module applies. A dropped object is somewhere IN the scene; something clinging to the
    border is an artefact or half out of view, and half an object is not evidence."""
    import backend.services.unattended as u
    m = u.EDGE_MARGIN
    assert m > 0, "the edge guard must be on by default"

    def touches(b):
        return b[0] <= m or b[1] <= m or b[2] >= 1.0 - m or b[3] >= 1.0 - m

    assert all(touches(b) for b in FIELD_EDGE_BOXES), \
        "every box the field capture produced at an edge must be caught"
    assert not any(touches(b) for b in FIELD_INTERIOR_BOXES), \
        "interior boxes must survive - they are the ones worth reporting"


def test_151_the_same_place_is_reported_once_not_repeatedly(unattended_mod):
    """The reported fault: continuous alerts for one object.

    The cooldown lived on the SLOT, and the capture shows 5915 APPEARED / 5915 GONE
    pairs - a static region flickers in and out of the mask as light shifts, and each
    rebuilt slot arrived with alerted=False and a fresh cooldown. The memory has to
    outlive the slot, so it is keyed on the LOCATION."""
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 20, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    base = room(11)
    run_frames(mon, base, clock, 60)
    obj = with_object(base)

    first = run_frames(mon, obj, clock, 60)
    assert len(first) == 1, f"the object must be reported once, got {len(first)}"

    # Force the exact churn the field capture showed: drop every slot, then bring the
    # identical object back. Before the fix this produced a second alert.
    mon._slots = []
    again = run_frames(mon, obj, clock, 90)
    assert again == [], \
        "a rebuilt slot at the same place must NOT re-alert - that is the reported bug"


def test_151b_an_object_left_for_a_long_time_alerts_exactly_once(unattended_mod):
    """ONE alert per object, not one per interval.

    The first fix suppressed a place for 15 minutes and then let it alert again - so a
    bag that had not moved was re-reported forever, just more slowly. That is still the
    behaviour that was reported as wrong. The memory is now REFRESHED for as long as the
    object is still visible, so the forget timer never starts while it is there."""
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 20, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    base = room(13)
    run_frames(mon, base, clock, 60)
    obj = with_object(base)

    evs = run_frames(mon, obj, clock, 60)
    assert len(evs) == 1, f"first sighting must alert once, got {len(evs)}"

    # Twenty more minutes of the SAME object sitting in the SAME place.
    later = run_frames(mon, obj, clock, 1200)
    assert later == [],         f"an object that never moved must never alert again, got {len(later)} more"


def test_151c_a_new_object_after_the_place_clears_is_a_new_alert(unattended_mod):
    """The memory must not become a permanent blind spot.

    Once the place has been clear for `forget_seconds`, something arriving there later is
    a genuinely new event and has to be reported - otherwise the first bag ever left in a
    doorway would silence that doorway for good."""
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 20, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": [],
                                  "forget_seconds": 30})
    base = room(14)
    run_frames(mon, base, clock, 60)
    obj = with_object(base)

    assert len(run_frames(mon, obj, clock, 60)) == 1
    run_frames(mon, base, clock, 120)          # object removed, place clears
    assert len(run_frames(mon, obj, clock, 60)) == 1,         "a new object in a cleared place must be reported"


def test_152_the_alert_carries_a_box_for_the_saved_picture(unattended_mod):
    """"An object was left somewhere" is not actionable. The operator has to see WHERE,
    and on a busy scene they will never find it unaided.

    The box was already built and attached to the event - and then dropped, because
    submit_events sent overlays=None. It now travels as snapshot_overlays."""
    import inspect

    import sys as _sys

    import backend.services.camera_manager  # noqa: F401  (registers the module)
    from backend.services import perception
    # `from backend.services import camera_manager` yields the INSTANCE, not the module -
    # services/__init__ exports it under the same name.
    camera_manager = _sys.modules["backend.services.camera_manager"]
    u, clock = unattended_mod
    mon = u.UnattendedMonitor(1, {"abandon_seconds": 20, "settle_seconds": 1,
                                  "min_area": 0.002, "zones": []})
    base = room(12)
    run_frames(mon, base, clock, 60)
    evs = run_frames(mon, with_object(base), clock, 60)
    assert evs, "fixture must alert"
    ov = (evs[0].get("overlays") or [])
    assert ov and ov[0]["kind"] == "box", "the event must carry a box"
    assert ov[0].get("label", "").startswith("UNATTENDED")
    assert ov[0].get("snapshot") is True

    # ...and the plumbing that used to throw it away must now carry it.
    assert "snapshot_overlays" in inspect.signature(perception.submit_events).parameters
    cm_src = inspect.getsource(camera_manager.CameraInstance._check_unattended)
    assert "snapshot_overlays=ovs" in cm_src


# ══════════════════════════════════════════════════════════════════════════════
# Vehicle counting by type
# ══════════════════════════════════════════════════════════════════════════════

VC_LINE = [[0.5, 0.0], [0.5, 1.0]]


def veh(tid, nx, ny=0.6, kind="car", w=0.10, h=0.08):
    """A tracked vehicle detection, the shape detect_and_track_vehicles returns."""
    x1, x2 = (nx - w / 2) * 1280, (nx + w / 2) * 1280
    y1, y2 = (ny - h / 2) * 720, (ny + h / 2) * 720
    return {"id": tid, "box": (x1, y1, x2, y2),
            "centroid": ((x1 + x2) / 2, (y1 + y2) / 2),
            "conf": 0.8, "cls": 2, "kind": kind}


def drive(c, xs, tid=1, kind="car", ny=0.6):
    evs = []
    for x in xs:
        e, _ = c.process([veh(tid, x, ny, kind)], [], (720, 1280))
        evs += e
    return evs


def make_vc(**cfg):
    from backend.services.analytics.vehicle_count import VehicleCount
    return VehicleCount(1, {"line": VC_LINE, **cfg})


def test_160_a_car_crossing_is_counted_as_a_car():
    c = make_vc()
    evs = drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], kind="car")
    assert evs, "the crossing machine must fire"
    assert all(e["event_type"] in ("vehicle_entry", "vehicle_exit") for e in evs), \
        "events must be relabelled - `entry` on this module would collide with people"
    assert evs[0]["data"]["vehicle_type"] == "car"
    assert c.by_kind["car"][0] + c.by_kind["car"][1] == 1


def test_161_each_type_gets_its_own_in_out_tally():
    """The whole point: "how many vehicles" answers almost nothing on a road camera.

    The opposing vehicle is deliberately in a different LANE (ny). Starting it where the
    outbound ones finished makes head-count's id hand-off treat it as the same track
    re-acquired - which is the anti-double-count behaviour working correctly, and would
    make this a test of the fixture rather than of the tally."""
    c = make_vc()
    drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], tid=1, kind="car", ny=0.45)
    drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], tid=2, kind="motorcycle", ny=0.45)
    drive(c, [0.70, 0.60, 0.52, 0.48, 0.40, 0.30], tid=3, kind="car", ny=0.75)
    assert set(c.by_kind) == {"car", "motorcycle"}
    car_in, car_out = c.by_kind["car"]
    assert car_in + car_out == 2, "two cars crossed"
    assert car_in == 1 and car_out == 1, "one each way"
    assert sum(c.by_kind["motorcycle"]) == 1


def test_162_the_breakdown_travels_with_the_event():
    """So a dashboard can show the split without replaying the whole table."""
    c = make_vc()
    drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], tid=1, kind="car")
    evs = drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], tid=2, kind="truck")
    bt = evs[-1]["data"]["by_type"]
    assert bt["car"]["in"] + bt["car"]["out"] == 1
    assert bt["truck"]["in"] + bt["truck"]["out"] == 1


def test_163_vehicle_types_can_be_narrowed_per_camera():
    """A camera watching a car park does not want every passing motorcycle."""
    c = make_vc(vehicle_types="car")
    assert drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], kind="motorcycle") == []
    assert drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], tid=2, kind="car")


def test_164_the_overlay_shows_the_breakdown():
    """An operator should read the split off the live view, not the database."""
    c = make_vc()
    drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], tid=1, kind="car")
    drive(c, [0.30, 0.40, 0.48, 0.52, 0.60, 0.70], tid=2, kind="motorcycle")
    label = next(o["label"] for o in c._overlays(VC_LINE, 0.02, False)
                 if o.get("kind") == "line")
    assert "CAR" in label and "MOTO" in label, label


def test_165_it_inherits_the_hardened_crossing_machine():
    """Not a second implementation. HeadCount's band, movement validation, direction
    consistency and debounce all exist because a real capture showed they were needed;
    rewriting them would mean rediscovering all of it."""
    from backend.services.analytics.headcount import HeadCount
    from backend.services.analytics.vehicle_count import VehicleCount
    assert issubclass(VehicleCount, HeadCount)
    c = make_vc()
    # A vehicle that never reaches the line must not count.
    assert drive(c, [0.20, 0.25, 0.30, 0.28, 0.22]) == []
    # Nor one that jitters ON the line without crossing it.
    assert drive(c, [0.49, 0.50, 0.51, 0.50, 0.49], tid=9) == []


def test_166_counting_uses_the_tracked_detector_no_stopping_does_not():
    """They need opposite things and both are correct.

    A stationary vehicle is matched better by box overlap than by a motion tracker; a
    vehicle crossing a line cannot be identified by overlap at all."""
    from backend.services.detectors import vehicle
    assert hasattr(vehicle, "detect_and_track_vehicles")
    assert hasattr(vehicle, "detect_vehicles"), "no_stopping still needs the untracked path"


def test_167_rickshaw_is_not_claimed():
    """COCO has no rickshaw, tuk-tuk or three-wheeler class of any kind. Counting one
    under a wrong label would be worse than not counting it."""
    from backend.services.detectors.vehicle import CLASS_NAMES
    names = {v.lower() for v in CLASS_NAMES.values()}
    assert not any("rickshaw" in n or "tuk" in n for n in names)
    from backend.services.modules.registry import MODULES
    assert "rickshaw" in MODULES["vehicle_count"]["description"].lower(), \
        "the limitation must be stated where an operator will see it"
