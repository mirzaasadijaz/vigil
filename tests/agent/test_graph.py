"""The explicit LangGraph in backend/agent/agent.py: state, topology and per-turn behaviour.

A scripted model stands in for Gemini, so these run offline (no key, no DB).
Skipped where only requirements.txt is installed (needs langgraph)."""
import asyncio
from datetime import datetime

import pytest

pytest.importorskip("langgraph")
from langchain_core.language_models.chat_models import BaseChatModel          # noqa: E402
from langchain_core.messages import (                                         # noqa: E402
    AIMessage, HumanMessage, SystemMessage, ToolMessage)
from langchain_core.outputs import ChatGeneration, ChatResult                 # noqa: E402

from backend.agent import agent as agent_mod                                  # noqa: E402
from backend.agent import lc_tools                                            # noqa: E402
from backend.agent.tools import registry                                      # noqa: E402


class RecordingModel(BaseChatModel):
    """Plays the scripted AIMessages in order and remembers what it was shown on each call."""
    script: list
    calls: list

    @property
    def _llm_type(self) -> str:
        return "recording"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.script[len(self.calls) - 1])])


def _tool_calls(*calls):
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": i} for n, a, i in calls])


def _use(monkeypatch, *script) -> RecordingModel:
    model = RecordingModel(script=list(script), calls=[])
    monkeypatch.setattr(agent_mod, "_get_model", lambda: model)
    return model


# ── state & topology ────────────────────────────────────────────────────────
def test_state_is_messages_only():
    g = agent_mod.GRAPH
    assert set(g.get_input_jsonschema()["properties"]) == {"messages"}
    assert set(g.get_output_jsonschema()["properties"]) == {"messages"}
    # create_agent() used to add these two channels; nothing in this app ever reads them
    assert not {"jump_to", "structured_response"} & set(g.channels)


def test_topology_is_a_model_tools_loop():
    g = agent_mod.GRAPH.get_graph()
    assert set(g.nodes) == {"__start__", "model", "tools", "__end__"}
    assert {(e.source, e.target) for e in g.edges} == {
        ("__start__", "model"), ("model", "tools"), ("model", "__end__"), ("tools", "model")}


# ── tools are built once, handlers are still resolved at call time ───────────
def test_tools_built_once_still_resolve_handlers_at_call_time(monkeypatch):
    by_name = {t.name: t for t in lc_tools.TOOLS}
    assert len(by_name) == len(registry.TOOLS) == 7

    async def patched(_):
        return {"cameras": ["patched"], "sources": ["cam-1"]}

    monkeypatch.setitem(registry.HANDLERS, "list_cameras", patched)       # patched AFTER import
    msg = asyncio.run(by_name["list_cameras"].ainvoke(
        {"name": "list_cameras", "args": {}, "id": "1", "type": "tool_call"}))
    assert "patched" in msg.content and msg.artifact == ["cam-1"]         # artifact = sources


# ── the model node ──────────────────────────────────────────────────────────
def test_system_prompt_is_added_at_call_time_and_never_stored_in_state(monkeypatch):
    model = _use(monkeypatch, AIMessage(content="hi"))
    fmt = "%A %Y-%m-%d"
    before = datetime.now(agent_mod.TZ).strftime(fmt)
    state = asyncio.run(agent_mod.GRAPH.ainvoke({"messages": [HumanMessage(content="hello")]}))
    after = datetime.now(agent_mod.TZ).strftime(fmt)

    first = model.calls[0][0]
    assert isinstance(first, SystemMessage)
    assert "{today}" not in first.content and (before in first.content or after in first.content)
    assert not any(isinstance(m, SystemMessage) for m in state["messages"])
    assert [type(m).__name__ for m in state["messages"]] == ["HumanMessage", "AIMessage"]


def test_history_is_replayed_as_plain_turns_and_not_returned(monkeypatch):
    model = _use(monkeypatch, AIMessage(content="ok"))
    history = [{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hello", "sources": ["s"]}]    # extra keys ignored
    out = asyncio.run(agent_mod.run_agent(history, "third"))

    seen = model.calls[0]
    assert [type(m).__name__ for m in seen] == ["SystemMessage", "HumanMessage", "AIMessage", "HumanMessage"]
    assert [m.content for m in seen[1:]] == ["hi", "hello", "third"]
    assert out == {"reply": "ok", "sources": [], "tools_used": []}            # nothing leaks from history


# ── the tools node ──────────────────────────────────────────────────────────
def test_parallel_tool_calls_run_concurrently_and_sources_dedupe_in_order(monkeypatch):
    async def go():
        second_started = asyncio.Event()

        async def first(_):                        # can only finish if `second` runs at the same time
            await asyncio.wait_for(second_started.wait(), timeout=2)
            return {"sources": ["sop/x.md", "sop/y.md"]}

        async def second(_):
            second_started.set()
            return {"sources": ["alert #5", "sop/x.md"]}

        monkeypatch.setitem(registry.HANDLERS, "search_knowledge", first)
        monkeypatch.setitem(registry.HANDLERS, "search_alerts", second)
        _use(monkeypatch,
             _tool_calls(("search_knowledge", {"query": "a"}, "p1"), ("search_alerts", {}, "p2")),
             AIMessage(content="done"))
        return await agent_mod.run_agent([], "both?")

    out = asyncio.run(go())
    assert out["sources"] == ["sop/x.md", "sop/y.md", "alert #5"]              # first-seen order, no dupes
    assert out["tools_used"] == ["search_knowledge", "search_alerts"]


def test_unknown_tool_is_reported_back_to_the_model_not_raised(monkeypatch):
    model = _use(monkeypatch, _tool_calls(("does_not_exist", {"x": 1}, "h1")), AIMessage(content="recovered"))
    out = asyncio.run(agent_mod.run_agent([], "?"))

    assert out["reply"] == "recovered" and out["tools_used"] == ["does_not_exist"]
    told = model.calls[1][-1]                                                  # what the model saw next
    assert isinstance(told, ToolMessage) and told.status == "error" and "search_alerts" in told.content


def test_recursion_budget_is_exactly_max_tool_rounds(monkeypatch):
    runs = []

    async def cams(_):
        runs.append(1)
        return {"cameras": []}

    monkeypatch.setitem(registry.HANDLERS, "list_cameras", cams)
    model = _use(monkeypatch, *[_tool_calls(("list_cameras", {}, f"t{i}")) for i in range(40)])
    out = asyncio.run(agent_mod.run_agent([], "loop"))

    assert "too complex" in out["reply"]
    assert len(runs) == agent_mod.MAX_TOOL_ROUNDS                  # 6 tool rounds ...
    assert len(model.calls) == agent_mod.MAX_TOOL_ROUNDS + 1       # ... and the 7th model call is refused


def test_empty_model_reply_gets_a_friendly_fallback(monkeypatch):
    _use(monkeypatch, AIMessage(content=""))
    out = asyncio.run(agent_mod.run_agent([], "hello"))
    assert out["reply"].startswith("(Empty response")
