"""The one place that builds Gemini chat clients (the agent graph and the vision tools share it).

Requires langchain-google-genai >= 3.1.0 (see requirements-agent.txt): older releases do not know
`thinking_level` and silently drop it - no exception, only a log warning - so a try/except around the
constructor can never catch that. Reading a reply's text needs no helper either: `AIMessage.text`
already drops Gemini's "thinking" blocks and joins the "text" blocks.
"""
from __future__ import annotations

from langchain_google_genai import ChatGoogleGenerativeAI

from backend.core.config import settings


def make_gemini(model: str, *, max_tokens: int, timeout: int) -> ChatGoogleGenerativeAI:
    """Gemini 3.x defaults to medium thinking; "low" is plenty for picking tools and reading one
    frame, and is much faster. Thinking models also spend part of `max_tokens` on thinking, so
    keep it generous or the visible answer comes back truncated / empty."""
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=settings.GEMINI_API_KEY or None,
        thinking_level="low",
        max_output_tokens=max_tokens,
        max_retries=1,                      # each retry can cost a full timeout
        timeout=timeout,
    )
