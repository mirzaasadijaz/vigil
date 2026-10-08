"""
API tests for the false-detection settings endpoints.

This laptop has no PostgreSQL, so the database session is replaced with an in-memory
fake through FastAPI's dependency override. That still exercises the real router, the
real validation path and the real auth guard — which is where the bugs would be. What it
does not exercise is SQL, and the migration is verified separately by generating its
DDL offline.

Run:  pytest tests/test_false_detection_api.py -q
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.core.dependencies import get_current_user, require_role
from backend.core.database import get_db
from backend.main import app
from backend.models.user import UserRole


class FakeSettingsRow:
    def __init__(self):
        self.id = 1
        self.false_detection = None


class FakeResult:
    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return self._rows


class FakeDB:
    """Enough of AsyncSession for this router: one settings row and one aggregate."""

    def __init__(self, row, severity_rows=None):
        self.row = row
        self.severity_rows = severity_rows or []
        self.commits = 0

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "group by" in text:
            return FakeResult(rows=self.severity_rows)
        return FakeResult(value=self.row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return obj

    def add(self, obj):
        self.row = obj


class FakeUser:
    id = 1
    username = "admin"
    role = UserRole.admin
    is_active = True


@pytest.fixture
def client():
    row = FakeSettingsRow()
    db = FakeDB(row, severity_rows=[("critical", 2), ("medium", 40), (None, 5)])

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: FakeUser()
    # require_role returns a fresh dependency per call, so override the factory's
    # products by overriding the underlying check for each role combination used here.
    for roles in ((UserRole.admin,), (UserRole.admin, UserRole.supervisor)):
        app.dependency_overrides[require_role(*roles)] = lambda: FakeUser()

    # Deliberately NOT `with TestClient(app)`: the context manager runs the app's
    # lifespan, which creates database tables and SPAWNS THE PERCEPTION PROCESS. Neither
    # is wanted here, and on a machine without PostgreSQL the lifespan blocks for two
    # minutes before failing. The router under test needs neither.
    c = TestClient(app)
    c.fake_db = db
    yield c
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════

def test_01_get_returns_config_defaults_and_counters(client):
    r = client.get("/api/settings/false-detection")
    assert r.status_code == 200
    body = r.json()
    assert "config" in body and "defaults" in body and "counters" in body
    assert body["config"]["fd_enabled"] is False, "must ship disabled"
    assert body["config"]["fd_mode"] == "observe", "must ship in observe"


def test_02_counters_summarise_the_last_24h(client):
    body = client.get("/api/settings/false-detection").json()
    c = body["counters"]
    assert c["window_hours"] == 24
    assert c["alerts_written"] == 47
    assert c["by_severity"]["critical"] == 2
    assert c["by_severity"]["unclassified"] == 5, \
        "rows written before the layer existed must be labelled, not dropped"


def test_03_every_parameter_is_exposed(client):
    """The admin panel cannot offer a control for a parameter the API does not return."""
    from backend.services.false_detection import FDConfig
    body = client.get("/api/settings/false-detection").json()
    for field in FDConfig().to_dict():
        assert field in body["config"], f"{field} missing from the API response"


def test_04_put_persists_and_round_trips(client):
    r = client.put("/api/settings/false-detection",
                   json={"fd_enabled": True, "fd_dedup_window": 90})
    assert r.status_code == 200
    stored = json.loads(client.fake_db.row.false_detection)
    assert stored["fd_enabled"] is True
    assert stored["fd_dedup_window"] == 90

    back = client.get("/api/settings/false-detection").json()["config"]
    assert back["fd_enabled"] is True and back["fd_dedup_window"] == 90


def test_05_invalid_values_are_clamped_not_half_applied(client):
    """A bad stored value would otherwise reach the single DB writer thread."""
    r = client.put("/api/settings/false-detection",
                   json={"fd_mode": "nonsense", "fd_min_confidence": 7,
                         "fd_dedup_scope": "sideways"})
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["fd_mode"] == "observe", "an unknown mode must fall back to the safe one"
    assert cfg["fd_min_confidence"] == 1.0
    assert cfg["fd_dedup_scope"] == "track"


def test_06_unknown_fields_are_dropped(client):
    """A payload from a newer build must not corrupt an older one."""
    r = client.put("/api/settings/false-detection",
                   json={"fd_enabled": True, "fd_from_the_future": 123})
    assert r.status_code == 200
    assert "fd_from_the_future" not in r.json()["config"]


def test_07_a_non_object_payload_is_rejected(client):
    assert client.put("/api/settings/false-detection", json=[1, 2, 3]).status_code == 400
    assert client.put("/api/settings/false-detection",
                      content=b"not json").status_code == 400


def test_08_partial_update_keeps_the_rest(client):
    client.put("/api/settings/false-detection",
               json={"fd_enabled": True, "fd_dedup_window": 90})
    client.put("/api/settings/false-detection", json={"fd_dedup_window": 30})
    cfg = client.get("/api/settings/false-detection").json()["config"]
    assert cfg["fd_dedup_window"] == 30
    assert cfg["fd_enabled"] is True, "an unrelated field must survive a partial update"


def test_09_saving_applies_immediately(client):
    """Live reload is also the fastest rollback there is, so it has to work."""
    from backend.services.false_detection.engine import FILTER
    client.put("/api/settings/false-detection",
               json={"fd_enabled": True, "fd_dedup_window": 42})
    assert FILTER.config.fd_enabled is True
    assert FILTER.config.fd_dedup_window == 42
    client.put("/api/settings/false-detection", json={"fd_enabled": False})
    assert FILTER.config.fd_enabled is False


def test_10_changes_are_audited(client, tmp_path, monkeypatch):
    from backend.services.false_detection.logging import LOG
    monkeypatch.setenv("FD_DEBUG", "1")
    monkeypatch.setenv("FD_LOG_DIR", str(tmp_path))
    LOG.reset()

    client.put("/api/settings/false-detection", json={"fd_dedup_window": 77})
    text = (tmp_path / "fd_config.log").read_text(encoding="utf-8")
    assert "CONFIG_CHANGE" in text
    assert "changed=fd_dedup_window" in text
    assert "after=77" in text
    assert "actor=admin" in text, "who changed it is part of the audit trail"
    LOG.reset()


def test_11_zones_survive_a_round_trip(client):
    zone = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]
    client.put("/api/settings/false-detection", json={"fd_ignore_zones": [zone]})
    cfg = client.get("/api/settings/false-detection").json()["config"]
    assert cfg["fd_ignore_zones"] == [zone]


def test_12_severity_map_survives_a_round_trip(client):
    client.put("/api/settings/false-detection",
               json={"fd_severity_map": {"phone_use": "high"}})
    cfg = client.get("/api/settings/false-detection").json()["config"]
    assert cfg["fd_severity_map"]["phone_use"] == "high"


def test_13_get_requires_authentication():
    """No override installed: the real auth guard must refuse."""
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/settings/false-detection").status_code == 401
