"""
Phase 1 cadence wiring tests.

Verifies the two-lane enqueue in camera_manager without needing a GPU, a camera or a DB:
a real CameraInstance is driven with a synthetic frame and a fake clock, and the tasks it
would have pushed to the perception child are captured.

What must hold:
  * a camera WITHOUT a fast module behaves exactly as before (one lane, ~2 fps, every
    task carrying the full-resolution JPEG)
  * a camera WITH head-count gets ~10 fps of tracking frames, of which only the ~2 fps
    slow frames carry a JPEG payload
  * the fast-lane module allow-list agrees between the producer (camera_manager) and the
    consumer (perception), because they are declared in two places
"""

from __future__ import annotations

import numpy as np
import pytest

import importlib

from backend.services import perception

# NOTE: backend/services/__init__.py re-exports the CameraManager *instance* under the
# name `camera_manager`, which shadows the module of the same name on the package. Import
# the module explicitly so monkeypatching reaches module-level globals.
cm = importlib.import_module("backend.services.camera_manager")


class FakeTime:
    """Controllable stand-in for the `time` module inside camera_manager."""

    def __init__(self):
        self.t = 10_000.0

    def monotonic(self):
        return self.t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def advance(self, s):
        self.t += s


@pytest.fixture
def captured(monkeypatch):
    tasks = []
    monkeypatch.setattr(perception, "enqueue", lambda t: tasks.append(t) or True)
    monkeypatch.setattr(perception, "queue_size", lambda cam_id=None: 0)
    return tasks


def drive(instance, fake, seconds, step=0.02):
    """Run _process_frame at `step` resolution for `seconds` of simulated time."""
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    n = int(seconds / step)
    for _ in range(n):
        instance._process_frame(frame)
        fake.advance(step)


def make_instance(monkeypatch, modules):
    fake = FakeTime()
    monkeypatch.setattr(cm, "time", fake)
    inst = cm.CameraInstance("rtsp://example/stream", frame_rate=10, cam_id=1,
                             modules=modules)
    return inst, fake


ATTENDANCE_ONLY = [{"module": "attendance", "enabled": True, "role": "both"}]
WITH_HEADCOUNT = [{"module": "attendance", "enabled": True, "role": "both"},
                  {"module": "headcount", "enabled": True,
                   "line": [[0.0, 0.5], [1.0, 0.5]]}]


def test_camera_without_fast_module_is_unchanged(monkeypatch, captured):
    inst, fake = make_instance(monkeypatch, ATTENDANCE_ONLY)
    assert inst._fast_enabled() is False
    drive(inst, fake, seconds=2.0)

    assert all(t["fast"] is False for t in captured)
    # No lane pre-encodes a JPEG any more; every task instead identifies the frame it was
    # built from so the writer can encode that exact frame on demand. See the note in
    # test_fast_frames_carry_no_jpeg_payload.
    assert all(t["frame_buf"] is None for t in captured)
    assert all(t.get("frame_seq") is not None for t in captured)
    # 2 s at the historical 0.5 s interval
    assert 4 <= len(captured) <= 5, len(captured)


def test_headcount_camera_gets_the_fast_lane(monkeypatch, captured):
    inst, fake = make_instance(monkeypatch, WITH_HEADCOUNT)
    assert inst._fast_enabled() is True
    drive(inst, fake, seconds=2.0)

    slow = [t for t in captured if not t["fast"]]
    fast = [t for t in captured if t["fast"]]

    # ~10 fps overall, of which ~2 fps are full slow-lane frames
    assert 18 <= len(captured) <= 22, len(captured)
    assert 4 <= len(slow) <= 5, len(slow)
    assert len(fast) == len(captured) - len(slow)


def test_fast_frames_carry_no_jpeg_payload(monkeypatch, captured):
    inst, fake = make_instance(monkeypatch, WITH_HEADCOUNT)
    drive(inst, fake, seconds=2.0)

    # CONTRACT CHANGED (optimisation plan item 2): NO lane pre-encodes a JPEG now.
    #
    # The slow lane used to carry a full-resolution snapshot on every frame - two per
    # second per camera, ~18 ms each at 1080p - to serve an alert that fires a few times
    # an hour. Instead each task carries the ring sequence number of its source frame,
    # and the writer encodes THAT EXACT FRAME on demand only when an event needs it.
    #
    # The snapshot is still the frame the decision was made on; it is simply no longer
    # produced speculatively.
    for t in captured:
        assert t["frame_buf"] is None, "no lane should pre-encode a JPEG any more"
        assert t.get("frame_seq") is not None,             "every task must identify its source frame so a snapshot can be produced"
        assert t["frame"] is not None
        assert t["cam_id"] == 1


def test_slow_lane_wins_when_both_are_due(monkeypatch, captured):
    """A slow frame is a superset of a fast one, so when both fall due the frame must be
    emitted once, as a slow frame — never duplicated."""
    inst, fake = make_instance(monkeypatch, WITH_HEADCOUNT)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    inst._process_frame(frame)                 # both lanes due on the first call
    assert len(captured) == 1
    assert captured[0]["fast"] is False


def test_disabled_headcount_does_not_trigger_the_fast_lane(monkeypatch, captured):
    mods = [{"module": "headcount", "enabled": False, "line": [[0, 0.5], [1, 0.5]]}]
    inst, fake = make_instance(monkeypatch, mods)
    assert inst._fast_enabled() is False


def test_fast_module_allowlist_agrees_across_processes():
    """camera_manager decides what to send; perception decides what to run. If these
    drift, fast frames either do nothing or silently run expensive modules."""
    assert cm.FAST_MODULES == perception.FAST_MODULES


def test_fast_task_module_filter_matches_the_child():
    """Reproduces the child's filter: a fast task must reduce to fast modules only."""
    mods = [m for m in WITH_HEADCOUNT if m.get("enabled", True)]
    filtered = [m for m in mods if m.get("module") in perception.FAST_MODULES]
    assert [m["module"] for m in filtered] == ["headcount"]


def test_queue_backpressure_still_drops_frames(monkeypatch, captured):
    """The existing drop-on-full behaviour must survive the rewrite."""
    monkeypatch.setattr(perception, "queue_size",
                        lambda cam_id=None: perception.MAX_QUEUE)
    inst, fake = make_instance(monkeypatch, WITH_HEADCOUNT)
    drive(inst, fake, seconds=1.0)
    assert captured == []


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — the medium (phone) lane must not disturb the fast (head-count) lane
# ══════════════════════════════════════════════════════════════════════════════

PHONE_ONLY = [{"module": "mobile", "enabled": True}]
HEADCOUNT_AND_PHONE = [{"module": "headcount", "enabled": True,
                        "line": [[0.0, 0.5], [1.0, 0.5]]},
                       {"module": "mobile", "enabled": True}]


def lanes(tasks):
    from collections import Counter
    return Counter(t["lane"] for t in tasks)


def test_phone_only_camera_does_not_enter_the_headcount_fast_lane(monkeypatch, captured):
    """A phone camera must not be dragged to 10 fps person tracking it does not need."""
    inst, fake = make_instance(monkeypatch, PHONE_ONLY)
    assert inst._fast_enabled() is False
    assert inst._sub_slow_lanes() == ["medium"]
    drive(inst, fake, seconds=2.0)

    counts = lanes(captured)
    assert counts["fast"] == 0, "phone-only must never emit a fast-lane task"
    # Every medium AND slow frame evaluates phones (the slow lane is a superset), so
    # the phone cadence is ~5 fps plus the slow ticks that absorb a medium one.
    assert 10 <= counts["medium"] + counts["slow"] <= 14, counts
    assert all(t["fast"] is False for t in captured)


def test_phone_only_medium_frames_can_still_produce_a_snapshot(monkeypatch, captured):
    """phone_use events attach a snapshot, so the medium lane must still be ABLE to
    produce one — it just no longer pre-encodes it.

    What matters is that the task identifies its source frame, so the writer can encode
    that exact frame if an event fires. Pre-encoding one for every frame in case an event
    might fire is the waste this replaced."""
    inst, fake = make_instance(monkeypatch, PHONE_ONLY)
    drive(inst, fake, seconds=2.0)
    assert captured, "the fixture must produce tasks"
    for t in captured:
        assert t["frame_buf"] is None, "no speculative encode"
        assert t.get("frame_seq") is not None,             f"{t['lane']} lane cannot identify its frame, so no snapshot is possible"


def test_headcount_and_phone_run_both_cadences(monkeypatch, captured):
    inst, fake = make_instance(monkeypatch, HEADCOUNT_AND_PHONE)
    assert sorted(inst._sub_slow_lanes()) == ["fast", "medium"]
    drive(inst, fake, seconds=2.0)

    counts = lanes(captured)
    assert counts["slow"] >= 4                        # ~2 fps
    assert counts["medium"] >= 4                      # ~5 fps
    assert counts["fast"] >= 8                        # ~10 fps, minus absorbed lanes
    assert sum(counts.values()) >= 18


def test_each_lane_carries_only_its_own_modules(monkeypatch, captured):
    inst, fake = make_instance(monkeypatch, HEADCOUNT_AND_PHONE)
    drive(inst, fake, seconds=2.0)

    for t in captured:
        mods = {m["module"] for m in t["modules"]}
        if t["lane"] == "fast":
            assert mods == {"headcount"}, "the fast lane must not carry phone inference"
        elif t["lane"] == "medium":
            # medium fires when fast is due too, so it carries both
            assert mods <= {"headcount", "mobile"} and "mobile" in mods
        else:
            assert mods == {"headcount", "mobile"}, "the slow lane carries everything"


def test_headcount_only_camera_is_unaffected_by_phase_2(monkeypatch, captured):
    """Regression: the Phase 1 cadence must be byte-for-byte what it was."""
    inst, fake = make_instance(monkeypatch, WITH_HEADCOUNT)
    assert inst._sub_slow_lanes() == ["fast"]
    drive(inst, fake, seconds=2.0)

    counts = lanes(captured)
    assert counts["medium"] == 0, "no phone module means no medium lane"
    assert 18 <= sum(counts.values()) <= 22
    assert 4 <= counts["slow"] <= 5


def test_disabled_phone_module_does_not_open_the_medium_lane(monkeypatch, captured):
    mods = [{"module": "mobile", "enabled": False}]
    inst, fake = make_instance(monkeypatch, mods)
    assert inst._sub_slow_lanes() == []


def test_lane_module_allowlists_agree_across_processes():
    """camera_manager decides what to send; perception decides what to run."""
    assert cm.FAST_MODULES == perception.FAST_MODULES
    assert cm.MEDIUM_MODULES == perception.MEDIUM_MODULES
    assert cm.LANE_MODULES == perception.LANE_MODULES
