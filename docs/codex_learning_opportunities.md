# Codex Learning Opportunities

This note is for tracking teaching questions that Codex offers during project work, especially when the question is useful but not answered in the moment.

The goal is practical: keep enough context that Brian or another AI can later reconstruct what was being learned, even if the original Codex chat transcript is unavailable.

## Public And Private Split

Keep this tracked file public-safe. It should explain the method, not store private transcripts.

Use the ignored local ledger for real working notes:

```text
local_archives/codex_learning_opportunities.md
```

That file is ignored by Git through `local_archives/`, so it can include fuller local context. Still avoid pasting secrets, API keys, webhook URLs, private invite links, tokens, raw EVE chat logs, or anything that should not remain on this machine.

## What To Capture

For every useful teaching prompt that is skipped or deferred, capture:

- Date.
- Project and local path.
- The teaching question or topic Codex offered.
- The concrete work context.
- Why the topic matters.
- Files, commands, docs, or UI screens involved.
- Whether the original Codex chat transcript might exist.
- Search hints for a future AI.
- Status: `open`, `answered`, `practiced`, or `no longer relevant`.

The context should be enough to teach from without the transcript. The transcript pointer is only a bonus.

## Codex Chat Log Lookup Notes

Codex chat/session records may exist locally, but they should not be treated as guaranteed source material. If they exist, likely places to check are:

- `C:\Users\Brian\.codex\sessions\`
- `C:\Users\Brian\.codex\memories\MEMORY.md`
- `C:\Users\Brian\.codex\memories\rollout_summaries\`

Useful search terms are the project path, the teaching question text, unique filenames, command names, date, and any thread or rollout id recorded in the ledger.

Before relying on old transcript context, re-check the current repo state. Code, docs, policies, and local settings can change.

## Entry Template

Copy this into the ignored local ledger when a teaching opportunity comes up:

```markdown
## YYYY-MM-DD - Short topic

- Status: open
- Project: EVE Voice Pilot
- Local path: C:\dev2\EveOnline-current
- Teaching question offered:
- Work context:
- Why this is worth learning:
- Files or commands involved:
- Transcript availability:
- Search hints for future AI:
- Notes:
```

## Public Summary Rule

If a learning opportunity later becomes useful public documentation, rewrite it as a short public-safe lesson. Do not copy private chat text, local-only paths beyond intentional repo paths, tokens, Discord links, raw EVE chat logs, or user-specific operational details into tracked docs.
