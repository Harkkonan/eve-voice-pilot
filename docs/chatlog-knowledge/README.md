# EVE Chatlog Knowledge Base

This folder contains a static, public-safe knowledge website generated from recent local EVE chat logs.

- Open `index.html` in a browser.
- `knowledge.json` is the structured database for reuse in other tools.
- Raw chat logs are not included.
- Public Star Fleet Productions website articles are included as sourced summaries with links.
- Full URLs for redacted review links are written only to ignored local report `profiles/chatlog_knowledge_link_review.md`.
- Generated at: 2026-06-03T22:02:37Z
- Source window: 2026-06-01T01:56:10Z to 2026-06-03T22:02:10Z

Regenerate from the repository root:

Set `EVE_LOGS_ROOT` to your local EVE logs folder first.

```powershell
.\.venv\Scripts\python.exe .\scripts\build_chatlog_knowledge_site.py --logs-root "$env:EVE_LOGS_ROOT" --since-date 2026-06-01
```
