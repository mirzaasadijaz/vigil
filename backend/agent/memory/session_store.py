"""Per-user chat history, one JSON file per user in data/agent/conversations/.
Plain text turns only (tool calls are not stored - they are re-done if needed)."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from backend.core.config import settings

DIR = Path(settings.BASE_DIR) / "data" / "agent" / "conversations"
MAX_STORED = 40            # messages kept per user
_lock = asyncio.Lock()


def _path(user_id: int) -> Path:
    return DIR / f"user_{int(user_id)}.json"      # int() => no path tricks


def _read(user_id: int) -> list[dict]:
    try:
        return json.loads(_path(user_id).read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(user_id: int, turns: list[dict]) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(user_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(turns[-MAX_STORED:], ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _path(user_id))               # atomic: never a half-written file


async def load(user_id: int, limit: int = 20) -> list[dict]:
    async with _lock:
        turns = await asyncio.to_thread(_read, user_id)
    turns = turns[-limit:]
    while turns and turns[0]["role"] != "user":   # the API requires user-first
        turns = turns[1:]
    return turns


async def append(user_id: int, user_msg: str, assistant_msg: str,
                 sources: list[str] | None = None) -> None:
    async with _lock:
        turns = await asyncio.to_thread(_read, user_id)
        answer = {"role": "assistant", "content": assistant_msg}
        if sources:
            answer["sources"] = list(sources)       # shown under the bubble after a reload
        turns += [{"role": "user", "content": user_msg}, answer]
        await asyncio.to_thread(_write, user_id, turns)


async def clear(user_id: int) -> None:
    async with _lock:
        await asyncio.to_thread(_path(user_id).unlink, True)
