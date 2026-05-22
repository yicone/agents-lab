#!/usr/bin/env python3
"""
Build ACNS-compliant Logseq write plans.

This file only plans writes. It does not talk to the HTTP API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BlockNode:
    content: str
    children: list["BlockNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BlockNode":
        return cls(
            content=str(payload["content"]),
            children=[cls.from_dict(child) for child in payload.get("children", [])],
        )


@dataclass
class WritePlan:
    destination_kind: str
    page_title: str
    page_properties: dict[str, Any]
    page_should_create: bool
    append_mode: str
    target_section_heading: str | None
    blocks: list[BlockNode]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination_kind": self.destination_kind,
            "page_title": self.page_title,
            "page_properties": dict(self.page_properties),
            "page_should_create": self.page_should_create,
            "append_mode": self.append_mode,
            "target_section_heading": self.target_section_heading,
            "blocks": [block.to_dict() for block in self.blocks],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WritePlan":
        return cls(
            destination_kind=str(payload["destination_kind"]),
            page_title=str(payload["page_title"]),
            page_properties=dict(payload.get("page_properties", {})),
            page_should_create=bool(payload.get("page_should_create", False)),
            append_mode=str(payload.get("append_mode", "page-end")),
            target_section_heading=payload.get("target_section_heading"),
            blocks=[BlockNode.from_dict(block) for block in payload.get("blocks", [])],
            warnings=list(payload.get("warnings", [])),
        )


def build_write_plan(
    *,
    intent_type: str,
    title: str,
    summary: str,
    related: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    body_sections: list[dict[str, Any]] | None = None,
) -> WritePlan:
    """
    Build a minimal ACNS-compliant plan.

    This is intentionally conservative for the first implementation pass.
    """

    related = related or []
    metadata = metadata or {}
    body_sections = body_sections or []

    if intent_type == "log":
        return _build_log_plan(title, summary, related, metadata, body_sections)
    if intent_type == "inbox":
        return _build_inbox_plan(title, summary, related, metadata, body_sections)
    if intent_type == "note-update":
        return _build_note_update_plan(title, summary, related, metadata, body_sections)

    raise ValueError(f"Unsupported intent_type: {intent_type}")


def _build_log_plan(
    title: str,
    summary: str,
    related: list[str],
    metadata: dict[str, Any],
    body_sections: list[dict[str, Any]],
) -> WritePlan:
    page_title = _normalize_log_title(title)
    props = {
        "title": page_title.split("/", 1)[-1],
        "type": "LOG",
        "status": "active",
    }
    if related:
        props["related"] = ", ".join(related)
    if metadata.get("created"):
        props["created"] = str(metadata["created"])

    blocks: list[BlockNode] = []
    header = _build_log_header_block(metadata)
    if header:
        blocks.append(header)
    if summary.strip():
        blocks.append(BlockNode(content=f"摘要：{summary.strip()}"))
    blocks.extend(_sections_to_blocks(body_sections))

    warnings: list[str] = []
    if not title.startswith(("I-LOG/", "P-LOG/", "OS-LOG/")):
        warnings.append("Log title did not include an explicit LOG namespace and was normalized to I-LOG/ by default.")
    if not metadata.get("timestamp"):
        warnings.append("Log metadata does not include a timestamp field.")
    return WritePlan("log", page_title, props, True, "page-end", None, blocks, warnings)


def _build_inbox_plan(
    title: str,
    summary: str,
    related: list[str],
    metadata: dict[str, Any],
    body_sections: list[dict[str, Any]],
) -> WritePlan:
    props = {
        "inbox-type": metadata.get("inbox-type", "info"),
        "inbox-status": metadata.get("inbox-status", "raw"),
    }
    if related:
        props["related-to"] = ", ".join(related)

    blocks = [BlockNode(content=summary.strip())] if summary.strip() else []
    blocks.extend(_sections_to_blocks(body_sections))
    return WritePlan("inbox", "Inbox", props, False, "page-end", None, blocks)


def _build_note_update_plan(
    title: str,
    summary: str,
    related: list[str],
    metadata: dict[str, Any],
    body_sections: list[dict[str, Any]],
) -> WritePlan:
    props: dict[str, Any] = {}
    if metadata.get("updated"):
        props["updated"] = str(metadata["updated"])
    if related:
        props["related"] = ", ".join(related)

    append_mode = str(metadata.get("append_mode", "page-end")).strip() or "page-end"
    target_section_heading = str(metadata.get("target_section_heading", "")).strip() or None

    blocks: list[BlockNode] = []
    if metadata.get("source"):
        blocks.append(BlockNode(content=f"更新来源:: {str(metadata['source']).strip()}"))
    if summary.strip():
        blocks.append(BlockNode(content=f"更新摘要：{summary.strip()}"))
    blocks.extend(_sections_to_blocks(body_sections))

    warnings: list[str] = []
    if not title.strip():
        warnings.append("note-update plan does not include a target page title.")
    if not blocks:
        warnings.append("note-update plan does not include any block content.")
    if append_mode not in {"page-end", "section-heading"}:
        warnings.append(f"Unsupported append_mode '{append_mode}', falling back to page-end.")
        append_mode = "page-end"
        target_section_heading = None
    if append_mode == "section-heading" and not target_section_heading:
        warnings.append("section-heading append_mode requires target_section_heading; falling back to page-end.")
        append_mode = "page-end"
    return WritePlan("note-update", title, props, False, append_mode, target_section_heading, blocks, warnings)


def _sections_to_blocks(body_sections: list[dict[str, Any]]) -> list[BlockNode]:
    blocks: list[BlockNode] = []
    for section in body_sections:
        heading = section.get("heading", "").strip()
        lines = [line.strip() for line in section.get("lines", []) if str(line).strip()]

        if heading:
            parent = BlockNode(content=f"## {heading}")
            parent.children.extend(BlockNode(content=line) for line in lines)
            blocks.append(parent)
        else:
            blocks.extend(BlockNode(content=line) for line in lines)
    return blocks


def _normalize_log_title(title: str) -> str:
    if title.startswith(("I-LOG/", "P-LOG/", "OS-LOG/")):
        return title
    return f"I-LOG/{title}"


def _build_log_header_block(metadata: dict[str, Any]) -> BlockNode | None:
    children: list[BlockNode] = []

    timestamp = str(metadata.get("timestamp", "")).strip()
    source = str(metadata.get("source", "")).strip()
    tags = metadata.get("tags", []) or []

    if timestamp:
        children.append(BlockNode(content=f"时间:: {timestamp}"))
    if source:
        children.append(BlockNode(content=f"来源:: {source}"))
    normalized_tags = _normalize_tags(tags)
    if normalized_tags:
        children.append(BlockNode(content=f"tags:: {', '.join(normalized_tags)}"))

    if not children:
        return None

    return BlockNode(content="记录元数据", children=children)


def _normalize_tags(tags: list[Any]) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        if not text:
            continue
        if text.startswith("[[") and text.endswith("]]"):
            normalized.append(text)
        elif text.startswith("#"):
            normalized.append(f"[[{text[1:]}]]")
        else:
            normalized.append(f"[[{text}]]")
    return normalized
