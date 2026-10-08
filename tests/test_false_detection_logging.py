"""
Tests for the false-detection logs and the report tool.

The logs exist to be shipped from a server and diagnosed elsewhere, so the properties
that matter are: silent unless asked for, one file per rule, and — the governing rule —
**every line carries the parameter values that produced the decision**. A log that
records a suppression without recording the threshold cannot be interpreted later, which
is precisely how a threshold defect survived two field captures in the phone module.

Run:  pytest tests/test_false_detection_logging.py -q
"""

from __future__ import annotations

import pytest

from backend.scripts import false_detection_report as rpt
from backend.services.false_detection import FDConfig
from backend.services.false_detection.engine import FalseDetectionFilter
from backend.services.false_detection.logging import LOG, RULE_FILES

CAM = 48


class Clock:
    def __init__(self, start=10_000.0):
        self.t = start

    def tick(self, s=1.0):
        self.t += s

    def __call__(self):
        return self.t


@pytest.fixture
def capture(tmp_path, monkeypatch):
    """Enable logging into a throwaway directory, then reset module state."""
    def _enable(level="trace", split=True):
        monkeypatch.setenv("FD_DEBUG", level)
        monkeypatch.setenv("FD_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("FD_LOG_SPLIT", "1" if split else "0")
        LOG.reset()
        return tmp_path
    yield _enable
    LOG.reset()


def busy_scene(cfg=None, clock=None):
    """One of each kind of suppression, so every rule file gets exercised."""
    clock = clock or Clock()
    cfg = cfg or FDConfig(
        fd_enabled=True, fd_mode="enforce", fd_warmup_seconds=3,
        fd_dedup_window=30, fd_max_alerts_per_minute=0, fd_storm_threshold=999,
        fd_min_confidence=0.30, fd_repeat_source_enabled=False,
        fd_ignore_zones=[[[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]])
    LOG.announce(cfg.log_fields())
    f = FalseDetectionFilter(cfg)
    f._now = clock
    f.camera_started(CAM, clock())

    def ev(track=1, etype="phone_use", **d):
        return {"module": "mobile", "event_type": etype, "data": {"track": track, **d}}

    f.judge(ev(), CAM)                                     # warm-up
    clock.tick(5)
    f.judge(ev(), CAM)                                     # accepted
    clock.tick(1)
    f.judge(ev(), CAM)                                     # dedup
    clock.tick(1)
    f.judge(ev(track=2, confidence=0.1), CAM)              # evidence
    clock.tick(1)
    f.judge(ev(track=3), CAM, point=(0.5, 0.5))            # zone
    f.emit_metrics(cfg)
    f.log_config_change("admin", 1, "global", "fd_dedup_window", 60, 30)
    return f, cfg


# ══════════════════════════════════════════════════════════════════════════════
# silence and creation
# ══════════════════════════════════════════════════════════════════════════════

def test_01_silent_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_DEBUG", "0")
    monkeypatch.setenv("FD_LOG_DIR", str(tmp_path))
    LOG.reset()
    busy_scene()
    assert list(tmp_path.iterdir()) == [], "nothing may be written when FD_DEBUG is off"
    LOG.reset()


def test_02_announce_creates_every_rule_file(capture):
    d = capture("1")
    assert LOG.announce(FDConfig().log_fields()) is True
    for rule in RULE_FILES:
        f = d / f"fd_{rule}.log"
        assert f.exists(), f"fd_{rule}.log missing"
        assert "event=SESSION" in f.read_text(encoding="utf-8")


def test_03_announce_reports_off_without_creating_anything(tmp_path, monkeypatch,
                                                           capsys):
    monkeypatch.setenv("FD_DEBUG", "0")
    monkeypatch.setenv("FD_LOG_DIR", str(tmp_path))
    LOG.reset()
    assert LOG.announce(FDConfig().log_fields()) is False
    assert "diagnostics OFF" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
    LOG.reset()


# ══════════════════════════════════════════════════════════════════════════════
# the governing rule: lines are self-describing
# ══════════════════════════════════════════════════════════════════════════════

def test_04_session_header_records_every_parameter(capture):
    d = capture("1")
    busy_scene()
    sessions = [f for _, f in rpt.parse_file(d / "fd_decisions.log")
                if f["event"] == "SESSION"]
    assert len(sessions) == 1
    s = sessions[0]
    for key in ("fd_enabled", "mode", "fd_dedup_window", "fd_dedup_scope",
                "fd_min_confidence", "fd_warmup_seconds", "fd_max_alerts_per_minute",
                "fd_storm_threshold", "fd_repeat_hits"):
        assert key in s, f"SESSION header is missing {key}"


@pytest.mark.parametrize("rule,param", [
    ("dedup", "fd_dedup_window"),
    ("evidence", "fd_min_confidence"),
    ("warmup", "fd_warmup_seconds"),
    ("zones", "fd_ignore_zones"),
])
def test_05_each_rule_line_carries_its_own_parameters(capture, rule, param):
    """The whole point of the design. A suppression that does not state the threshold
    in force is not diagnosable from the log alone."""
    d = capture("1")
    busy_scene()
    recs = [f for _, f in rpt.parse_file(d / f"fd_{rule}.log")
            if f["event"] != "SESSION"]
    assert recs, f"fd_{rule}.log recorded nothing"
    assert any(param in f for f in recs), \
        f"fd_{rule}.log lines do not carry {param}"


def test_06_every_suppression_names_a_rule_and_a_reason(capture):
    d = capture("1")
    busy_scene()
    judged = [f for _, f in rpt.parse_file(d / "fd_decisions.log")
              if f["event"] == "JUDGE"]
    suppressed = [f for f in judged if f["verdict"] != "ACCEPT"]
    assert suppressed
    for f in suppressed:
        assert f.get("rule") not in (None, "-"), f"unattributed suppression: {f}"
        assert f.get("reason") not in (None, "-")


def test_07_accept_records_which_rules_examined_it(capture):
    """So a rule that is silently not running is visible rather than invisible."""
    d = capture("debug")
    busy_scene()
    accepted = [f for _, f in rpt.parse_file(d / "fd_decisions.log")
                if f["event"] == "JUDGE" and f["verdict"] == "ACCEPT"]
    assert accepted
    passed = str(accepted[0]["checks_passed"])
    for rule in ("severity", "warmup", "zones", "evidence", "dedup"):
        assert rule in passed


def test_08_none_renders_as_a_dash_not_an_empty_field(capture):
    """An absent value is information; it must not silently vanish from the line."""
    d = capture("debug")
    busy_scene()
    text = (d / "fd_decisions.log").read_text(encoding="utf-8")
    assert "confidence=-" in text


# ══════════════════════════════════════════════════════════════════════════════
# levels and splitting
# ══════════════════════════════════════════════════════════════════════════════

def test_09_info_excludes_accepts(capture):
    d = capture("1")
    busy_scene()
    judged = [f for _, f in rpt.parse_file(d / "fd_decisions.log")
              if f["event"] == "JUDGE"]
    assert judged, "suppressions must still be recorded at INFO"
    assert all(f["verdict"] != "ACCEPT" for f in judged), \
        "ACCEPT lines belong to DEBUG and above"


def test_10_debug_includes_accepts(capture):
    d = capture("debug")
    busy_scene()
    judged = [f for _, f in rpt.parse_file(d / "fd_decisions.log")
              if f["event"] == "JUDGE"]
    assert any(f["verdict"] == "ACCEPT" for f in judged)


def test_11_trace_includes_rule_internals(capture):
    d = capture("trace")
    cfg = FDConfig(fd_enabled=True, fd_mode="enforce", fd_warmup_seconds=0,
                   fd_dedup_window=0, fd_max_alerts_per_minute=0,
                   fd_repeat_source_enabled=True, fd_repeat_hits=99,
                   fd_repeat_window=1, fd_repeat_min_tracks=99)
    LOG.announce(cfg.log_fields())
    clock = Clock()
    f = FalseDetectionFilter(cfg)
    f._now = clock
    f.camera_started(CAM, clock())
    for i in range(4):
        clock.tick(1)
        f.judge({"module": "m", "event_type": "phone_use", "data": {"track": i}},
                CAM, point=(0.5, 0.5))
    recs = [r for _, r in rpt.parse_file(d / "fd_repeat.log")
            if r.get("verdict") == "PASS"]
    assert recs, "TRACE must record the map's intermediate state, not only suppressions"


def test_12_split_off_writes_one_tagged_file(capture):
    d = capture("1", split=False)
    busy_scene()
    assert (d / "fd_all.log").exists()
    assert not (d / "fd_decisions.log").exists()
    text = (d / "fd_all.log").read_text(encoding="utf-8")
    assert "log=decisions" in text and "log=dedup" in text


# ══════════════════════════════════════════════════════════════════════════════
# the report tool
# ══════════════════════════════════════════════════════════════════════════════

def test_13_report_runs_on_a_capture(capture, capsys):
    d = capture("trace")
    busy_scene()
    assert rpt.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert "FALSE-DETECTION REPORT" in out
    assert "what each rule cost" in out
    assert "configuration in force" in out


def test_14_report_names_the_rule_for_each_suppression(capture, capsys):
    d = capture("trace")
    busy_scene()
    rpt.main([str(d), "--tracks"])
    out = capsys.readouterr().out
    for rule in ("warmup", "dedup", "evidence", "zones"):
        assert rule in out, f"the report does not attribute anything to {rule}"


def test_15_config_check_detects_drift(capture, capsys, monkeypatch):
    """The bug-class detector: settings that ran differ from what the environment says.
    That is how 'the parameter was never applied' becomes visible."""
    d = capture("1")
    busy_scene()
    monkeypatch.setenv("FD_DEDUP_WINDOW", "999")
    rpt.main([str(d), "--config-check"])
    out = capsys.readouterr().out
    assert "THESE DIFFER" in out and "fd_dedup_window" in out


def test_16_config_check_is_quiet_when_they_match(capture, capsys, monkeypatch):
    d = capture("1")
    for k, v in (("FD_ENABLED", "1"), ("FD_MODE", "enforce"),
                 ("FD_WARMUP_SECONDS", "3"), ("FD_DEDUP_WINDOW", "30"),
                 ("FD_MAX_ALERTS_PER_MINUTE", "0"), ("FD_STORM_THRESHOLD", "999"),
                 ("FD_MIN_CONFIDENCE", "0.30"), ("FD_REPEAT_SOURCE_ENABLED", "0")):
        monkeypatch.setenv(k, v)
    busy_scene()
    rpt.main([str(d), "--config-check"])
    out = capsys.readouterr().out
    assert "same settings" in out, out[-800:]


def test_17_report_handles_an_empty_directory(tmp_path, capsys):
    assert rpt.main([str(tmp_path)]) == 2
    assert "no records found" in capsys.readouterr().out


def test_18_report_handles_a_missing_directory(tmp_path, capsys):
    assert rpt.main([str(tmp_path / "nope")]) == 2
    assert "no such directory" in capsys.readouterr().out


def test_19_report_says_so_when_the_layer_was_disabled(capture, capsys):
    d = capture("1")
    LOG.announce(FDConfig(fd_enabled=False).log_fields())
    rpt.main([str(d)])
    out = capsys.readouterr().out
    assert "No JUDGE records" in out
    assert "fd_enabled=0" in out or "fd_enabled" in out


def test_20_sweep_produces_a_curve(capture, capsys):
    d = capture("trace")
    busy_scene()
    rpt.main([str(d), "--sweep", "fd_dedup_window", "--sweep-values", "0,30,120"])
    out = capsys.readouterr().out
    assert "sweeping fd_dedup_window" in out
    for v in ("0", "30", "120"):
        assert v in out


def test_21_parser_round_trips_the_writers_format():
    from backend.services.false_detection.logging import fmt
    assert rpt.parse_value(fmt(0.25)) == pytest.approx(0.25)
    assert rpt.parse_value(fmt(None)) is None
    assert rpt.parse_value(fmt(True)) == 1
    assert rpt.parse_value(fmt([1, 2])) == [1, 2]
    assert rpt.parse_value(fmt("plain")) == "plain"


def test_22_values_never_contain_spaces():
    """A space would break the whitespace-split parser, silently truncating a line."""
    from backend.services.false_detection.logging import fmt
    assert " " not in fmt("two words")
    assert " " not in fmt([1.5, 2.5])
    assert " " not in fmt({"a": 1, "b": 2})
