"""
Tests for `backend/scripts/false_detection_report.py`.

This is the tool the operator actually runs against a capture from the server, so its
failure mode is the worst kind: it prints a confident-looking report from a log it
half-understood. The tests therefore centre on ROUND-TRIPPING — real events pushed
through the real filter and the real logger, then read back by the real parser. The log
format lives in `logging.fmt` and the inverse lives in `report.parse_value`; they are two
pieces of code that must agree, and nothing but a round trip proves they do.

The second theme is damage tolerance. A capture handed over from a production box may be
truncated mid-line, rotated part-way, or missing files entirely. None of that may crash
the report, because a crash is what stops the diagnosis.

Run:  pytest tests/test_false_detection_report.py -q
"""

from __future__ import annotations

import os

import pytest

from backend.scripts import false_detection_report as R
from backend.services.false_detection import FDConfig
from backend.services.false_detection.engine import FalseDetectionFilter
from backend.services.false_detection.logging import LOG, RULE_FILES, fmt

CAM = 48


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    """A real capture directory written by the real logger."""
    monkeypatch.setenv("FD_DEBUG", "trace")
    monkeypatch.setenv("FD_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FD_LOG_SPLIT", "1")
    LOG.reset()
    yield tmp_path
    LOG.reset()


class Clock:
    def __init__(self, start=1_700_000_000.0):
        self.t = start

    def tick(self, s=1.0):
        self.t += s

    def __call__(self):
        return self.t


def make_filter(**cfg):
    clock = Clock()
    base = {"fd_enabled": True, "fd_mode": "enforce", "fd_warmup_seconds": 0}
    base.update(cfg)
    f = FalseDetectionFilter(FDConfig(**base))
    f._now = clock
    f.camera_started(CAM, clock())
    return f, clock


def ev(module="mobile", event_type="phone_use", track=1, confidence=0.6):
    return {"module": module, "event_type": event_type, "snapshot": True, "value": 1,
            "data": {"track": track, "confidence": confidence}}


def capture(logdir, n=30, **cfg):
    """Drive real traffic through the filter so the log is genuinely produced."""
    f, clock = make_filter(**cfg)
    LOG.announce(f.config.log_fields())
    for i in range(n):
        clock.tick(2.0)
        f.judge(ev(track=i % 4, confidence=0.3 + (i % 5) / 10.0), CAM,
                point=(0.5, 0.5))
    return f, clock


# ══════════════════════════════════════════════════════════════════════════════
# the round trip: what the logger writes, the parser must read back
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("value", [
    None, 0, 1, -5, 42, 0.5, 1.25, 0.0, "dedup", "phone_use", True, False,
    (1, 2, 3), [0.1, 0.2], (0.5, (1, 2)), (),
])
def test_01_fmt_and_parse_value_are_inverses(value):
    """Every field type the engine logs must survive the trip out and back."""
    back = R.parse_value(fmt(value))
    if value is None:
        assert back is None
    elif isinstance(value, bool):
        assert back == (1 if value else 0), "bools log as 1/0 and read back as ints"
    elif isinstance(value, (tuple, list)):
        assert back == [R.parse_value(fmt(v)) for v in value]
    elif isinstance(value, float):
        assert abs(back - value) < 1e-4
    else:
        assert back == value


def test_02_a_string_with_a_space_does_not_split_the_line():
    """`fmt` underscores spaces precisely so a line always splits on whitespace. If that
    ever changes, every field after it would be silently mis-parsed."""
    rendered = fmt("duplicate within window")
    assert " " not in rendered
    assert R.parse_value(rendered) == "duplicate_within_window"


def test_03_real_decisions_round_trip(logdir):
    f, _ = capture(logdir, n=20)
    data = R.load(str(logdir))
    judged = [fl for _, fl in data["decisions"] if fl.get("event") == "JUDGE"]
    assert len(judged) == 20, "every judgement must be readable back"
    for fl in judged:
        assert fl["camera"] == CAM
        assert fl["module"] == "mobile"
        assert fl["verdict"] in ("ACCEPT", "SUPPRESS", "DEMOTE")
        assert isinstance(fl["track"], int)


def test_04_confidence_survives_as_a_number_not_a_string(logdir):
    """`--sweep fd_min_confidence` compares against this. A string would compare wrong
    without erroring, which is the worst possible outcome."""
    capture(logdir, n=10)
    data = R.load(str(logdir))
    confs = [fl.get("confidence") for _, fl in data["decisions"]
             if fl.get("event") == "JUDGE"]
    assert confs and all(isinstance(c, float) for c in confs), confs


# ══════════════════════════════════════════════════════════════════════════════
# loading
# ══════════════════════════════════════════════════════════════════════════════

def test_05_load_finds_every_rule_file_that_has_content(logdir):
    capture(logdir, n=30, fd_dedup_window=60, fd_max_alerts_per_minute=2,
            fd_min_confidence=0.5)
    data = R.load(str(logdir))
    assert "decisions" in data
    assert set(data) <= set(RULE_FILES), "no unexpected keys"
    assert any(len(v) for v in data.values())


def test_06_load_reads_the_combined_file(tmp_path, monkeypatch):
    """FD_LOG_SPLIT=false is the mode an operator uses when mailing one file over."""
    monkeypatch.setenv("FD_DEBUG", "trace")
    monkeypatch.setenv("FD_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FD_LOG_SPLIT", "false")
    LOG.reset()
    try:
        capture(tmp_path, n=12, fd_dedup_window=60)
        assert (tmp_path / "fd_all.log").exists()
        data = R.load(str(tmp_path))
        judged = [fl for _, fl in data.get("decisions", [])
                  if fl.get("event") == "JUDGE"]
        assert len(judged) == 12
        assert all("log" not in fl for fl in judged), \
            "the rule tag must be consumed as the key, not left as a field"
    finally:
        LOG.reset()


def test_07_load_on_an_empty_directory_returns_nothing(tmp_path):
    assert R.load(str(tmp_path)) == {}


def test_08_a_truncated_final_line_is_skipped_not_fatal(logdir):
    """A capture taken while the server was still running ends mid-write."""
    capture(logdir, n=10)
    path = logdir / "fd_decisions.log"
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "2026-08-26 12:00:00,00", encoding="utf-8")
    data = R.load(str(logdir))
    assert len([f for _, f in data["decisions"] if f.get("event") == "JUDGE"]) == 10


def test_09_garbage_in_the_middle_is_skipped(logdir):
    capture(logdir, n=6)
    path = logdir / "fd_decisions.log"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.insert(2, "not a log line at all")
    lines.insert(4, "2026-08-26 12:00:00,000 no equals signs here")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data = R.load(str(logdir))
    assert len([f for _, f in data["decisions"] if f.get("event") == "JUDGE"]) == 6


def test_10_a_non_utf8_byte_does_not_stop_the_read(logdir):
    capture(logdir, n=5)
    path = logdir / "fd_decisions.log"
    with open(path, "ab") as fh:
        fh.write(b"\xff\xfe binary noise\n")
    data = R.load(str(logdir))
    assert len([f for _, f in data["decisions"] if f.get("event") == "JUDGE"]) == 5


# ══════════════════════════════════════════════════════════════════════════════
# the report itself
# ══════════════════════════════════════════════════════════════════════════════

def test_11_report_prints_the_counts(logdir, capsys):
    capture(logdir, n=24, fd_dedup_window=60)
    R.report(R.load(str(logdir)), show_tracks=False, config_check=False)
    out = capsys.readouterr().out
    assert "24" in out, "the number of judged events must appear"
    assert "dedup" in out, "the rule that fired must be named"


def test_12_report_survives_a_decisions_only_capture(logdir, capsys):
    """Rule files are only created when that rule has something to say."""
    capture(logdir, n=5, fd_dedup_window=0, fd_max_alerts_per_minute=0,
            fd_repeat_source_enabled=False)
    R.report({"decisions": R.load(str(logdir))["decisions"]},
             show_tracks=True, config_check=True)
    assert capsys.readouterr().out


def test_13_report_on_an_empty_dataset_does_not_crash(capsys):
    R.report({}, show_tracks=True, config_check=True)
    assert capsys.readouterr().out


def test_14_tracks_lists_the_individual_suppressions(logdir, capsys):
    capture(logdir, n=20, fd_dedup_window=120)
    R.report(R.load(str(logdir)), show_tracks=True, config_check=False)
    out = capsys.readouterr().out
    assert "dedup" in out


def test_15_config_check_reports_drift_against_this_environment(logdir, capsys,
                                                                monkeypatch):
    """The point of --config-check: 'the setting you think is live is not live'."""
    capture(logdir, n=5, fd_dedup_window=60)
    monkeypatch.setenv("FD_DEDUP_WINDOW", "999")
    R.report(R.load(str(logdir)), show_tracks=False, config_check=True)
    out = capsys.readouterr().out
    assert "fd_dedup_window" in out and "999" in out


def test_16_config_check_is_quiet_when_nothing_drifted(logdir, capsys, monkeypatch):
    """The converse of the test above, and the one that stops --config-check from
    crying wolf. The environment is set to match the capture exactly, so a report of
    drift here would mean the comparison itself is broken."""
    monkeypatch.setenv("FD_ENABLED", "1")
    monkeypatch.setenv("FD_MODE", "enforce")
    monkeypatch.setenv("FD_WARMUP_SECONDS", "0")
    capture(logdir, n=5)
    R.report(R.load(str(logdir)), show_tracks=False, config_check=True)
    out = capsys.readouterr().out
    assert "THESE DIFFER" not in out, out


# ══════════════════════════════════════════════════════════════════════════════
# replay — the claim that matters most, so it is checked against ground truth
# ══════════════════════════════════════════════════════════════════════════════

def test_17_replaying_at_the_captured_settings_reproduces_the_capture(logdir, capsys):
    """The only self-verifying property replay has: replayed at the settings it was
    captured with, it must reach the same suppression count. If it cannot reproduce the
    past, its predictions about other settings mean nothing.

    Restricted to the replayable rules: no zones, no warm-up, no repeat-source, since the
    docstring is explicit that those cannot be reconstructed from the log."""
    capture(logdir, n=40, fd_dedup_window=60, fd_repeat_source_enabled=False)
    data = R.load(str(logdir))
    live = sum(1 for _, f in data["decisions"]
               if f.get("event") == "JUDGE" and f.get("verdict") != "ACCEPT")
    assert live > 0, "the fixture must actually suppress something to be a real test"
    assert R.replay(data, {}, quiet=True) == live


def test_18_a_wider_dedup_window_suppresses_more(logdir):
    """Monotonicity is the sanity check on the whole sweep: if this direction is wrong,
    an operator would tune the parameter backwards."""
    capture(logdir, n=40, fd_dedup_window=0, fd_repeat_source_enabled=False)
    data = R.load(str(logdir))
    counts = [R.replay(data, {"fd_dedup_window": w}, quiet=True)
              for w in (0, 15, 60, 300)]
    assert counts == sorted(counts), counts
    assert counts[-1] > counts[0], "a 5-minute window must catch more than none"


def test_19_a_higher_confidence_floor_suppresses_more(logdir):
    capture(logdir, n=40, fd_min_confidence=0.0, fd_repeat_source_enabled=False)
    data = R.load(str(logdir))
    low = R.replay(data, {"fd_min_confidence": 0.0}, quiet=True)
    high = R.replay(data, {"fd_min_confidence": 0.9}, quiet=True)
    assert high > low


def test_20_replay_without_judge_records_says_so_rather_than_guessing(capsys):
    assert R.replay({}, {}) == -1
    assert "Cannot replay" in capsys.readouterr().out


def test_21_replay_names_its_own_limits_in_the_output(logdir, capsys):
    """An operator reading a replay must be told what it could not reproduce, or they
    will read the number as a full prediction."""
    capture(logdir, n=10, fd_dedup_window=60)
    R.replay(R.load(str(logdir)), {"fd_dedup_window": 5})
    out = capsys.readouterr().out
    assert "not replayable" in out
    assert "suppressed when captured" in out and "suppressed on replay" in out


# ══════════════════════════════════════════════════════════════════════════════
# the command line, which is the only surface the operator touches
# ══════════════════════════════════════════════════════════════════════════════

def test_22_main_on_a_real_capture_succeeds(logdir, capsys):
    capture(logdir, n=15, fd_dedup_window=60)
    assert R.main([str(logdir)]) == 0
    assert "15" in capsys.readouterr().out


def test_23_main_on_a_missing_directory_explains_itself(tmp_path, capsys):
    rc = R.main([str(tmp_path / "nope")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no such directory" in out
    assert "FD_DEBUG" in out, "the message must say how to produce a capture"


def test_24_main_on_an_empty_directory_explains_itself(tmp_path, capsys):
    assert R.main([str(tmp_path)]) == 2
    assert "no records found" in capsys.readouterr().out


def test_25_set_overrides_are_parsed_as_numbers(logdir, capsys):
    """`--set fd_dedup_window=90` passing the string "90" would silently do nothing."""
    capture(logdir, n=20, fd_dedup_window=0, fd_repeat_source_enabled=False)
    assert R.main([str(logdir), "--replay", "--set", "fd_dedup_window=300"]) == 0
    out = capsys.readouterr().out
    assert "'fd_dedup_window': 300" in out


def test_26_sweep_prints_one_row_per_value(logdir, capsys):
    capture(logdir, n=30, fd_dedup_window=0, fd_repeat_source_enabled=False)
    assert R.main([str(logdir), "--sweep", "fd_dedup_window",
                   "--sweep-values", "0,30,120"]) == 0
    out = capsys.readouterr().out
    body = out.split("--- sweeping")[1]
    rows = [ln for ln in body.splitlines() if ln.strip().split(" ")[0].strip()
            in ("0", "30", "120")]
    assert len(rows) == 3, body


def test_27_sweep_has_defaults_for_the_parameters_worth_sweeping(logdir, capsys):
    capture(logdir, n=20, fd_repeat_source_enabled=False)
    for key in ("fd_dedup_window", "fd_min_confidence", "fd_max_alerts_per_minute"):
        assert R.main([str(logdir), "--sweep", key]) == 0, key
    assert "needs --sweep-values" not in capsys.readouterr().out


def test_28_sweeping_an_unknown_key_asks_for_values_instead_of_crashing(logdir, capsys):
    capture(logdir, n=5)
    assert R.main([str(logdir), "--sweep", "fd_warmup_seconds"]) == 2
    assert "needs --sweep-values" in capsys.readouterr().out


def test_29_no_directory_argument_reads_the_default_location(logdir, capsys):
    """The documented invocation is bare `python -m backend.scripts.false_detection_report`
    and it must find the logger's own directory."""
    capture(logdir, n=8)
    assert R.main([]) == 0
    out = capsys.readouterr().out
    assert os.path.basename(str(logdir)) in out
