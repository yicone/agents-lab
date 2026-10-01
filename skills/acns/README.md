# ACNS suite

User-facing ACNS workflows (capture → route → review/archive).

Agent-era v0.1: Inbox = short-TTL capture shell only; process → Multica; hard-deadline light actions → Apple Reminders; vault = stable conclusions.

| Skill | Role |
|---|---|
| `acns-inbox` | Short-TTL capture into Inbox shell |
| `acns-route-inbox` | Triage / route Inbox items |
| `acns-weekly-review` | Weekly review across Logseq + Notion |
| `acns-suggest-archive` | Suggest archive candidates |
| `acns-pdf-summary` | PDF highlights → ACNS notes |

Logseq policy helpers stay at `skills/logseq-acns-*` (not this suite). Transport is `skills/logseq-http-transport`.
