---
name: acns-skills-manager
version: 2.0.0
description: Find, security scan, install Codex skills, and maintain ACNS-local skill documentation and inventory.
metadata:
  owner: agents-lab
  scope: repo
  adapter_targets:
    - /Users/tr/Library/Mobile Documents/iCloud~com~logseq~logseq/Documents/.agents/skills/acns-skills-manager
---

# ACNS Skills Manager

## Purpose

Manage skill discovery, verification, installation, and local documentation for the user's ACNS-oriented skill stack.

This skill is about:
- finding reusable skills before building from scratch
- doing security-aware evaluation before installation
- keeping locally owned skill inventory and conventions aligned

## When to Use

Manual trigger:
- user asks to find skills for a domain or task
- user asks to install a skill
- user asks which local skills should be updated or renamed

Proactive use:
- before building a domain-specific workflow from scratch
- when a local workflow might already exist in the marketplace or another repo

## Core Philosophy

- search first, build second
- security before installation
- keep ACNS-local skills and third-party skills clearly separated
- keep inventory and naming conventions synchronized

## Documentation Targets

When local skill ownership changes, update the appropriate inventory/conventions docs in `agents-lab`, not generic "Context OS" docs.

## Workflow

1. Discover relevant skills
2. Verify fit
3. Perform security scan
4. Install only after confirmation when appropriate
5. Sync local documentation and inventory if the change affects owned skills

## Important Naming Rule

For ACNS-local workflow skills, prefer the `acns-` prefix when the skill is tightly bound to the user's vault semantics, routing rules, or operating system.
