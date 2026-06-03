# EVE Chatlog Knowledge Base

This folder contains a static, public-safe knowledge website generated from recent local EVE chat logs.

- Open `index.html` in a browser.
- `knowledge.json` is the structured database for reuse in other tools.
- Raw chat logs are not included.
- Public Star Fleet Productions website articles are included as sourced summaries with links.
- Generated at: 2026-06-03T06:38:14Z
- Source window: 2026-06-01T01:56:10Z to 2026-06-03T06:34:43Z

Regenerate from the repository root:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_chatlog_knowledge_site.py --logs-root "$env:USERPROFILE\OneDrive\Documents\EVE\logs" --since-date 2026-06-01
```
