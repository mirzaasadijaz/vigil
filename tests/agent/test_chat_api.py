"""HTTP contract of /chat that the React client (frontend/src/lib/chatApi.ts) relies on:
POST -> {reply, sources}; 503 when no key; 502 when the agent dies; history keeps sources.

Real FastAPI router + real LangGraph + a scripted model: no key, no DB, no network.
Skipped where fastapi / httpx / langgraph are not installed."""
import pytest

pytest.importorskip("langgraph")
pytest.importorskip("httpx")
from fastapi import FastAPI                                                   # noqa: E402
from fastapi.testclient import TestClient                                     # noqa: E402
from langchain_core.messages import AIMessage                                 # noqa: E402

from backend.agent import agent as agent_mod                                  # noqa: E402
from backend.agent.memory import session_store                                # noqa: E402
from backend.agent.tools import registry                                      # noqa: E402
from backend.core.config import settings                                      # noqa: E402
from backend.core.dependencies import get_current_user                        # noqa: E402
from backend.routers import chat                                              # noqa: E402
from tests.agent.test_graph import RecordingModel, _tool_calls                # noqa: E402


class _User:
    id = 42


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "DIR", tmp_path)                       # history files -> tmp
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: _User()
    return TestClient(app)


def test_chat_roundtrip_returns_reply_and_sources_and_persists_them(client, monkeypatch):
    async def knowledge(_):
        return {"found": 1, "sources": ["sop/intrusion.md"]}

    monkeypatch.setitem(registry.HANDLERS, "search_knowledge", knowledge)
    model = RecordingModel(calls=[], script=[
        _tool_calls(("search_knowledge", {"query": "intrusion"}, "t1")),
        AIMessage(content="Supervisor ko call karein.")])
    monkeypatch.setattr(agent_mod, "_get_model", lambda: model)

    r = client.post("/api/chat/", json={"message": "intrusion par kya karein?"})
    assert r.status_code == 200
    assert r.json() == {"reply": "Supervisor ko call karein.", "sources": ["sop/intrusion.md"]}

    turns = client.get("/api/chat/history").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["sources"] == ["sop/intrusion.md"]                        # shown after a page reload

    assert client.delete("/api/chat/history").json() == {"ok": True}
    assert client.get("/api/chat/history").json() == {"turns": []}


def test_second_message_carries_the_stored_history_into_the_graph(client, monkeypatch):
    model = RecordingModel(calls=[], script=[AIMessage(content="one"), AIMessage(content="two")])
    monkeypatch.setattr(agent_mod, "_get_model", lambda: model)
    client.post("/api/chat/", json={"message": "first"})
    client.post("/api/chat/", json={"message": "second"})
    seen = [m.content for m in model.calls[1][1:]]                            # skip the system prompt
    assert seen == ["first", "one", "second"]


def test_503_when_no_api_key(client, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    assert client.post("/api/chat/", json={"message": "hi"}).status_code == 503


def test_502_when_the_agent_fails(client, monkeypatch):
    model = RecordingModel(calls=[], script=[])                               # empty script -> model blows up
    monkeypatch.setattr(agent_mod, "_get_model", lambda: model)
    assert client.post("/api/chat/", json={"message": "hi"}).status_code == 502


def test_422_on_empty_or_oversized_message(client):
    assert client.post("/api/chat/", json={"message": ""}).status_code == 422
    assert client.post("/api/chat/", json={"message": "x" * 2001}).status_code == 422
