"""
Intrusion: diagnostics were added, behaviour was not changed.

This module was already working in production, so the point of these tests is to prove
the logging did not alter a single decision. They pin the ORIGINAL semantics — centroid
membership, a fixed 10-second cooldown, one event carrying the count — against a
reference implementation of the pre-logging logic.

Run:  pytest tests/test_intrusion_logging.py -q
"""

from __future__ import annotations

import pytest

from backend.services.analytics.base import in_any_zone
from backend.services.analytics.intrusion import Intrusion

SHAPE = (720, 1280)
H, W = SHAPE
RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]


@pytest.fixture(autouse=True)
def _isolate_cooldowns():
    """Clear the cooldown state shared by every Analytic instance.

    `Analytic._cooldown` keeps its timestamps in a mutable default argument keyed by
    `(id(self), key)`. That dict is shared process-wide, and CPython reuses object
    addresses after garbage collection — so a freshly built consumer can inherit a DEAD
    one's timestamp and silently skip its own first alert.

    Here it would make results depend on allocation order between tests. This fixture
    isolates them from it; it does NOT fix it. The shared dict is pre-existing behaviour
    in base.py and outside the scope of adding a log to this module.
    """
    from backend.services.analytics.base import Analytic
    Analytic._cooldown.__defaults__[0].clear()
    yield
    Analytic._cooldown.__defaults__[0].clear()


def person(tid, nx, ny, h=0.25):
    ph = h * H
    pw = ph * 0.4
    cx, foot = nx * W, ny * H
    return {"id": tid, "box": [cx - pw / 2, foot - ph, cx + pw / 2, foot],
            "centroid": (cx, foot - ph / 2)}


def reference_intruders(tracks, zones):
    """The membership test exactly as it was before logging was added."""
    return [t for t in tracks
            if in_any_zone(t["centroid"][0] / W, t["centroid"][1] / H, zones)]


# ══════════════════════════════════════════════════════════════════════════════
# behaviour is unchanged
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("nx,ny", [
    (0.75, 0.80), (0.20, 0.80), (0.51, 0.50), (0.49, 0.50), (0.99, 0.10),
])
def test_membership_matches_the_pre_logging_logic(nx, ny):
    """Every position must be judged exactly as the original judged it."""
    t = person(1, nx, ny)
    got, _ = Intrusion(1, {"zones": [RIGHT_HALF]}).process([t], [], SHAPE)
    assert bool(got) is bool(reference_intruders([t], [RIGHT_HALF]))


def test_membership_is_still_the_centroid_not_the_feet():
    """Recorded deliberately: this module tests the box centroid, which sits at chest
    height. Anyone changing that to the foot point is changing behaviour, and this test
    should make them do it on purpose rather than by accident."""
    bottom = [[0.0, 0.5], [1.0, 0.5], [1.0, 1.0], [0.0, 1.0]]
    p = person(1, 0.5, 0.55, h=0.40)          # feet inside the zone, centroid above it
    assert Intrusion(1, {"zones": [bottom]}).process([p], [], SHAPE)[0] == []


def test_several_intruders_are_one_event_carrying_the_count():
    c = Intrusion(1, {"zones": [RIGHT_HALF]})
    evs, _ = c.process([person(1, 0.70, 0.8), person(2, 0.85, 0.8)], [], SHAPE)
    assert len(evs) == 1
    assert evs[0]["value"] == 2
    assert sorted(evs[0]["data"]["tracks"]) == [1, 2]
    assert evs[0]["snapshot"] is True


def test_the_ten_second_cooldown_still_applies():
    c = Intrusion(1, {"zones": [RIGHT_HALF]})
    assert len(c.process([person(1, 0.75, 0.8)], [], SHAPE)[0]) == 1
    assert c.process([person(1, 0.75, 0.8)], [], SHAPE)[0] == []


def test_an_empty_zone_does_not_consume_the_cooldown():
    """The original was `if intruders and self._cooldown(...)`. Splitting that into
    nested ifs to log the suppressed case must preserve the short-circuit: frames with
    nobody in the zone must not start the timer, or the first real intrusion would be
    swallowed."""
    c = Intrusion(1, {"zones": [RIGHT_HALF]})
    for _ in range(5):
        c.process([person(1, 0.10, 0.8)], [], SHAPE)      # nobody in the zone
    assert len(c.process([person(1, 0.75, 0.8)], [], SHAPE)[0]) == 1


def test_no_zone_is_inert():
    assert Intrusion(1, {"zones": []}).process([person(1, 0.5, 0.8)], [], SHAPE) == ([], [])


def test_a_disarmed_schedule_blocks_alerts_but_still_draws_the_zone(monkeypatch):
    import backend.services.analytics.intrusion as mod
    monkeypatch.setattr(mod, "within_schedule", lambda _s: False)
    c = Intrusion(1, {"zones": [RIGHT_HALF],
                      "schedule": {"start": "09:00", "end": "17:00"}})
    evs, ovs = c.process([person(1, 0.75, 0.8)], [], SHAPE)
    assert evs == []
    assert any(o.get("kind") == "zone" for o in ovs)


def test_overlays_are_unchanged():
    c = Intrusion(1, {"zones": [RIGHT_HALF]})
    _, ovs = c.process([person(1, 0.75, 0.8)], [], SHAPE)
    assert [o for o in ovs if o.get("label") == "RESTRICTED"]
    assert [o for o in ovs if o.get("label") == "INTRUSION"]


# ══════════════════════════════════════════════════════════════════════════════
# the logging itself
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def logged(tmp_path, monkeypatch):
    from backend.services.analytics import intrusion_debug
    monkeypatch.setenv("INTRUSION_DEBUG", "trace")
    monkeypatch.setenv("INTRUSION_DEBUG_LOG", str(tmp_path / "i.log"))
    intrusion_debug.reset()
    yield tmp_path / "i.log"
    intrusion_debug.reset()


def test_the_log_records_an_alert_and_its_suppression(logged):
    c = Intrusion(7, {"zones": [RIGHT_HALF]})
    c.process([person(1, 0.75, 0.8)], [], SHAPE)
    c.process([person(1, 0.75, 0.8)], [], SHAPE)          # inside the cooldown
    text = logged.read_text(encoding="utf-8")
    assert "event=SESSION" in text, "the resolved config must be recorded"
    assert "event=INTRUSION" in text and "verdict=ALERT" in text
    assert "verdict=COOLDOWN" in text, "a suppressed alert must be visible"
    assert "event=OBS" in text and "point=" in text


def test_creation_is_logged_before_any_frame(logged):
    """Separates 'not assigned to this camera' from 'assigned and silent' — an ambiguity
    that cost a round trip when diagnosing the trip wire."""
    Intrusion(9, {"zones": [RIGHT_HALF]})
    assert "event=CREATED" in logged.read_text(encoding="utf-8")


def test_a_camera_with_no_zone_says_so(logged):
    """A zone nobody drew is the most likely reason for silence."""
    Intrusion(11, {"zones": []}).process([person(1, 0.5, 0.8)], [], SHAPE)
    assert "verdict=NO_ZONE" in logged.read_text(encoding="utf-8")


def test_logging_is_off_by_default(tmp_path, monkeypatch):
    """It must stay completely inert unless explicitly switched on."""
    from backend.services.analytics import intrusion_debug
    monkeypatch.delenv("INTRUSION_DEBUG", raising=False)
    monkeypatch.setenv("INTRUSION_DEBUG_LOG", str(tmp_path / "off.log"))
    intrusion_debug.reset()
    try:
        Intrusion(1, {"zones": [RIGHT_HALF]}).process([person(1, 0.75, 0.8)], [], SHAPE)
        assert not (tmp_path / "off.log").exists()
    finally:
        intrusion_debug.reset()
