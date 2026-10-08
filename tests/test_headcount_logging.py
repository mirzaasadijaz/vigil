"""
Tests for the head-count diagnostic log and its analysis tooling.

The log exists to be handed to someone else for diagnosis, so the properties that matter
are: it is silent unless asked for, it carries enough configuration to be interpreted,
and — at TRACE — it carries enough trajectory detail to be REPLAYED. The replay fidelity
check is the important one: if a captured log does not reproduce the counts it recorded,
it cannot be trusted as evidence.
"""

from __future__ import annotations

import pytest

from backend.services.analytics import headcount_debug as hcdbg
from backend.scripts import headcount_report as rpt
from tests.test_headcount import make_consumer, track, run, linear


@pytest.fixture
def capture(tmp_path, monkeypatch):
    """Enable diagnostic logging into a throwaway file, then fully reset module state."""
    log_file = tmp_path / "headcount_debug.log"

    def _enable(level="TRACE"):
        monkeypatch.setenv("HEADCOUNT_DEBUG", "1")
        monkeypatch.setenv("HEADCOUNT_DEBUG_LEVEL", level)
        monkeypatch.setenv("HEADCOUNT_DEBUG_LOG", str(log_file))
        _reset()
        return log_file

    def _reset():
        hcdbg.reset()

    yield _enable
    _reset()


def _busy_scene(hc, clock):
    """A few people: one enters, one exits, one turns back, two cross together."""
    gap = lambda: run(hc, clock, [[] for _ in range(40)])
    run(hc, clock, [[track(1, 0.5, y)] for y in linear(0.20, 0.80, 12)]); gap()
    run(hc, clock, [[track(2, 0.5, y)] for y in linear(0.80, 0.20, 12)]); gap()
    run(hc, clock, [[track(3, 0.5, y)] for y in linear(0.20, 0.485, 8)
                    + linear(0.485, 0.20, 8)]); gap()
    run(hc, clock, [[track(5, 0.35, y), track(6, 0.65, y)]
                    for y in linear(0.20, 0.80, 12)]); gap()


def test_logging_is_silent_by_default(tmp_path, monkeypatch, capture):
    monkeypatch.setenv("HEADCOUNT_DEBUG", "0")
    monkeypatch.setenv("HEADCOUNT_DEBUG_LOG", str(tmp_path / "should_not_exist.log"))
    hcdbg.reset()

    hc, clock = make_consumer()
    _busy_scene(hc, clock)
    assert not (tmp_path / "should_not_exist.log").exists()


def test_session_header_records_the_configuration(capture):
    log_file = capture("INFO")
    hc, clock = make_consumer(crossing_band=0.042)
    run(hc, clock, [[track(1, 0.5, y)] for y in linear(0.20, 0.80, 12)])

    records = rpt.parse(log_file)
    sessions = [f for _, f in records if f["event"] == "SESSION"]
    assert len(sessions) == 1
    s = sessions[0]
    assert s["crossing_band"] == 0.042
    assert s["reference_point"] == "foot"
    assert s["line"] == [[0.0, 0.5], [1.0, 0.5]]
    assert s["shape"] == "1280x720"


def test_session_header_is_not_repeated_when_only_totals_change(capture):
    """Running totals must not be part of the session identity, or the header would
    reprint on every crossing and bury the log."""
    log_file = capture("INFO")
    hc, clock = make_consumer()
    _busy_scene(hc, clock)

    records = rpt.parse(log_file)
    assert len([f for _, f in records if f["event"] == "SESSION"]) == 1


def test_session_header_reprints_when_config_changes(capture):
    log_file = capture("INFO")
    hc, clock = make_consumer()
    run(hc, clock, [[track(1, 0.5, y)] for y in linear(0.20, 0.45, 4)])
    hc.config = dict(hc.config, crossing_band=0.09)      # operator retunes mid-capture
    run(hc, clock, [[track(1, 0.5, y)] for y in linear(0.45, 0.80, 4)])

    bands = [f["crossing_band"] for _, f in rpt.parse(log_file) if f["event"] == "SESSION"]
    assert bands == [0.03, 0.09]


def test_trace_level_records_every_track_every_frame(capture):
    log_file = capture("TRACE")
    hc, clock = make_consumer()
    frames = [[track(1, 0.35, y), track(2, 0.65, y)] for y in linear(0.20, 0.80, 12)]
    run(hc, clock, frames)

    obs = [f for _, f in rpt.parse(log_file) if f["event"] == "OBS"]
    assert len(obs) == 24, "TRACE must record both tracks on all 12 frames"
    assert {o["track"] for o in obs} == {1, 2}
    assert all(isinstance(o["box"], list) and len(o["box"]) == 4 for o in obs)


def test_debug_level_records_only_tracks_near_the_line(capture):
    log_file = capture("DEBUG")
    hc, clock = make_consumer()
    # one person crossing, one loitering in the far corner the whole time
    frames = [[track(1, 0.35, y), track(2, 0.9, 0.05)] for y in linear(0.20, 0.80, 12)]
    run(hc, clock, frames)

    obs = [f for _, f in rpt.parse(log_file) if f["event"] == "OBS"]
    assert obs, "DEBUG should still trace the crossing track"
    assert {o["track"] for o in obs} == {1}, "the distant track must not be traced"


def test_track_end_summarises_a_missed_approach(capture):
    """The 'why was this person missed?' record."""
    log_file = capture("INFO")
    hc, clock = make_consumer(state_ttl=0.5)
    run(hc, clock, [[track(9, 0.5, y)] for y in linear(0.20, 0.47, 8)])   # approach only
    run(hc, clock, [[] for _ in range(20)])                               # let it purge

    ends = [f for _, f in rpt.parse(log_file) if f["event"] == "TRACK_END"]
    assert len(ends) == 1
    e = ends[0]
    assert e["track"] == 9
    assert e["crossings"] == 0
    assert e["closest_approach"] < 0.05


def test_report_runs_on_a_captured_log(capture, capsys):
    log_file = capture("TRACE")
    hc, clock = make_consumer()
    _busy_scene(hc, clock)

    assert rpt.main([str(log_file), "--tracks"]) == 0
    out = capsys.readouterr().out
    assert "HEAD-COUNT DIAGNOSTIC REPORT" in out
    assert "crossings committed" in out
    assert "all completed tracks" in out


def test_replay_reproduces_the_captured_counts(capture, capsys):
    """The property that makes a captured log usable as evidence."""
    log_file = capture("TRACE")
    hc, clock = make_consumer()
    _busy_scene(hc, clock)
    live = (hc.entries, hc.exits)
    assert live == (3, 1)

    assert rpt.main([str(log_file), "--replay"]) == 0
    out = capsys.readouterr().out
    assert f"counted when live            in={live[0]} out={live[1]}" in out
    assert f"counted on replay            in={live[0]} out={live[1]}" in out
    assert "fidelity                     MATCH" in out


def test_replay_honours_threshold_overrides(capture, capsys):
    """Re-tuning against a real captured scene without needing the video."""
    log_file = capture("TRACE")
    hc, clock = make_consumer()
    _busy_scene(hc, clock)

    rpt.main([str(log_file), "--replay",
              "--set", "crossing_band=0.20", "--set", "confirm_observations=8"])
    out = capsys.readouterr().out
    assert "'crossing_band': 0.2" in out
    # An absurdly wide band with a long confirmation must suppress crossings
    replayed = [l for l in out.splitlines() if "counted on replay" in l][0]
    assert "in=3 out=1" not in replayed


def test_report_handles_an_empty_or_missing_file(tmp_path, capsys):
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert rpt.main([str(empty)]) == 2
    assert rpt.main([str(tmp_path / "nope.log")]) == 2


def test_parser_handles_nested_tuples_and_scalars():
    assert rpt._parse_value("((0.0000,0.5000),(1.0000,0.5000))") == [[0.0, 0.5], [1.0, 0.5]]
    assert rpt._parse_value("42") == 42
    assert rpt._parse_value("0.0300") == 0.03
    assert rpt._parse_value("BEFORE") == "BEFORE"
    assert rpt._parse_value("105-120") == "105-120"


@pytest.mark.parametrize("value,expect_obs", [
    ("trace", True),    # one variable, full capture
    ("TRACE", True),    # case-insensitive
    ("debug", False),   # decisions + near-line only; the far track is not traced
    ("1", False),       # decisions only
])
def test_single_variable_selects_the_level(tmp_path, monkeypatch, value, expect_obs):
    """HEADCOUNT_DEBUG carries the level, so enabling a full capture is ONE variable."""
    log_file = tmp_path / "hc.log"
    monkeypatch.setenv("HEADCOUNT_DEBUG", value)
    monkeypatch.delenv("HEADCOUNT_DEBUG_LEVEL", raising=False)
    monkeypatch.setenv("HEADCOUNT_DEBUG_LOG", str(log_file))
    hcdbg.reset()

    hc, clock = make_consumer()
    # a distant track: only TRACE records it
    run(hc, clock, [[track(1, 0.9, 0.05)] for _ in range(6)])

    records = rpt.parse(log_file) if log_file.exists() else []
    has_obs = any(f["event"] == "OBS" for _, f in records)
    assert has_obs is expect_obs

    hcdbg.reset()


def test_report_defaults_to_the_logger_location(tmp_path, monkeypatch, capsys):
    """No path argument: the tool finds the file the logger writes to. This removes the
    whole class of hand-typed-path errors, including a Windows path pasted into a POSIX
    shell (where the shell eats the backslash before Python sees it)."""
    log_file = tmp_path / "hc.log"
    monkeypatch.setenv("HEADCOUNT_DEBUG", "trace")
    monkeypatch.delenv("HEADCOUNT_DEBUG_LEVEL", raising=False)
    monkeypatch.setenv("HEADCOUNT_DEBUG_LOG", str(log_file))
    hcdbg.reset()

    hc, clock = make_consumer()
    run(hc, clock, [[track(1, 0.5, y)] for y in linear(0.20, 0.80, 12)])

    assert rpt.main([]) == 0                       # <- no path argument
    out = capsys.readouterr().out
    assert f"reading {log_file}" in out
    assert "crossings committed          1" in out

    hcdbg.reset()


def test_missing_file_points_at_the_default(tmp_path, capsys):
    assert rpt.main([str(tmp_path / "nope.log")]) == 2
    out = capsys.readouterr().out
    assert "run with no argument" in out
