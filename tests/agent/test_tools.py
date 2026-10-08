"""Agent-side tests: registry, session store, and the tool-calling loop with a scripted fake model."""
import asyncio
import pytest

from backend.agent import agent as agent_mod
from backend.agent.memory import session_store
from backend.agent.tools import registry


def test_registry_is_consistent():
    names = [t["name"] for t in registry.TOOLS]
    assert set(names) == {"search_alerts", "count_alerts", "list_cameras", "search_knowledge",
                           "search_snapshots"}
    for t in registry.TOOLS:
        assert t["input_schema"]["type"] == "object" and t["description"]


def test_unknown_tool_and_bad_args_do_not_raise(monkeypatch):
    assert "error" in asyncio.run(registry.run_tool("nope", {}))

    async def boom(_): raise ValueError("bad date")
    monkeypatch.setitem(registry.HANDLERS, "list_cameras", boom)
    assert "bad argument" in asyncio.run(registry.run_tool("list_cameras", {}))["error"]


def test_session_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "DIR", tmp_path)

    async def go():
        await session_store.append(7, "hi", "hello")
        await session_store.append(7, "q2", "a2")
        h = await session_store.load(7)
        assert [t["role"] for t in h] == ["user", "assistant"] * 2
        assert (await session_store.load(8)) == []
        await session_store.clear(7)
        assert (await session_store.load(7)) == []
    asyncio.run(go())


def test_session_store_keeps_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "DIR", tmp_path)

    async def go():
        await session_store.append(1, "q", "a", ["sop/x.md"])
        await session_store.append(1, "q2", "a2")                  # no sources -> no key
        h = await session_store.load(1)
        assert h[1]["sources"] == ["sop/x.md"] and "sources" not in h[3]
    asyncio.run(go())


# --- agent loop (needs langchain; skipped where only requirements.txt is installed) ----------
pytest.importorskip("langchain")
from langchain_core.language_models.chat_models import BaseChatModel          # noqa: E402
from langchain_core.messages import AIMessage                                 # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult                 # noqa: E402


class ScriptedModel(BaseChatModel):
    """Returns the scripted AIMessages in order; ignores the prompt."""
    script: list
    i: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.script[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _call(name, args, id_="t1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id_}])


def _run(monkeypatch, script, handlers=None, message="intrusion par kya karein?"):
    for k, fn in (handlers or {}).items():
        monkeypatch.setitem(registry.HANDLERS, k, fn)
    monkeypatch.setattr(agent_mod, "_get_model", lambda: ScriptedModel(script=script))
    return asyncio.run(agent_mod.run_agent([], message))


def test_agent_loop_calls_tool_then_answers(monkeypatch):
    seen = {}

    async def fake_knowledge(args):
        seen["args"] = args
        return {"found": 1, "passages": [{"source": "sop/x.md", "text": "call supervisor"}],
                "sources": ["sop/x.md"]}

    out = _run(monkeypatch,
               [_call("search_knowledge", {"query": "intrusion"}),
                AIMessage(content="Supervisor ko call karein.")],
               {"search_knowledge": fake_knowledge})
    assert out["reply"] == "Supervisor ko call karein."
    assert out["sources"] == ["sop/x.md"] and out["tools_used"] == ["search_knowledge"]
    assert seen["args"] == {"query": "intrusion"}


def test_agent_handles_gemini_block_list_content(monkeypatch):
    """Gemini 2.5 can return content as a list of blocks; thinking blocks must be dropped."""
    out = _run(monkeypatch, [AIMessage(content=[
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "Aaj "}, {"type": "text", "text": "3 alerts hain."}])])
    assert out["reply"] == "Aaj 3 alerts hain."


def test_agent_stops_runaway_tool_loop(monkeypatch):
    async def cams(_): return {"cameras": []}
    loop = [_call("list_cameras", {}, f"t{i}") for i in range(40)]
    out = _run(monkeypatch, loop, {"list_cameras": cams})
    assert "too complex" in out["reply"]