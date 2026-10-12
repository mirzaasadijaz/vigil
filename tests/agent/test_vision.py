"""Shared Gemini factory (backend/agent/llm.py) and the vision helpers built on it.
Offline: no API key, no network. Skipped where the agent dependencies are not installed."""
import asyncio

import pytest
from PIL import Image

pytest.importorskip("langchain_google_genai")
from langchain_core.messages import AIMessage                                 # noqa: E402

from backend.agent import llm, vision                                         # noqa: E402
from backend.core.config import settings                                      # noqa: E402


# ── llm.make_gemini ─────────────────────────────────────────────────────────
def test_make_gemini_builds_a_real_client_with_the_agent_settings(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    m = llm.make_gemini("gemini-test", max_tokens=777, timeout=33)
    # Releases before 3.1.0 silently DROP thinking_level (a log warning, no exception), so Gemini 3
    # would quietly run at its slower default. Fail loudly here instead of finding out in production.
    assert getattr(m, "thinking_level", None) == "low", \
        "langchain-google-genai is too old: pip install -U 'langchain-google-genai>=3.1.0'"
    assert m.model.endswith("gemini-test")             # newer releases add a "models/" prefix
    assert (m.max_output_tokens, m.max_retries) == (777, 1)


# ── vision helpers ──────────────────────────────────────────────────────────
def test_model_name_tiers_fall_back_to_the_main_model(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_MODEL", "main")
    monkeypatch.setattr(settings, "GEMINI_SCAN_MODEL", "")
    monkeypatch.setattr(settings, "GEMINI_VISION_MODEL", "big")
    assert vision.model_name("light") == "main"       # blank override -> main model
    assert vision.model_name("strong") == "big"       # explicit override wins


def test_ask_reads_gemini_block_content_and_caches_the_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    Image.new("RGB", (40, 30), (200, 0, 0)).save(tmp_path / "snap.jpg", "JPEG")
    monkeypatch.setattr(vision, "_CACHE", vision.OrderedDict())
    monkeypatch.setattr(vision, "_sem", None)

    class FakeVision:
        calls = 0

        async def ainvoke(self, messages):
            FakeVision.calls += 1
            return AIMessage(content=[{"type": "thinking", "thinking": "hmm"},
                                      {"type": "text", "text": "  Two "}, {"type": "text", "text": "chairs.  "}])

    monkeypatch.setattr(vision, "_get_model", lambda tier: FakeVision())
    first = asyncio.run(vision.ask("strong", "snap.jpg", "How many chairs?"))
    again = asyncio.run(vision.ask("strong", "snap.jpg", "How many chairs?"))
    assert first == again == "Two chairs."            # thinking block dropped, text joined + stripped
    assert FakeVision.calls == 1                       # second answer came from the cache


def test_snapshot_path_cannot_escape_media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    (tmp_path / "media" / "snapshots").mkdir(parents=True)
    (tmp_path / "secret.jpg").write_bytes(b"x")
    Image.new("RGB", (4, 4)).save(tmp_path / "media" / "snapshots" / "ok.jpg", "JPEG")

    assert vision.snapshot_path("snapshots\\ok.jpg") is not None      # Windows-style row still resolves
    assert vision.snapshot_path("../secret.jpg") is None              # traversal is refused
    assert vision.snapshot_path("") is None
