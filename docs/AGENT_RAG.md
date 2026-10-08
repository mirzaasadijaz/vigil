# Agent + RAG design

## Flow
```
user -> POST /api/chat/ -> session_store.load(history)
     -> agent.run_agent: LangChain create_agent (LangGraph) + Gemini decides which tool(s) to call
          search_alerts / count_alerts / list_cameras   -> SQL on alert_logs, camera_devices (exact)
          search_knowledge                              -> Chroma (SOPs, policies, manuals, alert notes)
     -> reply + sources -> session_store.append (sources kept) -> user
```
SQL is used for anything numeric (exact, same filters as the dashboard). RAG is only for text
knowledge SQL cannot answer.

## What gets indexed
| kind | from | when |
|---|---|---|
| document | data/rag/documents/**.{md,txt,pdf,docx} (sub-folder = category) | reindex; unchanged files skipped by SHA-1 |
| alert | alert_logs rows with a non-empty feedback_note | reindex; only rows judged since last run |

## Operations
- Add/replace a document: drop it in `data/rag/documents/<sop|policies|manuals>/`, then
  `python -m scripts.reindex_rag` (or admin `POST /api/chat/reindex`).
- Change RAG_EMBED_MODEL: run `python -m scripts.reindex_rag --full`.
- Delete a document: remove the file, reindex (its chunks are removed).
- Wipe everything: delete `data/rag/index` and `data/rag/cache`.
- Install: `pip install -r requirements-agent.txt` (the Dockerfile does this; compose mounts `./data`).

## Config (config.py / .env)
GEMINI_API_KEY, GEMINI_MODEL ("gemini-2.5-flash"), RAG_EMBED_MODEL ("default"), RAG_TOP_K (4),
RAG_MIN_SCORE (0.2)

## Errors seen by the UI
503 = GEMINI_API_KEY not configured, 502 = model/tool failure, 401 = session expired (UI reloads to login).

## Security
- Tools are read-only and bounded (max 50 rows, 100 groups, 10 passages).
- Camera credentials are never returned.
- Retrieved text is marked as data, not instructions (prompt-injection guard in system.md).
- Reindex is admin-only. Chat requires the normal login cookie (any role).
- Alert data, person names and documents in data/rag/documents are sent to Google (Gemini) as
  context when relevant: don't put secrets there.
