"""search_alerts must hand the model a real snapshot link for every alert.

Regression: the prompt asks for a "Snapshot" column in alert tables, but search_alerts returned no
URL, so the model invented one from the alert id (/alerts/215 = the dashboard page, not the image).
In-memory SQLite, no Postgres. Needs `aiosqlite` (dev only):  pip install aiosqlite"""
import asyncio
import json
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("langgraph")
from langchain_core.messages import AIMessage, ToolMessage                    # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine    # noqa: E402

import backend.models.camera as camera_model                                  # noqa: E402,F401
from backend.agent import agent as agent_mod                                  # noqa: E402
from backend.agent.tools import alert_tools                                   # noqa: E402
from backend.core.database import Base                                        # noqa: E402
from backend.models.alert import AlertLog                                     # noqa: E402
from tests.agent.test_graph import RecordingModel, _tool_calls                # noqa: E402

T0 = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def sess(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    maker = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(alert_tools, "AsyncSessionLocal", maker)
    # the real alerts router drags in the whole camera stack; the filters are not under test here
    fake_router = types.SimpleNamespace(_filtered=lambda stmt, *a: stmt, _emp_name=lambda uid: "Ali")
    monkeypatch.setattr(alert_tools, "_alerts_router", lambda: fake_router)

    async def init():
        async with eng.begin() as c:
            await c.run_sync(lambda cn: Base.metadata.create_all(
                cn, tables=[camera_model.CameraDevice.__table__, AlertLog.__table__]))
        async with maker() as db:
            for i, snap in enumerate(["snapshots/a_17ffe969.jpg",      # normal
                                      "snapshots\\b_2.jpg",            # row written on Windows
                                      None, ""]):                      # no snapshot
                db.add(AlertLog(violation_type="over_capacity", snapshot=snap, module="occupancy",
                                timestamp=T0.replace(minute=i)))
            await db.commit()
    asyncio.run(init())
    return maker


def test_every_alert_carries_a_media_link_or_null(sess):
    out = asyncio.run(alert_tools.search_alerts({"limit": 10}))
    by_snap = {a["snapshot"] for a in out["alerts"]}
    assert by_snap == {"/media/snapshots/a_17ffe969.jpg", "/media/snapshots/b_2.jpg", None}
    assert all("\\" not in (a["snapshot"] or "") for a in out["alerts"])      # no Windows separators
    assert sum(a["snapshot"] is None for a in out["alerts"]) == 2             # None and "" -> null


def test_the_link_reaches_the_model_through_the_graph(sess, monkeypatch):
    """What Gemini actually reads after calling search_alerts now contains the URL to cite."""
    model = RecordingModel(calls=[], script=[_tool_calls(("search_alerts", {}, "t1")),
                                             AIMessage(content="ok")])
    monkeypatch.setattr(agent_mod, "_get_model", lambda: model)
    asyncio.run(agent_mod.run_agent([], "aaj ke alerts?"))

    told = next(m for m in model.calls[1] if isinstance(m, ToolMessage))
    alerts = json.loads(told.content)["alerts"]
    assert "/media/snapshots/a_17ffe969.jpg" in {a["snapshot"] for a in alerts}


def test_prompt_forbids_guessing_links():
    prompt = (Path(agent_mod.__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8")
    assert "Never build or guess a link" in prompt and "/media/" in prompt


def test_search_alerts_schema_tells_the_model_it_has_links():
    schema = next(t for t in alert_tools.SCHEMAS if t["name"] == "search_alerts")
    assert "snapshot link" in schema["description"]
