from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.agent.agent import run_agent
from backend.agent.memory import session_store
from backend.core.config import settings
from backend.core.dependencies import admin_only, get_current_user
from backend.models.user import User
from backend.rag import ingest

router = APIRouter(prefix="/chat", tags=["chat"])

_reindex_state = {"running": False, "last": None, "error": None}


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/")
async def chat(body: ChatIn, user: User = Depends(get_current_user)):
    if not getattr(settings, "GEMINI_API_KEY", ""):
        raise HTTPException(503, "GEMINI_API_KEY is not configured")
    history = await session_store.load(user.id)
    try:
        result = await run_agent(history, body.message)
    except Exception as e:
        print(f"[CHAT] agent failed: {e!r}", flush=True)
        raise HTTPException(502, "Chatbot is unavailable right now")
    await session_store.append(user.id, body.message, result["reply"], result["sources"])
    return {"reply": result["reply"], "sources": result["sources"]}


@router.get("/history")
async def history(user: User = Depends(get_current_user)):
    return {"turns": await session_store.load(user.id, limit=40)}


@router.delete("/history")
async def clear_history(user: User = Depends(get_current_user)):
    await session_store.clear(user.id)
    return {"ok": True}


async def _do_reindex(full: bool):
    _reindex_state.update(running=True, error=None)
    try:
        _reindex_state["last"] = await ingest.reindex(full=full)
    except Exception as e:
        _reindex_state["error"] = str(e)
    finally:
        _reindex_state["running"] = False


@router.post("/reindex")
async def reindex(bg: BackgroundTasks, full: bool = False, _: User = Depends(admin_only())):
    """Admin only. Runs in the background; poll GET /chat/reindex/status."""
    if _reindex_state["running"]:
        raise HTTPException(409, "Reindex already running")
    bg.add_task(_do_reindex, full)
    return {"started": True, "full": full}


@router.get("/reindex/status")
async def reindex_status(_: User = Depends(admin_only())):
    return _reindex_state
