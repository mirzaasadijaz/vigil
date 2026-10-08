"""Agent: LangChain `create_agent` (runs on LangGraph) + Gemini + our tools.

Flow handled by the graph:  model -> (tool calls?) -> tools -> model -> ... -> final answer.
We bound it with recursion_limit so a confused model cannot loop forever.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.errors import GraphRecursionError

from backend.agent.lc_tools import build_tools
from backend.core.config import settings
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("Asia/Karachi")
MAX_TOOL_ROUNDS = 6
_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8")
_model = None


def _get_model():
    global _model
    if _model is None:
        kwargs = dict(
            model=getattr(settings, "GEMINI_MODEL", "") or "gemini-3.5-flash",
            google_api_key=getattr(settings, "GEMINI_API_KEY", "") or None,
            max_output_tokens=4096,      # thinking models spend part of this on thinking
            max_retries=1,               # each retry can cost a full timeout
            timeout=90,
        )
        try:
            # Gemini 3.x defaults to medium thinking; "low" is plenty for picking tools and
            # summarising results, and is much faster.
            _model = ChatGoogleGenerativeAI(thinking_level="low", **kwargs)
        except (ValueError, TypeError) as e:
            # Older langchain-google-genai does not know thinking_level: run without it.
            print(f"[AGENT] thinking_level not supported ({e!r:.120}); "
                  f"run: pip install -U langchain-google-genai", flush=True)
            _model = ChatGoogleGenerativeAI(**kwargs)
    return _model


def _build_agent():
    # Built per request: the system prompt contains today's date, and compiling is cheap.
    system = _PROMPT.replace("{today}", datetime.now(TZ).strftime("%A %Y-%m-%d"))
    return create_agent(model=_get_model(), tools=build_tools(), system_prompt=system)


def _text(msg) -> str:
    """Plain text of an AIMessage. Gemini 2.5 (thinking) can return `content` as a list of
    blocks ({"type": "text"|"thinking", ...}) instead of a str - keep only the text blocks."""
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(
            b if isinstance(b, str) else (b.get("text") or "")
            for b in c
            if isinstance(b, str) or (isinstance(b, dict) and b.get("type", "text") == "text"))
    return str(c or "")


async def run_agent(history: list[dict], message: str) -> dict:
    """history: [{"role","content"}] plain-text turns. Returns {"reply", "sources", "tools_used"}."""
    inputs = [(HumanMessage if h["role"] == "user" else AIMessage)(content=h["content"])
              for h in history]
    inputs.append(HumanMessage(content=message))

    def fallback(reply: str, sources=(), tools=()) -> dict:
        return {"reply": reply, "sources": list(dict.fromkeys(sources)), "tools_used": list(tools)}

    t0 = time.perf_counter()
    try:
        result = await _build_agent().ainvoke(
            {"messages": inputs},
            config={"recursion_limit": MAX_TOOL_ROUNDS * 2 + 1})
    except GraphRecursionError:
        return fallback("The question was too complex - please ask again more specifically.")
    except Exception as e:
        # Timing matters here: a failure after ~90 s means the model/network was too slow.
        print(f"[AGENT] failed after {time.perf_counter() - t0:.1f}s: {type(e).__name__}", flush=True)
        raise

    new = result["messages"][len(inputs):]               # only what happened in THIS turn
    sources, tools = [], []
    for m in new:
        if isinstance(m, ToolMessage):
            tools.append(m.name)
            if isinstance(m.artifact, list):
                sources += [str(s) for s in m.artifact]

    model_calls = sum(isinstance(m, AIMessage) for m in new)
    print(f"[AGENT] done in {time.perf_counter() - t0:.1f}s | model calls={model_calls} "
          f"| tools={tools}", flush=True)

    last = next((m for m in reversed(new) if isinstance(m, AIMessage)), None)
    text = (_text(last) if last is not None else "").strip()
    return fallback(text or "(Empty response received, please try again.)", sources, tools)
