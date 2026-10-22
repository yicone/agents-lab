"""Name-only Grok Bot skill declarations from Git; no Grok/API access."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

MANIFEST_PATH = Path(__file__).resolve().parents[3] / "instructions-governance/grok-bots-manifest.json"
# Historical ACM-298 evidence, not a claim about current runtime loading.
OBSERVED_BOTS = {"9cb8bf94-4ca4-4e1e-81f9-b174b5e0a985", "218f6ebc-7d91-49f9-851d-d533d6a38d87"}
OBSERVED_SKILLS = {"agent-steward-getting-started", "AI 商城产品 PM 上手", "Brand multi-format covers",
                   "ego-browser", "Multica on mini", "店铺内容口径审稿", "video-to-longform", "wechatide-skill"}
NOT_APPLICABLE = "仅有 Grok Bot 技能名称声明；正文、版本及本机 Runtime 不适用"


def grok_bot_skills(path: Path | None = None) -> tuple[list[dict], dict]:
    path = path if path is not None else MANIFEST_PATH
    source = {"id": "grok_bot", "label": "Grok Bot", "path": "instructions-governance/grok-bots-manifest.json"}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema") != "grok-bots-manifest/v1" or not isinstance(document.get("bots"), list):
            raise ValueError("invalid manifest")
        grouped: dict[str, list[dict]] = {}
        seen = set()
        for bot in document["bots"]:
            if (not isinstance(bot, dict) or not isinstance(bot.get("id"), str) or not bot["id"]
                    or bot["id"] in seen or not isinstance(bot.get("name"), str) or not bot["name"].strip()
                    or not isinstance(bot.get("skills"), list)
                    or any(not isinstance(name, str) or not name.strip() for name in bot["skills"])):
                raise ValueError("invalid bot")
            seen.add(bot["id"])
            for name in dict.fromkeys(" ".join(name.split()) for name in bot["skills"]):
                observed = bot["id"] in OBSERVED_BOTS and name in OBSERVED_SKILLS
                grouped.setdefault(name, []).append({
                    "id": bot["id"], "name": bot["name"],
                    "evidence_state": "observed" if observed else "inferred",
                    "evidence_label": "ACM-298 已实测（历史证据）" if observed else "按共享机制推断，未逐一实测",
                })
        stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except FileNotFoundError:
        return [], {**source, "state": "absent", "detail": "Grok Bot 技能清单未提供", "collected_at": None}
    except (OSError, UnicodeError, ValueError, TypeError):
        return [], {**source, "state": "bad", "detail": "Grok Bot 技能清单无法读取或格式无效", "collected_at": None}
    items = []
    for name, bots in sorted(grouped.items()):
        bots.sort(key=lambda bot: (bot["evidence_state"] != "observed", bot["name"].casefold(), bot["id"]))
        observed = sum(bot["evidence_state"] == "observed" for bot in bots)
        observed_names = "、".join(bot["name"] for bot in bots if bot["evidence_state"] == "observed")
        detail = (f"{observed_names}：ACM-298 已实测（历史证据）；" if observed else "没有已实测记录；")
        detail += f"其余 {len(bots) - observed} 个 Bot 按共享机制推断，未逐一实测。不代表当前会话加载。"
        items.append({
            "key": "grok_bot:" + quote(name, safe=""), "name": name, "origin": "grok_bot",
            "source_class": "grok_bot", "source_label": "Grok Bot", "scope": "bot",
            "channel_label": "Grok Bot", "channel_detail": "、".join(bot["name"] for bot in bots),
            "grok_bots": bots, "evidence_detail": detail, "manifest_path": source["path"],
            "content": {"state": "not_applicable", "message": NOT_APPLICABLE},
            "current_version": None, "observed_version": None, "reconciliation_status": None,
            "status_label": "名称声明", "review_time": None, "entries": [], "evidence": [], "tools": {},
            "shared_entry": {"state": "not_applicable", "label": "∅", "detail": NOT_APPLICABLE},
            "bucket": None, "baseline_debt": False, "quiet": False, "command": None,
            "constraints": [], "findings": [], "constraint_count": 0,
        })
    return items, {**source, "state": "ok", "detail": f"Git 清单 · {len(seen)} 个 Bot · {len(items)} 个技能名称；实测与推断见各条提示", "collected_at": stamp}
