"""
Tests for the phone diagnostic log and its analysis tooling.

Same properties that matter as for head-count: silent unless asked for, carries enough
configuration to be interpreted, and — at TRACE — carries enough detail to be REPLAYED.
The fidelity check is the important one: a captured log that does not reproduce the
decisions it recorded cannot be trusted as evidence.
"""

from __future__ import annotations

import pytest

from backend.services.analytics import headcount_debug as hcdbg
from backend.services.analytics import phone_debug as pdbg
from backend.scripts import phone_report as prpt
from tests.test_phone import (
    HAND_DRIFT, at_body, holding, make_consumer, person, run,
)


@pytest.fixture
def capture(tmp_path, monkeypatch):
    log_file = tmp_path / "phone_debug.log"

    def _enable(level="trace"):
        monkeypatch.setenv("PHONE_DEBUG", level)
        monkeypatch.delenv("PHONE_DEBUG_LEVEL", raising=False)
        monkeypatch.setenv("PHONE_DEBUG_LOG", str(log_file))
        pdbg.reset()
        return log_file

    yield _enable
    pdbg.reset()


def _scene(mc, clock):
    """One person on a phone, one not, plus a stray phone on a low table."""
    a, b = person(1, cx=0.25), person(2, cx=0.75)
    stray = at_body(person(98, cx=0.50, cy=0.95), 0.45)
    run(mc, clock, [([a, b], [at_body(a, 0.45, dx=HAND_DRIFT[i % 3]), stray])
                    for i in range(8)])
    run(mc, clock, [([a, b], [stray]) for _ in range(6)])
    run(mc, clock, [([], []) for _ in range(40)])


def test_logging_is_silent_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONE_DEBUG", "0")
    monkeypatch.setenv("PHONE_DEBUG_LOG", str(tmp_path / "nope.log"))
    pdbg.reset()
    mc, clock = make_consumer()
    _scene(mc, clock)
    assert not (tmp_path / "nope.log").exists()
    pdbg.reset()


def test_session_header_records_the_configuration(capture):
    log_file = capture("1")
    mc, clock = make_consumer(association_min=0.42, confirm_hits=4)
    run(mc, clock, holding(person(1), 6))

    sessions = [f for _, f in prpt.parse(log_file) if f["event"] == "SESSION"]
    assert len(sessions) == 1
    assert sessions[0]["association_min"] == 0.42
    assert sessions[0]["confirm_hits"] == 4
    assert sessions[0]["shape"] == "1280x720"


def test_trace_records_every_detection_and_evaluation(capture):
    log_file = capture("trace")
    mc, clock = make_consumer()
    _scene(mc, clock)

    recs = prpt.parse(log_file)
    kinds = {f["event"] for _, f in recs}
    assert {"SESSION", "DET", "EVAL", "STATE", "REJECT", "TRACK_END"} <= kinds
    dets = [f for _, f in recs if f["event"] == "DET"]
    assert dets, "TRACE must record every raw detection, before association"
    assert all(isinstance(d["phone"], list) and len(d["phone"]) == 4 for d in dets)


def test_info_level_records_decisions_but_not_every_detection(capture):
    log_file = capture("1")
    mc, clock = make_consumer()
    _scene(mc, clock)

    kinds = {f["event"] for _, f in prpt.parse(log_file)}
    assert "STATE" in kinds, "decisions must still be recorded"
    assert "DET" not in kinds, "per-detection tracing belongs to TRACE"
    assert "EVAL" not in kinds


def test_rejections_say_why(capture):
    """The whole point: a phone on the table must be distinguishable from no phone."""
    log_file = capture("1")
    mc, clock = make_consumer()
    _scene(mc, clock)

    reasons = {f["reason"] for _, f in prpt.parse(log_file) if f["event"] == "REJECT"}
    assert "phone_outside_upper_body" in reasons


def test_report_runs_on_a_captured_log(capture, capsys):
    log_file = capture("trace")
    mc, clock = make_consumer()
    _scene(mc, clock)

    assert prpt.main([str(log_file), "--tracks", "--scale"]) == 0
    out = capsys.readouterr().out
    assert "PHONE-USE DIAGNOSTIC REPORT" in out
    assert "confirmed phone use          1" in out
    assert "apparent scale" in out


def test_replay_reproduces_the_captured_decisions(capture, capsys):
    log_file = capture("trace")
    mc, clock = make_consumer()
    _scene(mc, clock)

    assert prpt.main([str(log_file), "--replay"]) == 0
    out = capsys.readouterr().out
    assert "confirmed when live          1" in out
    assert "confirmed on replay          1" in out
    assert "fidelity                     MATCH" in out


def test_replay_honours_threshold_overrides(capture, capsys):
    """Retuning against a real captured scene without re-recording it."""
    log_file = capture("trace")
    mc, clock = make_consumer()
    _scene(mc, clock)

    prpt.main([str(log_file), "--replay", "--set", "confirm_hits=99"])
    out = capsys.readouterr().out
    assert "'confirm_hits': 99" in out
    assert "confirmed on replay          0" in out


def test_report_defaults_to_the_logger_location(capture, capsys):
    log_file = capture("trace")
    mc, clock = make_consumer()
    run(mc, clock, holding(person(1), 6))

    assert prpt.main([]) == 0
    out = capsys.readouterr().out
    assert f"reading {log_file}" in out


def test_missing_file_points_at_the_default(tmp_path, capsys):
    assert prpt.main([str(tmp_path / "nope.log")]) == 2
    assert "run with no argument" in capsys.readouterr().out


def test_phone_and_headcount_logs_are_independent(tmp_path, monkeypatch):
    """Two modules, two switches, two files — enabling one must not enable the other."""
    phone_log = tmp_path / "p.log"
    hc_log = tmp_path / "h.log"
    monkeypatch.setenv("PHONE_DEBUG", "trace")
    monkeypatch.setenv("PHONE_DEBUG_LOG", str(phone_log))
    monkeypatch.setenv("HEADCOUNT_DEBUG", "0")
    monkeypatch.setenv("HEADCOUNT_DEBUG_LOG", str(hc_log))
    pdbg.reset()
    hcdbg.reset()

    assert pdbg.debug_enabled() is True
    assert hcdbg.debug_enabled() is False
    assert pdbg.log_path() != hcdbg.log_path()

    mc, clock = make_consumer()
    run(mc, clock, holding(person(1), 6))
    assert phone_log.exists()
    assert not hc_log.exists()

    pdbg.reset()
    hcdbg.reset()


def test_announce_creates_the_file_before_anything_is_logged(tmp_path, monkeypatch):
    """The reported bug: the logger was lazy, so a capture that produced no events was
    indistinguishable from a flag that never arrived — no file, no message, no clue."""
    log_file = tmp_path / "p.log"
    monkeypatch.setenv("PHONE_DEBUG", "trace")
    monkeypatch.setenv("PHONE_DEBUG_LOG", str(log_file))
    pdbg.reset()

    assert not log_file.exists()
    assert pdbg.announce("perception-child") is True
    assert log_file.exists(), "the file must exist from start-up, not from first write"
    assert "event=STARTUP" in log_file.read_text(encoding="utf-8")
    pdbg.reset()


def test_announce_says_so_when_the_flag_is_absent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONE_DEBUG", "0")
    monkeypatch.setenv("PHONE_DEBUG_LOG", str(tmp_path / "p.log"))
    pdbg.reset()

    assert pdbg.announce("main") is False
    out = capsys.readouterr().out
    assert "diagnostics OFF" in out and "PHONE_DEBUG" in out
    assert not (tmp_path / "p.log").exists()
    pdbg.reset()


def test_announce_reports_the_resolved_path(tmp_path, monkeypatch, capsys):
    """Where the file went is the other half of the problem: BASE_DIR may point
    somewhere other than the directory the operator is standing in."""
    log_file = tmp_path / "deep" / "p.log"
    monkeypatch.setenv("PHONE_DEBUG", "1")
    monkeypatch.setenv("PHONE_DEBUG_LOG", str(log_file))
    pdbg.reset()

    pdbg.announce("main")
    assert str(log_file) in capsys.readouterr().out
    assert log_file.exists(), "a nested directory must be created, not silently skipped"
    pdbg.reset()


def test_sub_threshold_detections_are_recorded_so_a_MISS_is_visible(capture):
    """The hardest thing to diagnose is a phone the system did not alert on. If the
    confidence floor silently drops a detection, the log cannot tell 'the model never
    saw it' from 'the floor was too high' — and those have opposite fixes."""
    log_file = capture("trace")
    mc, clock = make_consumer(phone_conf=0.50)
    p = person(1)
    run(mc, clock, holding(p, 6, conf=0.30))          # real phone, weak score

    recs = prpt.parse(log_file)
    ignored = [f for _, f in recs
               if f["event"] == "DET" and f.get("reason") == "below_confidence"]
    assert ignored, "a discarded detection must leave a trace"
    assert ignored[0]["threshold"] == 0.5
    assert ignored[0]["phone_conf"] == pytest.approx(0.30, abs=1e-6)
    assert mc.metrics.below_confidence >= 1


def test_report_shows_what_the_confidence_floor_cost(capture, capsys):
    log_file = capture("trace")
    mc, clock = make_consumer(phone_conf=0.50)
    run(mc, clock, holding(person(1), 6, conf=0.30))

    prpt.main([str(log_file)])
    out = capsys.readouterr().out
    assert "discarded by the confidence floor" in out
    assert "WOULD have associated with a person" in out


def test_track_end_records_what_blocked_the_episode(capture):
    """The tuning table is built from this: which gate stopped each track."""
    log_file = capture("1")
    mc, clock = make_consumer(state_ttl=0.5, min_motion_px=40, min_motion_frac=0)
    p = person(1)
    run(mc, clock, holding(p, 8, still=True))
    run(mc, clock, [([], []) for _ in range(20)])

    ends = [f for _, f in prpt.parse(log_file) if f["event"] == "TRACK_END"]
    assert ends and ends[0]["blocked_by"] == "phone_did_not_move"
    assert ends[0]["best_evidence"].startswith(("3/", "4/", "5/"))


def test_report_shows_the_gate_cost_table(capture, capsys):
    log_file = capture("1")
    mc, clock = make_consumer(state_ttl=0.5, min_motion_px=40, min_motion_frac=0)
    run(mc, clock, holding(person(1), 8, still=True))
    run(mc, clock, [([], []) for _ in range(20)])

    prpt.main([str(log_file)])
    out = capsys.readouterr().out
    assert "what stopped each track that DID have detections" in out
    assert "phone_did_not_move" in out
    assert "OFF by default" in out
