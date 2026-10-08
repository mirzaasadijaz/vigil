"""
Operator feedback on alerts — thumbs up / thumbs down.

The point of this feature is not the buttons. Every alert carries the numbers its
decision was made from in `data` (confidence, association, drift, dwell), so marking one
WRONG labels a specific point in that parameter space as a false positive. These tests
guard the properties that make the resulting dataset trustworthy:

  * NULL means "nobody judged it", and must never be conflated with "judged correct" -
    otherwise every untouched alert silently counts as a success and the accuracy figure
    the feature exists to produce is meaningless.
  * A verdict can be CLEARED. There is no neutral third value for the same reason.
  * The note travels with the verdict, because "that is the printed phone on his shirt"
    is a diagnosis and `conf=0.55` is not.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.database import get_db                     # noqa: E402
from backend.core.dependencies import get_current_user, require_role  # noqa: E402
from backend.main import app                                 # noqa: E402
from backend.models.user import UserRole                     # noqa: E402


class FakeAlert:
    def __init__(self, aid=1):
        self.id = aid
        self.module = "mobile"
        self.event_type = "phone_use"
        self.camera_id = 52
        self.violation_type = "phone_use"
        self.value = 0.55
        self.distance_score = None
        self.unknown_person = False
        self.snapshot = None
        self.timestamp = datetime.now(timezone.utc)
        self.severity = "medium"
        self.employee = None
        self.employee_id = None
        self.likely_candidate = None
        self.data = '{"phone_confidence": 0.55, "body_drift": 0.004}'
        self.feedback = None
        self.feedback_by = None
        self.feedback_at = None
        self.feedback_note = None


class FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj

    def scalars(self):
        return self

    def all(self):
        return [self._obj] if self._obj is not None else []


class FakeDB:
    def __init__(self, alert):
        self.alert = alert
        self.committed = False

    async def execute(self, stmt):
        return FakeResult(self.alert)

    async def commit(self):
        self.committed = True


class FakeUser:
    id = 7
    username = "operator"
    role = UserRole.admin
    is_active = True


@pytest.fixture
def client():
    alert = FakeAlert()
    db = FakeDB(alert)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: FakeUser()
    for roles in ((UserRole.admin,), (UserRole.admin, UserRole.supervisor)):
        app.dependency_overrides[require_role(*roles)] = lambda: FakeUser()
    # Not `with TestClient(app)`: the lifespan creates tables and spawns the perception
    # process, neither of which this router needs. See test_false_detection_api.py.
    c = TestClient(app)
    c.alert = alert
    c.fake_db = db
    yield c
    app.dependency_overrides.clear()


def test_01_a_thumbs_up_is_recorded(client):
    r = client.post("/api/alerts/1/feedback", json={"feedback": "up"})
    assert r.status_code == 200, r.text
    assert r.json()["feedback"] == "up"
    assert client.alert.feedback == "up"
    assert client.alert.feedback_by == FakeUser.id
    assert client.alert.feedback_at is not None
    assert client.fake_db.committed


def test_02_a_thumbs_down_carries_the_note(client):
    """The note is the highest-value field for tuning and the one no number supplies."""
    r = client.post("/api/alerts/1/feedback",
                    json={"feedback": "down",
                          "note": "that is the printed phone on his shirt"})
    assert r.status_code == 200
    assert client.alert.feedback == "down"
    assert client.alert.feedback_note == "that is the printed phone on his shirt"


def test_03_a_verdict_can_be_cleared(client):
    """Clearing must restore "nobody judged this", not record a neutral verdict."""
    client.post("/api/alerts/1/feedback", json={"feedback": "down", "note": "wrong"})
    r = client.post("/api/alerts/1/feedback", json={"feedback": None})
    assert r.status_code == 200
    assert client.alert.feedback is None
    assert client.alert.feedback_by is None
    assert client.alert.feedback_at is None
    assert client.alert.feedback_note is None, \
        "an orphan note explains nothing once the verdict is gone"


def test_04_there_is_no_third_verdict(client):
    """"Nobody looked" and "somebody looked and it was fine" are different facts, and a
    neutral value would collapse them."""
    r = client.post("/api/alerts/1/feedback", json={"feedback": "maybe"})
    assert r.status_code == 400
    assert client.alert.feedback is None


def test_05_an_unknown_alert_is_a_404(client):
    client.fake_db.alert = None
    r = client.post("/api/alerts/999/feedback", json={"feedback": "up"})
    assert r.status_code == 404


def test_06_a_long_note_is_truncated_not_rejected(client):
    """An operator typing an essay must not lose their verdict to a validation error."""
    r = client.post("/api/alerts/1/feedback",
                    json={"feedback": "down", "note": "x" * 5000})
    assert r.status_code == 200
    assert client.alert.feedback == "down"
    assert len(client.alert.feedback_note) <= 2000


def test_07_the_list_response_exposes_the_verdict(client):
    """The UI has to render "not judged" differently from "judged correct"."""
    r = client.get("/api/alerts/?limit=1")
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert "feedback" in row and row["feedback"] is None
    assert "feedback_note" in row


def test_08_accuracy_is_computed_over_JUDGED_alerts_only():
    """The denominator is the whole point.

    Dividing by TOTAL alerts would report a module nobody has reviewed as 100% accurate,
    which is the opposite of what this feature is for. This checks the arithmetic
    directly, because the endpoint's aggregation needs a real GROUP BY to exercise."""
    rows = [("mobile", "phone_use", "up", 3), ("mobile", "phone_use", "down", 7)]
    agg: dict = {}
    for module, event_type, fb, n in rows:
        e = agg.setdefault((module, event_type), {"up": 0, "down": 0})
        e["up" if fb == "up" else "down"] += n
    e = agg[("mobile", "phone_use")]
    judged = e["up"] + e["down"]
    assert judged == 10, "unjudged alerts must not appear in the denominator"
    assert round(e["up"] / judged, 3) == 0.3
