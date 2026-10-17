"""Layer resolution for Agent Atlas (design v3 A4 / D1).

The collector caches issue `properties` and workspace property definitions.
This module turns those into a normalized layer: option names are recognized
by an `L1`–`L5` prefix (or the exact name 不在地图), each of the three fields
inherits independently, then involved layers are normalized.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

LAYER_IDS = ("L1", "L2", "L3", "L4", "L5")
OUTSIDE = "outside"
LAYER_ORDER = ("L5", "L4", "L3", "L2", "L1")  # map top to bottom
LAYER_NAMES = {
    "L1": "资源",
    "L2": "执行",
    "L3": "能力",
    "L4": "协作",
    "L5": "感知与决策",
}
PRIMARY_PROP = "主层"
INVOLVED_PROP = "涉及层"
GOVERNANCE_PROP = "治理线"
ATLAS_SLUG = "acm-lab"

_PREFIX = re.compile(r"^(L[1-5])(?:\s|$)")
_CANCELLED = {"cancelled", "canceled"}
_CLOSED = _CANCELLED | {"done"}
_DECIDE = {"in_review", "blocked"}


def empty_layer() -> dict[str, Any]:
    return {
        "primary": None,
        "involved": [],
        "governance": None,
        "source": {"primary": None, "involved": None, "governance": None},
    }


def code_from_option_name(name: str | None) -> str | None:
    """Map an option label to `L1`–`L5` or `outside`. Unknown labels are ignored."""
    if not name:
        return None
    text = " ".join(str(name).split())
    if text == "不在地图":
        return OUTSIDE
    match = _PREFIX.match(text)
    return match.group(1) if match else None


class _Prop:
    def __init__(self, row: dict[str, Any]):
        self.id = row.get("id") or ""
        self.name = row.get("name") or ""
        self.type = row.get("type") or ""
        self.options: dict[str, str] = {}
        self.option_names: dict[str, str] = {}
        raw = row.get("options_json")
        if raw is None and isinstance(row.get("config"), dict):
            options = (row.get("config") or {}).get("options") or []
        else:
            try:
                options = json.loads(raw or "[]")
            except json.JSONDecodeError:
                options = []
        if not isinstance(options, list):
            options = []
        for opt in options:
            if not isinstance(opt, dict) or not opt.get("id"):
                continue
            label = opt.get("name") or ""
            code = code_from_option_name(label)
            self.option_names[str(opt["id"])] = label
            if code:
                self.options[str(opt["id"])] = code
            if label:
                self.option_names[label] = label
                if code:
                    self.options[label] = code


class Catalog:
    def __init__(self, primary: _Prop | None, involved: _Prop | None, governance: _Prop | None):
        self.primary = primary
        self.involved = involved
        self.governance = governance

    @property
    def ready(self) -> bool:
        return (
            self.primary is not None
            and self.primary.type == "select"
            and self.involved is not None
            and self.involved.type == "multi_select"
            and self.governance is not None
            and self.governance.type == "checkbox"
        )


def catalog_from_rows(rows: list[dict[str, Any]]) -> Catalog:
    """First non-archived definition wins for each property name."""
    found: dict[str, _Prop] = {}
    for row in sorted(rows, key=lambda r: (str(r.get("name") or ""), str(r.get("id") or ""))):
        if row.get("archived") in (1, True, "1"):
            continue
        name = row.get("name")
        if name in (PRIMARY_PROP, INVOLVED_PROP, GOVERNANCE_PROP) and name not in found:
            found[name] = _Prop(row)
    return Catalog(found.get(PRIMARY_PROP), found.get(INVOLVED_PROP), found.get(GOVERNANCE_PROP))


def _load_properties(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("properties")
    if raw is None:
        raw = row.get("properties_json")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _tokens(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_tokens(item))
        return out
    if isinstance(value, dict):
        if value.get("id"):
            return [str(value["id"])]
        if value.get("name"):
            return [str(value["name"])]
        return []
    return [str(value)]


def _codes(prop: _Prop | None, value: Any, *, allow_outside: bool) -> list[str] | None:
    """None means the field is unset. An empty list is a set-but-empty value."""
    if prop is None:
        return None
    tokens = _tokens(value)
    if not tokens:
        return None
    codes: list[str] = []
    for token in tokens:
        code = prop.options.get(token)
        if code is None:
            code = code_from_option_name(prop.option_names.get(token) or token)
        if code is None:
            continue
        if code == OUTSIDE and not allow_outside:
            continue
        if code not in codes:
            codes.append(code)
    return codes


def _governance(prop: _Prop | None, value: Any) -> bool | None:
    if prop is None or value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
    return None


def own_fields(row: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    """Values set on this issue alone. None means unset (eligible to inherit)."""
    props = _load_properties(row)
    primary_codes = _codes(catalog.primary, props.get(catalog.primary.id) if catalog.primary else None, allow_outside=True)
    involved_codes = _codes(catalog.involved, props.get(catalog.involved.id) if catalog.involved else None, allow_outside=False)
    primary = None
    if primary_codes:
        # A select has one option. Prefer a real layer over a stray outside token.
        layers = [c for c in primary_codes if c in LAYER_IDS]
        primary = layers[0] if layers else primary_codes[0]
    involved = None
    if involved_codes is not None:
        involved = [c for c in LAYER_IDS if c in involved_codes]
        if not involved:
            involved = None  # empty multi-select cannot express "none"; treat as unset
    return {
        "primary": primary,
        "involved": involved,
        "governance": _governance(catalog.governance, props.get(catalog.governance.id) if catalog.governance else None),
    }


def _inherited_source(parent_source: str | None, parent: dict[str, Any]) -> str | None:
    if not parent_source:
        return None
    if parent_source == "own":
        ident = parent.get("identifier") or parent.get("id")
        return f"inherited:{ident}" if ident else None
    return parent_source


def _normalize(primary: str | None, involved: list[str] | None, src_involved: str | None) -> tuple[list[str], str | None]:
    chosen = list(involved or [])
    if primary == OUTSIDE:
        return [], None
    if primary in LAYER_IDS:
        chosen = [c for c in chosen if c != primary]
    chosen = [c for c in LAYER_IDS if c in chosen]
    if not chosen:
        return [], None
    return chosen, src_involved


def resolve_issues(rows: list[dict[str, Any]], catalog: Catalog) -> dict[str, dict[str, Any]]:
    """Resolve every issue in one workspace. Missing parents stop that chain.

    Inheritance uses each ancestor's own value, before normalization, so a child
    whose primary differs still sees the layers the parent actually selected.
    """
    by_id = {r["id"]: r for r in rows if r.get("id")}
    raw_cache: dict[str, dict[str, Any]] = {}

    def walk(iid: str, seen: set[str]) -> dict[str, Any] | None:
        if iid in raw_cache:
            return raw_cache[iid]
        row = by_id.get(iid)
        if row is None or iid in seen:
            return None
        seen.add(iid)
        own = own_fields(row, catalog)
        parent_row = None
        parent_raw = None
        pid = row.get("parent_issue_id")
        if pid and pid in by_id:
            parent_row = by_id[pid]
            parent_raw = walk(pid, seen)

        raw: dict[str, Any] = {"primary": None, "involved": None, "governance": None, "source": empty_layer()["source"]}
        for field in ("primary", "involved", "governance"):
            if own[field] is not None:
                raw[field] = list(own[field]) if field == "involved" else own[field]
                raw["source"][field] = "own"
            elif parent_raw is not None and parent_raw[field] is not None and parent_row is not None:
                raw[field] = list(parent_raw[field]) if field == "involved" else parent_raw[field]
                raw["source"][field] = _inherited_source(parent_raw["source"][field], parent_row)
        raw_cache[iid] = raw
        return raw

    for iid in by_id:
        walk(iid, set())

    resolved: dict[str, dict[str, Any]] = {}
    for iid, raw in raw_cache.items():
        layer = empty_layer()
        layer["primary"] = raw["primary"]
        layer["governance"] = raw["governance"]
        layer["source"]["primary"] = raw["source"]["primary"] if raw["primary"] is not None else None
        layer["source"]["governance"] = raw["source"]["governance"] if raw["governance"] is not None else None
        involved, src = _normalize(raw["primary"], raw["involved"], raw["source"]["involved"])
        layer["involved"] = involved
        layer["source"]["involved"] = src
        resolved[iid] = layer
    return resolved


def _category(row: dict[str, Any]) -> str:
    return (row.get("status_category") or row.get("status") or "").lower()


def is_cancelled(row: dict[str, Any]) -> bool:
    return _category(row) in _CANCELLED


def is_open(row: dict[str, Any]) -> bool:
    return _category(row) not in _CLOSED


def decide_status(row: dict[str, Any]) -> str | None:
    """`in_review` or `blocked`. `status` wins when it is one of those two."""
    status = (row.get("status") or "").lower()
    if status in _DECIDE:
        return status
    category = (row.get("status_category") or "").lower()
    if category in _DECIDE:
        return category
    return None


def _waiting(row: dict[str, Any], events: list[dict[str, Any]]) -> tuple[str | None, bool]:
    current = (row.get("status") or "").lower()
    latest = None
    for ev in events:
        if current and (ev.get("status") or "").lower() == current and ev.get("at"):
            if latest is None or ev["at"] > latest:
                latest = ev["at"]
    if latest:
        return latest, True
    return row.get("last_activity_at"), False


def _bucket() -> dict[str, Any]:
    return {
        "total": 0,
        "open": 0,
        "decide": 0,
        "governance_open": 0,
        "governance_decide": 0,
        "decide_items": [],
    }


def _decide_item(row: dict[str, Any], layer: dict[str, Any], events: list[dict[str, Any]], url_for: Callable[[str], str | None]) -> dict[str, Any]:
    since, exact = _waiting(row, events)
    return {
        "key": f"{row.get('workspace_id')}:{row.get('id')}",
        "id": row.get("id"),
        "identifier": row.get("identifier"),
        "title": row.get("title"),
        "status": decide_status(row),
        "waiting_since": since,
        "waiting_exact": exact,
        "governance": layer.get("governance") is True,
        "url": url_for(row["id"]) if row.get("id") else None,
    }


def _top_decide(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Longest wait first. Missing timestamps sort last; identifier breaks ties.
    items.sort(key=lambda it: (it.get("waiting_since") is None, it.get("waiting_since") or "", it.get("identifier") or ""))
    return items[:3]


def build_report(
    rows: list[dict[str, Any]],
    resolved: dict[str, dict[str, Any]],
    events: dict[str, list[dict[str, Any]]],
    *,
    ready: bool,
    url_for: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Counts for one workspace. Cancelled issues are excluded from every total."""
    url_for = url_for or (lambda _iid: None)
    layers = {
        lid: {
            "id": lid,
            "name": LAYER_NAMES[lid],
            **_bucket(),
            "involved_extra": 0,
            "involved_from": [],
        }
        for lid in LAYER_ORDER
    }
    involved_from: dict[str, set[str]] = {lid: set() for lid in LAYER_IDS}
    outside = {**_bucket()}
    unassigned = {**_bucket()}
    pending: dict[str, list[dict[str, Any]]] = {lid: [] for lid in (*LAYER_IDS, OUTSIDE, "unassigned")}

    for row in rows:
        if not row.get("id") or is_cancelled(row):
            continue
        layer = resolved.get(row["id"]) or empty_layer()
        primary = layer.get("primary")
        gov = layer.get("governance") is True
        open_ = is_open(row)
        decide = decide_status(row)
        if primary in LAYER_IDS:
            cell = layers[primary]
        elif primary == OUTSIDE:
            cell = outside
        else:
            cell = unassigned
            primary = "unassigned"
        cell["total"] += 1
        on_map_primary = primary in LAYER_IDS or primary == OUTSIDE
        if open_:
            cell["open"] += 1
            if gov and on_map_primary:
                cell["governance_open"] += 1
        if decide:
            cell["decide"] += 1
            if gov and on_map_primary:
                cell["governance_decide"] += 1
            pending[primary].append(_decide_item(row, layer, events.get(row["id"]) or [], url_for))
        if primary in LAYER_IDS:
            for lid in layer.get("involved") or []:
                if lid in layers and lid != primary:
                    layers[lid]["involved_extra"] += 1
                    involved_from[lid].add(primary)

    for lid, cell in layers.items():
        cell["decide_items"] = _top_decide(pending[lid])
        cell["involved_from"] = [c for c in LAYER_IDS if c in involved_from[lid]]
    outside["decide_items"] = _top_decide(pending[OUTSIDE])
    unassigned["decide_items"] = _top_decide(pending["unassigned"])

    header = {
        "total": sum(cell["total"] for cell in layers.values()) + outside["total"] + unassigned["total"],
        "assigned": sum(cell["total"] for cell in layers.values()),
        "open": sum(cell["open"] for cell in layers.values()) + outside["open"] + unassigned["open"],
        "decide": sum(cell["decide"] for cell in layers.values()) + outside["decide"] + unassigned["decide"],
    }
    return {
        "ready": ready,
        "header": header,
        "layers": [layers[lid] for lid in LAYER_ORDER],
        "outside": outside,
        "unassigned": unassigned,
    }
