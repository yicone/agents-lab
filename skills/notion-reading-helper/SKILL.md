---
name: notion-reading-helper
description: Use when processing the agents-lab Notion reading database that stores article URLs, requires MCP-based row discovery and writeback, uses source-specific extraction with WeChat special handling, and runs distill-memory after each row.
metadata:
  owner: agents-lab
  scope: repo
---

# Notion Reading Helper

## Overview

Use this for the `文章列表` Notion database in this repo's reading-helper automation.
The stable path is: Notion MCP for row discovery and writeback, source-appropriate article extraction, and `distill-memory` for article-level distillation into `nmem`.

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

4. Extract the article with a source-specific path.
   - First classify the URL by domain and page behavior.
   - For `mp.weixin.qq.com` URLs, prefer a real browser session.
     - Do not use `curl` as the primary extraction method; it often hits WeChat environment verification.
     - Open the URL with `agent-browser`.
     - Read article data from page JS:
       - `window.cgiDataNew.title`
       - `window.cgiDataNew.nick_name`
       - `window.cgiDataNew.content_noencode`
     - Parse `content_noencode` with `DOMParser`, then extract text from `doc.body.innerText`.
   - For non-WeChat URLs, prefer the simplest trustworthy path that yields the article body, title, and author:
     - use direct fetch for static pages when the article text is present in HTML
     - use a real browser when the page is client-rendered, gated, or parsing quality is poor
     - extract from the primary article container instead of navigation or comments
   - If the page is inaccessible or does not expose enough trustworthy text, mark the row as blocked instead of speculating.

5. Summarize and write back.
   - Fill `Title` with the real article title.
   - Fill `Author`.
   - Generate `Tags` as plain text because the database field is text-based.
   - Use 2-5 concise tags that reflect the article's actual themes, separated by `, `.
   - Replace blank page content with a concise Chinese summary.
   - Set `Processing Status = Done`.

6. If extraction fails because of captcha, environment verification, paywall, login wall, or parsing failure:
   - If a trustworthy title is available from a related-link anchor, you may fill it.
   - Set `Processing Status = Blocked`.
   - Write the blocker to `Error`.
   - Replace blank page content with a short blocker note instead of a speculative summary.

7. After each processed row, invoke the `distill-memory` skill and write one `nmem` entry.
   - Use `source=reading-helper`.
   - Keep the memory atomic: title, URL, author or blocker, and a 1-2 sentence reusable summary.

## Notes

- Treat Notion MCP as the source of truth for row state.
- Use `agent-browser` when the site requires a real browser, especially for WeChat.
- Treat `nmem` as the destination for article-level distill output.
- Keep output scannable and team-ready.
- Do not speculate when article text is unavailable.
