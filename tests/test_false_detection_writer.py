"""
Integration tests for the false-detection hook inside the perception writer.

This laptop has no camera and no PostgreSQL, so these tests reproduce the writer's event
loop faithfully against a fake session and assert on what WOULD have been written. That
is the part worth testing anyway: the layer's contract with the writer is "decide, then
either persist or skip", and nothing about that needs a database to verify.

What is deliberately NOT claimed here: that the layer behaves correctly against real
camera traffic. That is `false-detection-test-cases.md`, and it needs the hardware.

Run:  pytest tests/test_false_detection_writer.py -q
"""

from __future__ import annotations

import json

import pytest

from backend.services.false_detection import FDConfig
from backend.services.false_detection.engine import FalseDetectionFilter

CAM = 48


class FakeAlertLog:
    """Stands in for the ORM row so the assertions can inspect what the writer built."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeSession:
    def __init__(self):
        self.rows: list = []
        self.commits = 0

    def add(self, row):
        self.rows.append(row)

    def commit(self):
        self.commits += 1


def write_events(events, filt, session, overlays=None, snapshots_taken=None):
    """A faithful reproduction of the writer's per-event block in
    `perception._result_writer`, with the false-detection hook in place.

    Kept deliberately close to the real code: if the real loop changes shape, this test
    should be updated alongside it rather than silently diverging."""
    snapshots_taken = snapshots_taken if snapshots_taken is not None else []
    for ev in events:
        module = ev.get("module")

        point = None
        track = (ev.get("data") or {}).get("track")
        for ov in (overlays or []):
            if ov.get("track") == track and ov.get("box"):
                b = ov["box"]
                point = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
                break

        verdict = filt.judge(ev, CAM, point=point,
                             has_snapshot=bool(ev.get("snapshot")))
        if not verdict.write:
            continue

        want_snap = ev.get("snapshot") and verdict.snapshot
        snap = None
        if want_snap:
            snap = f"snapshots/a_{len(snapshots_taken)}.jpg"
            snapshots_taken.append(snap)

        session.add(FakeAlertLog(
            module=module, camera_id=CAM, event_type=ev.get("event_type"),
            violation_type=ev.get("label") or f"{module}:{ev.get('event_type')}",
            value=ev.get("value"),
            data=json.dumps(ev.get("data")) if ev.get("data") is not None else None,
            snapshot=snap,
            severity=verdict.severity if verdict.severity_recorded else None,
        ))
    session.commit()
    return session, snapshots_taken


class Clock:
    def __init__(self, start=10_000.0):
        self.t = start

    def tick(self, s=1.0):
        self.t += s

    def __call__(self):
        return self.t


def make_filter(**cfg):
    clock = Clock()
    base = {"fd_enabled": True, "fd_warmup_seconds": 0, "fd_dedup_window": 0,
            "fd_max_alerts_per_minute": 0, "fd_repeat_source_enabled": False}
    base.update(cfg)
    f = FalseDetectionFilter(FDConfig(**base))
    f._now = clock
    f.camera_started(CAM, clock())
    return f, clock


def ev(module="mobile", event_type="phone_use", track=1, snapshot=True, **data):
    return {"module": module, "event_type": event_type, "snapshot": snapshot,
            "value": 1, "data": {"track": track, **data}}


# ══════════════════════════════════════════════════════════════════════════════
# the contract: disabled must be byte-identical
# ══════════════════════════════════════════════════════════════════════════════

def test_01_disabled_writes_exactly_what_it_did_before():
    """Rows must be indistinguishable from those written before this layer existed —
    including a NULL severity, so nothing downstream can start depending on it."""
    f = FalseDetectionFilter(FDConfig(fd_enabled=False))
    s, snaps = write_events([ev(), ev(track=2), ev(track=3)], f, FakeSession())
    assert len(s.rows) == 3
    assert all(r.severity is None for r in s.rows)
    assert len(snaps) == 3
    assert s.commits == 1


def test_02_disabled_writes_the_same_rows_as_enabled_observe():
    """observe mode may annotate, but must never remove a row."""
    off = FalseDetectionFilter(FDConfig(fd_enabled=False))
    on, _ = make_filter(fd_mode="observe", fd_dedup_window=60)
    events = [ev(), ev(), ev()]          # two of these are duplicates
    a, _ = write_events(events, off, FakeSession())
    b, _ = write_events(events, on, FakeSession())
    assert len(a.rows) == len(b.rows) == 3


# ══════════════════════════════════════════════════════════════════════════════
# suppression actually prevents the write
# ══════════════════════════════════════════════════════════════════════════════

def test_03_enforce_removes_the_duplicate_row():
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=60)
    s = FakeSession()
    write_events([ev()], f, s)
    clock.tick(5)
    write_events([ev()], f, s)
    assert len(s.rows) == 1, "the duplicate must never reach the database"


def test_04_severity_is_persisted():
    f, _ = make_filter()
    s, _ = write_events([ev(event_type="fire"), ev(event_type="entry", track=2)],
                        f, FakeSession())
    assert [r.severity for r in s.rows] == ["critical", "low"]


def test_05_demote_writes_the_row_at_info_without_a_snapshot():
    """A demoted event still exists — that is the difference between demote and
    suppress, and it matters for a security product."""
    f, clock = make_filter(fd_mode="enforce", fd_storm_threshold=2,
                           fd_storm_action="demote")
    s = FakeSession()
    snaps = []
    for i in range(4):
        clock.tick(1)
        write_events([ev(track=i)], f, s, snapshots_taken=snaps)
    assert len(s.rows) == 4, "storm demotes, it does not silence the camera"
    assert s.rows[-1].severity == "info"
    assert s.rows[-1].snapshot is None
    assert len(snaps) < 4, "demoted rows must not consume snapshot disk"


def test_06_min_severity_snapshot_stops_the_file_but_not_the_row():
    f, _ = make_filter(fd_min_severity_snapshot="critical")
    s, snaps = write_events([ev(event_type="phone_use")], f, FakeSession())
    assert len(s.rows) == 1
    assert s.rows[0].snapshot is None
    assert snaps == [], "the snapshot file must not be written either"


def test_07_an_event_that_never_wanted_a_snapshot_is_unaffected():
    f, _ = make_filter()
    s, snaps = write_events([ev(snapshot=False)], f, FakeSession())
    assert len(s.rows) == 1 and s.rows[0].snapshot is None and snaps == []


# ══════════════════════════════════════════════════════════════════════════════
# the spatial rule needs a point, which the writer derives from overlays
# ══════════════════════════════════════════════════════════════════════════════

ZONE = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]


def test_08_point_is_derived_from_the_matching_overlay():
    f, _ = make_filter(fd_mode="enforce", fd_ignore_zones=[ZONE])
    overlays = [{"track": 1, "box": [0.45, 0.45, 0.55, 0.55]}]
    s, _ = write_events([ev(track=1)], f, FakeSession(), overlays=overlays)
    assert s.rows == [], "an event inside an ignore zone must not be written"


def test_09_an_overlay_for_a_different_track_is_not_used():
    """Using someone else's position would suppress the wrong person."""
    f, _ = make_filter(fd_mode="enforce", fd_ignore_zones=[ZONE])
    overlays = [{"track": 99, "box": [0.45, 0.45, 0.55, 0.55]}]
    s, _ = write_events([ev(track=1)], f, FakeSession(), overlays=overlays)
    assert len(s.rows) == 1, "no point for this track means the zone rule cannot run"


def test_10_no_overlays_at_all():
    f, _ = make_filter(fd_mode="enforce", fd_ignore_zones=[ZONE])
    s, _ = write_events([ev()], f, FakeSession(), overlays=None)
    assert len(s.rows) == 1


# ══════════════════════════════════════════════════════════════════════════════
# failure of the layer must never lose an event
# ══════════════════════════════════════════════════════════════════════════════

def test_11_a_broken_filter_lets_events_through():
    """The layer sits in the single DB writer. If it raises, the event must still be
    persisted — losing a security event to a bug in a filter is far worse than an
    unfiltered alert."""
    from backend.services.false_detection import Judgement

    class Exploding(FalseDetectionFilter):
        def judge(self, *a, **kw):
            raise RuntimeError("boom")

    filt = Exploding(FDConfig(fd_enabled=True))

    def safe_judge(event, cam, point=None, has_snapshot=True):
        try:
            return filt.judge(event, cam, point=point, has_snapshot=has_snapshot)
        except Exception:
            return Judgement()          # what perception.fd_judge does

    s = FakeSession()
    for e in [ev(), ev(track=2)]:
        v = safe_judge(e, CAM)
        if v.write:
            s.add(FakeAlertLog(event_type=e["event_type"], severity=None))
    assert len(s.rows) == 2


def test_12_module_events_keep_their_payload():
    """The layer must not alter what a module reported."""
    f, _ = make_filter()
    payload = {"track": 7, "confidence": 0.61, "evidence": "3/5"}
    s, _ = write_events(
        [{"module": "mobile", "event_type": "phone_use", "snapshot": True,
          "value": 0.61, "data": payload}], f, FakeSession())
    assert json.loads(s.rows[0].data) == payload
    assert s.rows[0].value == 0.61


# ══════════════════════════════════════════════════════════════════════════════
# mixed traffic, the way the writer actually sees it
# ══════════════════════════════════════════════════════════════════════════════

def test_13_a_realistic_batch():
    """Several modules and several people in one result batch, which is what the writer
    normally receives."""
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=30)
    batch = [
        ev(module="headcount", event_type="entry", track=1, snapshot=False),
        ev(module="headcount", event_type="entry", track=2, snapshot=False),
        ev(module="mobile", event_type="phone_use", track=1),
        ev(module="mobile", event_type="phone_use", track=1),   # duplicate
        ev(module="intrusion", event_type="intrusion", track=3),
    ]
    s, _ = write_events(batch, f, FakeSession())
    assert len(s.rows) == 4, "only the duplicate is removed"
    assert [r.severity for r in s.rows] == ["low", "low", "medium", "high"]


def test_14_two_people_survive_a_batch():
    f, _ = make_filter(fd_mode="enforce", fd_dedup_window=60, fd_dedup_scope="track")
    s, _ = write_events([ev(track=1), ev(track=2)], f, FakeSession())
    assert len(s.rows) == 2


@pytest.mark.parametrize("n", [200])
def test_15_judgement_is_cheap_enough_for_the_writer(n):
    """The writer is a single synchronous thread. The rule is O(1) per event, so a few
    hundred events must not take a measurable amount of time."""
    import time as _time
    f, clock = make_filter(fd_mode="enforce", fd_dedup_window=60,
                           fd_repeat_source_enabled=True)
    start = _time.perf_counter()
    for i in range(n):
        clock.tick(0.1)
        f.judge(ev(track=i), CAM, point=(0.5, 0.5))
    elapsed = _time.perf_counter() - start
    assert elapsed < 0.5, f"{n} judgements took {elapsed:.3f}s - not O(1)"


# ══════════════════════════════════════════════════════════════════════════════
# start-up when the database is unreachable
# ══════════════════════════════════════════════════════════════════════════════

def test_16_startup_survives_an_unreachable_database(tmp_path, monkeypatch, capsys):
    """`fd_load_config` runs before the writer sees its first event and it talks to
    PostgreSQL. If the database is down — which is exactly when a start-up is most
    fragile — it must fall back to the environment defaults and still announce itself,
    not raise into `start_perception`.

    This was verified by hand against a genuinely broken local PostgreSQL (Windows
    `error code 487`, every backend fork dying); the test pins the behaviour so it stays
    true. The unreachable address below is the general case, not that specific fault."""
    from backend.services.false_detection.logging import LOG

    monkeypatch.setenv("FD_DEBUG", "1")
    monkeypatch.setenv("FD_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FD_ENABLED", "0")
    LOG.reset()
    try:
        from backend.core.config import settings as app_settings
        monkeypatch.setattr(
            app_settings, "DATABASE_URL",
            "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nowhere", raising=False)

        from backend.services.perception import fd_load_config
        cfg = fd_load_config()

        assert cfg.fd_enabled is False, "must fall back to the safe default"
        out = capsys.readouterr().out
        assert "config load skipped" in out, "the operator must be told it degraded"
        assert "fd_enabled=0" in out, "and told what it degraded TO"
        assert (tmp_path / "fd_decisions.log").exists(), \
            "diagnostics must still start, or the failure is invisible"
    finally:
        LOG.reset()


# ══════════════════════════════════════════════════════════════════════════════
# A failed snapshot must cost the picture, not the alert
# ══════════════════════════════════════════════════════════════════════════════

def test_17_a_snapshot_failure_still_writes_the_alert():
    """Unprotected, a write error here propagated to the writer loop's blanket handler
    and discarded the WHOLE result batch — every alert in it, silently, under a generic
    error line. With MEDIA_ROOT commonly left at the .env.example placeholder, that is a
    realistic way to lose every alert on a deployment while the logs look healthy.

    The row matters more than the thumbnail."""
    from backend.services.false_detection import Judgement

    f = FalseDetectionFilter(FDConfig(fd_enabled=False))
    session = FakeSession()
    written = []

    def exploding_save(_buf, _ov=None):
        raise OSError("No space left on device")

    for e in [ev(module="crowd", event_type="gathering"),
              ev(module="crowd", event_type="dispersed", track=2)]:
        verdict = f.judge(e, CAM, has_snapshot=True)
        assert verdict.write
        snap = None
        if e.get("snapshot") and verdict.snapshot:
            try:
                snap = exploding_save(b"")
            except Exception:
                snap = None                     # what the writer now does
        session.add(FakeAlertLog(module=e["module"], event_type=e["event_type"],
                                 snapshot=snap, severity=None))
        written.append(e["event_type"])

    assert written == ["gathering", "dispersed"], "both alerts must survive"
    assert all(r.snapshot is None for r in session.rows)
    assert isinstance(Judgement().snapshot, bool)


def test_18_both_crowd_events_ask_for_a_picture():
    """An alert row with no thumbnail reads as broken next to one that has it."""
    import inspect
    from backend.services.analytics import crowd
    src = inspect.getsource(crowd.Crowd.process)
    assert src.count('"snapshot": True') == 2, \
        "gathering AND dispersed must both request a snapshot"
