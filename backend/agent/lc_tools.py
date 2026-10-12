"""Adapter: our provider-neutral tools (tools/registry.py) -> LangChain StructuredTools.

The tools themselves are untouched, so the same SQL / RAG code can serve any framework. Each tool
returns (JSON text for the model, sources list); the sources travel as the ToolMessage *artifact*,
which is never shown to the model.

TOOLS is built once at import. Each tool resolves its handler through registry.run_tool() at CALL
time, so tests can still monkeypatch registry.HANDLERS after import.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from backend.agent.tools import registry


def _make(schema: dict[str, Any]) -> StructuredTool:
    name = schema["name"]

    async def _call(**kwargs: Any) -> tuple[str, list[str]]:
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


TOOLS: list[StructuredTool] = [_make(s) for s in registry.TOOLS]
