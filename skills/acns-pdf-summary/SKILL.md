---
name: acns-pdf-summary
description: Summarize Logseq PDF highlights into structured notes that fit the user's ACNS naming, ownership, and page-boundary rules.
metadata:
  owner: agents-lab
  scope: repo
  adapter_targets:
    - <target-project-skill-dir>/acns-pdf-summary
---

# ACNS PDF Summary

## Overview

Read Logseq PDF highlight pages such as `hls__*.md` and turn them into structured reading notes that fit the user's current vault semantics.

## Usage

User input:
- `/acns-pdf-summary [PDF title or keyword]`

## Workflow

1. Find PDF highlight pages
2. Read and parse highlight content
3. Extract highlight text, page numbers, comments, and major themes
4. Decide the right destination page
5. Generate a structured summary

## Destination Rules

Do not default to creating a brand-new page if an existing durable page already owns the topic.

Possible destinations:
- append to an existing topic RES page
- create a new RES page when the PDF deserves a durable home
- append to a project context page only if the PDF is tightly project-bound

## Output Guidance

When creating a new page, choose namespace based on actual ownership:
- topic or knowledge reference -> likely `I-/P-/A-/F-/E--RES`
- project-bound reading note -> possibly project-related RES or linked from the project page

## Logseq PDF Highlight Format

Logseq PDF highlights are often stored as:

```markdown
- 高亮文本内容
  ls-type:: annotation
  hl-page:: 5
  hl-color:: yellow
  id:: uuid-xxx
```

## Output Template

```markdown
title:: [PDF标题] 阅读笔记
type:: RES
source:: [[hls__原始PDF页面]]
created:: YYYY-MM-DD
status:: active

## 📖 文档概述
[1-2 句概述]

## 📌 核心要点

### 主题1
- 要点 (p.5)

## 💡 值得沉淀的洞察
- [提炼出的结论]

## 📚 原始高亮
{{embed [[hls__原始PDF]]}}
```

## Important Rules

- preserve page references and page numbers
- do not over-summarize away the source structure
- follow current namespace and ownership rules instead of using a generic `[层级]-RES` placeholder blindly
