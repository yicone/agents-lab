---
name: notion-wechat-reading-helper
description: Use when processing the agents-lab Notion reading database that stores WeChat article URLs and requires MCP-based row discovery, browser-based article extraction, Notion writeback, and distill-memory after each row.
---

# Notion WeChat Reading Helper

## Overview

Use this for the `文章列表` Notion database in this repo's reading-helper automation.
The stable path is: Notion MCP for row discovery and writeback, `agent-browser` for WeChat article extraction, and `distill-memory` for article-level distillation into `nmem`.

## Workflow

1. Fetch the database/data source and confirm these properties exist:
   - `Title`
   - `URL `
   - `Processing Status`
   - `Author`
   - `Tags`
   - `Last Attempt At`
   - `Error`

2. Discover candidate rows through Notion MCP search, not by blank-value enumeration.
   - Current working mechanism: rows must have a non-empty, searchable `Title`.
   - Search the data source for that placeholder title text and then `fetch` each returned page.
   - Skip rows whose `Processing Status` is not `Queued`.
   - If queued rows still have empty `Title`, stop and report discovery as blocked.

3. Before extracting an article, update the row:
   - `Processing Status = Processing`
   - `Last Attempt At = now`
   - clear `Error` if appropriate

4. For `mp.weixin.qq.com` URLs, prefer a real browser session.
   - Do not use `curl` as the primary extraction method; it often hits WeChat environment verification.
   - Open the URL with `agent-browser`.
   - Read article data from page JS:
     - `window.cgiDataNew.title`
     - `window.cgiDataNew.nick_name`
     - `window.cgiDataNew.content_noencode`
   - Parse `content_noencode` with `DOMParser`, then extract text from `doc.body.innerText`.

5. Summarize and write back.
   - Fill `Title` with the real article title.
   - Fill `Author`.
   - Generate `Tags` from the existing Notion options when possible.
   - Replace blank page content with a concise Chinese summary.
   - Set `Processing Status = Done`.

6. If WeChat blocks extraction with captcha or environment verification:
   - If a trustworthy title is available from a related-link anchor, you may fill it.
   - Set `Processing Status = Blocked`.
   - Write the blocker to `Error`.
   - Replace blank page content with a short blocker note instead of a speculative summary.

7. After each processed row, invoke the `distill-memory` skill and write one `nmem` entry.
   - Use `source=reading-helper`.
   - Keep the memory atomic: title, URL, author or blocker, and a 1-2 sentence reusable summary.

## Notes

- Treat Notion MCP as the source of truth for row state.
- Treat `agent-browser` as the source of truth for WeChat article content.
- Treat `nmem` as the destination for article-level distill output.
- Keep output scannable and team-ready.
- Do not speculate when article text is unavailable.
