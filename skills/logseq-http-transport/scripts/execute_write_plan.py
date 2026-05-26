#!/usr/bin/env python3
"""
Execute or dry-run a Logseq write plan.

Default behavior is dry-run. Use --write for actual HTTP mutations.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from logseq_http import LogseqHTTPTransport

TEST_WRITE_TITLE_PREFIX = "I-LOG/Test Agent Write"


@dataclass
class BlockNode:
    content: str
    children: list["BlockNode"] = field(default_factory=list)

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


def load_plan(path: str) -> WritePlan:
    payload = json.loads(Path(path).read_text())
    return WritePlan.from_dict(payload)


def preview_operations(plan: WritePlan) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    ops.append({
        "op": "ensure_page",
        "page_title": plan.page_title,
        "page_should_create": plan.page_should_create,
        "page_properties": plan.page_properties,
    })
    target_ref = f"page:{plan.page_title}"
    target_mode = plan.append_mode
    if (
        plan.destination_kind == "note-update"
        and plan.append_mode == "section-heading"
        and plan.target_section_heading
    ):
        target_ref = f"section:{plan.target_section_heading}"
        ops.append(
            {
                "op": "resolve_target_section",
                "page_title": plan.page_title,
                "target_section_heading": plan.target_section_heading,
                "fallback": "page-end",
            }
        )
    elif plan.destination_kind == "note-update" and plan.append_mode == "section-heading":
        target_mode = "page-end"
    for block in plan.blocks:
        _preview_block_ops(block, parent_ref=target_ref, ops=ops, root_append_mode=target_mode)
    return ops


def _preview_block_ops(block: BlockNode, parent_ref: str, ops: list[dict[str, Any]], root_append_mode: str) -> None:
    block_ref = f"block:{len(ops)}"
    ops.append(
        {
            "op": "insert_block",
            "parent_ref": parent_ref,
            "content": block.content,
            "sibling": parent_ref.startswith("page:") and root_append_mode == "page-end",
        }
    )
    for child in block.children:
        _preview_block_ops(child, parent_ref=block_ref, ops=ops, root_append_mode="child")


def execute_plan(plan: WritePlan, transport: LogseqHTTPTransport) -> dict[str, Any]:
    page = transport.get_page(plan.page_title)
    if not page and plan.page_should_create:
        page = transport.create_page(plan.page_title, create_first_block=False)
    elif not page:
        raise ValueError(f"Target page does not exist and page_should_create is false: {plan.page_title}")

    page_uuid = page["uuid"]
    for key, value in plan.page_properties.items():
        transport.upsert_block_property(page_uuid, key, value)

    inserted = []
    anchor_parent_uuid, anchor_mode = _resolve_execution_anchor(plan, transport, page_uuid)
    previous_root_uuid: str | None = None
    for block in plan.blocks:
        block_result = _insert_block_tree(
            transport=transport,
            block=block,
            page_uuid=page_uuid,
            previous_root_uuid=previous_root_uuid,
            parent_uuid=anchor_parent_uuid,
            root_append_mode=anchor_mode,
        )
        if anchor_mode == "page-end":
            previous_root_uuid = block_result["uuid"]
        inserted.append(block_result)

    return {
        "page": page,
        "append_mode": plan.append_mode,
        "target_section_heading": plan.target_section_heading,
        "inserted_root_blocks": inserted,
        "warnings": plan.warnings,
    }


def _resolve_execution_anchor(
    plan: WritePlan,
    transport: LogseqHTTPTransport,
    page_uuid: str,
) -> tuple[str | None, str]:
    if plan.destination_kind != "note-update":
        return None, "page-end"
    if plan.append_mode != "section-heading" or not plan.target_section_heading:
        return None, "page-end"

    blocks = transport.get_page_blocks_tree(plan.page_title)
    match = _find_block_by_content(blocks, f"## {plan.target_section_heading}")
    if match:
        return match["uuid"], "section-child"
    return None, "page-end"


def _find_block_by_content(blocks: list[dict[str, Any]], content: str) -> dict[str, Any] | None:
    for block in blocks:
        if block.get("content") == content:
            return block
        match = _find_block_by_content(block.get("children", []), content)
        if match:
            return match
    return None


def _enforce_write_safety(plan: WritePlan, allow_title_prefix: str | None, allow_create: bool) -> None:
    if not allow_title_prefix:
        raise ValueError(
            f"Real writes require --allow-title-prefix. Recommended first-write prefix: '{TEST_WRITE_TITLE_PREFIX}'."
        )
    if not plan.page_title.startswith(allow_title_prefix):
        raise ValueError(
            f"Refusing real write because page title '{plan.page_title}' does not start with allowed prefix '{allow_title_prefix}'."
        )
    if plan.page_should_create and not allow_create:
        raise ValueError("Real writes that may create a page require --allow-create.")


def _insert_block_tree(
    *,
    transport: LogseqHTTPTransport,
    block: BlockNode,
    page_uuid: str,
    previous_root_uuid: str | None,
    parent_uuid: str | None,
    root_append_mode: str,
) -> dict[str, Any]:
    if parent_uuid:
        result = transport.insert_block(parent_uuid, block.content, sibling=False)
    elif root_append_mode == "page-end" and previous_root_uuid:
        result = transport.insert_block(previous_root_uuid, block.content, sibling=True)
    else:
        result = transport.insert_block(page_uuid, block.content, sibling=False)

    current_uuid = result["uuid"]
    for child in block.children:
        _insert_block_tree(
            transport=transport,
            block=child,
            page_uuid=page_uuid,
            previous_root_uuid=None,
            parent_uuid=current_uuid,
            root_append_mode="child",
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or execute a Logseq write plan")
    parser.add_argument("plan_path", help="Path to a JSON write plan")
    parser.add_argument("--write", action="store_true", help="Execute the plan instead of printing a dry-run preview")
    parser.add_argument(
        "--allow-title-prefix",
        help=f"Required for real writes. The target page title must start with this prefix. Recommended first-write prefix: {TEST_WRITE_TITLE_PREFIX}",
    )
    parser.add_argument(
        "--allow-create",
        action="store_true",
        help="Required for real writes when the write plan may create a page.",
    )
    args = parser.parse_args()

    plan = load_plan(args.plan_path)
    if not args.write:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "recommended_test_title_prefix": TEST_WRITE_TITLE_PREFIX,
                    "warnings": plan.warnings,
                    "operations": preview_operations(plan),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    _enforce_write_safety(plan, args.allow_title_prefix, args.allow_create)
    transport = LogseqHTTPTransport.from_defaults()
    result = execute_plan(plan, transport)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
