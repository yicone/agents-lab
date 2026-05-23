---
name: logseq-http-transport
description: Thin Logseq HTTP transport for the local Vault. Owns API calling, token loading, and response normalization only. It must not decide page naming, page classes, templates, or ACNS write policy.
metadata:
  owner: agents-lab
  scope: repo
allowed-tools: Read, Bash, Grep, Glob
---

# Logseq HTTP Transport

## Purpose

This skill is the transport layer between an agent and the local Logseq HTTP API.

It only owns:

- API URL and token discovery
- HTTP request execution
- response normalization
- transport-layer error handling

It must not own:

- page ownership decisions
- `AREA/PROJ/RES/LOG` classification
- page-title normalization
- default page templates
- content formatting beyond transport needs

## Coordination Boundary

Use `logseq-http-transport` only after semantic and write-plan decisions are already made.

Do not use this skill to decide:

- whether content belongs in Logseq
- which page should own the content
- how ACNS should split properties or blocks

Pair or hand off to:

- `logseq-acns-vault` for vault semantics and page ownership
- `logseq-acns-write` for ACNS-compliant write-plan construction

## Required Boundaries

- Use this only after a higher-level ACNS policy layer has already produced a write plan.
- Never invent page paths such as `Claude Notes/`, `Tasks/`, `Knowledge/`, or `Meetings/`.
- Never send a large free-form page string when the caller should instead provide a block tree.

## Local Defaults

- Base URL: `http://127.0.0.1:12315`
- Token source priority:
  1. explicit parameter
  2. environment variable
  3. local Logseq app config

## Minimum Interface

- `get_current_graph()`
- `get_page(title)`
- `get_page_blocks_tree(title)`
- `create_page(title, properties, create_first_block=True)`
- `insert_block(parent_uuid, content, sibling=False, properties=None)`
- `update_block(uuid, content)`
- `upsert_block_property(uuid, key, value)`

## Response Rule

- If the HTTP API returns `{"result": ...}`, unwrap `result`.
- If the HTTP API returns a bare object or bare array, return it directly.
- If the payload contains an `error` field, raise a transport exception.

## Implementation Entry

- See `{baseDir}/scripts/logseq_http.py`
