"""
Optimisation plan: the savings are real, and the AI path is untouched.

The user's constraint was explicit — the alerts are good and must not change. So these
tests are in two halves:

  1. the optimisations do what they claim
  2. nothing that reaches a detector has changed

Anything that COULD change detections is gated off by default, and there are tests
asserting those gates are off.

Run:  pytest tests/test_performance.py -q
"""

from __future__ import annotations

import sys

import numpy as np
import pytest


def cm():
    """The camera_manager MODULE. `from backend.services import camera_manager` yields
    the singleton — services/__init__ re-exports it under the same name."""
    __import__("backend.services.camera_manager")   # ensure it is loaded
    return sys.modules["backend.services.camera_manager"]


# ══════════════════════════════════════════════════════════════════════════════
# The gates that protect existing behaviour
# ══════════════════════════════════════════════════════════════════════════════

def test_01_decode_gating_is_off_by_default():
    """It changes WHICH frames reach the detectors — same rate, marginally fresher, but
    not the byte-identical frames today's code picks. On a system whose alerts are
    known-good that is not a change to make silently."""
    assert cm().DECODE_GATING is False


def test_02_opencv_threads_in_the_main_process_are_off_by_default():
    """Pinning threads was part of what made the native stack stable. 'Probably safe' is
    not good enough for a change that previously corrupted memory."""
    import os
    assert os.environ.get("MAIN_CV_THREADS", "0") == "0"


def test_03_the_stream_ceiling_is_off_by_default():
    assert cm().MAX_LIVE_STREAMS == 0


# ══════════════════════════════════════════════════════════════════════════════
# The AI path is untouched
# ══════════════════════════════════════════════════════════════════════════════

def test_04_detection_frame_width_is_unchanged():
    """The frame handed to the detectors must be exactly what it was."""
    m = cm()
    assert m.DET_WIDTH == 1280
    assert m.DET_WIDTH_FAST == 640


def test_05_lane_cadences_are_unchanged():
    m = cm()
    assert m.ENQUEUE_INTERVAL == 0.5
    assert m.TRACK_INTERVAL == 0.1
    assert m.PHONE_INTERVAL == 0.2


def test_06_the_broadcast_downscale_never_touches_the_detection_frame():
    """BROADCAST_WIDTH is the human-viewing path. If it could reach _process_frame's
    detection frame it would change what every model sees."""
    import inspect
    src = inspect.getsource(cm().CameraInstance._process_frame)
    assert "BROADCAST_WIDTH" not in src, \
        "the broadcast width must not appear anywhere in the detection path"


def test_07_module_assignment_and_lanes_are_as_specified():
    m = cm()
    # vehicle_count joined: a crossing needs the track to survive frame to frame, and a
    # vehicle moves faster than a person - if ByteTrack needs 10 fps for a walker it
    # needs it more here. See test_105 for why the vehicle backbone is safe on this lane.
    assert m.FAST_MODULES == {"headcount", "tripwire", "vehicle_count"}
    # `crowd` joined the medium lane on evidence: a capture at the slow lane's 1.9 fps
    # saw one person in 194 of 218 evaluations, while every time two people WERE both
    # seen they clustered. Clustering needs both people in the SAME frame, so it is far
    # more sampling-sensitive than a per-person rule. See camera_manager.MEDIUM_MODULES.
    assert m.MEDIUM_MODULES == {"mobile", "crowd"}
    # `unattended` joined for two reasons: like tamper it is useless if the camera is
    # only watched when somebody opens the live view, and it additionally holds two
    # background models per camera - an idle-out throws them away and forces a fresh
    # warm-up before anything can fire again.
    assert m.ALWAYS_ON_MODULES == {"tamper", "unattended"}


def test_07c_lane_is_a_per_camera_setting_not_a_global_one():
    """The registry value is a DEFAULT; the camera's own config overrides it.

    Cadence is an operator decision, because the right rate depends on the camera - a
    doorway where people are large and close needs far less sampling than a wide yard.
    Baking one lane into code made that the developer's call instead."""
    from backend.services.modules.registry import carried_by_lane, effective_lane, needs_lane
    m = cm()
    inst = m.CameraInstance.__new__(m.CameraInstance)

    inst.modules = [{"module": "crowd", "enabled": True}]
    assert effective_lane(inst.modules[0]) == "medium", "registry default"
    assert inst._lane_enabled("medium") and not inst._lane_enabled("fast")

    inst.modules = [{"module": "crowd", "enabled": True, "lane": "slow"}]
    assert not inst._lane_enabled("medium"), "an override back to slow must be honoured"

    inst.modules = [{"module": "crowd", "enabled": True, "lane": "fast"}]
    assert inst._lane_enabled("fast")

    # A typo must never silently promote a camera to 10 fps.
    assert effective_lane({"module": "crowd", "lane": "quick"}) == "medium"


def test_07d_both_filters_agree_on_the_per_camera_lane():
    """camera_manager schedules the lane; perception decides what that lane CARRIES.

    If one used the per-camera value and the other the global set, the lane would fire
    and its modules would be filtered straight back out - no exception, no log, just a
    module that silently never runs."""
    from backend.services.modules.registry import carried_by_lane
    m = cm()
    inst = m.CameraInstance.__new__(m.CameraInstance)
    for lane in ("slow", "medium", "fast"):
        cfg = {"module": "crowd", "enabled": True, "lane": lane}
        inst.modules = [cfg]
        for frame_lane in ("fast", "medium"):
            scheduled = inst._lane_enabled(frame_lane)
            if scheduled:
                assert carried_by_lane(cfg, frame_lane), (
                    f"lane={lane}: {frame_lane} frame is scheduled but would carry "
                    f"nothing - the module would never run")


def test_07b_the_two_copies_of_the_lane_sets_agree():
    """camera_manager decides which lane to schedule; perception decides which modules a
    lane's task may carry. If they disagree a lane fires and its modules are filtered
    out, which produces no error anywhere - just a module that silently never runs."""
    import backend.services.perception as pc
    m = cm()
    assert m.FAST_MODULES == pc.FAST_MODULES
    assert m.MEDIUM_MODULES == pc.MEDIUM_MODULES
    assert m.LANE_MODULES == pc.LANE_MODULES


# ══════════════════════════════════════════════════════════════════════════════
# The frame ring — snapshots stay exact
# ══════════════════════════════════════════════════════════════════════════════

class FakeInst:
    """Minimal stand-in exercising the real ring methods."""

    def __init__(self, cam_id=1):
        self.cam_id = cam_id
        self._ring = []
        self._ring_seq = 0
        self.frame = None
        self.stopped = False

    _push_frame = None      # bound below


@pytest.fixture
def inst():
    m = cm()
    i = m.CameraInstance.__new__(m.CameraInstance)
    i.cam_id = 4242
    i._ring = []
    i._ring_seq = 0
    i.frame = None
    i.stopped = False
    return i


def frame(v):
    return np.full((32, 48, 3), v, dtype=np.uint8)


def test_08_the_ring_returns_the_exact_frame_by_sequence(inst):
    """The whole point: the snapshot is the frame the DECISION was made on, not whatever
    the camera happens to show when the alert reaches the database."""
    seqs = []
    for v in (10, 20, 30):
        inst._push_frame(frame(v))
        seqs.append(inst._ring_seq)
    for seq, v in zip(seqs, (10, 20, 30)):
        got = inst.frame_by_seq(seq)
        assert got is not None and int(got[0, 0, 0]) == v


def test_09_the_ring_is_bounded(inst):
    m = cm()
    for v in range(m.FRAME_RING * 3):
        inst._push_frame(frame(v % 250))
    assert len(inst._ring) == m.FRAME_RING, "the ring must not grow without bound"


def test_10_an_aged_out_frame_returns_none_not_a_wrong_one(inst):
    """A stale frame silently substituted for the right one would be worse than none —
    the alert would carry evidence of a different moment."""
    m = cm()
    inst._push_frame(frame(1))
    first = inst._ring_seq
    for v in range(m.FRAME_RING + 5):
        inst._push_frame(frame(v % 250))
    assert inst.frame_by_seq(first) is None


def test_11_an_unknown_sequence_returns_none(inst):
    inst._push_frame(frame(7))
    assert inst.frame_by_seq(999999) is None
    assert inst.frame_by_seq(None) is None


def test_12_latest_frame_is_still_exposed(inst):
    """`self.frame` is what the AI loop and the tamper monitor read."""
    inst._push_frame(frame(99))
    assert inst.frame is not None and int(inst.frame[0, 0, 0]) == 99


def test_13_jpeg_for_seq_encodes_the_requested_frame(monkeypatch, inst):
    m = cm()
    inst._push_frame(frame(10))
    wanted = inst._ring_seq
    inst._push_frame(frame(200))                 # newer frame is NOT the one asked for
    monkeypatch.setitem(m.camera_manager.instances, "fake://ring", inst)

    buf = m.jpeg_for_seq(4242, wanted)
    assert buf and buf[:2] == b"\xff\xd8"
    import cv2
    decoded = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
    assert abs(int(decoded[16, 24, 0]) - 10) < 6, "must be the OLD frame, not the newest"


def test_14_jpeg_for_seq_returns_none_when_the_frame_has_gone(monkeypatch, inst):
    """The writer treats None as 'fall back', so it must not raise into the DB loop."""
    m = cm()
    monkeypatch.setitem(m.camera_manager.instances, "fake://ring2", inst)
    assert m.jpeg_for_seq(4242, 12345) is None
    assert m.jpeg_for_seq(999999, 1) is None


# ══════════════════════════════════════════════════════════════════════════════
# The stream ceiling
# ══════════════════════════════════════════════════════════════════════════════

def test_15_no_ceiling_means_always_available(monkeypatch):
    m = cm()
    monkeypatch.setattr(m, "MAX_LIVE_STREAMS", 0)
    assert m.stream_slot_available(1) is True


def test_16_a_ceiling_refuses_beyond_the_limit(monkeypatch):
    m = cm()
    monkeypatch.setattr(m, "MAX_LIVE_STREAMS", 2)
    monkeypatch.setattr(m, "live_stream_count", lambda: 2)
    monkeypatch.setattr(m.connection_manager, "has_clients", lambda _k: False)
    assert m.stream_slot_available(99) is False


def test_17_a_camera_already_being_watched_is_never_cut_off(monkeypatch):
    """The ceiling limits how many streams are STARTED; it must not interrupt one
    somebody is already watching."""
    m = cm()
    monkeypatch.setattr(m, "MAX_LIVE_STREAMS", 1)
    monkeypatch.setattr(m, "live_stream_count", lambda: 5)
    monkeypatch.setattr(m.connection_manager, "has_clients", lambda _k: True)
    assert m.stream_slot_available(7) is True


# ══════════════════════════════════════════════════════════════════════════════
# The performance log
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def perf_log(tmp_path, monkeypatch):
    from backend.services import perf_debug
    monkeypatch.setenv("PERF_DEBUG", "trace")
    monkeypatch.setenv("PERF_DEBUG_LOG", str(tmp_path / "perf.log"))
    monkeypatch.setattr(perf_debug, "SUMMARY_EVERY", 0.0)
    monkeypatch.setattr(perf_debug, "SAMPLE_EVERY", 1)
    perf_debug.reset()
    yield perf_debug, tmp_path / "perf.log"
    perf_debug.reset()


def test_18_stage_timings_are_recorded(perf_log):
    perf, path = perf_log
    for name in perf.STAGES:
        with perf.stage(5, name):
            pass
    t = perf.totals()["5"]
    assert all(t["counts"][s] == 1 for s in perf.STAGES)
    assert "event=OP" in path.read_text(encoding="utf-8")


def test_19_a_summary_reports_each_stage(perf_log):
    perf, path = perf_log
    for _ in range(3):
        with perf.stage(6, "decode"):
            pass
    with perf.stage(6, "broadcast"):
        pass
    perf.summarise(6)
    text = path.read_text(encoding="utf-8")
    assert "event=SUMMARY" in text
    assert "decode_ms=" in text and "broadcast_ms=" in text
    assert "cpu_pct=" in text, "the number an operator actually watches"


def test_20_drops_are_counted(perf_log):
    perf, _ = perf_log
    perf.note_drop(8, 3)
    assert perf.totals()["8"]["drops"] == 3


def test_21_the_perf_log_is_completely_inert_when_off(tmp_path, monkeypatch):
    """Instrumentation that costs something when disabled is worse than none."""
    from backend.services import perf_debug
    monkeypatch.delenv("PERF_DEBUG", raising=False)
    monkeypatch.setenv("PERF_DEBUG_LOG", str(tmp_path / "off.log"))
    perf_debug.reset()
    try:
        for _ in range(500):
            with perf_debug.stage(1, "decode"):
                pass
        perf_debug.note_drop(1)
        perf_debug.summarise(1)
        assert not (tmp_path / "off.log").exists()
        assert perf_debug.totals() == {}, "no accumulation when disabled"
    finally:
        perf_debug.reset()


def test_22_a_summary_resets_its_window(perf_log):
    perf, _ = perf_log
    with perf.stage(9, "decode"):
        pass
    perf.summarise(9)
    assert perf.totals()["9"]["counts"]["decode"] == 0, \
        "counters must reset, or every window reports cumulative totals"


# ══════════════════════════════════════════════════════════════════════════════
# The busy-spin — found by the perf log, and the actual cause of the pinned CPU
# ══════════════════════════════════════════════════════════════════════════════

def test_23_a_full_queue_backs_off_instead_of_spinning(monkeypatch):
    """From a real capture:

        cam 48: 2196 drops/sec   but the stream only delivers 15 fps

    You cannot drop 146x more frames than arrive. When the perception queue was full,
    _process_frame counted a drop and returned WITHOUT stamping the lane timer, so
    _work_due() was still true on the very next iteration and the loop retried
    immediately - spinning as fast as the CPU allowed, running the overlay draw on every
    spin."""
    m = cm()
    inst = m.CameraInstance.__new__(m.CameraInstance)
    inst._sched = {}
    inst._queue_full_until = 0.0

    now = 1000.0
    assert inst._work_due(now) is True, "a fresh camera has work due"

    inst._queue_full_until = now + m.QUEUE_BACKOFF
    assert inst._work_due(now) is False, \
        "while the queue is known full the loop must NOT come straight back"
    assert inst._work_due(now + m.QUEUE_BACKOFF + 0.001) is True, \
        "and it must resume once the back-off has elapsed"


def test_24_the_backoff_is_long_enough_to_matter():
    """Shorter than one fast-lane interval and the loop is still effectively spinning:
    the child cannot have drained anything in less time than it takes to produce one."""
    m = cm()
    assert m.QUEUE_BACKOFF >= m.TRACK_INTERVAL / 4


def test_25_the_perf_log_separates_blocking_from_cpu(tmp_path, monkeypatch):
    """The first capture was unreadable because decode_ms was ~100% of every window on
    every camera - cap.read() BLOCKS, and wall time counts that. Optimising on it would
    have meant attacking the wrong stage entirely."""
    import time as _t
    from backend.services import perf_debug
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setenv("PERF_DEBUG_LOG", str(tmp_path / "cpu.log"))
    monkeypatch.setattr(perf_debug, "SUMMARY_EVERY", 0.0)
    perf_debug.reset()
    try:
        with perf_debug.stage(1, "decode"):
            _t.sleep(0.2)                      # blocked, not working
        t = perf_debug.totals()["1"]
        assert t["ms"]["decode"] > 150, "wall time should see the block"
        assert t["cpu"]["decode"] < 20, "CPU time must NOT count time spent blocked"
    finally:
        perf_debug.reset()


def test_26_drops_per_second_is_reported(tmp_path, monkeypatch):
    """The field that made the spin visible. A rate far above the camera's frame rate is
    a bug, not a busy machine."""
    from backend.services import perf_debug
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setenv("PERF_DEBUG_LOG", str(tmp_path / "d.log"))
    monkeypatch.setattr(perf_debug, "SUMMARY_EVERY", 0.0)
    perf_debug.reset()
    try:
        perf_debug.note_drop(3, 50)
        with perf_debug.stage(3, "overlay"):
            pass
        perf_debug.summarise(3)
        assert "drops_per_sec=" in (tmp_path / "d.log").read_text(encoding="utf-8")
    finally:
        perf_debug.reset()


# ══════════════════════════════════════════════════════════════════════════════
# Detection must run without anyone watching
# ══════════════════════════════════════════════════════════════════════════════

def test_27_autostart_is_on_by_default():
    """Reported from the deployment and confirmed in the code: nothing in the
    application's lifespan opened a camera. The only call sites for get_instance were
    the WebSocket handler and get_frame_bytes - both on the VIEWING path.

    So after a restart no detection ran and no alert was produced until an operator
    opened the live feed. A surveillance system that only detects while a human is
    looking at the screen is a video player with a detector attached."""
    from backend.services import autostart
    assert autostart.AUTOSTART is True


def test_28_it_can_be_switched_back_off(monkeypatch):
    """Anyone who genuinely wants viewer-driven behaviour keeps it."""
    import importlib
    monkeypatch.setenv("AUTOSTART_CAMERAS", "0")
    from backend.services import autostart
    importlib.reload(autostart)
    try:
        assert autostart.AUTOSTART is False
    finally:
        monkeypatch.undo()
        importlib.reload(autostart)


def test_29_the_lifespan_actually_calls_it():
    """Guards the wiring: the module existing is useless if nothing invokes it."""
    import inspect
    import backend.main as bm
    src = inspect.getsource(bm.lifespan)
    assert "start_all" in src, "autostart must be invoked from the application lifespan"
    assert src.index("start_perception") < src.index("start_all"), \
        "the perception queues and writer must exist before frames are produced"


def test_30_autostart_skips_cameras_with_no_analytics():
    """A camera with nothing assigned has nothing to detect, so opening it at boot would
    be pure cost. Those stay on demand."""
    import inspect
    from backend.services import autostart
    src = inspect.getsource(autostart._cameras_with_modules)
    assert "if not mods:" in src and "continue" in src


def test_31_a_globally_disabled_module_does_not_cause_a_start():
    import inspect
    from backend.services import autostart
    assert "module_toggles.filter_modules" in inspect.getsource(autostart._sweep)


def test_32_autostart_does_not_block_application_start():
    """Opening RTSP sources can block for seconds each when a camera is unreachable.
    Doing that inline would delay the app becoming ready."""
    import inspect
    from backend.services import autostart
    assert "threading.Thread" in inspect.getsource(autostart.start_all)


def test_33_one_unreachable_camera_does_not_stop_the_others():
    import inspect
    from backend.services import autostart
    src = inspect.getsource(autostart._sweep)
    body = src[src.index("for cam_id"):]
    assert "except Exception" in body, \
        "a failing camera must not abort the sweep for the rest"
