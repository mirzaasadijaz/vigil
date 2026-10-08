"""
Phase 2 phone-use regression suite.

Deterministic: person tracks and phone detections are constructed directly, so the
association geometry and the temporal state machine are exercised without a GPU, model
weights or a camera. What a real capture adds on top is detector recall, which no unit
test can stand in for — that is what the diagnostic log and replay tooling are for.

Run:  pytest tests/test_phone.py -q
"""

from __future__ import annotations

import pytest

from backend.services.analytics.mobile import (
    CONFIRMED, NO_PHONE, RELEASED, MobileUsage,
)

FRAME_W, FRAME_H = 1280, 720
SHAPE = (FRAME_H, FRAME_W)

# Normalised person geometry roughly matching the real camera-47 measurements.
P_W, P_H = 0.10, 0.50
# A phone is a small fraction of a person: ~0.018 x 0.033 normalised here.
PH_W, PH_H = 0.018, 0.033


class Clock:
    def __init__(self, fps: float = 5.0):
        self.t = 5000.0
        self.dt = 1.0 / fps

    def tick(self, n: int = 1):
        self.t += self.dt * n

    def __call__(self):
        return self.t


def make_consumer(fps: float = 5.0, **config):
    cfg = {"module": "mobile", "enabled": True}
    cfg.update(config)
    mc = MobileUsage(cam_id=3, config=cfg)
    clock = Clock(fps)
    mc._now = clock
    return mc, clock


def person(tid, cx=0.5, cy=0.5, w=P_W, h=P_H, conf=0.9):
    """Person track in det-frame pixels, from a normalised centre."""
    x1, x2 = (cx - w / 2) * FRAME_W, (cx + w / 2) * FRAME_W
    y1, y2 = (cy - h / 2) * FRAME_H, (cy + h / 2) * FRAME_H
    return {"id": tid, "box": (x1, y1, x2, y2),
            "centroid": ((x1 + x2) / 2, (y1 + y2) / 2), "conf": conf}


def at_body(p, frac_y, dx=0.0, conf=0.6, w=PH_W, h=PH_H):
    """A phone detection positioned by fraction DOWN the person's box.

    frac_y 0.0 = top of head, 0.45 ~ chest/waist (in hand), 1.0 = feet (on the floor).
    `dx` shifts it sideways as a fraction of the person's box width."""
    x1, y1, x2, y2 = p["box"]
    pw, ph = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2 + dx * pw
    cy = y1 + frac_y * ph
    hw, hh = w * FRAME_W / 2, h * FRAME_H / 2
    return {"box": (cx - hw, cy - hh, cx + hw, cy + hh), "conf": conf, "source": "crop"}


def run(mc, clock, frames):
    """frames: list of (tracks, detections). Returns all emitted events."""
    events = []
    for tracks, dets in frames:
        evs, _ = mc.process_phones(tracks, dets, SHAPE)
        events.extend(evs)
        clock.tick()
    return events


# A hand is never perfectly still: this drift spans ~15 px for the default person size,
# which is what distinguishes a phone in use from an object lying on a desk.
HAND_DRIFT = (-0.06, 0.0, 0.06)


def holding(p, n, still=False, **kw):
    """n frames of one person holding a phone.

    `still=True` pins it to one pixel position — a phone lying on a desk, not one being
    used. Real hands drift, and the module relies on that."""
    frames = []
    for i in range(n):
        dx = 0.0 if still else HAND_DRIFT[i % len(HAND_DRIFT)]
        fy = 0.45 if still else 0.45 + 0.01 * (i % 2)
        frames.append(([p], [at_body(p, fy, dx=dx, **kw)]))
    return frames


def state_of(mc, tid):
    st = mc.tracks.get(tid)
    return st.state if st else None


# ══════════════════════════════════════════════════════════════════════════════
# association
# ══════════════════════════════════════════════════════════════════════════════

def test_01_phone_clearly_held_by_one_person():
    mc, clock = make_consumer()
    p = person(1)
    events = run(mc, clock, holding(p, 5))
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "phone_use"
    assert ev["data"]["track"] == 1
    assert state_of(mc, 1) == CONFIRMED


def test_02_phone_lying_on_a_table_is_not_phone_use():
    """Below the hand band — a phone on a desk in front of a person, or on the floor."""
    mc, clock = make_consumer()
    p = person(1)
    frames = [([p], [at_body(p, 0.98)]) for _ in range(8)]
    assert run(mc, clock, frames) == []
    assert mc.metrics.reject_reasons.get("phone_outside_upper_body", 0) >= 1


def test_03_phone_outside_the_upper_body_region_is_rejected():
    """Far to the side, past the sideways hand pad."""
    mc, clock = make_consumer()
    p = person(1)
    frames = [([p], [at_body(p, 0.45, dx=1.4)]) for _ in range(8)]
    assert run(mc, clock, frames) == []


def test_04_phone_between_two_people_goes_to_the_better_match():
    """Both are candidates; only the one whose hand region actually contains it wins."""
    mc, clock = make_consumer()
    a, b = person(1, cx=0.40), person(2, cx=0.62)
    events = run(mc, clock,                     # squarely in A's hand region
                 [([a, b], [at_body(a, 0.45, dx=HAND_DRIFT[i % 3])]) for i in range(5)])
    assert [e["data"]["track"] for e in events] == [1]
    assert state_of(mc, 2) in (NO_PHONE, None)


def test_05_a_phone_is_never_assigned_to_two_people():
    mc, clock = make_consumer()
    a, b = person(1, cx=0.47), person(2, cx=0.53)     # heavily overlapping boxes
    events = run(mc, clock, [([a, b], [at_body(a, 0.45, dx=HAND_DRIFT[i % 3])])
                             for i in range(6)])
    assert len({e["data"]["track"] for e in events}) <= 1


def test_06_oversized_box_is_not_a_phone_in_a_hand():
    """A detection occupying a large share of the person is a foreground object."""
    mc, clock = make_consumer()
    p = person(1)
    frames = [([p], [at_body(p, 0.45, w=0.09, h=0.20)]) for _ in range(8)]
    assert run(mc, clock, frames) == []


def test_07_detection_with_nobody_near_is_unassociated():
    mc, clock = make_consumer()
    p = person(1, cx=0.10)
    far = at_body(person(99, cx=0.90), 0.45)
    frames = [([p], [far]) for _ in range(6)]
    assert run(mc, clock, frames) == []
    assert mc.metrics.detections_unassociated >= 1


# ══════════════════════════════════════════════════════════════════════════════
# temporal confirmation and hysteresis
# ══════════════════════════════════════════════════════════════════════════════

def test_08_single_frame_detection_noise_produces_no_event():
    mc, clock = make_consumer()
    p = person(1)
    frames = [([p], [at_body(p, 0.45)])] + [([p], []) for _ in range(8)]
    assert run(mc, clock, frames) == []
    assert state_of(mc, 1) == NO_PHONE
    assert mc.metrics.temporal_rejections >= 1


def test_09_intermittent_detections_still_confirm():
    """Real detections flicker. The default is 2 hits inside a 5-evaluation window,
    chosen from a threshold sweep over a real capture: the medium lane measured 2.2
    evaluations/second, so 3 hits demanded ~2 s of continuous detection and texting is
    briefer than that."""
    mc, clock = make_consumer()
    p = person(1)
    pattern = [1, 0, 1]
    frames = [([p], [at_body(p, 0.45, dx=HAND_DRIFT[i % 3])] if hit else [])
              for i, hit in enumerate(pattern)]
    events = run(mc, clock, frames)
    assert len(events) == 1
    assert events[0]["data"]["evidence"] == "2/3"


def test_10_one_hit_is_not_enough():
    """A single detection must never alert, whatever confirm_hits is set to."""
    mc, clock = make_consumer()
    p = person(1)
    pattern = [1, 0, 0, 0, 0]
    frames = [([p], [at_body(p, 0.45, dx=HAND_DRIFT[i % 3])] if hit else [])
              for i, hit in enumerate(pattern)]
    assert run(mc, clock, frames) == []


def test_10b_the_confirmation_requirement_is_tunable_per_camera():
    """A noisy camera can demand more evidence without changing anyone else."""
    p = person(1)
    pattern = [1, 0, 1, 0, 0]
    frames = [([p], [at_body(p, 0.45, dx=HAND_DRIFT[i % 3])] if hit else [])
              for i, hit in enumerate(pattern)]
    strict, clock = make_consumer(confirm_hits=3)
    assert run(strict, clock, frames) == []


def test_11_long_phone_use_produces_exactly_one_event():
    mc, clock = make_consumer()
    p = person(1)
    events = run(mc, clock, holding(p, 60))       # 12 s at 5 fps
    assert len(events) == 1
    assert state_of(mc, 1) == CONFIRMED


def test_12_putting_the_phone_away_releases_the_state():
    mc, clock = make_consumer()
    p = person(1)
    run(mc, clock, holding(p, 5))
    assert state_of(mc, 1) == CONFIRMED
    run(mc, clock, [([p], []) for _ in range(4)])
    assert state_of(mc, 1) == RELEASED
    assert mc.metrics.phone_released == 1


def test_13_brief_dropout_does_not_release():
    """Hysteresis: losing the phone for a frame or two must not end the episode."""
    mc, clock = make_consumer()
    p = person(1)
    run(mc, clock, holding(p, 5))
    run(mc, clock, [([p], []) for _ in range(3)])     # release_misses is 4
    assert state_of(mc, 1) == CONFIRMED


def test_14_phone_disappears_and_reappears():
    mc, clock = make_consumer(cooldown=0.0)
    p = person(1)
    run(mc, clock, holding(p, 5))
    run(mc, clock, [([p], []) for _ in range(6)])
    assert state_of(mc, 1) == RELEASED
    events = run(mc, clock, holding(p, 5))
    assert len(events) == 1, "a fresh episode after release should alert again"
    assert state_of(mc, 1) == CONFIRMED


def test_15_cooldown_suppresses_a_rapid_second_episode():
    mc, clock = make_consumer(cooldown=60.0)
    p = person(1)
    run(mc, clock, holding(p, 5))
    run(mc, clock, [([p], []) for _ in range(6)])
    events = run(mc, clock, holding(p, 5))
    assert events == []
    assert mc.metrics.duplicate_events_prevented >= 1


# ══════════════════════════════════════════════════════════════════════════════
# multiple people, tracks and identity
# ══════════════════════════════════════════════════════════════════════════════

def test_16_two_people_using_phones_simultaneously():
    mc, clock = make_consumer()
    a, b = person(1, cx=0.25), person(2, cx=0.75)
    frames = [([a, b], [at_body(a, 0.45, dx=HAND_DRIFT[i % 3]),
                        at_body(b, 0.45, dx=HAND_DRIFT[(i + 1) % 3])])
              for i in range(5)]
    events = run(mc, clock, frames)
    assert sorted(e["data"]["track"] for e in events) == [1, 2]
    assert state_of(mc, 1) == CONFIRMED and state_of(mc, 2) == CONFIRMED


def test_17_state_is_per_track_not_per_camera():
    mc, clock = make_consumer()
    a, b, c = person(1, cx=0.20), person(2, cx=0.50), person(3, cx=0.80)
    frames = [([a, b, c], [at_body(a, 0.45, dx=HAND_DRIFT[i % 3]),
                           at_body(c, 0.45, dx=HAND_DRIFT[(i + 1) % 3])])
              for i in range(5)]
    events = run(mc, clock, frames)
    assert sorted(e["data"]["track"] for e in events) == [1, 3]
    assert state_of(mc, 2) in (NO_PHONE, None), "the middle person must stay clean"
    assert mc.confirmed_tracks() == {1, 3}


def test_18_person_with_no_phone_never_fires():
    mc, clock = make_consumer()
    p = person(1)
    assert run(mc, clock, [([p], []) for _ in range(20)]) == []
    assert state_of(mc, 1) == NO_PHONE


def test_19_two_nearby_people_one_phone():
    mc, clock = make_consumer()
    a, b = person(1, cx=0.44), person(2, cx=0.56)
    frames = [([a, b], [at_body(b, 0.45, dx=HAND_DRIFT[i % 3])]) for i in range(5)]
    events = run(mc, clock, frames)
    assert [e["data"]["track"] for e in events] == [2]


def test_20_works_without_any_face_recognition():
    """No faces are ever supplied to this consumer — identity is additive, never a
    prerequisite. Somebody facing away must still be eligible."""
    mc, clock = make_consumer()
    p = person(1)
    events = run(mc, clock, holding(p, 5))
    assert len(events) == 1
    assert "photo_base" not in events[0], "identity is attached later, by perception"


def test_21_track_temporarily_lost_then_returns():
    mc, clock = make_consumer(state_ttl=10.0)
    p = person(1)
    run(mc, clock, holding(p, 5))
    run(mc, clock, [([], []) for _ in range(3)])      # person not detected at all
    assert state_of(mc, 1) == CONFIRMED, "state must survive a short detection gap"


def test_22_state_is_purged_after_the_ttl():
    mc, clock = make_consumer(state_ttl=1.0)
    p = person(1)
    run(mc, clock, holding(p, 5))
    run(mc, clock, [([], []) for _ in range(12)])
    assert mc.tracks == {}


def test_23_multiple_phones_in_the_scene():
    mc, clock = make_consumer()
    a, b = person(1, cx=0.25), person(2, cx=0.75)
    stray = at_body(person(98, cx=0.50, cy=0.95), 0.45)     # a phone on a low table
    frames = [([a, b], [at_body(a, 0.45, dx=HAND_DRIFT[i % 3]),
                        at_body(b, 0.45, dx=HAND_DRIFT[(i + 1) % 3]), stray])
              for i in range(5)]
    events = run(mc, clock, frames)
    assert sorted(e["data"]["track"] for e in events) == [1, 2]


# ══════════════════════════════════════════════════════════════════════════════
# small / occluded / camera-specific geometry
# ══════════════════════════════════════════════════════════════════════════════

def test_24_distant_small_person_still_associates():
    """Camera 50's median person is only ~61 px wide; association must not assume a
    large box."""
    mc, clock = make_consumer()
    p = person(1, cx=0.5, w=0.048, h=0.245)          # ~61 x 176 px
    events = run(mc, clock, [([p], [at_body(p, 0.45, dx=HAND_DRIFT[i % 3],
                                            w=0.009, h=0.016)])
                             for i in range(5)])
    assert len(events) == 1


def test_25_partial_occlusion_narrows_the_person_box():
    mc, clock = make_consumer()
    frames = []
    for i in range(6):
        occluded = 2 <= i <= 4
        p = person(1, w=P_W * (0.5 if occluded else 1.0), conf=0.45 if occluded else 0.9)
        frames.append(([p], [at_body(p, 0.45, dx=HAND_DRIFT[i % 3])]))
    assert len(run(mc, clock, frames)) == 1


def test_26_association_geometry_is_camera_configurable():
    """A camera looking down a corridor may need a different hand band. The default
    rejects a phone at 0.85 down the box; a camera configured for it accepts."""
    p = person(1)
    mc, clock = make_consumer()
    assert run(mc, clock, [([p], [at_body(p, 0.85, dx=HAND_DRIFT[i % 3])])
                           for i in range(6)]) == []

    mc2, clock2 = make_consumer(hand_bottom=0.95)
    assert len(run(mc2, clock2, [([p], [at_body(p, 0.85, dx=HAND_DRIFT[i % 3])])
                                 for i in range(6)])) == 1


def test_27_confirmation_thresholds_are_configurable():
    p = person(1)
    mc, clock = make_consumer(confirm_hits=1, confirm_window=1)
    assert len(run(mc, clock, holding(p, 1))) == 1

    mc2, clock2 = make_consumer(confirm_hits=8, confirm_window=10)
    assert run(mc2, clock2, holding(p, 5)) == []


def test_28_association_threshold_is_configurable():
    p = person(1)
    edge = [([p], [at_body(p, 0.45, dx=0.55 + 0.065 * (i % 3))]) for i in range(6)]
    mc, clock = make_consumer(association_min=0.9)
    assert run(mc, clock, edge) == []

    mc2, clock2 = make_consumer(association_min=0.05)
    assert len(run(mc2, clock2, edge)) == 1


# ══════════════════════════════════════════════════════════════════════════════
# event contract and confidence
# ══════════════════════════════════════════════════════════════════════════════

def test_29_event_contract():
    """module/event_type are attached by perception; the consumer owns the payload."""
    mc, clock = make_consumer()
    p = person(7)
    ev = run(mc, clock, holding(p, 5))[0]
    assert ev["event_type"] == "phone_use"
    assert ev["snapshot"] is True
    d = ev["data"]
    assert d["track"] == 7
    for key in ("phone_confidence", "association_confidence", "confidence",
                "evidence", "duration"):
        assert key in d, f"missing {key}"
    assert 0.0 <= d["confidence"] <= 1.0


def test_30_confidence_combines_detector_and_association():
    """Not detector confidence alone — and the components are reported separately so
    the number is never opaque."""
    mc, clock = make_consumer()
    p = person(1)
    ev = run(mc, clock, holding(p, 5, conf=0.80))[0]
    d = ev["data"]
    assert d["phone_confidence"] == pytest.approx(0.80, abs=1e-6)
    assert 0.0 < d["association_confidence"] <= 1.0
    assert d["confidence"] == pytest.approx(
        d["phone_confidence"] * d["association_confidence"], abs=1e-3)
    # A phone squarely in the hand region associates perfectly, so the composite equals
    # the detector confidence. A marginal association must discount it.
    assert d["association_confidence"] == pytest.approx(1.0, abs=1e-6)

    mc2, clock2 = make_consumer()
    edge = [([p], [at_body(p, 0.45, dx=0.51 + 0.055 * (i % 3), conf=0.80)])
            for i in range(5)]
    d2 = run(mc2, clock2, edge)[0]["data"]
    assert d2["association_confidence"] < 1.0
    assert d2["confidence"] < d["confidence"]


def test_31_low_detector_confidence_yields_a_lower_event_confidence():
    mc, clock = make_consumer()
    p = person(1)
    hi = run(*make_consumer(), holding(p, 5, conf=0.90))[0]["data"]["confidence"]
    lo = run(*make_consumer(), holding(p, 5, conf=0.45))[0]["data"]["confidence"]
    assert lo < hi


def test_32_the_phone_label_marks_only_confirmed_tracks():
    mc, clock = make_consumer()
    a, b = person(1, cx=0.25), person(2, cx=0.75)
    for i in range(5):
        _, overlays = mc.process_phones([a, b],
                                        [at_body(a, 0.45, dx=HAND_DRIFT[i % 3])], SHAPE)
        clock.tick()
    # The label now carries the numbers the decision was made from
    # ("PHONE USE  assoc 0.87"), so match the marker rather than the whole string:
    # an alert screenshot has to say WHY it fired, not just that it did.
    def phone_labels(items):
        return [o for o in items if str(o.get("label") or "").startswith("PHONE USE")]

    assert len(phone_labels(overlays)) == 1
    assert len(phone_labels(mc.current_overlays([a, b], SHAPE))) == 1


def test_33_the_search_region_is_drawn_whenever_the_module_runs():
    """The phone module's equivalent of the head-count line and band: without an
    on-screen mark there is no way to tell it is running, or where it is looking."""
    mc, clock = make_consumer()
    a, b = person(1, cx=0.25), person(2, cx=0.75)
    _, overlays = mc.process_phones([a, b], [], SHAPE)      # no phone anywhere

    regions = [o for o in overlays if o.get("color") == "accent-soft"
               and o["kind"] == "box"]
    assert len(regions) == 2, "one search region per tracked person"
    assert [o.get("label") for o in regions] == ["PHONE AREA", None]
    assert any(o["kind"] == "label" and o["text"] == "PHONE MONITORING"
               for o in overlays), "a corner mark, so an empty scene still shows it is on"


def test_34_the_search_region_matches_the_association_band():
    """What is drawn must be what is actually searched, or it is worse than useless
    for tuning."""
    mc, clock = make_consumer(hand_top=0.10, hand_bottom=0.60, hand_pad=0.30)
    p = person(1, cx=0.5)
    _, overlays = mc.process_phones([p], [], SHAPE)
    region = next(o for o in overlays if o.get("label") == "PHONE AREA")

    x1, y1, x2, y2 = p["box"]
    bw, bh = x2 - x1, y2 - y1
    rx1, ry1, rx2, ry2 = region["box"]
    assert rx1 == pytest.approx((x1 - bw * 0.30) / FRAME_W, abs=1e-6)
    assert rx2 == pytest.approx((x2 + bw * 0.30) / FRAME_W, abs=1e-6)
    assert ry1 == pytest.approx((y1 + bh * 0.10) / FRAME_H, abs=1e-6)
    assert ry2 == pytest.approx((y1 + bh * 0.60) / FRAME_H, abs=1e-6)


def test_35_the_search_region_can_be_switched_off():
    for value in (False, "false", "0", "off", "no"):
        mc, clock = make_consumer(show_region=value)
        _, overlays = mc.process_phones([person(1)], [], SHAPE)
        assert overlays == [], f"show_region={value!r} should draw nothing"


def test_36_base_process_is_inert():
    """The generic consumer path must not double-drive the module — phone detections
    arrive through process_phones, like fire/smoke uses process_detections."""
    mc, _ = make_consumer()
    assert mc.process([person(1)], [], SHAPE) == ([], [])


# ══════════════════════════════════════════════════════════════════════════════
# static objects — the office false-positive case
# ══════════════════════════════════════════════════════════════════════════════

def test_37_a_phone_held_still_IS_phone_use():
    """Texting, scrolling, reading and photographing are all done with the phone held
    still. An earlier build required the phone to MOVE before confirming, and a field
    capture showed it blocking 61 evaluations that had full evidence and perfect
    association. Holding still is the normal case, not the suspicious one."""
    mc, clock = make_consumer()
    p = person(1)
    events = run(mc, clock, holding(p, 6, still=True))
    assert len(events) == 1
    assert state_of(mc, 1) == CONFIRMED


def test_38_a_near_camera_person_holding_still_still_alerts():
    """The specific regression: motion was scaled to 10% of box width, so a person
    480 px wide had to move the phone 48 px to be believed."""
    mc, clock = make_consumer()
    big = person(1, cx=0.5, w=0.375, h=0.9)          # ~480 px wide, near the camera
    assert len(run(mc, clock, holding(big, 6, still=True))) == 1


def test_39_motion_requirement_scales_with_apparent_size():
    """When motion IS enabled, it must scale with apparent size: a person across the
    room moves their hand through far fewer pixels than one near the camera."""
    far = person(1, cx=0.5, w=0.048, h=0.245)          # ~61 px wide, as on camera 50
    mc, clock = make_consumer(min_motion_frac=0.10, min_motion_px=6)
    assert len(run(mc, clock, holding(far, 6, w=0.009, h=0.016))) == 1


def test_40_an_address_that_serves_several_people_is_furniture():
    """A desk object outlives the people near it. One phone cannot be in two people's
    hands, so an address credited to several tracks is a fixture."""
    mc, clock = make_consumer(fps=5.0, static_hits=6, static_window=1.0,
                              static_min_tracks=3)
    frames = []
    for i in range(18):
        p = person(1 + i // 6, cx=0.5)                 # the seat changes hands twice
        frames.append(([p], [at_body(p, 0.45)]))
    run(mc, clock, frames)
    assert mc.metrics.reject_reasons.get("static_object", 0) >= 1


def test_41_static_map_does_not_suppress_one_person_using_a_phone():
    """The counterpart: a single person on their phone must never be mistaken for
    furniture, however long they hold it."""
    mc, clock = make_consumer(static_hits=4, static_window=1.0, static_min_tracks=3)
    p = person(1)
    events = run(mc, clock, holding(p, 30, still=True))
    assert len(events) == 1
    assert mc.metrics.reject_reasons.get("static_object", 0) == 0


def test_42_an_address_held_far_too_long_is_furniture_even_for_one_track():
    """Nobody holds a phone at one pixel address for four minutes."""
    mc, clock = make_consumer(fps=5.0, static_hits=6, static_window=1.0,
                              static_min_tracks=99, static_hard_window=3.0)
    p = person(1)
    run(mc, clock, holding(p, 40, still=True))
    assert mc.metrics.reject_reasons.get("static_object", 0) >= 1


def test_43_motion_filter_is_available_but_off_by_default():
    """It stays configurable for a site that genuinely wants it, but a still phone must
    confirm out of the box."""
    from backend.services.analytics import mobile as mob
    assert mob.DEF_MIN_MOTION_FRAC == 0 and mob.DEF_MIN_MOTION_PX == 0

    on, clock = make_consumer(min_motion_px=40, min_motion_frac=0)
    p = person(1)
    assert run(on, clock, holding(p, 8, still=True)) == []
    assert on.metrics.reject_reasons.get("phone_did_not_move", 0) >= 1


def test_44_the_event_reports_how_far_the_phone_moved():
    mc, clock = make_consumer()
    p = person(1)
    d = run(mc, clock, holding(p, 6))[0]["data"]
    assert d["motion_px"] > 0, "the evidence for 'in use' must be in the payload"


# ══════════════════════════════════════════════════════════════════════════════
# field regressions: glasses, the crown of the head, and the threshold bug
# ══════════════════════════════════════════════════════════════════════════════

def test_45_the_acting_threshold_defaults_to_the_detectors_floor():
    """The bug that made the system worse while it was being debugged.

    With diagnostics on, the detector deliberately runs BELOW its configured floor so
    near-misses appear in the log; the consumer is what restores the real threshold.
    Defaulting that to zero silently dropped the live floor from 0.35 to 0.15."""
    from backend.services.detectors.phone import CONF
    mc, clock = make_consumer()                       # no phone_conf in the config
    p = person(1)
    assert run(mc, clock, holding(p, 6, conf=CONF - 0.10)) == []
    assert mc.metrics.below_confidence >= 1

    mc2, clock2 = make_consumer()
    assert len(run(mc2, clock2, holding(p, 6, conf=CONF + 0.10))) == 1


def test_46_a_camera_may_still_lower_its_own_threshold():
    mc, clock = make_consumer(phone_conf=0.20)
    assert len(run(mc, clock, holding(person(1), 6, conf=0.25))) == 1


def test_47_spectacles_are_not_a_phone():
    """Reported from the field: a wide flat bar on someone's face read as a phone.
    Glasses, watch straps and keyboard edges are all long and thin; a phone is not."""
    mc, clock = make_consumer()
    p = person(1)
    # 3.4:1 — the shape of a pair of glasses, at chest height where a phone would be
    glasses = [([p], [at_body(p, 0.45, dx=HAND_DRIFT[i % 3], w=0.034, h=0.010)])
               for i in range(8)]
    assert run(mc, clock, glasses) == []
    assert mc.metrics.reject_reasons.get("implausible_shape", 0) >= 1


def test_48_a_landscape_phone_is_still_a_phone():
    """Filming or photographing is held landscape, and is exactly what we want to
    catch — so the shape bound has to be generous, not merely portrait-only."""
    mc, clock = make_consumer()
    p = person(1)
    landscape = [([p], [at_body(p, 0.45, dx=HAND_DRIFT[i % 3], w=0.028, h=0.028)])
                 for i in range(6)]        # 36 x 20 px: a phone held sideways
    assert len(run(mc, clock, landscape)) == 1


def test_49_the_crown_of_the_head_is_outside_the_hand_band():
    """vy 0.00 admitted the face, and with it every pair of glasses."""
    mc, clock = make_consumer()
    p = person(1)
    at_face = [([p], [at_body(p, 0.03, dx=HAND_DRIFT[i % 3])]) for i in range(8)]
    assert run(mc, clock, at_face) == []


def test_50_a_phone_at_the_ear_still_counts():
    """A call is held below the crown, so excluding the face must not cost us calls."""
    mc, clock = make_consumer()
    p = person(1)
    at_ear = [([p], [at_body(p, 0.12, dx=HAND_DRIFT[i % 3])]) for i in range(6)]
    assert len(run(mc, clock, at_ear)) == 1


# ══════════════════════════════════════════════════════════════════════════════
# the evidence gate: confidence WEIGHED BY association, not a flat floor
# ══════════════════════════════════════════════════════════════════════════════

def test_51_a_weak_detection_in_exactly_the_right_place_is_believed():
    """Calls, voice notes and covert photography were being missed: the phone is small,
    angled and half-hidden, so the detector scores it 0.20-0.33 — under any flat floor
    high enough to keep furniture out. Placement is the other half of the evidence."""
    mc, clock = make_consumer()
    p = person(1)
    assert len(run(mc, clock, holding(p, 6, conf=0.24))) == 1


def test_52_a_confident_detection_in_the_wrong_place_is_not():
    """The converse, and the reason this is a product rather than two thresholds."""
    mc, clock = make_consumer()
    p = person(1)
    # well outside the body: association collapses, so confidence cannot rescue it
    edge = [([p], [at_body(p, 0.45, dx=0.66 + 0.01 * (i % 3), conf=0.85)])
            for i in range(8)]
    assert run(mc, clock, edge) == []


def test_53_pure_noise_is_still_discarded_before_any_geometry():
    mc, clock = make_consumer()
    p = person(1)
    assert run(mc, clock, holding(p, 6, conf=0.10)) == []
    assert mc.metrics.below_confidence >= 1


def test_54_the_gate_is_the_product_of_the_two_signals():
    from backend.services.analytics import mobile as mob
    mc, clock = make_consumer()
    p = person(1)
    # 0.24 x 1.0 = 0.24, above the 0.20 default
    assert len(run(mc, clock, holding(p, 6, conf=0.24))) == 1
    # a camera demanding more evidence rejects the same footage
    strict, clock2 = make_consumer(min_combined=0.40)
    assert run(strict, clock2, holding(p, 6, conf=0.24)) == []
    assert strict.metrics.reject_reasons.get("weak_evidence", 0) >= 1
    assert mob.DEF_MIN_COMBINED == 0.20


def test_55_a_phone_at_the_ear_survives_a_weak_score():
    """Phone calls: held high, partly hidden by the head, and scored poorly."""
    mc, clock = make_consumer()
    p = person(1)
    at_ear = [([p], [at_body(p, 0.12, dx=HAND_DRIFT[i % 3], conf=0.26)])
              for i in range(6)]
    assert len(run(mc, clock, at_ear)) == 1

# ── The minimum-size floor, sized from a real capture ────────────────────────────
# Every confirmed phone_use event in logs/phone_debug.log on 2026-08-28, by the short
# side of the detection that confirmed it. 33 events, four cameras.
FIELD_CONFIRMED_PX = [
    4.1, 4.5, 4.8, 6.6,                     # physically impossible - the false positives
    8.1, 8.2, 8.6, 11.6, 12.1, 12.6, 12.8, 12.9, 14.5, 14.7, 14.8, 16.1, 17.0, 17.4,
    17.6, 18.0, 18.0, 18.1, 18.7, 20.0, 20.9, 21.6, 22.4, 27.0, 30.6, 45.2,
    63.0, 63.4, 64.4,                       # camera 53, people close to the lens
]


def test_45_the_size_floor_removes_only_impossible_detections():
    """The default must cut the sub-resolution tail WITHOUT touching real phones.

    The detector's finest head is stride 8 and bench_phone_scale.py puts the collapse
    point near 16 px, so a 4 px box cannot be a phone. But camera 50's genuine phones
    are only ~11-12 px, which is why an earlier 12 px floor was unusable - it cut 8 of
    the 33 real confirmations. 8 px is the value that separates them."""
    from backend.services.analytics.mobile import DEF_MIN_PX
    removed = [px for px in FIELD_CONFIRMED_PX if px < DEF_MIN_PX]
    kept = [px for px in FIELD_CONFIRMED_PX if px >= DEF_MIN_PX]
    assert removed == [4.1, 4.5, 4.8, 6.6], (
        f"the default floor ({DEF_MIN_PX}px) no longer cuts exactly the impossible "
        f"detections - it removes {removed}")
    assert min(kept) >= 8.0
    assert len(kept) == 29, "every plausible confirmation must survive"


def test_46_the_floor_is_per_camera():
    """A camera whose real phones are 60px+ can run a much higher floor than a wide view
    whose real phones are 11px. One global number cannot serve both, which is why this
    is a per-camera setting rather than only an environment variable."""
    from backend.services.modules.registry import MODULES
    assert "min_px" in MODULES["mobile"]["config"]
    mc, clock = make_consumer(min_px=40)
    # A 23x24px phone is real on a wide camera but below a close-up camera's floor.
    p = person(1, cx=0.5)
    assert run(mc, clock, holding(p, 6)) == [], "the per-camera floor must be honoured"


# ── A phone PRINTED on a t-shirt ─────────────────────────────────────────────────
# The hardest false positive seen in the field (screenshot, 2026-08-28 19:16): confirmed
# at conf 0.55, aspect 0.65, area_ratio 0.0149, association 1.00 - every one of those
# indistinguishable from the genuine detections around it, because a printed phone IS
# phone-shaped and phone-sized and does sit in the hand band.
#
# Neither existing motion signal sees it. `motion()` measures travel across the FRAME,
# and a print on a walking person travels exactly as far as the person. The static map
# keys on frame ADDRESS, which a moving person never holds. What IS different is the
# offset INSIDE the person's box, which for a print never changes.

def printed_on_shirt(p, n, **kw):
    """n frames of a person walking with a phone PRINTED on their chest.

    The person moves across the frame; the print keeps exactly the same offset within
    their box, because it is attached to them."""
    frames = []
    for i in range(n):
        moved = person(p["id"], cx=0.3 + 0.03 * i)
        frames.append(([moved], [at_body(moved, 0.45, dx=0.0, **kw)]))
    return frames


def test_56_a_print_and_a_held_phone_differ_in_body_relative_drift():
    """The measurement that separates them, before any threshold is chosen.

    A held phone wanders inside the person's box; a print cannot. If this ever stops
    holding, no threshold on `body_drift` can work and the approach is wrong."""
    mc, clock = make_consumer()
    p = person(1)
    run(mc, clock, printed_on_shirt(p, 8))
    printed = mc.tracks[1].body_drift()

    mc2, clock2 = make_consumer()
    run(mc2, clock2, holding(person(1), 8))          # HAND_DRIFT moves it in the hand
    held = mc2.tracks[1].body_drift()

    assert printed < held, (
        f"a print must be more pinned than a held phone: printed={printed:.4f} "
        f"held={held:.4f}")
    assert printed < 0.01, f"a print barely moves inside the box, got {printed:.4f}"


def test_57_the_body_drift_gate_is_off_until_calibrated():
    """Shipped OFF. The mechanism is measured and logged, but the threshold has not been
    set against real footage - and a guessed one suppresses real alerts, which is exactly
    how the size floor went wrong twice before it was measured from a capture."""
    from backend.services.analytics.mobile import DEF_BODY_DRIFT_MIN
    assert DEF_BODY_DRIFT_MIN == 0.0
    mc, clock = make_consumer()
    assert len(run(mc, clock, printed_on_shirt(person(1), 8))) == 1,         "with the gate off the print still alerts - that is the current behaviour"


def test_58_enabling_the_gate_rejects_the_print_but_keeps_the_held_phone():
    """And when it IS enabled, it must cut the print without touching real use."""
    mc, clock = make_consumer(body_drift_min=0.02, body_drift_obs=4)
    assert run(mc, clock, printed_on_shirt(person(1), 10)) == [],         "a phone pinned to the torso must be rejected once the gate is on"

    mc2, clock2 = make_consumer(body_drift_min=0.02, body_drift_obs=4)
    assert len(run(mc2, clock2, holding(person(1), 10))) == 1,         "a genuinely held phone must survive the same gate"


def test_59_track_end_reports_the_peak_drift_not_the_post_reset_zero():
    """TRACK_END is where an operator reads what a genuine phone's drift looks like.

    It reported 0.0000 for every released track, because the per-episode reset wipes
    body_ext before the line is written - so a capture could never show the population
    the threshold needs to sit above. The peak is kept across the whole track, the way
    best_conf and best_association already are."""
    mc, clock = make_consumer()
    p = person(1)
    run(mc, clock, holding(p, 8))
    st = mc.tracks[1]
    assert st.best_body_drift > 0, "a held phone must record non-zero peak drift"
    # Survives the episode reset that RELEASE performs.
    st.body_ext, st.body_obs = None, 0
    assert st.best_body_drift > 0, "the peak must outlive the per-episode reset"

