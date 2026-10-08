"""search_knowledge: semantic search over SOPs / policies / manuals and operator alert notes."""
from backend.rag import retriever

SCHEMAS = [{
    "name": "search_knowledge",
    "description": (
        "Semantic search in the knowledge base. scope='documents': SOPs, policies, manuals "
        "(use for 'what is the procedure', 'what does the policy say'). scope='alerts': past alerts "
        "where an operator explained WHY it was right/wrong (use for 'have we seen this false "
        "alarm before'). Returns passages with their source. For counts or lists of alerts use "
        "count_alerts / search_alerts instead."),
    "input_schema": {"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "description": "What to look for, in natural language"},
        "scope": {"type": "string", "enum": ["documents", "alerts", "all"], "description": "default all"},
        "category": {"type": "string", "description": "optional folder filter: sop, policies, manuals"},
        "k": {"type": "integer", "description": "passages to return, 1-10, default 4"}}},
}]


async def search_knowledge(a: dict) -> dict:
    hits = await retriever.search(a["query"], k=a.get("k"), scope=a.get("scope") or "all",
                                  category=a.get("category"))
    if not hits:
        return {"found": 0, "note": "Nothing relevant in the knowledge base.", "sources": []}
    return {
        "found": len(hits),
        "passages": [{"source": h.metadata.get("source"), "category": h.metadata.get("category"),
                      "score": round(h.score, 2), "text": h.text} for h in hits],
        "sources": list(dict.fromkeys(str(h.metadata.get("source")) for h in hits)),
    }


HANDLERS = {"search_knowledge": search_knowledge}
