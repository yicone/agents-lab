---
name: logseq-acns-write
description: Build ACNS-compliant Logseq write plans before any transport call. Owns page classification, naming, properties, and valid block-tree construction for the user's main Vault.
metadata:
  owner: agents-lab
  scope: repo
allowed-tools: Read, Bash, Grep, Glob
---

# Logseq ACNS Write

## Purpose

This skill converts agent intent into an ACNS-compliant write plan.

It owns:

- page ownership
- page naming and namespace choice
- page property selection
- block splitting
- content warnings when a write does not fit one page cleanly

It must not own:

- HTTP request details
- token loading
- API response parsing

## Coordination Boundary

Use `logseq-acns-write` when the destination is already known or can be decided through ACNS write rules and the immediate need is to build a valid write plan.

For **Fast Path** (known page + one-liner), prefer `intent_type: fast-append`: skip vault deliberation, do not create pages, emit a single block, then hand to `logseq-http-transport`.

If page ownership, namespace choice, or system-layer placement is still unclear, pair or defer first to:
- `logseq-acns-vault`

After the write plan is produced, hand off execution concerns to:
- `logseq-http-transport` when the Logseq HTTP API is available
- vault disk Edit per `AGENTS.md` (`pages/%2F`) when API is unavailable or the agent is already editing on disk

Decided 2026-09-14: dual exits by scenario; no third path; no forced single playbook.

## Required Inputs

- `intent_type`
- `title`
- `summary`
- `related`
- `metadata`
- `body_sections`

## Required Output

A write plan with:

- `destination_kind`
- `page_title`
- `page_properties`
- `page_should_create`
- `blocks`
- `warnings`

## Hard Rules

- Never emit third-party namespace defaults such as `Claude Notes/`, `Tasks/`, `Knowledge/`, or `Meetings/`.
- Prefer existing pages before creating new durable pages.
- Use ACNS naming and ownership rules.
- Keep page properties separate from normal body text.
- Do not emit a block that contains multiple headings.
- Do not emit a block that contains multiple unordered-list scopes.

## First Supported Intent Types

- `log`
- `inbox`
- `note-update`
- `fast-append` — known existing page (or Inbox/Journal title), one block only; `page_should_create=false`; minimal/no new page properties

### `fast-append` rules

- Required: non-empty `title` (existing page) and non-empty `summary` (the one-liner)
- Optional: `related` (ignored for page props on fast path; may be woven into the block text by the caller)
- Do not invent headings or multi-section `body_sections` on this path; if sections are needed, use `note-update` instead
- Warnings if title empty, summary empty, or body_sections non-empty (suggest `note-update`)

## Implementation Entry

- See `{baseDir}/scripts/build_write_plan.py`
