"""Single list of provider-neutral tool schemas + one dispatcher (wrapped for LangChain in lc_tools.py)."""
from backend.agent.tools import alert_tools, camera_tools, rag_tools, image_tools, vision_tools
_MODULES = (alert_tools, camera_tools, rag_tools, image_tools, vision_tools)

TOOLS: list[dict] = [s for m in _MODULES for s in m.SCHEMAS]
HANDLERS: dict = {k: v for m in _MODULES for k, v in m.HANDLERS.items()}

assert len({t["name"] for t in TOOLS}) == len(TOOLS), "duplicate tool name"
assert {t["name"] for t in TOOLS} == set(HANDLERS), "schema/handler mismatch"


async def run_tool(name: str, args: dict) -> dict:
    fn = HANDLERS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    try:
        return await fn(args or {})
    except ValueError as e:                    # e.g. bad date format
        return {"error": f"bad argument: {e}"}
    except Exception as e:                     # never leak stack traces to the model
        print(f"[AGENT] tool {name} failed: {e!r}", flush=True)
        return {"error": "tool failed"}
