"""Chat agent: an explicit two-node LangGraph over a one-field state.

    START -> model --(tool calls?)--> tools
               ^  |                     |
               |  +--(none)--> END      |
               +------------------------+

State is `MessagesState` and nothing else: the conversation, merged by the `add_messages`
reducer. Everything else a turn produces rides on a message instead of becoming graph state:

    sources     -> ToolMessage.artifact   (carried for the UI, never shown to the model)
    tool names  -> ToolMessage.name
    the answer  -> the last AIMessage

The graph is compiled once at import. Today's date goes into the system prompt inside the model
node, at call time, so nothing has to be rebuilt per request and the prompt is never stored in state.
"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from backend.agent.lc_tools import TOOLS
from backend.agent.llm import make_gemini
from backend.core.config import settings

# Settings ignores keys it does not declare, so this is what exports LANGCHAIN_* / LangSmith
# variables from .env into os.environ. Removing it silently turns tracing off.
load_dotenv()

TZ = ZoneInfo("Asia/Karachi")
MAX_TOOL_ROUNDS = 6
# One round = model step + tools step; +1 for the final model call that writes the answer.
RECURSION_LIMIT = 2 * MAX_TOOL_ROUNDS + 1
_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8")


class AgentReply(TypedDict):
    reply: str
    sources: list[str]
    tools_used: list[str]


@lru_cache(maxsize=1)
def _get_model() -> BaseChatModel:
    return make_gemini(settings.GEMINI_MODEL, max_tokens=4096, timeout=300)


def _system_message() -> SystemMessage:
    today = datetime.now(TZ).strftime("%A %Y-%m-%d")
    return SystemMessage(_PROMPT.replace("{today}", today))


# ── graph ────────────────────────────────────────────────────────────────────
async def call_model(state: MessagesState) -> dict[str, list[AnyMessage]]:
    # _get_model() is resolved here, at call time, so tests can swap the model after import.
    model = _get_model().bind_tools(TOOLS)
    
    # Limit history to prevent massive payloads that cause 504 Timeouts
    messages = state["messages"]
    if len(messages) > 15:
        messages = messages[-15:]
        
        # Prevent API errors by removing orphaned tool outputs at the slice boundary
        while messages and isinstance(messages[0], ToolMessage):
            messages.pop(0)

    # Force LangGraph to respect the 120-second timeout configuration
    return {
        "messages": [
            await model.ainvoke(
                [_system_message()] + messages,
                config={"timeout": 120.0}
            )
        ]
    }

def _build_graph() -> CompiledStateGraph[MessagesState, None, MessagesState, MessagesState]:
    g = StateGraph(MessagesState)
    g.add_node("model", call_model)
    g.add_node("tools", ToolNode(TOOLS))          # runs every tool call of the last AIMessage
    g.add_edge(START, "model")
    g.add_conditional_edges("model", tools_condition)     # tool calls -> "tools", else END
    g.add_edge("tools", "model")
    return g.compile()


GRAPH = _build_graph()


# ── entry point ──────────────────────────────────────────────────────────────
def _reply(text: str, sources: Sequence[str] = (), tools: Sequence[str] = ()) -> AgentReply:
    return {"reply": text, "sources": list(dict.fromkeys(sources)), "tools_used": list(tools)}


async def run_agent(history: Sequence[Mapping[str, Any]], message: str) -> AgentReply:
    """history: plain-text turns [{"role": "user"|"assistant", "content": str}]."""
    inputs: list[AnyMessage] = [
        (HumanMessage if t["role"] == "user" else AIMessage)(content=t["content"]) for t in history]
    inputs.append(HumanMessage(content=message))

    t0 = time.perf_counter()
    try:
        state = await GRAPH.ainvoke({"messages": inputs}, {"recursion_limit": RECURSION_LIMIT})
    except GraphRecursionError:
        return _reply("The question was too complex - please ask again more specifically.")
    except Exception as e:
        # Timing matters here: a failure after ~90 s means the model/network was too slow.
        print(f"[AGENT] failed after {time.perf_counter() - t0:.1f}s: {type(e).__name__}", flush=True)
        raise

    turn = state["messages"][len(inputs):]                # only what THIS turn added
    tool_msgs = [m for m in turn if isinstance(m, ToolMessage)]
    sources = [str(s) for m in tool_msgs if isinstance(m.artifact, list) for s in m.artifact]
    tools = [m.name for m in tool_msgs if m.name]
    print(f"[AGENT] done in {time.perf_counter() - t0:.1f}s | "
          f"model calls={sum(isinstance(m, AIMessage) for m in turn)} | tools={tools}", flush=True)

    final = next((m for m in reversed(turn) if isinstance(m, AIMessage)), None)
    text = final.text.strip() if final is not None else ""
    return _reply(text or "(Empty response received, please try again.)", sources, tools)
