"""Adapter: our provider-neutral tools (tools/registry.py) -> LangChain StructuredTools.

The tools themselves are untouched, so the same SQL / RAG code is used by LangChain, by the
Google SDK version, or by any future framework. Each tool returns (text for the model, sources
list). The sources travel as the ToolMessage *artifact*, which is never shown to the model.
"""
from __future__ import annotations

import json

from langchain_core.tools import StructuredTool

from backend.agent.tools import registry


def _make(schema: dict) -> StructuredTool:
    name = schema["name"]

    async def _call(**kwargs):
        out = await registry.run_tool(name, kwargs)
        sources = out.get("sources", []) if isinstance(out, dict) else []
        return json.dumps(out, default=str), sources

    return StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=schema["description"],
        args_schema=schema["input_schema"],          # plain JSON schema dict is accepted
        response_format="content_and_artifact",
    )


def build_tools() -> list[StructuredTool]:
    # Looked up at call time (not import time) so tests can monkeypatch registry.HANDLERS.
    return [_make(s) for s in registry.TOOLS]