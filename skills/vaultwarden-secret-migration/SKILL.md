---
name: vaultwarden-secret-migration
description: Use when Logseq notes contain API tokens, secrets, passwords, recovery codes, or private keys that should be migrated into Vaultwarden and replaced with Vaultwarden reference text instead of raw secret values.
metadata:
  owner: agents-lab
  scope: repo
---

# Vaultwarden Secret Migration

## Overview

Use this skill to remove existing secrets from Logseq notes without losing the operational context around them.

Default boundary: the agent should not become the long-term holder of the secret. Prefer `bw` CLI for direct item creation, but only when authentication is already established or the user explicitly assists with login and unlock.

## When to Use

- Logseq pages or blocks already contain API tokens, passwords, private keys, recovery codes, cookies, or connection strings
- the user wants notes to keep metadata and retrieval hints, but not the secret itself
- the user wants a repeatable cleanup workflow for an existing vault

Do not use this skill for:
- generating new secrets
- bulk exporting Vaultwarden
- storing secrets back into notes in encrypted form

## Core Rules

- Prefer `graphthulhu` to read and write the live Logseq graph
- Prefer official `bw` CLI for Vaultwarden writes when available
- Keep notes Chinese-first unless the original page is clearly English-first
- Never leave the full secret in the final note content
- Never invent the secret metadata; if missing, mark it as unknown
- Prefer replacing raw values with a stable reference line such as:
  - `Token stored in Vaultwarden: OpenAI / prod / last4=7KQ2`
- Never persist `BW_SESSION`, master passwords, or exported vault data into notes, repo files, or shell history on purpose

## Workflow

1. Find candidate pages or blocks that may contain secrets
2. Inspect the exact block context before editing
3. Build a migration checklist per secret:
   - service or system name
   - environment or scope
   - secret type
   - last 4 visible characters if available
   - note/page location
4. Check whether `bw` is configured and authenticated
5. If `bw` is ready, create the Vaultwarden item first
6. If `bw` is not ready, ask the user to complete login and unlock or fall back to the human-in-the-loop workflow
7. Rewrite the Logseq block so it keeps operational context but removes the secret body
8. Add a short follow-up note if rotation is recommended

## bw CLI Preconditions

Before writing to Vaultwarden with `bw`, verify:

- `bw` is installed
- `bw config server` points to the intended self-hosted domain
- `bw status` is `unlocked`, or the user can provide an interactive unlock step

Preferred command sequence:

1. `bw status`
2. if needed, `bw login`
3. if needed, `bw unlock --raw`
4. export `BW_SESSION` only for the current command or current shell session
5. `bw sync`

Read [references/bw-cli.md](references/bw-cli.md) before creating items.

If login or unlock would require storing the master password in a script, stop and ask the user to perform the interactive step instead.

## Reference Format

Preferred replacement text:

- `Token stored in Vaultwarden: <service> / <env> / last4=<last4>`
- `Password stored in Vaultwarden: <service> / <account>`
- `Recovery code stored in Vaultwarden: <service> / <account>`
- `Private key stored in Vaultwarden: <service> / <purpose>`

If the item name is known, prefer matching Vaultwarden naming exactly.

Suggested Vaultwarden item names:

- `OpenAI / prod`
- `GitHub / yicone@gmail.com`
- `PostgreSQL / app user / prod`
- `VPS deploy key / bw.yic.one`

## Detection Hints

Look for:

- long alphanumeric strings
- bearer tokens
- `sk-`, `ghp_`, `xoxp-`, `xoxb-`, `AIza`, `AKIA`, `eyJ`
- PEM blocks
- `password::`, `token::`, `secret::`, `api key`, `access token`, `refresh token`
- connection URLs embedding credentials

Read [references/redaction-rules.md](references/redaction-rules.md) before editing if the secret format is ambiguous.

## Editing Pattern

Before editing:

- capture the page name and block UUID
- preserve surrounding context such as purpose, owner, expiry, and rotation notes
- if one block mixes normal notes and a secret, rewrite only the sensitive fragment

After editing:

- verify the raw secret string no longer appears on the page
- keep backlinks and related links intact
- if the page describes an active production integration, suggest token rotation unless the user confirms the note never left trusted local storage
- if a `bw` item was created, report the item name and item id when practical

## Common Mistakes

- deleting the whole block when only the secret fragment needed removal
- leaving enough of the value in place to reconstruct the secret
- replacing with vague text like `saved elsewhere` instead of a concrete Vaultwarden reference
- moving too fast and cleaning notes before the user has actually stored the secret
- attempting `bw create` while unauthenticated or locked
- leaking `BW_SESSION` into durable files or printed notes

## Output Expectation

When you finish, report:

- which pages or blocks were cleaned
- the exact Vaultwarden reference format used
- whether token rotation is still recommended
